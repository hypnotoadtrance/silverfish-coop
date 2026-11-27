package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local pickup = require("native_authored_pickup_input_fixture")
local placements = require("authored_supply_catalog")
local profile = placements[1]
local next_address = 0

local function object(properties)
    next_address = next_address + 1
    local address = next_address
    properties.IsValid = function() return true end
    properties.GetAddress = function() return address end
    return properties
end

local world = object({})
world.PersistentLevel = object({})
local pawn = object({Dead = false, InteractionRange = 250})
function pawn:GetWorld() return world end
function pawn:HasAuthority() return true end
function pawn:K2_GetActorLocation() return {X = 0, Y = 0, Z = 0} end
local actor = object({})
function actor:GetWorld() return world end
function actor:GetOuter() return world.PersistentLevel end
function actor:IsActorBeingDestroyed() return false end
function actor:GetFullName() return profile.name .. " " .. profile.path end
function actor:HasAuthority() return true end
local camera_type, box_type = object({}), object({})
local camera = object({})
function camera:GetOwner() return pawn end
function camera:GetClass() return camera_type end
function camera:K2_GetComponentLocation() return {X = 0, Y = 0, Z = 64} end
pawn.Camera = camera
local box = object({})
function box:GetOwner() return actor end
function box:GetClass() return box_type end
function box:K2_GetComponentLocation() return {X = 100, Y = 0, Z = 64} end
function box:GetScaledBoxExtent() return {X = 5, Y = 5, Z = 5} end
function box:GetUpVector() return {X = 0, Y = 0, Z = 1} end
function box:GetRightVector() return {X = 0, Y = 1, Z = 0} end
actor.Box, actor.RootComponent = box, box

local wall = object({GetFullName = function() return "Mock wall" end})
local blocked, calls = false, {}
local trace = object({})
function trace:LineTraceSingle(standing, _, endpoint, channel, complex, ignored, _, result)
    assert(standing == pawn and ignored[1] == pawn and #ignored == 1)
    calls[#calls + 1] = channel
    if channel == 1 then
        assert(complex)
        if not blocked then return false end
        result.OutHit = {ImpactPoint = {X = 50, Y = 0, Z = 64}, Component = {Get = function() return wall end}}
        return true
    end
    assert(channel == 4 and not complex)
    local component = endpoint.X >= 90 and box or wall
    result.OutHit = {Component = {Get = function() return component end}}
    return true
end

local engine_objects = {
    ["/Script/Engine.Default__KismetSystemLibrary"] = trace,
    ["/Script/Engine.CameraComponent"] = camera_type,
    ["/Script/Engine.BoxComponent"] = box_type,
}
function StaticFindObject(name) return engine_objects[name] end

local state = {map = profile.map, world = world, listening = true, server = false}
assert(pickup.authority_sight(state, "hosting", profile, pawn, actor))
assert(#calls == 2 and calls[1] == 1 and calls[2] == 4)
blocked, calls = true, {}
assert(not pickup.authority_sight(state, "hosting", profile, pawn, actor))
assert(#calls > 2 and #calls % 2 == 0)
calls = {}
assert(not pickup.authority_sight(state, "connected", profile, pawn, actor))
assert(not pickup.authority_sight(state, "hosting", placements[2], pawn, actor))
local clone = {source = profile.source, map = profile.map, name = profile.name, path = profile.path}
assert(not pickup.authority_sight(state, "hosting", clone, pawn, actor))
assert(#calls == 0)

for _, authored in ipairs({
    {"crossroads-dead-drop-bag", "CrossRoads_P", "Bag_DeadDrop_CrossRoads_C"},
    {"metro-dead-drop-bag", "City_01_P", "Bag_DeadDrop_Metro_C"},
    {"flood-town-dead-drop-bag", "RuinFeilds_P", "Bag_DeadDrop_FloodVillage_C"},
}) do
    local source, map, name = table.unpack(authored)
    local area = "/Game/Classes/Maps/Maps_OverWorld/" .. map
    profile = {source = source, map = area, name = name, path = area .. "." .. map .. ":PersistentLevel." .. name .. "_1",
        quest_source = true, authority_sight = true}
    state.map, blocked, calls = area, false, {}
    assert(pickup.authority_sight(state, "hosting", profile, pawn, actor), source)
    assert(#calls == 2 and calls[1] == 1 and calls[2] == 4, source)
    blocked = true
    assert(not pickup.authority_sight(state, "hosting", profile, pawn, actor), source)
    state.map, blocked, calls = "wrong map", false, {}
    assert(not pickup.authority_sight(state, "hosting", profile, pawn, actor) and #calls == 0, source)
end
print("Stock supply and all three Dead Drops passed exact scope, stock sight and wall refusal.")
