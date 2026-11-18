"""Command-line interface for the two-player Project Silverfish prototype."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
from pathlib import Path

from .control import select_run, status, submit
from .runtime import REPO, prepare_game

def _workspace(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("expected a UDP port from 0 to 65535")
    return port


def _bounded_count(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 1000:
        raise argparse.ArgumentTypeError("expected a count from 1 to 1000")
    return count


def _target(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--campaign")
    target.add_argument("--profile")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--work", type=_workspace, default=REPO / ".work",
                     help="isolated local staging and save root")
    commands = cli.add_subparsers(dest="operation", required=True)

    stage = commands.add_parser("stage", help="copy stock game files and stage UE4SS")
    stage.add_argument("--source", type=Path, required=True, help="original Steam installation")
    stage.add_argument("--ue4ss", type=Path, required=True, help="extracted UE4SS v4.0.0 release root")
    stage.add_argument("--signatures", type=Path, help="extracted Silverfish UE4SS_Signatures folder")
    stage.add_argument("--cpp-runtime", type=Path, help="locally built UE4SS v4.0.0 runtime DLL")
    stage.add_argument("--cpp-addon", type=Path, help="Silverfish native add-on from the same build")

    host = commands.add_parser("host", help="stage a listen session and wait for the guest")
    host.add_argument("--campaign", required=True)
    host.add_argument("--bind", default="127.0.0.1", help="local IP to bind for authenticated Direct IP")
    host.add_argument("--port", type=_port, default=7777)
    host.add_argument("--headless", action="store_true")
    host.add_argument("--native-events", action="store_true",
                      help="enable supervised host-side native event scope in the C++ lab")
    host.add_argument("--native-guest", action="store_true",
                      help="detach the authenticated guest's native save for the C++ playtest")
    host.add_argument("--fixtures", action="store_true", help="enable the playtest loot marker and controlled damage")
    host.add_argument("--fixture-keep-slots", type=int, help="test how many inventory stacks survive a wipe")
    host.add_argument("--fixture-backpacks", action="store_true", help="drop unretained test inventory in owner-only bags")

    invite = commands.add_parser("invite", help="create a private guest invitation file")
    invite.add_argument("--campaign", required=True)
    invite.add_argument("--name", required=True)
    invite.add_argument("--character", required=True)
    invite.add_argument("--address", help="address to put in the invite when different from the bind address")

    join = commands.add_parser("join", help="use a private invitation file")
    join.add_argument("--profile", required=True)
    join.add_argument("--invite", type=Path, required=True)
    join.add_argument("--headless", action="store_true")
    join.add_argument("--character", help="character ID selected from the guest's catalog")

    catalog = commands.add_parser("characters", help="list your host-owned characters before joining")
    catalog.add_argument("--invite", type=Path, required=True)
    create = commands.add_parser("create-character", help="create a character before joining")
    create.add_argument("--invite", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID on retry")

    inspect = commands.add_parser("status", help="read the latest structured session status")
    _target(inspect)
    events = commands.add_parser("events", help="read recent structured session events")
    _target(events)
    events.add_argument("--tail", type=_bounded_count, default=30)

    action = commands.add_parser("action", help="perform a supported local action")
    _target(action)
    action.add_argument("action", choices=[
        "checkpoint", "drop-link", "reconnect", "stop", "walk", "interact", "test-damage", "travel",
        "buy", "test-quest-reward", "vitals-probe", "vitals-reset", "quest-probe", "rest", "keep",
        "native-death-probe", "native-damage-probe", "native-inventory-probe", "native-guest-damage",
    ])
    action.add_argument("--target", help="character ID, loot ID, or full destination map path")
    action.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID to retry an interaction")
    action.add_argument("--amount", type=int, help="controlled damage amount for fixture tests")
    action.add_argument("--x", type=float, help="normalized world X movement")
    action.add_argument("--y", type=float, help="normalized world Y movement")
    action.add_argument("--milliseconds", type=int, help="movement duration, from 50 to 1000")
    action.add_argument("--keep", action="append", metavar="ITEM=COUNT", help="inventory stack to retain after a wipe")
    return cli


def _repository_run(opts) -> Path:
    if opts.campaign is not None:
        return select_run(opts.work, "campaigns", opts.campaign)
    return select_run(opts.work, "clients", opts.profile)


def _events(directory: Path, limit: int) -> dict:
    with (directory / "events.jsonl").open(encoding="utf-8") as file:
        rows = deque(file, maxlen=limit)
    return {"schema": 1, "events": [json.loads(line) for line in rows if line.endswith("\n")]}


async def execute(opts):
    operation = opts.operation
    if operation == "stage":
        return prepare_game(opts.work, opts.source, opts.ue4ss, opts.signatures,
                            cpp_runtime=opts.cpp_runtime, cpp_addon=opts.cpp_addon)
    if operation == "host":
        from .service import host_game
        rules = None
        if opts.fixture_keep_slots is not None or opts.fixture_backpacks:
            if not opts.fixtures or (opts.fixture_keep_slots is not None and opts.fixture_keep_slots < 0):
                raise ValueError("Wipe fixture settings require --fixtures and a nonnegative slot count")
            rules = {"keep_slots": opts.fixture_keep_slots, "drop_backpacks": opts.fixture_backpacks}
        await host_game(opts.work, select_run(opts.work, "campaigns", opts.campaign),
                        opts.bind, opts.port, opts.headless, opts.fixtures, rules,
                        native_events=opts.native_events, native_guest=opts.native_guest)
        return {"ok": True, "state": "stopped"}
    if operation == "join":
        from .service import join_game
        await join_game(opts.work, select_run(opts.work, "clients", opts.profile),
                        opts.invite, opts.headless, opts.character)
        return {"ok": True, "state": "stopped"}
    if operation in {"characters", "create-character"}:
        from .service import character_catalog
        return await character_catalog(opts.work, opts.invite, getattr(opts, "name", None),
                                       getattr(opts, "operation_id", None))
    if operation == "invite":
        directory = select_run(opts.work, "campaigns", opts.campaign) / "run"
        return await submit(directory, "invite", name=opts.name, character=opts.character,
                            address=opts.address)

    directory = _repository_run(opts) / "run"
    if operation == "status":
        return status(directory)
    if operation == "events":
        return _events(directory, opts.tail)
    retained = {}
    for value in opts.keep or ():
        item, separator, count = value.partition("=")
        if not item or not separator or item in retained or not count.isdecimal() or int(count) < 1:
            raise ValueError("Each retained item needs a distinct ID and positive count")
        retained[item] = int(count)
    return await submit(directory, opts.action, target=opts.target, operation_id=opts.operation_id,
                        amount=opts.amount, x=opts.x, y=opts.y, milliseconds=opts.milliseconds,
                        retained=retained if opts.action == "keep" else None)


def main() -> int:
    options = parser().parse_args()
    try:
        reply = asyncio.run(execute(options))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        reply = {"ok": False, "error": str(error), "error_type": type(error).__name__}
    print(json.dumps(reply, ensure_ascii=True, allow_nan=False))
    return 1 if reply.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
