import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.silverfish import native_authored_pickup as pickup, native_guest_melee as melee, party
from tools.silverfish.travel import CROSSROADS


GUID = "AB" * 16
OPERATION = "a" * 32


class NativeGuestMeleeTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        state = party.new_party()
        party.add_player(state, "guest")
        state[pickup.STATE_KEY] = {pickup.OLD_PIPE: {"owner": "guest", "guid": GUID}}
        return SimpleNamespace(native_guest=True, fixtures=True, native_profile=True, stopping=False, transitioning=False,
                               native_checkpoint_unsafe=False, party=state, last_seen=time.monotonic(),
                               last_game={"state": "hosting", "map": CROSSROADS}, checkpoint_native=AsyncMock(),
                               native_recipients=lambda: {"guest": {"connected": True, "pawn": "exact pawn"}})

    def swing(self, status, **changes):
        settled = status == "surface_verified"
        return {"state": "hosting", "map": CROSSROADS, melee.CANARY_KEY: dict(
            {"map": CROSSROADS, "pawn": "exact pawn", "guid": GUID, "serial": 3, "status": status,
             "presses": 1, "releases": 1, "contacts": int(settled), "qualified": int(settled),
             "settled": settled, "passive_free": True}, **changes)}

    async def test_pending_swing_holds_checkpoint_and_replays_exact_receipt(self):
        host = self.host()
        complete = self.swing("surface_verified")

        async def observe(terminal, timeout):
            self.assertTrue(host.native_checkpoint_unsafe)
            self.assertEqual(host.checkpoint_native.await_count, 1)
            self.assertFalse(terminal(self.swing("surface_verified", serial=4)))
            self.assertFalse(terminal(self.swing("surface_verified", pawn="another pawn")))
            self.assertTrue(terminal(complete))
            return complete

        bridge = SimpleNamespace(command=AsyncMock(return_value=self.swing("pending")), wait_for=AsyncMock(side_effect=observe))
        args = host, bridge, melee.CANARY, "guest", GUID, OPERATION
        self.assertEqual(await melee.perform(*args), {melee.CANARY_KEY: complete[melee.CANARY_KEY]})
        self.assertEqual(await melee.perform(*args), {melee.CANARY_KEY: complete[melee.CANARY_KEY]})
        bridge.command.assert_awaited_once_with(melee.CANARY + "-start", "exact pawn", GUID, timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest"})})
        self.assertFalse(host.native_checkpoint_unsafe)

    async def test_uncertain_contact_never_commits_and_clean_refusal_releases_fence(self):
        for error, dirty in ((RuntimeError("native_guest_melee_stock_swing_unqualified"), False), (None, True)):
            with self.subTest(dirty=dirty):
                host = self.host()
                frame = self.swing("mutation_unverified_guest_melee_contact_path", contacts=1)
                bridge = SimpleNamespace(command=AsyncMock(return_value=frame, side_effect=error))
                with self.assertRaises(RuntimeError if dirty else party.RuleError):
                    await melee.perform(host, bridge, melee.CANARY, "guest", GUID, OPERATION)
                self.assertEqual(host.checkpoint_native.await_count, 1)
                self.assertEqual(host.stopping, dirty)
                self.assertEqual(host.native_checkpoint_unsafe, dirty)
                self.assertEqual(host.party["receipts"], {})

    async def test_equipment_needs_exact_placed_claim_before_native_access(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock())
        host.party[pickup.STATE_KEY][pickup.OLD_PIPE]["owner"] = "other guest"
        with self.assertRaisesRegex(party.RuleError, "exact_authored_claim"):
            await melee.perform(host, bridge, melee.EQUIP, "guest", GUID, OPERATION)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()

    def test_stock_animation_tsv_and_pocket_watch_fallback(self):
        fields = [melee.CANARY, CROSSROADS, "exact pawn", GUID, "3", "surface_verified", "1", "1", "1", "1", "1", "1"]
        self.assertEqual(melee.parse(fields), (melee.CANARY_KEY, self.swing("surface_verified")[melee.CANARY_KEY]))
        self.assertEqual(pickup.select_source(CROSSROADS, None), pickup.POCKET_WATCH)
        self.assertEqual(pickup.select_source(CROSSROADS, pickup.OLD_PIPE), pickup.OLD_PIPE)
