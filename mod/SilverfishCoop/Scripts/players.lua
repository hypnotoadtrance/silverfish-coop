local players = {}

local player_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local interact_path = player_path .. ":InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19"
local tick_path = player_path .. ":ReceiveTick"
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

local desired = { ["local"] = "standing", guest = "standing" }
local slot_pawns = {}
local applied = {}
local markers = {}
local host_authority = false
local local_player
local walking
local input_file
local input_sequence = 0
local interact_hook_checked = false
local movement_hook_checked = false
local movement_hooked = false
local pulse_queued = false

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
    if not valid(pawn) or not valid(movement) then return end
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

local function release(slot)
    local record = applied[slot]
    if not record then return end
    if valid(record.pawn) and valid(record.movement) then
        restore_fields(record.pawn, record.pawn_values)
        restore_fields(record.movement, record.movement_values)
        record.pawn.HoldingCrouch = record.holding_crouch
        if not record.was_crouched then record.pawn:UnCrouch(false) end
    end
    remove_mapping_gate(record.gate)
    applied[slot] = nil
end

local function begin_downed(slot, details)
    local movement = details.pawn.CharacterMovement
    if not valid(movement) then return end
    local movement_values = read_fields(movement, downed_movement_fields)
    local pawn_values = read_fields(details.pawn, downed_pawn_fields)
    local baseline_speed = movement_values.MaxWalkSpeedCrouched
    if not finite(baseline_speed) or baseline_speed <= 0 then baseline_speed = pawn_values.Speed_Crouching end
    local record = {
        pawn = details.pawn,
        movement = movement,
        movement_values = movement_values,
        pawn_values = pawn_values,
        speed_cap = baseline_speed * 0.5,
        was_crouched = details.pawn.bIsCrouched,
        holding_crouch = details.pawn.HoldingCrouch,
    }
    applied[slot] = record
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

local function sync_marker(slot, details)
    local viewer = local_player and local_player.pawn
    local show = slot == "guest" and desired[slot] == "downed" and details
        and valid(details.pawn) and valid(viewer) and finite(details.x) and finite(details.y) and finite(details.z)
    local marker = markers[slot]
    if marker and (not show or not same(marker.pawn, details.pawn)) then
        clear_marker(marker)
        markers[slot] = nil
        marker = nil
    end
    if not show then return end

    if not marker then
        local world = viewer:GetWorld()
        if not valid(world) then return end
        marker = { pawn = details.pawn, actors = {}, visible = false }
        markers[slot] = marker
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
    local slots = {}
    local enabled = active(phase)
    if enabled then
        for _, pawn in ipairs(state.pawns) do slots[pawn.kind] = pawn end
    else
        desired["local"], desired.guest = "standing", "standing"
        walking = nil
    end
    slot_pawns = slots
    local_player = slots["local"]
    host_authority = phase == "hosting"

    for slot, value in pairs(desired) do
        local details = slots[slot]
        local old = applied[slot]
        local enforce = value == "downed" and details
            and valid(details.pawn)
            and ((slot == "local" and details.pawn:IsLocallyControlled())
                or (host_authority and details.pawn:HasAuthority()))
        if old and (not enforce or not same(old.pawn, details.pawn)) then release(slot) end
        if enforce and not applied[slot] then begin_downed(slot, details) end
        sync_marker(slot, details)
    end
end

function players.command(fields, state, phase)
    local command = fields[2]
    if command == "walk" then
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
    elseif command == "player-state" then
        local slot, next_state = fields[3], fields[4]
        if #fields ~= 4 or desired[slot] == nil or (next_state ~= "standing" and next_state ~= "downed") then
            error("Expected player-state local|guest standing|downed", 0)
        end
        if not active(phase) then error("Player projection requires an active co-op session", 0) end
        desired[slot] = next_state
        return "player state projected"
    end
end

function players.rows(status_row)
    local rows = {}
    for slot, details in pairs(slot_pawns) do
        local pawn, record, marker = details.pawn, applied[slot], markers[slot]
        local movement = pawn.CharacterMovement
        if valid(movement) then
            local speed = pawn.bIsCrouched and movement.MaxWalkSpeedCrouched or movement.MaxWalkSpeed
            rows[#rows + 1] = status_row("player", slot, desired[slot], pawn.bIsCrouched and 1 or 0,
                speed, movement.JumpZVelocity)
            rows[#rows + 1] = status_row("player-visual", slot,
                record and record.gate and 1 or 0, marker and marker.visible and 1 or 0)
        end
    end
    return rows
end

local function on_interact(context)
    if not local_player or desired["local"] ~= "standing" then return end
    local pawn = context:get()
    if not same(pawn, local_player.pawn) or not pawn:IsLocallyControlled() then return end
    local file = io.open(input_file, "ab")
    if not file then return end
    input_sequence = input_sequence + 1
    file:write("interact\t", tostring(input_sequence), "\n")
    file:close()
end

local function apply_walk(pawn)
    if not local_player or not same(local_player.pawn, pawn)
        or not pawn:IsLocallyControlled() or now(pawn) >= walking.deadline then
        walking = nil
        return
    end
    pawn:AddMovementInput(walking.world_direction, 1.0, false)
end

local function on_actor_tick(context)
    if not walking then return end
    local pawn = context:get()
    if not same(pawn, walking.pawn) then return end
    local ok, failure = pcall(apply_walk, pawn)
    if not ok then
        walking = nil
        print("[SilverfishCoop] Player movement update failed: " .. tostring(failure) .. "\n")
    end
end

local function ensure_hooks()
    if not interact_hook_checked and valid(StaticFindObject(interact_path)) then
        local ok, failure = pcall(RegisterHook, interact_path, on_interact)
        interact_hook_checked = true
        if not ok then
            print("[SilverfishCoop] Enhanced Input interaction hook unavailable: " .. tostring(failure) .. "\n")
        end
    end
    if not movement_hook_checked and valid(StaticFindObject(tick_path)) then
        local ok, failure = pcall(RegisterHook, tick_path, on_actor_tick)
        movement_hook_checked = true
        movement_hooked = ok
        if not ok then
            print("[SilverfishCoop] Using periodic movement input: " .. tostring(failure) .. "\n")
        end
    end
end

local function pulse()
    for _, record in pairs(applied) do apply_downed(record) end
    if walking and not movement_hooked then apply_walk(walking.pawn) end
end

function players.start(bridge)
    input_file = bridge .. "/input.tsv"
    LoopAsync(250, function()
        ensure_hooks()
        return interact_hook_checked and movement_hook_checked
    end)
    LoopAsync(20, function()
        if not pulse_queued and ((walking and not movement_hooked) or next(applied)) then
            pulse_queued = true
            ExecuteInGameThread(function()
                local ok, failure = pcall(pulse)
                pulse_queued = false
                if not ok then
                    walking = nil
                    print("[SilverfishCoop] Player movement update failed: " .. tostring(failure) .. "\n")
                end
            end)
        end
        return false
    end)
end

return players
