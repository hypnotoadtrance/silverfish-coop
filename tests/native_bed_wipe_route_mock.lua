local bed = assert(loadfile("mod/SilverfishCoop/Scripts/native_bed.lua"))()
local cross = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"

local function object(name, fields)
    fields = fields or {}
    function fields:IsValid() return true end
    function fields:GetAddress() return self end
    function fields:GetFullName() return name end
    return fields
end

local save = object("SaveGame_BP_C fixture", {RespawnLevel = "Observitory_P", Bed = ""})
local instance = object("GameInstance_FP_C fixture", {SaveSlot_Ref = save})
local statics = object("Default__GameplayStatics", {GetGameInstance = function() return instance end})
function StaticFindObject(path)
    assert(path == "/Script/Engine.Default__GameplayStatics")
    return statics
end

local world = object("World " .. cross .. ".CrossRoads_P")
local pawn = object("Player_FP_C host", {Save = save, HasAuthority = function() return true end,
    GetWorld = function() return world end, ForceNetUpdate = function() end})
function pawn:K2_TeleportTo(location)
    self.location = location
    return true
end
function pawn:K2_GetActorLocation() return self.location end
local controller = object("PlayerControler_FP_C fixture", {Pawn = pawn, IsLocalController = function() return true end,
    GetWorld = function() return world end, SetControlRotation = function() end})
local state = {world = world, map = cross, listening = true, server = false,
    local_pawn = {pawn = pawn, controller_object = controller}}

local function refusal(callback)
    local ok = pcall(callback)
    assert(not ok)
end

local named = bed.source(state, "hosting", true)
assert(named.fallback and named.destination == observatory and named.bed_id == "")
refusal(function() bed.source(state, "hosting") end)
save.RespawnLevel = observatory
assert(bed.source(state, "hosting", true).fallback)
save.RespawnLevel = ""
assert(bed.source(state, "hosting").fallback)
save.RespawnLevel = "Observitory_P"
save.Bed = "SavePoint_BP_C_3-Observitory_P-"
assert(bed.source(state, "hosting", true).bed_id == save.Bed)
assert(bed.source(state, "hosting").bed_id == save.Bed)
save.Bed = "unrecognized"
refusal(function() bed.source(state, "hosting", true) end)
save.RespawnLevel, save.Bed = "CrossRoads_P", ""
refusal(function() bed.source(state, "hosting", true) end)

world = object("World " .. observatory .. ".Observitory_P")
state.world, state.map = world, observatory
local marker = object("PlayerStart " .. observatory .. ".Observitory_P:PersistentLevel.PlayerStart_4",
    {GetWorld = function() return world end,
        K2_GetActorLocation = function() return {X = 1, Y = 2, Z = 3} end,
        K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end})
function FindAllOf(class)
    assert(class == "PlayerStart")
    return {marker}
end
save.RespawnLevel = "Observitory_P"
local arrival = bed.wipe_fallback(state, "hosting")
assert(arrival.destination == observatory and arrival.bed_id == "" and arrival.placement == "player_start")
assert(arrival.actor == marker:GetFullName() and pawn.location.Z == 3)
save.Bed = "SavePoint_BP_C_3-Observitory_P-"
refusal(function() bed.wipe_fallback(state, "hosting") end)
save.RespawnLevel, save.Bed = "CrossRoads_P", ""
refusal(function() bed.wipe_fallback(state, "hosting") end)

print("Stock wipe distinguishes the named Observatory default from a saved bed")
