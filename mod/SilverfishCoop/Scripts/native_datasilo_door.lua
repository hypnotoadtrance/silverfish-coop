local door = {}
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local guest_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world_path = map .. ".DataSilo_01_P"
local persistent = world_path .. ":PersistentLevel."
local path = persistent .. "Door_Metal_Sliding_01_BP_C_1"
local name = "Door_Metal_Sliding_01_BP_C " .. path
local class = "/Game/Assets/StaticMeshes/Doors/Door_Metal_Sliding_01_BP.Door_Metal_Sliding_01_BP_C"
local panel_asset = "/Game/Assets/StaticMeshes/Mesh_Scenery/Doors_Windows/"
    .. "SM_Bld_Door_Single_05_SM_Bld_Door_Single_05_Door_01.SM_Bld_Door_Single_05_SM_Bld_Door_Single_05_Door_01"
local terminal_class = "/Game/Classes/Actors/Terminal_BP.Terminal_BP_C"
local key_class = "/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local item_class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local closed, opened = {X = 0, Y = 0, Z = 0}, {X = 0, Y = -155, Z = 0}
local authored = {X = 0, Y = 5000, Z = 6600}
local scoped, is_client

local function refuse(reason) error("native_datasilo_door_" .. reason, 0) end
local function valid(value) return not not (value and value:IsValid()) end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, type_path) return valid(value) and same(value:GetClass(), StaticFindObject(type_path)) end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < 10000000 end

local function point(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return {X = value.X, Y = value.Y, Z = value.Z}
    end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll}
    end
end

local function near(a, b, distance)
    return a and b and (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= distance^2
end

local function angle(a, b, tolerance)
    return math.abs((a - b + 180) % 360 - 180) <= tolerance
end

local function placed(value, type_path, full_name, world)
    return exact(value, type_path) and value:GetFullName() == full_name and not value:IsActorBeingDestroyed()
        and same(value:GetWorld(), world) and same(value:GetOuter(), world.PersistentLevel)
end

local function owned(value, owner, type_name)
    return exact(value, "/Script/Engine." .. type_name) and same(value:GetOwner(), owner)
end

local function component(value, owner, suffix, type_name)
    return owned(value, owner, type_name) and value:GetFullName() == type_name .. " " .. path .. "." .. suffix
end

local function guarded_native()
    return type(SilverfishDataSiloDoorGuardAvailable) == "function" and SilverfishDataSiloDoorGuardAvailable() == true
end

local function peer_role(state, phase)
    if not enabled or state.map ~= map or not state.listening then return end
    if phase == "hosting" and state.server == false then return "host" end
    if phase == "connected" and state.server == true then return "guest" end
end

local function world_scope(world, peer)
    if not valid(world) or world:GetFullName() ~= "World " .. world_path then return end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) then return end
    local server = driver.ServerConnection
    if peer == "host" then return not valid(server) end
    return is_client and is_client() and valid(server) and same(server.Driver, driver)
end

local function collision(value)
    local mode = math.tointeger(value:GetCollisionEnabled())
    if mode and mode >= 0 and mode <= 5 then return mode end
end

local function queries(mode)
    return mode == 1 or mode == 3 or mode == 5
end

local function scene(world, peer)
    if not world_scope(world, peer) then return end
    local actor = StaticFindObject(path)
    if not placed(actor, class, name, world) or actor.bNetLoadOnClient ~= true
        or not same(actor.KeyItem, StaticFindObject(key_class)) then return end
    local sphere, arrow, panel, knob = actor.RootComponent, actor.Arrow, actor.DoorMesh, actor.Box_Knob
    if not component(sphere, actor, "Sphere", "SphereComponent") or not same(sphere, actor.Sphere)
        or not component(arrow, actor, "Arrow", "ArrowComponent") or not same(arrow:GetAttachParent(), sphere)
        or arrow.bNetAddressable ~= true or arrow.bAbsoluteLocation ~= false
        or not component(panel, actor, "DoorMesh", "StaticMeshComponent") or not same(panel:GetAttachParent(), arrow)
        or not same(panel.StaticMesh, StaticFindObject(panel_asset))
        or not component(knob, actor, "Box_Knob", "BoxComponent") or not same(knob:GetAttachParent(), panel) then return end

    local root = point(actor:K2_GetActorLocation())
    local yaw = rotation(actor:K2_GetActorRotation())
    local relative, arrow_world = point(arrow.RelativeLocation), point(arrow:K2_GetComponentLocation())
    local panel_world = point(panel:K2_GetComponentLocation())
    if not near(root, authored, 2) or not yaw or not angle(yaw.Yaw, -90, .15)
        or not angle(yaw.Roll, 0, .15) or not angle(yaw.Pitch, 0, .15) or not relative
        or math.abs(relative.X) > 2 or math.abs(relative.Z) > 2 or relative.Y < -157 or relative.Y > 2
        or not near(arrow_world, {X = root.X + relative.Y, Y = root.Y - relative.X, Z = root.Z + relative.Z}, 3)
        or not near(panel_world, arrow_world, 2) then return end

    local local_role = math.tointeger(actor:GetLocalRole())
    local panel_collision, knob_collision = collision(panel), collision(knob)
    if not local_role or local_role < 0 or local_role > 3 or not panel_collision or not knob_collision
        or type(actor.bReplicates) ~= "boolean" or type(arrow.bReplicates) ~= "boolean" then return end
    if peer == "host" and (type(actor.Locked) ~= "boolean" or type(actor.Open) ~= "boolean") then return end
    local pose = near(relative, closed, 2) and "closed" or near(relative, opened, 2) and "open" or "moving"
    return {world = world, peer = peer, actor = actor, sphere = sphere, arrow = arrow, panel = panel, knob = knob,
        relative = relative, arrow_world = arrow_world, panel_world = panel_world, pose = pose,
        panel_collision = panel_collision, knob_collision = knob_collision, role = local_role}
end

local function same_scope(current)
    return scoped and current.peer == scoped.peer and same(current.world, scoped.world)
        and same(current.actor, scoped.actor) and same(current.sphere, scoped.sphere)
        and same(current.arrow, scoped.arrow) and same(current.panel, scoped.panel) and same(current.knob, scoped.knob)
end

local function scope(current)
    if not same_scope(current) then
        scoped = {world = current.world, peer = current.peer, actor = current.actor,
            sphere = current.sphere, arrow = current.arrow, panel = current.panel, knob = current.knob}
    end
    return scoped
end

local function activate_host(current, projection)
    local actor, arrow = current.actor, current.arrow
    if not guarded_native() or projection.failed or current.role ~= 3 or not actor:HasAuthority()
        or actor:GetActorEnableCollision() ~= true or not queries(current.panel_collision)
        or not queries(current.knob_collision) then return "replication_unverified" end
    if not projection.active then
        local prior_actor, prior_arrow = actor.bReplicates, arrow.bReplicates
        local applied = pcall(function()
            if not prior_actor then actor:SetReplicates(true) end
            if actor.bReplicates == true and not prior_arrow then arrow:SetIsReplicated(true) end
        end)
        if not applied or actor.bReplicates ~= true or arrow.bReplicates ~= true then
            pcall(function()
                if arrow.bReplicates ~= prior_arrow then arrow:SetIsReplicated(prior_arrow) end
                if actor.bReplicates ~= prior_actor then actor:SetReplicates(prior_actor) end
            end)
            projection.failed = true
            return "replication_unverified"
        end
        projection.active = true
    elseif actor.bReplicates ~= true or arrow.bReplicates ~= true then
        projection.failed = true
        return "replication_unverified"
    end
    -- The stock latent action slides the Arrow. Native component replication
    -- carries RelativeLocation and updates the attached physical panel.
    if not near(current.relative, projection.sent, .5) or projection.sent_pose ~= current.pose then
        actor:ForceNetUpdate()
        projection.sent, projection.sent_pose = current.relative, current.pose
    end
    return "active"
end

local function guard_guest(current)
    if not guarded_native() or current.actor:GetActorEnableCollision() ~= true
        or not queries(current.panel_collision) then return "guard_unverified" end
    -- The attached DoorMesh continues to block pawns at its replicated pose.
    -- Only the guest's exact knob ceases to be an interaction target.
    if current.knob_collision ~= 0 then current.knob:SetCollisionEnabled(0) end
    if current.knob:GetCollisionEnabled() ~= 0 then return "guard_unverified" end
    return current.role == 1 and "guarded" or "awaiting_replica"
end

local function sync_world(world, peer)
    local current = scene(world, peer)
    if not current then return end
    local projection = scope(current)
    projection.status = peer == "host" and activate_host(current, projection) or guard_guest(current)
end

function door.start(client_role)
    if not enabled or is_client or type(client_role) ~= "function" then return end
    is_client = client_role
    if type(RegisterLoadMapPostHook) == "function" then
        RegisterLoadMapPostHook(function(_, loaded)
            pcall(function() sync_world(loaded:get(), "guest") end)
        end)
    end
    if type(RegisterBeginPlayPreHook) == "function" then
        RegisterBeginPlayPreHook(function(context)
            pcall(function()
                if not is_client() then return end
                local candidate = context:get()
                if valid(candidate) and candidate:GetFullName() == name then sync_world(candidate:GetWorld(), "guest") end
            end)
        end)
    end
end

function door.sync(state, phase)
    local peer = peer_role(state, phase)
    if peer then pcall(sync_world, state.world, peer) end
end

local function coords(value)
    return string.format("%.3f", value.X), string.format("%.3f", value.Y), string.format("%.3f", value.Z)
end

function door.rows(state, phase, row)
    local peer = peer_role(state, phase)
    if not peer then return {} end
    local ok, result = pcall(function()
        local current = scene(state.world, peer)
        if not current or not same_scope(current) or not scoped.status then return {} end
        local status = scoped.status
        if peer == "guest" and current.knob_collision ~= 0 then status = "guard_unverified" end
        local host = peer == "host"
        local x, y, z = coords(current.relative)
        local ax, ay, az = coords(current.arrow_world)
        return {row("native-datasilo-door", map, peer, status, name, current.role,
            current.actor.bReplicates and 1 or 0, current.arrow.bReplicates and 1 or 0,
            current.panel_collision, current.knob_collision, current.pose,
            host and (current.actor.Locked and 1 or 0) or "-", host and (current.actor.Open and 1 or 0) or "-",
            x, y, z, ax, ay, az, coords(current.panel_world))}
    end)
    return ok and result or {}
end

local function standing_pawn(state, peer)
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or pawn:GetFullName() ~= detail.name or pawn:IsActorBeingDestroyed()
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or pawn:HasAuthority() ~= (peer == "host") or not pawn:IsLocallyControlled()
        or not controller:IsLocalController()
        or (peer == "host" and (controller.Loading ~= false or not controller.MenuStack
            or controller.MenuStack:GetArrayNum() ~= 0))
        or pawn.Dead ~= false or players.is_downed(pawn) ~= false then return end
    return pawn
end

local function canonical_save(pawn)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
        or not same(instance["As Player FP"], pawn) or not same(pawn.Save, save) then refuse("fixture_canonical_save") end
    return save
end

local function owns_key(save, expected)
    local backpack = save.PlayerBackpack
    local count = backpack and math.tointeger(backpack:GetArrayNum())
    if not count or count < 1 or count > 1024 then return false end
    for index = 1, count do
        local item = backpack[index]
        if item and same(item[item_class_field], expected) then return true end
    end
    return false
end

local function terminal_power(current, pawn)
    local terminal_path = persistent .. "Terminal_BP_C_1"
    local terminal = StaticFindObject(terminal_path)
    if not placed(terminal, terminal_class, "Terminal_BP_C " .. terminal_path, current.world)
        or not terminal:HasAuthority() then refuse("fixture_terminal") end
    local links = terminal.Active_Actors
    local link = links and links:Contains(current.actor) and links:Find(current.actor):get()
    if type(link) == "userdata" then link = link:ToString() end
    if link ~= "CL-Door" then refuse("fixture_terminal_door_binding") end
    current.actor:Send_PowerSignal(true, pawn)
end

local function button(current, pawn, save)
    if current.actor.Locked and not owns_key(save, current.actor.KeyItem) then refuse("fixture_savlo_key_required") end
    local camera, press, take = pawn.Camera, SilverfishProbeNativeInteractInput, SilverfishTakeNativeInteractPress
    if not owned(camera, pawn, "CameraComponent") or type(press) ~= "function"
        or type(take) ~= "function" then refuse("fixture_stock_input") end
    local prior, eye = rotation(camera.RelativeRotation), point(camera:K2_GetComponentLocation())
    local target, reach = point(current.knob:K2_GetComponentLocation()), pawn.InteractionRange
    if not prior or not eye or not finite(reach) or reach <= 0 or reach > 1000
        or not near(point(pawn:K2_GetActorLocation()), target, reach) then refuse("fixture_knob_out_of_reach") end
    local dx, dy, dz = target.X-eye.X, target.Y-eye.Y, target.Z-eye.Z
    local aim = {Pitch = math.deg(math.atan(dz, math.sqrt(dx^2 + dy^2))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
    local ok, failure = pcall(function()
        camera:K2_SetWorldRotation(aim, false, {}, true)
        pawn:LookTrace()
        if pawn.CanInteract ~= true or not same(pawn.InteractionTarget_Component, current.knob) then
            refuse("fixture_knob_not_stock_focus")
        end
        local accepted, outcome, sequence = press(pawn:GetAddress(), true)
        if accepted ~= true then refuse("fixture_knob_input:" .. tostring(outcome)) end
        if outcome == "stock_started_consumed" and sequence == 0 then return end
        local receipt, owner = take(pawn:GetAddress())
        if outcome ~= "stock_started" or not math.tointeger(sequence) or sequence < 1
            or receipt ~= sequence or owner ~= pawn:GetAddress() then refuse("fixture_knob_input_readback") end
    end)
    camera:K2_SetRelativeRotation(prior, false, {}, true)
    pawn:LookTrace()
    local restored = rotation(camera.RelativeRotation)
    if not restored or not angle(restored.Pitch, prior.Pitch, .05) or not angle(restored.Yaw, prior.Yaw, .05)
        or not angle(restored.Roll, prior.Roll, .05) then refuse("fixture_camera_restore") end
    if not ok then error(failure, 0) end
end

local function guest_guard(current, pawn)
    if not guest_fixture or current.pose ~= "closed" or current.knob_collision ~= 0
        or scoped.status ~= "guarded" or current.role ~= 1 then refuse("fixture_guest_guard_scope") end
    local locked, open = current.actor.Locked, current.actor.Open
    current.actor:Send_PowerSignal(true, pawn)
    if current.actor.Locked ~= locked or current.actor.Open ~= open
        or not near(point(current.arrow.RelativeLocation), current.relative, .02) then
        refuse("fixture_guest_guard_mutation")
    end
    return "stock Data Silo upper door guest guard probed"
end

function door.command(fields, state, phase)
    if fields[2] ~= "native-datasilo-door-fixture" then return end
    local target, peer = fields[3], peer_role(state, phase)
    if #fields ~= 3 or not peer or not guarded_native()
        or (peer == "guest" and (target ~= "guard" or not guest_fixture))
        or (peer == "host" and (not host_fixture or (target ~= "button" and target ~= "power"))) then
        refuse("fixture_scope")
    end
    local current, pawn = scene(state.world, peer), standing_pawn(state, peer)
    if not current or not same_scope(current) or current.pose == "moving" then refuse("fixture_door_not_ready") end
    if not pawn then refuse("fixture_pawn_not_ready") end
    if peer == "guest" then return guest_guard(current, pawn) end
    if scoped.status ~= "active" then refuse("fixture_replication_unverified") end
    local save = canonical_save(pawn)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local now = statics:GetRealTimeSeconds(pawn)
    if not finite(now) or (scoped.last_input and now - scoped.last_input < .45) then refuse("fixture_input_busy") end
    scoped.last_input = now
    if target == "power" then terminal_power(current, pawn) else button(current, pawn, save) end
    return "stock Data Silo upper door " .. target .. " dispatched"
end

function door.clear()
    -- Keep old guest knob collision quiet while the world and its actors retire.
    scoped = nil
end

return door
