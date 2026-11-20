from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock

from tools.silverfish import native_guest_drop as drop, native_guest_drop_record as record, party
from tools.silverfish import native_guest_drop_projection as projection


MAP = record.OBSERVATORY
WORLD = f"{MAP}.Observitory_P:PersistentLevel."
PAWN = f"Player_FP_C {WORLD}Player_FP_C_404"
ACTOR = f"Ammo_Pistol_BP_C {WORLD}Ammo_Pistol_BP_C_8"
ID, OPERATION = "a" * 32, "b" * 32


class DropProjectionTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        game = {"state": "connected", "map": MAP, "pawns": {"local": {"name": PAWN}}}
        row = record.visible(dict(owner="owner", map=MAP, class_path=record.ITEM, guid="D" * 32, quantity=9,
                                  location=[10., 20., 40.]), ID)
        client = SimpleNamespace(last_game=game.copy(), gameplay={record.STATE: {ID: row}},
                                 invite={"character_id": "owner"}, expected_zone=MAP, stopping=False,
                                 suspended=False, needs_travel=False, server_transitioning=False,
                                 transport=SimpleNamespace(game_access=True), host_session="verified-host",
                                 zone_epoch="1" * 32, gameplay_seen=time.monotonic(),
                                 gameplay_action=AsyncMock(return_value={drop.RESULT: {**row, "state": "reclaimed"}}))

        async def bridge(command, *args, timeout):
            if command == projection.CLEAR:
                return game.copy()
            area, epoch, drop_id, *coords = args
            self.assertEqual(area, MAP)
            self.assertEqual((command, drop_id, coords), (projection.SYNC, ID, ["10.0", "20.0", "40.0"]))
            row = [projection.ROW, MAP, epoch, drop_id, PAWN, ACTOR, "0"]
            return {**game, projection.KEY: projection.parse(row, "connected", MAP)}

        client.bridge = SimpleNamespace(command=AsyncMock(side_effect=bridge))
        return client

    def test_exact_actor_status_and_local_intent_validation(self):
        epoch = "c" * 32
        row = [projection.ROW, MAP, epoch, ID, PAWN, ACTOR, "124"]
        self.assertEqual(projection.parse(row, "connected", MAP)["sequence"], 124)
        for state, changed in (("hosting", row), ("connected", [*row[:5], "Item_9mm_C Fake", "124"]),
                               ("connected", [*row[:-1], str(2**53)]),
                               ("connected", [*row[:4], PAWN.replace("Observitory", "CrossRoads"), *row[5:]])):
            with self.assertRaises(ValueError):
                projection.parse(changed, state, MAP)
        self.assertEqual(projection.target_parts(f"stack-drop:{epoch}:{ID}"), (epoch, ID))
        self.assertIsNone(projection.target_parts(f"stack-drop:{epoch}:{ID.upper()}"))

    async def test_only_matching_authenticated_owner_has_a_stock_projection(self):
        client, view = self.client(), projection.Projection()
        row = client.gameplay[record.STATE][ID]
        row["owner"] = "other-guest"
        await view.sync(client, True)
        client.bridge.command.assert_not_awaited()
        row["owner"] = "owner"
        await view.sync(client, True)
        self.assertEqual(client.last_game[projection.KEY]["actor"], ACTOR)
        self.assertEqual(client.bridge.command.await_args.args[3:], (ID, "10.0", "20.0", "40.0"))
        client.suspended = True
        await view.sync(client, False)
        client.bridge.command.assert_awaited_with(projection.CLEAR, timeout=2)
        self.assertNotIn(projection.KEY, client.last_game)

    async def test_stale_authenticated_snapshot_pauses_projection_without_retiring_its_identity(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        proof = client.last_game.pop(projection.KEY)
        client.gameplay_seen = time.monotonic() - 2.5
        view.next_sync = 0.
        await view.sync(client, True)
        client.bridge.command.assert_awaited_once()
        self.assertEqual(view.current, (proof["epoch"], ID, PAWN, MAP))
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, (proof["epoch"], ID))
        with self.assertRaisesRegex(party.RuleError, "fixture_scope_unavailable"):
            await view.fixture(client, ID)
        client.gameplay_action.assert_not_awaited()

        client.gameplay_seen = time.monotonic()
        await view.sync(client, True)
        self.assertEqual(client.last_game[projection.KEY], proof)
        self.assertEqual([call.args[0] for call in client.bridge.command.await_args_list],
                         [projection.SYNC, projection.SYNC])

    async def test_old_epoch_cannot_reclaim_but_same_durable_drop_gets_new_projection(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        prior = (client.last_game[projection.KEY]["epoch"], ID)
        view.rebind()  # The same host and zone can admit a fresh transport.
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, prior)
        client.gameplay_action.assert_not_awaited()
        await view.sync(client, True)
        fresh = (client.last_game[projection.KEY]["epoch"], ID)
        self.assertNotEqual(prior, fresh)
        result = await view.reclaim(client, OPERATION, fresh)
        self.assertEqual(result[drop.RESULT]["state"], "reclaimed")
        client.gameplay_action.assert_awaited_once_with(drop.RECLAIM, OPERATION, ID)
        await view.sync(client, True)  # An old authenticated snapshot cannot redraw the claimed projection.
        self.assertNotIn(projection.KEY, client.last_game)
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, fresh)


    async def test_foreign_map_ledger_snapshot_cannot_rematerialize_the_old_drop(self):
        client, view = self.client(), projection.Projection()
        await view.sync(client, True)
        previous = client.last_game[projection.KEY]
        client.expected_zone = client.last_game["map"] = record.CROSSROADS
        client.last_game["pawns"] = {"local": {"name":
            f"Player_FP_C {record.CROSSROADS}.CrossRoads_P:PersistentLevel.Player_FP_C_404"}}
        with self.assertRaisesRegex(party.RuleError, "intent_stale"):
            await view.reclaim(client, OPERATION, (previous["epoch"], ID))
        await view.sync(client, True)
        client.bridge.command.assert_awaited_with(projection.CLEAR, timeout=2)
        client.gameplay_action.assert_not_awaited()
