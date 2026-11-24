local players = {}
local native_downed_ai = require("native_downed_ai")
local native_bag_projection = require("native_bag_projection")
local native_guest_drop_projection = require("native_guest_drop_projection")
local native_authored_pickup = require("native_authored_pickup")
local native_enemy_shell_view = require("native_enemy_shell_view")
local native_stock_door = require("native_stock_door")
local native_stock_switch = require("native_stock_switch")
players.critters = require("native_critters")

local player_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local interact_path = player_path .. ":InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19"
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local system_library_path = "/Script/Engine.Default__KismetSystemLibrary"
local input_subsystem_path = "/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem"
local subsystem_library_path = "/Script/Engine.Default__SubsystemBlueprintLibrary"
local gameplay_input_path = "/Game/Classes/Player/Player_Inputs/IMC_FP.IMC_FP"
local inventory_path = "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"
local inventory_fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local inventory_fixture_map = "/Game/Classes/Maps/PlaytestMap_01"
local vector_zero = { X = 0, Y = 0, Z = 0 }
local mapping_options = { bIgnoreAllPressedKeysUntilRelease = true, bForceImmediately = true }
local input_labels = {
    IA_Look = "look", IA_Jump = "jump", IA_Inventory = "inventory", IA_Use_Primary = "primary",
    IA_Use_Secondary = "secondary", IA_Reload = "reload", IA_Interact = "interact",
}
local input_fields = { "look", "move", "jump", "inventory", "primary", "secondary", "reload", "interact" }
local downed_movement_fields = {
    "MaxWalkSpeed", "MaxWalkSpeedCrouched", "JumpZVelocity",
}
local downed_pawn_fields = {
    "Speed_Walking", "Speed_Crouching", "Speed_Sprinting", "JumpMaxCount", "IsWeaponBlocked",
}

local valid_slots = { ["local"] = true, guest = true, guest2 = true, guest3 = true, guest4 = true }
local desired = {}
local slot_pawns = {}
local applied = {}
local markers = {}
local marker_frame
local location_markers = {}
local local_player
local guest_sprints = {}
local local_input_status
local standing_mapping
local restoring_gate
local walking
local walk_frame_action, walk_frame_clock, last_walk_frame
local input_file
local input_sequence = 0
local interact_hook_checked = false
local native_interact_address

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function active(phase)
    return phase == "hosting" or phase == "connected"
end

local function sync_guest_sprints(state, slots, phase)
    if phase ~= "hosting" then
        guest_sprints = {}
        return
    end
    local instance = valid(state.world) and state.world.OwningGameInstance
    local canonical = valid(instance) and instance.SaveSlot_Ref
    for slot, details in pairs(slots) do
        local pawn = details.pawn
        local entry = guest_sprints[details.name]
        if slot ~= "local" and entry and valid(canonical) and valid(pawn.Save) and not same(canonical, pawn.Save)
            and not pawn:IsLocallyControlled() and pawn.Dead == false and valid(pawn.CharacterMovement) then
            if desired[details.name] or applied[details.name] then
                entry.applied = nil
            else
                local sprint = entry.sprint and pawn.MoveInputs_Enabled and not pawn.Stunned
                    and pawn.CharacterMovement.MovementMode ~= 0
                if sprint then
                    local can = {}
                    pawn:Check_CanSprint(can)
                    sprint = can.CanSprint == true
                end
                sprint = sprint == true
                if entry.applied ~= sprint or pawn.Sprinting ~= sprint then
                    pawn:Action_Sprint(sprint)
                    entry.applied = sprint
                end
            end
        end
    end
end

local function write_interaction(pawn, pickup_source, pressed_component)
    if not input_file or not local_player or desired[local_player.name] or not same(pawn, local_player.pawn)
        or not pawn:IsLocallyControlled() then return end
    local target = pickup_source or native_stock_door.focused_target(pawn, pressed_component)
        or native_stock_switch.focused_target(pawn, pressed_component)
        or players.critters.focused_target(pawn, pressed_component)
        or native_bag_projection.focused_target(pawn) or native_enemy_shell_view.focused_target(pawn)
    input_sequence = input_sequence + 1
    local record = "interact\t" .. tostring(input_sequence) .. (target and "\t" .. target or "") .. "\n"
    if type(SilverfishEnqueueLocalBridgeInput) == "function" and SilverfishEnqueueLocalBridgeInput(record) then
        return target
    end
    local file = io.open(input_file, "ab")
    if not file then return end
    local written = file:write(record)
    local closed = file:close()
    if written and closed then return target end
end

local function drain_native_interact()
    local address = native_interact_address
    local pawn = local_player and local_player.pawn
    if not address or not valid(pawn) or pawn:GetAddress() ~= address
        or pawn:IsActorBeingDestroyed() or not pawn:IsLocallyControlled() then return end
    for _ = 1, 8 do
        local source = native_authored_pickup.take_press(pawn)
        if not source then break end
        write_interaction(pawn, source)
    end
    local stack_drop = native_guest_drop_projection.take_press(pawn)
    if stack_drop then write_interaction(pawn, stack_drop) end
    local valve = SilverfishTakeNativeSubwayValvePress
    if type(valve) == "function" and valve(address) then write_interaction(pawn, "subway-valve") end
    local lantern = SilverfishTakeNativeLanternPress
    if type(lantern) == "function" and lantern(address) and valid(pawn.Camera) then
        local aim = pawn.Camera:GetForwardVector()
        if aim and type(aim.X) == "number" and type(aim.Y) == "number" and type(aim.Z) == "number" then
            local pitch = math.deg(math.atan(aim.Z, math.sqrt(aim.X^2 + aim.Y^2)))
            local yaw = math.deg(math.atan(aim.Y, aim.X))
            write_interaction(pawn, string.format("observatory-lantern:%.2f:%.2f", pitch, yaw))
        end
    end
    local target
    for _ = 1, 8 do
        local sequence, matched, pressed_component = SilverfishTakeNativeInteractPress(address)
        if matched ~= address or type(sequence) ~= "number" or sequence < 1 then break end
        target = write_interaction(pawn, nil, pressed_component)
    end
    return target
end

local function sync_native_interact(phase)
    if type(SilverfishSetNativeInteractScope) ~= "function" or type(SilverfishTakeNativeInteractPress) ~= "function" then
        return
    end
    local pawn = local_player and local_player.pawn
    if not active(phase) or not valid(pawn) then
        if native_interact_address then SilverfishClearNativeInteractScope() end
        native_interact_address = nil
        return
    end
    local address = pawn:GetAddress()
    native_interact_address = SilverfishSetNativeInteractScope(address, phase == "hosting") and address or nil
    return drain_native_interact()
end

local function roster(state, phase)
    local slots, names = {}, {}
    local world = state.world
    if not active(phase) or not valid(world) then return slots, names end

    for _, details in ipairs(state.pawns) do
        local slot = details.slot or details.kind
        local pawn = details.pawn
        local name = details.name
        if valid_slots[slot] and valid(pawn) and same(pawn:GetWorld(), world)
            and not pawn:IsActorBeingDestroyed() and type(name) == "string" and name:match("^Player_FP_C ")
            and name == pawn:GetFullName()
            and (phase ~= "hosting" or pawn:HasAuthority()) then
            slots[slot] = slots[slot] == nil and details or false
            names[name] = names[name] == nil and details or false
        end
    end
    for slot, details in pairs(slots) do
        if not details or not names[details.name] then slots[slot] = nil end
    end
    return slots, names
end

local function current_identity(record, details, world)
    return details and same(record.pawn, details.pawn) and same(record.world, world)
end

local function writable(record)
    local pawn = record.pawn
    return valid(pawn) and not pawn:IsActorBeingDestroyed() and same(pawn:GetWorld(), record.world)
        and pawn:GetFullName() == record.name and (pawn:HasAuthority() or pawn:IsLocallyControlled())
end

local function now(pawn)
    local statics = StaticFindObject(gameplay_statics_path)
    return statics:GetRealTimeSeconds(pawn)
end

local function cancel_walk_frames()
    if walk_frame_action then
        CancelDelayedAction(walk_frame_action)
        walk_frame_action = nil
    end
end

local function stop_walk()
    walking = nil
    cancel_walk_frames()
end

local function walk_scope(record)
    local pawn, controller, world = record.pawn, record.controller, record.world
    return valid(pawn) and not pawn:IsActorBeingDestroyed() and pawn.Dead == false
        and pawn:GetFullName() == record.name and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world)
        and valid(controller) and controller:IsLocalController() and same(pawn.Controller, controller)
        and same(controller.Pawn, pawn) and same(controller:GetWorld(), world)
end

local function add_walk_input(record)
    local pawn = record.pawn
    local movement = pawn.CharacterMovement
    if not valid(movement) or movement.MovementMode == 0 or pawn:IsMoveInputIgnored() then return end
    if walk_frame_clock then
        local frame = walk_frame_clock:GetFrameCount()
        if frame == last_walk_frame then return end
        last_walk_frame = frame
    end
    pawn:AddMovementInput(record.world_direction, 1.0, false)
end

local function apply_walk()
    local record = walking
    if not record or not local_player or local_player.name ~= record.name or not same(local_player.pawn, record.pawn)
        or not walk_scope(record) or now(record.pawn) >= record.deadline then
        stop_walk()
        return
    end
    add_walk_input(record)
end

local function movement_failure(failure)
    stop_walk()
    print("[SilverfishCoop] Player movement update failed: " .. tostring(failure) .. "\n")
end

local function start_walk_frames()
    if walk_frame_action or EngineTickAvailable ~= true or type(LoopInGameThreadAfterFrames) ~= "function"
        or type(CancelDelayedAction) ~= "function" then return end
    local clock = StaticFindObject(system_library_path)
    if not valid(clock) then return end
    local measured, frame = pcall(function() return clock:GetFrameCount() end)
    if not measured or math.type(frame) ~= "integer" then return end
    local scheduled, handle = pcall(LoopInGameThreadAfterFrames, 1, function()
        local ok, failure = pcall(apply_walk)
        if not ok then movement_failure(failure) end
    end)
    if scheduled and math.type(handle) == "integer" then walk_frame_action, walk_frame_clock = handle, clock end
end

local function read_fields(object, names)
    local values = {}
    for _, name in ipairs(names) do values[name] = object[name] end
    return values
end

local function restore_fields(object, values)
    for name, value in pairs(values) do object[name] = value end
end

local function retain_mapping(action)
    local name = action:GetFName():ToString()
    return name == "IA_Look" or name:match("^IA_Move") ~= nil
end

local function prepare_mapping_gate(controller)
    local library = StaticFindObject(subsystem_library_path)
    local subclass = StaticFindObject(input_subsystem_path)
    local original = StaticFindObject(gameplay_input_path)
    if not valid(library) or not valid(subclass) or not valid(original) then
        return nil
    end
    local subsystem = library:GetLocalPlayerSubSystemFromPlayerController(controller, subclass)
    if not valid(subsystem) or not valid(controller.PlayerInput) then return nil end
    local player_input = controller.PlayerInput
    local contexts = player_input.AppliedInputContexts
    if not contexts:Contains(original) then return nil end
    local priority = contexts:Find(original):get()
    local clone = StaticConstructObject(original:GetClass(), controller, 0, 0x40, 0, false, false, original)
    if not valid(clone) or same(original, clone) or #clone.Mappings ~= #original.Mappings then return nil end

    local allowed, blocked = {}, {}
    for index = 1, #clone.Mappings do
        local action = clone.Mappings[index].Action
        if not valid(action) then return nil end
        local group = retain_mapping(action) and allowed or blocked
        group[action:GetAddress()] = action
    end
    local original_count = #original.Mappings
    for _, action in pairs(blocked) do clone:UnmapAllKeysFromAction(action) end
    if #clone.Mappings < 1 or #clone.Mappings >= original_count or #original.Mappings ~= original_count then
        return nil
    end
    return { subsystem = subsystem, original = original, clone = clone, priority = priority,
        controller = controller, player_input = player_input, allowed = allowed, blocked = blocked }
end

local function apply_mapping_gate(gate)
    local contexts = gate.player_input.AppliedInputContexts
    if not contexts:Contains(gate.clone) then
        gate.subsystem:AddMappingContext(gate.clone, gate.priority, mapping_options)
    end
    if contexts:Contains(gate.original) then gate.subsystem:RemoveMappingContext(gate.original, mapping_options) end
end

local function remove_mapping_gate(gate)
    if not valid(gate.subsystem) then return true end
    if not valid(gate.player_input) or not valid(gate.controller) or not same(gate.controller.PlayerInput, gate.player_input) then
        if valid(gate.clone) then gate.subsystem:RemoveMappingContext(gate.clone, mapping_options) end
        return true
    end
    local contexts = gate.player_input.AppliedInputContexts
    if not contexts:Contains(gate.original) then gate.subsystem:AddMappingContext(gate.original, gate.priority, mapping_options) end
    if contexts:Contains(gate.clone) then gate.subsystem:RemoveMappingContext(gate.clone, mapping_options) end
    return contexts:Contains(gate.original) and not contexts:Contains(gate.clone)
end

local function observe_mapping(mappings, count, stock, reduced)
    if count > 512 then return end
    local active, flags = {}, {}
    for _, name in ipairs(input_fields) do flags[name] = false end
    for index = 1, count do
        local action = mappings[index].Action
        if valid(action) then
            active[action:GetAddress()] = true
            local name = action:GetFName():ToString()
            local label = input_labels[name] or (name:match("^IA_Move") and "move")
            if label then flags[label] = true end
        end
    end
    return {stock = stock, reduced = reduced, flags = flags, active = active}
end

local function verified_mapping(observed, gate, downed)
    if not observed or not observed.flags.look or not observed.flags.move then return false end
    if not downed then
        if not observed.stock or observed.reduced then return false end
        for _, label in ipairs(input_fields) do if not observed.flags[label] then return false end end
        return true
    end
    if not gate or observed.stock or not observed.reduced then return false end
    for address in pairs(gate.allowed) do if not observed.active[address] then return false end end
    for address in pairs(gate.blocked) do if observed.active[address] then return false end end
    for _, label in ipairs(input_fields) do
        if label ~= "look" and label ~= "move" and observed.flags[label] then return false end
    end
    return true
end

local function read_local_mapping(controller, pawn, gate, downed)
    local original, player_input = StaticFindObject(gameplay_input_path), controller.PlayerInput
    if not valid(original) or not valid(player_input) then
        standing_mapping = nil
        return nil, false
    end
    local contexts, mappings = player_input.AppliedInputContexts, player_input.EnhancedActionMappings
    local stock, reduced = contexts:Contains(original), gate ~= nil and contexts:Contains(gate.clone)
    local count = #mappings
    local now, cached = os.clock(), standing_mapping

    -- The standing readback is diagnostic. Refresh it periodically for in-place key
    -- changes, and sample every time the downed gate is applied or being restored.
    if not downed and not gate and stock and cached and now < cached.refresh
        and count == cached.count and same(pawn, cached.pawn) and same(player_input, cached.player_input) then
        return cached.observed, true
    end
    standing_mapping = nil
    local observed = observe_mapping(mappings, count, stock, reduced)
    local verified = verified_mapping(observed, gate, downed)
    if verified and not downed and not gate then
        standing_mapping = {pawn = pawn, player_input = player_input, count = count, observed = observed,
            refresh = now + 1}
    end
    return observed, verified
end

local function host_inventory_state(controller, pawn, record)
    local stack = controller.MenuStack
    local count = stack:GetArrayNum()
    local clear = record and record.inventory_closed and "closed" or "clear"
    if count == 0 then return clear end
    local class = StaticFindObject(inventory_path)
    if not valid(class) or count > 32 then return "waiting" end
    local menu = stack[count]
    if count ~= 1 or not valid(menu) or not same(menu:GetClass(), class) then
        for index = 1, count do
            local widget = stack[index]
            if valid(widget) and same(widget:GetClass(), class) then return "waiting" end
        end
        return clear
    end
    if not same(menu:GetOwningPlayer(), controller) or not same(menu:GetOwningPlayerPawn(), pawn)
        or not menu:IsInViewport() then return "waiting" end
    if not record then return "open", menu end
    local result = {}
    controller:CloseMenu(result)
    if result.Success ~= true or controller.MenuStack:GetArrayNum() ~= 0
        or (valid(menu) and menu:IsInViewport()) then return "waiting" end
    record.inventory_closed = true
    return "closed"
end

local function local_input_controller(state, phase, details)
    local controller, pawn = state.controller, details and details.pawn
    if not active(phase) or not valid(pawn) or not valid(controller)
        or not same(details.controller_object, controller) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(controller:GetWorld(), state.world)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetClass(), StaticFindObject(controller_path))
        or not same(pawn:GetClass(), StaticFindObject(player_path))
        or controller:IsActorBeingDestroyed() or pawn:IsActorBeingDestroyed()
        or not controller:IsLocalController() or not pawn:IsLocallyControlled() then return end
    return controller, pawn
end

local function open_host_inventory_fixture(fields, state, phase)
    local details = state.local_pawn
    local controller, pawn = local_input_controller(state, phase, details)
    if not inventory_fixture or #fields ~= 3 or phase ~= "hosting" or state.map ~= inventory_fixture_map
        or not state.listening or state.server ~= false or not controller or not controller:HasAuthority()
        or not pawn:HasAuthority() or details.name ~= fields[3] or pawn:GetFullName() ~= fields[3]
        or desired[fields[3]] or pawn.Dead ~= false or controller.Loading ~= false
        or controller.MenuStack:GetArrayNum() ~= 0 then error("The exact standing staged host inventory is unavailable", 0) end
    local class = StaticFindObject(inventory_path)
    if not valid(class) then
        LoadAsset(inventory_path)
        class = StaticFindObject(inventory_path)
    end
    if not valid(class) then error("The stock host inventory class is unavailable", 0) end
    local created = {}
    controller:OpenMenu(class, created)
    local status, widget = host_inventory_state(controller, pawn)
    if status ~= "open" or not same(widget, created.CreatedMenu) then
        error("mutation_unverified_stock_host_inventory_fixture", 0)
    end
    return "exact stock host inventory active"
end

local function sync_local_input(state, phase)
    local restore = restoring_gate
    if restore then
        local ok, complete = pcall(remove_mapping_gate, restore)
        if ok and complete then restoring_gate = nil end
    end
    local_input_status = nil
    local details = local_player
    local controller, pawn = local_input_controller(state, phase, details)
    if not controller then
        standing_mapping = nil
        return
    end
    local record, downed_request = applied[details.name], desired[details.name]
    local downed = downed_request ~= nil
    if record then
        record.input_verified = false
        if record.gate and (not same(record.gate.controller, controller)
            or not same(record.gate.player_input, controller.PlayerInput)) then
            restoring_gate, record.gate = record.gate, nil
        end
        if not restoring_gate then
            pcall(function()
                if not record.gate then record.gate = prepare_mapping_gate(controller) end
                if record.gate then apply_mapping_gate(record.gate) end
            end)
        end
    end
    local gate = record and record.gate or restoring_gate
    local ok, observed, verified = pcall(read_local_mapping, controller, pawn, gate, downed)
    if not ok then
        standing_mapping = nil
        observed, verified = nil, false
    end
    if record then record.input_verified = verified end
    local inventory = "not_applicable"
    if phase == "hosting" and state.server == false and controller:HasAuthority() and pawn:HasAuthority() then
        local inspected, result = pcall(host_inventory_state, controller, pawn, downed_request)
        inventory = inspected and result or "waiting"
    end
    local_input_status = { pawn = details.name, state = downed and "downed" or "standing",
        verified = verified, mapping = observed, inventory = inventory }
end

local function apply_downed(record)
    local pawn, movement = record.pawn, record.movement
    if not writable(record) or not valid(movement) then return end
    -- Removing the input mapping can swallow the stock aim-release event.
    if pawn.Aiming then pawn:CancelScope() end
    pawn.HoldingCrouch = true
    pawn.HoldingJump = false
    pawn.Sprinting = false
    pawn.IsWeaponBlocked = true
    pawn.JumpMaxCount = 0
    pawn.Speed_Walking = record.pawn_values.Speed_Walking * 0.5
    pawn.Speed_Crouching = record.pawn_values.Speed_Crouching * 0.5
    pawn.Speed_Sprinting = record.pawn_values.Speed_Sprinting * 0.5
    movement.MaxWalkSpeed = record.speed_cap
    movement.MaxWalkSpeedCrouched = record.speed_cap
    movement.JumpZVelocity = 0
    if pawn.bPressedJump then pawn:StopJumping() end
    if not pawn.bIsCrouched or not movement.bWantsToCrouch then pawn:Crouch(false) end
end

local function release(name)
    local record = applied[name]
    if not record then return end
    if writable(record) and valid(record.movement) then
        restore_fields(record.pawn, record.pawn_values)
        restore_fields(record.movement, record.movement_values)
        record.pawn.HoldingCrouch = record.holding_crouch
        if not record.was_crouched then record.pawn:UnCrouch(false) end
    end
    if record.gate then restoring_gate = record.gate end
    applied[name] = nil
end

local function begin_downed(details, world)
    local movement = details.pawn.CharacterMovement
    if not valid(movement) then return end
    local movement_values = read_fields(movement, downed_movement_fields)
    local pawn_values = read_fields(details.pawn, downed_pawn_fields)
    local baseline_speed = movement_values.MaxWalkSpeedCrouched
    if not finite(baseline_speed) or baseline_speed <= 0 then baseline_speed = pawn_values.Speed_Crouching end
    local record = {
        name = details.name,
        pawn = details.pawn,
        world = world,
        movement = movement,
        movement_values = movement_values,
        pawn_values = pawn_values,
        speed_cap = baseline_speed * 0.5,
        was_crouched = details.pawn.bIsCrouched,
        holding_crouch = details.pawn.HoldingCrouch,
    }
    applied[details.name] = record
    apply_downed(record)
end

local marker_parts = {
    { offset_z = 128, scale_z = 0.27 },
    { offset_z = 104, scale_z = 0.09 },
}
local marker_rotation = { Pitch = 0, Roll = 0, Yaw = 0 }

local function marker_asset(path)
    local asset = StaticFindObject(path)
    if not valid(asset) then
        LoadAsset(path)
        asset = StaticFindObject(path)
    end
    return asset
end

local function marker_position(details, part)
    return { X = details.x, Y = details.y, Z = details.z + part.offset_z }
end

local function clear_marker(marker)
    if not marker then return end
    for _, actor in ipairs(marker.actors) do
        if valid(actor) and not actor:IsActorBeingDestroyed() then
            actor:K2_DestroyActor()
        end
    end
    marker.actors = {}
    marker.visible = false
end

local function create_marker(marker, world, details)
    local class = StaticFindObject("/Script/Engine.StaticMeshActor")
    local mesh = marker_asset("/Engine/BasicShapes/Cube.Cube")
    if not valid(class) or not valid(mesh) then error("Downed marker mesh is unavailable", 0) end
    marker.mesh = mesh
    local material = marker_asset("/Game/Assets/Materials/M_Light_Yellow.M_Light_Yellow")

    -- Spawn an exclamation in the viewer's world. Neither part is attached
    -- to or registered on the replicated pawn that the viewer is observing.
    for _, part in ipairs(marker_parts) do
        local actor = world:SpawnActor(class, marker_position(details, part), marker_rotation)
        if not valid(actor) then error("Downed marker actor did not spawn", 0) end
        marker.actors[#marker.actors + 1] = actor
        if not actor:HasAuthority() or actor.bReplicates ~= false then
            error("Downed marker actor is not local to the viewer", 0)
        end
        local component = actor.StaticMeshComponent
        if not valid(component) then error("Downed marker mesh component is unavailable", 0) end
        component:SetMobility(2)
        actor:SetActorEnableCollision(false)
        component:SetCollisionEnabled(0)
        actor.bCanBeDamaged = false
        if not component:SetStaticMesh(mesh) then error("Downed marker mesh assignment failed", 0) end
        actor:SetActorScale3D({ X = 0.09, Y = 0.09, Z = part.scale_z })
        if valid(material) then component:SetMaterial(0, material) end
    end
    marker.x, marker.y, marker.z = details.x, details.y, details.z
    marker.visible = true
end

local function follow_marker(marker, details)
    local move = math.abs(marker.x - details.x) + math.abs(marker.y - details.y)
        + math.abs(marker.z - details.z) >= 0.5
    for index, actor in ipairs(marker.actors) do
        if not valid(actor) or actor:IsActorBeingDestroyed() then
            error("Downed marker actor is no longer available", 0)
        end
        if move then
            local target = marker_position(details, marker_parts[index])
            actor:K2_TeleportTo(target, marker_rotation)
            local actual = actor:K2_GetActorLocation()
            if math.abs(actual.X - target.X) > 2 or math.abs(actual.Y - target.Y) > 2
                or math.abs(actual.Z - target.Z) > 2 then
                error("Downed marker did not follow its target", 0)
            end
        end
    end
    if move then marker.x, marker.y, marker.z = details.x, details.y, details.z end
end

local function sync_marker(name, details)
    local viewer = local_player and local_player.pawn
    local world = valid(viewer) and viewer:GetWorld() or nil
    local show = details and valid(world) and not same(details.pawn, viewer)
        and finite(details.x) and finite(details.y) and finite(details.z)
    local marker = markers[name]
    if marker and (not show or not current_identity(marker, details, world)) then
        clear_marker(marker)
        markers[name] = nil
        marker = nil
    end
    if not show then return end

    if not marker then
        marker = { pawn = details.pawn, world = world, actors = {}, visible = false }
        markers[name] = marker
        local ok, failure = pcall(create_marker, marker, world, details)
        if not ok then
            clear_marker(marker)
            print("[SilverfishCoop] Downed marker unavailable: " .. tostring(failure) .. "\n")
        end
    elseif marker.visible then
        local ok, failure = pcall(follow_marker, marker, details)
        if not ok then
            clear_marker(marker)
            print("[SilverfishCoop] Downed marker unavailable: " .. tostring(failure) .. "\n")
        end
    end
end

local marker_frame_lifetime = 2.5
local marker_coordinate_limit = 10000000

local function marker_character(value)
    if value == "host" then return true end
    if type(value) ~= "string" or #value ~= 36 then return false end
    local first, second, third, fourth, last = value:match("^([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)$")
    return first and #first == 8 and #second == 4 and #third == 4 and #fourth == 4 and #last == 12
end

local function marker_viewer(state, phase)
    if phase ~= "connected" or state.server ~= true or not state.listening or not valid(state.world) then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not valid(pawn) or not valid(controller) or not same(detail.controller_object, controller)
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or not pawn:IsLocallyControlled() or not controller:IsLocalController() or pawn:HasAuthority()
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed() or detail.name ~= pawn:GetFullName()
        or not same(pawn:GetClass(), StaticFindObject(player_path))
        or not same(controller:GetClass(), StaticFindObject(controller_path)) then return end
    return pawn
end

local function clear_marker_frame()
    for _, marker in pairs(location_markers) do clear_marker(marker) end
    marker_frame, location_markers = nil, {}
end

local function read_location_marker(marker, world, expected)
    if not marker.visible or #marker.actors ~= #marker_parts then return end
    local point
    for index, actor in ipairs(marker.actors) do
        if not valid(actor) or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), world)
            or not actor:HasAuthority() or actor.bReplicates ~= false or actor.bHidden ~= false
            or actor:GetActorEnableCollision() ~= false then return end
        local component = actor.StaticMeshComponent
        if not valid(component) or not same(component:GetOwner(), actor) or not same(component.StaticMesh, marker.mesh)
            or not component:IsVisible() or component:GetCollisionEnabled() ~= 0 then return end
        local part, position = marker_parts[index], actor:K2_GetActorLocation()
        local target, scale = marker_position(expected, part), actor:GetActorScale3D()
        if not finite(position.X) or not finite(position.Y) or not finite(position.Z)
            or math.abs(position.X - target.X) > 2 or math.abs(position.Y - target.Y) > 2
            or math.abs(position.Z - target.Z) > 2 or math.abs(scale.X - 0.09) > 0.005
            or math.abs(scale.Y - 0.09) > 0.005 or math.abs(scale.Z - part.scale_z) > 0.005 then return end
        if not point then point = { x = position.X, y = position.Y, z = position.Z - part.offset_z } end
    end
    return point
end

local function sync_location_markers(state, phase)
    local frame = marker_frame
    if not frame then return end
    local pawn = marker_viewer(state, phase)
    if not same(pawn, frame.pawn) or not same(state.world, frame.world) or frame.map ~= state.map
        or now(pawn) >= frame.expires then
        clear_marker_frame()
        return
    end
    for character, marker in pairs(location_markers) do
        if not frame.positions[character] then
            clear_marker(marker)
            location_markers[character] = nil
        end
    end
    frame.visible = {}
    for _, character in ipairs(frame.characters) do
        local details, marker = frame.positions[character], location_markers[character]
        if not marker then
            marker = { actors = {}, visible = false }
            location_markers[character] = marker
        end
        local ok, point = pcall(function()
            if marker.visible then
                follow_marker(marker, details)
            else
                create_marker(marker, frame.world, details)
            end
            return read_location_marker(marker, frame.world, details)
        end)
        if ok and point then
            frame.visible[#frame.visible + 1] = { character = character, point = point }
        else
            clear_marker(marker)
            location_markers[character] = nil
        end
    end
end

local function apply_marker_frame(fields, state, phase)
    local epoch, area = fields[3], fields[4]
    local pawn = marker_viewer(state, phase)
    if not pawn or #fields < 4 or #fields > 20 or (#fields - 4) % 4 ~= 0 or area ~= state.map
        or type(epoch) ~= "string" or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") then
        error("Downed markers require the exact connected client, map and zone epoch", 0)
    end
    local characters, positions = {}, {}
    for index = 5, #fields, 4 do
        local character = fields[index]
        local x, y, z = tonumber(fields[index + 1]), tonumber(fields[index + 2]), tonumber(fields[index + 3])
        if not marker_character(character) or positions[character] or not finite(x) or not finite(y) or not finite(z)
            or math.abs(x) > marker_coordinate_limit or math.abs(y) > marker_coordinate_limit
            or math.abs(z) > marker_coordinate_limit then
            error("Downed markers require distinct character IDs and finite in-range positions", 0)
        end
        characters[#characters + 1] = character
        positions[character] = { x = x, y = y, z = z }
    end
    if marker_frame and (marker_frame.epoch ~= epoch or not same(marker_frame.pawn, pawn)
        or not same(marker_frame.world, state.world) or marker_frame.map ~= area) then
        clear_marker_frame()
    end
    -- Exact authenticated locations replace legacy name-based markers for remote pawns.
    for name in pairs(desired) do
        if name ~= state.local_pawn.name then desired[name] = nil end
    end
    marker_frame = { epoch = epoch, map = area, world = state.world, pawn = pawn, expires = now(pawn) + marker_frame_lifetime,
        characters = characters, positions = positions, visible = {} }
    return "downed marker frame accepted"
end

function players.sync(state, phase)
    local slots, names = roster(state, phase)
    slot_pawns = slots
    local_player = slots["local"]
    if walking and not current_identity(walking, local_player, state.world) then stop_walk() end

    for name, record in pairs(desired) do
        local details = names[name]
        if not current_identity(record, details, state.world) then
            desired[name] = nil
        else
            local enforce = phase == "hosting" or (details == local_player and details.pawn:IsLocallyControlled())
            local old = applied[name]
            if old and (not enforce or not current_identity(old, details, state.world)) then release(name) end
            if enforce and not applied[name] then begin_downed(details, state.world) end
            sync_marker(name, details)
        end
    end
    for name in pairs(applied) do
        if not desired[name] then release(name) end
    end
    for name in pairs(markers) do
        if not desired[name] then sync_marker(name, nil) end
    end
    sync_guest_sprints(state, slots, phase)
    pcall(native_downed_ai.sync, state, phase, desired)
    sync_local_input(state, phase)
    sync_location_markers(state, phase)
    sync_native_interact(phase)
end

function players.is_downed(pawn)
    if not local_player or not same(local_player.pawn, pawn) then return nil end
    local record = desired[local_player.name]
    return record ~= nil and same(record.pawn, pawn) and same(record.world, pawn:GetWorld())
end

function players.is_standing_guest(pawn, name)
    for slot, detail in pairs(slot_pawns) do
        if slot ~= "local" and detail.name == name and same(detail.pawn, pawn) then
            return desired[name] == nil and applied[name] == nil
        end
    end
    return false
end

function players.command(fields, state, phase)
    local command = fields[2]
    if command == "player-guest-sprints" then
        if phase ~= "hosting" or state.map ~= fields[3] or #fields > 11 or (#fields - 3) % 2 ~= 0 then
            error("Invalid guest sprint state", 0)
        end
        local slots = roster(state, phase)
        local next_sprints = {}
        for slot, details in pairs(slots) do
            if slot ~= "local" and guest_sprints[details.name] then
                local entry = guest_sprints[details.name]
                next_sprints[details.name] = {sprint = false, applied = entry.applied}
            end
        end
        for index = 4, #fields, 2 do
            local name, flag = fields[index], fields[index + 1]
            if flag ~= "0" and flag ~= "1" then error("Invalid guest sprint state", 0) end
            local old = guest_sprints[name]
            next_sprints[name] = {sprint = flag == "1", applied = old and old.applied}
        end
        guest_sprints = next_sprints
        sync_guest_sprints(state, slots, phase)
        return "Guest sprint intent accepted"
    end
    if command == "native-stock-door-input-fixture" and #fields == 3 then
        local pawn = local_player and local_player.pawn
        if not valid(pawn) or desired[local_player.name] or native_interact_address ~= pawn:GetAddress()
            or type(SilverfishProbeNativeInteractInput) ~= "function" then
            error("stock_door_guest_input_fixture_unavailable", 0)
        end
        return native_stock_door.input_fixture(state, phase, fields[3], pawn, function()
            local ok, reason = SilverfishProbeNativeInteractInput(native_interact_address)
            if not ok then error(reason, 0) end
            if sync_native_interact(phase) ~= "stock-door:" .. fields[3] then
                error("stock_door_guest_input_fixture_not_published", 0)
            end
        end)
    end
    if command == "native-enemy-shell-input-fixture" and #fields == 3 then
        local pawn = local_player and local_player.pawn
        if not valid(pawn) or desired[local_player.name] or native_interact_address ~= pawn:GetAddress()
            or type(SilverfishProbeNativeInteractInput) ~= "function" then error("native_enemy_shell_fixture_scope_unavailable", 0) end
        return native_enemy_shell_view.fixture(state, phase, fields[3], function()
            local ok, reason = SilverfishProbeNativeInteractInput(native_interact_address)
            if not ok then error(reason, 0) end
            return sync_native_interact(phase)
        end)
    end
    if command == "native-interaction-fixture" then
        local pawn = local_player and local_player.pawn
        if #fields ~= 2 or not active(phase) or not valid(pawn) or desired[local_player.name]
            or native_interact_address ~= pawn:GetAddress() or type(SilverfishProbeNativeInteractInput) ~= "function" then
            error("The stock interact fixture requires the exact standing staged pawn", 0)
        end
        local ok, status = SilverfishProbeNativeInteractInput(native_interact_address)
        if not ok then error(status, 0) end
        sync_native_interact(phase)
        return "stock interact input callback verified"
    elseif command == "walk" or command == "walk-continuous" then
        local x, y, milliseconds = tonumber(fields[3]), tonumber(fields[4]), tonumber(fields[5])
        if #fields ~= 5 or not finite(x) or not finite(y) or not finite(milliseconds)
            or math.abs(x) > 1 or math.abs(y) > 1 or milliseconds % 1 ~= 0
            or milliseconds < 20 or milliseconds > 3000 or (x == 0 and y == 0) then
            error("Expected walk direction components in [-1, 1] and 20-3000 milliseconds", 0)
        end
        local detail = state.local_pawn
        local record = {pawn = detail and detail.pawn, name = detail and detail.name, controller = state.controller,
            world = state.world}
        if not active(phase) or not walk_scope(record) then
            error("Walking requires the active local pawn", 0)
        end
        local scale = math.max(1, math.sqrt(x * x + y * y))
        record.world_direction = { X = x / scale, Y = y / scale, Z = 0 }
        record.deadline = now(record.pawn) + milliseconds / 1000
        walking = record
        if command == "walk-continuous" then start_walk_frames() else cancel_walk_frames() end
        add_walk_input(record)
        return "walking input scheduled"
    elseif command == "native-host-downed-inventory-fixture" then
        return open_host_inventory_fixture(fields, state, phase)
    elseif command == "player-marker-frame" then
        return apply_marker_frame(fields, state, phase)
    elseif command == "player-state" or command == "player-state-exact" then
        local target, next_state = fields[3], fields[4]
        local exact = command == "player-state-exact"
        if #fields ~= 4 or (not exact and not valid_slots[target])
            or (next_state ~= "standing" and next_state ~= "downed") then
            error("Expected player-state <slot> or player-state-exact <pawn full name>, with standing or downed", 0)
        end
        if not active(phase) then error("Player projection requires an active co-op session", 0) end
        local slots, names = roster(state, phase)
        local details = (exact and names or slots)[target]
        if not details then error("Player projection requires one current pawn matching the target", 0) end
        local name = details.name
        desired[name] = next_state == "downed" and { pawn = details.pawn, world = state.world } or nil
        return "player state projected"
    end
end

local function input_flag(value)
    if value == nil then return "-" end
    return value and 1 or 0
end

function players.rows(status_row)
    local rows = {}
    if local_player and valid(local_player.pawn) then
        rows[#rows + 1] = status_row("player-local-sprint", local_player.pawn.Sprinting and 1 or 0)
    end
    for slot, details in pairs(slot_pawns) do
        local name = details.name
        local pawn, record, marker = details.pawn, applied[name], markers[name]
        local movement = pawn.CharacterMovement
        if valid(movement) then
            local speed = pawn.bIsCrouched and movement.MaxWalkSpeedCrouched or movement.MaxWalkSpeed
            local state = desired[name] and "downed" or "standing"
            rows[#rows + 1] = status_row("player", slot, state, pawn.bIsCrouched and 1 or 0, speed, movement.JumpZVelocity)
            rows[#rows + 1] = status_row("player-visual", slot,
                record and record.input_verified and 1 or 0, marker and marker.visible and 1 or 0)
        end
    end
    local input = local_input_status
    if input then
        local mapping = input.mapping
        local flags = {}
        for _, label in ipairs(input_fields) do flags[#flags + 1] = input_flag(mapping and mapping.flags[label]) end
        flags[#flags + 1] = input.inventory
        rows[#rows + 1] = status_row("player-local-input", input.pawn, input.state,
            input.verified and "verified" or "waiting", input_flag(mapping and mapping.stock),
            input_flag(mapping and mapping.reduced), table.unpack(flags))
    end
    local frame = marker_frame
    if frame then
        rows[#rows + 1] = status_row("player-marker-frame", frame.epoch, frame.map, #frame.visible)
        for _, item in ipairs(frame.visible) do
            rows[#rows + 1] = status_row("player-marker", frame.epoch, frame.map, item.character,
                item.point.x, item.point.y, item.point.z)
        end
    end
    return rows
end

local function on_interact(context)
    if type(SilverfishTakeNativeInteractPress) ~= "function" then write_interaction(context:get()) end
end

local function ensure_hooks()
    if type(SilverfishTakeNativeInteractPress) == "function" then
        interact_hook_checked = true
    elseif not interact_hook_checked and valid(StaticFindObject(interact_path)) then
        local ok, failure = pcall(RegisterHook, interact_path, on_interact)
        interact_hook_checked = true
        if not ok then
            print("[SilverfishCoop] Enhanced Input interaction hook unavailable: " .. tostring(failure) .. "\n")
        end
    end
end

local function pulse()
    for _, record in pairs(applied) do apply_downed(record) end
    if walking and not walk_frame_action then apply_walk() end
end

function players.start(bridge)
    input_file = bridge .. "/input.tsv"
    LoopInGameThreadWithDelay(250, function()
        if not interact_hook_checked then ensure_hooks() end
    end)
    LoopInGameThreadWithDelay(33, function()
        if not native_interact_address then return end
        local ok, failure = pcall(drain_native_interact)
        if not ok then
            native_interact_address = nil
            print("[SilverfishCoop] Interact input unavailable: " .. tostring(failure) .. "\n")
        end
    end)
    LoopInGameThreadWithDelay(20, function()
        if not (walking and not walk_frame_action) and not next(applied) then return end
        local ok, failure = pcall(pulse)
        if not ok then movement_failure(failure) end
    end)
end

return players
