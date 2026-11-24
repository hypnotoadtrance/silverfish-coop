"""Verify the mounted Loki's original mode, reload, and primary dispatch in one private campaign."""

import asyncio

from live_multi_smoke import campaign_save_hashes

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
