from pathlib import Path
import uuid
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import (
    generate_guest_weapon_registry, guest_weapon_profiles, native_guest_consumable, native_guest_weapon, party, runtime, service,
)


class NativeGuestWeaponProfilesTests(unittest.TestCase):
    def test_shot_contact_accepts_only_canonical_npc_references(self):
        geometry = "1.000000,0.000000,0.000000;0.000,0.000,0.000;1.000,0.000,0.000;0.000,0.000,0.000"
        for reference in ("npc:1", "npc:65535"):
            self.assertTrue(native_guest_weapon.valid_shot_view(f"{geometry}|{reference}|1.000,0.000,0.000"))
        for reference in ("npc:0", "npc:01", "npc:65536", "npc:-1", "npc:2.5"):
            self.assertFalse(native_guest_weapon.valid_shot_view(f"{geometry}|{reference}|1.000,0.000,0.000"))

    def test_shot_contact_accepts_placed_actors_in_mounted_and_streamed_worlds(self):
        geometry = "1.000000,0.000000,0.000000;0.000,0.000,0.000;1.000,0.000,0.000;0.000,0.000,0.000"
        for path in (
            "/FrontierPack/Maps/Canyon.Canyon:PersistentLevel.Worker_1",
            "/Game/Classes/Maps/Maps_Dungeons/CoopNpcSublevel.Tut_Dock_P:PersistentLevel.NPC_Survivor_Worker_C_1",
        ):
            self.assertTrue(native_guest_weapon.valid_shot_view(f"{geometry}|{path}|1.000,0.000,0.000"), path)
        for path in (
            "/FrontierPack/Maps/Canyon.Canyon:OtherLevel.Worker_1",
            "FrontierPack/Maps/Canyon.Canyon:PersistentLevel.Worker_1",
            "/FrontierPack/Maps/Canyon.Canyon:PersistentLevel.",
        ):
            self.assertFalse(native_guest_weapon.valid_shot_view(f"{geometry}|{path}|1.000,0.000,0.000"), path)

    def test_game_adapters_match_launcher_profiles(self):
        repo = Path(__file__).resolve().parents[1]
        for filename, expected in generate_guest_weapon_registry.outputs().items():
            with self.subTest(filename=filename):
                self.assertEqual((repo / filename).read_text(encoding="utf-8"), expected)

    def test_ordinary_pistol_classes_are_exact(self):
        for kind, blueprint in {
            "army-pistol": "Wep_ArmyPistol", "pistol": "Wep_Pistol",
            "rusty-pistol": "Wep_RustyPistol",
            "revolver-carbine-cult": "Wep_Revolver_Carbine_Cult", "revolver-9mm-silenced": "Wep_Revolver_9mm_Silenced",
            "pistol-02": "Wep_Pistol02", "tac-pistol": "Wep_TacPistol", "model-1931": "Wep_Pistol_Long",
        }.items():
            path = f"/Game/Classes/Weapons/{blueprint}.{blueprint}_C"
            self.assertEqual(native_guest_weapon.FIRE_CLASSES[kind], path)
            self.assertIn(path, native_guest_weapon.CLASS_PATHS)
            self.assertIn(kind, native_guest_weapon.SEED_KINDS)

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
        for kind, load in (
            ("rusty-pistol", guest_weapon_profiles.NINE_MM_VAMP),
            ("burst-pistol", guest_weapon_profiles.NINE_MM_AP),
            ("scout-rifle-02", guest_weapon_profiles.FIVE_SIX_AP),
            ("drakv-cult", guest_weapon_profiles.FIVE_SIX_STUN),
            ("battle-rifle-cult", guest_weapon_profiles.THIRTY_06_MINE),
            ("rifle-50-semi", guest_weapon_profiles.FIFTY_CAL_HEIAP),
            ("carbine-57", guest_weapon_profiles.FIVE_SEVEN_HP),
        ):
            with self.subTest(weapon=kind, ammo=load.row):
                self.assertTrue(native_guest_weapon.valid_mounted_ammo(kind, load.item_path))
        self.assertFalse(native_guest_weapon.valid_mounted_ammo("rifle-50-semi", guest_weapon_profiles.FIVE_SIX_AP.item_path))

    def test_shotgun_magazines_keep_each_stock_shell_and_its_projectile_count(self):
        shells = guest_weapon_profiles.SHOTGUN_AMMO
        self.assertEqual({ammo.row: ammo.projectile_count for ammo in shells}, {
            "12Gauge_Buck": 9, "12Gauge_Slugs": 1, "12Gauge_DSAPI": 1,
            "12Gauge_Frag12": 1, "12Gauge_DragonBreath": 12, "12Gauge_HyperBall": 3,
        })
        for ammo in shells:
            with self.subTest(ammo=ammo.row):
                self.assertTrue(native_guest_weapon.valid_mounted_ammo("sawn-off-shotgun", ammo.item_path))
                self.assertFalse(native_guest_weapon.valid_mounted_ammo("rusty-pistol", ammo.item_path))

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
                          ("sawn-off-shotgun", "Wep_SawnOff_ShotGun", 2, 1, 0.1, False),
                          ("bolt-action-shotgun-loki", "Wep_BoltAction_Shotgun_Loki", 4, 1, 1.3, True),
                          *((kind, bp, capacity, 1, rate, True)
                            for kind, bp, capacity, rate, base in guest_weapon_profiles.PUMP_SHOTGUNS if base == ammo)])
        self.assertTrue(all(p.magazine.require_default_payload
                            and p.magazine.reload_first_stack != p.magazine.single_reload for p in profiles))
        self.assertEqual(set(guest_weapon_profiles.SINGLE_RELOAD_MAGAZINES),
                         {kind for kind, *_ in guest_weapon_profiles.PUMP_SHOTGUNS})
        self.assertEqual(native_guest_weapon.LOKI_KINDS, {"bolt-action-shotgun-loki", "hcr-loki", "ar-08"})
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
            "rusty-pistol", "full-auto-shotgun", "full-auto-shotgun-02",
            "sg-71-a", "sg-71-b", "sg-71-a-suppressed", "model-1931", "fennec-57", "black-fox", "break-action-50",
            "bolt-action-shotgun", "bolt-action-shotgun-suppressed", "sawn-off-shotgun", "grenade-launcher",
            "revolver-9mm", "revolver-01", "revolver-02", "revolver-03",
            *(kind for kind, *_ in (*guest_weapon_profiles.ORDINARY_WEAPONS, *guest_weapon_profiles.RELEASE_RIFLES)),
            *native_guest_weapon.LOKI_KINDS,
        })
        self.assertEqual(guest_weapon_profiles.MAX_TAKE_AMMO_ROUNDS, 100)
        self.assertEqual(len(native_guest_weapon.FIRE_CLASSES), len(guest_weapon_profiles.FIREARMS))

    def test_loki_primary_intent_requires_the_mounted_muzzle_and_receiver(self):
        view = "1.000000,0.000000,0.000000;1.000,0.000,0.000;200.000,0.000,0.000;2.000,0.000,0.000"
        self.assertTrue(native_guest_weapon.valid_primary_shot("hcr-loki", "loki:" + view))
        self.assertFalse(native_guest_weapon.valid_primary_shot("light-pistol", "loki:" + view))
        self.assertFalse(native_guest_weapon.valid_primary_shot("hcr-loki", "loki:" + view.rsplit(";", 1)[0]))
        self.assertTrue(native_guest_weapon.valid_primary_shot("light-pistol", view))

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

    def test_stock_338_rifles_keep_their_action_edges_and_ammo(self):
        gp = guest_weapon_profiles
        profiles = {weapon.kind: weapon.magazine for weapon in gp.FIREARMS}
        ammo = frozenset(round_type.item_path for round_type in (gp.MAGNUM_338_FMJ, gp.MAGNUM_338_AP, gp.MAGNUM_338_FRAG, gp.MAGNUM_338_OP))
        for kind, capacity, action_on_release in (("csr-02", 12, False), ("sniper-04", 10, True)):
            self.assertEqual(gp.AMMO_BY_KIND[kind], ammo)
            self.assertEqual(profiles[kind].capacity, capacity)
            self.assertEqual(profiles[kind].action_on_release, action_on_release)
            self.assertIn(kind, native_guest_weapon.PRIMARY_KINDS)
            self.assertNotIn(kind, gp.STOCK_HELD_FIRE_MAGAZINES)

    def test_held_fire_registry_includes_large_automatic_and_burst_magazines(self):
        magazines = guest_weapon_profiles.STOCK_HELD_FIRE_MAGAZINES
        self.assertEqual(magazines["bizon-01"].capacity, 64)
        self.assertEqual(magazines["light-ar-01"].capacity, 24)
        self.assertEqual(magazines["smg-standard"].capacity, 30)
        self.assertEqual(magazines["smg-03"].burst_length, 3)
        self.assertNotIn("rusty-pistol", magazines)
        self.assertNotIn("sg-71-a", magazines)
        self.assertEqual(magazines["famas"].burst_length, 3)
        self.assertEqual(magazines["lmg-01"].capacity, 100)
        for kind, capacity in (("full-auto-shotgun", 8), ("full-auto-shotgun-02", 24)):
            self.assertEqual(magazines[kind], guest_weapon_profiles.Magazine(
                capacity, 0, 0.16, True, guest_weapon_profiles.TWELVE_GAUGE_DSAPI, reload_first_stack=True))
            self.assertEqual(guest_weapon_profiles.AMMO_BY_KIND[kind],
                             frozenset(ammo.item_path for ammo in guest_weapon_profiles.SHOTGUN_AMMO))
        self.assertIn(guest_weapon_profiles.THIRTY_06_TANDEM.item_path, guest_weapon_profiles.AMMO_BY_KIND["lmg-01"])
        self.assertEqual(guest_weapon_profiles.MAX_HELD_FIRE_ROUNDS, 100)


class NativeGuestInventoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_pump_reload_continues_on_the_same_scoped_item(self):
        guid = "A" * 32
        area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        kind = "pump-shotgun"
        client = SimpleNamespace(
            last_reload_intent=0, tube_reload=None, stopping=False, host_session="host", zone_epoch="epoch",
            primary_scope=(area, "guest", guid, kind), viewer_role="guest", gameplay_seen=time.monotonic(),
            bridge=SimpleNamespace(input_namespace=uuid.uuid4()), control=Mock(),
            last_game={"map": area, "pawns": {"local": {"name": "guest"}},
                       "native_guest_reload_intent": {
                           "map": area, "pawn": "guest", "sequence": 1, "guid": guid, "barrel": "primary",
                       }},
            gameplay={"native_equipped_weapon": {"kind": kind, "guid": guid}},
            gameplay_action=AsyncMock(return_value={"native_guest_weapon": {"guid": guid, "after": 1, "reserve_after": 2}}),
        )
        intent = client.last_game["native_guest_reload_intent"]
        intent["guid"] = "B" * 32
        await service.Client.service_reload_input(client, True)
        client.gameplay_action.assert_not_awaited()
        self.assertEqual(client.last_reload_intent, 0)

        intent.update(guid=guid)
        client.primary_scope = None
        await service.Client.service_reload_input(client, True)
        client.gameplay_action.assert_not_awaited()
        self.assertEqual(client.last_reload_intent, 0)

        client.primary_scope = (area, "guest", guid, kind)
        await service.Client.service_reload_input(client, True)
        scope, sequence, step, due = client.tube_reload
        self.assertEqual((sequence, step), (1, 1))
        self.assertGreater(due, time.monotonic())
        self.assertEqual(client.gameplay_action.await_args.args[2], f"{guid}@epoch")
        await service.Client.service_reload_input(client, True)
        self.assertEqual(client.gameplay_action.await_count, 1)

        client.tube_reload = scope, sequence, step, time.monotonic() - 1
        await service.Client.service_reload_input(client, True)
        calls = client.gameplay_action.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0].args[1], calls[1].args[1])
        client.zone_epoch = "next"
        await service.Client.service_reload_input(client, True)
        self.assertIsNone(client.tube_reload)
        self.assertEqual(client.gameplay_action.await_count, 2)

        client.zone_epoch = "epoch"
        client.tube_reload = scope, sequence, step, time.monotonic() - 1
        client.last_game["native_guest_primary_scope"] = {
            "map": area, "pawn": "guest", "guid": guid, "kind": kind, "pump_trigger_held": True,
        }
        await service.Client.service_reload_input(client, True)
        self.assertIsNone(client.tube_reload)
        self.assertEqual(client.gameplay_action.await_count, 2)

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

    async def test_compact_fire_receipt_survives_older_world_equipment_until_the_next_frame(self):
        host = self.host()
        pawn, guid = "exact pawn", "1" * 32
        action = "native-guest-weapon-fire"
        scope = "session\thost\thosting\tzone\t1\t0\n"
        def equipped(rounds):
            return f"native-guest-equipped-weapon\t{pawn}\t{guid}\trusty-pistol\t{rounds}\t-\t-\n"
        def frame(sequence, rounds):
            return (f"schema\t1\nseq\t{sequence}\n" + scope + f"native-guest-save\tbound\t{pawn}\n"
                    + "wallet\t50\n" + equipped(rounds))
        async def reply(payload, timeout):
            nonce, sent, owner, weapon = payload.decode().strip().split("\t")
            self.assertEqual((sent, owner, weapon, timeout), (action, pawn, guid, 5))
            return (native_guest_weapon.REPLY_HEADER + "schema\t1\nseq\t40\n" + scope
                    + f"ack\t{nonce}\taccepted\tguest shot\n" + equipped(7)
                    + f"native-guest-weapon\tzone\tfire\t{pawn}\t{guid}\t1\tequipped\t8\t7\t7\t7\t1\t-\t-\t-\n")
        bridge = runtime.Bridge(Path("."), None)
        command = AsyncMock(side_effect=reply)
        bridge.local_bridge = SimpleNamespace(command=command, read_status=Mock(return_value=frame(40, 8)))
        host.last_game = bridge.snapshot()
        host.last_game["native_guest_save_retention"] = True
        host.pending_guest_saves = {"character": (pawn, {})}

        result = await native_guest_weapon.perform(host, bridge, "character", "a" * 32, action, guid)
        self.assertEqual(result["native_guest_weapon"]["after"], 7)
        self.assertLess(result[native_guest_weapon.REPLY_BYTES], 600)
        self.assertEqual(host.last_game["native_wallet"], 50)
        self.assertEqual(host.last_game[native_guest_weapon.EQUIPPED][pawn]["rounds"], 7)
        self.assertEqual(bridge.snapshot()[native_guest_weapon.EQUIPPED][pawn]["rounds"], 7)
        self.assertIsNone(bridge._local_status)
        bridge.local_bridge.read_status.return_value = frame(41, 6)
        self.assertEqual(bridge.snapshot()[native_guest_weapon.EQUIPPED][pawn]["rounds"], 6)
        self.assertEqual(await native_guest_weapon.perform(host, bridge, "character", "a" * 32, action, guid), result)
        command.assert_awaited_once()
        host.checkpoint_native.assert_not_awaited()

    async def test_preflight_denies_weapon_request_without_stopping_host(self):
        for action, target, refusal in (
            ("fire", "1" * 32, "guest_weapon_refused_detached_save"),
            ("equip", "1" * 32, "guest_weapon_refused_detached_save"),
            ("reload", "equipped", "guest_weapon_refused_detached_save"),
            ("fire", "1" * 32, "A native weapon operation needs an exact guest and item GUID"),
            ("reload", "equipped", "The matched native weapon add-on is unavailable"),
        ):
            with self.subTest(action=action, refusal=refusal):
                host = self.host()
                bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError(refusal)))
                with self.assertRaisesRegex(party.RuleError, f"^{refusal}$"):
                    await native_guest_weapon.perform(host, bridge, "character", "a" * 32,
                                                      f"native-guest-weapon-{action}", target)
                bridge.command.assert_awaited_once_with(f"native-guest-weapon-{action}", "exact pawn", target, timeout=5)
                self.assertFalse(host.stopping)
                self.assertFalse(host.native_checkpoint_unsafe)
                self.assertEqual(host.party["receipts"], {})
                host.checkpoint_native.assert_not_awaited()

    async def test_quick_slot_selection_denies_detached_save_loss_without_stopping_host(self):
        host = self.host()
        host.native_zones, host.zone_epoch = True, "current"
        guid = "1" * 32
        bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("guest_weapon_refused_detached_save")))

        with self.assertRaisesRegex(party.RuleError, "^guest_weapon_refused_detached_save$"):
            await native_guest_weapon.select_equipment(host, bridge, "character", f"current@{guid}")

        bridge.command.assert_awaited_once_with(native_guest_weapon.SELECT_EQUIPMENT, "exact pawn", guid, timeout=3)
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
        slugs = guest_weapon_profiles.TWELVE_GAUGE_SLUGS.item_path
        dragon = guest_weapon_profiles.TWELVE_GAUGE_DRAGON_BREATH.item_path
        for pawn, kind, equipped_guid, ammo, count, accepted in (
            ("exact pawn", "sg-71-a", guid, None, 9, True),
            ("exact pawn", "sg-71-a", "2" * 32, None, 9, False),
            ("other pawn", "sg-71-a", guid, None, 9, False),
            ("exact pawn", "pistol", guid, dragon, 12, False),
            ("exact pawn", "sg-71-a", guid, None, 1, False),
            ("exact pawn", "sg-71-a", guid, None, 8, False),
            ("exact pawn", "sg-71-a", guid, slugs, 1, True),
            ("exact pawn", "sg-71-a", guid, slugs, 9, False),
            ("exact pawn", "sg-71-a", guid, dragon, 12, True),
        ):
            with self.subTest(pawn=pawn, kind=kind, equipped_guid=equipped_guid, ammo=ammo, count=count):
                host = self.host()
                observed = dict(proof, projectiles=count)
                bridge = SimpleNamespace(command=AsyncMock(return_value={
                    "state": "hosting", "map": "zone", "native_guest_weapon": observed,
                    "native_guest_equipped_weapons": {pawn: {"kind": kind, "guid": equipped_guid, "ammo_class": ammo}},
                }))
                request = native_guest_weapon.perform(
                    host, bridge, "character", operation, "native-guest-weapon-fire", guid
                )
                if accepted:
                    self.assertEqual((await request)["native_guest_weapon"], observed)
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
