local players = {}
local native_bag_projection = require("native_bag_projection")
local native_authored_pickup = require("native_authored_pickup")

local player_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local interact_path = player_path .. ":InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19"
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local input_subsystem_path = "/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem"
local subsystem_library_path = "/Script/Engine.Default__SubsystemBlueprintLibrary"
local gameplay_input_path = "/Game/Classes/Player/Player_Inputs/IMC_FP.IMC_FP"
local vector_zero = { X = 0, Y = 0, Z = 0 }
local mapping_options = { bIgnoreAllPressedKeysUntilRelease = true }
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
local local_player
local walking
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

local function write_interaction(pawn)
    if not input_file or not local_player or desired[local_player.name] or not same(pawn, local_player.pawn)
        or not pawn:IsLocallyControlled() then return end
    local file = io.open(input_file, "ab")
    if not file then return end
    input_sequence = input_sequence + 1
    local target = native_bag_projection.focused_target(pawn) or native_authored_pickup.focused_target(pawn)
    file:write("interact\t", tostring(input_sequence), target and "\t" .. target or "", "\n")
    file:close()
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
    if not SilverfishSetNativeInteractScope(address, phase == "hosting") then
        native_interact_address = nil
        return
    end
    native_interact_address = address
    for _ = 1, 8 do
        local sequence, matched = SilverfishTakeNativeInteractPress(address)
        if matched ~= address or type(sequence) ~= "number" or sequence < 1 then break end
        write_interaction(pawn)
    end
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
    local contexts = controller.PlayerInput.AppliedInputContexts
    if not contexts:Contains(original) then return nil end
    local priority = contexts:Find(original):get()
    local clone = StaticConstructObject(original:GetClass(), controller, 0, 0x40, 0, false, false, original)
    if not valid(clone) or #clone.Mappings ~= #original.Mappings then return nil end

    local blocked = {}
    for index = 1, #clone.Mappings do
        local action = clone.Mappings[index].Action
        if valid(action) and not retain_mapping(action) then blocked[action:GetAddress()] = action end
    end
    local original_count = #original.Mappings
    for _, action in pairs(blocked) do clone:UnmapAllKeysFromAction(action) end
    if #clone.Mappings < 1 or #clone.Mappings >= original_count or #original.Mappings ~= original_count then
        return nil
    end
    return { subsystem = subsystem, original = original, clone = clone, priority = priority }
end

local function apply_mapping_gate(details)
    if not valid(details.controller_object) or not details.controller_object:IsLocalController() then return nil end
    local gate = prepare_mapping_gate(details.controller_object)
    if not gate then return nil end
    gate.subsystem:AddMappingContext(gate.clone, gate.priority, mapping_options)
    local ok, err = pcall(function()
        gate.subsystem:RemoveMappingContext(gate.original, mapping_options)
    end)
    if not ok then
        gate.subsystem:RemoveMappingContext(gate.clone, mapping_options)
        error(err, 0)
    end
    return gate
end

local function remove_mapping_gate(gate)
    if gate and valid(gate.subsystem) then
        if valid(gate.original) then
            gate.subsystem:AddMappingContext(gate.original, gate.priority, mapping_options)
        end
        if valid(gate.clone) then gate.subsystem:RemoveMappingContext(gate.clone, mapping_options) end
    end
end

local function apply_downed(record)
    local pawn, movement = record.pawn, record.movement
    if not writable(record) or not valid(movement) then return end
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
    remove_mapping_gate(record.gate)
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
    local ok, result = pcall(apply_mapping_gate, details)
    if ok then record.gate = result end
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

function players.sync(state, phase)
    local slots, names = roster(state, phase)
    if not active(phase) then walking = nil end
    slot_pawns = slots
    local_player = slots["local"]

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
    sync_native_interact(phase)
end

function players.is_downed(pawn)
    if not local_player or not same(local_player.pawn, pawn) then return nil end
    local record = desired[local_player.name]
    return record ~= nil and same(record.pawn, pawn) and same(record.world, pawn:GetWorld())
end

function players.command(fields, state, phase)
    local command = fields[2]
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
    elseif command == "walk" then
        local x, y, milliseconds = tonumber(fields[3]), tonumber(fields[4]), tonumber(fields[5])
        if #fields ~= 5 or not finite(x) or not finite(y) or not finite(milliseconds)
            or math.abs(x) > 1 or math.abs(y) > 1 or milliseconds % 1 ~= 0
            or milliseconds < 20 or milliseconds > 3000 or (x == 0 and y == 0) then
            error("Expected walk direction components in [-1, 1] and 20-3000 milliseconds", 0)
        end
        if not active(phase) or not state.local_pawn or not state.local_pawn.pawn:IsLocallyControlled() then
            error("Walking requires the active local pawn", 0)
        end
        local pawn = state.local_pawn.pawn
        local scale = math.max(1, math.sqrt(x * x + y * y))
        walking = { pawn = pawn, world_direction = { X = x / scale, Y = y / scale, Z = 0 },
            deadline = now(pawn) + milliseconds / 1000 }
        pawn:AddMovementInput(walking.world_direction, 1.0, false)
        return "walking input scheduled"
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

function players.rows(status_row)
    local rows = {}
    for slot, details in pairs(slot_pawns) do
        local name = details.name
        local pawn, record, marker = details.pawn, applied[name], markers[name]
        local movement = pawn.CharacterMovement
        if valid(movement) then
            local speed = pawn.bIsCrouched and movement.MaxWalkSpeedCrouched or movement.MaxWalkSpeed
            local state = desired[name] and "downed" or "standing"
            rows[#rows + 1] = status_row("player", slot, state, pawn.bIsCrouched and 1 or 0, speed, movement.JumpZVelocity)
            rows[#rows + 1] = status_row("player-visual", slot,
                record and record.gate and 1 or 0, marker and marker.visible and 1 or 0)
        end
    end
    return rows
end

local function on_interact(context)
    if type(SilverfishTakeNativeInteractPress) ~= "function" then write_interaction(context:get()) end
end

local function apply_walk(pawn)
    if not local_player or not same(local_player.pawn, pawn)
        or not pawn:IsLocallyControlled() or now(pawn) >= walking.deadline then
        walking = nil
        return
    end
    pawn:AddMovementInput(walking.world_direction, 1.0, false)
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
    if walking then apply_walk(walking.pawn) end
end

function players.start(bridge)
    input_file = bridge .. "/input.tsv"
    LoopInGameThreadWithDelay(250, function()
        if not interact_hook_checked then ensure_hooks() end
    end)
    LoopInGameThreadWithDelay(20, function()
        if not walking and not next(applied) then return end
        local ok, failure = pcall(pulse)
        if not ok then
            walking = nil
            print("[SilverfishCoop] Player movement update failed: " .. tostring(failure) .. "\n")
        end
    end)
end

return players
