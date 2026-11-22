"""Use the placed PlaytestMap sludge to exercise the stock acid overlap on both seats."""

import asyncio
from contextlib import contextmanager
import math
from pathlib import Path
import uuid

MAP = "/Game/Classes/Maps/PlaytestMap_01"
SOURCE = f"ToxicSludge_Plane_BP_C {MAP}.PlaytestMap_01:PersistentLevel.ToxicSludge_Plane_BP_C_1"


@contextmanager
def stage_acid_probe(work):
    mods = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods"
    if mods.resolve(strict=True) != mods or "steamapps" in {part.casefold() for part in mods.parts}:
        raise ValueError("Use a private staged mod directory for the stock acid probe")
    folder = mods / "SilverfishStockAcidProbe"
    folder.mkdir()
    scripts = folder / "Scripts"
    enabled = folder / "enabled.txt"
    script = scripts / "main.lua"
    try:
        scripts.mkdir()
        script.write_bytes((Path(__file__).with_name("fixtures") / "stock_acid_probe.lua").read_bytes())
        enabled.touch()
        yield
    finally:
        if folder.resolve() != folder or scripts.resolve() != scripts:
            raise RuntimeError("The private acid fixture path changed during the test")
        enabled.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        if scripts.is_dir():
            scripts.rmdir()
        folder.rmdir()


async def _probe(mailbox, operation):
    nonce = uuid.uuid4().hex
    request = mailbox / "acid-probe"
    pending = request.with_suffix(".pending")
    pending.write_text(f"{nonce}|{operation}\n", encoding="ascii", newline="")
    pending.replace(request)
    async with asyncio.timeout(8):
        while True:
            try:
                reply_id, result, message = (mailbox / "acid-reply").read_text(encoding="utf-8").rstrip().split("|", 2)
            except (OSError, ValueError):
                pass
            else:
                if reply_id == nonce:
                    if result != "ok":
                        raise RuntimeError(f"Stock acid {operation}: {message}")
                    return message
            await asyncio.sleep(.05)


async def exercise_acid(command, wait, note, run, client_run, campaign, profile, character):
    mailbox, = (run / "game-bridge").iterdir()
    for role, slot, helper, character_id, helper_slot, control in (
        ("guest", "guest", "local", character, "local", ("--campaign", campaign)),
        ("host", "local", "guest", "host", "guest", ("--profile", profile)),
    ):
        def standing_pair(frame):
            game = frame.get("game", {})
            return game.get("map") == MAP and frame.get("link_connected") and all(
                game.get("player_effects", {}).get(key, {}).get("state") == "standing"
                and (game.get("pawns", {}).get(key, {}).get("health") or 0) > 0 for key in ("local", "guest"))

        before = await wait(run, standing_pair)
        pawns = before["game"]["pawns"]
        entered = await _probe(mailbox, role)
        assert entered == f"{SOURCE}|{pawns[slot]['name']}", entered

        def scoped_down(frame):
            game, party = frame.get("game", {}), frame.get("gameplay", {})
            return (party.get("players", {}).get(role, {}).get("downed")
                    and game.get("player_effects", {}).get(slot, {}).get("state") == "downed")

        try:
            down = await wait(run, scoped_down, timeout=18)
        finally:
            terminal = await _probe(mailbox, "return")
        pulses, helper_pulses, acid, effects, victim_before, victim_after, helper_before, helper_after = map(
            float, terminal.split("|"))
        assert pulses > 0 and helper_pulses == 0 and acid > 0 and effects > 0, (role, pulses, helper_pulses, acid, effects)
        assert not down["gameplay"]["wipe"] and not down["game"]["native_dead_flags"][slot]
        assert helper_after >= helper_before and victim_after < victim_before, (role, terminal)

        await wait(client_run, lambda view: view.get("game", {}).get("player_effects", {})
                   .get(helper_slot, {}).get("state") == "downed", timeout=10)

        restored = await command("action", *control, "interact", "--target", character_id)
        assert restored["effect"] == "revive", restored
        expected = max(1, pawns[slot]["max_health"] * .10)

        def standing_after_revive(view):
            game = view.get("game", {})
            pawn = game.get("pawns", {}).get(slot, {})
            if pawn.get("name") != pawns[slot]["name"] or (pawn.get("health") or 0) < expected - .01:
                return False
            if game.get("player_effects", {}).get(slot, {}).get("state") != "standing":
                return False
            receipt = game.get("native_host_revived_health") if role == "host" else pawn["health"]
            return receipt is not None and math.isclose(receipt, expected, abs_tol=.01)

        revived = await wait(run, standing_after_revive, timeout=15)
        assert revived["game"]["pawns"][helper]["health"] >= helper_before, (role, terminal, revived["game"]["pawns"][helper])
        note(f"stock_acid_overlap_downs_and_revives_{role}", {
            "source": SOURCE, "pawn": pawns[slot]["name"], "acid": acid, "stock_pulses": int(pulses),
            "stock_damage_effects": int(effects), "health_before": victim_before, "health_after_acid": victim_after,
            "revive_health": expected,
            "health_after_revive": revived["game"]["pawns"][slot]["health"],
            "helper_health_before": helper_before, "helper_health_after_acid": helper_after,
            "helper_health": revived["game"]["pawns"][helper]["health"], "guest_saw_downing": True,
        })
