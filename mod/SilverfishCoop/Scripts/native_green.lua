local green = {}
local bed = require("native_bed")
local transport = require("native_transport")

local safe_zones = "/Game/Classes/Maps/Maps_SafeZones/"
local overworld = "/Game/Classes/Maps/Maps_OverWorld/"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"

-- Exact cooked SafeLevels order followed by Level_RepStats order.
local candidates = {
    { map = safe_zones .. "Shop_ScavBar_P", bed = "SavePoint_BP_C_1", faction = 0, min_safe_rep = 300 },
    { map = safe_zones .. "Shop_ShopKeep_P", start = "PlayerStart_1" },
    { map = safe_zones .. "Borough", bed = "SavePoint_BP_C_1", faction = 2, min_safe_rep = 0,
        alternate = "LevelExit_Spawn_BP_C_1" },
    { map = safe_zones .. "Shop_BlackMarket_01", start = "PlayerStart_1", faction = 8 },
    { map = overworld .. "Observitory_P", bed = "SavePoint_BP_C_3", faction = 8, min_safe_rep = -90,
        alternate = "LevelExit_Spawn_BP_C_1" },
    { map = safe_zones .. "Shop_Scientist_01", start = "PlayerStart_2", faction = 2 },
    { map = overworld .. "DeadBridge_P", bed = "SavePoint_BP_C_1", faction = 4, min_safe_rep = 300,
        alternate = "LevelExit_Spawn_BP_C_4" },
    { map = overworld .. "Forest_01_P", start = "PlayerStart_2", faction = 5 },
}

local profiles = {}
for _, candidate in ipairs(candidates) do profiles[candidate.map] = candidate end

function green.supports(map)
    return profiles[map] ~= nil
end

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function stock_instance(state, phase, require_fresh)
    local host, fresh = transport.scoped_host(state, phase)
    local world = state.world
    local basename = state.map and state.map:match("/([^/]+)$")
    if not host or (require_fresh and not fresh) or not valid(world) or not basename
        or world:GetFullName() ~= "World " .. state.map .. "." .. basename
        or not host.pawn:GetFullName():match("^Player_FP_C ") then
        error("The active authoritative Green Cube host is unavailable", 0)
    end

    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not same(host.pawn.Save, save) or not save:GetFullName():match("^SaveGame_BP_C ") then
        error("The Green Cube requires the canonical native host save", 0)
    end
    return host, world, instance
end

local function faction_reputation(instance, faction)
    local out = {}
    instance:Find_PlayerReputation(faction, out, out, out)
    local relationship, points = out.Relationship, out.Points
    if type(relationship) ~= "number" or not math.tointeger(relationship) or relationship < 0 or relationship > 6
        or type(points) ~= "number" or not math.tointeger(points) or points < -2147483648 or points > 2147483647 then
        error("The canonical host's stock Green faction reputation is unavailable", 0)
    end
    return { relationship = relationship, points = points }
end

function green.source(state, phase)
    local _, _, instance = stock_instance(state, phase, true)
    local reputation = {}
    for _, faction in ipairs({ 2, 8, 4, 5 }) do reputation[faction] = faction_reputation(instance, faction) end
    local destinations = {}
    for _, candidate in ipairs(candidates) do
        local rule = reputation[candidate.faction]
        if not rule or rule.relationship <= 2 then
            destinations[#destinations + 1] = candidate.map
        end
    end
    return destinations, reputation
end

local function actor_name(map, class, name)
    return class .. " " .. map .. "." .. map:match("/([^/]+)$") .. ":PersistentLevel." .. name
end

local function lone_placed_actor(world, map, class, name)
    local expected = name and actor_name(map, class, name)
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if match or actor:GetFullName() ~= expected or not actor:HasAuthority() then
                error("The exact authored Green Cube " .. class .. " is ambiguous or unqualified", 0)
            end
            match = actor
        end
    end
    if name and not match then error("The authored Green Cube " .. class .. " is unavailable", 0) end
    return match
end

local function arrival_transform(world, instance, profile)
    local rest = lone_placed_actor(world, profile.map, "SavePoint_BP_C", profile.bed)
    if not rest then
        local start = lone_placed_actor(world, profile.map, "PlayerStart", profile.start)
        return start, start:K2_GetActorLocation(), start:K2_GetActorRotation(), "start"
    end
    local arrow = rest.Arrow
    if not valid(arrow) or not same(arrow:GetOwner(), rest)
        or rest.Faction ~= profile.faction or rest.MinSafeRep ~= profile.min_safe_rep then
        error("The authored Green Cube rest point has changed", 0)
    end
    if profile.faction == 0 or faction_reputation(instance, profile.faction).points >= profile.min_safe_rep then
        return rest, arrow:K2_GetComponentLocation(), arrow:K2_GetComponentRotation(), "arrow"
    end

    local alternate = rest.Safe_AltSpawn
    if not valid(alternate) or not same(alternate:GetWorld(), world) or not alternate:HasAuthority()
        or alternate:IsActorBeingDestroyed()
        or alternate:GetFullName() ~= actor_name(profile.map, "LevelExit_Spawn_BP_C", profile.alternate) then
        error("The authored Green Cube reputation alternate is unavailable", 0)
    end
    local location = alternate:K2_GetActorLocation()
    return alternate, { X = location.X, Y = location.Y, Z = location.Z + 50 },
        alternate:K2_GetActorRotation(), "alternate"
end

function green.arrival(state, phase)
    local host, world, instance = stock_instance(state, phase, false)
    local profile = profiles[state.map]
    if not profile then error("The current map is outside the qualified Green Cube arrivals", 0) end
    local actor, target, rotation, placement = arrival_transform(world, instance, profile)
    local location = bed.place_host(host.pawn, host.controller_object, world, target, rotation, placement)
    return { destination = state.map, actor = actor:GetFullName(), placement = placement,
        x = location.X, y = location.Y, z = location.Z }
end

return green
