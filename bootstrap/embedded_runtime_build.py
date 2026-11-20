"""Fetch locked artifacts and build the Windows x64 embedded co-op runtime."""
import argparse
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

LOCK_PATH = Path(__file__).with_name("embedded_runtime_lock.json")
CPYTHON_VERSION = "3.14.6"
MAX_ARTIFACT = 15 * 1024**2
MAX_RUNTIME = 60 * 1024**2
MAX_MANIFEST = 512 * 1024
MAX_ENTRIES = 4096
WHEEL_TAGS = {"py3-none-any", "cp310-abi3-win_amd64", "cp311-abi3-win_amd64", "cp314-cp314-win_amd64"}
MODULE_PATHS = ("python314.zip", ".", "site-packages", "app")
PORTABLE_NAME = re.compile(r"[a-zA-Z0-9._-]+")
WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", re.IGNORECASE)
APP_FILES = {"tools/__init__.py", "tools/silverfish/__init__.py", "tools/silverfish/embedded_startup.py"}


def load_lock():
    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if (data["schema"] != 1 or data["python"]["version"] != CPYTHON_VERSION
            or len(data["wheels"]) != 9 or len(data["native_notices"]) != 5):
        raise ValueError("Unexpected runtime lock shape")
    return data


def relative_name(name):
    if any(not PORTABLE_NAME.fullmatch(part) or part.endswith(".") or WINDOWS_DEVICE.fullmatch(part)
           for part in name.split("/")):
        raise ValueError(f"Unsafe archive path {name!r}")
    return PurePosixPath(name)


def checked_payload(data, digest, name, size=None):
    if len(data) > MAX_ARTIFACT or (size is not None and len(data) != size) or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Artifact digest or length mismatch for {name}")
    return data


def artifact(directory, url, digest, *, name=None, size=None, fetch=False):
    parsed = urlsplit(url)
    hosts = {"www.python.org", "files.pythonhosted.org", "raw.githubusercontent.com"}
    if parsed.scheme != "https" or parsed.netloc not in hosts or parsed.query or parsed.fragment:
        raise ValueError(f"Expected an official HTTPS artifact URL: {url}")
    url_name = relative_name(parsed.path.rsplit("/", 1)[-1]).name
    name = url_name if name is None else name
    if len(relative_name(name).parts) != 1:
        raise ValueError(f"Invalid artifact cache filename {name!r}")
    path = directory / name
    if not path.exists() and fetch:
        with urlopen(url, timeout=30) as response:
            if response.geturl() != url:
                raise ValueError(f"Unexpected upstream redirect for {name}")
            data = checked_payload(response.read(MAX_ARTIFACT + 1), digest, name, size)
        with TemporaryDirectory(prefix=".dependency-", dir=directory) as temporary:
            pending = Path(temporary) / name
            pending.write_bytes(data)
            try:
                pending.rename(path)
            except FileExistsError:
                pass
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a cached file for {name}")
    with path.open("rb") as stream:
        data = checked_payload(stream.read(MAX_ARTIFACT + 1), digest, name, size)
    return name, data


def dependencies(cache, lock, *, fetch=False):
    archives = []
    for record in [lock["python"], *lock["wheels"]]:
        name, data = artifact(cache, record["url"], record["sha256"], size=record.get("size"), fetch=fetch)
        if name != record["filename"]:
            raise ValueError("Artifact URL and filename disagree")
        archives.append(data)

    source_distributions = {}
    for name in ("certifi", "pylsqpack"):
        record = next(item for item in lock["wheels"] if item["name"] == name)
        source_distributions[name] = artifact(cache, record["source_url"], record["source_sha256"], fetch=fetch)

    notices = {}
    for name, record in lock["native_notices"].items():
        if "url" in record:
            _, notices[name] = artifact(cache, record["url"], record["sha256"], name=name, fetch=fetch)
        else:
            with tarfile.open(fileobj=BytesIO(source_distributions["pylsqpack"][1]), mode="r:gz") as source:
                member = source.getmember(record["source_member"])
                if not member.isfile() or member.size > MAX_ARTIFACT:
                    raise ValueError(f"Unexpected notice member for {name}")
                with source.extractfile(member) as stream:
                    notices[name] = checked_payload(stream.read(MAX_ARTIFACT + 1), record["sha256"], name)
    return archives, source_distributions["certifi"], notices


class Extract:
    def __init__(self, root):
        self.root = root
        self.written = {}
        self.entries = {}
        self.size = 0

    def write(self, rel, data):
        name = str(rel)
        folded = name.casefold()
        if folded in self.written or self.size + len(data) > MAX_RUNTIME:
            raise ValueError(f"Conflicting archive entry or runtime is too large: {name}")
        for entry in (rel, *rel.parents[:-1]):
            text = str(entry)
            if self.entries.setdefault(text.casefold(), text) != text:
                raise ValueError(f"Archive entries disagree on path casing: {text}")
        if len(self.entries) > MAX_ENTRIES:
            raise ValueError("Runtime archive entry limit exceeded")
        path = self.root.joinpath(*rel.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        self.written[folded] = (name, hashlib.sha256(data).hexdigest())
        self.size += len(data)

    def archive(self, archive, prefix="", *, wheel=False, python=False):
        for member in archive.infolist():
            mode = stat.S_IFMT(member.external_attr >> 16)
            if mode not in {0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG}:
                raise ValueError(f"Unexpected file type in archive: {member.filename}")
            if member.is_dir():
                relative_name(member.filename[:-1])
                continue
            rel = relative_name(member.filename)
            if wheel and (rel.parts[0].endswith(".data") or rel.suffix == ".pth" or rel.suffix == ".pyc"):
                raise ValueError(f"Wheel needs an explicitly reviewed install rule: {rel}")
            if member.file_size > MAX_RUNTIME - self.size:
                raise ValueError("Runtime payload size limit exceeded")
            if python and rel.name == "python314._pth":
                data = archive.read(member)
                if data.replace(b"\r\n", b"\n").split(b"\n")[:2] != [b"python314.zip", b"."]:
                    raise ValueError("Unexpected upstream Python search paths")
                continue
            self.write(PurePosixPath(prefix) / rel, archive.read(member))


def metadata(archive, record):
    candidates = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    metadata_name, = candidates
    info = BytesParser().parsebytes(archive.read(metadata_name))
    dist = metadata_name.removesuffix("/METADATA")
    wheel_info = BytesParser().parsebytes(archive.read(dist + "/WHEEL"))
    normalize = lambda name: re.sub(r"[-_.]+", "-", name.lower())
    if (normalize(info["Name"]) != normalize(record["name"]) or info["Version"] != record["version"]
        or wheel_info.get_all("Tag") != [record["tag"]] or record["tag"] not in WHEEL_TAGS):
        raise ValueError("Wheel metadata disagrees with the lock")
    license_files = [dist + "/licenses/" + path for path in info.get_all("License-File", [])]
    if not license_files or any(name not in archive.namelist() for name in license_files):
        raise ValueError("Missing declared license")
    return {
        "name": info["Name"],
        "version": info["Version"],
        "license_expression": info["License-Expression"],
        "license_files": ["site-packages/" + path for path in license_files],
        "requires_dist": info.get_all("Requires-Dist", []),
        "source_url": record["source_url"],
        "source_sha256": record["source_sha256"],
    }


def git(source, *args):
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(source), *args], check=True, capture_output=True,
    ).stdout


def source_tree(source, revision):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise ValueError("Supply a full lowercase Git commit hash")
    if git(source, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("The app revision must identify a Git commit")
    blobs = {}
    listing = git(source, "ls-tree", "--full-tree", "-r", "-z", revision, "--", "tools")
    for row in listing.rstrip(b"\0").split(b"\0"):
        if not row:
            continue
        entry, path_bytes = row.split(b"\t", 1)
        path = path_bytes.decode("utf-8")
        if path != "tools/__init__.py" and not path.startswith("tools/silverfish/"):
            continue
        mode, kind, object_id = entry.decode("ascii").split()
        if mode not in {"100644", "100755"} or kind != "blob" or not path.endswith(".py"):
            raise ValueError(f"Unexpected mod app source file {path!r}")
        blobs[path] = object_id
    if not APP_FILES.issubset(blobs):
        raise ValueError("The Git commit has no complete embedded worker entry")
    return blobs


def notice_document(wheels, certifi_source):
    lines = [
        "# Third-party runtime notices",
        "",
        "This private prototype redistributes the official CPython 3.14.6 Windows x64 embeddable package.",
        "The upstream PSF and bundled-component notices are in LICENSE.txt.",
        "The change to the upstream layout is a relative python314._pth containing app and site-packages.",
        "CPython binaries and its standard library ZIP are unchanged.",
        "Official release: https://www.python.org/downloads/release/python-3146/",
        "",
        "The following exact upstream wheels were unpacked without altering their payload files.",
    ]
    for wheel in wheels:
        label = wheel["license_expression"] or {"certifi": "MPL-2.0", "pyOpenSSL": "Apache-2.0"}[wheel["name"]]
        files = ", ".join(wheel["license_files"])
        lines += ["", f"- {wheel['name']} {wheel['version']} ({label})", f"  License files: {files}", f"  Source: {wheel['source_url']}"]
    lines += [
        "",
        "The unmodified certifi source distribution is included to make its covered source available.",
        f"Source archive: source/{certifi_source}",
        "The certifi file-level MPL-2.0 terms are preserved in its original LICENSE.",
        "",
        "The native-notices folder holds the upstream CPython SPDX report and the license texts for the three",
        "OpenSSL builds (CPython 3.5.7, aioquic 3.5.4, cryptography 4.0.3) and the ls-qpack copy in pylsqpack.",
        "The CPython Windows-binary conditions for Microsoft Distributable Code are in LICENSE.txt.",
        "Release review should reconcile the complete native component notices and protective terms before distribution.",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def assemble(target, lock, archives, certifi_source, notices, source, revision, blobs):
    out = Extract(target)
    with ZipFile(BytesIO(archives[0])) as archive:
        out.archive(archive, python=True)
    wheels = []
    for record, data in zip(lock["wheels"], archives[1:], strict=True):
        with ZipFile(BytesIO(data)) as archive:
            wheels.append(metadata(archive, record))
            out.archive(archive, "site-packages", wheel=True)
    for path, object_id in blobs.items():
        out.write(PurePosixPath("app") / relative_name(path), git(source, "cat-file", "blob", object_id))
    out.write(PurePosixPath("python314._pth"), ("\n".join(MODULE_PATHS) + "\n").encode("ascii"))
    certifi_name, certifi_data = certifi_source
    out.write(PurePosixPath("source") / certifi_name, certifi_data)
    for name, payload in notices.items():
        rel = relative_name(name)
        if len(rel.parts) != 1:
            raise ValueError(f"Unexpected upstream notice name: {name}")
        out.write(PurePosixPath("native-notices") / rel, payload)
    notice_index = json_line(lock["native_notices"])
    out.write(PurePosixPath("native-notices/sources.json"), notice_index)
    out.write(PurePosixPath("THIRD-PARTY-NOTICES.md"), notice_document(wheels, certifi_name))
    required = ["python314.dll", "python3.dll", "python314.zip", "python.exe", "_ssl.pyd", "LICENSE.txt"]
    if any(name.casefold() not in out.written for name in required):
        raise ValueError("Incomplete CPython embeddable distribution")
    manifest = {
        "schema": 1, "platform": "win-amd64", "app_revision": revision,
        "python_version": CPYTHON_VERSION, "python_lock": lock["python"],
        "python_library": "python314.dll", "module_paths": list(MODULE_PATHS),
        "wheels": wheels, "payload_bytes": out.size,
        "files": dict(sorted(out.written.values())),
    }
    payload = json_line(manifest)
    if len(payload) > MAX_MANIFEST:
        raise ValueError("Embedded runtime exceeds the native manifest or entry limit")
    out.write(PurePosixPath("runtime-manifest.json"), payload)
    return hashlib.sha256(payload).hexdigest()


def json_line(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def build(cache: Path, source: Path, revision: str, output: Path) -> str:
    """Publish a new runtime tree offline and return its manifest SHA-256."""
    if output.exists() or output.is_symlink() or output.name in {"", ".", ".."}:
        raise FileExistsError(f"Expected a fresh runtime output directory: {output}")
    parent = output.parent.resolve(strict=True)
    target = parent / output.name
    lock = load_lock()
    source = source.resolve(strict=True)
    blobs = source_tree(source, revision)
    archives, certifi_source, notices = dependencies(cache.resolve(strict=True), lock)
    with TemporaryDirectory(prefix=".embedded-runtime-", dir=parent) as temporary:
        staging = Path(temporary) / "runtime"
        staging.mkdir()
        digest = assemble(staging, lock, archives, certifi_source, notices, source, revision, blobs)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Runtime output already exists: {target}")
        staging.rename(target)
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch", help="populate the cache from the locked upstream URLs")
    fetch.add_argument("--cache", type=Path, required=True, help="directory for exact downloaded dependencies")
    offline = subparsers.add_parser("build", help="assemble from the cached dependencies without network access")
    offline.add_argument("--cache", type=Path, required=True)
    offline.add_argument("--source", type=Path, required=True, help="Git checkout containing the release commit")
    offline.add_argument("--revision", required=True, help="full Git commit hash used by the matching coordinator")
    offline.add_argument("--output", type=Path, required=True, help="new runtime directory in an existing parent")
    args = parser.parse_args()
    if args.command == "fetch":
        args.cache.mkdir(exist_ok=True)
        dependencies(args.cache.resolve(strict=True), load_lock(), fetch=True)
        print("Verified the locked runtime dependencies")
    else:
        digest = build(args.cache, args.source, args.revision, args.output)
        print(f"SILVERFISH_RUNTIME_MANIFEST_SHA256={digest}")


if __name__ == "__main__":
    main()
