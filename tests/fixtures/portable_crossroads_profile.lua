local probe = {}
local prefix = bridge .. "/crossroads-profile-"
local capture, complete, previous, previous_cycles, last
local stamp = SilverfishBridgeProfileStamp
local window_us = 3000000

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
    if not capture then return operation(...) end
    local started, started_cycles = stamp()
    return measured(capture, tag, started, started_cycles, operation(...))
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
    return ("%.5f"):format(complete.elapsed) .. table.concat(rows) .. table.concat(peak)
end

LoopInGameThreadWithDelay(1, function()
    local active = capture
    if not active then return end
    local now, cycles = stamp()
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
    if action == "arm" and not capture then
        local now, cycles
        if type(stamp) == "function" then now, cycles = stamp() end
        if type(now) == "number" and type(cycles) == "number" then
            capture, previous, previous_cycles, complete = {
                rows = {}, calls = {}, peak = 0, peak_calls = {}, started = now, deadline = now + window_us,
                original_mirror = status_publication.mirror,
            }, nil, nil, nil
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
    end
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    output:write(ticket, message and "\tok\t" or "\terror\t", message or "unexpected sampling request", "|end")
    output:close()
end)

SilverfishProfileCrossroadsCall = probe.call
return probe
