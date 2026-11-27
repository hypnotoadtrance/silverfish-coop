"""Time co-op work on isolated seats before and after stock travel to Crossroads."""

import asyncio
from pathlib import Path
import math
import re
import struct

from portable_guest_world_start import wait_resident_status
from tools.silverfish import native_downed_markers, native_kill_volume, native_package_handoff_journal
from tools.silverfish.control import current_status, submit
from tools.silverfish.travel import CROSSROADS


def instrument_profile(source, *, stock_receipts=False):
    code = (Path(__file__).with_name("fixtures") / "portable_crossroads_profile.lua").read_text(encoding="utf-8")
    anchor = "local function write_status("
    before, separator, rest = source.partition(anchor)
    if not separator:
        raise ValueError("The status publication anchor changed")
    names = r"(?:native_[a-z_]+|players(?:\.critters)?)\.[\w]+"
    wrapper = "status_publication.profile.call"
    rest, protected = re.subn(r"\bpcall\((" + names + r"|tick)(?=[,)])",
                              lambda m: 'pcall(' + wrapper + ', "' + m[1] + '", ' + m[1], rest)
    rest, direct = re.subn(
        r"(?<!function )\b(" + names + r"|inspect|write_status|read_command|observe_session|current_world)\((?=[^)]*\))",
                           lambda m: wrapper + '("' + m[1] + '", ' + m[1]
                           + ("" if rest[m.end()] == ")" else ", "), rest)
    if protected < 10 or direct < 20:
        raise ValueError("The gameplay timing anchors changed")
    status_code, boundary, after_status = rest.partition("\nlocal function observe_session()")
    assert boundary
    mark = "status_publication.profile.status_mark"
    for needle, call in (
        ("    sequence = sequence + 1\n", f"    {mark}()\n"),
        ("    if not failure then\n", f'    {mark}("status_base_rows")\n'),
        ("        local crate_rows\n", f'        {mark}("status_gameplay_rows")\n'),
        ('    rows[#rows + 1] = ""\n', f'    {mark}("status_world_rows")\n'),
        ('    local frame = not join_in_native and table.concat(rows, "\\n") or nil\n', None),
        ("    if resident_title and status_publication.installed and status_publication.memory_live",
         f'    {mark}("status_native_publish")\n'),
    ):
        assert status_code.count(needle) == 1, needle
        if call is None:
            status_code = status_code.replace(needle, needle + f'    {mark}("status_concat", #rows - 1, frame and #frame or nil)\n'
                                              '    status_publication.profile.status_size(rows)\n')
        else:
            status_code = status_code.replace(needle, call + needle)
    rest = status_code + boundary + after_status
    if stock_receipts:
        native_publish = "        status_publication.memory_live = SilverfishPublishLocalBridgeStatusRows(rows)\n"
        if native_publish in rest:
            rest = rest.replace(native_publish, native_publish
                                + '        if status_publication.memory_live then\n'
                                '            status_publication.profile.stock_receipt("native-status-publication", 0)\n'
                                '        end\n', 1)
        fast_dispatch = "            status_publication.dispatch(os.clock(), fields, pending, false)\n"
        if fast_dispatch in rest:
            rest = rest.replace(fast_dispatch, '            status_publication.profile.stock_receipt("fast/" .. action, 0)\n'
                                + fast_dispatch, 1)
        # Keep the private status mirror available to the runner while exercising the
        # installed compact interaction branch. Scope validation still runs normally.
        scope = "if resident_title and status_publication.installed and status_publication.memory_live and not status_publication.mirror then"
        gate = ("if interaction and pending and not periodic and current_phase == interaction.phase\n"
                "        and status_publication.compact() and status_publication.steady(state, current_phase) then")
        admission = gate.replace("status_publication.compact()", "resident_title and status_publication.installed "
                                                                  "and status_publication.memory_live")
        publish = "        SilverfishPublishLocalBridgeReply(pending.ticket, readback and reply .. readback or reply)"
        assert all(rest.count(marker) == 1 for marker in (gate, publish))
        rest = rest.replace(scope, scope.replace(" and not status_publication.mirror", ""))
        rest = rest.replace(gate, admission)
        rest = rest.replace(publish, "        if accepted then status_publication.profile.stock_receipt("
                            "fields[2], #reply + (readback and #readback or 0)) end\n" + publish)
    tick = "local function tick(fields, pending, periodic)\n"
    assert rest.count(tick) == 1
    rest = rest.replace(tick, tick + "    local profile_fields = fields\n"
                        "    status_publication.profile.command(profile_fields, periodic, false)\n")
    final_write = '    status_publication.profile.call("write_status", write_status, state, current_phase, not projected'
    assert rest.count(final_write) == 1
    rest = rest.replace(final_write, '    status_publication.profile.command(profile_fields, periodic, true)\n' + final_write)
    mailbox = 'io.open(command_path, "rb")'
    assert before.count(mailbox) == 1
    before = before.replace(mailbox, f'{wrapper}("command_mailbox", io.open, command_path, "rb")')
    viewport = 'FindFirstOf("GameViewportClient")'
    assert before.count(viewport) == 1
    before = before.replace(viewport, f'{wrapper}("viewport_discovery", FindFirstOf, "GameViewportClient")')
    player_query = "statics:GetAllActorsOfClass(world, class, out)"
    assert before.count(player_query) == 1
    before = before.replace(player_query, f'{wrapper}("player_actor_query", function() {player_query} end)')
    fallback_query = 'FindAllOf("Player_FP_C")'
    assert before.count(fallback_query) == 1
    before = before.replace(fallback_query, f'{wrapper}("player_actor_fallback", FindAllOf, "Player_FP_C")')
    return before + "status_publication.profile = (function()\n" + code + "\nend)()\n\n" + anchor + rest


async def read_stock_receipts(seat, probe):
    raw = await probe(seat.bridge, "crossroads-profile", "stockreceipts", reply_end="|end")
    return {name: {"count": int(count), "maximum_bytes": int(size)}
            for name, count, size in (row.split(",") for row in raw.removesuffix("|end").split(";") if row)}


async def read_collector_modes(host, guest, probe):
    replies = await asyncio.gather(*(probe(seat.bridge, "crossroads-profile", "gcstate", reply_end="|end")
                                     for seat in (host, guest)))
    modes = {}
    for role, reply in zip(("host", "guest"), replies, strict=True):
        original, active = reply.removesuffix("|end").split(",")
        modes[role] = {"original": original, "active": active}
    return modes


def instrument_kill_volume(source, *, measure=False, recovery_fixture=False):
    if measure:
        anchor = "local geometry = stock_box(state)"
        assert source.count(anchor) == 1
        source = source.replace(anchor, 'local geometry = SilverfishProfileCrossroadsCall("kill_volume_geometry", stock_box, state)')
    if recovery_fixture:
        gate = 'and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        assert source.count(gate) == 1
        source = source.replace(gate, 'and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
                                ' or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")')
    return source


async def verify_crossroads_recovery(host, guest, _pawn):
    from live_downed_input_smoke import input_proof

    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def local_input(run, *, downed):
        def observed(frame):
            game = frame["game"]
            pawn = game.get("pawns", {}).get("local", {}).get("name")
            inventory = "clear" if run == host_run else "not_applicable"
            return pawn and input_proof(game, pawn, downed=downed, inventory=inventory)

        frame = await wait_resident_status(run, observed)
        return frame["game"]["player_local_input"]

    receipts = {}
    for role, slot, helper_slot, helper_run in (
        ("guest", "guest", "local", host_run), ("host", "local", "guest", guest_run),
    ):
        before = await wait_resident_status(host_run, lambda frame:
            frame.get("link_connected")
            and frame["game"].get(native_kill_volume.KEY, {}).get("state") == "armed"
            and frame["game"].get("player_effects", {}).get(slot, {}).get("state") == "standing")
        game = before["game"]
        victim, helper = game["pawns"][slot], game["pawns"][helper_slot]
        victim_name = victim["name"]
        prior = game.get(native_kill_volume.RECOVERED, {}).get(victim_name, {}).get("sequence", 0)
        drop = await host.bridge.command(native_kill_volume.FIXTURE, victim_name, timeout=5)
        assert native_kill_volume.pending(drop), drop.get(native_kill_volume.KEY)
        fallen = await wait_resident_status(host_run, lambda frame:
            frame["game"].get(native_kill_volume.RECOVERED, {}).get(victim_name, {}).get("sequence", 0) > prior
            and frame["game"].get("player_effects", {}).get(slot, {}).get("state") == "downed")
        receipt = fallen["game"][native_kill_volume.RECOVERED][victim_name]
        status = fallen["game"][native_kill_volume.KEY]
        assert receipt["placement"] == "teammate" and receipt["native_health_at_down"] == 0, receipt
        assert math.dist(receipt["location"], helper["location"]) <= 250, receipt
        assert status["overlaps_vetoed"] > game[native_kill_volume.KEY]["overlaps_vetoed"] and not status["fault"], status
        assert fallen["link_connected"] and (fallen["generation"], fallen["zone_epoch"]) == (
            before["generation"], before["zone_epoch"])

        def guest_observed(frame):
            if frame.get("zone_epoch") != before["zone_epoch"]:
                return False
            effects = frame["game"].get("player_effects", {})
            if role == "guest":
                return effects.get("local", {}).get("state") == "downed"
            host_down = frame.get("gameplay", {}).get("players", {}).get("host", {}).get("downed")
            return host_down and effects.get("guest", {}).get("crouched") is True

        await wait_resident_status(guest_run, guest_observed)
        victim_run = guest_run if role == "guest" else host_run
        downed_input = await local_input(victim_run, downed=True)
        if role == "host":
            await wait_resident_status(guest_run, lambda frame:
                "host" in frame["game"].get(native_downed_markers.KEY, {}).get("markers", {}))
        revived = await submit(helper_run, "native-interaction-fixture")
        assert revived["ok"] and revived["native_interaction_fixture"], revived
        after = await wait_resident_status(host_run, lambda frame:
            frame["game"].get("player_effects", {}).get(slot, {}).get("state") == "standing"
            and frame["game"]["pawns"][slot]["name"] == victim_name
            and frame["game"]["pawns"][slot]["health"] > 0)
        assert after["game"]["pawns"][helper_slot]["health"] >= helper["health"], after["game"]["pawns"]
        receipts[role] = {"recovered": receipt, "revived_health": after["game"]["pawns"][slot]["health"],
                          "downed_input": downed_input, "revived_input": await local_input(victim_run, downed=False)}
        if role == "host":
            await wait_resident_status(guest_run, lambda frame:
                frame.get("gameplay", {}).get("players", {}).get("host", {}).get("downed") is False
                and "host" not in frame["game"].get(native_downed_markers.KEY, {}).get("markers", {}))
            receipts[role]["guest_marker_appeared_and_cleared"] = True
    return {"stock_crossroads_recovery": receipts}


def instrument_zone_walls(source):
    anchor = "local sources = wall_sources(world, scope.map, route, scope.fixture, scope.secondary)"
    assert source.count(anchor) == 1
    return source.replace(anchor, 'local sources = SilverfishProfileCrossroadsCall("zone_wall_sources", '
                          'wall_sources, world, scope.map, route, scope.fixture, scope.secondary)')


def instrument_stash_binding(source):
    anchor = "local function report(state, phase)"
    insertion = "local function identifier(id)"
    assert source.count(anchor) == source.count(insertion) == 1
    return source.replace(anchor, "local function qualify_binding(state, phase)").replace(
        insertion, 'local function report(...)\n'
        '    return SilverfishProfileCrossroadsCall("stash_ui_binding", qualify_binding, ...)\n'
        'end\n\n' + insertion)


def instrument_avatar_lean(source):
    anchor = "function lean_bridge.update(get_world, role, leaving)"
    read = "SilverfishReadNativeLocalLean(current.pawn_address, current.world_address, role == \"host\")"
    assert source.count(anchor) == source.count(read) == 1
    fixture = """
local lean_sample_count = 0
local function check_native_lean(current, world, host)
    local roll = SilverfishProfileCrossroadsCall("avatar_lean.native_read", SilverfishReadNativeLocalLean,
        current.pawn_address, current.world_address, host)
    if type(roll) == "number" and math.abs(roll) > 4 then
        local legacy = avatar.local_lean(current.pawn, world)
        assert(legacy and math.abs(roll - legacy) < 0.001, "Native lean disagreed with the stock component")
        lean_sample_count = lean_sample_count + 1
    end
    return roll
end

SilverfishProfileLocalLean = function()
    local current = assert(scope, "Local stock lean scope unavailable")
    local pawn, world, host = current.pawn_address, current.world_address, current.role == "host"
    local native = SilverfishReadNativeLocalLean
    local function legacy()
        if qualify_lua(current, current.world) then return avatar.local_lean(current.pawn, current.world) end
    end
    local value, expected = native(pawn, world, host), legacy()
    assert(type(value) == "number" and type(expected) == "number" and math.abs(value - expected) < 0.001)
    assert(native(pawn + 1, world, host) == nil and native(pawn, world + 1, host) == nil
        and native(pawn, world, not host) == nil, "Lean read accepted a different pawn, world or role")
    local reads = 256
    local function measure(read, ...)
        local start = SilverfishBridgeProfileStamp()
        for _ = 1, reads do read(...) end
        return (SilverfishBridgeProfileStamp() - start) / 1000
    end
    local native_ms, legacy_ms = measure(native, pawn, world, host), measure(legacy)
    return ("%d,%.3f,%.3f,%d"):format(reads, native_ms, legacy_ms, lean_sample_count)
end

"""
    return source.replace(read, 'check_native_lean(current, world, role == "host")').replace(anchor, fixture + anchor)


async def compare_lean_reader(seat, probe):
    info = await probe(seat.bridge, "crossroads-profile", "lean", reply_end="|end")
    reads, native_ms, legacy_ms, held = info.removesuffix("|end").split(",")
    return {"reads_per_path": int(reads), "native_ms": float(native_ms), "lua_ms": float(legacy_ms),
            "nonzero_samples_matched": int(held), "wrong_pawn_world_and_role_refused": True}


def instrument_input_mappings(source):
    original = "local function observe_mapping("
    anchor = "local function verified_mapping("
    if source.count(original) != 1 or source.count(anchor) != 1:
        raise ValueError("The input mapping timing anchors changed")
    wrapper = (
        "local function observe_mapping(...)\n"
        '    return SilverfishProfileCrossroadsCall("players.observe_mapping", collect_mapping, ...)\n'
        "end\n\n"
    )
    return source.replace(original, "local function collect_mapping(").replace(anchor, wrapper + anchor)


def instrument_inline_crate_scan(source):
    probes = {
        "sample(state, phase, status_row, profiles)": (
            "local readings = ", "crate_inline_scan", "sample, state, phase, status_row, profiles"),
        "refresh_blocks(scan)": ("            ", "crate_snapshot_refresh", "refresh_blocks, scan"),
        "refresh_blocks(sampler)": ("if sampled then ", "crate_command_refresh", "refresh_blocks, sampler"),
    }
    for call, (prefix, name, arguments) in probes.items():
        anchor = prefix + call
        if source.count(anchor) != 1:
            raise ValueError(f"The crate status timing anchor changed: {name}")
        source = source.replace(anchor, f'{prefix}SilverfishProfileCrossroadsCall("{name}", {arguments})')
    return source


def instrument_loose_discovery(source):
    anchor = "SilverfishDiscoverLoosePickups(pawn:GetAddress(), current.host)"
    if source.count(anchor) != 1:
        raise ValueError("The loose-item discovery anchor changed")
    source = source.replace(anchor, 'SilverfishProfileCrossroadsCall("loose_discovery", '
                            'SilverfishDiscoverLoosePickups, pawn:GetAddress(), current.host)')
    batch = "sample(current.pawn:GetAddress(), current.host, table.unpack(sources))"
    if batch in source:
        source = source.replace(batch, 'SilverfishProfileCrossroadsCall("authored_pickup_batch", '
                                'sample, current.pawn:GetAddress(), current.host, table.unpack(sources))')
        fallback = "local qualified = refresh_authored_sources(state, phase)"
        assert source.count(fallback) == 1
        source = source.replace(fallback, 'local qualified = SilverfishProfileCrossroadsCall('
                                '"authored_pickup_scalar_fallback", refresh_authored_sources, state, phase)')
    return source


def instrument_npc_class_discovery(source):
    anchor = ("local base = class\n"
              "        while valid(base) and not npc_families[base:GetFullName()] do base = base:GetSuperStruct() end")
    if source.count(anchor) != 1:
        raise ValueError("The NPC marker class discovery anchor changed")
    return source.replace(anchor, 'local base = SilverfishProfileCrossroadsCall("npc_marker_class_discovery", function()\n'
                          '            local ancestor = class\n'
                          '            while valid(ancestor) and not npc_families[ancestor:GetFullName()] do\n'
                          '                ancestor = ancestor:GetSuperStruct()\n'
                          '            end\n'
                          '            return ancestor\n'
                          '        end)')


def instrument_shop_discovery(source):
    anchor = "pcall(shop_actors, world)"
    if source.count(anchor) != 1:
        raise ValueError("The stock shop actor discovery anchor changed")
    return source.replace(anchor, 'pcall(SilverfishProfileCrossroadsCall, "stock_shop_discovery", shop_actors, world)')


def instrument_journal_context(source):
    gate = 'and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
    call = "local context = host(state, phase)\n    if not context or not same(menu:GetOwningPlayer()"
    if source.count(gate) != 1 or source.count(call) != 1:
        raise ValueError("The stock journal timing anchors changed")
    return source.replace(gate, 'and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
                          ' or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")').replace(
        call, call.replace("host(state, phase)", 'SilverfishProfileCrossroadsCall("journal_host_context", host, state, phase)'))


def instrument_host_drop_context(source):
    anchor = "host_save.canonical_host(state, phase)"
    if source.count(anchor) != 1:
        raise ValueError("The host drop context timing anchor changed")
    return source.replace(anchor, 'SilverfishProfileCrossroadsCall('
                          '"host_drop_context", host_save.canonical_host, state, phase)')


def instrument_weather_status(source):
    previous, boundary, status = source.partition("function view.status(state, phase)")
    anchor = "local actual = readback(current, frame, lease.saved_seconds)"
    if not boundary or status.count(anchor) != 1:
        raise ValueError("The guest weather status timing anchor changed")
    return previous + boundary + status.replace(
        anchor, 'local actual = SilverfishProfileCrossroadsCall('
        '"weather_status_readback", readback, current, frame, lease.saved_seconds)')


def instrument_death_poll(source):
    anchor = "pcall(SilverfishTakeScopedNativeDeathHit, record.address, scope_token)"
    if source.count(anchor) != 2:
        raise ValueError("The scoped death receipt timing anchor changed")
    return source.replace(anchor, 'pcall(SilverfishProfileCrossroadsCall, "death_pawn_receipt", '
                          'SilverfishTakeScopedNativeDeathHit, record.address, scope_token)', 1)


def instrument_spawn_context(source):
    anchor = "pcall(host_context, state, phase)"
    if source.count(anchor) != 1:
        raise ValueError("The stock spawn status timing anchor changed")
    return source.replace(anchor, 'pcall(SilverfishProfileCrossroadsCall, "roster_spawn_qualify", host_context, state, phase)')


def instrument_flashlight_scope(source):
    previous, boundary, sync = source.partition("function flashlight.sync(state, phase)")
    body, next_function, rest = sync.partition("function flashlight.command(")
    anchor = "scope(state, phase)"
    if not boundary or not next_function or body.count(anchor) != 1:
        raise ValueError("The flashlight scope timing anchor changed")
    return previous + boundary + body.replace(
        anchor, 'SilverfishProfileCrossroadsCall("flashlight_sync_scope", scope, state, phase)') + next_function + rest


def instrument_anomaly_work(source):
    calls = (
        "pyrogeist.apply", "shadows.project", "lamps.project", "observers.target", "observers.publish_rewards",
        "effects.observe_spatial_cues", "canisters.rows", "drum_fire.rows", "fixed.rows", "destructibles.rows", "acid_pools.rows",
    )
    start, separator, rest = source.partition("function fields.rows(")
    if not separator:
        raise ValueError("The anomaly status timing anchor changed")
    for call in calls:
        anchor = call + "("
        if rest.count(anchor) != 1:
            raise ValueError(f"The anomaly status call changed: {call}")
        rest = rest.replace(anchor, f'SilverfishProfileCrossroadsCall("anomalies.{call}", {call}, ')
    return start + separator + rest


def instrument_vital_reads(source):
    anchor = "function scope.vitals_for_status(details, save)\n"
    if source.count(anchor) != 1:
        raise ValueError("The vitals timing anchor changed")
    fixture = """local vital_details, vital_save
function SilverfishProfileCrossroadsVitals()
    local details, save = vital_details, vital_save
    local function from_lua()
        local fatigue, intox, sickness = scope.personal_flags(save)
        return save.PlayerHealth, save.PlayerFood, save.PlayerRegen, save.PlayerStim,
            fatigue, intox, sickness, save.PlayerRads
    end
    local native = table.pack(scope.vitals_for_status(details, save))
    local stock = table.pack(from_lua())
    for index = 1, 8 do assert(native[index] == stock[index], "native vital read differs at field " .. index) end
    local name, pawn_address, save_address = details.name, details.pawn:GetAddress(), save:GetAddress()
    assert(type(SilverfishReadBoundNativeVitals(name, pawn_address, save_address)) == "number", "native vitals unavailable")
    assert(SilverfishReadBoundNativeVitals(name, pawn_address, save_address + 1) == nil, "mismatched save read accepted")
    assert(SilverfishReadBoundNativeVitals(name, pawn_address + 1, save_address) == nil, "mismatched pawn read accepted")
    local rounds = 512
    local function measure(fn, ...)
        local before = os.clock()
        for index = 1, rounds do fn(...) end
        return (os.clock() - before) * 1000
    end
    local native_first = measure(scope.vitals_for_status, details, save)
    local lua_first = measure(from_lua)
    local lua_second = measure(from_lua)
    local native_second = measure(scope.vitals_for_status, details, save)
    return ("%d,%.3f,%.3f"):format(rounds * 2, native_first + native_second, lua_first + lua_second)
end

"""
    wrapped = source.replace(anchor, fixture + anchor + "    vital_details, vital_save = details, save\n")
    baseline = "SilverfishReadBoundNativeVitals(details.name, details.pawn:GetAddress(), save:GetAddress())"
    if wrapped.count(baseline) != 1:
        raise ValueError("The native vitals call changed")
    return wrapped.replace(baseline, 'SilverfishProfileCrossroadsCall("vitals.native", SilverfishReadBoundNativeVitals, '
                           'details.name, details.pawn:GetAddress(), save:GetAddress())')


def instrument_guest_status_binding(source):
    if "function guest_save.status_context(" not in source:
        return source
    sync = "function guest_save.sync(state, phase)\n"
    checked = "    if not changed then state.status_guest_saves, state.status_guest_lease = verified, fresh end\n"
    end = "\nreturn guest_save\n"
    assert source.count(sync) == source.count(checked) == source.count(end) == 1
    probe = """
function SilverfishProfileCrossroadsGuestBinding()
    local state = assert(guest_status_observation, "missing observed guest status")
    local detail = assert(state.guests[1], "missing guest pawn")
    local pawn, save, character = guest_save.status_context(state, "hosting", detail, true)
    local expected_pawn, expected_save, expected_character = guest_save.exact_item_context(state, "hosting", detail.name)
    assert(same(pawn, expected_pawn) and same(save, expected_save) and character == expected_character)
    local observed_context = guest_save.observed_item_context or function(state, phase, detail)
        return guest_save.status_context(state, phase, detail, true)
    end
    if guest_save.observed_item_context then
        local observed_pawn, observed_save, observed_character = observed_context(state, "hosting", detail)
        assert(same(pawn, observed_pawn) and same(save, observed_save) and character == observed_character)
        local cached = state.status_guest_saves
        state.status_guest_saves = nil
        local fallback_pawn, fallback_save, fallback_character = observed_context(state, "hosting", detail)
        assert(same(pawn, fallback_pawn) and same(save, fallback_save) and character == fallback_character)
        assert(not observed_context(state, "hosting", {name=detail.name, pawn=state.local_pawn.pawn}))
        state.status_guest_saves = cached
    end
    assert(not guest_save.status_context(state, "connected", detail, true))
    assert(not guest_save.status_context(state, "hosting", {name=detail.name, pawn=state.local_pawn.pawn}, true))
    assert(not guest_save.status_context({world=state.world, map="other", status_guest_saves=state.status_guest_saves,
        status_guest_lease=true}, "hosting", detail, true))
    local rounds = 128
    local function measure(fn, ...)
        local start = os.clock()
        for _ = 1, rounds do fn(...) end
        return 1000 * (os.clock() - start)
    end
    local reused = measure(observed_context, state, "hosting", detail)
    local exact = measure(guest_save.exact_item_context, state, "hosting", detail.name)
    exact = exact + measure(guest_save.exact_item_context, state, "hosting", detail.name)
    reused = reused + measure(observed_context, state, "hosting", detail)
    return ("%d,%.3f,%.3f"):format(rounds * 2, exact, reused)
end
"""
    return source.replace(sync, "local guest_status_observation\n\n" + sync).replace(checked, checked +
        "    if state.status_guest_lease then guest_status_observation = state end\n").replace(end, probe + end)


def read_comparison(raw):
    raw, memory, sizes, commands = raw.split("|")
    raw, status = raw.split("^")
    summary, slow_frame = raw.split("!")
    duration, *sections = summary.split(";")
    totals = {}
    for section in sections:
        name, count, p50, p95, p99, maximum, total, *cycles = section.split(",")
        cpu = dict(zip(("p50", "p95", "p99", "max", "sum"), map(int, cycles), strict=True))
        totals[name] = dict(count=int(count), p50_ms=float(p50), p95_ms=float(p95), p99_ms=float(p99),
                            max_ms=float(maximum), sum_ms=float(total), thread_cycles=cpu)
        assert all(value >= 0 for value in cpu.values()), (name, cpu)
    assert float(duration) >= 3 and totals["frame"]["count"] > 10 and totals["write_status"]["count"] > 3
    peak, *calls = slow_frame.split(";")
    elapsed, peak_cycles = peak.split(",")
    slow_calls, call_cycles = {}, {}
    for entry in calls:
        name, wall, cycles = entry.split(",")
        slow_calls[name], call_cycles[name] = float(wall), int(cycles)
    frame_size, *stages = status.split(";")
    max_rows, max_bytes, heap_start, heap_peak, max_drop, large_drops, old_pause, old_step, pause, step = frame_size.split(",")
    heap = {"start_kib": float(heap_start), "peak_kib": float(heap_peak),
            "largest_drop_kib": float(max_drop), "drops_over_one_mib": int(large_drops)}
    if int(old_pause):
        heap["gc_settings"] = {"previous_pause": int(old_pause), "previous_step_multiplier": int(old_step),
                               "sampled_pause": int(pause), "sampled_step_multiplier": int(step)}
    status_peaks = {}
    for entry in stages:
        name, wall, cycles, heap_delta = entry.split(",")
        status_peaks[name] = {"elapsed_ms": float(wall), "thread_cycles": int(cycles),
                              "lua_heap_delta_kib": float(heap_delta)}
    assert int(max_bytes) > 0 and int(max_rows) > 0 and "status_native_publish" in status_peaks, status
    heap_calls = {}
    for entry in memory.split(";")[1:]:
        name, count, drops, growth, peak_growth = entry.split(",")
        heap_calls[name] = {"nonnegative_samples": int(count), "heap_drops": int(drops),
                            "inclusive_growth_kib": float(growth), "peak_inclusive_growth_kib": float(peak_growth)}
    status_bytes_by_tag = {entry.split(",")[0]: int(entry.split(",")[1]) for entry in sizes.split(";")[1:]}
    assert status_bytes_by_tag and sum(status_bytes_by_tag.values()) <= int(max_bytes)
    tick_sources = {}
    for entry in commands.split(";")[1:]:
        name, ticks, snapshots = entry.split(",")
        tick_sources[name] = {"ticks": int(ticks), "snapshot_attempts": int(snapshots)}
    assert tick_sources
    return {"seconds": float(duration), "timings": totals, "status_sections": status_peaks,
            "max_status_bytes": int(max_bytes), "max_status_rows": int(max_rows), "lua_heap": heap,
            "heap_calls": heap_calls, "sample_status_bytes_by_tag": status_bytes_by_tag, "tick_sources": tick_sources,
            "slowest_interval": {"elapsed_ms": float(elapsed), "inclusive_calls_ms": slow_calls,
                                 "thread_cycles": int(peak_cycles), "inclusive_call_cycles": call_cycles}}


async def capture_rendered_frame(seat, probe):
    screenshots = seat.profile / "Saved/Screenshots"
    existing = set(screenshots.rglob("*.png"))
    reply = await probe(seat.bridge, "crossroads-profile", "shot", reply_end="|end")
    assert reply == "shot|end", reply
    async with asyncio.timeout(12):
        while True:
            for path in set(screenshots.rglob("*.png")) - existing:
                with path.open("rb") as image:
                    header = image.read(24)
                if len(header) == 24 and header.startswith(b"\x89PNG\r\n\x1a\n"):
                    width, height = struct.unpack(">II", header[16:24])
                    assert width >= 320 and height >= 240, (path, width, height)
                    return {"path": str(path), "width": width, "height": height}
            await asyncio.sleep(.15)


async def profile_world(host, guest, area, probe, *, gc_trial=False):
    guest_run = guest.client / "run"
    seats = {"host": host, "guest": guest}
    runs = {"host": host.campaign / "run", "guest": guest_run}
    counters = ("quic_datagrams_sent", "quic_payload_bytes_sent", "quic_payload_bytes_received", "game_packets_queued",
                "game_packets_delivered", "game_payload_bytes_queued", "game_wire_payload_bytes_queued", "game_packets_compressed",
                "keepalive_probes_queued", "keepalive_probes_acked", "keepalive_ticks_congested",
                "relay_queue_evicted_frames", "relay_queue_expired_frames", "interaction_priority_passes",
                "snapshot_responses_encoded", "snapshot_delta_responses",
                "snapshot_row_delta_responses", "snapshot_rows_changed",
                "snapshot_encoded_bytes", "snapshot_plaintext_bytes", "snapshot_responses_decoded", "snapshot_received_bytes")

    async def transport():
        frames = await asyncio.gather(*(current_status(run) for run in runs.values()))
        return {seat: frame.get("transport", {}) for seat, frame in zip(runs, frames, strict=True)}

    async with asyncio.timeout(8):
        while True:
            game = (await current_status(runs["host"])).get("game", {})
            pawn_rates = game.get("native_replication_rates", {})
            if game.get("map") == area and len(pawn_rates) >= 2:
                break
            await asyncio.sleep(.1)
    assert all(0 < entry["cap_hz"] <= 20 for entry in pawn_rates.values()), pawn_rates

    def traffic(before, after):
        return {seat: {**{key: after[seat][key] - before[seat][key] for key in counters
                          if key in before[seat] and key in after[seat] and before[seat][key] <= after[seat][key]},
                       "keepalive_rtt_us": after[seat].get("keepalive_rtt_us"),
                       "relay_queue_wait_max_ns": after[seat].get("relay_queue_wait_max_ns")}
                for seat in runs}

    async def command(seat, action):
        response = await probe(seat.bridge, "crossroads-profile", action, reply_end="|end")
        return response.removesuffix("|end")

    async def arm(action="arm"):
        assert await asyncio.gather(*(command(seat, action) for seat in seats.values())) == ["armed", "armed"]

    async def read(seat):
        async with asyncio.timeout(7):
            while (raw := await command(seat, "read")) == "sampling":
                await asyncio.sleep(.16)
        return read_comparison(raw)

    async def collect():
        profiles = await asyncio.gather(*(read(seat) for seat in seats.values()))
        for seat, profile in zip(seats, profiles, strict=True):
            timings = profile["timings"]
            for operation in ("command_mailbox", "crate_inline_scan", "journal_host_context",
                              "death_pawn_receipt", "roster_spawn_qualify",
                              "player_actor_fallback", "stash_ui_binding"):
                assert operation not in timings, (operation, timings.get(operation))
            discoveries = timings["loose_discovery"]["count"]
            assert 0 < discoveries <= math.ceil(profile["seconds"] / .125) + 1, profile
            lookups = timings.get("player_actor_query", {}).get("count", 0)
            if seat == "guest":
                assert 0 < lookups <= math.ceil(profile["seconds"]) + 1, (area, lookups, profile["seconds"])
                weather_reads = timings.get("weather_status_readback", {}).get("count", 0)
                assert weather_reads <= math.ceil(profile["seconds"] / .5) + 1, (area, weather_reads)
            else:
                assert lookups == 0, (area, lookups)
                drop_checks = timings.get("host_drop_context", {}).get("count", 0)
                assert 0 < drop_checks <= timings["write_status"]["count"], (area, drop_checks)
        return dict(zip(seats, profiles, strict=True))

    async def compare_vitals(seat):
        calls, native_ms, lua_ms = map(float, (await command(seat, "vitals")).split(","))
        return {"reads_per_path": int(calls), "native_ms": native_ms, "lua_ms": lua_ms,
                "all_vitals_matched": True, "wrong_pawn_and_save_refused": True}

    async def compare_rows(seat):
        calls, native_ms, lua_ms, native_cycles, lua_cycles = (await command(seat, "rows")).split(",")
        return {"rows_per_path": int(calls), "native_ms": float(native_ms), "lua_ms": float(lua_ms),
                "native_cycles": int(native_cycles), "lua_cycles": int(lua_cycles), "all_rows_matched": True}

    async def compare_switch_reads(seat):
        reply = await command(seat, "switchreader")
        if reply == "unavailable":
            return None
        assert not reply.startswith("switch comparison failed"), reply
        switches, native_ms, lua_ms, native_kib, lua_kib = map(float, reply.split(","))
        return {"fast_reads_per_pass": int(switches), "passes_per_reader": 32, "all_rows_matched": True,
                "native_ms": native_ms, "lua_ms": lua_ms, "native_heap_growth_kib": native_kib,
                "lua_heap_growth_kib": lua_kib}

    def position(status):
        return status.get("game", {}).get("pawns", {}).get("local", {}).get("location")

    def pawns_here(frame):
        game = frame.get("game", {})
        pawns = game.get("pawns", {})
        return (game.get("map") == area and set(pawns) == {"local", "guest"}
                and all(row.get("name", "").startswith(f"Player_FP_C {area}.") and row.get("location")
                        for row in pawns.values())
                and pawns["local"]["name"] != pawns["guest"]["name"])

    baseline = await wait_resident_status(guest_run, pawns_here)
    wall_status = await asyncio.gather(*(wait_resident_status(
        run, lambda frame: frame.get("game", {}).get("map") == area
        and frame["game"].get("native_zone_wall", {}).get("state") == "ready"
        and not frame["game"]["native_zone_wall"]["failure"]) for run in runs.values()))
    collector_modes = await read_collector_modes(host, guest, probe)
    before = await transport()
    await arm()
    idle = await collect()
    after_idle = await transport()
    heartbeat = {}
    if area == CROSSROADS and "keepalive_probes_queued" in after_idle["guest"]:
        clock = asyncio.get_running_loop().time
        try:
            assert await asyncio.gather(*(command(seat, "lowfps") for seat in seats.values())) == ["lowfps", "lowfps"]
            start = clock()
            heartbeat_before = await transport()
            await arm()
            low_fps = await collect()
            heartbeat_after = await transport()
            seconds = clock() - start
        finally:
            assert await asyncio.gather(*(command(seat, "uncap") for seat in seats.values())) == ["uncap", "uncap"]
        frame_ms = {seat: profile["timings"]["frame"]["p50_ms"] for seat, profile in low_fps.items()}
        rates = traffic(heartbeat_before, heartbeat_after)
        guest_pulses = rates["guest"]
        queued = guest_pulses["keepalive_probes_queued"]
        acked = guest_pulses["keepalive_probes_acked"]
        heartbeat = {"low_fps_heartbeat": {"seconds": seconds, "median_frame_interval_ms": frame_ms,
                                           "guest_probes_per_second": queued / seconds, "link": rates}}
        assert all(ms >= 75 for ms in frame_ms.values()), heartbeat
        assert 17 <= queued / seconds <= 23 and acked >= queued - 3, heartbeat

    async def walk(laps):
        movement = []
        # Use normal guest movement with short pulses near the stock arrival point.
        for x, y in ((1, 0), (0, 1), (-1, 0), (0, -1)) * laps:
            reply = await submit(guest_run, "walk", x=x, y=y, milliseconds=550, continuous=True)
            assert reply["ok"] and reply["movement_scheduled"], reply
            await asyncio.sleep(.58)
            point = position(await current_status(guest_run))
            if point:
                movement.append(math.dist(point, position(baseline)))
        assert movement and max(movement) >= 40, movement
        return max(movement)

    await arm()
    displacement = await walk(1)
    moving = await collect()
    after_movement = await transport()
    coarse = {}
    if area == CROSSROADS:
        await arm("coarse")
        coarse_displacement = await walk(2)
        samples = await asyncio.gather(*(read(seat) for seat in seats.values()))
        assert all(sample["seconds"] >= 5 for sample in samples), samples
        coarse = {"coarse_guest_walking": dict(zip(seats, samples, strict=True)),
                  "coarse_guest_displacement_cm": coarse_displacement}
        if gc_trial == "generational":
            results, switches = {}, {}
            original, = {entry["active"] for entry in collector_modes.values()}
            mode = original

            async def switch(label, target):
                previous = mode
                changes = await asyncio.gather(*(command(seat, target) for seat in seats.values()))
                recorded = {}
                for seat, reply in zip(seats, changes, strict=True):
                    current, duration, before, after = reply.split(",")
                    assert current == previous, (seat, target, current, previous)
                    recorded[seat] = {"previous": current, "switch_ms": float(duration),
                                      "heap_before_kib": float(before), "heap_after_kib": float(after)}
                switches[label] = {"target": target, **recorded}

            try:
                for label, target in (("before", "incremental"), ("generational_first", "generational"),
                                      ("generational_second", "generational"), ("after", "incremental")):
                    if target != mode:
                        await switch(label, target)
                        mode = target
                        await walk(2)
                    before_mode = await transport()
                    await arm("gcmode")
                    travelled = await walk(5)
                    samples = await asyncio.gather(*(read(seat) for seat in seats.values()))
                    assert all(sample["seconds"] >= 12 for sample in samples), samples
                    results[label] = {"mode": mode, "displacement_cm": travelled,
                                      **dict(zip(seats, samples, strict=True)),
                                      "transport": traffic(before_mode, await transport())}
                assert mode == "incremental"
            finally:
                # The isolated seats may continue into further gameplay checks.
                await asyncio.gather(*(command(seat, original) for seat in seats.values()))
            coarse["gc_mode_comparison"] = {"windows": results, "switches": switches}
        elif gc_trial:
            results = {}
            original, = {entry["active"] for entry in collector_modes.values()}
            try:
                if original != "incremental":
                    await asyncio.gather(*(command(seat, "incremental") for seat in seats.values()))
                    await walk(2)
                for label, action in (("before", "gcbaseline"), ("tuned", "gctuned"), ("after", "gcbaseline")):
                    await arm(action)
                    travelled = await walk(5)
                    samples = await asyncio.gather(*(read(seat) for seat in seats.values()))
                    assert all(sample["seconds"] >= 12 for sample in samples), samples
                    if label == "tuned":
                        for sample in samples:
                            applied = sample["lua_heap"]["gc_settings"]
                            assert (applied["sampled_pause"], applied["sampled_step_multiplier"]) == (120, 160), applied
                    results[label] = {"displacement_cm": travelled, **dict(zip(seats, samples, strict=True))}
            finally:
                if original != "incremental":
                    await asyncio.gather(*(command(seat, original) for seat in seats.values()))
            coarse["gc_comparison"] = results
    settled = await wait_resident_status(guest_run, pawns_here)
    comparison = await asyncio.gather(*(compare_vitals(seat) for seat in seats.values()))
    binding = await command(host, "guestbinding")
    guest_binding = {}
    if binding != "unavailable":
        calls, exact, reused = map(float, binding.split(","))
        guest_binding = {"guest_binding_comparison": {"reads_per_path": int(calls),
                         "full_qualification_ms": exact, "observed_binding_ms": reused}}
    rows = await asyncio.gather(*(compare_rows(seat) for seat in seats.values()))
    switches = await asyncio.gather(*(compare_switch_reads(seat) for seat in seats.values()))
    lean = await asyncio.gather(*(compare_lean_reader(seat, probe) for seat in seats.values()))
    journal = {}
    if area == CROSSROADS:
        async def journal_action(action, receipt):
            reply = await host.bridge.command("native-host-invite-fixture", action)
            assert reply["ack"]["message"] == receipt, reply

        await journal_action("open", "native_host_invite_journal_open")
        view = await wait_resident_status(runs["host"], lambda frame:
            frame["game"].get("native_host_invite_journal") == "journal"
            and frame["game"].get(native_package_handoff_journal.JOURNAL, {}).get("state") == "idle")
        await journal_action("close", "native_host_invite_journal_closed")
        await wait_resident_status(runs["host"], lambda frame: "native_host_invite_journal" not in frame["game"])
        journal = {"journal_open_and_close": view["game"][native_package_handoff_journal.JOURNAL]}
    return {"collector_modes": collector_modes, "idle": idle, "guest_walking": moving,
            "guest_max_displacement_cm": displacement,
            "transport_idle": traffic(before, after_idle), "transport_guest_walking": traffic(after_idle, after_movement),
            "pawn_replication_rates": pawn_rates,
            "row_format_comparison": dict(zip(seats, rows, strict=True)),
            "switch_reader_comparison": dict(zip(seats, switches, strict=True)),
            "lean_reader_comparison": dict(zip(seats, lean, strict=True)),
            "zone_walls": {seat: frame["game"]["native_zone_wall"]
                           for seat, frame in zip(seats, wall_status, strict=True)},
            "guest_roster": {slot: row["name"] for slot, row in settled["game"]["pawns"].items()},
            "vitals_comparison": dict(zip(seats, comparison, strict=True)), **heartbeat, **guest_binding, **coarse, **journal}
