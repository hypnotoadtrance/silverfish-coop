import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_save, native_stash, party
from tools.silverfish.travel import OBSERVATORY


SITE = "A" * 32
GUEST = "guest-character"


class NativeStashTests(unittest.IsolatedAsyncioTestCase):
    async def test_stash_census_reads_only_the_authenticated_characters_frame(self):
        profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        native_host_save.directory(profile).mkdir(parents=True)

        def saved(letter):
            slot = f"SFCoop_{letter * 32}"
            native_host_save.slot_path(profile, slot).write_bytes(b"GVAS" + letter.encode() * 32)
            return native_host_save.record(profile, slot, OBSERVATORY)

        owner, visitor = saved("a"), {**saved("b"), "character_id": GUEST}
        campaign = dict(revision=5, world={"native_host_save": owner},
                        characters=[dict(character_id=GUEST, state={"native_guest_save": visitor})])
        state = party.new_party()
        party.add_player(state, "host")
        party.add_player(state, GUEST)
        game = {"state": "hosting", "map": OBSERVATORY, "pawns": {"local": {"name": "host-pawn"}},
                native_stash.SETTING: True, native_stash.SITES: {SITE: {"map": OBSERVATORY}}}
        host = SimpleNamespace(last_game=game, zone_epoch="zone", native_zones=True, native_profile=profile,
                               stopping=False, transitioning=False, native_checkpoint_unsafe=False,
                               party=state, store=Mock(status=Mock(return_value=campaign)),
                               native_recipients=lambda: {GUEST: {"pawn": "guest-pawn", "connected": True}},
                               checkpoint_native=AsyncMock(return_value=5))

        async def census(action, pawn, slot, site, offset, *, timeout):
            value = "C" if slot == owner["slot"] else "D"
            assert action == native_stash.CENSUS and site == SITE
            return {**game, "native_stash_census": dict(map=OBSERVATORY, pawn=pawn, slot=slot, site=site,
                combined=game[native_stash.SETTING], total=1, offset=int(offset), count=1, items=[
                    dict(guid=value * 32, source="stash", index=0, quantity=1, class_path="/Game/Classes/Items/Test.Test_C")])}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=census))
        guest_view = (await native_stash.inspect(host, bridge, GUEST, SITE))[native_stash.RESULT]
        self.assertEqual((guest_view["owner"], guest_view["combined"], guest_view["items"][0]["guid"]),
                         (GUEST, True, "D" * 32))
        self.assertEqual(bridge.command.await_args.args[1:3], ("guest-pawn", visitor["slot"]))
        host.checkpoint_native.assert_awaited_with(bridge, require_guest_frames=frozenset({GUEST}))
        game[native_stash.SETTING] = False
        host_view = (await native_stash.inspect(host, bridge, "host", SITE))[native_stash.RESULT]
        self.assertEqual((host_view["owner"], host_view["combined"], host_view["items"][0]["guid"]),
                         ("host", False, "C" * 32))
        self.assertEqual(bridge.command.await_args.args[1:3], ("host-pawn", owner["slot"]))
        with self.assertRaisesRegex(party.RuleError, "exact_site"):
            await native_stash.inspect(host, bridge, GUEST, SITE + "|4097")
        with self.assertRaisesRegex(party.RuleError, "scope_unavailable"):
            await native_stash.inspect(host, bridge, "foreign-character", SITE)
        self.assertEqual(bridge.command.await_count, 2)
