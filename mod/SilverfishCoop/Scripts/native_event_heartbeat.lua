local heartbeat = {}
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"

function heartbeat.fresh(path, digit_limit)
    if not path then return false end
    local active, timestamp
    if type(SilverfishReadLocalNativeEventsHeartbeat) == "function" then
        active, timestamp = SilverfishReadLocalNativeEventsHeartbeat()
    end
    if not active then
        if embedded then return false end
        local file = io.open(path, "rb")
        if not file then return false end
        local raw = file:read(32)
        file:close()
        local digits = raw and raw:match("^(%d+)%s*$")
        if not digits or digit_limit and #digits > digit_limit then return false end
        timestamp = tonumber(digits)
    end
    if not timestamp then return false end
    local age = os.time() - timestamp
    return age >= 0 and age <= 2
end

return heartbeat
