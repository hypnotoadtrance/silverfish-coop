local lease = {}
local memory_seen = false

local function fallback(path, limit)
    if memory_seen or not path then return end
    local file = io.open(path, "rb")
    if not file then return end
    local text = file:read(limit)
    file:close()
    return text
end

function lease.fresh(path)
    local attached, timestamp
    if type(SilverfishReadLocalNativeZoneHeartbeat) == "function" then
        attached, timestamp = SilverfishReadLocalNativeZoneHeartbeat()
    end
    if attached then
        memory_seen = true
    else
        local text = fallback(path, 32)
        local digits = text and text:match("^(%d+)%s*$")
        timestamp = digits and #digits <= 12 and tonumber(digits) or nil
    end
    if not timestamp then return false end
    local age = os.time() - timestamp
    return age >= 0 and age <= 2
end

function lease.roster(path)
    if type(SilverfishReadLocalNativeZoneRoster) == "function" then
        local attached, body = SilverfishReadLocalNativeZoneRoster()
        if attached then
            memory_seen = true
            return body
        end
    end
    return fallback(path, 8192)
end

return lease
