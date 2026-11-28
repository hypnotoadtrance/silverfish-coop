local probe = {}
local prefix = bridge .. "/crossroads-profile-"
local capture, complete, previous, previous_cycles, last
local stamp = SilverfishBridgeProfileStamp
local window_us = 3000000
local coarse_window_us = 5000000
local gc_window_us = 12000000
local gc_suspended_growth_limit_kib = 96 * 1024
local gc_pause, gc_step_multiplier = 120, 160
local stock_receipts = {}
local scheduler_probe
local long_interval_limit = 5
local cached_format = SilverfishFormatLocalStatusRow
local uncached_format = SilverfishFormatLocalStatusRowUncached
local last_status_sample
local last_census_map, watched_title_scope, session_publication
local native_ownership = {connected = false, rows = {literal = {}, marker = {}}}
local clock_lua_requests, clock_lua_count = {}, 0
local health_lua_requests, health_lua_count = {}, 0

local function record(active, tag, wall_ms, cycles)
    local rows = active.rows[tag]
    if not rows then
        rows = {wall = {}, cycles = {}}
        active.rows[tag] = rows
    end
    rows.wall[#rows.wall + 1] = wall_ms
    rows.cycles[#rows.cycles + 1] = cycles
end

local function record_call(active, tag, wall_ms, used_cycles)
    record(active, tag, wall_ms, used_cycles)
    local call = active.calls[tag]
    if call then
        call.wall, call.cycles = call.wall + wall_ms, call.cycles + used_cycles
    else
        active.calls[tag] = {wall = wall_ms, cycles = used_cycles}
    end
end

local function measured(active, tag, started, started_cycles, heap_before, ...)
    local now, cycles = stamp()
    local wall_ms, used_cycles = (now - started) / 1000, cycles - started_cycles
    if heap_before then
        local growth = collectgarbage("count") - heap_before
        local sample = active.heap_calls[tag]
        if not sample then
            sample = {retained = 0, peak = 0, samples = 0, drops = 0}
            active.heap_calls[tag] = sample
        end
        if growth >= 0 then
            sample.retained = sample.retained + growth
            sample.peak = math.max(sample.peak, growth)
            sample.samples = sample.samples + 1
        else
            sample.drops = sample.drops + 1
        end
    end
    record_call(active, tag, wall_ms, used_cycles)
    return ...
end

function probe.call(tag, operation, ...)
    if not capture or capture.coarse and tag ~= "write_status" and tag ~= "tick"
        and tag ~= "status_native_reply" and tag ~= "observer_world_lookup" then return operation(...) end
    local heap_before = not capture.coarse and collectgarbage("count")
    local started, started_cycles = stamp()
    return measured(capture, tag, started, started_cycles, heap_before, operation(...))
end

function probe.read_observer_world()
    return probe.call("observer_world_lookup", current_world)
end

function probe.observer_resolver(cached)
    if capture and capture.uncached_world then return probe.read_observer_world end
    return cached
end

function probe.host_maintenance_suspended()
    if not capture then return false, false end
    if capture.suspend_observers then capture.skipped_observers = capture.skipped_observers + 1 end
    if capture.suspend_status then capture.skipped_status = capture.skipped_status + 1 end
    return capture.suspend_observers, capture.suspend_status
end

function probe.observers_begin()
    if capture then return stamp() end
end

function probe.observers_end(started, started_cycles)
    if not started or not capture then return end
    local now, cycles = stamp()
    record_call(capture, "delayed_observers", (now - started) / 1000, cycles - started_cycles)
end

function probe.keep_status_sample(rows, token, local_health)
    if not capture or capture.status_sample then return end
    if token then
        local frame = assert(SilverfishFormatNativeSessionStatusRows(rows, token, local_health),
            "could not render native status sample")
        local materialized = {}
        for line in frame:gmatch("[^\n]+") do materialized[#materialized + 1] = line end
        rows = materialized
    end
    capture.status_sample = rows
end

function probe.session_published(published, frame, rows, state, phase, local_health)
    if published ~= true or capture or #state.pawns < 2
        or session_publication and session_publication.map == state.map
            and session_publication.pawns == #state.pawns then return end
    local expected = table.move(rows, 1, #rows, 1, {})
    status_publication.restore_session_rows(expected, state, phase, local_health)
    session_publication = {map = state.map, pawns = #state.pawns, bytes = type(frame) == "string" and #frame or 0,
        matched = frame == table.concat(expected, "\n"), sequence = sequence}
end

local function count_status_bytes(active)
    local rows = active.status_sample
    if not rows then return end
    last_status_sample, active.status_sample = rows, nil
    local sizes = {}
    for _, row in ipairs(rows) do
        for line in row:gmatch("[^\n]+") do
            local tag = line:match("^[^\t]+")
            if tag then sizes[tag] = (sizes[tag] or 0) + #line + 1 end
        end
    end
    local sum = 0
    for _, size in pairs(sizes) do sum = sum + size end
    active.max_status_bytes = math.max(active.max_status_bytes, sum)
    active.status_sizes = sizes
end

function probe.command(fields, periodic, publishes_snapshot)
    if not capture then return end
    local action = fields and fields[2] or "scheduled"
    local counts = publishes_snapshot and capture.snapshot_sources or capture.tick_sources
    local key = (periodic and "due/" or "between/") .. action
    counts[key] = (counts[key] or 0) + 1
end

function probe.scheduler_inspection(fields, native, fast)
    local active = scheduler_probe
    if active and fields[4] == active.crate then
        local take = fast and (native and "native-fast" or "lua-fast") or "maintenance"
        active.inspections[#active.inspections + 1] = ("%s,%d,%s"):format(fields[1], stamp(), take)
    end
end

function probe.stock_receipt(action, bytes)
    local report = stock_receipts[action] or {count = 0, max = 0}
    report.count = report.count + 1
    report.max = math.max(report.max, bytes)
    stock_receipts[action] = report
end

function probe.status_mark(tag, rows, bytes)
    local active = capture
    if not active then return end
    local heap = collectgarbage("count")
    local now, cycles = stamp()
    if tag then
        local wall, used_cycles = (now - active.status_at) / 1000, cycles - active.status_cycles
        local delta = heap - active.status_heap
        record(active, tag, wall, used_cycles)
        local longest = active.status_peaks[tag]
        if not longest or wall > longest.wall then
            active.status_peaks[tag] = {wall = wall, cycles = used_cycles, heap_delta = delta}
        end
        local call = active.calls[tag]
        if call then
            call.wall, call.cycles = call.wall + wall, call.cycles + used_cycles
        else
            active.calls[tag] = {wall = wall, cycles = used_cycles}
        end
        if rows then
            active.max_status_rows = math.max(active.max_status_rows, rows)
            active.max_status_bytes = math.max(active.max_status_bytes, bytes or 0)
        end
    end
    active.status_at, active.status_cycles, active.status_heap = now, cycles, heap
end

local function percentiles(samples)
    table.sort(samples)
    local count, sum = #samples, 0
    for i = 1, count do sum = sum + samples[i] end
    return samples[math.ceil(count * .5)], samples[math.ceil(count * .95)],
        samples[math.ceil(count * .99)], samples[count], sum
end

local function result()
    count_status_bytes(complete)
    local rows = {}
    for tag, samples in pairs(complete.rows) do
        local p50, p95, p99, peak, total = percentiles(samples.wall)
        rows[#rows + 1] = (";%s,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%d,%d,%d,%d,%d"):format(tag, #samples.wall, p50, p95,
            p99, peak, total, percentiles(samples.cycles))
    end
    table.sort(rows)
    local peak = {("!%.4f,%d,%.3f"):format(complete.peak, complete.peak_cycles, complete.peak_heap_delta)}
    for tag, call in pairs(complete.peak_calls) do
        if call.wall >= .5 then
            peak[#peak + 1] = (";%s,%.4f,%d"):format(tag, call.wall, call.cycles)
        end
    end
    local status = {("^%d,%d,%.3f,%.3f,%.3f,%d,%d,%d,%d,%d,%d,%d"):format(
        complete.max_status_rows, complete.max_status_bytes, complete.heap_start, complete.heap_peak,
        complete.heap_max_drop, complete.heap_large_drops, complete.previous_pause or 0,
        complete.previous_step_multiplier or 0, complete.restored_pause or 0, complete.restored_step_multiplier or 0,
        complete.skipped_observers, complete.skipped_status)}
    if complete.measure_gc then
        status[1] = status[1] .. (",%d,%d,%d,%d,%d"):format(
            complete.gc_started_running and 1 or 0, complete.gc_running_samples,
            complete.gc_stopped_samples, complete.gc_restored_running and 1 or 0,
            complete.gc_heap_limit_reached and 1 or 0)
    end
    for tag, sample in pairs(complete.status_peaks) do
        status[#status + 1] = (";%s,%.4f,%d,%.3f"):format(tag, sample.wall, sample.cycles, sample.heap_delta)
    end
    local memory = {}
    for tag, sample in pairs(complete.heap_calls) do
        memory[#memory + 1] = (";%s,%d,%d,%.3f,%.3f"):format(
            tag, sample.samples, sample.drops, sample.retained, sample.peak)
    end
    local sizes = {}
    for tag, size in pairs(complete.status_sizes or {}) do
        sizes[#sizes + 1] = (";%s,%d"):format(tag, size)
    end
    local commands = {}
    for key, count in pairs(complete.tick_sources) do
        commands[#commands + 1] = (";%s,%d,%d"):format(key, count, complete.snapshot_sources[key] or 0)
    end
    local intervals = {}
    for _, interval in ipairs(complete.long_intervals) do
        intervals[#intervals + 1] = (";%.4f,%d,%.3f,%.4f,%.4f,%.4f"):format(
            interval.elapsed, interval.cycles, interval.heap_delta,
            interval.tick, interval.observers, interval.status)
    end
    return ("%.5f"):format(complete.elapsed) .. table.concat(rows) .. table.concat(peak) .. table.concat(status)
        .. "|" .. table.concat(memory) .. "|" .. table.concat(sizes) .. "|" .. table.concat(commands)
        .. "|" .. table.concat(intervals) .. "|" .. (complete.engine_tick or "")
end

local function compare_switch_reads()
    local native, read_latch = SilverfishReadNativeSwitchPower, SilverfishReadNativeDoorLatch
    if type(native) ~= "function" then return "unavailable" end
    local state = inspect()
    local seat = phase(state)
    assert(seat == "hosting" or seat == "connected")
    local switched, streamed, candle_samples, candle_requests, first = 0, 0, 0, 0, nil
    local latch_samples, first_latch = 0, nil
    if type(read_latch) == "function" then
        SilverfishReadNativeDoorLatch = function(world, address, path, door, dial, known)
            local locked, pitch, yaw, roll, name
            if known then locked, pitch, yaw, roll, name = read_latch(world, address, path, door, dial, known)
            else locked, pitch, yaw, roll, name = read_latch(world, address, path, door, dial) end
            local latch = StaticFindObject(path)
            local rotation = latch.SM_Prop_Kitchen_Grill_01_Dial_01.RelativeRotation
            assert(locked == latch.Door.Locked and (name or known) == latch.Door:GetFullName() and pitch == rotation.Pitch
                and yaw == rotation.Yaw and roll == rotation.Roll, "native latch sample did not match stock")
            latch_samples = latch_samples + 1
            first_latch = first_latch or {world, address, path, door, dial}
            return locked, pitch, yaw, roll, name
        end
    end
    SilverfishReadNativeSwitchPower = function(world_address, actor, path, field, lantern, candle_overlap)
        local powered, broken, overlap = native(world_address, actor, path, field, lantern, candle_overlap == true)
        if powered ~= nil then
            switched = switched + 1
            if path:match("^([^%.]+)%.") ~= state.map then streamed = streamed + 1 end
            first = first or {world_address, actor, path, field, lantern}
            if candle_overlap then
                candle_requests = candle_requests + 1
                if overlap ~= nil then
                    local device = StaticFindObject(path)
                    assert(device.Sphere:GetGenerateOverlapEvents() == overlap,
                        "native candle overlap differed from the stock component")
                    candle_samples = candle_samples + 1
                end
            end
        end
        return powered, broken, overlap
    end
    local read = native_stock_switch.rows
    local function sorted_rows()
        local rows = read(state, seat, status_row)
        table.sort(rows)
        return table.concat(rows, "\n")
    end
    local function trial(reader, latch)
        SilverfishReadNativeSwitchPower = reader
        SilverfishReadNativeDoorLatch = latch
        local heap, start = collectgarbage("count"), stamp()
        for _ = 1, 32 do read(state, seat, status_row) end
        return (stamp() - start) / 1000, collectgarbage("count") - heap
    end
    local function with_candle_overlap(enabled)
        return function(world, actor, path, field, lantern, requested)
            return native(world, actor, path, field, lantern, enabled and requested == true)
        end
    end
    local running = collectgarbage("isrunning")
    local ok, result = pcall(function()
        local fast = sorted_rows()
        SilverfishReadNativeSwitchPower, SilverfishReadNativeDoorLatch = nil, nil
        assert(switched > 0 and fast == sorted_rows(), "native switch status did not match Lua")
        assert(candle_requests == 0 or candle_samples > 0, "no guest candle overlap was read natively")
        assert(native(0, first[2], first[3], first[4], first[5]) == nil, "native switch accepted a different world")
        assert(native(first[1], 1, first[3], first[4], first[5]) == nil, "native switch accepted a different actor")
        assert(native(first[1], first[2], first[3], true, first[5]) == nil, "native switch accepted a non-string field")
        if type(read_latch) == "function" and state.map:match("/CrossRoads_P$") then
            assert(latch_samples > 0, "no Crossroads latch was read natively")
        end
        if first_latch then
            local a = first_latch
            assert(read_latch(a[1] + 1, a[2], a[3], a[4], a[5]) == nil
                and read_latch(a[1], a[2], a[3] .. "_missing", a[4], a[5]) == nil
                and read_latch(a[1], a[2], a[3], a[4] + 1, a[5]) == nil
                and read_latch(a[1], a[2], a[3], a[4], a[5] + 1) == nil, "native latch accepted another scope")
        end
        collectgarbage("stop")
        local enabled = candle_samples > 0 and with_candle_overlap(true) or native
        local native_ms, native_kib = trial(enabled, read_latch)
        local lua_ms, lua_kib = trial(nil)
        local candle_control_ms, candle_control_kib = 0, 0
        if candle_samples > 0 then
            candle_control_ms, candle_control_kib = trial(with_candle_overlap(false), read_latch)
        end
        local latch_control_ms, latch_control_kib = 0, 0
        if latch_samples > 0 then latch_control_ms, latch_control_kib = trial(enabled) end
        return ("%d,%d,%.3f,%.3f,%.3f,%.3f,%d,%.3f,%.3f,%d,%.3f,%.3f"):format(switched, streamed, native_ms,
            lua_ms, native_kib, lua_kib, candle_samples, candle_control_ms, candle_control_kib,
            latch_samples, latch_control_ms, latch_control_kib)
    end)
    SilverfishReadNativeSwitchPower, SilverfishReadNativeDoorLatch = native, read_latch
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    local compare_latch = SilverfishProfileStockLatchRows
    local cached_latch = compare_latch and compare_latch(state, seat, status_row)
    return cached_latch and result .. ";" .. cached_latch or result
end

local function compare_acid_reads(dynamic)
    local reader = dynamic and "SilverfishReadNativeDynamicAcidSample" or "SilverfishReadNativePlacedAcidPhases"
    local native = _G[reader]
    if type(native) ~= "function" then return "unavailable" end
    local pools = require("native_acid_pools")
    local state = inspect()
    assert(phase(state) == "hosting")

    local function read()
        return assert(pools.rows(state, "hosting", status_row), "no placed acid pool row")
    end

    local function measure(operation)
        _G[reader] = operation
        local heap, start = collectgarbage("count"), stamp()
        for _ = 1, 16 do read() end
        return (stamp() - start) / 1000, collectgarbage("count") - heap
    end

    local running = collectgarbage("isrunning")
    local ok, result = pcall(function()
        local pool_count, native_count, first_address, second_argument = 0, 0
        _G[reader] = function(first, second)
            first_address, second_argument = first, second
            local sample, pose = native(first, second)
            if dynamic then
                pool_count = pool_count + 1
                if type(sample) == "string" and pose then native_count = native_count + 1 end
            elseif sample then
                pool_count = #sample
                local _, phases = sample:gsub("[afe]", "")
                native_count = phases
            end
            return sample, pose
        end
        local native_row = pools.rows(state, "hosting", status_row)
        if not native_row or not first_address then return "unavailable" end
        assert(native_count > 0, "no acid samples read natively")
        if dynamic then
            assert(native(first_address, second_argument + 1) == nil, "native acid sample accepted another world")
        else
            assert(native(first_address + 1, second_argument) == nil, "native acid phases accepted another world")
        end
        _G[reader] = nil
        assert(read() == native_row, "acid pool status did not match Lua")

        collectgarbage("stop")
        local native_first, native_first_kib = measure(native)
        local lua_first, lua_first_kib = measure(nil)
        local lua_second, lua_second_kib = measure(nil)
        local native_second, native_second_kib = measure(native)
        return ("%d,%d,%.3f,%.3f,%.3f,%.3f"):format(pool_count, native_count,
            native_first + native_second, lua_first + lua_second,
            native_first_kib + native_second_kib, lua_first_kib + lua_second_kib)
    end)
    _G[reader] = native
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end

-- Equal alternating blocks avoid attributing warmup to one path.
local function compare_paths(count, native, stock)
    local function measure(reader)
        local heap, started = collectgarbage("count"), stamp()
        for _ = 1, count // 2 do reader() end
        return (stamp() - started) / 1000, collectgarbage("count") - heap
    end
    local running = collectgarbage("isrunning")
    collectgarbage("stop")
    local ok, native_ms, lua_ms, native_kib, lua_kib = pcall(function()
        local n1, nk1 = measure(native)
        local s1, sk1 = measure(stock)
        local s2, sk2 = measure(stock)
        local n2, nk2 = measure(native)
        return n1 + n2, s1 + s2, nk1 + nk2, sk1 + sk2
    end)
    if running then collectgarbage("restart") end
    if not ok then error(native_ms, 0) end
    return native_ms, lua_ms, native_kib, lua_kib
end

function probe.native_phase_update(update, action, map, port)
    local owner, expected, listener, departing = update(action, map, port)
    if owner and (action == "join" or action == "title_idle") then native_ownership.connected = false end
    return owner, expected, listener, departing
end

function probe.native_phase_observe(state, prior_role, prior_map, prior_leaving, owner, phase, departing)
    local expected_role, expected_phase, expected_leaving = prior_role, nil, prior_leaving
    if prior_leaving then
        if state.controller and state.map == main_menu and not state.listening then
            expected_role, expected_leaving, native_ownership.connected = "idle", false, false
        else
            expected_phase = "leaving"
        end
    end
    if not expected_phase then
        if expected_role == "host" then
            expected_phase = state.map == prior_map and state.listening and not state.server
                and state.local_pawn and "hosting" or "opening"
        elseif expected_role == "client" then
            if state.server and state.local_pawn and state.map ~= main_menu then
                native_ownership.connected, expected_phase = true, "connected"
            else
                expected_phase = native_ownership.connected and "disconnected" or "joining"
            end
        else
            expected_phase = valid(state.controller) and "ready" or "opening"
        end
    end
    if owner ~= expected_role or phase ~= expected_phase or departing ~= expected_leaving then
        native_ownership.failure = native_ownership.failure or
            ("%s session %s/%s/%s differs from stock %s/%s/%s"):format(state.map,
                owner, phase, tostring(departing), expected_role, expected_phase, tostring(expected_leaving))
    end
    native_ownership.map, native_ownership.mode, native_ownership.phase = state.map, owner, phase
end

function probe.native_phase_row(state, phase, row)
    if capture or native_ownership.map ~= state.map or native_ownership.mode ~= role
        or native_ownership.phase ~= phase then return end
    local kind = row == -1 and "marker" or "literal"
    local witnessed = native_ownership.rows[kind]
    if witnessed.map == state.map and witnessed.phase == phase and witnessed.mode == role then
        witnessed.sequence = sequence
        return
    end
    if kind == "literal" and row ~= status_publication.lua_row(
        "session", role, phase, state.map, state.clients, state.server and 1 or 0) then
        native_ownership.failure = native_ownership.failure or "ordinary native-owned session row differs from stock bytes"
    end
    witnessed.map, witnessed.mode, witnessed.phase, witnessed.sequence = state.map, role, phase, sequence
end

local function native_phase_suffix(map, owner, phase, kind)
    if not native_phase_expected then return "" end
    assert(not native_ownership.failure, native_ownership.failure)
    local witnessed = native_ownership.rows[kind]
    if witnessed.map ~= map or witnessed.mode ~= owner or witnessed.phase ~= phase then return nil end
    return (",%s,%s,%d"):format(owner, phase, witnessed.sequence)
end

local function compare_title_session(state)
    local observe = SilverfishObserveNativeSession
    if type(observe) ~= "function" then return "unavailable" end
    assert(valid(state.world) and state.map == main_menu and not state.listening and not state.native_session_token,
        "the observed state is outside the stock title")
    assert(observe(state.world, "host", state.map, true) == nil
        and observe(state.world, "client", state.map, false) == nil, "native census accepted the title world")
    local reported = native_phase_suffix(state.map, "idle", "ready", "literal")
    assert(reported ~= nil, "no ordinary native-owned ready title row was observed")
    return state.map .. reported
end

local function watch_title_session()
    if type(SilverfishObserveNativeSession) ~= "function" then return "unavailable" end
    watched_title_scope = nil
    local armed_at = sequence
    probe.session_watch = function(state)
        if state.map ~= main_menu or state.listening then return end
        local title = native_ownership.rows.literal
        if native_phase_expected and (title.map ~= state.map or title.mode ~= "idle" or title.phase ~= "ready"
            or title.sequence <= armed_at) then return end
        local ok, result = pcall(compare_title_session, state)
        watched_title_scope = ok and result or "session title comparison failed: " .. tostring(result)
        session_publication = nil
        probe.session_watch = nil
    end
    return "armed"
end

local function compare_session_census()
    local observe, format = SilverfishObserveNativeSession, SilverfishFormatNativeSessionStatusRows
    if type(observe) ~= "function" then return "unavailable" end
    assert(type(format) == "function", "missing native session frame formatter")
    local current, previous = inspect(), inspect(true)
    local seat = phase(current)
    if type(current.native_session_token) ~= "number" then
        local world, controller = previous.world, previous.controller
        local pawn = previous.local_pawn and previous.local_pawn.pawn
        local driver = valid(world) and world.NetDriver or nil
        local instance = valid(world) and world.OwningGameInstance or nil
        local function describe(call)
            local ok, value = pcall(call)
            return ok and tostring(value) or "unavailable"
        end
        local function class_name(object)
            return describe(function() return object:GetClass():GetFullName() end)
        end
        local function local_role(object)
            return describe(function() return object:GetLocalRole() end)
        end
        error(("native census declined %s (%s), controller role=%s loading=%s, pawn role=%s, "
            .. "instance=%s, driver=%s WorldMatches=%s ServerConnection=%s ClientConnections=%s"):format(
                seat, previous.map, local_role(controller), describe(function() return controller.Loading end),
                local_role(pawn), class_name(instance), class_name(driver),
                describe(function() return same_object(driver.World, world) end),
                describe(function() return valid(driver.ServerConnection) end),
                describe(function() return #driver.ClientConnections end)), 0)
    end
    assert((seat == "hosting" or seat == "connected") and not previous.native_session_token,
        "current and forced legacy inspection did not use distinct paths")
    if not session_publication or session_publication.map ~= current.map
        or session_publication.pawns ~= #current.pawns then return "awaiting-publication" end
    assert(session_publication.matched and session_publication.bytes > 0,
        "actual native memory publication and the legacy frame bytes differ")
    local ownership = native_phase_suffix(current.map, role, seat, "marker")
    if ownership == nil then return "awaiting-publication" end
    for _, key in ipairs({"map", "clients", "server", "listening", "connection_timeout"}) do
        assert(current[key] == previous[key], "session inspection differs in " .. key)
    end
    local function same_optional(a, b)
        return (not valid(a) and not valid(b)) or same_object(a, b)
    end
    assert(same_object(current.world, previous.world) and same_object(current.controller, previous.controller),
        "session world or local controller differs")
    local function compare_pawn(a, b)
        assert(a and b, "missing observed pawn")
        for _, key in ipairs({"name", "kind", "slot", "controller", "x", "y", "z", "health", "max_health"}) do
            assert(a[key] == b[key], "session pawn differs in " .. key)
        end
        assert(same_object(a.pawn, b.pawn) and same_optional(a.controller_object, b.controller_object)
            and same_optional(a.save, b.save), "session pawn, controller, or save differs")
    end
    local pawn_count = #current.pawns
    assert(pawn_count >= 2 and pawn_count == #previous.pawns and #current.guests == #previous.guests,
        "session roster shape differs")
    compare_pawn(current.local_pawn, previous.local_pawn)
    for index, details in ipairs(current.pawns) do compare_pawn(details, previous.pawns[index]) end
    for index, details in ipairs(current.guests) do compare_pawn(details, previous.guests[index]) end

    local function rows_with_health(local_health)
        local rows = {"schema\t1", -1, "fixture-literal\tunchanged"}
        local expected = {rows[1], status_publication.lua_row("session", role, seat, previous.map, previous.clients,
            previous.server and 1 or 0), rows[3]}
        for index, details in ipairs(previous.pawns) do
            if finite(details.x) and finite(details.y) and finite(details.z) then
                rows[#rows + 1] = index
                local health = details == previous.local_pawn and local_health or details.health
                expected[#expected + 1] = status_row("pawn", details.slot or details.kind, details.name, health or "-",
                    details.x, details.y, details.z, details.controller, details.max_health or "-")
            end
        end
        rows[#rows + 1], expected[#expected + 1] = "", ""
        assert(#rows == pawn_count + 4, "session row proof did not include every pawn")
        local selected_health = local_health == nil and previous.local_pawn.health or local_health
        local actual = format(rows, current.native_session_token, selected_health)
        assert(actual == table.concat(expected, "\n"), "native session and pawn status bytes differ")
        return rows, #actual
    end
    local rows, bytes = rows_with_health(nil)
    local _, override_bytes = rows_with_health(0)

    local opposite = role == "host" and "client" or "host"
    local prior_map = last_census_map
    last_census_map = current.map
    local traveled = prior_map ~= nil and prior_map ~= current.map
    assert(observe(current.world, role, traveled and prior_map or current.map .. "_missing", true) == nil,
        "native census accepted another map")
    assert(format(rows, current.native_session_token, nil) == nil, "native status reused an invalidated token")
    assert(observe(current.world, opposite, current.map, true) == nil
        and observe(current.local_pawn.pawn, role, current.map, false) == nil,
        "native census accepted another role or a pawn as its world")
    local geometry = observe(current.world, role, current.map, false)
    assert(type(geometry) == "table" and #geometry.pawns == pawn_count
        and same_object(geometry.world, previous.world)
        and geometry.connection_timeout == previous.connection_timeout, "geometry-only census lost the current visit or roster")
    for index, details in ipairs(geometry.pawns) do
        local expected = previous.pawns[index]
        assert(same_object(details.pawn, expected.pawn) and details.x == expected.x and details.y == expected.y
            and details.z == expected.z and details.health == nil and not valid(details.save),
            "geometry-only census read personal health or differed from the roster")
    end

    local count = 64
    local function fast() assert(inspect().native_session_token, "timed census fell back to Lua") end
    local function legacy() assert(not inspect(true).native_session_token, "legacy benchmark used native census") end
    local native_ms, lua_ms, native_kib, lua_kib = compare_paths(count, fast, legacy)
    return ("%s,%d,%d,%d,%d,%d,%d,%d,%.3f,%.3f,%.3f,%.3f"):format(current.map, pawn_count, count, bytes, override_bytes,
        traveled and 1 or 0, session_publication.bytes, session_publication.sequence,
        native_ms, lua_ms, native_kib, lua_kib) .. ownership
end

local function compare_kill_volume_visit()
    local read, read_stock = SilverfishReadNativeKillVolumeVisit, SilverfishReadNativeKillVolume
    if type(read) ~= "function" then return "unavailable" end
    local state = inspect()
    local current_phase = phase(state)
    assert(current_phase == "hosting" or current_phase == "connected", "missing active kill-volume seat")
    local hosting = current_phase == "hosting"
    local world, driver = state.world, state.world.NetDriver
    local world_address = world:GetAddress()
    local function stock()
        local current = world.NetDriver
        local armed, overlaps, reloads = read_stock()
        if armed and valid(current) then return current:GetAddress(), overlaps, reloads end
    end
    local function native() return read(world:GetAddress(), hosting) end
    local expected, stock_overlaps, stock_reloads = stock()
    local current, overlaps, reloads = native()
    assert(valid(driver) and current == driver:GetAddress() and current == expected
        and overlaps == stock_overlaps and reloads == stock_reloads, "kill-volume visit differs from stock")
    assert(read(world_address + 1, hosting) == nil and read(world_address, not hosting) == nil,
        "native kill-volume visit accepted another world or role")
    local count = 64
    local native_ms, lua_ms, native_kib, lua_kib = compare_paths(count, native, stock)
    return ("%d,%d,%d,%.3f,%.3f,%.3f,%.3f"):format(count, overlaps, reloads,
        native_ms, lua_ms, native_kib, lua_kib)
end

function SilverfishProfileCrossroadsClockLuaFallback(nonce)
    clock_lua_count = clock_lua_count + 1
    clock_lua_requests[(clock_lua_count - 1) % 64 + 1] = nonce
end

local function read_clock_dispatch()
    local read = SilverfishReadNativeClockDispatchProfile
    if type(read) ~= "function" then return "unavailable" end
    local accepted, refused, nonce, map, pawn, epoch, clock_sequence, status_sequence = read()
    assert(math.type(accepted) == "integer" and accepted >= 0 and math.type(refused) == "integer" and refused >= 0,
        "invalid native clock dispatch counters")
    local lua_ids = table.concat(clock_lua_requests, ",")
    if accepted == 0 then return table.concat({accepted, refused, clock_lua_count, lua_ids}, "\t") end
    assert(type(nonce) == "string" and #nonce == 32 and nonce:match("^%x+$") and type(map) == "string"
        and type(pawn) == "string" and type(epoch) == "string"
        and math.type(clock_sequence) == "integer" and math.type(status_sequence) == "integer",
        "incomplete native clock dispatch receipt")
    local seen_by_lua = false
    for _, observed in ipairs(clock_lua_requests) do
        if observed == nonce then seen_by_lua = true; break end
    end
    return table.concat({accepted, refused, clock_lua_count, lua_ids, nonce, map, pawn, epoch, clock_sequence,
        status_sequence, seen_by_lua and 1 or 0}, "\t")
end

function SilverfishProfileCrossroadsHealthLuaProjection(nonce)
    health_lua_count = health_lua_count + 1
    health_lua_requests[(health_lua_count - 1) % 128 + 1] = nonce
end

local function read_health_dispatch()
    local read = SilverfishReadNativeHealthDispatchProfile
    if type(read) ~= "function" then return "unavailable" end
    local history = read()
    assert(type(history) == "string", "invalid native health dispatch history")
    return table.concat(health_lua_requests, ",") .. "\n" .. history
end

local function probe_health_dispatch()
    local read, probe = SilverfishReadNativeHealthDispatchProfile, SilverfishProbeNativeHealthDispatch
    if type(read) ~= "function" or type(probe) ~= "function" then return "unavailable" end
    local state = inspect()
    local current_phase = phase(state)
    assert(current_phase == "connected" and state.local_pawn, "missing connected guest health scope")
    local save, pawn, token = native_save_scope.bind_guest(state, current_phase)
    assert(valid(save) and valid(pawn) and same_object(pawn, state.local_pawn.pawn), "missing isolated guest save")
    local request
    for line in read():gmatch("[^\n]+") do
        local fields = {}
        for value in (line .. "\t"):gmatch("([^\t]*)\t") do fields[#fields + 1] = value end
        if fields[1] == "native-guest-health-reply" and fields[5] == state.map and fields[8] == token
            and fields[9] == state.local_pawn.name and fields[10] == "accepted" then
            request = {fields[3], "native-guest-health-project", table.unpack(fields, 5, 9)}
            for index = 12, #fields do request[#request + 1] = fields[index] end
        end
    end
    assert(request and #request >= 9, "no native guest health receipt in the active scope")
    local health, maximum, regen = save.PlayerHealth, pawn.MaxHealth, save.PlayerRegen
    request[8] = tostring(health >= 1 and health - 1 or math.min(maximum, health + 1))
    if probe(table.concat(request, "\t") .. "\n") ~= true then
        for index = #request, 10, -1 do request[index] = nil end
        if probe(table.concat(request, "\t") .. "\n") ~= true then return "awaiting-scope" end
    end
    local count = #request
    for _, index in ipairs({4, 5, 6}) do
        local exact = request[index]
        assert(type(exact) == "string" and #exact == 32 and exact:match("^%x+$"), "invalid health scope identity")
        request[index] = exact:sub(1, 31) .. (exact:sub(32) == "0" and "1" or "0")
        assert(probe(table.concat(request, "\t") .. "\n") == false, "native health admitted a changed scope field: " .. index)
        request[index] = exact
    end
    assert(save.PlayerHealth == health and pawn.MaxHealth == maximum and save.PlayerRegen == regen,
        "native health preflight changed the stock guest")
    return tostring(count)
end

local function compare_save_game_clock()
    local read, stock = SilverfishReadNativeSavedGameSeconds, native_world_view.read_game_seconds_lua
    if type(read) ~= "function" then return "unavailable" end
    assert(type(stock) == "function", "missing legacy stock save clock")
    local state = inspect()
    local pawn = state.local_pawn and state.local_pawn.pawn
    local world, current_phase = state.world, phase(state)
    assert(valid(world) and valid(pawn), "missing active local pawn or world")
    local save, instance = pawn.Save, world.OwningGameInstance
    assert(valid(instance) and same_object(save, instance.SaveSlot_Ref), "missing active canonical save")
    local verified_sequence = 0
    if current_phase == "connected" then
        local verify = SilverfishVerifyNativeGuestClockSeat
        local row = native_guest_clock.rows(state, current_phase, table.pack)[1]
        assert(type(verify) == "function" and row and row[1] == "native-guest-clock"
            and row[2] == state.map and row[3] == state.local_pawn.name,
            "active guest clock lease unavailable")
        local connection = world.NetDriver.ServerConnection
        local w, a, c, i, n = world:GetAddress(), pawn:GetAddress(), state.controller:GetAddress(),
            instance:GetAddress(), connection:GetAddress()
        verified_sequence = row[5]
        assert(verify(w, a, c, i, n, verified_sequence) == true, "warm native clock seat was not accepted")
        assert(verify(w, a, c, i, n, 0) == nil and verify(w, a, c, i, n + 1, verified_sequence) == nil,
            "native clock seat accepted the wrong connection or sequence")
    end
    local function legacy() return stock(save) end
    local function qualified() return read(save) end
    local seconds = legacy()
    assert(type(seconds) == "number" and qualified() == seconds, "native clock differed from stock save time")
    assert(read(pawn) == nil and read(state.world) == nil and read(nil) == nil,
        "native clock accepted an object outside its save scope")
    local count = 64
    local native_ms, lua_ms, native_kib, lua_kib = compare_paths(count, qualified, legacy)
    return ("%.3f,%d,%d,%.3f,%.3f,%.3f,%.3f"):format(seconds, verified_sequence, count,
        native_ms, lua_ms, native_kib, lua_kib)
end

local function compare_pawn_values()
    local native, legacy = SilverfishReadNativeSessionPawn, status_publication.read_pawn_values_lua
    if type(native) ~= "function" or type(legacy) ~= "function" then return "unavailable" end
    local state = inspect()
    assert(valid(state.world) and state.local_pawn and #state.pawns > 0, "missing live session pawns")
    assert(native(state.local_pawn.pawn, state.local_pawn.pawn, false) == nil
        and native(state.world, state.world, false) == nil, "mismatched pawn/world scope was accepted")

    local function stock(pawn, health)
        return legacy(pawn, health)
    end
    local function qualified(pawn, health)
        return native(state.world, pawn, health)
    end
    for _, details in ipairs(state.pawns) do
        for _, health in ipairs({false, true}) do
            local expected = table.pack(stock(details.pawn, health))
            local actual = table.pack(qualified(details.pawn, health))
            assert(type(actual[1]) == "number", "one observed pawn fell back to Lua")
            for index = 1, 5 do assert(actual[index] == expected[index], "session pawn scalar differs: " .. index) end
            assert((actual[6] == nil and expected[6] == nil) or same_object(actual[6], expected[6]),
                "session pawn save differs")
        end
    end
    local function measure(reader, health)
        local heap, started = collectgarbage("count"), stamp()
        for _ = 1, 32 do
            for _, details in ipairs(state.pawns) do reader(details.pawn, health) end
        end
        return (stamp() - started) / 1000, collectgarbage("count") - heap
    end
    local function compare(health)
        local native_first, native_first_heap = measure(qualified, health)
        local lua_first, lua_first_heap = measure(stock, health)
        local lua_second, lua_second_heap = measure(stock, health)
        local native_second, native_second_heap = measure(qualified, health)
        return ("%.3f,%.3f,%.3f,%.3f"):format(native_first + native_second, lua_first + lua_second,
            native_first_heap + native_second_heap, lua_first_heap + lua_second_heap)
    end
    local running = collectgarbage("isrunning")
    collectgarbage("stop")
    local ok, result = pcall(function()
        return ("%d,%d;%s;%s"):format(#state.pawns, 64, compare(false), compare(true))
    end)
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end

local function compare_inspection()
    if type(observe_session_controllers) ~= "function" then return "unavailable" end

    local function recheck_controllers(state)
        local checked = 0
        for _, details in ipairs(state.pawns) do
            local controller = details.controller_object
            if valid(controller) then
                assert(controller_in_world(controller, state.world))
                assert(controller:IsLocalController() == (details.kind == "local"))
                assert(controller:GetFullName() == details.controller)
                checked = checked + 1
            end
        end
        return checked
    end

    local state = inspect()
    local seat = phase(state)
    assert(state.local_pawn and (seat == "hosting" or seat == "connected"))
    local checked = recheck_controllers(state)
    assert(checked > 0)
    local function measure(recheck)
        local heap, started = collectgarbage("count"), stamp()
        for _ = 1, 16 do
            local view = inspect()
            if recheck then assert(recheck_controllers(view) == checked) end
        end
        return (stamp() - started) / 1000, collectgarbage("count") - heap
    end

    local running = collectgarbage("isrunning")
    collectgarbage("stop")
    local ok, result = pcall(function()
        local fast_first, fast_kib_first = measure(false)
        local repeated_first, repeated_kib_first = measure(true)
        local repeated_second, repeated_kib_second = measure(true)
        local fast_second, fast_kib_second = measure(false)
        return ("%d,%d,%.3f,%.3f,%.3f,%.3f"):format(checked, 32, fast_first + fast_second,
            repeated_first + repeated_second, fast_kib_first + fast_kib_second, repeated_kib_first + repeated_kib_second)
    end)
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end

local function compare_rows()
    local lua_status_row = status_publication.lua_row
    local samples = {
        {"pawn", "local", "Player_FP_C /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Player_FP_C_17",
            248.75, -111143.078, 33305.762, 1325.09, "PlayerControler_FP_C PC", 250},
        {"native-loot-pickup", 1, "finite", "valid", "Item Bandage", "\tcontains\ncontrols\r\0", false, true},
        {"ack", "sample", "accepted", "merchant é", -17, math.maxinteger, -0.0, "a\127b"},
        {},
    }
    for _, fields in ipairs(samples) do
        local expected = lua_status_row(table.unpack(fields))
        assert(SilverfishFormatLocalStatusRow(table.unpack(fields)) == expected, "native row bytes differ")
        assert(status_row(table.unpack(fields)) == expected, "normal status row bytes differ")
    end
    local custom = setmetatable({}, {__tostring = function() return "custom\tvalue" end})
    assert(SilverfishFormatLocalStatusRow("row", custom) == nil and status_row("row", custom) == "row\tcustom value")
    assert(status_row("row", nil) == lua_status_row("row", nil))
    local rounds = 512
    local function measure(format)
        local started, started_cycles = stamp()
        for _ = 1, rounds do
            for _, fields in ipairs(samples) do format(table.unpack(fields)) end
        end
        local now, cycles = stamp()
        return (now - started) / 1000, cycles - started_cycles
    end
    local native_first, native_first_cycles = measure(status_row)
    local lua_first, lua_first_cycles = measure(lua_status_row)
    local lua_second, lua_second_cycles = measure(lua_status_row)
    local native_second, native_second_cycles = measure(status_row)
    return ("%d,%.3f,%.3f,%d,%d"):format(rounds * 2 * #samples, native_first + native_second,
        lua_first + lua_second, native_first_cycles + native_second_cycles, lua_first_cycles + lua_second_cycles)
end

local function compare_status_reuse()
    if type(uncached_format) ~= "function" then return "unavailable" end
    local samples = {}
    for _, row in ipairs(assert(last_status_sample)) do
        if #row > 0 then
            local fields = {}
            for field in (row .. "\t"):gmatch("([^\t]*)\t") do fields[#fields + 1] = field end
            assert(cached_format(table.unpack(fields)) == uncached_format(table.unpack(fields)))
            samples[#samples + 1] = fields
        end
    end
    local rounds = 32
    local function measure(format)
        local heap, start = collectgarbage("count"), stamp()
        for _ = 1, rounds do
            for _, fields in ipairs(samples) do format(table.unpack(fields)) end
        end
        return (stamp() - start) / 1000, collectgarbage("count") - heap
    end
    local running = collectgarbage("isrunning")
    collectgarbage("stop")
    local ok, result = pcall(function()
        local cached_first, cached_heap_first = measure(cached_format)
        local uncached_first, uncached_heap_first = measure(uncached_format)
        local uncached_second, uncached_heap_second = measure(uncached_format)
        local cached_second, cached_heap_second = measure(cached_format)
        return ("%d,%d,%.3f,%.3f,%.3f,%.3f"):format(#samples, rounds * 2,
            cached_first + cached_second, uncached_first + uncached_second,
            cached_heap_first + cached_heap_second, uncached_heap_first + uncached_heap_second)
    end)
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end

local function compare_status_numbers()
    local before = SilverfishFormatLocalStatusRowWithLuaNumbers
    if type(before) ~= "function" then return "unavailable" end
    local samples = {
        {"native-number-format", math.mininteger, math.maxinteger, -0.0, 0.0, -1.0, 1e+300, 1e-300},
        {"native-number-format", math.huge, -math.huge, 0/0, "control\t\r\n\0"},
    }
    for i = 1, 128 do
        samples[#samples + 1] = {"pawn", "status-number-fixture", i * 34.51 + .12345,
            -i * 71.92 - .98765, i * 1007, 35.007 / i, true}
    end
    for _, fields in ipairs(samples) do
        local expected = status_publication.lua_row(table.unpack(fields))
        assert(cached_format(table.unpack(fields)) == expected and before(table.unpack(fields)) == expected)
    end
    local function measure(format)
        collectgarbage("collect")
        collectgarbage("stop")
        local heap, start = collectgarbage("count"), stamp()
        for _, fields in ipairs(samples) do format(table.unpack(fields)) end
        return (stamp() - start) / 1000, collectgarbage("count") - heap
    end
    local running = collectgarbage("isrunning")
    local ok, result = pcall(function()
        local now_ms, now_kib = measure(cached_format)
        local before_ms, before_kib = measure(before)
        return ("%d,%.3f,%.3f,%.3f,%.3f"):format(#samples, now_ms, before_ms, now_kib, before_kib)
    end)
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end

local function retain_long_interval(active, elapsed, cycles, heap_delta)
    local entries = active.long_intervals
    local index = #entries + 1
    while index > 1 and elapsed > entries[index - 1].elapsed do index = index - 1 end
    if index > long_interval_limit then return end
    local calls = active.calls
    table.insert(entries, index, {
        elapsed = elapsed, cycles = cycles, heap_delta = heap_delta,
        tick = calls.tick and calls.tick.wall or 0,
        observers = calls.delayed_observers and calls.delayed_observers.wall or 0,
        status = calls.write_status and calls.write_status.wall or 0,
    })
    entries[long_interval_limit + 1] = nil
end

local function restore_automatic_collection(active)
    if active and active.gc_suspended then
        collectgarbage("restart")
        active.gc_suspended = false
    end
    return collectgarbage("isrunning")
end

LoopInGameThreadWithDelay(1, function()
    local active = capture
    if not active then return end
    if active.native_tick_started and type(SilverfishWitnessEngineTickProfile) == "function" then
        SilverfishWitnessEngineTickProfile()
    end
    local now, cycles = stamp()
    local heap = collectgarbage("count")
    if active.measure_gc then
        if collectgarbage("isrunning") then
            active.gc_running_samples = active.gc_running_samples + 1
        else
            active.gc_stopped_samples = active.gc_stopped_samples + 1
        end
        active.gc_heap_limit_reached = active.gc_suspended and heap - active.heap_start >= gc_suspended_growth_limit_kib
    end
    active.heap_peak = math.max(active.heap_peak, heap)
    local collected = active.heap_previous - heap
    active.heap_max_drop = math.max(active.heap_max_drop, collected)
    if collected >= 1024 then active.heap_large_drops = active.heap_large_drops + 1 end
    active.heap_previous = heap
    if previous then
        local duration, used_cycles = (now - previous) / 1000, cycles - previous_cycles
        record(active, "frame", duration, used_cycles)
        if duration > 1000 / 60 then
            record(active, "frame_over_16ms", duration, used_cycles)
            if duration > 1000 / 30 then record(active, "frame_over_33ms", duration, used_cycles) end
            if duration > 50 then record(active, "frame_over_50ms", duration, used_cycles) end
            retain_long_interval(active, duration, used_cycles, -collected)
        end
        if collected >= 1024 then record(active, "frame_with_heap_drop", duration, used_cycles) end
        if duration > active.peak then
            active.peak, active.peak_cycles, active.peak_calls = duration, used_cycles, active.calls
            active.peak_heap_delta = -collected
            active.calls = {}
        else
            for tag in pairs(active.calls) do active.calls[tag] = nil end
        end
    end
    previous, previous_cycles = now, cycles
    if now >= active.deadline or active.gc_heap_limit_reached then
        if active.measure_gc then active.gc_restored_running = restore_automatic_collection(active) end
        if active.native_tick_started then
            active.engine_tick = table.concat({SilverfishFinishEngineTickProfile()}, ",")
        end
        active.elapsed = (now - active.started) / 1e6
        status_publication.mirror = active.original_mirror
        if active.previous_pause then
            active.restored_pause = collectgarbage("setpause", active.previous_pause)
            active.restored_step_multiplier = collectgarbage("setstepmul", active.previous_step_multiplier)
        end
        capture, complete = nil, active
    end
end)

LoopInGameThreadWithDelay(50, function()
    local input = io.open(prefix .. "command.tsv", "rb")
    if not input then return end
    local text = input:read(64)
    input:close()
    if not text or text == last then return end
    last = text
    local ticket, action = text:match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 then return end
    local message
    local measure_gc = action == "gcrunning" or action == "gcstopped"
    if action == "gcrestore" then
        message = restore_automatic_collection(capture) and "running" or "stopped"
    elseif measure_gc and not collectgarbage("isrunning") then
        message = "collector was already stopped"
    elseif action == "coarseraw" and type(status_publication.get_observer_world) ~= "function" then
        message = "unavailable"
    elseif (action == "arm" or action == "coarse" or action == "coarseraw" or action == "coarsequiet"
        or action == "quietobservers" or action == "quietstatus"
        or action == "gcbaseline" or action == "gctuned" or action == "gcmode" or measure_gc) and not capture then
        local now, cycles
        if type(stamp) == "function" then now, cycles = stamp() end
        if type(now) == "number" and type(cycles) == "number" then
            local coarse = action ~= "arm"
            local gc_trial = action == "gcbaseline" or action == "gctuned" or action == "gcmode" or measure_gc
            local heap = collectgarbage("count")
            capture, previous, previous_cycles, complete = {
                rows = {}, calls = {}, peak = 0, peak_calls = {}, started = now,
                coarse = coarse, deadline = now + (gc_trial and gc_window_us or coarse and coarse_window_us or window_us),
                uncached_world = action == "coarseraw",
                suspend_observers = role == "host" and (action == "coarsequiet" or action == "quietobservers"),
                suspend_status = role == "host" and (action == "coarsequiet" or action == "quietstatus"),
                skipped_observers = 0, skipped_status = 0,
                long_intervals = {},
                status_peaks = {}, heap_calls = {}, tick_sources = {}, snapshot_sources = {},
                max_status_rows = 0, max_status_bytes = 0,
                heap_start = heap, heap_previous = heap, heap_peak = heap, heap_max_drop = 0, heap_large_drops = 0,
                measure_gc = measure_gc, gc_started_running = measure_gc,
                gc_running_samples = 0, gc_stopped_samples = 0,
                original_mirror = status_publication.mirror,
            }, nil, nil, nil
            if action == "gctuned" then
                capture.previous_pause = collectgarbage("setpause", gc_pause)
                capture.previous_step_multiplier = collectgarbage("setstepmul", gc_step_multiplier)
            end
            capture.native_tick_started = type(SilverfishBeginEngineTickProfile) == "function"
                and SilverfishBeginEngineTickProfile()
            -- Keep the private launch flags, and time the installed memory-only gameplay path.
            status_publication.mirror = false
            if action == "gcstopped" and role == "host" then
                capture.gc_suspended = true
                collectgarbage("stop")
            end
            message = "armed"
        else
            message = "native game-thread clock unavailable"
        end
    elseif (action == "lowfps" or action == "uncap" or action == "shot") and not capture then
        local world = current_world()
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local controller = valid(world) and valid(statics) and statics:GetPlayerController(world, 0)
        if controller and valid(controller) and controller:IsLocalController() and valid(system) then
            local command = action == "shot" and "Shot" or action == "lowfps" and "t.MaxFPS 10" or "t.MaxFPS 0"
            system:ExecuteConsoleCommand(world, command, controller)
            message = action
        end
    elseif action == "gcstate" and not capture then
        local original = status_publication.original_collection
        local expected = original and "generational" or "incremental"
        local actual = collectgarbage(expected)
        if actual ~= expected then collectgarbage(actual) end
        message = (original or "unscoped") .. "," .. actual
    elseif action == "objectpath" and not capture then
        local lookup, world = SilverfishFindObjectByPath, current_world()
        if type(lookup) ~= "function" then
            message = "unavailable"
        elseif valid(world) then
            local path = world:GetFullName():match("^World (/.+)$")
            local found = path and lookup(path)
            local absent = path and lookup(path .. ":SilverfishMissingObjectFixture")
            local package = lookup("/Script/Engine")
            if valid(found) and found:GetAddress() == world:GetAddress() and valid(package)
                and absent ~= nil and not absent:IsValid() then
                message = "matched world and package, returned an invalid object for the missing path"
            end
        end
    elseif (action == "gcminordefault" or action == "gcminorsmall") and not capture then
        local minor = action == "gcminorsmall" and 8 or 20
        local old = collectgarbage("generational", minor, 100)
        message = ("%s,%d,100"):format(old, minor)
    elseif (action == "incremental" or action == "generational") and not capture then
        local started, before = stamp(), collectgarbage("count")
        local old = collectgarbage(action)
        local done, after = stamp(), collectgarbage("count")
        message = ("%s,%.3f,%.3f,%.3f"):format(old, (done - started) / 1000, before, after)
    elseif action == "read" then
        message = complete and result() or "sampling"
    elseif action == "vitals" and not capture then
        local ok, value = pcall(SilverfishProfileCrossroadsVitals)
        if ok then message = value else message = "vitals comparison failed: " .. tostring(value) end
    elseif action == "guestbinding" and not capture then
        if type(SilverfishProfileCrossroadsGuestBinding) == "function" then
            local ok, value = pcall(SilverfishProfileCrossroadsGuestBinding)
            if ok then message = value else message = "guest binding comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "switchreader" and not capture then
        local ok, value = pcall(compare_switch_reads)
        if ok then message = value else message = "switch comparison failed: " .. tostring(value) end
    elseif (action == "acidphasereader" or action == "acidposereader") and not capture then
        local ok, value = pcall(compare_acid_reads, action == "acidposereader")
        if ok then message = value else message = "acid reader comparison failed: " .. tostring(value) end
    elseif action == "readyroster" and not capture then
        if type(SilverfishProfileReadyRoster) == "function" then
            local ok, value = pcall(SilverfishProfileReadyRoster)
            if ok then message = value else message = "ready roster comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "sessiontitle" and not capture then
        local ok, value = pcall(function() return compare_title_session(inspect()) end)
        if ok then message = value else message = "session title comparison failed: " .. tostring(value) end
    elseif action == "watchsessiontitle" and not capture then
        message = watch_title_session()
    elseif action == "sessiontitleresult" and not capture then
        message = watched_title_scope or "unobserved"
    elseif action == "sessioncensus" and not capture then
        local ok, value = pcall(compare_session_census)
        if ok then message = value else message = "session census comparison failed: " .. tostring(value) end
    elseif action == "pawnvalues" and not capture then
        local ok, value = pcall(compare_pawn_values)
        if ok then message = value else message = "pawn scalar comparison failed: " .. tostring(value) end
    elseif action == "inspectcompare" and not capture then
        local ok, value = pcall(compare_inspection)
        if ok then message = value else message = "inspection comparison failed: " .. tostring(value) end
    elseif action == "exitposition" and not capture then
        if type(SilverfishProfileCrossroadsExitPosition) == "function" then
            local ok, value = pcall(SilverfishProfileCrossroadsExitPosition)
            if ok then message = value else message = "exit position comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "wallsources" and not capture then
        local ok, value = pcall(function()
            local state = inspect()
            return SilverfishProfileCrossroadsWallSources(state.world, state.map)
        end)
        if ok then message = value else message = "zone wall comparison failed: " .. tostring(value) end
    elseif action == "nearbypickupscope" and not capture then
        if type(SilverfishProfileCrossroadsNearbyPickupScope) == "function" then
            local ok, reply = pcall(function() return SilverfishProfileCrossroadsNearbyPickupScope(inspect()) end)
            if ok then message = reply else message = "nearby pickup scope failed: " .. tostring(reply) end
        else
            message = "unavailable"
        end
    elseif action == "killvolumevisit" and not capture then
        local ok, value = pcall(compare_kill_volume_visit)
        if ok then message = value else message = "kill-volume visit comparison failed: " .. tostring(value) end
    elseif action == "weatherlease" and not capture then
        if type(SilverfishProfileCrossroadsWeatherLease) == "function" then
            local ok, value = pcall(function()
                local state = inspect()
                return SilverfishProfileCrossroadsWeatherLease(state, phase(state))
            end)
            if ok then message = value else message = "weather lease comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "worldscene" and not capture then
        if type(SilverfishProfileCrossroadsWorldScene) == "function" then
            local ok, value = pcall(function()
                local state = inspect()
                return SilverfishProfileCrossroadsWorldScene(state, phase(state), status_row)
            end)
            if ok then message = value else message = "world scene comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "hoststash" and not capture then
        if type(SilverfishProfileCrossroadsHostStash) == "function" then
            local ok, value = pcall(function()
                local state = inspect()
                return SilverfishProfileCrossroadsHostStash(state, phase(state))
            end)
            if ok then message = value else message = "host stash comparison failed: " .. tostring(value) end
        else
            message = "unavailable"
        end
    elseif action == "healthdispatch" and not capture then
        local ok, value = pcall(read_health_dispatch)
        if ok then message = value else message = "health dispatch profile failed: " .. tostring(value) end
    elseif action == "healthdispatchprobe" and not capture then
        local ok, value = pcall(probe_health_dispatch)
        if ok then message = value else message = "health dispatch preflight failed: " .. tostring(value) end
    elseif action == "clockdispatch" and not capture then
        local ok, value = pcall(read_clock_dispatch)
        if ok then message = value else message = "clock dispatch profile failed: " .. tostring(value) end
    elseif action == "gameclock" and not capture then
        local ok, value = pcall(compare_save_game_clock)
        if ok then message = value else message = "stock save clock comparison failed: " .. tostring(value) end
    elseif action == "headrotation" and not capture then
        local ok, value = pcall(SilverfishProfileRemoteHeadRotation)
        if ok then message = value else message = "head rotation comparison failed: " .. tostring(value) end
    elseif action == "avatargait" and not capture then
        local ok, value = pcall(SilverfishProfileAvatarGait)
        if ok then message = value else message = "avatar gait comparison failed: " .. tostring(value) end
    elseif action == "avatargaitcounts" and not capture then
        message = SilverfishProfileAvatarGaitCounts()
    elseif action == "avatarappearance" and not capture then
        local ok, value = pcall(SilverfishProfileAvatarAppearance)
        if ok then message = value else message = "avatar appearance comparison failed: " .. tostring(value) end
    elseif action == "avatarmotion" and not capture then
        local ok, value = pcall(SilverfishProfileAvatarMotion)
        if ok then message = value else message = "avatar motion comparison failed: " .. tostring(value) end
    elseif action == "avatarclock" and not capture then
        local ok, value = pcall(SilverfishProfileAvatarClock)
        if ok then message = value else message = "avatar clock comparison failed: " .. tostring(value) end
    elseif action == "anomalysamples" and not capture then
        local ok, value = pcall(SilverfishProfileAnomalySamples)
        if ok then message = value else message = "anomaly sample comparison failed: " .. tostring(value) end
    elseif action == "electrodesamples" and not capture then
        local ok, value = pcall(SilverfishProfileElectrodeSamples)
        if ok then message = value else message = "Electrode sample comparison failed: " .. tostring(value) end
    elseif action == "rows" and not capture then
        local ok, value = pcall(compare_rows)
        if ok then message = value else message = "status rows comparison failed: " .. tostring(value) end
    elseif action == "rowreuse" and not capture then
        local ok, value = pcall(compare_status_reuse)
        if ok then message = value else message = "status row reuse comparison failed: " .. tostring(value) end
    elseif action == "rownumbers" and not capture then
        local ok, value = pcall(compare_status_numbers)
        if ok then message = value else message = "status number comparison failed: " .. tostring(value) end
    elseif (action == "rowcacheoff" or action == "rowcacheon") and not capture then
        if type(uncached_format) == "function" then
            SilverfishFormatLocalStatusRow = action == "rowcacheoff" and uncached_format or cached_format
            message = action
        else
            message = "unavailable"
        end
    elseif action == "standingmapping" and not capture then
        local ok, value = pcall(function() return SilverfishProfileStandingMapping(inspect()) end)
        if ok then message = value else message = "standing mapping comparison failed: " .. tostring(value) end
    elseif action == "lean" and not capture then
        local ok, value = pcall(SilverfishProfileLocalLean)
        if ok then message = value else message = "lean reader comparison failed: " .. tostring(value) end
    elseif action == "schedulerstart" then
        if not native_fast_expected then
            message = "legacy"
        else
            local read = assert(SilverfishReadFastLocalBridgeProfile, "native fast bridge counters unavailable")
            local state = inspect()
            scheduler_probe = {counts = {read()}, started = stamp(), inspections = {},
                crate = "LootCrate_Small_BP_C " .. state.map .. ".CrossRoads_P:PersistentLevel.LootCrate_Small_BP_C_9"}
            assert(#scheduler_probe.counts == 4, "unexpected native fast bridge counters")
            message = "armed"
        end
    elseif action == "schedulerread" then
        local active = assert(scheduler_probe, "no scheduler sample")
        local counts = {SilverfishReadFastLocalBridgeProfile()}
        for index, baseline in ipairs(active.counts) do counts[index] = counts[index] - baseline end
        message = table.concat({stamp() - active.started, table.unpack(counts)}, ",")
            .. ";" .. table.concat(active.inspections, ";")
        scheduler_probe = nil
    elseif action == "stockreceipts" and not capture then
        local summary = {}
        for command, report in pairs(stock_receipts) do
            summary[#summary + 1] = ("%s,%d,%d"):format(command, report.count, report.max)
        end
        message = table.concat(summary, ";")
    end
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    output:write(ticket, message and "\tok\t" or "\terror\t", message or "unexpected sampling request", "|end")
    output:close()
end)

SilverfishProfileCrossroadsCall = probe.call
SilverfishProfileCrossroadsCompare = compare_paths
return probe
