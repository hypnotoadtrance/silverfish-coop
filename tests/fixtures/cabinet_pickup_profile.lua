local measure = {}
local bridge = assert(os.getenv("SILVERFISH_COOP_BRIDGE"))
local nonce, deadline, previous, next_probe = nil, nil, nil, 0
local rows = {}
local target, acknowledged_ticket

local function record(label, start, stop)
    rows[#rows + 1] = {label, start, stop}
end

function measure.mark(label, identity)
    if deadline and target and identity == target then
        local now = os.clock()
        record(label, now, now)
    end
end

function measure.acknowledge(fields, result, message)
    if deadline and target and fields[2] == "native-world-loot-present-host-claim"
        and fields[4] == target and fields[1] ~= acknowledged_ticket then
        acknowledged_ticket = fields[1]
        local label = ("presentation-ack:" .. result .. ":" .. message):gsub("[\r\n\t]", " ")
        local now = os.clock()
        record(label, now, now)
    end
end

function measure.timed(label, operation, ...)
    if not deadline then return operation(...) end
    local start = os.clock()
    local result = table.pack(operation(...))
    record(label, start, os.clock())
    return table.unpack(result, 1, result.n)
end

LoopInGameThreadWithDelay(1, function()
    local now = os.clock()
    if deadline then
        record("frame", previous, now)
        previous = now
        if now >= deadline then
            local output = assert(io.open(bridge .. "/cabinet-cold-" .. nonce .. ".tsv", "wb"))
            local lines = {}
            for index, row in ipairs(rows) do
                lines[index] = string.format("%s\t%.6f\t%.6f\t%.3f",
                    row[1], row[2], row[3], (row[3] - row[2]) * 1000)
            end
            output:write(table.concat(lines, "\n"), "\n")
            output:close()
            rows, deadline = {}, nil
        end
    elseif now >= next_probe then
        next_probe = now + .15
        local request = io.open(bridge .. "/measure-cabinet-cold", "rb")
        if not request then return end
        local line = request:read("*l")
        request:close()
        local id, identity
        if line then
            id, identity = line:match("^(%x+)\t(%x+)$")
            if not id then id = line end
        end
        if identity and #identity ~= 32 then return end
        if id and #id == 16 and id:match("^%x+$") and nonce ~= id then
            nonce, deadline, previous, target = id, now + 6, now, identity
            acknowledged_ticket = nil
            local armed = assert(io.open(bridge .. "/cabinet-cold-" .. nonce .. ".armed", "wb"))
            assert(armed:write(line, "\n"))
            assert(armed:close())
        end
    end
end)

return measure
