local motion = {}

local settle_seconds = .20
local position_tolerance = .5
local angle_tolerance = .75

local function finite(value, limit)
    return type(value) == "number" and value == value and math.abs(value) <= limit
end

local function angle_distance(a, b)
    return math.abs((a - b + 180) % 360 - 180)
end

-- Observe the authoritative stock actor until its position and rotation have stopped changing.
-- Game time avoids treating several status reads during a paused frame as elapsed physics time.
function motion.settled(actor, sample)
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not api or not api:IsValid() then return false end
    local now = api:GetTimeSeconds(actor)
    local at, rotation = actor:K2_GetActorLocation(), actor:K2_GetActorRotation()
    if not finite(now, 1e9) or not at or not rotation
        or not finite(at.X, 1e7) or not finite(at.Y, 1e7) or not finite(at.Z, 1e7)
        or not finite(rotation.Pitch, 360) or not finite(rotation.Yaw, 360) or not finite(rotation.Roll, 360) then return false end
    local address = actor:GetAddress()
    if sample.address ~= address then
        sample.address, sample.origin_z, sample.since = address, at.Z, nil
    end
    local position, angle = sample.position, sample.rotation
    if not sample.since or now < sample.since
        or (at.X - position.X)^2 + (at.Y - position.Y)^2 + (at.Z - position.Z)^2 > position_tolerance^2
        or angle_distance(rotation.Pitch, angle.Pitch) > angle_tolerance
        or angle_distance(rotation.Yaw, angle.Yaw) > angle_tolerance
        or angle_distance(rotation.Roll, angle.Roll) > angle_tolerance then
        sample.since, sample.position, sample.rotation = now, at, rotation
        return false
    end
    return now - sample.since >= settle_seconds
end

return motion
