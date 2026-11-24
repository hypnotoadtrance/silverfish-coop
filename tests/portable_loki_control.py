"""Verify the mounted Loki's original mode, reload, and primary dispatch in one private campaign."""

import asyncio
import re

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_inventory
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import LOKI_RECEIVERS

RECEIVERS = {
    "Wep_BoltAction_Shotgun_Loki": ("4", "1", "1.3", "true", "Ammo_ShotgunShells_BP_C"),
    "Wep_HCR_Loki": ("20", "0", "0.1", "false", "Ammo_FullRifle_BP_C"),
    "Wep_AR_08": ("30", "4", "0.08", "false", "Ammo_IntrRifle_BP_C"),
}
FIELDS = (
    "capacity", "burst", "rate", "release_fire", "rounds", "stored_rounds", "ammo",
    "alt_capacity", "alt_burst", "alt_rate", "alt_release_fire", "alt_single_reload",
    "alt_rounds", "alt_ammo", "alt_selected", "alt_owns_input", "mode_tag", "alt_reloading", "alt_class",
)


async def verify_stock_loki_control(host, _guest, _pawn):
    checkpoint = campaign_save_hashes(host.campaign)
    proof = {}
    for receiver, primary in RECEIVERS.items():
        async def command(verb):
            reply = await host.bridge.command("fixture-loki-stock", receiver, verb)
            result = reply["ack"]["message"]
            if verb == "equip" or result == "waiting":
                return result
            return dict(zip(FIELDS, result.split("|"), strict=True))

        assert await command("equip") == "equipping"
        async with asyncio.timeout(12):
            while (before := await command("read")) == "waiting":
                await asyncio.sleep(.15)
        assert tuple(before[field] for field in ("capacity", "burst", "rate", "release_fire", "ammo")) == primary, before
        assert (before["alt_class"], before["alt_capacity"], before["alt_burst"], before["alt_rate"],
                before["alt_release_fire"], before["alt_single_reload"], before["alt_ammo"]) == (
            "Weapon_UnderBarrel_Loki_C", "4", "1", "1.0", "false", "true", "Ammo_ShotgunShells_BP_C",
        ), before
        toggled = await command("toggle")
        assert (toggled["alt_selected"], toggled["alt_owns_input"], toggled["mode_tag"], toggled["alt_rounds"]) == (
            "true", "true", "On", "0",
        ), toggled
        loaded = await command("reload")
        async with asyncio.timeout(14):
            while loaded["alt_rounds"] == "0" or loaded["alt_reloading"] == "true":
                await asyncio.sleep(.25)
                loaded = await command("read")
        assert loaded["alt_rounds"] == "1", loaded

        await command("press")
        fired = await command("release")
        async with asyncio.timeout(6):
            while fired["alt_rounds"] == "1":
                await asyncio.sleep(.15)
                fired = await command("read")
        assert fired["alt_rounds"] == "0", fired

        restored = await command("toggle")
        assert (restored["alt_selected"], restored["alt_owns_input"], restored["mode_tag"]) == (
            "false", "false", "Off",
        ), restored
        for state in (toggled, loaded, fired, restored):
            assert (state["rounds"], state["stored_rounds"]) == (before["rounds"], before["stored_rounds"]), state
        proof[receiver] = {"primary": before, "alternate": toggled, "reloaded": loaded, "fired": fired,
                           "returned": restored}

    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_loki_control": proof}


async def verify_guest_loki(host, guest, pawn):
    checkpoint = campaign_save_hashes(host.campaign)
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_inventory = await submit(host_run, "native-inventory-list")
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected")
    proof = {}

    async def stock_input(kind):
        name = f"native-guest-{kind}-input-fixture"
        event = f"native_guest_{kind}_input"
        requested = await submit(guest_run, name)
        assert requested["ok"], requested
        sequence = requested[f"native_guest_{kind}_fixture"]["sequence"]
        async with asyncio.timeout(12):
            while True:
                for entry in await resident_events(guest_run, guest_frame):
                    if entry.get("sequence") == sequence and entry["event"] in (event, event + "_rejected"):
                        assert entry["event"] == event, entry
                        return entry["native_guest_weapon"]
                await asyncio.sleep(.1)

    for profile in LOKI_RECEIVERS:
        seeded = await host.bridge.command("fixture-stock-take-ammo-seed", pawn, profile.kind)
        guid = seeded["ack"]["message"]
        assert re.fullmatch(r"[0-9A-F]{32}", guid), seeded["ack"]
        inspected = await submit(guest_run, "native-guest-weapon-inspect", target=guid)
        assert inspected.get("native_guest_weapon", {}).get("stored_ammo") == 1, inspected
        moved = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=guid)
        assert moved["ok"], moved
        slot = moved[native_inventory.STORE_KEY]["slot"]

        async def selected(verb):
            result = await guest.bridge.command("fixture-loki-guest", guid, verb)
            return result["ack"]["message"]

        async def await_magazine(expected):
            async with asyncio.timeout(12):
                while await selected("read") != expected:
                    await asyncio.sleep(.15)

        async with asyncio.timeout(12):
            while True:
                result = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, "read")
                if result["ack"]["message"] != "waiting":
                    break
                await asyncio.sleep(.15)
        await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, "equip")
        await await_magazine("1,0,primary")
        await wait_resident_status(host_run, lambda frame:
            frame.get("game", {}).get("native_guest_equipped_weapons", {}).get(pawn, {}).get("guid") == guid)
        assert await selected("toggle") == "1,0,loki"
        seeded = await host.bridge.command("fixture-loki-reserve", pawn)
        assert seeded["ack"]["message"] == "seeded"
        reload = await stock_input("reload")
        assert (reload["guid"], reload["status"], reload["before"], reload["after"]) == (guid, "underbarrel_equipped", 0, 1), reload
        await await_magazine("1,1,loki")
        shot = await stock_input("primary")
        assert (shot["guid"], shot["status"], shot["before"], shot["after"], shot["projectiles"]) == (
            guid, "underbarrel_equipped", 1, 0, 9,
        ), shot
        await await_magazine("1,0,loki")
        assert await selected("toggle") == "1,0,primary"
        primary = await stock_input("primary")
        assert (primary["guid"], primary["status"], primary["before"], primary["after"], primary["projectiles"]) == (
            guid, "equipped", 1, 0, profile.magazine.ammo.projectile_count,
        ), primary
        await await_magazine("0,0,primary")
        proof[profile.kind] = {"reload": reload, "underbarrel": shot, "primary": primary}

    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_inventory["native_inventory"]
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"guest_loki": proof}
