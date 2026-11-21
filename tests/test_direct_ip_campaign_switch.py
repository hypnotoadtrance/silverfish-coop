import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

from tools.silverfish import direct_ip, link, runtime
from tools.silverfish.campaign import CampaignStore


class DirectIPCampaignSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_endpoint_keeps_host_identity_and_separate_campaign_rosters(self):
        work = Path(self.enterContext(TemporaryDirectory())).resolve()
        profile = work / "guest"
        first, second = (work / "campaigns" / name for name in ("one", "two"))
        build, protocol = "a" * 64, 2
        port = runtime.spare_udp_port()
        address = "127.0.0.1"

        async def connect(store, identity, **options):
            enrollment = direct_ip.Enrollment(store, identity, build, protocol, {}, "")
            listener = await link.start_host(address, port, address, runtime.spare_udp_port(), identity,
                                             lambda _: {"accepted": False}, enroll=enrollment)
            try:
                return await direct_ip.connect(profile, address, port, "", build, protocol, **options)
            finally:
                await listener.close()

        legacy = direct_ip._new_identity_record()
        with CampaignStore(first / "save") as store:
            runtime.write_json(first / "save/host-identity.json", legacy)
            first_identity = link.load_server_identity(legacy["certificate"], legacy["private_key"])
            existing = await connect(store, first_identity)
            store.create_linked_character(existing.details["guest_id"], "Scout", uuid.uuid4().hex)
        with CampaignStore(second / "save") as store:
            identity = direct_ip.resident_host_identity(work, second, guest_count=0)
            self.assertEqual(identity.certificate_pem, first_identity.certificate_pem)
            first.rename(work / "old-campaign")
            identity = direct_ip.resident_host_identity(work, second, guest_count=0)
            guest = await connect(store, identity)
            self.assertNotEqual(guest.details["guest_id"], existing.details["guest_id"])
            self.assertEqual(store.linked_character_catalog(guest.details["guest_id"]), [])

            replacement = link.make_server_identity()
            with self.assertRaisesRegex(direct_ip.Refused, "different host identity"):
                await connect(store, replacement)
            with self.assertRaisesRegex(direct_ip.Refused, "fingerprint does not match"):
                await connect(store, replacement, verified_fingerprint="0" * 64)
            fingerprint = hashlib.sha256(replacement.certificate_pem.encode("ascii")).hexdigest()
            verified = await connect(store, replacement, verified_fingerprint=fingerprint)
            self.assertEqual((await connect(store, replacement)).details["guest_id"], verified.details["guest_id"])
