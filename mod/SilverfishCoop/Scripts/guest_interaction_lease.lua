local lease = {shop = 1, broker = 2, dialogue = 4, mosby = 8}
local memory_seen = false

function lease.timestamp(path, feature, area, pawn, now, digits_only)
    local active, stamp, features
    if type(SilverfishReadLocalGuestInteractionLease) == "function" then
        active, stamp, features = SilverfishReadLocalGuestInteractionLease(area, pawn)
    end
    if active then
        memory_seen = true
        if not features or features & feature == 0 then return end
    else
        -- A detached resident worker cannot revive an old diagnostic file.
        if memory_seen then return end
        local file = io.open(path, "rb")
        if not file then return end
        local raw = file:read(32)
        file:close()
        if digits_only then
            local digits = raw and raw:match("^(%d+)%s*$")
            stamp = digits and #digits <= 12 and tonumber(digits)
        else
            stamp = tonumber(raw)
        end
    end
    if stamp and stamp <= now and now <= stamp + 2 then return stamp end
end

return lease
