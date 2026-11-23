"""Check the resident guest's compact stock world view receipt in a private pair."""

import asyncio
import math
from pathlib import Path

from portable_guest_world_start import wait_resident_status
from tools.silverfish import native_weather_view, runtime
from tools.silverfish.authored_world_view import WORLD_VIEW_ACTORS
from tools.silverfish.control import current_status
from tools.silverfish.travel import OBSERVATORY


TRACE = "portable-world-view-receipts.tsv"


def patch_world_view_receipts(text: str) -> str:
    """Mount the two test hooks without altering the portable isolation environment."""
    tag = "status_publication.world_view_fixture"
    support = '''function status_publication.world_view_fixture()
    return resident_title and status_publication.installed and status_publication.memory_live
        and status_publication.mirror and role == "client" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
end

status_publication.world_view_trace_count = 0
function status_publication.trace_world_view(route, state, fields, payload, reason)
    if not status_publication.world_view_fixture() or status_publication.world_view_trace_count >= 32 then return end
    local proof = payload:match("\\n(native%-world%-view\\tapplied\\t[^\\n]+)\\n")
    if not proof then return end
    local output = io.open(bridge .. "/portable-world-view-receipts.tsv", "ab")
    if not output then return end
    status_publication.world_view_trace_count = status_publication.world_view_trace_count + 1
    local _, rows = payload:gsub("\\n", "")
    output:write(status_row(route, sequence, state.local_pawn.name, fields[1], rows, #payload, reason), "\\t", proof, "\\n")
    output:close()
end

'''
    scope = "        and world_view_enabled and status_publication.compact() and status_publication.steady(state, current_phase)"
    capability = '    if current_phase == "connected" and world_view_enabled and compact_crates then'
    short_reply = "        SilverfishPublishLocalBridgeReply(pending.ticket, reply)\n        return true"
    full_reply = "            SilverfishPublishLocalBridgeReply(pending.ticket, frame)\n        end"
    dispatch = '    if fields and pending and fields[2] == "native-world-view-apply-local" and not periodic'
    replacements = {
        "function status_publication.steady(state, current_phase)": support + "function status_publication.steady(state, current_phase)",
        capability: capability.replace("and compact_crates", f"and (compact_crates or {tag}())"),
        scope: scope.replace("status_publication.compact()", f"(status_publication.compact() or {tag}())"),
        short_reply: short_reply.replace("\n        return true", '\n        status_publication.trace_world_view("compact", state, fields, reply, "steady")\n        return true'),
        full_reply: full_reply.replace("\n        end", '''
            if pending.world_view_fixture_reason and acknowledged_result == "accepted" then
                status_publication.trace_world_view("full", state, pending.fields, frame, pending.world_view_fixture_reason)
            end
        end'''),
        dispatch: '''    if fields and pending and fields[2] == "native-world-view-apply-local" and status_publication.world_view_fixture() then
        pending.world_view_fixture_reason = periodic and "periodic" or not status_publication.steady(state, current_phase)
            and "scope" or os.clock() >= status_publication.next_frame and "publication-due" or "eligibility"
    end
''' + dispatch,
    }
    for original, patched in replacements.items():
        if text.count(original) != 1:
            raise ValueError(f"The portable replica lacks the exact world view receipt fixture anchor {original!r}")
        text = text.replace(original, patched)
    return text


def receipts(path: Path) -> list[dict]:
    try:
        rows = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return []
    result = []
    for row in rows:
        if not row.endswith("\n"):
            continue
        route, bridge_sequence, pawn, nonce, count, wire_bytes, reason, *fields = row.rstrip("\n").split("\t")
        if (route not in {"compact", "full"} or len(fields) != 32 or fields[:2] != ["native-world-view", "applied"]
                or len(nonce) != 32 or not bridge_sequence.isdecimal() or not count.isdecimal() or not wire_bytes.isdecimal()):
            raise ValueError("The portable stock world view trace is malformed")
        result.append(dict(route=route, bridge_sequence=int(bridge_sequence), pawn=pawn, nonce=nonce, count=int(count),
                           bytes=int(wire_bytes), reason=reason, stock=runtime._parse_world_view(fields, "connected")))
    return result


async def verify_world_view_receipt(host, guest, pawn: str) -> dict:
    area = OBSERVATORY
    actor = WORLD_VIEW_ACTORS[area]
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def ready(row):
        game = row.get("game", {})
        proof = game.get("native_world_view", {})
        return (row.get("local_bridge") == row.get("local_status_source") == "memory"
                and row.get("state") == game.get("state") == "connected" and game.get(native_weather_view.LOCAL_CAPABILITY) is True
                and game.get("pawns", {}).get("local", {}).get("name") == pawn
                and proof.get("actor") == actor and proof.get("mode") == "applied" and proof.get("save_unchanged") is True)

    initial = await wait_resident_status(guest_run, ready)
    await wait_resident_status(host_run, lambda row: row.get("game", {}).get("native_world_view", {}).get("actor") == actor)
    baseline = initial["game"]["native_world_view"]
    epoch = initial["zone_epoch"]
    trace = guest.bridge.directory / TRACE
    observed, latest = None, initial["game"]["sequence"]
    try:
        async with asyncio.timeout(20):
            while observed is None:
                try:
                    status = await current_status(guest_run)
                except (OSError, RuntimeError):
                    await asyncio.sleep(.05)
                    continue
                if not ready(status) or status["zone_epoch"] != epoch:
                    continue
                game = status["game"]
                latest = game["sequence"]
                current = game["native_world_view"]
                for receipt in receipts(trace):
                    # A full stock status after application would already have a greater local sequence.
                    if (receipt["route"] == "compact" and receipt["count"] == 3 and receipt["pawn"] == pawn
                            and receipt["bridge_sequence"] >= latest and receipt["stock"] == current
                            and (current["map"], current["epoch"]) == (area, epoch)
                            and current["sequence"] > baseline["sequence"]
                            and not math.isclose(current["game_seconds"], baseline["game_seconds"], abs_tol=.001)):
                        observed = {"receipt": receipt, "resident_game_sequence": latest}
                        break
    except TimeoutError as error:
        details = [(row["route"], row["bridge_sequence"], row["stock"]["sequence"]) for row in receipts(trace)]
        raise AssertionError(f"No resident status confirmed a new compact receipt at sequence {latest}: {details}") from error

    fallback = next((entry for entry in receipts(trace) if entry["route"] == "full" and entry["pawn"] == pawn
                     and (entry["stock"]["map"], entry["stock"]["actor"], entry["stock"]["epoch"]) == (area, actor, epoch)), None)
    if fallback is not None:
        assert fallback["count"] > 3 and fallback["stock"]["save_unchanged"] is True
    return {"baseline": baseline, "accepted_compact": observed, "naturally_observed_full_fallback": fallback,
            "guest_trace_path": str(trace),
            "native_world_view_scope": {"map": area, "pawn": pawn, "epoch": epoch}}
