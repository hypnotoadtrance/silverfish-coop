import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import authored_supply, generate_authored_supply, native_authored_pickup as pickup, party, runtime
from tools.silverfish.service import Client
from tools.silverfish.travel import CITY, CROSSROADS


CITY_AMMO, CROSSROADS_AMMO = authored_supply.CITY_AMMO, authored_supply.CROSSROADS_AMMO
EPOCH, OLD_EPOCH = "a" * 32, "b" * 32
PAWN, CHARACTER = "Player_FP_C exact guest", "guest-1"


def source_row(profile):
    return dict(source=profile.source, map=profile.area, actor=profile.actor, class_path=profile.ammo.item_path, state="available")


class SupplyCatalogTests(unittest.TestCase):
    def test_generated_source_ids_include_map_for_identical_actor_names(self):
        self.assertEqual(CITY_AMMO.name, CROSSROADS_AMMO.name)
        self.assertNotEqual(CITY_AMMO.source, CROSSROADS_AMMO.source)
        slums, fort = authored_supply.SLUMS_BUCKSHOT, authored_supply.FORT_LETO_BUCKSHOT
        self.assertEqual(slums.name, fort.name)
        self.assertNotEqual(slums.source, fort.source)
        self.assertEqual((slums.units, fort.units), (14, 5))
        self.assertEqual((CITY_AMMO.area, CROSSROADS_AMMO.area), (CITY, CROSSROADS))
        self.assertEqual(pickup.select_source(CROSSROADS, None), pickup.POCKET_WATCH)
        for row in authored_supply.PLACEMENTS:
            self.assertEqual(pickup.SOURCES[row.source], (row.area, row.actor, row.ammo.item_path))
        root = Path(__file__).resolve().parents[1]
        for path, expected in generate_authored_supply.outputs().items():
            self.assertEqual((root / path).read_text(encoding="utf-8"), expected, path)

    def test_bridge_preserves_stock_interact_and_rejects_wrong_area_source_rows(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bridge = runtime.Bridge(directory, Mock())
        (directory / "input.tsv").write_text(f"interact\t1\t{CITY_AMMO.source}\n", encoding="ascii")
        self.assertEqual([target for _, target in bridge.interactions()], [CITY_AMMO.source])

        header = f"schema\t1\nsession\thost\thosting\t{CITY}\t1\t0\n"
        for profile in authored_supply.PLACEMENTS:
            row = "\t".join(("native-authored-pickup", *source_row(profile).values()))
            (directory / "status.tsv").write_text(header + row + "\n", encoding="utf-8")
            status = bridge.snapshot()
            if profile is CITY_AMMO:
                self.assertEqual(status[pickup.SOURCES_KEY], {profile.source: source_row(profile)})
            else:
                self.assertIsNone(status)


class SupplyClaimTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        data = party.new_party()
        party.add_player(data, CHARACTER)
        return SimpleNamespace(
            native_zones=True, native_profile=True, native_checkpoint_unsafe=False, stopping=False, transitioning=False,
            zone_epoch=EPOCH, last_seen=time.monotonic(), party=data, control=Mock(), checkpoint_native=AsyncMock(),
            native_recipients=lambda: {CHARACTER: {"pawn": PAWN, "connected": True}},
            last_game={"state": "hosting", "map": CITY, pickup.SOURCES_KEY: {CITY_AMMO.source: source_row(CITY_AMMO)}},
        )

    async def test_client_attaches_current_epoch_to_stock_input_and_keeps_explicit_stale_epoch(self):
        sender = SimpleNamespace(zone_epoch=EPOCH, last_game={"state": "connected"},
                                 transport=SimpleNamespace(connected=True, request=AsyncMock(return_value={"ok": True})))
        operation = "c" * 32
        targets = (CITY_AMMO.source, f"{CITY_AMMO.source}@{OLD_EPOCH}", pickup.CITY_WIRE)
        for raw_target, expected in zip(targets, (f"{CITY_AMMO.source}@{EPOCH}", targets[1], targets[2]), strict=True):
            await Client.gameplay_action(sender, pickup.ACTION, operation, raw_target)
            self.assertEqual(sender.transport.request.call_args.args[0]["target"], expected)

    async def test_stale_and_cross_map_supply_requests_refuse_before_checkpoint(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock())
        for target, reason in ((CITY_AMMO.source, "stale_zone"), (f"{CITY_AMMO.source}@{OLD_EPOCH}", "stale_zone"),
                               (f"{CROSSROADS_AMMO.source}@{EPOCH}", "unavailable")):
            with self.subTest(target=target), self.assertRaisesRegex(party.RuleError, reason):
                await pickup.claim(host, bridge, CHARACTER, target, "c" * 32)
        bridge.command.assert_not_awaited()
        host.checkpoint_native.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertNotIn(pickup.STATE_KEY, host.party)

    async def test_epoch_rechecked_after_checkpoint_before_native_mutation(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock())

        async def checkpoint(*_args):
            host.zone_epoch = OLD_EPOCH

        host.checkpoint_native.side_effect = checkpoint
        with self.assertRaisesRegex(party.RuleError, "stale_zone"):
            await pickup.claim(host, bridge, CHARACTER, pickup.request_target(CITY_AMMO.source, EPOCH), "c" * 32)
        bridge.command.assert_not_awaited()
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertNotIn(pickup.STATE_KEY, host.party)

    async def test_native_sight_refusal_commits_no_claim_or_owned_frame(self):
        host = self.host()

        async def blocked(_action, pawn, slot, source, *, timeout):
            self.assertEqual((pawn, source, timeout), (PAWN, CITY_AMMO.source, 7))
            return {**host.last_game, "native_authored_pickup_claim": {
                "source": source, "map": CITY, "pawn": pawn, "slot": slot, "ok": False, "applied": False,
                "status": "authored_pickup_exact_authority_sight_required", "guid": "-",
            }}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=blocked))
        with self.assertRaisesRegex(party.RuleError, "authority_sight_required"):
            await pickup.claim(host, bridge, CHARACTER, pickup.request_target(CITY_AMMO.source, EPOCH), "c" * 32)
        host.checkpoint_native.assert_awaited_once_with(bridge)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertFalse(host.stopping)
        self.assertNotIn(pickup.STATE_KEY, host.party)
        self.assertEqual(host.party["receipts"], {})
