local probe = {}
local prefix = bridge .. "/crossroads-profile-"
local capture, complete, previous, last
local window = 3

local function record(rows, start, stop)
    rows[#rows + 1] = (stop - start) * 1000
end

local function measured(active, tag, started, ...)
    local rows = active.rows[tag]
    if not rows then rows = {}; active.rows[tag] = rows end
    record(rows, started, os.clock())
    return ...
end

function probe.call(tag, operation, ...)
    if not capture then return operation(...) end
    return measured(capture, tag, os.clock(), operation(...))
end

local function result()
    local rows = {}
    for tag, samples in pairs(complete.rows) do
        table.sort(samples)
        local count, sum = #samples, 0
        for i = 1, count do sum = sum + samples[i] end
        rows[#rows + 1] = (";%s,%d,%.4f,%.4f,%.4f,%.4f,%.4f"):format(tag, count,
            samples[math.ceil(count * .5)], samples[math.ceil(count * .95)], samples[math.ceil(count * .99)],
            samples[count], sum)
    end
    table.sort(rows)
    return ("%.5f"):format(complete.elapsed) .. table.concat(rows)
end

LoopInGameThreadWithDelay(1, function()
    local active = capture
    if not active then return end
    local now = os.clock()
    if previous then record(active.rows.frame, previous, now) end
    previous = now
    if now >= active.deadline then
        active.elapsed = now - active.started
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
        local now = os.clock()
        capture, previous, complete = {rows = {frame = {}}, started = now, deadline = now + window}, nil, nil
        message = "armed"
    elseif action == "read" then
        message = complete and result() or "sampling"
    end
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    output:write(ticket, message and "\tok\t" or "\terror\t", message or "unexpected sampling request")
    output:close()
end)

return probe
