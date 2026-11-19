-- Run with Lua 5.4 from the repository root.
local area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local path = area .. ".CrossRoads_P"
local placed = path .. ":PersistentLevel."
local kill_path = placed .. "KillVolume_BP_C_0"
local pawn_type = "/Game/Classes/Player/Player_FP.Player_FP_C"
local save_type = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_type = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local capsule_type = "/Script/Engine.CapsuleComponent"
local movement_type = "/Script/Engine.CharacterMovementComponent"
local volume_type = "/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C"
local box_type = "/Script/Engine.BoxComponent"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local query_path = "/Script/Engine.Default__KismetSystemLibrary"
local registry, next_address = {}, 0

local function object(name, class, key)
    next_address = next_address + 1
    local address = next_address
    local result = {GetAddress = function() return address end, GetFullName = function() return name end,
        GetClass = function() return registry[class] end, IsValid = function() return true end,
        HasAuthority = function() return true end, IsActorBeingDestroyed = function() return false end}
    if key then registry[key] = result end
    return result
end

for _, class in ipairs({pawn_type, save_type, instance_type, capsule_type, movement_type, volume_type, box_type}) do
    object("Class " .. class, nil, class)
end
local world = object("World " .. path)
world.NetDriver, world.PersistentLevel = object("driver"), object("level")
local volume = object("KillVolume_BP_C " .. kill_path, volume_type, kill_path)
volume.GetWorld, volume.GetOuter = function() return world end, function() return world.PersistentLevel end
local box = object("BoxComponent " .. kill_path .. ".Box", box_type)
volume.Box = box
function box:GetOwner() return volume end
function box:K2_GetComponentLocation() return {X = 0, Y = 0, Z = -22574.027039} end
function box:GetScaledBoxExtent() return {X = 200000, Y = 200000, Z = 100} end
function box:IsOverlappingActor(pawn) return math.abs(pawn.position.Z + 22574.027039) < 100 end

local function make_pawn(id, kind, x)
    local name = "Player_FP_C " .. placed .. "Player_FP_C_" .. id
    local pawn = object(name, pawn_type)
    pawn.Save, pawn.Dead = object("save", save_type), false
    pawn.Save.PlayerHealth = 250
    pawn.position, pawn.teleports = {X = x, Y = 0, Z = 96}, 0
    pawn.GetWorld = function() return world end
    function pawn:K2_GetActorLocation() return self.position end
    function pawn:K2_GetActorRotation() return {Pitch = 0, Yaw = 0, Roll = 0} end
    function pawn:K2_TeleportTo(target) self.position, self.teleports = target, self.teleports + 1; return true end
    function pawn:ForceNetUpdate() self.updated = true end
    local capsule, movement = object("capsule", capsule_type), object("movement", movement_type)
    pawn.CapsuleComponent, pawn.CharacterMovement = capsule, movement
    capsule.GetOwner, movement.GetOwner = function() return pawn end, function() return pawn end
    function capsule:GetScaledCapsuleRadius() return 32 end
    function capsule:GetScaledCapsuleHalfHeight() return 92 end
    function movement:IsMovingOnGround() return pawn.position.Z == 96 end
    function movement:StopMovementImmediately() self.stopped = true end
    function movement:SetMovementMode(mode) assert(mode == 3); self.falling = true end
    return pawn, {kind = kind, name = name, pawn = pawn}
end

local host, local_detail = make_pawn(12, "local", 0)
local guest, guest_detail = make_pawn(14, "guest", 150)
local statics = object("statics", nil, statics_path)
local instance = object("instance", instance_type)
instance.SaveSlot_Ref, instance["As Player FP"] = host.Save, host
function statics:GetGameInstance(pawn) assert(pawn == host); return instance end
local query = object("query", nil, query_path)
function query:LineTraceSingle(_, _, _, _, _, _, _, out)
    out.ImpactPoint, out.ImpactNormal = {X = 0, Y = 0, Z = 0}, {X = 0, Y = 0, Z = 1}
    return true
end
function query:CapsuleTraceSingle() return false end

local authorized, exposed, expired, hits, dispatches, world_is_nothing = true, false, false, 0, 0, false
local modules = {
    native_transport = {scoped_host = function() return local_detail, true end},
    guest_save = {exact_item_context = function(_, _, name)
        assert(name == guest_detail.name)
        if authorized then return guest, guest.Save end
    end},
    players = {is_downed = function() return false end,
        is_standing_guest = function() return not guest.HoldingCrouch end},
    native_death = {publish_kill_volume_down = function(_, _, pawn, save, projected_downed)
        assert(pawn == guest and save == guest.Save and not box:IsOverlappingActor(pawn))
        if projected_downed then return true end
        dispatches, save.PlayerHealth = dispatches + 1, 0
        return true, save.PlayerHealth
    end},
}
local env = setmetatable({require = function(name) return assert(modules[name]) end, os = {getenv = function() return "1" end, time = os.time},
    StaticFindObject = function(key) return registry[key] end,
    SilverfishReadNativeKillVolume = function() assert(not world_is_nothing); return true, hits, 0 end,
    SilverfishNextNativeKillVolumeCrossing = function(after)
        if exposed and after < hits then return hits, guest:GetAddress(), volume:GetAddress(), expired end
        return 0
    end,
    SilverfishFinishNativeKillVolumeCrossing = function(sequence, address)
        assert(sequence == hits and address == guest:GetAddress() and exposed)
        exposed = false
        return true
    end}, {__index = _G})
local guard = assert(loadfile("mod/SilverfishCoop/Scripts/native_kill_volume.lua", "t", env))()
local state = {world = world, map = area, listening = true, server = false, pawns = {local_detail, guest_detail}}
local function update() return guard.sync(state, "hosting") end
local function rows() return guard.rows(state, "hosting", table.pack) end
assert(not update() and rows()[1][4] == "armed")
assert(guard.command({"nonce", "native-kill-volume-fall-fixture", guest_detail.name}, state, "hosting"))
assert(not box:IsOverlappingActor(guest) and guest.CharacterMovement.falling and guest.Save.PlayerHealth == 250)
guest.position = box:K2_GetComponentLocation()
authorized, exposed, hits = false, true, 1
assert(not update() and dispatches == 0 and guest.teleports == 1 and guard.capture_blocked(state, "hosting"))
authorized = true
assert(update() and dispatches == 1 and guest.teleports == 2 and guest.Save.PlayerHealth == 0)
assert(guard.capture_blocked(state, "hosting") and rows()[2][2] == guest_detail.name and rows()[2][4] == "teammate" and rows()[2][8] == 0)
guest.HoldingCrouch, guest.JumpMaxCount = true, 0
assert(not update() and dispatches == 1 and not guard.capture_blocked(state, "hosting"))

guest.position, guest.Save.PlayerHealth = box:K2_GetComponentLocation(), 3.452
exposed, hits = true, 2
assert(update() and dispatches == 1 and guest.teleports == 3 and guest.Save.PlayerHealth == 3.452 and rows()[2][8] == "-")

guest.position = box:K2_GetComponentLocation()
authorized, exposed, expired, hits = false, true, true, 3
assert(not update() and dispatches == 1 and guest.teleports == 3)
assert(rows()[1][4] == "fault" and rows()[1][7] == "crossing_owner_unavailable")
state.map, world_is_nothing = "/Game/Classes/Maps/Dreams/Nothing_P", true
assert(not update() and #rows() == 0)
