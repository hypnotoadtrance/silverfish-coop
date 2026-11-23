local measure = {}
local bridge = assert(os.getenv("SILVERFISH_COOP_BRIDGE"))
local nonce, deadline, previous, next_probe = nil, nil, nil, 0
local rows = {}

local function record(label, start, stop)
    rows[#rows + 1] = {label, start, stop}
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
        local id = request:read("*l")
        request:close()
        if id and #id == 16 and id:match("^%x+$") and nonce ~= id then
            nonce, deadline, previous = id, now + 6, now
        end
    end
end)

return measure
