local loot = require("native_loot")
local input = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local client_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local crate_path = loot.crate:match("^%S+ (.+)$")
local open_rotation = { Pitch = 0, Yaw = 0, Roll = -95 }
local binding
local fixture_sequence

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
    binding, fixture_sequence = nil, nil
    if enabled and type(SilverfishClearNativeLootCrateInput) == "function" then
        SilverfishClearNativeLootCrateInput()
    end
end

function input.update(state, phase)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    local host = phase == "hosting" and state.server == false
    if not enabled or state.map ~= loot.map or not state.listening or not valid(state.world)
        or not valid(pawn) or not same(pawn:GetWorld(), state.world)
        or (not host and (phase ~= "connected" or state.server ~= true)) then
        if binding then input.clear() end
        return nil, "lua_scope"
    end
    if type(SilverfishSetNativeLootCrateInputScope) ~= "function"
        or type(SilverfishReadNativeLootCrateInput) ~= "function" then
        return nil, "lua_addon"
    end

    local accepted, reason = SilverfishSetNativeLootCrateInputScope(pawn:GetAddress(), host)
    if not accepted then
        if binding then input.clear() end
        return nil, reason or "native_scope"
    end
    local crate = StaticFindObject(crate_path)
    if not valid(crate) or crate:GetFullName() ~= loot.crate or not same(crate:GetWorld(), state.world) then
        if binding then input.clear() end
        return nil, "lua_crate"
    end
    if not binding or not same(binding.crate, crate) or not same(binding.pawn, pawn) then
        binding = { crate = crate, pawn = pawn, name = detail.name, host = host }
        fixture_sequence = nil
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
    return report
end

local function scene(current)
    local active, _, box_state, box_address = SilverfishReadNativeLootCrateInput()
    if not active then return end
    local report = observe_scene(current, box_state, box_address)
    if report.qualified then return report.door, report.box end
end

local function close_angle(actual, expected)
    return type(actual) == "number" and math.abs((actual - expected + 180) % 360 - 180) < 0.05
end

local function exhausted(crate, door, box)
    if crate.Looted ~= true or not door or box and box:GetCollisionEnabled() ~= 0 then return false end
    local rotation = door.RelativeRotation
    return rotation and close_angle(rotation.Pitch, open_rotation.Pitch)
        and close_angle(rotation.Yaw, open_rotation.Yaw) and close_angle(rotation.Roll, open_rotation.Roll)
end

local function project(current)
    local crate = current.crate
    local door, box = scene(current)
    if exhausted(crate, door, box) then return "stock crate exhausted projection verified" end
    local original_looted = crate.Looted
    if not door or type(original_looted) ~= "boolean" or not box and not original_looted then
        error("native_loot_crate_exact_scene_unavailable", 0)
    end
    local rotation = door.RelativeRotation
    if not rotation or not close_angle(rotation.Pitch, rotation.Pitch) or not close_angle(rotation.Yaw, rotation.Yaw)
        or not close_angle(rotation.Roll, rotation.Roll) then
        error("native_loot_crate_door_rotation_unavailable", 0)
    end
    local original = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    local collision = box and box:GetCollisionEnabled()
    if box and collision ~= 0 and collision ~= 1 and collision ~= 3 then
        error("native_loot_crate_box_collision_unavailable", 0)
    end

    -- Apply only the spent appearance. The cook opens the cabinet with roll
    -- -95 and removes its query Box. No stock open/respawn graph is called.
    local invoked, verified = pcall(function()
        if box then box:SetCollisionEnabled(0) end
        door:K2_SetRelativeRotation(open_rotation, false, {}, false)
        crate.Looted = true
        return exhausted(crate, door, box)
    end)
    if not invoked or not verified then
        local restored, matches = pcall(function()
            crate.Looted = original_looted
            door:K2_SetRelativeRotation(original, false, {}, false)
            if box then box:SetCollisionEnabled(collision) end
            local readback = door.RelativeRotation
            return crate.Looted == original_looted and (not box or box:GetCollisionEnabled() == collision)
                and close_angle(readback.Pitch, original.Pitch) and close_angle(readback.Yaw, original.Yaw)
                and close_angle(readback.Roll, original.Roll)
        end)
        error(restored and matches and "native_loot_crate_projection_refused"
            or "mutation_unverified_native_loot_crate_projection", 0)
    end
    return "stock crate exhausted projection verified"
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
    local before_active, before = SilverfishReadNativeLootCrateInput()
    if before_active ~= true then error("native_loot_crate_stock_fixture_scope_unavailable", 0) end
    local original = pawn.InteractionTarget_Component
    pawn.InteractionTarget_Component = box
    local invoked, ok, detail = pcall(SilverfishProbeNativeInteractInput, pawn:GetAddress(), true)
    pawn.InteractionTarget_Component = valid(original) and original or nil
    local active, sequence = SilverfishReadNativeLootCrateInput()
    if not invoked or not ok or detail ~= "stock_started_consumed" or not active or sequence <= before
        or crate.Looted ~= false then
        error("native_loot_crate_stock_fixture_receipt_unverified", 0)
    end
    fixture_sequence = sequence
    return "stock crate interaction intercepted"
end

function input.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-loot-crate-fixture" and command ~= "native-loot-crate-project" then return end
    local current = input.update(state, phase)
    if not current then error("native_loot_crate_input_scope_unavailable", 0) end
    if command == "native-loot-crate-fixture" and #fields == 2 then return fixture(current) end
    if command == "native-loot-crate-project" and #fields == 3 and fields[3] == "exhausted" then
        return project(current)
    end
    error("native_loot_crate_input_arguments", 0)
end

function input.rows(state, phase, status_row)
    if not enabled or state.map ~= loot.map or not state.listening or phase ~= "hosting" and phase ~= "connected" then
        return {}
    end
    local current, scope = input.update(state, phase)
    local active, sequence, scene_report
    if current then
        local box_state, box_address
        active, sequence, box_state, box_address = SilverfishReadNativeLootCrateInput()
        scope = active and "active" or "scope_inactive"
        if active then scene_report = observe_scene(current, box_state, box_address) end
    end
    local info = scene_report or {}
    local diagnostic = status_row("native-loot-crate-diagnostic", loot.map, loot.crate, phase,
        scope or "-", info.door_state or "-", info.box_state or "-",
        info.looted == nil and "-" or (info.looted and 1 or 0), info.collision or "-", info.roll or "-")
    local rows = { diagnostic }
    if scene_report and scene_report.qualified then
        rows[#rows + 1] = status_row("native-loot-crate-input", loot.map, loot.crate,
            exhausted(current.crate, info.door, info.box) and "exhausted" or "ready")
    end
    if active and sequence > 0 then
        rows[#rows + 1] = status_row("native-loot-crate-interaction", loot.map, loot.crate,
            current.name, sequence, sequence == fixture_sequence and "fixture" or "stock")
    end
    return rows
end

return input
