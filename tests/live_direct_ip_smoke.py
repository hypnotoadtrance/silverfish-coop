"""Verify invitation-free Direct IP enrollment, ownership, and real Unreal reconnect."""

import argparse
import asyncio
import secrets
import socket
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import direct_ip, link, native_host_save, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import select_run, submit
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes


async def verify(work: Path, original: Path) -> None:
    originals = saved_hashes(original)
    if ("steamapps" in {part.casefold() for part in work.parts} or work.is_relative_to(original)
            or original.is_relative_to(work) or not originals or not runtime.qualified_authored_routes(work)):
        raise ValueError("Use a separate qualified replica and the original saves for comparison")
    name = "direct-ip-" + uuid.uuid4().hex[:8]
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    campaign = select_run(work, "campaigns", name)
    guest = select_run(work, "clients", name)
    other = select_run(work, "clients", name + "-other")
    host_run, guest_run = campaign / "run", guest / "run"
    build = runtime.staged_build(work)
    port = runtime.spare_udp_port()
    password, new_password = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    evidence = {"verified": False, "port": port}
    tasks = []

    async def refuses(operation, message):
        try:
            await operation
        except link.LinkError as error:
            assert str(error) == message, str(error)
        else:
            raise AssertionError(f"Expected refusal: {message}")

    async def connect(profile=guest, supplied=password):
        return await direct_ip.connect(profile, "127.0.0.1", port, supplied, build, service.PROTOCOL)

    async def catalog(profile=guest, supplied=password, name=None, operation=None):
        return await service.direct_catalog(work, profile, "127.0.0.1", port, supplied, name, operation)

    async def start_host(supplied, *, fresh):
        task = asyncio.create_task(service.host_game(
            work, campaign, "127.0.0.1", port, True, start_preset="wanderer" if fresh else None,
            max_guests=2, runtime_listener=True, direct_password=supplied, **service.MENU_HOST_OPTIONS,
        ))
        tasks.append(task)
        await wait(host_run, lambda value: value.get("state") == "hosting" and value.get("active") is True)
        return task

    async def stop_host(task):
        assert (await submit(host_run, "stop"))["ok"]
        await asyncio.wait_for(task, 20)

    def read_checkpoint():
        return CampaignStore.read_existing(
            campaign / "save", verify_world=service.verify_travel_checkpoint,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(campaign / "game-user", data),
        )

    try:
        first = await start_host(password, fresh=True)
        await refuses(connect(supplied=""), "The host requires a Direct IP password")
        await refuses(connect(supplied="incorrect"), "The Direct IP password is incorrect")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unreachable:
            unreachable.bind(("127.0.0.1", 0))
            await refuses(direct_ip.connect(guest, "127.0.0.1", unreachable.getsockname()[1], "", build, service.PROTOCOL),
                          "The Direct IP address or port could not be reached")
        assert read_checkpoint()["guests"] == {}
        assert (await catalog()) == {"ok": True, "characters": []}
        creation = uuid.uuid4().hex
        created = await catalog(name="Direct Scout", operation=creation)
        assert created["ok"] and created == await catalog(name="Direct Scout", operation=creation)
        character = created["character"]["character_id"]
        assert (await catalog(other)) == {"ok": True, "characters": []}
        other_connection = (await connect(other)).session(character)
        probe = await link.start_client()
        try:
            denied = await probe.connect(other_connection["host"], other_connection["port"], other_connection["certificate"], {
                key: other_connection[key]
                for key in ("guest_id", "credential", "character_id", "game_build", "protocol_version")
            })
            assert denied["accepted"] is False
        finally:
            await probe.close()
        connection = await connect()
        game = asyncio.create_task(service.join_game(work, guest, connection, True, character))
        tasks.append(game)
        joined = await wait(guest_run, lambda value: value.get("state") == "connected")
        assert joined["character_id"] == character
        paired = await wait(host_run, lambda value: value.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        pawn = paired["game"]["native_guest_save"]["pawn"]
        assert (await submit(guest_run, "drop-link"))["ok"]
        await wait(host_run, lambda value: any(
            lease["character_id"] == character and lease["state"] == "reserved" for lease in value["campaign"]["leases"]))
        assert (await submit(guest_run, "reconnect"))["resumed"]
        resumed = await wait(host_run, lambda value: any(
            lease["character_id"] == character and lease["state"] == "active" for lease in value["campaign"]["leases"]))
        assert resumed["game"]["native_guest_save"]["pawn"] == pawn
        assert (await submit(guest_run, "leave-session"))["ok"]
        await asyncio.wait_for(game, 20)
        await stop_host(first)
        checkpoint = read_checkpoint()
        assert checkpoint["characters"][character]["state"]["native_guest_save"]
        assert checkpoint["guests"][connection.details["guest_id"]]["direct"] is True

        second = await start_host(new_password, fresh=False)
        old = connection.session()
        probe = await link.start_client(catalog_only=True)
        try:
            denial = await probe.connect(old["host"], old["port"], old["certificate"], {
                **{key: old[key] for key in ("guest_id", "credential", "game_build", "protocol_version")},
                "catalog": True,
            })
        finally:
            await probe.close()
        assert denial == {"accepted": False, "reason": "direct_enrollment_required"}
        await refuses(connect(supplied=password), "The Direct IP password is incorrect")
        roster = await catalog(supplied=new_password)
        assert roster == {"ok": True, "characters": [{"character_id": character, "name": "Direct Scout"}]}
        reopened = await connect(supplied=new_password)
        assert reopened.details["guest_id"] == connection.details["guest_id"]
        await stop_host(second)

        with CampaignStore(case / "alternate-host") as alternate:
            identity = link.make_server_identity()
            enrollment = direct_ip.Enrollment(alternate, identity, build, service.PROTOCOL, {}, "")
            relay = await link.start_host("127.0.0.1", port, "127.0.0.1", runtime.spare_udp_port(), identity,
                                         lambda _: {"accepted": False}, enroll=enrollment)
            try:
                await refuses(connect(supplied=""), "This address now presents a different host identity")
                unpinned = await connect(other / "fresh-profile", "")
                assert alternate.is_direct_guest(unpinned.details["guest_id"])
            finally:
                await relay.close()
        evidence.update(verified=True, scoped_character=character, same_pawn_reconnected=True,
                        character_retained_after_password_change=True, checkpoint=checkpoint["revision"],
                        open_host_verified=True, different_identity_rejected=True)
    finally:
        for task in reversed(tasks):
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] &= evidence["original_saves_verified"]
        runtime.write_json(case / "direct-ip-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
