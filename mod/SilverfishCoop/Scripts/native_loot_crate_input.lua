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
local blowtorch_path = "/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C"
local crowbar_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C"
local tool_report

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
    tool_report = nil
    if enabled and type(SilverfishClearNativeLootCrateInput) == "function" then
        SilverfishClearNativeLootCrateInput()
    end
end

function input.fire_blowtorch(scope, guid, arm)
    local pawn, crate, save = scope.pawn, scope.crate, scope.save
    local torch = pawn.EquipedItem
    local tool_type = StaticFindObject(blowtorch_path)
    local inspect = SilverfishInspectNativeGuestWeapon
    if same(save, scope.stock) or crate.Locked ~= true or not valid(torch) or not same(torch:GetClass(), tool_type)
        or type(inspect) ~= "function" or pawn:Check_CanUseEquipment() ~= true
        or #guid ~= 32 or not guid:match("^[0-9A-F]+$") then error("stock_blowtorch_scope_unavailable", 0) end
    local ok, status, loaded, stored = inspect(pawn:GetAddress(), save:GetAddress(), guid)
    if not ok or status ~= "blowtorch_equipped" or loaded ~= stored or math.type(loaded) ~= "integer"
        or loaded < 1 or loaded > 3 or torch.Range ~= 600 then error("stock_blowtorch_exact_fuel_unavailable", 0) end

    local camera = pawn.Camera
    if not exact_component(camera, "/Script/Engine.CameraComponent", pawn) then error("stock_blowtorch_camera_unavailable", 0) end
    local relative, start, point = camera.RelativeRotation, camera:K2_GetComponentLocation(), scope.box:K2_GetComponentLocation()
    if not relative or not start or not point then error("stock_blowtorch_aim_unavailable", 0) end
    local original = {Pitch = relative.Pitch, Yaw = relative.Yaw, Roll = relative.Roll}
    local x, y, z = point.X - start.X, point.Y - start.Y, point.Z - start.Z
    local length = math.sqrt(x*x + y*y + z*z)
    if not (length >= 1 and length <= torch.Range) then error("stock_blowtorch_out_of_range", 0) end
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(api) then error("stock_blowtorch_trace_unavailable", 0) end
    local point_at_range = {X = start.X + x * torch.Range / length, Y = start.Y + y * torch.Range / length,
        Z = start.Z + z * torch.Range / length}
    local hit, color = {}, {R = 0, G = 0, B = 0, A = 1}
    local found = api:LineTraceSingle(torch, start, point_at_range, 2, true, {pawn, torch}, 0, hit, true, color, color, 0)
    hit = hit.OutHit or hit
    local surface = hit.Component and hit.Component:Get()
    if found ~= true or not valid(surface) or not same(surface:GetOwner(), crate) then
        error(scope.profile and "stock_blowtorch_vehicle_obstructed" or "stock_blowtorch_obstructed", 0)
    end
    arm()

    local applied = pcall(function()
        camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))),
            Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
        torch:Use_Item_Primary(true, 0)
        torch:Use_Item_Primary(false, 0)
    end)
    local restored = pcall(camera.K2_SetRelativeRotation, camera, original, false, {}, true)
    local readback = camera.RelativeRotation
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        restored = restored and readback and math.abs((readback[axis] - original[axis] + 180) % 360 - 180) < .05
    end
    local now, equipment, live_fuel, saved_fuel = inspect(pawn:GetAddress(), save:GetAddress(), guid)
    if not applied or not restored or not now or equipment ~= "blowtorch_equipped" or saved_fuel ~= live_fuel then
        error("mutation_unverified_stock_blowtorch_use", 0)
    end
    return loaded, saved_fuel
end

local function ignite(current, state, phase, pawn_name, guid)
    local scope = loot.qualify(state, phase, pawn_name, current.profile.name, true)
    local loaded, saved_fuel = input.fire_blowtorch(scope, guid, function()
        if type(SilverfishArmNativeGuestVehiclePry) ~= "function" or type(SilverfishCancelNativeGuestVehiclePry) ~= "function"
            or not SilverfishArmNativeGuestVehiclePry(current.profile.index, scope.pawn:GetAddress(), scope.save:GetAddress()) then
            error("stock_blowtorch_vehicle_busy", 0)
        end
    end)
    if saved_fuel == loaded and scope.crate.Locked == true
        and SilverfishCancelNativeGuestVehiclePry(current.profile.index, scope.pawn:GetAddress()) then
        error("stock_blowtorch_stock_action_busy", 0)
    end
    if saved_fuel ~= loaded - 1 or scope.crate.Locked ~= (current.profile.deferred_pry == true) then
        error("mutation_unverified_stock_blowtorch_use", 0)
    end
    tool_report = {kind = "blowtorch", map = state.map, crate = current.profile.name, pawn = pawn_name, guid = guid,
        before = loaded, after = saved_fuel}
    return current.profile.deferred_pry and "stock blowtorch spent one charge and started the pry delay"
        or "stock blowtorch spent one charge and unlocked the container"
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
    if report.qualified and (current.profile.paired_doors or current.profile.small_cabinet or current.profile.office_desk) then
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
    if not rotation then return false end
    return (close_angle(rotation.Pitch, expected.Pitch) and close_angle(rotation.Yaw, expected.Yaw)
        and close_angle(rotation.Roll, expected.Roll))
        or (close_angle(rotation.Pitch, 180 - expected.Pitch) and close_angle(rotation.Yaw, expected.Yaw + 180)
        and close_angle(rotation.Roll, expected.Roll + 180))
end

local function valid_angle(profile, angle)
    local lid = profile.random_lid
    return lid and type(angle) == "number" and angle >= lid[2] and angle <= lid[3]
end

local function observe_angle(profile, door)
    if not profile.random_lid then return end
    local angle = door.RelativeRotation
    local axis = profile.random_lid[1]
    if not angle or type(angle[axis]) ~= "number" then return end
    local value = angle[axis] % 360
    if axis == "Pitch" and close_angle(angle.Yaw, 180) and close_angle(angle.Roll, 180) then
        -- Unreal can express a pitch past vertical using the other Euler representation.
        value = (180 - value) % 360
    elseif not close_angle(angle.Yaw, 0) or not close_angle(angle[axis == "Pitch" and "Roll" or "Pitch"], 0) then
        return
    end
    if valid_angle(profile, value) then return value end
end

local function open_poses(profile, second, angle)
    if second then return cabinet_rotations end
    if profile.random_lid then
        if not valid_angle(profile, angle) then return end
        local rotation = {Pitch = 0, Yaw = 0, Roll = 0}
        rotation[profile.random_lid[1]] = angle
        return {rotation}
    end
    if profile.open_roll then return {{Pitch = profile.open_pitch or 0, Yaw = profile.open_yaw or 0, Roll = profile.open_roll}} end
    return {open_rotation}
end

local function matches_pose(door, rotation, location)
    if not matches_rotation(door, rotation) then return false end
    if not location then return true end
    local current = door.RelativeLocation
    if not current then return false end
    for _, axis in ipairs({"X", "Y", "Z"}) do
        if type(current[axis]) ~= "number" or not (math.abs(current[axis] - location[axis]) < 0.05) then return false end
    end
    return true
end

local function set_pose(door, rotation, location)
    if location then
        door:K2_SetRelativeLocationAndRotation(location, rotation, false, {}, false)
    else
        door:K2_SetRelativeRotation(rotation, false, {}, false)
    end
end

local function exhausted(crate, door, box, second, opened, locations, profile)
    local ignored_channel = profile.lid_ignores_channel
    if crate.Looted ~= true or not opened or not door or (box and box:GetCollisionEnabled() ~= 0)
        or (profile.lockable and crate.Locked ~= false)
        or (ignored_channel and door:GetCollisionResponseToChannel(ignored_channel) ~= 0) then return false end
    return matches_pose(door, opened[1], locations[1]) and (not second or matches_pose(second, opened[2], locations[2]))
end

local function project(current, angle)
    local crate = current.crate
    local profile = current.profile
    local door, box, second = scene(current)
    local opened = open_poses(profile, second, angle)
    if not opened or (not profile.random_lid and angle ~= nil) then error("native_loot_crate_open_pose_unavailable", 0) end
    local locations = profile.door_locations or {}
    local ignored_channel = profile.lid_ignores_channel
    if exhausted(crate, door, box, second, opened, locations, profile) then return "stock crate exhausted projection verified" end
    local original_looted = crate.Looted
    local original_locked = profile.lockable and crate.Locked
    if not door or type(original_looted) ~= "boolean" or (profile.lockable and type(original_locked) ~= "boolean")
        or (not box and not original_looted) then
        error("native_loot_crate_exact_scene_unavailable", 0)
    end
    local doors, original, original_locations = {door, second}, {}, {}
    for index, component in ipairs(doors) do
        local rotation = component.RelativeRotation
        if not rotation or not matches_rotation(component, rotation) then error("native_loot_crate_door_rotation_unavailable", 0) end
        original[index] = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
        if locations[index] then
            local location = component.RelativeLocation
            if not location or not matches_pose(component, rotation, location) then
                error("native_loot_crate_door_location_unavailable", 0)
            end
            original_locations[index] = {X = location.X, Y = location.Y, Z = location.Z}
        end
    end
    local collision = box and box:GetCollisionEnabled()
    local door_response = ignored_channel and door:GetCollisionResponseToChannel(ignored_channel)
    if box and collision ~= 0 and collision ~= 1 and collision ~= 3 then
        error("native_loot_crate_box_collision_unavailable", 0)
    end
    if ignored_channel and door_response ~= 0 and door_response ~= 1 and door_response ~= 2 then
        error("native_loot_crate_lid_collision_unavailable", 0)
    end

    -- Replicate only the spent door pose and disable the query Box.
    local invoked, verified = pcall(function()
        if box then box:SetCollisionEnabled(0) end
        if ignored_channel then door:SetCollisionResponseToChannel(ignored_channel, 0) end
        for index, component in ipairs(doors) do set_pose(component, opened[index], locations[index]) end
        if profile.lockable then crate.Locked = false end
        crate.Looted = true
        return exhausted(crate, door, box, second, opened, locations, profile)
    end)
    if not invoked or not verified then
        local restored, matches = pcall(function()
            crate.Looted = original_looted
            if profile.lockable then crate.Locked = original_locked end
            for index, component in ipairs(doors) do set_pose(component, original[index], original_locations[index]) end
            if box then box:SetCollisionEnabled(collision) end
            if ignored_channel then door:SetCollisionResponseToChannel(ignored_channel, door_response) end
            if crate.Looted ~= original_looted or (box and box:GetCollisionEnabled() ~= collision)
                or (profile.lockable and crate.Locked ~= original_locked)
                or (ignored_channel and door:GetCollisionResponseToChannel(ignored_channel) ~= door_response) then return false end
            for index, component in ipairs(doors) do
                if not matches_pose(component, original[index], original_locations[index]) then return false end
            end
            return true
        end)
        error(restored and matches and "native_loot_crate_projection_refused"
            or "mutation_unverified_native_loot_crate_projection", 0)
    end
    return "stock crate exhausted projection verified"
end

local function stock_focus(pawn, box, dispatch, focus_refusal)
    local camera = pawn.Camera
    if not exact_component(camera, "/Script/Engine.CameraComponent", pawn) then
        error(focus_refusal or "native_loot_crate_stock_fixture_camera_unavailable", 0)
    end
    local rotation, eye = camera.RelativeRotation, camera:K2_GetComponentLocation()
    local center, extent = box:K2_GetComponentLocation(), box:GetScaledBoxExtent()
    if not rotation or not eye or not center or not extent or not close_angle(rotation.Pitch, rotation.Pitch)
        or not close_angle(rotation.Yaw, rotation.Yaw) or not close_angle(rotation.Roll, rotation.Roll) then
        error(focus_refusal or "native_loot_crate_stock_fixture_camera_unavailable", 0)
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
                    if dispatch then return dispatch() end
                    return SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                end
            end
        end
        if focus_refusal then error(focus_refusal, 0) end
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

input.stock_focus = stock_focus

function input.swing_crowbar(scope, chance, guid, arm)
    local pawn, crate, save = scope.pawn, scope.crate, scope.save
    local crowbar = pawn.EquipedItem
    local inspect = SilverfishInspectNativeGuestWeapon
    if same(save, scope.stock) or crate.Locked ~= true or not valid(crowbar)
        or not same(crowbar:GetClass(), StaticFindObject(crowbar_path)) or crowbar.PryLock_Stgth ~= .5
        or type(inspect) ~= "function" or pawn:Check_CanUseEquipment() ~= true
        or #guid ~= 32 or not guid:match("^[0-9A-F]+$") then error("stock_crowbar_scope_unavailable", 0) end
    local ok, status, equipped_ammo, stored = inspect(pawn:GetAddress(), save:GetAddress(), guid)
    if not ok or status ~= "crowbar_equipped" or equipped_ammo ~= -1 or stored ~= -1 then
        error("stock_crowbar_owned_tool_unavailable", 0)
    end
    if type(chance) ~= "number" or not (chance >= 0 and chance <= 1 and crowbar.PryLock_Stgth >= 1 - chance) then
        error("stock_crowbar_lock_too_strong", 0)
    end
    local reach_error = scope.profile and "stock_crowbar_vehicle_out_of_reach" or "stock_crowbar_out_of_reach"
    stock_focus(pawn, scope.box, function()
        if not same(pawn.ActorBeingLookedAt, crate) then error(reach_error, 0) end
        arm()
        local applied = pcall(function()
            crowbar:ToggleMode(true)
            crowbar:ToggleMode(false)
        end)
        local verified, equipment, live_ammo, saved_ammo = inspect(pawn:GetAddress(), save:GetAddress(), guid)
        if not applied or not verified or equipment ~= "crowbar_equipped" or live_ammo ~= -1 or saved_ammo ~= -1 then
            error("mutation_unverified_stock_crowbar_use", 0)
        end
    end, reach_error)
    return -1, -1
end

local function pry_with_crowbar(current, state, phase, pawn_name, guid)
    local scope = loot.qualify(state, phase, pawn_name, current.profile.name, true)
    local before, after = input.swing_crowbar(scope, scope.crate["Pick Chance"], guid, function()
        if type(SilverfishArmNativeGuestVehiclePry) ~= "function" or type(SilverfishCancelNativeGuestVehiclePry) ~= "function"
            or not SilverfishArmNativeGuestVehiclePry(current.profile.index, scope.pawn:GetAddress(), scope.save:GetAddress()) then
            error("stock_crowbar_vehicle_busy", 0)
        end
    end)
    if scope.crate.Locked == true and SilverfishCancelNativeGuestVehiclePry(current.profile.index, scope.pawn:GetAddress()) then
        error("stock_crowbar_stock_action_busy", 0)
    end
    if scope.crate.Locked ~= (current.profile.deferred_pry == true) then error("mutation_unverified_stock_crowbar_use", 0) end
    tool_report = {kind = "crowbar", map = state.map, crate = current.profile.name, pawn = pawn_name, guid = guid,
        before = before, after = after}
    return current.profile.deferred_pry and "stock crowbar started the pry delay" or "stock crowbar unlocked the container"
end

function input.pry_tool_input_fixture(state, phase, target, dispatch)
    local profile = loot.profile(state.map, target)
    local current = headless and client_fixtures and phase == "connected" and profile and profile.lockable
        and input.update(state, phase, profile)
    local _, box
    if current and current.crate.Locked == true then _, box = scene(current) end
    if not box then error("stock_vehicle_pry_input_fixture_unavailable", 0) end
    stock_focus(current.pawn, box, dispatch)
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
        invoked, ok, detail = stock_focus(pawn, box)
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
    if command == "native-loot-guest-blowtorch-use" or command == "native-loot-guest-crowbar-use" then
        local crowbar = command == "native-loot-guest-crowbar-use"
        local profile = loot.profile(state.map, fields[3])
        local current = #fields == 5 and phase == "hosting" and profile and profile.lockable
            and input.update(state, phase, profile)
        if not current then error(crowbar and "stock_crowbar_scope_unavailable" or "stock_blowtorch_scope_unavailable", 0) end
        tool_report = nil
        if crowbar then return pry_with_crowbar(current, state, phase, fields[4], fields[5]) end
        return ignite(current, state, phase, fields[4], fields[5])
    end
    if command == "native-loot-vehicle-pry-fixture" then
        local profile = loot.profile(state.map, fields[3])
        if not headless or not host_fixtures or #fields ~= 3 or phase ~= "hosting"
            or not profile or not profile.lockable then error("native_loot_vehicle_pry_fixture_unavailable", 0) end
        local current = input.update(state, phase, profile)
        if not current or current.crate.Locked ~= true then error("native_loot_vehicle_pry_fixture_unavailable", 0) end
        local scope = loot.qualify(state, phase, current.name, profile.name)
        scope.crate:PriedOpen_Lock()
        if scope.crate.Locked ~= (profile.deferred_pry == true) or scope.crate.Looted ~= false then
            error("native_loot_vehicle_pry_readback_unverified", 0)
        end
        return profile.deferred_pry and "stock fixture started the pry delay" or "stock fixture unlocked the container"
    end
    if command == "native-loot-vehicle-locks" or command == "native-loot-restore-key-unlocks" then
        local restore = command == "native-loot-restore-key-unlocks"
        if phase ~= (restore and "hosting" or "connected") or fields[3] ~= state.map
            or #fields < 5 or #fields > 403 or #fields % 2 ~= 1 then
            error("native_loot_vehicle_lock_scope_unavailable", 0)
        end
        local locks = {}
        for index = 4, #fields, 2 do
            local profile, flag = loot.profile(state.map, fields[index]), fields[index + 1]
            local current = profile and profile.lockable and input.update(state, phase, profile)
            if not current or locks[current.crate] ~= nil or type(current.crate.Locked) ~= "boolean"
                or (restore and (not profile.key_unlock_only or current.crate.Looted ~= false or flag ~= "0"))
                or (flag ~= "0" and flag ~= "1") then error("native_loot_vehicle_lock_scope_unavailable", 0) end
            locks[current.crate] = flag == "1"
        end
        for crate, locked in pairs(locks) do
            crate.Locked = locked
            if crate.Locked ~= locked then error("native_loot_vehicle_lock_projection_unverified", 0) end
        end
        return "stock container locks synchronized"
    end
    if command ~= "native-loot-crate-fixture" and command ~= "native-loot-crate-project" then return end
    local target = command == "native-loot-crate-fixture" and fields[3] or fields[4]
    local current = input.update(state, phase, loot.profile(state.map, target))
    if not current then error("native_loot_crate_input_scope_unavailable", 0) end
    if command == "native-loot-crate-fixture" and (#fields == 2 or #fields == 3) then return fixture(current) end
    if command == "native-loot-crate-project" and fields[3] == "exhausted" then
        if current.profile.random_lid and #fields == 5 then return project(current, tonumber(fields[5])) end
        if not current.profile.random_lid and (#fields == 3 or #fields == 4) then return project(current) end
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
        local active, sequence, pried, guest, info = false, 0, false, 0, {}
        if current then
            local box_state, box_address
            active, sequence, box_state, box_address, pried, guest = SilverfishReadNativeLootCrateInput(profile.index)
            scope = active and "active" or "scope_inactive"
            if active then info = observe_scene(current, box_state, box_address) end
        end
        rows[#rows + 1] = status_row("native-loot-crate-diagnostic", profile.map, profile.name, phase,
            scope or "-", info.door_state or "-", info.box_state or "-",
            info.looted == nil and "-" or (info.looted and 1 or 0), info.collision or "-", info.roll or "-")
        if info.qualified then
            local angle = observe_angle(profile, info.door)
            local spent = exhausted(current.crate, info.door, info.box, info.second, open_poses(profile, info.second, angle),
                profile.door_locations or {}, profile)
            local status = spent and "exhausted" or "ready"
            local args = spent and angle and {angle} or {}
            rows[#rows + 1] = status_row("native-loot-crate-input", profile.map, profile.name, status, table.unpack(args))
            if profile.lockable and type(current.crate.Locked) == "boolean" then
                rows[#rows + 1] = status_row("native-loot-vehicle-lock", profile.map, profile.name, current.crate.Locked and 1 or 0)
            end
            if current.host and info.looted == false and info.box and (info.collision == 1 or info.collision == 3) then
                local verified, active = pcall(loot.temporary_activation, current.crate, current.pawn)
                if verified and active == false then
                    rows[#rows + 1] = status_row("native-loot-crate-replenished", profile.map, profile.name, current.name)
                end
            end
        end
        if active and pried and guest and guest ~= 0 then
            for _, detail in ipairs(state.guests or {}) do
                if valid(detail.pawn) and detail.pawn:GetAddress() == guest then
                    rows[#rows + 1] = status_row("native-loot-guest-pry", profile.map, profile.name, detail.name, sequence)
                    break
                end
            end
        elseif active and sequence > latest then
            latest = sequence
            reported = status_row("native-loot-crate-interaction", profile.map, profile.name,
                current.name, sequence, pried and "pried" or sequence == fixture_sequences[profile.index] and "fixture" or "stock")
        end
    end
    if reported then rows[#rows + 1] = reported end
    if tool_report and phase == "hosting" and tool_report.map == state.map then
        local report = tool_report
        rows[#rows + 1] = status_row("native-loot-guest-" .. report.kind .. "-use", report.map, report.crate, report.pawn,
            report.guid, report.before, report.after)
    end
    return rows
end

return input
