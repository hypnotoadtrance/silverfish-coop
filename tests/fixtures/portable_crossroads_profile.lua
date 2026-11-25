local probe = {}
local prefix = bridge .. "/crossroads-profile-"
local capture, complete, previous, previous_cycles, last
local stamp = SilverfishBridgeProfileStamp
local window_us = 3000000
local coarse_window_us = 5000000
local gc_window_us = 12000000
local gc_pause, gc_step_multiplier = 120, 160

local function record(active, tag, wall_ms, cycles)
    local rows = active.rows[tag]
    if not rows then
        rows = {wall = {}, cycles = {}}
        active.rows[tag] = rows
    end
    rows.wall[#rows.wall + 1] = wall_ms
    rows.cycles[#rows.cycles + 1] = cycles
end

local function measured(active, tag, started, started_cycles, ...)
    local now, cycles = stamp()
    local wall_ms, used_cycles = (now - started) / 1000, cycles - started_cycles
    record(active, tag, wall_ms, used_cycles)
    local call = active.calls[tag]
    if call then
        call.wall, call.cycles = call.wall + wall_ms, call.cycles + used_cycles
    else
        active.calls[tag] = {wall = wall_ms, cycles = used_cycles}
    end
    return ...
end

function probe.call(tag, operation, ...)
    if not capture or capture.coarse and tag ~= "write_status" and tag ~= "tick" then return operation(...) end
    local started, started_cycles = stamp()
    return measured(capture, tag, started, started_cycles, operation(...))
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
            active.max_status_bytes = math.max(active.max_status_bytes, bytes)
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
    local rows = {}
    for tag, samples in pairs(complete.rows) do
        local p50, p95, p99, peak, total = percentiles(samples.wall)
        rows[#rows + 1] = (";%s,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%d,%d,%d,%d,%d"):format(tag, #samples.wall, p50, p95,
            p99, peak, total, percentiles(samples.cycles))
    end
    table.sort(rows)
    local peak = {("!%.4f,%d"):format(complete.peak, complete.peak_cycles)}
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
    return ("%.5f"):format(complete.elapsed) .. table.concat(rows) .. table.concat(peak) .. table.concat(status)
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
        if duration > active.peak then
            active.peak, active.peak_cycles, active.peak_calls = duration, used_cycles, active.calls
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
    if (action == "arm" or action == "coarse" or action == "gcbaseline" or action == "gctuned") and not capture then
        local now, cycles
        if type(stamp) == "function" then now, cycles = stamp() end
        if type(now) == "number" and type(cycles) == "number" then
            local coarse = action ~= "arm"
            local gc_trial = action == "gcbaseline" or action == "gctuned"
            local heap = collectgarbage("count")
            capture, previous, previous_cycles, complete = {
                rows = {}, calls = {}, peak = 0, peak_calls = {}, started = now,
                coarse = coarse, deadline = now + (gc_trial and gc_window_us or coarse and coarse_window_us or window_us),
                status_peaks = {}, max_status_rows = 0, max_status_bytes = 0,
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
    elseif action == "read" then
        message = complete and result() or "sampling"
    elseif action == "vitals" and not capture then
        local ok, value = pcall(SilverfishProfileCrossroadsVitals)
        if ok then message = value else message = "vitals comparison failed: " .. tostring(value) end
    elseif action == "rows" and not capture then
        local ok, value = pcall(compare_rows)
        if ok then message = value else message = "status rows comparison failed: " .. tostring(value) end
    end
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    output:write(ticket, message and "\tok\t" or "\terror\t", message or "unexpected sampling request", "|end")
    output:close()
end)

SilverfishProfileCrossroadsCall = probe.call
return probe
