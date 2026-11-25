-- Invoke from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
os.getenv = function(name)
    return name == "SILVERFISH_COOP_NATIVE_DEATH" and "cpp" or "1"
end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local paths, address = {}, 0
local function object(name, class, properties)
    address = address + 1
    local identity = address
    local result = properties or {}
    function result:IsValid() return true end
    function result:GetAddress() return identity end
    function result:GetFullName() return name end
    function result:GetClass() return class end
    function result:IsA(expected) return class == expected end
    return result
end
local function register(path, class, properties)
    paths[path] = object(path, class, properties)
    return paths[path]
end

local checks = 0
function StaticFindObject(path)
    if path == "/Game/Classes/Player/GameMode_FP.GameMode_FP_C" then checks = checks + 1 end
    return paths[path]
end

local world = object("World " .. map .. ".Observitory_P")
local function actor(name, kind, local_player, properties)
    local result = object(name, kind, properties)
    function result:GetWorld() return world end
    function result:HasAuthority() return true end
    function result:IsActorBeingDestroyed() return false end
    function result:IsLocallyControlled() return local_player end
    function result:IsLocalController() return local_player end
    return result
end

local pawn_type = register("/Game/Classes/Player/Player_FP.Player_FP_C")
local controller_type = register("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
local game_mode_type = register("/Game/Classes/Player/GameMode_FP.GameMode_FP_C")
local game_instance_type = register("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
local save_type = register("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
local player_state_type = register("/Script/Engine.PlayerState")
local connection_type = register("/Script/Engine.NetConnection")
local capsule_type = register("/Script/Engine.CapsuleComponent")
local save = object("SaveGame_BP local", save_type)
local capsule = object("CapsuleComponent stock", capsule_type, {
    GetUnscaledCapsuleRadius = function() return 25 end, GetUnscaledCapsuleHalfHeight = function() return 88 end,
    GetScaledCapsuleRadius = function() return 25 end, GetScaledCapsuleHalfHeight = function() return 88 end,
})
local host = actor("Player_FP_C host", pawn_type, true, {
    Dead = false, Save = save, CapsuleComponent = capsule,
    K2_GetActorLocation = function() return {X = 0, Y = 0, Z = 88} end,
    K2_GetActorRotation = function() return {Yaw = 0} end,
})
function capsule:GetOwner() return host end
function pawn_type:GetCDO() return {IsValid = function() return true end, CapsuleComponent = capsule} end
local local_controller = actor("PlayerControler_FP_C host", controller_type, true, {Pawn = host})
host.Controller = local_controller
local guest_controller = actor("PlayerControler_FP_C guest", controller_type, false, {
    PlayerState = object("PlayerState guest", player_state_type, {bOnlySpectator = false}),
})
local connection = object("IpConnection guest", connection_type, {
    PlayerController = guest_controller, OwningActor = guest_controller,
})
guest_controller.Player = connection
local driver = object("IpNetDriver live", nil, {World = world,
    ClientConnections = {connection, GetArrayNum = function(entries) return #entries end},
})
connection.Driver, world.NetDriver = driver, driver
local restart = object("Function /Script/Engine.GameModeBase:RestartPlayerAtTransform")
setmetatable(restart, {__call = function(_, _, controller, transform)
    assert(controller == guest_controller and transform.Translation.X == 120)
    controller.Pawn = actor("Player_FP_C guest", pawn_type, false, {
        Controller = controller, Dead = false, K2_GetActorLocation = function() return transform.Translation end,
    })
end})
local mode = actor("GameMode_FP_C mode", game_mode_type, true, {
    DefaultPawnClass = pawn_type, PlayerControllerClass = controller_type,
    bStartPlayersAsSpectators = false, RestartPlayerAtTransform = restart,
})
local instance = object("GameInstance_FP_C local", game_instance_type, {SaveSlot_Ref = save})
register("/Script/Engine.Default__GameplayStatics", nil, {
    GetGameMode = function() return mode end, GetGameInstance = function() return instance end,
})
register("/Script/Engine.Default__KismetSystemLibrary", nil, {
    LineTraceSingle = function(_, _, from, _, _, _, _, _, hit)
        hit.ImpactPoint, hit.ImpactNormal = {X = from.X, Y = from.Y, Z = 0}, {X = 0, Y = 0, Z = 1}
        return true
    end,
    CapsuleTraceSingle = function() return false end,
})

local roster, spawn = require("native_roster"), require("native_roster_spawn")
local row = function(...) return table.concat({...}, "\t") end
local state = {server = false, listening = true, clients = 1, world = world, map = map, controller = local_controller,
    local_pawn = {name = host:GetFullName(), pawn = host, controller_object = local_controller}}
local function observe_roster()
    local _, observed = roster.rows(state, "hosting", row)
    return spawn.rows(state, "hosting", row, observed)
end

driver.ClientConnections[1], state.clients = nil, 0
assert(#observe_roster() == 0 and checks == 0)
driver.ClientConnections[1], state.clients = connection, 1
assert(observe_roster()[1] == "native-roster-spawn-capability\t1" and checks == 1)
local command = {"test", "native-roster-restart-player", connection:GetFullName(), guest_controller:GetFullName()}
assert(spawn.command(command, state, "hosting") == "exact stock remote player restarted" and checks == 3)
local receipt = observe_roster()
assert(#receipt == 1 and receipt[1]:find("native-roster-spawn\trestarted", 1, true) == 1 and checks == 3)
assert(observe_roster()[1] == receipt[1] and checks == 3)
driver.ClientConnections[1], state.clients = nil, 0
assert(#observe_roster() == 0 and checks == 3)
print("stock player recovery capability and receipt reuse passed")
