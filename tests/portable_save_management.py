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
    host_slot = native_host_save.slot_path(storage, original["world"]["native_host_save"]["slot"])
    guest_slot = native_host_save.slot_path(storage, original["characters"][character]["state"]["native_guest_save"]["slot"])
    host_bytes = host_slot.read_bytes()
    fixture = "native-title-button-fixture"

    async def title_control(action, name):
        unavailable = "title_panel_fixture_focus_unavailable" if action == "focus" else "title_panel_fixture_button_unavailable"
        command = "native-title-focus-fixture" if action == "focus" else fixture
        async with asyncio.timeout(8):
            while True:
                try:
                    reply = await host.bridge.command(command, name, timeout=3)
                except RuntimeError as error:
                    if str(error) != unavailable:
                        raise
                    await asyncio.sleep(.1)
                else:
                    assert reply["ack"]["result"] == "accepted", reply
                    return

    async def click(name):
        await title_control("click", name)

    async def focused(name):
        await title_control("focus", name)

    async def saved_after(revision):
        async with asyncio.timeout(12):
            while True:
                data = read()
                if data["revision"] == revision + 1:
                    return data
                await asyncio.sleep(.1)

    await click("multiplayer")
    await focused("host")
    await click("host")
    await click("new")
    await focused("campaign")
    await click("network")
    await focused("direct")
    await click("back")
    await focused("network")
    await click("back")
    await focused("new")
    await click("manage")
    await click("save:" + campaign_id)
    await click("characters")
    await click("character:" + character)
    await click("back")
    await focused("character:" + character)
    await click("character:" + character)
    await click("delete-character")
    await focused("back")
    assert not (host.bridge.directory / title_host_campaigns.REQUEST).exists()
    assert read()["revision"] == original["revision"]
    await click("back")
    await focused("back")
    await click("delete-character")
    await click("confirm")
    deleted_character = await saved_after(original["revision"])
    assert deleted_character["characters"][character]["state"] == {DELETED: True}
    assert host_slot.read_bytes() == host_bytes and not guest_slot.exists()
    assert b"Management scout" not in (checkpoint / "campaign.previous.json").read_bytes()

    await focused("save:" + campaign_id)
    await click("back")
    await focused("manage")
    await click("back")
    reopened = await start_pair(host, guest, "Replacement scout", load_existing=True)
    assert reopened["campaign_id"] == created["campaign_id"] and reopened["character_id"] != character
    assert reopened["host_pid"] == created["host_pid"] and reopened["guest_pid"] == created["guest_pid"]

    await click("multiplayer")
    await click("host")
    await click("manage")
    await click("save:" + campaign_id)
    await click("delete-save")
    await focused("back")
    assert read()["revision"] == reopened["revision"]
    await click("confirm")
    async with asyncio.timeout(8):
        while True:
            try:
                catalog = (host.bridge.directory / title_host_campaigns.FILE).read_text(encoding="utf-8")
            except FileNotFoundError:
                catalog = ""
            if catalog and campaign_id not in catalog:
                break
            await asyncio.sleep(.1)
    assert not (checkpoint / "campaign.json").exists()
    assert not (checkpoint / "campaign.previous.json").exists()
    assert not list(native_host_save.directory(storage).glob("*.sav"))
    await click("back")
    await focused("manage")
    await click("browse")
    try:
        await host.bridge.command(fixture, "load:" + host.campaign.name)
    except RuntimeError as error:
        assert str(error) == "title_panel_fixture_button_unavailable", error
    else:
        raise AssertionError("A deleted co-op save was still offered for loading")

    await click("back")
    await click("manage")
    await focused("back")
    await click("back")
    await click("back")
    await click("modes")
    return {"created": created, "reopened": reopened, "campaign_files_erased": True,
            "guest_slot_erased": True, "confirmation_cancelled": True, "title_focus_verified": True,
            "revisions": [original["revision"], deleted_character["revision"], reopened["revision"]]}
