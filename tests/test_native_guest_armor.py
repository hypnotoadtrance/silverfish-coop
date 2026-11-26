import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.silverfish import link, native_guest_armor as armor, party
from tools.silverfish.enemy_loot_profiles import BY_MAP
from tools.silverfish.travel import DEAD_BRIDGE, RAIL_YARD


class NativeGuestArmorTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        players = party.new_party()
        party.add_player(players, "guest-id")
        return SimpleNamespace(
            native_guest=True, native_zones=True, native_profile=True, stopping=False, fixtures=False,
            transitioning=False, native_checkpoint_unsafe=False, party=players, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": "area"}, checkpoint_native=AsyncMock(),
            native_recipients=lambda: {"guest-id": {"connected": True, "pawn": "exact pawn"}},
        )

    def report(self, **changes):
        return dict({"map": "area", "pawn": "exact pawn", "guid": "AB" * 16, "action": "equip",
                     "ok": True, "status": "equipped", "before": 2, "after": 1,
                     "carry_before": 1.5, "carry_after": 1.25, "minimum": 25, "slash": .2, "acid": .2}, **changes)

    async def test_equip_requires_exact_frame_and_replays_without_second_mutation(self):
        host = self.host()
        proof = self.report()
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        args = (host, bridge, "guest-id", "a" * 32, armor.ACTION, proof["guid"])
        equipped = await armor.perform(*args)
        bridge.command.assert_awaited_once_with(armor.ACTION, "exact pawn", proof["guid"], timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest-id"})})
        self.assertEqual(equipped["native_guest_armor"], dict(proof, class_path=armor.CLASS_PATH))
        self.assertEqual(await armor.perform(*args), equipped)
        self.assertEqual(bridge.command.await_count, 1)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertGreaterEqual(link.checkpoint_action_timeout(
            {"action": armor.ACTION, "operation_id": "a" * 32, "target": proof["guid"]}
        ), 15)

    async def test_local_worn_report_mirrors_through_stale_host_status_only_in_its_visit(self):
        host = self.host()
        host.zone_epoch = "a" * 32
        host.party["players"]["guest-id"]["downed"] = True
        proof = self.report()
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        args = (host, bridge, "guest-id", "f" * 32, armor.ACTION)
        with self.assertRaisesRegex(party.RuleError, "downed"):
            await armor.perform(*args, proof["guid"])
        with self.assertRaisesRegex(party.RuleError, "visit_expired"):
            await armor.perform(*args, f"{proof['guid']}|{'b' * 32}")
        bridge.command.assert_not_awaited()

        with patch.object(armor.native_inventory, "retain_guest_save", AsyncMock(return_value=True)):
            request = f"{proof['guid']}|{host.zone_epoch}"
            result = await armor.perform(*args, request)
            self.assertEqual(await armor.perform(*args, request), result)
        self.assertTrue(result["native_guest_armor"]["reported"])
        bridge.command.assert_awaited_once_with(armor.ACTION, "exact pawn", proof["guid"], "reported", timeout=5)
        host.checkpoint_native.assert_not_awaited()

    async def test_worn_slot_refusal_is_clean_and_projection_mismatch_is_unsafe(self):
        for proof in (self.report(ok=False, status="native_guest_armor_slot_occupied"),
                      self.report(carry_after=1.5), self.report(pawn="different pawn")):
            with self.subTest(proof=proof):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "area", "native_guest_armor": proof,
                }))
                with self.assertRaises(party.RuleError if not proof["ok"] else RuntimeError):
                    await armor.perform(host, bridge, "guest-id", "b" * 32, armor.ACTION, proof["guid"])
                self.assertEqual(host.stopping, proof["ok"])
                self.assertEqual(host.native_checkpoint_unsafe, proof["ok"])
                self.assertEqual(host.party["receipts"], {})
                host.checkpoint_native.assert_not_awaited()

    async def test_no_receipt_is_exposed_if_detached_checkpoint_fails(self):
        host = self.host()
        host.checkpoint_native.side_effect = RuntimeError("native guest frame missing")
        proof = self.report()
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        with self.assertRaisesRegex(RuntimeError, "native guest frame missing"):
            await armor.perform(host, bridge, "guest-id", "a" * 32, armor.ACTION, proof["guid"])
        self.assertTrue(host.stopping)
        self.assertTrue(host.native_checkpoint_unsafe)
        self.assertEqual(host.party["receipts"], {})

    async def test_stock_hazop_equip_and_distinct_armor_swap_keep_exact_replacement(self):
        old_guid = "FE" * 16
        equip = self.report(profile="hazop", carry_before=6.0, carry_after=5.0, minimum=32.0)
        swap = dict(equip, status="swapped", before=2, after=2, carry_after=5.25,
                    replaced_profile="bomber", replaced_guid=old_guid)
        reverse = self.report(guid=old_guid, profile="bomber", status="swapped", before=2, after=2,
                              carry_before=5.25, carry_after=6.0, minimum=25.0, replaced_profile="hazop", replaced_guid=equip["guid"])
        for proof in (equip, swap, reverse):
            with self.subTest(proof=proof):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={"state": "hosting", "map": "area", "native_guest_armor": proof}))
                args = (host, bridge, "guest-id", "c" * 32, armor.ACTION, proof["guid"])
                result = await armor.perform(*args)
                self.assertTrue(armor.effects(result["native_guest_armor"]))
                self.assertEqual(result["native_guest_armor"]["class_path"], armor.PROFILES[proof["profile"]].class_path)
                self.assertEqual(result["native_guest_armor"].get("replaced_guid"), proof.get("replaced_guid"))
                self.assertEqual(await armor.perform(*args), result)
                self.assertEqual(bridge.command.await_count, 1)
        for replacement in (dict(replaced_profile="hazop"), dict(replaced_guid=equip["guid"]), dict(carry_after=5.0),
                            dict(replaced_class_path=armor.HAZOP_CLASS_PATH)):
            self.assertFalse(armor.effects({**swap, **replacement}))

    def test_runtime_head_and_chest_defaults_require_matching_slots_and_combined_stats(self):
        head_path = "/Game/Classes/Armor_Items/Helm_Demo.Helm_Demo_C"
        chest_path = "/Game/Classes/Armor_Items/Armor_LabCoat.Armor_LabCoat_C"
        head = f"{head_path},1.7,.1,.2,.5,20,1"
        chest = f"{chest_path},.5,.15,.3,.3,22,0"
        fields = ["native-guest-helmet", "area", "Player_FP_C area.Test", "AB" * 16,
                  "equip", "1", "equipped", "2", "1", "6", "5.15", "22", ".1", ".2",
                  head, "-", "-", chest, ".25", ".5", ".8", "1"]
        proof = armor.parse(fields)
        self.assertEqual(proof["class_path"], head_path)
        self.assertEqual(armor.equip_action(head_path), armor.HELMET_ACTION)
        self.assertEqual(armor.equip_action(chest_path), armor.ACTION)
        self.assertTrue(armor.helmet_effects(proof))
        for index, value in ((14, chest), (20, ".5"), (21, "0")):
            with self.subTest(index=index):
                changed = list(fields)
                changed[index] = value
                with self.assertRaises(ValueError):
                    armor.parse(changed)

    def test_legacy_and_exact_profile_status_frames(self):
        old = ["native-guest-armor", "area", "pawn", "AB" * 16, "equip", "1", "equipped", "2", "1", "6", "5.75", "25", ".2", ".2"]
        self.assertTrue(armor.effects(armor.parse(old)))
        new = [*old[:6], "swapped", "2", "2", "6", "5.25", "32", ".2", ".2", "hazop", "bomber", "FE" * 16]
        proof = armor.parse(new)
        self.assertEqual((proof["class_path"], proof["replaced_class_path"]), (armor.HAZOP_CLASS_PATH, armor.CLASS_PATH))
        self.assertTrue(armor.effects(proof))
        with self.assertRaisesRegex(ValueError, "Invalid exact stock armor profile"):
            armor.parse([*new[:-2], "bomber", "-"])

    def test_exact_helmet_and_existing_body_compose_only_their_stock_values(self):
        pawn = f"Player_FP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Player_FP_C_7"
        picked_up = ["native-guest-helmet", RAIL_YARD, pawn, "FA" * 16, "equip", "1", "equipped",
                     "2", "1", "6", "5.8", "20", ".1", ".2", "dust-filter", "-", "-", "-", ".1", ".2", ".5"]
        alone = armor.parse(picked_up)
        self.assertEqual(alone["class_path"], armor.DUST_FILTER_PATH)
        self.assertTrue(armor.helmet_effects(alone))
        self.assertTrue(armor.helmet_effects(dict(alone, torso_profile="bomber", minimum=25, combined_slash=.3, combined_acid=.4)))
        self.assertTrue(armor.helmet_effects(dict(alone, torso_profile="hazop", minimum=32,
                                                 combined_slash=.3, combined_acid=.4, combined_poison=.6)))
        for column, value in [(2, "foreign pawn"), (10, "6"), (11, "25"), (14, "hazop"),
                              (18, ".3"), (19, ".5"), (20, "nan")]:
            with self.subTest(column=column), self.assertRaises(ValueError):
                frame = picked_up.copy()
                frame[column] = value
                armor.parse(frame)

    def test_ordinary_gear_profiles_keep_exact_slots_and_stock_effects(self):
        for path in (armor.BASIC_HELMET_PATH, armor.GOGGLES_PATH):
            self.assertEqual(armor.EQUIP_ACTIONS[path], armor.HELMET_ACTION)
        self.assertEqual(armor.EQUIP_ACTIONS[armor.HUNTER_JACKET_PATH], armor.ACTION)
        self.assertNotIn("/Game/Classes/Armor_Items/Helm_FaceMask.Helm_FaceMask_C", armor.EQUIP_ACTIONS)

        jacket = self.report(profile="hunter-jacket", class_path=armor.HUNTER_JACKET_PATH, carry_before=6, carry_after=5.4,
                             minimum=25, slash=.1, acid=.05)
        self.assertTrue(armor.effects(jacket))
        for change in (dict(carry_after=5.75), dict(slash=.2), dict(acid=.2), dict(class_path=armor.CLASS_PATH)):
            self.assertFalse(armor.effects(dict(jacket, **change)))
        self.assertTrue(armor.effects(dict(jacket, status="swapped", carry_after=5.65, replaced_profile="bomber",
                                          replaced_guid="FA" * 16, replaced_class_path=armor.CLASS_PATH)))

        pawn = f"Player_FP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Player_FP_C_7"
        frame = ["native-guest-helmet", RAIL_YARD, pawn, "FA" * 16, "project", "1", "reapplied",
                 "1", "1", "6", "6", "25", ".1", ".1", "basic-helmet", "-", "-", "hunter-jacket", ".2", ".15", "0"]
        proof = armor.parse(frame)
        self.assertEqual(proof["class_path"], armor.BASIC_HELMET_PATH)
        self.assertFalse(armor.helmet_effects(dict(proof, status="equipped", carry_after=5.3)))
        self.assertFalse(armor.helmet_effects(dict(proof, combined_poison=.5)))
        self.assertFalse(armor.helmet_effects(dict(proof, torso_profile="bomber")))

    def test_state_gear_has_distinct_stock_slots_and_recognition(self):
        self.assertEqual(armor.EQUIP_ACTIONS[armor.CONSCRIPT_UNIFORM_PATH], armor.ACTION)
        self.assertEqual(armor.EQUIP_ACTIONS[armor.STANDARD_INFANTRY_HELMET_PATH], armor.HELMET_ACTION)
        map_profile = BY_MAP[DEAD_BRIDGE]
        pawn = f"Player_FP_C {map_profile.world}:PersistentLevel.Player_FP_C_1"
        helmet = armor.parse(["native-guest-helmet", DEAD_BRIDGE, pawn, "FA" * 16, "equip", "1", "equipped",
                              "2", "1", "7.2", "6", "28", ".1", ".2", "standard-infantry-helmet", "-", "-",
                              "conscript-uniform", ".25", ".4", ".9", "1"])
        self.assertEqual(helmet["class_path"], armor.STANDARD_INFANTRY_HELMET_PATH)
        self.assertFalse(armor.helmet_effects(dict(helmet, voice_filter=False)))
        frame = [armor.DISGUISE_PROBE, DEAD_BRIDGE, pawn, map_profile.actors[0], "0", "0", "12", "5",
                 "4", "0", "4", "0"]
        proof = armor.parse_recognition(frame)
        self.assertEqual(proof["host"], dict(configured=0, compromised=False, recognized=12, relation=5))
        self.assertEqual(proof["guest"], dict(configured=4, compromised=False, recognized=4, relation=0))
        for index, value in ((2, "foreign pawn"), (3, "foreign NPC"), (5, "2"), (10, "15"), (11, "7")):
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                changed = frame.copy()
                changed[index] = value
                armor.parse_recognition(changed)

    async def test_hazard_suit_fixture_and_owned_equip_use_the_stock_body_profile(self):
        guid = "AB" * 16
        pawn = f"Player_FP_C {RAIL_YARD}.RailYard_P:PersistentLevel.Player_FP_C_7"
        self.assertEqual(armor.EQUIP_ACTIONS[armor.HAZARD_SUIT_PATH], armor.ACTION)
        seed = armor.parse(["native-guest-armor", "area", pawn, guid, "seed", "1", "seeded",
                            "3", "4", "-1", "-1", "-1", "-1", "-1", "hazard-suit", "-", "-"])
        suit = armor.parse(["native-guest-armor", "area", pawn, guid, "equip", "1", "equipped",
                            "4", "3", "12", "6.75", "28", ".3", ".5", "hazard-suit", "-", "-"])
        host = self.host()
        host.native_recipients = lambda: {"guest-id": {"connected": True, "pawn": pawn}}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=tuple(
            {"state": "hosting", "map": "area", "native_guest_armor": row} for row in (seed, suit))))
        args = (host, bridge, "guest-id")
        with self.assertRaisesRegex(party.RuleError, "native_guest_armor_unavailable"):
            await armor.perform(*args, "a" * 32, armor.FIXTURE, guid, seed_kind="hazard-suit")
        bridge.command.assert_not_awaited()

        host.fixtures = True
        seeded = await armor.perform(*args, "a" * 32, armor.FIXTURE, guid, seed_kind="hazard-suit")
        self.assertEqual(seeded["native_guest_armor"]["class_path"], armor.HAZARD_SUIT_PATH)
        bridge.command.assert_awaited_once_with(armor.FIXTURE, pawn, guid, "hazard-suit", timeout=5)

        host.fixtures = False
        result = await armor.perform(*args, "b" * 32, armor.ACTION, guid)
        self.assertEqual(result["native_guest_armor"], suit)
        self.assertTrue(armor.effects(suit))
        self.assertFalse(armor.effects(dict(suit, acid=.2)))
        self.assertEqual(await armor.perform(*args, "b" * 32, armor.ACTION, guid), result)
        self.assertEqual(bridge.command.await_count, 2)

        helmet = armor.parse(["native-guest-helmet", RAIL_YARD, pawn, "CD" * 16, "project", "1", "reapplied",
                              "1", "1", "7", "7", "28", ".1", ".2", "dust-filter", "-", "-",
                              "hazard-suit", ".4", ".7", ".6", "1"])
        self.assertTrue(armor.helmet_effects(helmet))
        self.assertFalse(armor.helmet_effects(dict(helmet, combined_acid=.5)))
        self.assertFalse(armor.helmet_effects(dict(helmet, voice_filter=False)))

    async def test_goggles_replace_distinct_dust_filter_and_require_exact_guest_checkpoint(self):
        frame = ["native-guest-helmet", "area", "Player_FP_C area.World:PersistentLevel.Player_FP_C_1",
                 "AB" * 16, "equip", "1", "swapped", "2", "2", "5", "5.15", "32", "0", ".1",
                 "goggles", "dust-filter", "FA" * 16, "hazop", ".2", ".3", ".1", "0"]
        proof = armor.parse(frame)
        self.assertEqual((proof["class_path"], proof["replaced_class_path"]), (armor.GOGGLES_PATH, armor.DUST_FILTER_PATH))
        self.assertIs(proof["voice_filter"], False)
        for value in ("1", "-"):
            with self.assertRaises(ValueError):
                armor.parse([*frame[:-1], value])
        for change in (dict(replaced_guid=proof["guid"]), dict(replaced_class_path=armor.BASIC_HELMET_PATH),
                       dict(replaced_profile="goggles"), dict(combined_poison=.6), dict(carry_after=5)):
            self.assertFalse(armor.helmet_effects(dict(proof, **change)))
        host = self.host()
        host.native_recipients = lambda: {"guest-id": {"connected": True, "pawn": proof["pawn"]}}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_helmet": proof,
        }))
        args = (host, bridge, "guest-id", "d" * 32, armor.HELMET_ACTION, proof["guid"])
        self.assertEqual(await armor.perform(*args), {"native_guest_helmet": proof})
        self.assertEqual(await armor.perform(*args), {"native_guest_helmet": proof})
        bridge.command.assert_awaited_once_with(armor.HELMET_ACTION, proof["pawn"], proof["guid"], timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest-id"})})

    async def test_named_stock_fixture_only_accepts_exact_profile_and_replays_its_own_receipt(self):
        proof = armor.parse(["native-guest-armor", "area", "exact pawn", "AB" * 16, "seed", "1", "seeded",
                             "3", "4", "-1", "-1", "-1", "-1", "-1", "basic-helmet", "-", "-"])
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "area", "native_guest_armor": proof,
        }))
        host = self.host()
        args = (host, bridge, "guest-id", "e" * 32, armor.FIXTURE, proof["guid"])
        with self.assertRaisesRegex(party.RuleError, "native_guest_armor_unavailable"):
            await armor.perform(*args, seed_kind="basic-helmet")
        host.fixtures = True
        with self.assertRaisesRegex(party.RuleError, "native_guest_armor_fixture_profile_unavailable"):
            await armor.perform(*args, seed_kind="face-mask")
        bridge.command.assert_not_awaited()
        result = await armor.perform(*args, seed_kind="basic-helmet")
        self.assertEqual(result["native_guest_armor"]["class_path"], armor.BASIC_HELMET_PATH)
        self.assertEqual(await armor.perform(*args, seed_kind="basic-helmet"), result)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await armor.perform(*args, seed_kind="goggles")
        bridge.command.assert_awaited_once_with(armor.FIXTURE, proof["pawn"], proof["guid"], "basic-helmet", timeout=5)
        self.assertEqual(host.checkpoint_native.await_args.kwargs, {"require_guest_frames": frozenset({"guest-id"})})

    async def test_helmet_checkpoint_and_replay_use_separate_intent(self):
        host = self.host()
        proof = self.report(profile="dust-filter", class_path=armor.DUST_FILTER_PATH, torso_profile="bomber",
                            carry_before=5.0, carry_after=4.8, slash=.1, acid=.2,
                            combined_slash=.3, combined_acid=.4, combined_poison=.5)
        bridge = SimpleNamespace(command=AsyncMock(return_value={"state": "hosting", "map": "area", "native_guest_helmet": proof}))
        args = (host, bridge, "guest-id", "a" * 32, armor.HELMET_ACTION, proof["guid"])
        equipped = await armor.perform(*args)
        self.assertEqual(equipped["native_guest_helmet"], proof)
        self.assertEqual(host.checkpoint_native.await_args.kwargs["require_guest_frames"], frozenset({"guest-id"}))
        self.assertEqual(await armor.perform(*args), equipped)
        bridge.command.assert_awaited_once_with(armor.HELMET_ACTION, "exact pawn", proof["guid"], timeout=5)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await armor.perform(host, bridge, "guest-id", "a" * 32, armor.ACTION, proof["guid"])
        self.assertGreaterEqual(link.checkpoint_action_timeout(
            {"action": armor.HELMET_ACTION, "operation_id": "a" * 32, "target": proof["guid"]}
        ), 20)
