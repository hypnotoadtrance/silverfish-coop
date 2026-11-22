"""Direct IP discovery and persistent guest enrollment over the co-op link."""

from dataclasses import dataclass, field
from hashlib import sha256
import hmac
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import secrets
from tempfile import NamedTemporaryFile
import time
import uuid

from cryptography.hazmat.primitives import serialization

from . import link
from .campaign import CampaignError, CampaignStore
from .runtime import read_json, write_json


_SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
_PIN = re.compile(r"[0-9a-f]{64}")
_REFUSALS = {
    "direct_join_disabled": "The host requires an invitation",
    "direct_wrong_password": "The Direct IP password is incorrect",
    "direct_password_required": "The host requires a Direct IP password",
    "direct_password_rate_limited": "Too many password attempts. Try again shortly",
    "direct_enrollment_required": "Connect by address and authenticate to this host first",
    "direct_campaign_changed": "The host changed campaigns during connection",
    "incompatible_game_or_protocol": "The host has an incompatible game or mod build",
    "direct_host_identity_changed": "This address now presents a different host identity",
    "direct_identity_verification_failed": "The supplied fingerprint does not match the host identity",
}


class Refused(link.LinkError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(_REFUSALS.get(reason, "The host declined Direct IP enrollment"))


class HostTrustRequired(link.LinkError):
    def __init__(self, fingerprint: str, *, changed: bool):
        self.fingerprint = fingerprint
        self.changed = changed
        super().__init__("The host identity needs confirmation")


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.int != 0


def _password_bytes(password):
    if type(password) is not str or len(password) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("The Direct IP password must be at most 128 printable characters")
    return password.encode("utf-8")


class Enrollment:
    """Serve public identity separately from password-gated character ownership."""

    def __init__(self, store: CampaignStore, identity: link.ServerIdentity, game_build: str, protocol: int,
                 features: dict, password: str):
        self._store = store
        self._campaign = store.status()["campaign_id"]
        self._certificate = identity.certificate_pem
        self._build = game_build
        self._protocol = protocol
        self._features = dict(features)
        self._key = secrets.token_bytes(32)
        encoded = _password_bytes(password)
        self._proof = hmac.digest(self._key, encoded, "sha256") if encoded else None
        self._attempts = {}
        self._authorized = set()

    def authorized(self, guest_id: str) -> bool:
        return guest_id in self._authorized

    async def __call__(self, request: dict, peer: str) -> dict:
        if request == {"direct": "discover"}:
            return {"accepted": True, "game_access": False, "certificate": self._certificate,
                    "campaign": self._campaign, "password_required": self._proof is not None}
        if (request.keys() != {"direct", "campaign", "guest_id", "credential", "game_build", "protocol_version", "password"}
                or request["direct"] != "enroll" or not _uuid(request["guest_id"])
                or type(request["credential"]) is not str or not _SECRET.fullmatch(request["credential"])):
            return {"accepted": False, "reason": "invalid_request"}
        if (request["game_build"] != self._build or type(request["protocol_version"]) is not int
                or request["protocol_version"] != self._protocol):
            return {"accepted": False, "reason": "incompatible_game_or_protocol"}
        if request["campaign"] != self._campaign:
            return {"accepted": False, "reason": "direct_campaign_changed"}
        try:
            password = _password_bytes(request["password"])
        except (ValueError, UnicodeError):
            return {"accepted": False, "reason": "invalid_request"}
        if self._proof is not None:
            now = time.monotonic()
            self._attempts = {ip: entry for ip, entry in self._attempts.items() if now - entry[0] < 12}
            started, count = self._attempts.get(peer, (now, 0))
            if count >= 8 or (peer not in self._attempts and len(self._attempts) >= 128):
                return {"accepted": False, "reason": "direct_password_rate_limited"}
            if not hmac.compare_digest(hmac.digest(self._key, password, "sha256"), self._proof):
                self._attempts[peer] = started, count + 1
                reason = "direct_wrong_password" if password else "direct_password_required"
                return {"accepted": False, "reason": reason}
            self._attempts.pop(peer, None)
        try:
            self._store.register_direct_guest(request["guest_id"], request["credential"])
        except CampaignError:
            return {"accepted": False, "reason": "guest_unavailable"}
        self._authorized.add(request["guest_id"])
        return {"accepted": True, "game_access": False, "features": self._features}


def _accepted(reply: dict) -> dict:
    if reply["accepted"] is not True:
        reason = reply.get("reason")
        raise Refused(reason if type(reason) is str and reason in _REFUSALS else "direct_refused")
    return reply


def _record(path: Path, default: dict | None = None) -> dict | None:
    if path.resolve() != path:
        raise ValueError("The local Direct IP record is redirected")
    try:
        value = read_json(path, limit=4096)
        if type(value) is not dict:
            raise ValueError("Invalid local Direct IP record")
        return value
    except FileNotFoundError:
        if default is None:
            return None
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".direct-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(default, separators=(",", ":")).encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                temporary.rename(path)
            else:
                os.link(temporary, path)
        except FileExistsError:
            return _record(path)
        return default
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _new_identity_record() -> dict:
    identity = link.make_server_identity()
    key = identity.private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    return {"certificate": identity.certificate_pem, "private_key": key.decode("ascii")}


WORKSPACE_IDENTITY = "direct-ip-host-identity.json"
RESTORED_IDENTITY = "cloud-host-identity.json"


def read_resident_identity(work: Path, campaign: Path) -> dict | None:
    restored = _record(campaign / "save" / RESTORED_IDENTITY)
    return restored if restored is not None else _record(work / WORKSPACE_IDENTITY)


def resident_host_identity(work: Path, campaign: Path, guest_count: int) -> link.ServerIdentity:
    """Keep a restored campaign's identity and share one identity for new campaigns."""
    restored = _record(campaign / "save" / RESTORED_IDENTITY)
    if restored is not None:
        return link.load_server_identity(restored["certificate"], restored["private_key"])
    path = work / WORKSPACE_IDENTITY
    shared = _record(path)
    if shared is None:
        legacy = _record(campaign / "save/host-identity.json")
        seed = legacy
        if not (guest_count and legacy):
            # If there is one legacy identity already used by guests, retain it.
            prior = {}
            for sibling in campaign.parent.iterdir():
                if sibling == campaign or not sibling.is_dir() or sibling.resolve() != sibling:
                    continue
                try:
                    record = _record(sibling / "save/host-identity.json")
                    if record is None:
                        continue
                    checkpoint = CampaignStore.read_existing(sibling / "save", max_read_bytes=8 * 1024**2)
                    if checkpoint and checkpoint["guests"]:
                        identity = link.load_server_identity(record["certificate"], record["private_key"])
                        prior[identity.certificate_pem] = record
                except (OSError, CampaignError, KeyError, ValueError):
                    continue
                if len(prior) > 1:
                    break
            if len(prior) == 1:
                seed = next(iter(prior.values()))
        if seed is not None:
            link.load_server_identity(seed["certificate"], seed["private_key"])
        shared = _record(path, seed or _new_identity_record())
    return link.load_server_identity(shared["certificate"], shared["private_key"])


@dataclass(frozen=True)
class Connection:
    details: dict = field(repr=False)

    def session(self, character_id: str | None = None) -> dict:
        if character_id is not None and not _uuid(character_id):
            raise ValueError("Choose an exact character from the host's catalog")
        return dict(self.details) if character_id is None else dict(self.details, character_id=character_id)


async def connect(profile: Path, host: str, port: int, password: str, game_build: str, protocol: int, *,
                  verified_fingerprint: str | None = None, require_trust: bool = False) -> Connection:
    """Discover the host, then enroll over a second TLS connection pinned to that certificate."""
    if type(host) is not str or "%" in host or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Enter a numeric host address and a UDP port from 1 to 65535")
    try:
        host = str(ip_address(host))
    except ValueError:
        raise ValueError("Enter a numeric host IP address") from None
    _password_bytes(password)
    if verified_fingerprint is not None and not _PIN.fullmatch(verified_fingerprint):
        raise ValueError("Expected a SHA-256 host fingerprint")
    discovery = _accepted(await link.direct_request(host, port, {"direct": "discover"}, certificate=None))
    certificate, campaign = discovery.get("certificate"), discovery.get("campaign")
    if type(certificate) is not str or not _uuid(campaign) or type(discovery.get("password_required")) is not bool:
        raise link.LinkError("The host returned an invalid Direct IP identity")
    pin = sha256(certificate.encode("utf-8")).hexdigest()
    if verified_fingerprint is not None and not hmac.compare_digest(verified_fingerprint, pin):
        raise Refused("direct_identity_verification_failed")
    root = profile.resolve() / "direct-ip"
    if root.resolve() != root:
        raise ValueError("The local Direct IP directory is redirected")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    endpoint = sha256(f"{host}/{port}".encode("ascii")).hexdigest()
    host_record = root / f"host-{endpoint}.json"
    saved_pin = {"schema": 1, "certificate_sha256": pin}
    remembered = _record(host_record)
    changing_pin = remembered is not None and remembered != saved_pin
    if require_trust and remembered != saved_pin and verified_fingerprint is None:
        raise HostTrustRequired(pin, changed=changing_pin)
    if changing_pin and verified_fingerprint is None:
        raise Refused("direct_host_identity_changed")

    def remember_host():
        if changing_pin:
            if _record(host_record) != remembered:
                raise Refused("direct_host_identity_changed")
            write_json(host_record, saved_pin)
        if _record(host_record, saved_pin) != saved_pin:
            raise Refused("direct_host_identity_changed")

    if discovery["password_required"] and not password:
        if verified_fingerprint is not None and remembered != saved_pin:
            # Verify the approved key without spending a password attempt.
            confirmed = _accepted(await link.direct_request(host, port, {"direct": "discover"}, certificate=certificate))
            if confirmed.get("certificate") != certificate or confirmed.get("campaign") != campaign:
                raise Refused("direct_identity_verification_failed")
            remember_host()
        raise Refused("direct_password_required")
    participant = _record(root / f"guest-{pin}-{uuid.UUID(campaign).hex}.json", {
        "schema": 1, "guest_id": str(uuid.uuid4()), "credential": secrets.token_urlsafe(32),
    })
    if (type(participant) is not dict or participant.keys() != {"schema", "guest_id", "credential"}
            or participant["schema"] != 1 or not _uuid(participant["guest_id"])
            or type(participant["credential"]) is not str or not _SECRET.fullmatch(participant["credential"])):
        raise ValueError("The local Direct IP guest record is invalid")
    credentials = {name: participant[name] for name in ("guest_id", "credential")}
    reply = await link.direct_request(host, port, {
        "direct": "enroll", "campaign": campaign, **credentials, "password": password,
        "game_build": game_build, "protocol_version": protocol,
    }, certificate=certificate)
    if verified_fingerprint is not None:
        remember_host()
    reply = _accepted(reply)
    features = reply.get("features")
    if (type(features) is not dict
            or any((not key.startswith("native_") and key != "expanded_zones") or type(value) is not bool
                   for key, value in features.items())):
        raise link.LinkError("The host returned invalid Direct IP game features")
    if verified_fingerprint is None:
        remember_host()
    return Connection(dict(features, schema=1, host=host, port=port, certificate=certificate,
                           game_build=game_build, protocol_version=protocol, **credentials))
