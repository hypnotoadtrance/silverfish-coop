local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local package = map .. ".DataSilo_01_P"
local persistent = package .. ":PersistentLevel."
local pawn_name = "Player_FP_C " .. persistent .. "Player_FP_C_170"
local core_path, cradle_path = persistent .. "MindCore_BP_C_1", persistent .. "MindCore_Cradle_BP_C_1"
local session, epoch = string.rep("a", 32), string.rep("b", 32)
local verb = "native-mindcore-guest-carry-fixture"
local module = "mod/SilverfishCoop/Scripts/native_mindcore_carry_view.lua"
local classes, address = {}, 0
local function object(values, class)
    address = address + 1
    local key = address
    values.IsValid = function() return true end
    values.GetClass = function() return classes[class] end
    values.GetAddress = function() return key end
    values.GetFullName = function() return values.name end
    values.IsActorBeingDestroyed = function() return false end
    if class and not classes[class] then classes[class] = object({}) end
    return values
end
local function component(owner, class, values)
    values.GetOwner = function() return owner end
    return object(values, "/Script/Engine." .. class)
end
local function position(x, y, z) return {X = x, Y = y, Z = z} end
local function row(...)
    local fields = {...}
    for i, value in ipairs(fields) do fields[i] = tostring(value) end
    return table.concat(fields, "\t")
end

local world = object({name = "World " .. package, PersistentLevel = object({})})
local driver = object({World = world})
driver.ServerConnection, world.NetDriver = object({Driver = driver}), driver
local ready, downed, overlap, role, now, mode = true, false, false, 1, 200, "ready"
local lease_stamp, lease_contents, lease_pawn = now, nil, pawn_name
local actuation, clears, probes, serial, latest, event_lines = nil, 0, 0, 0, nil, {}
local aim = {Pitch = 0, Yaw = 160, Roll = 0}
local pawn = object({
    name = pawn_name, Dead = false, InteractionRange = 400,
    IsLocallyControlled = function() return true end, HasAuthority = function() return false end,
    GetWorld = function() return world end,
}, "/Game/Classes/Player/Player_FP.Player_FP_C")
local controller = object({
    Pawn = pawn, Loading = false, MenuStack = {GetArrayNum = function() return 0 end},
    IsLocalController = function() return true end, GetWorld = function() return world end,
    GetControlRotation = function() return aim end, SetControlRotation = function(_, angle) aim = angle end,
}, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
pawn.Controller = controller
pawn.Camera = component(pawn, "CameraComponent", {
    RelativeRotation = aim, K2_GetComponentLocation = function() return position(0, 0, 0) end,
    K2_SetWorldRotation = function(_, angle) aim = angle end,
    K2_SetRelativeRotation = function(_, angle) aim = angle end,
    GetForwardVector = function()
        local y, p = math.rad(aim.Yaw), math.rad(aim.Pitch)
        return position(math.cos(y)*math.cos(p), math.sin(y)*math.cos(p), math.sin(p))
    end,
})
local core = object({
    name = "MindCore_BP_C " .. core_path, bReplicateMovement = true,
    GetLocalRole = function() return role end, GetActorEnableCollision = function() return true end,
    GetOuter = function() return world.PersistentLevel end, GetWorld = function() return world end,
    K2_GetActorLocation = function() return position(155, 0, 10) end,
}, "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C")
core.PropMesh = component(core, "StaticMeshComponent", {IsSimulatingPhysics = function() return true end})
core.RootComponent = core.PropMesh
local cradle = object({
    name = "MindCore_Cradle_BP_C " .. cradle_path, Status = 0, MindCore_Ref = core,
    GetOuter = function() return world.PersistentLevel end, GetWorld = function() return world end,
}, "/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C")
cradle.SM_MindCoffin_Cradle = component(cradle, "StaticMeshComponent", {
    K2_GetComponentLocation = function() return position(0, 6650, 6700) end,
})
cradle.Capsule = component(cradle, "CapsuleComponent", {
    name = "CapsuleComponent " .. cradle_path .. ".Capsule",
    GetAttachParent = function() return cradle.SM_MindCoffin_Cradle end,
    GetGenerateOverlapEvents = function() return overlap end,
})
pawn.LookTrace = function()
    pawn.ActorBeingLookedAt = math.abs(aim.Yaw) < 1 and core or nil
    pawn.ActorLookedAt_Distance = pawn.ActorBeingLookedAt and 155.3 or 0
end
classes[core_path], classes[cradle_path] = core, cradle
local state = {map = map, world = world, server = true, listening = true, controller = controller,
    local_pawn = {name = pawn_name, pawn = pawn, controller_object = controller}}
local env = setmetatable({
    os = {getenv = function() return ready and "1" or nil end, time = function() return now end},
    io = {open = function(path, mode_open)
        if path == "test/native-mindcore-carry-heartbeat" and mode_open == "rb" then
            return {read = function()
                return lease_contents or row(1, lease_stamp, session, epoch, lease_pawn, mode) .. "\n"
            end, close = function() return true end}
        end
        assert(path == "test/native-mindcore-carry-input.tsv" and mode_open == "ab")
        return {write = function(_, fields, line_end)
            event_lines[#event_lines + 1] = fields .. line_end
            return true
        end, close = function() return true end}
    end},
    FName = function(name) return name end, EFindName = {FNAME_Find = 0},
    StaticFindObject = function(path) return classes[path] end,
    SilverfishSetNativeMindCoreCarryInputScope = function(owner, holding)
        assert(owner == pawn:GetAddress() and type(holding) == "boolean")
        actuation = holding and "release" or "grab"
        return true
    end,
    SilverfishTakeNativeMindCoreCarryInput = function(owner)
        assert(owner == pawn:GetAddress())
        if not latest then return end
        local pending = latest
        latest = nil
        return table.unpack(pending)
    end,
    SilverfishClearNativeMindCoreCarryInputScope = function() actuation, clears = nil, clears + 1 end,
    SilverfishProbeNativeInteractInput = function(owner, consumed)
        assert(owner == pawn:GetAddress() and consumed == true and actuation ~= nil)
        probes, serial = probes + 1, serial + 1
        latest = {serial, actuation, "interact"}
        return true, "stock_started_consumed", 0
    end,
}, {__index = _G})
local view = assert(loadfile(module, "t", env))()
view.start("test", function() return downed end)
local function command(action, name)
    return pcall(view.command, {"1", verb, name or pawn_name, action}, state, "connected")
end
local function sync()
    view.sync(state, "connected")
    return view.rows(state, "connected", row)
end
local function event(operation, source, serial)
    return row("native-mindcore-carry-toggle", serial, map, pawn_name, core.name, session, epoch, operation, source)
end

assert(#sync() == 1 and actuation == "grab")
assert(not command("grab", "another pawn") and probes == 0)
assert(command("grab") and probes == 1 and aim.Yaw == 0)
assert(event_lines[1]:find(event("grab", "interact", 1), 1, true) == 1)
assert(event_lines[1]:match("\t3%.691\t0%.000\n$"))
latest = {1, "grab", "interact"}
assert(#sync() == 1 and #event_lines == 1)

mode = "held"
local held = sync()
assert(#held == 2 and held[2]:match("\t1\t3%.691\t0%.000$"))
assert(command("turn") and math.abs(aim.Yaw - 25) < .001)
held = view.rows(state, "connected", row)
assert(held[2]:match("\t2\t3%.691\t25%.000$"))
assert(command("release") and probes == 2 and event_lines[2]:find(event("release", "interact", 2), 1, true) == 1)
mode = "ready"
latest = {3, "grab", "pickup"}
assert(#sync() == 1 and event_lines[3]:find(event("grab", "pickup", 3), 1, true) == 1)

overlap = true
assert(#sync() == 0 and clears == 1 and not command("grab"))
overlap, role = false, 3
assert(#sync() == 0 and not command("grab"))
role, lease_pawn = 1, "wrong pawn"
assert(#sync() == 0 and not command("grab"))
lease_pawn, now = pawn_name, 203
assert(#sync() == 0 and not command("grab"))
now, cradle.Status = 200, 1
assert(#sync() == 0 and not command("grab"))
cradle.Status, downed = 0, true
assert(#sync() == 0 and not command("grab"))
downed, lease_contents = false, "attacker-controlled row\n"
assert(#sync() == 0 and not command("grab"))

ready = false
local disabled = assert(loadfile(module, "t", env))()
disabled.start("test", function() return false end)
assert(not pcall(disabled.command, {"1", verb, pawn_name, "grab"}, state, "connected"))
assert(#event_lines == 3 and probes == 2)
