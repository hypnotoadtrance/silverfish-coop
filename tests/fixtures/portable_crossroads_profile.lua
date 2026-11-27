local probe = {}
local prefix = bridge .. "/crossroads-profile-"
local capture, complete, previous, previous_cycles, last
local stamp = SilverfishBridgeProfileStamp
local window_us = 3000000
local coarse_window_us = 5000000
local gc_window_us = 12000000
local gc_pause, gc_step_multiplier = 120, 160
local stock_receipts = {}

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

function probe.observers_begin()
    if capture then return stamp() end
end

function probe.observers_end(started, started_cycles)
    if not started or not capture then return end
    local now, cycles = stamp()
    record_call(capture, "delayed_observers", (now - started) / 1000, cycles - started_cycles)
end

function probe.keep_status_sample(rows)
    if capture and not capture.status_sample then capture.status_sample = rows end
end

local function count_status_bytes(active)
    local rows = active.status_sample
    if not rows then return end
    active.status_sample = nil
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
    local status = {("^%d,%d,%.3f,%.3f,%.3f,%d,%d,%d,%d,%d"):format(
        complete.max_status_rows, complete.max_status_bytes, complete.heap_start, complete.heap_peak,
        complete.heap_max_drop, complete.heap_large_drops, complete.previous_pause or 0,
        complete.previous_step_multiplier or 0, complete.restored_pause or 0, complete.restored_step_multiplier or 0)}
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
    return ("%.5f"):format(complete.elapsed) .. table.concat(rows) .. table.concat(peak) .. table.concat(status)
        .. "|" .. table.concat(memory) .. "|" .. table.concat(sizes) .. "|" .. table.concat(commands)
end

local function compare_switch_reads()
    local native = SilverfishReadNativeSwitchPower
    if type(native) ~= "function" then return "unavailable" end
    local state = inspect()
    local seat = phase(state)
    assert(seat == "hosting" or seat == "connected")
    local switched, first = 0, nil
    SilverfishReadNativeSwitchPower = function(...)
        local powered, broken = native(...)
        if powered ~= nil then
            switched = switched + 1
            first = first or {...}
        end
        return powered, broken
    end
    local read = native_stock_switch.rows
    local function sorted_rows()
        local rows = read(state, seat, status_row)
        table.sort(rows)
        return table.concat(rows, "\n")
    end
    local function trial(reader)
        SilverfishReadNativeSwitchPower = reader
        local heap, start = collectgarbage("count"), stamp()
        for _ = 1, 32 do read(state, seat, status_row) end
        return (stamp() - start) / 1000, collectgarbage("count") - heap
    end
    local running = collectgarbage("isrunning")
    local ok, result = pcall(function()
        local fast = sorted_rows()
        SilverfishReadNativeSwitchPower = nil
        assert(switched > 0 and fast == sorted_rows(), "native switch status did not match Lua")
        assert(native(0, first[2], first[3], first[4], first[5]) == nil, "native switch accepted a different world")
        assert(native(first[1], 1, first[3], first[4], first[5]) == nil, "native switch accepted a different actor")
        assert(native(first[1], first[2], first[3], true, first[5]) == nil, "native switch accepted a non-string field")
        collectgarbage("stop")
        local native_ms, native_kib = trial(native)
        local lua_ms, lua_kib = trial(nil)
        return ("%d,%.3f,%.3f,%.3f,%.3f"):format(switched, native_ms, lua_ms, native_kib, lua_kib)
    end)
    SilverfishReadNativeSwitchPower = native
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
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

LoopInGameThreadWithDelay(1, function()
    local active = capture
    if not active then return end
    local now, cycles = stamp()
    local heap = collectgarbage("count")
    active.heap_peak = math.max(active.heap_peak, heap)
    local collected = active.heap_previous - heap
    active.heap_max_drop = math.max(active.heap_max_drop, collected)
    if collected >= 1024 then active.heap_large_drops = active.heap_large_drops + 1 end
    active.heap_previous = heap
    if previous then
        local duration, used_cycles = (now - previous) / 1000, cycles - previous_cycles
        record(active, "frame", duration, used_cycles)
        if active.gc_mode_trial then
            if duration > 1000 / 60 then record(active, "frame_over_16ms", duration, used_cycles) end
            if duration > 1000 / 30 then record(active, "frame_over_33ms", duration, used_cycles) end
            if collected >= 1024 then record(active, "frame_with_heap_drop", duration, used_cycles) end
        end
        if duration > active.peak then
            active.peak, active.peak_cycles, active.peak_calls = duration, used_cycles, active.calls
            active.peak_heap_delta = -collected
            active.calls = {}
        else
            for tag in pairs(active.calls) do active.calls[tag] = nil end
        end
    end
    previous, previous_cycles = now, cycles
    if now >= active.deadline then
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
    if action == "coarseraw" and type(status_publication.get_observer_world) ~= "function" then
        message = "unavailable"
    elseif (action == "arm" or action == "coarse" or action == "coarseraw" or action == "gcbaseline"
        or action == "gctuned" or action == "gcmode") and not capture then
        local now, cycles
        if type(stamp) == "function" then now, cycles = stamp() end
        if type(now) == "number" and type(cycles) == "number" then
            local coarse = action ~= "arm"
            local gc_trial = action == "gcbaseline" or action == "gctuned" or action == "gcmode"
            local heap = collectgarbage("count")
            capture, previous, previous_cycles, complete = {
                rows = {}, calls = {}, peak = 0, peak_calls = {}, started = now,
                coarse = coarse, deadline = now + (gc_trial and gc_window_us or coarse and coarse_window_us or window_us),
                uncached_world = action == "coarseraw",
                gc_mode_trial = action == "gcmode",
                status_peaks = {}, heap_calls = {}, tick_sources = {}, snapshot_sources = {},
                max_status_rows = 0, max_status_bytes = 0,
                heap_start = heap, heap_previous = heap, heap_peak = heap, heap_max_drop = 0, heap_large_drops = 0,
                original_mirror = status_publication.mirror,
            }, nil, nil, nil
            if action == "gctuned" then
                capture.previous_pause = collectgarbage("setpause", gc_pause)
                capture.previous_step_multiplier = collectgarbage("setstepmul", gc_step_multiplier)
            end
            -- Keep the private launch flags, and time the installed memory-only gameplay path.
            status_publication.mirror = false
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
    elseif action == "inspectcompare" and not capture then
        local ok, value = pcall(compare_inspection)
        if ok then message = value else message = "inspection comparison failed: " .. tostring(value) end
    elseif action == "anomalysamples" and not capture then
        local ok, value = pcall(SilverfishProfileAnomalySamples)
        if ok then message = value else message = "anomaly sample comparison failed: " .. tostring(value) end
    elseif action == "rows" and not capture then
        local ok, value = pcall(compare_rows)
        if ok then message = value else message = "status rows comparison failed: " .. tostring(value) end
    elseif action == "lean" and not capture then
        local ok, value = pcall(SilverfishProfileLocalLean)
        if ok then message = value else message = "lean reader comparison failed: " .. tostring(value) end
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
return probe
