local loot = require("native_loot")
local input = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local client_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local legacy_fixture_profiles = 3
local open_rotation = { Pitch = 0, Yaw = 0, Roll = -95 }
local cabinet_rotations = {{Pitch = 0, Yaw = 95, Roll = 0}, {Pitch = 0, Yaw = -95, Roll = 0}}
local bindings, fixture_sequences = {}, {}
local active_world

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function exact_component(value, path, owner)
    return valid(value) and same(value:GetClass(), StaticFindObject(path)) and same(value:GetOwner(), owner)
end

function input.clear()
    bindings, fixture_sequences, active_world = {}, {}, nil
    if enabled and type(SilverfishClearNativeLootCrateInput) == "function" then
        SilverfishClearNativeLootCrateInput()
    end
end

function input.update(state, phase, profile)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    local host = phase == "hosting" and state.server == false
    if not enabled or not profile or state.map ~= profile.map or not state.listening or not valid(state.world)
        or not valid(pawn) or not same(pawn:GetWorld(), state.world)
        or (not host and (phase ~= "connected" or state.server ~= true)) then
        return nil, "lua_scope"
    end
    if not same(active_world, state.world) then
        input.clear()
        active_world = state.world
    end
    if type(SilverfishSetNativeLootCrateInputScope) ~= "function"
        or type(SilverfishReadNativeLootCrateInput) ~= "function" then
        return nil, "lua_addon"
    end

    local index = profile.index
    local accepted, reason = SilverfishSetNativeLootCrateInputScope(pawn:GetAddress(), host, index)
    if not accepted then
        bindings[index], fixture_sequences[index] = nil, nil
        return nil, reason or "native_scope"
    end
    local crate = StaticFindObject(profile.path)
    if not valid(crate) or crate:GetFullName() ~= profile.name or not same(crate:GetWorld(), state.world) then
        bindings[index], fixture_sequences[index] = nil, nil
        return nil, "lua_crate"
    end
    local binding = bindings[index]
    if not binding or not same(binding.crate, crate) or not same(binding.pawn, pawn) then
        binding = { crate = crate, pawn = pawn, name = detail.name, host = host, profile = profile }
        bindings[index], fixture_sequences[index] = binding, nil
    end
    return binding
end

local function observe_scene(current, box_state, box_address)
    local crate = current.crate
    local door = crate.SM_Prop_Cabinet_Small_Door_01
    local report = { door_state = "door_identity", box_state = box_state, looted = crate.Looted }
    if exact_component(door, "/Script/Engine.StaticMeshComponent", crate) then
        report.door_state, report.door = "live", door
        local angle = door.RelativeRotation
        report.roll = angle and angle.Roll
    end

    if box_state == "live" then
        local box = crate.Box
        if not exact_component(box, "/Script/Engine.BoxComponent", crate) or box:GetAddress() ~= box_address then
            report.box_state = "box_reflection"
        else
            report.box, report.collision = box, box:GetCollisionEnabled()
        end
    end

    -- The authorized stock open can keep the Blueprint Box reference while
    -- it destroys the underlying component. Native checks owned components
    -- and object lifetime before Lua reads a Box or its collision state.
    local box_qualified = report.box
        or report.looted == true and (report.box_state == "retired" or report.box_state == "missing")
    report.qualified = report.door ~= nil and box_qualified
    if report.qualified and (current.profile.small_cabinet or current.profile.office_desk) then
        local frame, second = crate.SM_Prop_Cabinet_Small_02, crate.SM_Prop_Cabinet_Small_Door_02
        if not exact_component(frame, "/Script/Engine.StaticMeshComponent", crate)
            or not exact_component(second, "/Script/Engine.StaticMeshComponent", crate) or same(door, second)
            or not same(door:GetAttachParent(), frame) or not same(second:GetAttachParent(), frame)
            or report.box and not same(report.box:GetAttachParent(), frame) then
            report.qualified, report.door_state = false, "cabinet_identity"
        else
            report.second = second
        end
    end
    return report
end

local function scene(current)
    local active, _, box_state, box_address = SilverfishReadNativeLootCrateInput(current.profile.index)
    if not active then return end
    local report = observe_scene(current, box_state, box_address)
    if report.qualified then return report.door, report.box, report.second end
end

local function close_angle(actual, expected)
    return type(actual) == "number" and math.abs((actual - expected + 180) % 360 - 180) < 0.05
end

local function matches_rotation(door, expected)
    local rotation = door.RelativeRotation
    return rotation and close_angle(rotation.Pitch, expected.Pitch)
        and close_angle(rotation.Yaw, expected.Yaw) and close_angle(rotation.Roll, expected.Roll)
end

local function exhausted(crate, door, box, second)
    if crate.Looted ~= true or not door or box and box:GetCollisionEnabled() ~= 0 then return false end
    if second then return matches_rotation(door, cabinet_rotations[1]) and matches_rotation(second, cabinet_rotations[2]) end
    return matches_rotation(door, open_rotation)
end

local function project(current)
    local crate = current.crate
    local door, box, second = scene(current)
    if exhausted(crate, door, box, second) then return "stock crate exhausted projection verified" end
    local original_looted = crate.Looted
    if not door or type(original_looted) ~= "boolean" or not box and not original_looted then
        error("native_loot_crate_exact_scene_unavailable", 0)
    end
    local doors, original = {door, second}, {}
    for index, component in ipairs(doors) do
        local rotation = component.RelativeRotation
        if not rotation or not matches_rotation(component, rotation) then error("native_loot_crate_door_rotation_unavailable", 0) end
        original[index] = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
    end
    local opened = second and cabinet_rotations or {open_rotation}
    local collision = box and box:GetCollisionEnabled()
    if box and collision ~= 0 and collision ~= 1 and collision ~= 3 then
        error("native_loot_crate_box_collision_unavailable", 0)
    end

    -- Replicate only the spent door pose and disable the query Box.
    local invoked, verified = pcall(function()
        if box then box:SetCollisionEnabled(0) end
        for index, component in ipairs(doors) do component:K2_SetRelativeRotation(opened[index], false, {}, false) end
        crate.Looted = true
        return exhausted(crate, door, box, second)
    end)
    if not invoked or not verified then
        local restored, matches = pcall(function()
            crate.Looted = original_looted
            for index, component in ipairs(doors) do component:K2_SetRelativeRotation(original[index], false, {}, false) end
            if box then box:SetCollisionEnabled(collision) end
            if crate.Looted ~= original_looted or box and box:GetCollisionEnabled() ~= collision then return false end
            for index, component in ipairs(doors) do
                if not matches_rotation(component, original[index]) then return false end
            end
            return true
        end)
        error(restored and matches and "native_loot_crate_projection_refused"
            or "mutation_unverified_native_loot_crate_projection", 0)
    end
    return "stock crate exhausted projection verified"
end

local function fixture_focus(pawn, box)
    local camera = pawn.Camera
    if not exact_component(camera, "/Script/Engine.CameraComponent", pawn) then
        error("native_loot_crate_stock_fixture_camera_unavailable", 0)
    end
    local rotation, eye = camera.RelativeRotation, camera:K2_GetComponentLocation()
    local center, extent = box:K2_GetComponentLocation(), box:GetScaledBoxExtent()
    if not rotation or not eye or not center or not extent or not close_angle(rotation.Pitch, rotation.Pitch)
        or not close_angle(rotation.Yaw, rotation.Yaw) or not close_angle(rotation.Roll, rotation.Roll) then
        error("native_loot_crate_stock_fixture_camera_unavailable", 0)
    end
    local original = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
    local ray = {center}
    for _, axis in ipairs({{box:GetUpVector(), extent.Z}, {box:GetRightVector(), extent.Y}}) do
        local direction, size = axis[1], axis[2]
        if direction and type(size) == "number" and size > 0 and size <= 100 then
            for _, sign in ipairs({1, -1}) do
                local offset = sign * size * 0.35
                ray[#ray + 1] = {X = center.X + offset * direction.X, Y = center.Y + offset * direction.Y, Z = center.Z + offset * direction.Z}
            end
        end
    end
    local invoked, consumed, detail = pcall(function()
        for _, point in ipairs(ray) do
            local dx, dy, dz = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
            local horizontal = math.sqrt(dx * dx + dy * dy)
            if horizontal > 1 and horizontal < 1000 and math.abs(dz) < 1000 then
                local aim = {Pitch = math.deg(math.atan(dz, horizontal)), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
                camera:K2_SetWorldRotation(aim, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, box) then
                    return SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                end
            end
        end
        local focused = pawn.InteractionTarget_Component
        error(string.format("native_loot_crate_stock_fixture_focus_unavailable:%s eye=(%.1f,%.1f,%.1f) box=(%.1f,%.1f,%.1f)",
            valid(focused) and focused:GetFullName() or "-", eye.X, eye.Y, eye.Z, center.X, center.Y, center.Z), 0)
    end)
    local restored, matches = pcall(function()
        if not same(pawn.Camera, camera) or not exact_component(camera, "/Script/Engine.CameraComponent", pawn) then return false end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        pawn:LookTrace()
        local readback = camera.RelativeRotation
        return readback and close_angle(readback.Pitch, original.Pitch) and close_angle(readback.Yaw, original.Yaw)
            and close_angle(readback.Roll, original.Roll)
    end)
    if not restored or not matches then error("mutation_unverified_native_loot_crate_stock_fixture_camera", 0) end
    if not invoked then error(consumed, 0) end
    return invoked, consumed, detail
end

local function fixture(current)
    local _, box = scene(current)
    local pawn, crate = current.pawn, current.crate
    if not headless or (current.host and not host_fixtures) or (not current.host and not client_fixtures)
        or not box or crate.Looted ~= false or type(SilverfishSetNativeInteractScope) ~= "function"
        or type(SilverfishProbeNativeInteractInput) ~= "function"
        or not SilverfishSetNativeInteractScope(pawn:GetAddress(), current.host) then
        error("native_loot_crate_stock_fixture_unavailable", 0)
    end
    local index = current.profile.index
    local before_active, before = SilverfishReadNativeLootCrateInput(index)
    if before_active ~= true then error("native_loot_crate_stock_fixture_scope_unavailable", 0) end
    local invoked, ok, detail
    if index > legacy_fixture_profiles then
        invoked, ok, detail = fixture_focus(pawn, box)
    else
        local original = pawn.InteractionTarget_Component
        pawn.InteractionTarget_Component = box
        invoked, ok, detail = pcall(SilverfishProbeNativeInteractInput, pawn:GetAddress(), true)
        pawn.InteractionTarget_Component = valid(original) and original or nil
    end
    local active, sequence = SilverfishReadNativeLootCrateInput(index)
    if not invoked or not ok or detail ~= "stock_started_consumed" or not active or sequence <= before
        or crate.Looted ~= false then
        error("native_loot_crate_stock_fixture_receipt_unverified", 0)
    end
    fixture_sequences[index] = sequence
    return "stock crate interaction intercepted"
end

function input.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-loot-crate-fixture" and command ~= "native-loot-crate-project" then return end
    local target = command == "native-loot-crate-fixture" and fields[3] or fields[4]
    local current = input.update(state, phase, loot.profile(state.map, target))
    if not current then error("native_loot_crate_input_scope_unavailable", 0) end
    if command == "native-loot-crate-fixture" and (#fields == 2 or #fields == 3) then return fixture(current) end
    if command == "native-loot-crate-project" and (#fields == 3 or #fields == 4) and fields[3] == "exhausted" then
        return project(current)
    end
    error("native_loot_crate_input_arguments", 0)
end

function input.rows(state, phase, status_row)
    local profiles = loot.profiles(state.map)
    if not enabled or #profiles == 0 or not state.listening or phase ~= "hosting" and phase ~= "connected" then
        if active_world then input.clear() end
        return {}
    end
    local rows, latest, reported = {}, 0, nil
    for _, profile in ipairs(profiles) do
        local current, scope = input.update(state, phase, profile)
        local active, sequence, info = false, 0, {}
        if current then
            local box_state, box_address
            active, sequence, box_state, box_address = SilverfishReadNativeLootCrateInput(profile.index)
            scope = active and "active" or "scope_inactive"
            if active then info = observe_scene(current, box_state, box_address) end
        end
        rows[#rows + 1] = status_row("native-loot-crate-diagnostic", profile.map, profile.name, phase,
            scope or "-", info.door_state or "-", info.box_state or "-",
            info.looted == nil and "-" or (info.looted and 1 or 0), info.collision or "-", info.roll or "-")
        if info.qualified then
            rows[#rows + 1] = status_row("native-loot-crate-input", profile.map, profile.name,
                exhausted(current.crate, info.door, info.box, info.second) and "exhausted" or "ready")
            if current.host and info.looted == false and info.box and (info.collision == 1 or info.collision == 3) then
                local verified, active = pcall(loot.temporary_activation, current.crate, current.pawn)
                if verified and active == false then
                    rows[#rows + 1] = status_row("native-loot-crate-replenished", profile.map, profile.name, current.name)
                end
            end
        end
        if active and sequence > latest then
            latest = sequence
            reported = status_row("native-loot-crate-interaction", profile.map, profile.name,
                current.name, sequence, sequence == fixture_sequences[profile.index] and "fixture" or "stock")
        end
    end
    if reported then rows[#rows + 1] = reported end
    return rows
end

return input
