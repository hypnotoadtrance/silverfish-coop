local purple = {}
local bed = require("native_bed")
local green = require("native_green")
local transport = require("native_transport")

local overworld = "/Game/Classes/Maps/Maps_OverWorld/"
local dungeons = "/Game/Classes/Maps/Maps_Dungeons/"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"

local shared_arrivals = {
    [overworld .. "Observitory_P"] = true,
    [overworld .. "DeadBridge_P"] = true,
    [overworld .. "Forest_01_P"] = true,
}
local neutral_beds = {
    [overworld .. "City_01_P"] = true,
    [overworld .. "RuinFeilds_P"] = true,
    [overworld .. "Lake_01_P"] = true,
}
local player_starts = {
    [overworld .. "CrossRoads_P"] = "PlayerStart_2",
    [overworld .. "RailYard_P"] = "PlayerStart_2",
    [overworld .. "Beach_01_P"] = "PlayerStart_1",
    [overworld .. "Docks_01_P"] = "PlayerStart_1",
    [overworld .. "NorthEast_Rail_P"] = "PlayerStart_1",
    [overworld .. "Slums_P"] = "PlayerStart_1",
    [overworld .. "TrashPit_P"] = "PlayerStart_1",
    [dungeons .. "Cistern_P"] = "PlayerStart_1",
    [dungeons .. "Refinery_01_P"] = "PlayerStart_1",
}

function purple.supports(map)
    return shared_arrivals[map] == true or neutral_beds[map] == true or player_starts[map] ~= nil
end

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function placed_actor(world, class, name)
    local level_path = world:GetFullName():match("^World (.+)$")
    local expected = name and class .. " " .. level_path .. ":PersistentLevel." .. name
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if match or actor:GetFullName() ~= expected or not actor:HasAuthority() then
                error("The stock Purple Cube " .. class .. " is ambiguous or unqualified", 0)
            end
            match = actor
        end
    end
    if name and not match then error("The stock Purple Cube " .. class .. " is unavailable", 0) end
    return match
end

local function host_in_world(state, phase)
    local host = transport.scoped_host(state, phase)
    local world = state.world
    local basename = state.map:match("/([^/]+)$")
    local world_name = "World " .. state.map .. "." .. basename
    if not host or not valid(world) or world:GetFullName() ~= world_name
        or not host.pawn:GetFullName():match("^Player_FP_C ") then
        error("The active authoritative Purple Cube host is unavailable", 0)
    end
    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not same(host.pawn.Save, save) or not save:GetFullName():match("^SaveGame_BP_C ") then
        error("The Purple Cube requires the canonical native host save", 0)
    end
    return host, world
end

function purple.arrival(state, phase)
    local map = state.map
    if not purple.supports(map) then error("The current map has no qualified stock Purple Cube arrival", 0) end
    if shared_arrivals[map] then return green.arrival(state, phase) end

    local host, world = host_in_world(state, phase)
    local stock_bed = placed_actor(world, "SavePoint_BP_C", neutral_beds[map] and "SavePoint_BP_C_1")
    local actor, target, rotation, placement
    if stock_bed then
        local arrow = stock_bed.Arrow
        if not valid(arrow) or not same(arrow:GetOwner(), stock_bed) or stock_bed.Faction ~= 0 then
            error("The authored Purple Cube neutral bed has changed", 0)
        end
        actor, target, rotation, placement = stock_bed, arrow:K2_GetComponentLocation(),
            arrow:K2_GetComponentRotation(), "arrow"
    else
        local start = placed_actor(world, "PlayerStart", player_starts[map])
        actor, target, rotation, placement = start, start:K2_GetActorLocation(), start:K2_GetActorRotation(), "start"
    end

    local location = bed.place_host(host.pawn, host.controller_object, world, target, rotation, placement)
    return { destination = map, actor = actor:GetFullName(), placement = placement,
        x = location.X, y = location.Y, z = location.Z }
end

return purple
