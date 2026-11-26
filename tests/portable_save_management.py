"""Exercise save management with the installed stock title buttons."""

import asyncio
import uuid

from live_unified_title_smoke import start_pair
from tools.silverfish import native_host_save, title_host_campaigns
from tools.silverfish.campaign import CampaignStore, DELETED


async def verify_save_management(host, guest):
    created = await start_pair(host, guest, "Management scout")
    campaign_id = uuid.UUID(created["campaign_id"]).hex
    character = created["character_id"]
    checkpoint = host.campaign / "save"
    profile = host.profile

    def read():
        return CampaignStore.read_existing(checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data))

    original = read()
    storage = native_host_save.checkpoint_profile(profile, original)
    slots = {entry["slot"]: native_host_save.slot_path(storage, entry["slot"]).read_bytes()
             for entry in native_host_save.references(original)}
    fixture = "native-title-button-fixture"

    async def click(name):
        async with asyncio.timeout(8):
            while True:
                try:
                    reply = await host.bridge.command(fixture, name, timeout=3)
                except RuntimeError as error:
                    if str(error) != "title_panel_fixture_button_unavailable":
                        raise
                    await asyncio.sleep(.1)
                else:
                    assert reply["ack"]["result"] == "accepted", reply
                    return

    async def saved_after(revision):
        async with asyncio.timeout(12):
            while True:
                data = read()
                if data["revision"] == revision + 1:
                    return data
                await asyncio.sleep(.1)

    await click("multiplayer")
    await click("host")
    await click("manage")
    await click("save:" + campaign_id)
    await click("characters")
    await click("character:" + character)
    await click("delete-character")
    assert not (host.bridge.directory / title_host_campaigns.REQUEST).exists()
    assert read()["revision"] == original["revision"]
    await click("back")
    await click("delete-character")
    await click("confirm")
    deleted_character = await saved_after(original["revision"])
    assert deleted_character["characters"][character]["state"][DELETED] is True

    await click("save:" + campaign_id)
    await click("characters")
    await click("character:" + character)
    await click("restore-character")
    restored_character = await saved_after(deleted_character["revision"])
    assert DELETED not in restored_character["characters"][character]["state"]

    await click("save:" + campaign_id)
    await click("delete-save")
    assert read()["revision"] == restored_character["revision"]
    await click("confirm")
    deleted_save = await saved_after(restored_character["revision"])
    assert deleted_save["world"][DELETED] is True
    async with asyncio.timeout(8):
        while True:
            try:
                catalog = (host.bridge.directory / title_host_campaigns.FILE).read_text(encoding="utf-8")
            except FileNotFoundError:
                catalog = ""
            if f"\t{campaign_id}\t{deleted_save['revision']}\tdeleted\n" in catalog:
                break
            await asyncio.sleep(.1)
    await click("back")
    await click("browse")
    try:
        await host.bridge.command(fixture, "load:" + host.campaign.name)
    except RuntimeError as error:
        assert str(error) == "title_panel_fixture_button_unavailable", error
    else:
        raise AssertionError("A deleted co-op save was still offered for loading")

    await click("back")
    await click("manage")
    await click("save:" + campaign_id)
    await click("restore-save")
    restored_save = await saved_after(deleted_save["revision"])
    assert restored_save["world"] == original["world"]
    assert restored_save["characters"] == original["characters"]
    assert all(native_host_save.slot_path(storage, slot).read_bytes() == data for slot, data in slots.items())
    await click("back")
    await click("back")

    reopened = await start_pair(host, guest, "Management scout", load_existing=True, resume_character=character)
    assert reopened["campaign_id"] == created["campaign_id"] and reopened["character_id"] == character
    assert reopened["host_pid"] == created["host_pid"] and reopened["guest_pid"] == created["guest_pid"]
    return {"created": created, "reopened": reopened, "deleted_saves_hidden": True,
            "confirmation_cancelled": True, "native_slots_preserved": len(slots),
            "revisions": [original["revision"], deleted_character["revision"], restored_character["revision"],
                          deleted_save["revision"], restored_save["revision"]]}
