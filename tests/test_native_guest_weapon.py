from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import (
    generate_guest_weapon_registry, guest_weapon_profiles, native_guest_consumable, native_guest_weapon, party,
)


class NativeGuestWeaponProfilesTests(unittest.TestCase):
    def test_game_adapters_match_launcher_profiles(self):
        repo = Path(__file__).resolve().parents[1]
        for filename, expected in generate_guest_weapon_registry.outputs().items():
            with self.subTest(filename=filename):
                self.assertEqual((repo / filename).read_text(encoding="utf-8"), expected)

    def test_ordinary_pistol_classes_are_exact(self):
        for kind, blueprint in {
            "army-pistol": "Wep_ArmyPistol", "pistol": "Wep_Pistol",
            "rusty-pistol": "Wep_RustyPistol",
            "pistol-02": "Wep_Pistol02", "tac-pistol": "Wep_TacPistol", "model-1931": "Wep_Pistol_Long",
        }.items():
            path = f"/Game/Classes/Weapons/{blueprint}.{blueprint}_C"
            self.assertEqual(native_guest_weapon.FIRE_CLASSES[kind], path)
            self.assertIn(path, native_guest_weapon.CLASS_PATHS)
            self.assertIn(kind, native_guest_weapon.SEED_KINDS)
        self.assertNotIn("revolver-carbine-cult", native_guest_weapon.PRIMARY_KINDS)

    def test_45_ball_ammo_keeps_the_audited_item_and_projectile(self):
        ammo = guest_weapon_profiles.FORTY_FIVE_BALL
        self.assertEqual((ammo.caliber, ammo.row, ammo.item, ammo.projectile, ammo.fixture_units),
                         (1, "45Cal_Ball", "Ammo_Magnum_BP", "Projectile_Pistol_Magnum", 10))
        firearms = {
            profile.kind: (profile.blueprint, profile.magazine.capacity, profile.magazine.burst_length)
            for profile in guest_weapon_profiles.FIREARMS
            if profile.magazine and profile.magazine.ammo == ammo
        }
        self.assertEqual(firearms["1911"], ("Wep_1911", 8, 1))
        self.assertEqual(firearms["1911-02"], ("Wep_1911_02", 16, 4))
        self.assertEqual(firearms["revolver-01"], ("Wep_Revolver_01", 6, 1))
        self.assertEqual(firearms["revolver-snub"], ("Wep_Revolver_SnubNose", 6, 1))

    def test_alternate_round_stays_with_compatible_receivers(self):
        alternate = guest_weapon_profiles.FORTY_FIVE_HOLLOW_POINT.item_path
        ball = guest_weapon_profiles.FORTY_FIVE_BALL.item_path
        self.assertTrue(native_guest_weapon.valid_mounted_ammo("1911", alternate))
        self.assertTrue(native_guest_weapon.valid_mounted_ammo("heavy-pistol-silenced", ball))
        self.assertFalse(native_guest_weapon.valid_mounted_ammo("rusty-pistol", alternate))

    def test_9mm_revolver_uses_the_stock_seven_round_ball_magazine(self):
        path = "/Game/Classes/Weapons/Wep_Revolver_9mm.Wep_Revolver_9mm_C"
        self.assertEqual(native_guest_weapon.FIRE_CLASSES["revolver-9mm"], path)
        self.assertIn("revolver-9mm", native_guest_weapon.SEED_KINDS)
        self.assertEqual(guest_weapon_profiles.TAKE_AMMO_MAGAZINES[path],
                         guest_weapon_profiles.Magazine(7, 1, 0.2, True, reload_first_stack=True))

    def test_long_barrel_revolvers_keep_their_cooked_ammo_rows(self):
        hollow = guest_weapon_profiles.FORTY_FIVE_HOLLOW_POINT
        self.assertEqual((hollow.caliber, hollow.row, hollow.item, hollow.projectile, hollow.fixture_units),
                         (1, "45Cal_HollowPoint", "Ammo_Magnum_HollowPoint_BP", "Projectile_45_HollowPoint", 18))
        for index, ammo in enumerate((guest_weapon_profiles.FORTY_FIVE_BALL, hollow, hollow), start=1):
            kind = f"revolver-0{index}"
            path = f"/Game/Classes/Weapons/Wep_Revolver_0{index}.Wep_Revolver_0{index}_C"
            with self.subTest(kind=kind):
                self.assertEqual(native_guest_weapon.FIRE_CLASSES[kind], path)
                self.assertIn(kind, native_guest_weapon.SEED_KINDS)
                magazine = guest_weapon_profiles.TAKE_AMMO_MAGAZINES[path]
                self.assertEqual((magazine.capacity, magazine.burst_length, magazine.fire_rate, magazine.ammo),
                                 (6, 1, 0.5, ammo))
                self.assertTrue(magazine.require_default_payload and magazine.reload_first_stack)
        self.assertEqual({profile.kind for profile in guest_weapon_profiles.FIREARMS
                          if profile.magazine and profile.magazine.ammo == hollow},
                         {"revolver-02", "revolver-03", "heavy-pistol-silenced", "tac-hmp2"})

    def test_obrez_routes_primary_to_authority_without_an_ordinary_magazine_profile(self):
        path = "/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C"
        self.assertEqual(native_guest_weapon.FIRE_CLASSES["obrez"], path)
        self.assertIn("obrez", native_guest_weapon.PRIMARY_KINDS)
        self.assertNotIn(path, guest_weapon_profiles.TAKE_AMMO_MAGAZINES)

    def test_qualified_shotguns_use_nine_pellets_per_shell(self):
        ammo = guest_weapon_profiles.TWELVE_GAUGE_BUCK
        self.assertEqual((ammo.caliber, ammo.row, ammo.item, ammo.projectile, ammo.fixture_units, ammo.projectile_count),
                         (4, "12Gauge_Buck", "Ammo_ShotgunShells_BP", "Projectile_ShotGun_Pellet", 5, 9))
        profiles = [profile for profile in guest_weapon_profiles.FIREARMS
                    if profile.magazine and profile.magazine.ammo == ammo]
        self.assertEqual([(p.kind, p.blueprint, p.magazine.capacity, p.magazine.burst_length,
                           p.magazine.fire_rate, p.magazine.action_on_release) for p in profiles],
                         [("sg-71-a", "Wep_ShotGun_SemiAuto", 10, 1, 0.2, False),
                          ("sg-71-b", "Wep_ShotGun_SemiAuto_02", 10, 1, 0.2, False),
                          ("sg-71-a-suppressed", "Wep_ShotGun_SemiAuto_Suppressed", 5, 1, 0.2, False),
                          ("bolt-action-shotgun", "Wep_BoltAction_Shotgun", 4, 1, 1.3, True),
                          ("bolt-action-shotgun-suppressed", "Wep_BoltAction_Shotgun_Suppressed", 4, 1, 1.3, True),
                          ("sawn-off-shotgun", "Wep_SawnOff_ShotGun", 2, 1, 0.1, False)])
        self.assertTrue(all(p.magazine.require_default_payload and p.magazine.reload_first_stack for p in profiles))
        self.assertNotIn("bolt-action-shotgun-loki", native_guest_weapon.PRIMARY_KINDS)
        self.assertFalse({p.kind for p in profiles} & guest_weapon_profiles.STOCK_HELD_FIRE_MAGAZINES.keys())

    def test_exact_fennec_ap_profiles_use_one_stock_projectile(self):
        ammo = guest_weapon_profiles.FIVE_SEVEN_AP
        self.assertEqual((ammo.caliber, ammo.row, ammo.item, ammo.projectile, ammo.fixture_units, ammo.projectile_count),
                         (11, "5-7_AP", "Ammo_5-7_AP_BP", "Projectile_5-7_AP", 7, 1))
        profiles = [p for p in guest_weapon_profiles.FIREARMS if p.magazine and p.magazine.ammo == ammo]
        self.assertEqual([(p.kind, p.blueprint, p.magazine.capacity, p.magazine.burst_length, p.magazine.fire_rate) for p in profiles],
                         [("fennec-57", "Wep_Pistol_Fennec_01", 17, 1, 0.06),
                          ("black-fox", "Wep_Pistol_Fennec_02", 17, 1, 0.06),
                          ("rifle-57-01", "Wep_5-7AR_01", 50, 4, 0.05),
                          ("rifle-57-02", "Wep_5-7AR_02", 50, 4, 0.05),
                          ("rifle-57-03", "Wep_5-7AR_03", 50, 4, 0.05),
                          ("vsk-01", "Wep_VSK_01", 20, 0, 0.0857)])
        self.assertTrue(all(p.magazine.require_default_payload and p.magazine.reload_first_stack for p in profiles))

    def test_only_audited_magazines_use_first_stack_reload(self):
        same_stack = {profile.kind for profile in guest_weapon_profiles.FIREARMS
                      if profile.magazine and profile.magazine.reload_first_stack}
        self.assertEqual(same_stack, {
            "army-pistol", "pistol", "pistol-02", "tac-pistol", "1911", "1911-fancy", "heavy-pistol",
            "rusty-pistol",
            "sg-71-a", "sg-71-b", "sg-71-a-suppressed", "model-1931", "fennec-57", "black-fox", "break-action-50",
            "bolt-action-shotgun", "bolt-action-shotgun-suppressed", "sawn-off-shotgun",
            "revolver-9mm", "revolver-01", "revolver-02", "revolver-03",
            *(kind for kind, *_ in guest_weapon_profiles.ORDINARY_WEAPONS),
        })
        self.assertEqual(guest_weapon_profiles.MAX_TAKE_AMMO_ROUNDS, 64)
        self.assertEqual(len(native_guest_weapon.FIRE_CLASSES), len(guest_weapon_profiles.FIREARMS))

    def test_stock_light_ar_family_uses_the_audited_intermediate_fmj_magazine(self):
        ammo = guest_weapon_profiles.FIVE_SIX_FMJ
        self.assertEqual((ammo.caliber, ammo.row, ammo.item, ammo.projectile, ammo.fixture_units, ammo.projectile_count),
                         (2, "5.6Intr_FMJ", "Ammo_IntrRifle_BP", "Projectile_Rifle_Inter", 7, 1))
        family = {profile.kind: profile for profile in guest_weapon_profiles.FIREARMS
                  if profile.blueprint.startswith("Wep_LightAR_")}
        self.assertEqual({kind: profile.blueprint for kind, profile in family.items()}, {
            "light-ar-01": "Wep_LightAR_01", "light-ar-02": "Wep_LightAR_02", "light-ar-03": "Wep_LightAR_03",
            "light-ar-cqc": "Wep_LightAR_CQC", "light-ar-silenced": "Wep_LightAR_Silenced",
        })
        for kind, profile in family.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, native_guest_weapon.PRIMARY_KINDS)
                self.assertTrue(profile.headless_seed)
                self.assertEqual(profile.magazine, guest_weapon_profiles.Magazine(
                    24, 0, 0.1, require_default_payload=True, ammo=ammo, reload_first_stack=True))

    def test_held_fire_registry_includes_large_automatic_and_burst_magazines(self):
        magazines = guest_weapon_profiles.STOCK_HELD_FIRE_MAGAZINES
        self.assertEqual(magazines["bizon-01"].capacity, 64)
        self.assertEqual(magazines["light-ar-01"].capacity, 24)
        self.assertEqual(magazines["smg-standard"].capacity, 30)
        self.assertEqual(magazines["smg-03"].burst_length, 3)
        self.assertNotIn("rusty-pistol", magazines)
        self.assertNotIn("sg-71-a", magazines)
        self.assertEqual(guest_weapon_profiles.MAX_HELD_FIRE_ROUNDS, 64)


class NativeGuestInventoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def host(self):
        player_party = party.new_party()
        party.add_player(player_party, "character")
        return SimpleNamespace(
            native_guest=True, native_profile=True, fixtures=False, transitioning=False,
            stopping=False, native_checkpoint_unsafe=False, last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": "zone"},
            native_recipients=lambda: {"character": {"connected": True, "pawn": "exact pawn"}},
            party=player_party, native_pickup=Mock(), control=Mock(),
            checkpoint_native=AsyncMock(),
        )

    async def test_quick_slot_selection_uses_the_enrolled_guest_without_a_checkpoint(self):
        host = self.host()
        host.native_zones, host.zone_epoch = True, "current"
        guid = "1" * 32
        proof = {"map": "zone", "action": "select", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "equipped", "actor_ammo": 1, "stored_ammo": 1}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone", "native_guest_weapon": proof,
        }))
        with self.assertRaisesRegex(party.RuleError, "selection_expired"):
            await native_guest_weapon.select_equipment(host, bridge, "character", f"previous@{guid}")
        bridge.command.assert_not_awaited()

        result = await native_guest_weapon.select_equipment(host, bridge, "character", f"current@{guid}")
        self.assertEqual(result, {"native_guest_weapon": proof})
        bridge.command.assert_awaited_once_with(native_guest_weapon.SELECT_EQUIPMENT, "exact pawn", guid, timeout=3)
        self.assertFalse(host.native_checkpoint_unsafe)
        self.assertFalse(host.stopping)
        self.assertEqual(host.party["receipts"], {})
        host.checkpoint_native.assert_not_awaited()

    async def test_selector_choice_retains_the_enrolled_guest_save_without_a_checkpoint(self):
        host = self.host()
        host.native_zones, host.zone_epoch = True, "current"
        host.last_game["native_guest_save_retention"] = True
        host.pending_guest_saves = {}
        host.queue_guest_save = Mock()
        guid = "1" * 32
        current = {"state": "hosting", "map": "zone"}
        bridge = SimpleNamespace(command=AsyncMock(side_effect=[current, {**current, "ack": {"message": "semi"}}]))
        for target in (f"previous@{guid}@semi", f"current@{guid}@burst"):
            with self.assertRaisesRegex(party.RuleError, "fire_mode_expired"):
                await native_guest_weapon.store_fire_mode(host, bridge, "character", target)
        bridge.command.assert_not_awaited()

        result = await native_guest_weapon.store_fire_mode(host, bridge, "character", f"current@{guid}@semi")
        self.assertEqual(result, {"guid": guid, "fire_mode": "semi"})
        self.assertEqual(bridge.command.await_args_list[0].args, ("native-guest-save-retain", "exact pawn", "character"))
        self.assertEqual(bridge.command.await_args_list[1].args,
                         (native_guest_weapon.SET_FIRE_MODE, "exact pawn", guid, "semi"))
        host.queue_guest_save.assert_called_once_with("character", "exact pawn")
        host.checkpoint_native.assert_not_awaited()

    async def test_stale_binding_denies_weapon_request_without_stopping_host(self):
        for action, target in (("fire", "1" * 32), ("equip", "1" * 32), ("reload", "equipped")):
            with self.subTest(action=action):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("guest_weapon_refused_detached_save")))
                with self.assertRaisesRegex(party.RuleError, "^guest_weapon_refused_detached_save$"):
                    await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                                      f"native-guest-weapon-{action}", target)
                bridge.command.assert_awaited_once_with(f"native-guest-weapon-{action}", "exact pawn", target, timeout=5)
                self.assertFalse(host.stopping)
                self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(host.party["receipts"], {})
                host.checkpoint_native.assert_not_awaited()

    async def test_trigger_view_is_scoped_to_the_current_visit(self):
        host = self.host()
        host.zone_epoch = "current"
        guid, operation = "1" * 32, "a" * 32
        action = "native-guest-weapon-fire"
        direction = "0.600000,-0.800000,0.000000"
        eye, target, muzzle = "7.000,9.000,1100.000", "607.000,-791.000,1100.000", "53.000,-32.000,1094.000"
        shot = f"{direction};{eye};{target};{muzzle}"
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone",
            "native_guest_weapon": {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid,
                                    "ok": True, "status": "equipped", "actor_ammo": 5, "stored_ammo": 5,
                                    "before": 6, "after": 5, "projectiles": 1},
        }))
        for invalid in (f"{guid}@previous@{shot}", f"{guid}@current@0.000000,0.000000,0.000000",
                        f"{guid}@current@{direction};{eye}", f"{guid}@current@{direction};{eye};1e10,0,0"):
            with self.assertRaisesRegex(party.RuleError, "view_unqualified"):
                await native_guest_weapon.perform(host, bridge, "character", operation, action, invalid)
        bridge.command.assert_not_awaited()

        result = await native_guest_weapon.perform(host, bridge, "character", operation, action,
                                                   f"{guid}@current@{shot}")
        bridge.command.assert_awaited_once_with(action, "exact pawn", guid, shot, timeout=5)
        self.assertEqual(result["native_guest_trigger_direction"], direction)
        self.assertEqual(result["native_guest_trigger_view"], {"eye": eye, "target": target, "muzzle": muzzle})
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await native_guest_weapon.perform(host, bridge, "character", operation, action,
                                              f"{guid}@current@{direction};{eye};{target}")

    async def test_missing_acknowledgment_keeps_host_checkpoint_unsafe(self):
        host = self.host()
        bridge = SimpleNamespace(command=AsyncMock(side_effect=TimeoutError("missing acknowledgment")))
        with self.assertRaises(TimeoutError):
            await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                              "native-guest-weapon-fire", "1" * 32)
        self.assertTrue(host.stopping)
        self.assertTrue(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()

    async def test_failed_native_shot_readback_keeps_host_checkpoint_unsafe(self):
        host = self.host()
        guid = "1" * 32
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone",
            "native_guest_weapon": {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid,
                                    "ok": False, "status": "mutation_unverified_guest_weapon_shot"},
        }))
        with self.assertRaisesRegex(RuntimeError, "mutation_unverified_guest_weapon_shot"):
            await native_guest_weapon.perform(host, bridge, "character", "a" * 32, "native-guest-weapon-fire", guid)
        self.assertTrue(host.stopping)
        self.assertEqual(host.session_stop_reason, 'native_guest_weapon_verification_failed')
        host.control.event.assert_called_once()
        self.assertTrue(host.native_checkpoint_unsafe)
        host.checkpoint_native.assert_not_awaited()

    async def test_verified_weapon_shot_requires_exact_guest_checkpoint(self):
        guid, operation = "1" * 32, "a" * 32
        proof = {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "equipped", "actor_ammo": 5, "stored_ammo": 5, "before": 6, "after": 5, "projectiles": 1}
        for missing in (False, True):
            with self.subTest(missing=missing):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_weapon": proof,
                }))
                if missing:
                    host.checkpoint_native.side_effect = RuntimeError("Required native guest frame is unavailable")
                    with self.assertRaisesRegex(RuntimeError, "Required native guest frame"):
                        await native_guest_weapon.perform(host, bridge, "character", operation,
                                                          "native-guest-weapon-fire", guid)
                    self.assertTrue(host.stopping)
                    self.assertTrue(host.native_checkpoint_unsafe)
                    self.assertNotIn("character", host.party["receipts"])
                else:
                    result = await native_guest_weapon.perform(host, bridge, "character", operation,
                                                               "native-guest-weapon-fire", guid)
                    self.assertEqual(result["native_guest_weapon"], proof)
                    self.assertEqual(host.party["receipts"]["character"][0]["id"], operation)
                    self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(host.checkpoint_native.await_args.kwargs,
                                 {"require_guest_frames": frozenset({"character"})})

    async def test_weapon_inspection_does_not_checkpoint(self):
        host = self.host()
        guid = "1" * 32
        proof = {"map": "zone", "action": "inspect", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "backpack", "actor_ammo": -1, "stored_ammo": 6}
        bridge = SimpleNamespace(command=AsyncMock(return_value={
            "state": "hosting", "map": "zone", "native_guest_weapon": proof,
        }))
        self.assertEqual(await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                                            "native-guest-weapon-inspect", guid),
                         {"native_guest_weapon": proof})
        host.checkpoint_native.assert_not_awaited()

    async def test_nine_projectile_receipt_requires_same_authenticated_equipped_shotgun(self):
        guid, operation = "1" * 32, "a" * 32
        proof = {"map": "zone", "action": "fire", "pawn": "exact pawn", "guid": guid, "ok": True,
                 "status": "equipped", "actor_ammo": 5, "stored_ammo": 5, "before": 6, "after": 5, "projectiles": 9}
        for pawn, kind, equipped_guid, count, accepted in (
            ("exact pawn", "sg-71-a", guid, 9, True),
            ("exact pawn", "sg-71-a", "2" * 32, 9, False),
            ("other pawn", "sg-71-a", guid, 9, False),
            ("exact pawn", "pistol", guid, 9, False),
            ("exact pawn", "sg-71-a", guid, 1, False),
            ("exact pawn", "sg-71-a", guid, 8, False),
        ):
            with self.subTest(pawn=pawn, kind=kind, equipped_guid=equipped_guid, count=count):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_weapon": dict(proof, projectiles=count),
                    "native_guest_equipped_weapons": {pawn: {"kind": kind, "guid": equipped_guid}},
                }))
                request = native_guest_weapon.perform(
                    host, bridge, "character", operation, "native-guest-weapon-fire", guid
                )
                if accepted:
                    self.assertEqual((await request)["native_guest_weapon"], proof)
                    self.assertEqual(host.checkpoint_native.await_args.kwargs,
                                     {"require_guest_frames": frozenset({"character"})})
                else:
                    with self.assertRaisesRegex(RuntimeError, "projectile count"):
                        await request
                    self.assertTrue(host.native_checkpoint_unsafe)
                    self.assertTrue(host.stopping)
                    host.checkpoint_native.assert_not_awaited()

    async def test_verified_bandage_consumption_requires_exact_guest_checkpoint(self):
        guid, operation = "1" * 32, "b" * 32
        proof = {"state": "used", "map": "zone", "pawn": "exact pawn", "guid": guid,
                 "quantity_before": 2, "quantity_after": 1, "health_before": 25, "health_after": 25,
                 "regen_before": 25, "regen_after": 52, "max_health": 100, "hemophilia": True}
        for missing in (False, True):
            with self.subTest(missing=missing):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_bandage": proof,
                }))
                if missing:
                    host.checkpoint_native.side_effect = (5, RuntimeError("Required native guest frame is unavailable"))
                    with self.assertRaisesRegex(RuntimeError, "Required native guest frame"):
                        await native_guest_consumable.use(host, bridge, "character", guid, operation)
                    self.assertTrue(host.stopping)
                    self.assertTrue(host.native_checkpoint_unsafe)
                    self.assertNotIn("character", host.party["receipts"])
                    host.native_pickup.forget_committed_row.assert_not_called()
                else:
                    result = await native_guest_consumable.use(host, bridge, "character", guid, operation)
                    self.assertEqual(result["native_guest_bandage"], proof)
                    self.assertEqual(host.party["receipts"]["character"][0]["id"], operation)
                    self.assertFalse(host.native_checkpoint_unsafe)
                    host.native_pickup.forget_committed_row.assert_called_once_with("character", guid)
                before, after = host.checkpoint_native.await_args_list
                self.assertEqual(before.args, (bridge,))
                self.assertEqual(before.kwargs, {})
                self.assertEqual(after.kwargs, {"require_guest_frames": frozenset({"character"})})

    async def test_starter_bag_awards_only_after_typed_checkpoint_and_replays_once(self):
        action = native_guest_consumable.STARTER_BAG
        key = native_guest_consumable.RESULT[action]
        guid, operation, award = "1" * 32, "b" * 32, "A" * 32
        proof = {"state": "used", "map": "zone", "pawn": "exact pawn", "guid": guid,
                 "quantity_before": 1, "quantity_after": 0, "awarded_guids": [award],
                 "class_path": native_guest_consumable.STARTER_BAGS["random"]}
        for verified in (True, False):
            with self.subTest(verified=verified):
                host = self.host()
                host.last_game["native_paired_save_capture"] = True
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone",
                    key: proof if verified else dict(proof, awarded_guids=[award, guid]),
                }))
                pending = native_guest_consumable.use(host, bridge, "character", guid, operation, action=action)
                if not verified:
                    with self.assertRaisesRegex(RuntimeError, "exact stock rewards"):
                        await pending
                    self.assertTrue(host.stopping)
                    self.assertNotIn("character", host.party["receipts"])
                    host.native_pickup.forget_committed_row.assert_not_called()
                    host.checkpoint_native.assert_awaited_once_with(bridge)
                    continue
                result = await pending
                self.assertEqual(result, {key: proof})
                self.assertEqual(host.checkpoint_native.await_args.kwargs,
                                 {"require_guest_frames": frozenset({"character"}), "typed_inventory": True})
                self.assertEqual(await native_guest_consumable.use(host, bridge, "character", guid, operation,
                                                                  action=action), result)
                bridge.command.assert_awaited_once_with(action, "exact pawn", guid, timeout=6)

    def test_starter_bag_bridge_receipt_rejects_repeated_and_wrong_award_identities(self):
        area = "/Game/Map"
        source, award = "1" * 32, "A" * 32
        fields = [native_guest_consumable.STARTER_BAG, "used", area, "Player_FP_C /Game/Map.ExactPawn",
                  source, "hyperrandom", award]
        parsed = native_guest_consumable.parse_starter_bag(fields)
        self.assertEqual(parsed["class_path"], native_guest_consumable.STARTER_BAGS["hyperrandom"])
        self.assertEqual(parsed["awarded_guids"], [award])
        for invalid in (source, award + "," + award, award + ",", ""):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                native_guest_consumable.parse_starter_bag(fields[:-1] + [invalid])
