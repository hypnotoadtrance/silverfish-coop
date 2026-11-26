package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local destination = map .. ".CrossRoads_P"
local address = 0
local function object(name, data)
    address = address + 1
    local id = address
    data = data or {}
    data.GetFullName = function() return name end
    data.GetAddress = function() return id end
    data.IsValid = function() return true end
    data.IsActorBeingDestroyed = function() return false end
    data.HasAuthority = function() return true end
    return data
end
local level = object("Level")
local world = object("World " .. destination, {PersistentLevel = level})
local save = object("SaveGame_BP_C Save")
local host = object("Player_FP_C Host", { Save = save, GetWorld = function() return world end })
local pc = object("PlayerControler_FP_C Controller", { Pawn = host, GetWorld = host.GetWorld, IsLocalController = function() return true end })
local base = object("Class")
local arrow_type = object("Class")
local instance = object("GameInstance_FP_C Instance", {SaveSlot_Ref = save})
local target, candidates = {X = 10, Y = 20, Z = 30}, {}
host.K2_GetActorLocation = function() return target end
local location_calls = 0
local statics = object("GameplayStatics", {
    GetGameInstance = function() return instance end,
    GetAllActorsOfClass = function(_, found_world, class, out)
        assert(found_world == world and class == base)
        out.OutActors = {}
        for index, candidate in ipairs(candidates) do
            out.OutActors[index] = { type = function() return "RemoteUnrealParam" end, get = function() return candidate end }
        end
    end,
})
function StaticFindObject(path)
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
    if path == "/Script/Engine.ArrowComponent" then return arrow_type end
    if path == "/Game/Classes/ScriptingActors/LevelExit_Spawn_BP.LevelExit_Spawn_BP_C" then return base end
end
package.loaded.native_bed = {place_host = function(pawn, controller, current, point, _, type)
    assert(pawn == host and controller == pc and current == world and point == target and type == "arrow")
    location_calls = location_calls + 1
    return point
end}
local function marker(suffix)
    local actor = object("ModArrival_C " .. destination .. ":PersistentLevel." .. suffix, {
        GetWorld = host.GetWorld, GetOuter = function() return level end, GetParentComponent = function() return nil end,
        IsA = function(_, type) return type == base end, EntranceTag = "Mod-Entrance",
    })
    actor.Arrow = object("Arrow", {GetOwner = function() return actor end, GetClass = function() return arrow_type end,
        K2_GetComponentLocation = function() return target end, K2_GetComponentRotation = function() return {} end})
    return actor
end
local arrival = require("native_authored_arrival")
local state = {map = map, listening = true, server = false, world = world, local_pawn = {pawn = host, controller_object = pc}}
candidates = {marker("NewStockStyleMarker_201")}
assert(arrival.place(state, "hosting", "Mod-Entrance", "overlap", "-").actor == candidates[1]:GetFullName())
assert(location_calls == 1)
candidates[2] = marker("SecondMarker_8")
local retained = arrival.place(state, "hosting", "Mod-Entrance", "overlap", "-")
assert(retained.placement == "stock-spawn:duplicate-entrance-tag" and retained.actor == host:GetFullName())
assert(location_calls == 1)
candidates = {}
retained = arrival.place(state, "hosting", "Mod-Entrance", "overlap", "-")
assert(retained.placement == "stock-spawn:entrance-unavailable" and retained.actor == host:GetFullName())
assert(location_calls == 1)
