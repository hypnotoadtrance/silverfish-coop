local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local package = map .. ".DataSilo_01_P"
local core_path = package .. ":PersistentLevel.MindCore_BP_C_1"
local core_name = "MindCore_BP_C " .. core_path
local pawn_name = "Player_FP_C " .. package .. ":PersistentLevel.Player_FP_C_0"
local path = "mod/SilverfishCoop/Scripts/native_mindcore_fixture.lua"
local verb = "native-mindcore-carry-fixture"

local address, classes = 100, {}
local function object(properties, class, owner)
    address = address + 1
    local identity = address
    properties.IsValid = function() return true end
    properties.GetAddress = function() return identity end
    properties.GetClass = function() return classes[class] end
    properties.GetOwner = function() return owner end
    return properties
end
local function actor_class(path)
    classes[path] = object({})
    return path
end
local function row(...)
    local fields = {...}
    for index, value in ipairs(fields) do fields[index] = tostring(value) end
    return table.concat(fields, "\t")
end

local host, focus, do_move, now = true, true, true, 1
local probe_count, walk_count = 0, 0
local pending, grabbed
local standing = {X = 0, Y = 0, Z = 88}
local core_position = {X = 714.3901, Y = -1686.9511, Z = 118.3686}
local angles = {Pitch = 0, Yaw = 0, Roll = 0}
local world = object({GetFullName = function() return "World " .. package end})
world.NetDriver = object({World = world})
local pawn = object({
    GetFullName = function() return pawn_name end,
    HasAuthority = function() return host end,
    IsLocallyControlled = function() return true end,
    IsActorBeingDestroyed = function() return false end,
    GetWorld = function() return world end,
    K2_GetActorRotation = function() return angles end,
    K2_GetActorLocation = function() return standing end,
    K2_TeleportTo = function(_, position) standing = position; return true end,
    InteractionRange = 400, MaxHoldWeight = 110, Dead = false,
}, actor_class("/Game/Classes/Player/Player_FP.Player_FP_C"))
local core = object({
    GetFullName = function() return core_name end,
    GetActorEnableCollision = function() return true end,
    GetWorld = function() return world end,
    GetLocalRole = function() return 3 end,
    IsActorBeingDestroyed = function() return false end,
    K2_GetActorLocation = function() return core_position end,
    K2_SetActorLocation = function() error("The carry path cannot move the Core directly") end,
    bReplicates = false, bReplicateMovement = false,
}, actor_class("/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"))
core.PropMesh = object({
    IsSimulatingPhysics = function() return true end,
    GetMass = function() return 65 end,
    GetFullName = function() return "StaticMeshComponent " .. core_path .. ".PropMesh" end,
}, actor_class("/Script/Engine.StaticMeshComponent"), core)
core.RootComponent = core.PropMesh
local controller = object({
    Pawn = pawn, GetWorld = function() return world end,
    IsLocalController = function() return true end,
    SetControlRotation = function() end, GetControlRotation = function() return angles end,
}, actor_class("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"))
pawn.Controller = controller
pawn.PhysicsHandle = object({GetGrabbedComponent = function() return grabbed end},
    actor_class("/Script/Engine.PhysicsHandleComponent"), pawn)
pawn.Camera = object({
    RelativeRotation = angles,
    K2_GetComponentLocation = function() return {X = standing.X, Y = standing.Y, Z = standing.Z + 60} end,
    K2_SetWorldRotation = function() end, K2_SetRelativeRotation = function() end,
}, actor_class("/Script/Engine.CameraComponent"), pawn)
pawn.CapsuleComponent = object({GetScaledCapsuleHalfHeight = function() return 88 end},
    actor_class("/Script/Engine.CapsuleComponent"), pawn)
pawn.LookTrace = function()
    pawn.ActorBeingLookedAt = focus and core or nil
    pawn.ActorLookedAt_Distance = focus and 160 or 0
end
local state = {map = map, world = world, local_pawn = {pawn = pawn}, controller = controller, listening = true, server = false}
local scene = {world = world, host = pawn, core = core, frame = core.PropMesh, status = 0, installed = false}
classes[core_path] = core
classes["/Script/Engine.Default__GameplayStatics"] = object({GetRealTimeSeconds = function() return now end})
classes["/Script/Engine.Default__KismetSystemLibrary"] = object({
    LineTraceSingle = function(_, _, start, _, _, _, _, _, hit)
        hit.ImpactPoint, hit.ImpactNormal = {X = start.X, Y = start.Y, Z = 0}, {X = 0, Y = 0, Z = 1}
        return true
    end,
})
local env = setmetatable({
    require = function(name)
        if name == "native_mindcore" then return {scene = function() return scene end} end
        if name == "players" then return {
            is_downed = function() return false end,
            command = function(fields, game, phase)
                assert(fields[2] == "walk" and game == state and phase == "hosting")
                walk_count = walk_count + 1
                if do_move then
                    standing = {X = standing.X + 80, Y = standing.Y, Z = standing.Z}
                    core_position = {X = core_position.X + 63, Y = core_position.Y, Z = core_position.Z}
                end
            end,
        } end
        return {}
    end,
    os = {getenv = function() return "1" end},
    FName = function(name) return name end, EFindName = {FNAME_Find = 0},
    StaticFindObject = function(path) return classes[path] end,
    SilverfishProbeNativeInteractInput = function(address, allow_consumed)
        assert(address == pawn:GetAddress() and allow_consumed == false and not pending)
        probe_count = probe_count + 1
        pending = probe_count
        if grabbed then grabbed, pawn.HeldObject = nil, nil
        else grabbed, pawn.HeldObject = core.PropMesh, core end
        return true, "stock_started", pending
    end,
    SilverfishTakeNativeInteractPress = function(address)
        assert(address == pawn:GetAddress() and pending)
        local sequence = pending
        pending = nil
        return sequence, address
    end,
}, {__index = _G})
local fixture = assert(loadfile(path, "t", env))()
local function request(operation, name, phase)
    return pcall(fixture.command, {"1", verb, name or pawn_name, operation}, state, phase or "hosting")
end
local function report()
    return table.concat(fixture.rows(state, "hosting", row), "\n")
end
local function advance(t)
    now = t
    fixture.sync(state, "hosting")
end

assert(not request("grab", "Player_FP_C wrong") and probe_count == 0)
assert(not request("grab", nil, "connected") and probe_count == 0)
focus = false
assert(not request("grab") and probe_count == 0 and standing.X == 0)
assert(report():find("carry_no_stock_focus", 1, true))
focus = true
assert(request("grab") and probe_count == 1 and report():find("\theld\t0.000\t0.000", 1, true))
assert(request("step") and report():find("\tcarrying\t", 1, true))
advance(1.35)
assert(walk_count == 0 and report():find("\tcarrying\t", 1, true))
advance(1.60)
assert(walk_count == 1 and report():find("\tcarrying\t", 1, true))
advance(1.85)
assert(report():find("\tcarried\t80.000\t63.000", 1, true))
assert(request("release") and report():find("\treleased\t80.000\t63.000", 1, true))
assert(probe_count == 2 and not grabbed and not pawn.HeldObject and not pending)
assert(report():find("\thost\t" .. core_name, 1, true))

fixture.clear()
do_move = false
assert(request("grab") and request("step"))
advance(2.2)
advance(2.5)
advance(6)
assert(report():find("carry_motion_timeout", 1, true) and not report():find("\tcarried\t", 1, true))
assert(request("release"))
fixture.clear()
host, state.server, world.NetDriver.ServerConnection = false, true, object({})
local guest_rows = table.concat(fixture.rows(state, "connected", row), "\n")
assert(guest_rows:find("\tguest\t" .. core_name, 1, true) and guest_rows:find("\t1\t1\t0\t0\t3", 1, true))
assert(not request("grab", nil, "connected") and probe_count == 4)
