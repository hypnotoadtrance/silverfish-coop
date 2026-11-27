local reference = {}
-- Native TargetPoints transfer owner references and quantized integer positions.
-- Each purpose has its own height interval outside the playable maps.
local heights = {barter = 262144, critter = 327680, crate = 393216}
reference.class = "/Script/Engine.TargetPoint"
reference.limit = 65535

local function id_at(point, purpose)
    local offset = point.Z - heights[purpose]
    local id = math.floor(offset + .5)
    if id > 0 and id <= reference.limit and math.abs(offset - id) < .25 then return id end
end

function reference.number(marker, purpose)
    return id_at(marker:K2_GetActorLocation(), purpose)
end

function reference.is_reference(marker)
    local point = marker:K2_GetActorLocation()
    return id_at(point, "barter") ~= nil or id_at(point, "critter") ~= nil or id_at(point, "crate") ~= nil
end

function reference.spawn(world, class, purpose, owner, id)
    if id < 1 or id > reference.limit then return end
    local point = owner:K2_GetActorLocation()
    local marker = world:SpawnActor(class, {X = point.X, Y = point.Y, Z = heights[purpose] + id},
        {Pitch = 0, Yaw = 0, Roll = 0})
    if not marker or not marker:IsValid() then return end
    marker:SetOwner(owner)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker.NetUpdateFrequency = 2
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    return marker
end

return reference
