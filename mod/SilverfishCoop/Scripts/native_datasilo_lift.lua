local lift = {}
local guest_save = require("guest_save")
local mindcore = require("native_mindcore")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local guest_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world_path = map .. ".DataSilo_01_P"
local persistent = world_path .. ":PersistentLevel."
local lift_path = persistent .. "RailLift_BP_C_1"
local lift_name = "RailLift_BP_C " .. lift_path
local lift_class = "/Game/Classes/Actors/RailLift_BP.RailLift_BP_C"
local waypoint_class = "/Game/Classes/Actors/Waypoint_BP.Waypoint_BP_C"
local terminal_class = "/Game/Classes/Actors/Terminal_BP.Terminal_BP_C"
local key_class = "/Game/Classes/Items/Key_Items/KeyCard_NFTA_Old_BP.KeyCard_NFTA_Old_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local deck_asset = "/Game/Assets/StaticMeshes/Mesh_Structures/Structure_AssemblyComponents/SM_Prop_Elevator_02.SM_Prop_Elevator_02"
local item_class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local lower, upper = {X = -1780, Y = 0, Z = 0}, {X = -1780, Y = 0, Z = 2400}
local west_waypoint = {X = -2132.16, Y = 1053.62, Z = 2400}
local route_targets = {
    ["route-west-landing"] = {X = -1990, Y = 64, Z = 2400},
    ["route-east-stairs"] = {X = 2217.16, Y = -2277.82, Z = 2410},
    ["route-upper-stairs"] = {X = 1550, Y = 2150, Z = 4510},
    ["route-control-room"] = {X = -534, Y = -1, Z = 5410},
    ["route-catwalk"] = {X = 0, Y = 4810, Z = 6610},
}
local actor_scope, is_client, cargo

local function refuse(reason) error("native_datasilo_lift_" .. reason, 0) end
local function valid(object) return not not (object and object:IsValid()) end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) <= 10000000 end

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

local function near_angle(a, b, tolerance)
    return math.abs((a - b + 180) % 360 - 180) <= tolerance
end

local function actor(value, class, name, world)
    return exact(value, class) and value:GetFullName() == name and not value:IsActorBeingDestroyed()
        and same(value:GetWorld(), world) and same(value:GetOuter(), world.PersistentLevel)
end

local function owned(value, owner, class)
    return exact(value, "/Script/Engine." .. class) and same(value:GetOwner(), owner)
end

local function lift_component(value, owner, name, class)
    return owned(value, owner, class) and value:GetFullName() == class .. " " .. lift_path .. "." .. name
end

local function role(state, phase)
    if not enabled or state.map ~= map or not state.listening then return end
    if phase == "hosting" and state.server == false then return "host" end
    if phase == "connected" and state.server == true then return "guest" end
end

local function world_scope(world, peer)
    if not valid(world) or world:GetFullName() ~= "World " .. world_path then return end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) then return end
    local connection = driver.ServerConnection
    if peer == "host" then return not valid(connection) end
    return is_client and is_client() and valid(connection) and same(connection.Driver, driver)
end

local function guard_available()
    return type(SilverfishDataSiloLiftGuardAvailable) == "function" and SilverfishDataSiloLiftGuardAvailable() == true
end

local function collision(value)
    local mode = math.tointeger(value:GetCollisionEnabled())
    if mode and mode >= 0 and mode <= 5 then return mode end
end

local function has_query(mode)
    return mode == 1 or mode == 3 or mode == 5
end

local function scene(world, peer)
    if not world_scope(world, peer) then return end
    local current = StaticFindObject(lift_path)
    if not actor(current, lift_class, lift_name, world) or current.bNetLoadOnClient ~= true
        or current.TravelSpeed ~= 300 or not same(current.KeyItem, StaticFindObject(key_class)) then return end
    local root, deck, button = current.RootComponent, current.SM_Prop_Elevator_01, current.Box
    if not lift_component(root, current, "DefaultSceneRoot", "SceneComponent")
        or not same(root, current.DefaultSceneRoot)
        or not lift_component(deck, current, "SM_Prop_Elevator_01", "StaticMeshComponent")
        or not same(deck.StaticMesh, StaticFindObject(deck_asset))
        or not same(deck:GetAttachParent(), root)
        or not lift_component(button, current, "Box", "BoxComponent")
        or not same(button:GetAttachParent(), deck) then return end

    local a, b = current.Stop_A, current.Stop_B
    if not actor(a, waypoint_class, "Waypoint_BP_C " .. persistent .. "Waypoint_BP_C_1", world)
        or not actor(b, waypoint_class, "Waypoint_BP_C " .. persistent .. "Waypoint_BP_C_2", world)
        or not near(point(a:K2_GetActorLocation()), lower, 1)
        or not near(point(b:K2_GetActorLocation()), upper, 1) then return end

    local position, angle = point(current:K2_GetActorLocation()), rotation(current:K2_GetActorRotation())
    local deck_position, button_position = point(deck:K2_GetComponentLocation()), point(button:K2_GetComponentLocation())
    if not position or not angle or not near_angle(angle.Pitch, 0, .15) or not near_angle(angle.Roll, 0, .15)
        or not near_angle(angle.Yaw, 180, .15)
        or not near(position, {X = lower.X, Y = lower.Y, Z = position.Z}, 3)
        or position.Z < lower.Z - 3 or position.Z > upper.Z + 3
        or not near(position, deck_position, 3)
        or not near(button_position, {X = position.X + 59.185, Y = position.Y, Z = position.Z + 94.363}, 3) then return end

    local stop = near(position, lower, 3) and "lower" or near(position, upper, 3) and "upper" or "moving"
    local deck_collision, button_collision = collision(deck), collision(button)
    local local_role = math.tointeger(current:GetLocalRole())
    if not deck_collision or not button_collision or not local_role or local_role < 0 or local_role > 3
        or type(current.bReplicates) ~= "boolean" or type(current.bReplicateMovement) ~= "boolean" then return end
    return {world = world, actor = current, root = root, deck = deck, button = button, peer = peer, stop = stop,
        position = position, deck_position = deck_position, button_position = button_position,
        deck_collision = deck_collision, button_collision = button_collision, local_role = local_role}
end

local function binding(current)
    if not actor_scope or current.peer ~= actor_scope.peer or not same(current.world, actor_scope.world)
        or not same(current.actor, actor_scope.actor) or not same(current.root, actor_scope.root)
        or not same(current.deck, actor_scope.deck) or not same(current.button, actor_scope.button) then
        actor_scope = {world = current.world, actor = current.actor, root = current.root, deck = current.deck,
            button = current.button, peer = current.peer}
    end
    return actor_scope
end

local function host_status(current)
    local scoped = binding(current)
    local actor = current.actor
    if not guard_available() or scoped.failed or current.local_role ~= 3 or not actor:HasAuthority()
        or actor:GetActorEnableCollision() ~= true or not has_query(current.deck_collision)
        or not has_query(current.button_collision) then return "replication_unverified" end
    if not scoped.activated then
        local prior_replicates, prior_movement = actor.bReplicates, actor.bReplicateMovement
        local ok = pcall(function()
            if not prior_replicates then actor:SetReplicates(true) end
            if not prior_movement and actor.bReplicates == true then actor:SetReplicateMovement(true) end
        end)
        if not ok or actor.bReplicates ~= true or actor.bReplicateMovement ~= true then
            pcall(function()
                if actor.bReplicateMovement ~= prior_movement then actor:SetReplicateMovement(prior_movement) end
                if actor.bReplicates ~= prior_replicates then actor:SetReplicates(prior_replicates) end
            end)
            scoped.failed = true
            return "replication_unverified"
        end
        scoped.activated = true
    elseif actor.bReplicates ~= true or actor.bReplicateMovement ~= true then
        scoped.failed = true
        return "replication_unverified"
    end
    if scoped.last_stop ~= current.stop then
        actor:ForceNetUpdate()
        scoped.last_stop = current.stop
    end
    return "active"
end

local function guest_status(current)
    binding(current)
    if not guard_available() or current.actor:GetActorEnableCollision() ~= true
        or not has_query(current.deck_collision) then return "guard_unverified" end
    -- The Box serves the button alone. The deck remains a collidable movement base.
    if current.button_collision ~= 0 then current.button:SetCollisionEnabled(0) end
    if current.button:GetCollisionEnabled() ~= 0 then return "guard_unverified" end
    if current.local_role ~= 1 or current.actor.bReplicateMovement ~= true then return "awaiting_replica" end
    return "guarded"
end

local function guard_guest(world)
    local current = scene(world, "guest")
    if current then guest_status(current) end
end

local function release_cargo()
    if not cargo then return end
    local core, frame, deck = cargo.core, cargo.frame, cargo.deck
    if not valid(core) or core:IsActorBeingDestroyed() or not same(core.RootComponent, frame) then
        cargo = nil
        return
    end
    if same(frame:GetAttachParent(), deck) then
        core:K2_DetachFromActor(1, 1, 1)
        if same(frame:GetAttachParent(), deck) then return end
    end
    if not valid(frame:GetAttachParent()) then
        frame:SetSimulatePhysics(true)
        if frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then return end
        core:ForceNetUpdate()
    end
    cargo = nil
end

local function loose_core(state, phase)
    local stock = mindcore.scene(state, phase)
    local core, frame = stock.core, stock.frame
    if stock.status ~= 0 or stock.installed or core.bReplicates ~= true or core.bReplicateMovement ~= true
        or core.BeingHeld ~= false or valid(core["Holding Actor"]) or core:GetActorEnableCollision() ~= true
        or valid(frame:GetAttachParent()) or frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then return end
    for _, detail in ipairs(state.pawns) do
        local pawn = detail.pawn
        if valid(pawn) and same(pawn:GetWorld(), stock.world) then
            local handle = pawn.PhysicsHandle
            if same(pawn.HeldObject, core)
                or (owned(handle, pawn, "PhysicsHandleComponent") and same(handle:GetGrabbedComponent(), frame)) then
                return
            end
        end
    end
    stock.position = point(core:K2_GetActorLocation())
    if stock.position then return stock end
end

local function in_cabin(position, deck)
    local x, y, z = position.X - deck.X, position.Y - deck.Y, position.Z - deck.Z
    return x > 83 and x < 138 and math.abs(y) < 48 and z > 22 and z < 160
end

local function sync_cargo(current, scoped, state, phase)
    if cargo then
        if current.stop == "moving" and same(current.deck, cargo.deck) and same(cargo.frame:GetAttachParent(), cargo.deck) then
            return
        end
        release_cargo()
        if cargo then return end
    end
    local prior = scoped.parked_core
    scoped.parked_core = nil
    if current.stop == "moving" and not (prior and prior.settled) then return end
    local stock = loose_core(state, phase)
    if not stock or not in_cabin(stock.position, current.deck_position) then return end
    if current.stop ~= "moving" then
        stock.settled = prior and prior.stop == current.stop and same(stock.frame, prior.frame)
            and near(stock.position, prior.position, 1.5)
        stock.stop, stock.deck_position = current.stop, current.deck_position
        scoped.parked_core = stock
        return
    end
    local expected = {X = prior.position.X, Y = prior.position.Y,
        Z = prior.position.Z + current.deck_position.Z - prior.deck_position.Z}
    if not same(stock.frame, prior.frame) or not near(stock.position, expected, 18) then return end
    -- Carry only the settled, authored Core with the deck until the next stop.
    -- Keep the pose and collisions, and hand the body back to stock physics at the destination.
    cargo = {core = stock.core, frame = stock.frame, deck = current.deck}
    local ok, attached = pcall(function()
        stock.frame:SetSimulatePhysics(false)
        if stock.frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= false then return false end
        return stock.core:K2_AttachToComponent(current.deck, FName("None", EFindName.FNAME_Find),
            1, 1, 1, false) and same(stock.frame:GetAttachParent(), current.deck)
    end)
    if not ok or not attached then
        release_cargo()
        return
    end
    stock.core:ForceNetUpdate()
end

function lift.start(client_role)
    if not enabled or is_client or type(client_role) ~= "function" then return end
    is_client = client_role
    if type(RegisterLoadMapPostHook) == "function" then
        RegisterLoadMapPostHook(function(_, loaded_world)
            pcall(function() guard_guest(loaded_world:get()) end)
        end)
    end
    if type(RegisterBeginPlayPreHook) == "function" then
        RegisterBeginPlayPreHook(function(context)
            pcall(function()
                if not is_client() then return end
                local candidate = context:get()
                if valid(candidate) and candidate:GetFullName() == lift_name then guard_guest(candidate:GetWorld()) end
            end)
        end)
    end
end

function lift.sync(state, phase)
    local peer = role(state, phase)
    if cargo and peer ~= "host" then pcall(release_cargo) end
    if not peer then return end
    pcall(function()
        local current = scene(state.world, peer)
        if not current then
            release_cargo()
            return
        end
        local scoped = binding(current)
        scoped.status = (peer == "host" and host_status or guest_status)(current)
        if scoped.status == "active" then
            sync_cargo(current, scoped, state, phase)
        elseif peer == "host" then
            scoped.parked_core = nil
            release_cargo()
        end
    end)
end

local function coordinates(position)
    return string.format("%.3f", position.X), string.format("%.3f", position.Y), string.format("%.3f", position.Z)
end

local function local_pawn(state, peer)
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or detail.name ~= pawn:GetFullName() or pawn:IsActorBeingDestroyed()
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or pawn:HasAuthority() ~= (peer == "host") or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() then return end
    return pawn, controller
end

local function rider(current, pawn, slot, row)
    if not exact(pawn, pawn_class) or not same(pawn:GetWorld(), current.world)
        or pawn:IsActorBeingDestroyed() then return end
    local based = pawn.BasedMovement
    if not based or not same(based.MovementBase, current.deck) then return end
    local capsule = pawn.CapsuleComponent
    if not owned(capsule, pawn, "CapsuleComponent") or not same(pawn.RootComponent, capsule) then return end
    local position = point(capsule:K2_GetComponentLocation())
    if position then
        return row("native-datasilo-lift-rider", map, slot, pawn:GetFullName(), current.deck:GetFullName(),
            coordinates(position))
    end
end

local function guest_pawn(state, phase, detail)
    local pawn, detached = guest_save.exact_item_context(state, phase, detail.name)
    if same(pawn, detail.pawn) and same(pawn.Save, detached) then return pawn end
end

local function append_rider(rows, current, slot, row, find)
    local ok, observed = pcall(function() return rider(current, find(), slot, row) end)
    if ok and observed then rows[#rows + 1] = observed end
end

function lift.rows(state, phase, row)
    local peer = role(state, phase)
    if not peer then return {} end
    local ok, result = pcall(function()
        local current = scene(state.world, peer)
        if not current or not actor_scope or not same(actor_scope.actor, current.actor) or not actor_scope.status then
            return {}
        end
        local position, deck, button = current.position, current.deck_position, current.button_position
        local x, y, z = coordinates(position)
        local dx, dy, dz = coordinates(deck)
        local status = actor_scope.status
        if peer == "guest" and current.button_collision ~= 0 then status = "guard_unverified" end
        local rows = {row("native-datasilo-lift", map, peer, status, lift_name, current.local_role,
            current.actor.bReplicates and 1 or 0, current.actor.bReplicateMovement and 1 or 0,
            current.deck_collision, current.button_collision, current.stop, x, y, z, dx, dy, dz, coordinates(button))}
        append_rider(rows, current, "local", row, function() return local_pawn(state, peer) end)
        if peer == "host" then
            for _, guest in ipairs(state.guests) do
                append_rider(rows, current, guest.slot, row, function() return guest_pawn(state, phase, guest) end)
            end
        end
        return rows
    end)
    return ok and result or {}
end

local function host_save(state, pawn)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
        or not same(pawn.Save, save) or not same(instance["As Player FP"], pawn)
        or not same(pawn:GetWorld(), state.world) then refuse("fixture_canonical_save") end
    return save
end

local function stock_key(save, class)
    local backpack = save.PlayerBackpack
    local count = backpack and math.tointeger(backpack:GetArrayNum())
    if not count or count < 1 or count > 1024 then return false end
    for index = 1, count do
        if same(backpack[index][item_class_field], class) then return true end
    end
    return false
end

local function stock_power(current, pawn)
    local path = persistent .. "Terminal_BP_C_1"
    local terminal = StaticFindObject(path)
    if not actor(terminal, terminal_class, "Terminal_BP_C " .. path, current.world)
        or not terminal:HasAuthority() then refuse("fixture_terminal") end
    local connections = terminal.Active_Actors
    local link = connections and connections:Contains(current.actor) and connections:Find(current.actor):get()
    if type(link) == "userdata" then link = link:ToString() end
    if link ~= "LIFT01" then refuse("fixture_lift_power_binding") end
    current.actor:Send_PowerSignal(true, pawn)
end

local function stock_button(current, pawn, save)
    local camera, press = pawn.Camera, SilverfishProbeNativeInteractInput
    if not stock_key(save, current.actor.KeyItem) then refuse("fixture_old_nfta_key_required") end
    if not owned(camera, pawn, "CameraComponent") or type(press) ~= "function"
        or type(SilverfishTakeNativeInteractPress) ~= "function" then refuse("fixture_stock_input_unavailable") end
    local prior = rotation(camera.RelativeRotation)
    local eye, target = point(camera:K2_GetComponentLocation()), current.button_position
    local reach = pawn.InteractionRange
    if not prior or not eye or not finite(reach) or reach <= 0 or reach > 1000
        or not near(point(pawn:K2_GetActorLocation()), target, reach) then refuse("fixture_button_out_of_reach") end
    local dx, dy, dz = target.X-eye.X, target.Y-eye.Y, target.Z-eye.Z
    local direction = {Pitch = math.deg(math.atan(dz, math.sqrt(dx^2 + dy^2))),
        Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
    local ok, failure = pcall(function()
        camera:K2_SetWorldRotation(direction, false, {}, true)
        pawn:LookTrace()
        if pawn.CanInteract ~= true or not same(pawn.InteractionTarget_Component, current.button) then
            refuse("fixture_button_stock_focus")
        end
        local accepted, status, sequence = press(pawn:GetAddress(), true)
        if accepted ~= true then refuse("fixture_button_input:" .. tostring(status)) end
        if status == "stock_started_consumed" and sequence == 0 then return end
        local received, owner = SilverfishTakeNativeInteractPress(pawn:GetAddress())
        if status ~= "stock_started" or not math.tointeger(sequence) or sequence < 1
            or received ~= sequence or owner ~= pawn:GetAddress() then refuse("fixture_button_input_readback") end
    end)
    camera:K2_SetRelativeRotation(prior, false, {}, true)
    pawn:LookTrace()
    local restored = rotation(camera.RelativeRotation)
    if not restored or not near_angle(restored.Pitch, prior.Pitch, .05)
        or not near_angle(restored.Yaw, prior.Yaw, .05) or not near_angle(restored.Roll, prior.Roll, .05) then
        refuse("fixture_button_camera_restore")
    end
    if not ok then error(failure, 0) end
end

local function guard_probe(state, phase)
    if not guest_fixture or role(state, phase) ~= "guest" then refuse("fixture_guest_guard_scope") end
    local current, pawn = scene(state.world, "guest"), local_pawn(state, "guest")
    if not current or not pawn or pawn.Dead ~= false or players.is_downed(pawn) ~= false
        or current.stop == "moving" or current.local_role ~= 1 or current.button_collision ~= 0
        or not actor_scope or not same(actor_scope.actor, current.actor) or actor_scope.status ~= "guarded" then
        refuse("fixture_guest_guard_not_ready")
    end
    current.actor:Send_PowerSignal(true, pawn)
    if not near(point(current.actor:K2_GetActorLocation()), current.position, .02) then
        refuse("fixture_guest_guard_unexpected_movement")
    end
    return "stock Data Silo lift guest guard probed"
end

local function navigation_probe(state, phase, target)
    if not fixtures or role(state, phase) ~= "host" or not world_scope(state.world, "host") then
        refuse("navigation_scope")
    end
    local pawn = local_pawn(state, "host")
    local navigation = StaticFindObject("/Script/NavigationSystem.Default__NavigationSystemV1")
    local is_path_valid = StaticFindObject("/Script/NavigationSystem.NavigationPath:IsValid")
    if not pawn or not valid(navigation) or not valid(is_path_valid) then refuse("navigation_unavailable") end

    local function project(location, label)
        local projected = {}
        local extent = {X = 100, Y = 100, Z = 130}
        local found = navigation:K2_ProjectPointToNavigation(pawn, location, projected, nil, nil, extent)
        local result = point(projected)
        if found ~= true or not near(result, location, 190) then refuse(label) end
        return result
    end

    local from = project(west_waypoint, "navigation_start_unavailable")
    local destination = project(route_targets[target], "navigation_destination_unavailable")
    local path = navigation:FindPathToLocationSynchronously(pawn, from, destination, nil, nil)
    if not exact(path, "/Script/NavigationSystem.NavigationPath") or path:CallFunction(is_path_valid) ~= true then
        refuse("navigation_no_path")
    end
    local points, encoded = path.PathPoints, {}
    local count = points:GetArrayNum()
    if count < 2 or count > 100 then refuse("navigation_path_size") end
    for index = 1, count do
        local position = point(points[index])
        if not position then refuse("navigation_path_point") end
        encoded[index] = string.format("%.2f,%.2f,%.2f", position.X, position.Y, position.Z)
    end
    return target .. " " .. (path:IsPartial() and "partial" or "complete") .. " " .. table.concat(encoded, ";")
end

function lift.command(fields, state, phase)
    if fields[2] ~= "native-datasilo-lift-fixture" then return end
    local target = fields[3]
    if #fields ~= 3 or not guard_available() then refuse("fixture_scope") end
    if target == "guard" then return guard_probe(state, phase) end
    if route_targets[target] then return navigation_probe(state, phase, target) end
    if not fixtures or role(state, phase) ~= "host"
        or (target ~= "power" and target ~= "button") then refuse("fixture_scope") end
    local current, pawn, controller = scene(state.world, "host"), local_pawn(state, "host")
    if not current or not pawn or pawn.Dead ~= false or players.is_downed(pawn) ~= false
        or controller.Loading ~= false or not controller.MenuStack or controller.MenuStack:GetArrayNum() ~= 0
        or current.stop == "moving" or not actor_scope or not same(actor_scope.actor, current.actor)
        or actor_scope.status ~= "active" then refuse("fixture_not_ready") end
    local save = host_save(state, pawn)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local now = statics:GetRealTimeSeconds(pawn)
    if not finite(now) or (actor_scope.last_input and now - actor_scope.last_input < 1) then
        refuse("fixture_input_busy")
    end
    actor_scope.last_input = now
    if target == "power" then stock_power(current, pawn) else stock_button(current, pawn, save) end
    return "stock Data Silo lift " .. target .. " dispatched"
end

function lift.clear()
    -- The retiring co-op world keeps its client button blocked as it unloads.
    pcall(release_cargo)
    actor_scope = nil
end

return lift
