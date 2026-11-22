"""Drive the stock grip and release against the exact shared loot actor on two muted seats."""

import asyncio
from contextlib import contextmanager
import math
from pathlib import Path
import uuid

from tools.silverfish import native_prop_carry, native_world_loot as loot


@contextmanager
def staged_carry_probe(work):
    scripts = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishCoop/Scripts"
    main = scripts / "main.lua"
    fixture = scripts / "world_loot_carry_probe.lua"
    original = main.read_bytes()
    with fixture.open("xb") as stream:
        stream.write((Path(__file__).with_name("fixtures") / fixture.name).read_bytes())
    try:
        main.write_bytes(original + b'\nrequire("world_loot_carry_probe").listen()\n')
        yield
    finally:
        main.write_bytes(original)
        fixture.unlink()


async def probe(run, action, target=""):
    mailbox, = (run / "game-bridge").iterdir()
    reply = mailbox / "world-loot-carry-reply"
    reply.unlink(missing_ok=True)
    temporary = mailbox / "world-loot-carry-probe.tmp"
    temporary.write_text(f"{action}|{target}|{uuid.uuid4().hex}\n", encoding="ascii", newline="\n")
    temporary.replace(mailbox / "world-loot-carry-probe")
    async with asyncio.timeout(8):
        while True:
            try:
                status, result = reply.read_text().split("|", 1)
            except (OSError, ValueError):
                await asyncio.sleep(.05)
                continue
            if status != "ok":
                raise RuntimeError(f"{action}: {result}")
            return result


async def carry_and_throw(host, guest, identity, original, ledger_transform, wait):
    def visible(state):
        item = state.get("game", {}).get(loot.KEY, {}).get(identity, {})
        return item if item and not item.get("moving") else None

    def edge(state, expected):
        proof = state.get("game", {}).get(native_prop_carry.PROOF, {})
        return (proof.get("loot_id"), proof.get("edge")) == (identity, expected)

    paths = {}
    before = {}
    for seat in (host, guest):
        item = visible(await wait(seat, visible))
        paths[seat] = item["actor"].split(" ", 1)[1]
        before[seat] = (await probe(seat, "pose", paths[seat])).split(",")
    address = await probe(guest, "grip", identity)
    assert address == before[guest][0]
    await wait(host, lambda state: edge(state, "grab"))
    await wait(guest, lambda state: identity in state.get("gameplay", {}).get(loot.MOVING, [])
               and state.get("game", {}).get(loot.KEY, {}).get(identity, {}).get("moving"))
    release = list(map(float, (await probe(guest, "throw")).split(",")))
    await wait(host, lambda state: edge(state, "throw"))

    def settled(state):
        if identity in state.get("gameplay", {}).get(loot.MOVING, []) or not visible(state):
            return False
        transform = ledger_transform(state)
        return bool(transform and transform != original)

    final = ledger_transform(await wait(guest, settled, timeout=12))
    landed = {}
    for role, seat in (("host", host), ("guest", guest)):
        await wait(seat, visible)
        pose = (await probe(seat, "pose", paths[seat])).split(",")
        assert pose[0] == before[seat][0], (role, before[seat], pose)
        landed[role] = list(map(float, pose[1:]))
        assert math.dist(final["position"], landed[role]) <= 2, (role, final, landed)
    assert math.dist(release, final["position"]) > 25, (release, final)
    assert math.dist(original["position"], final["position"]) > 25, (original, final)
    return {"identity": identity, "actors": {role: paths[seat] for role, seat in (("host", host), ("guest", guest))},
            "release": release, "landed": landed, "settled_transform": final}
