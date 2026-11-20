local fixture = {}
local guest_save = require("guest_save")
local mindcore = require("native_mindcore")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local install = "native-mindcore-install-fixture"
local approach = "native-mindcore-approach-fixture"
local carry_verb = "native-mindcore-carry-fixture"
local input_fixture = enabled or os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world_name = "World " .. map .. ".DataSilo_01_P"
local core_path = map .. ".DataSilo_01_P:PersistentLevel.MindCore_BP_C_1"
local core_name = "MindCore_BP_C " .. core_path
local cradle_origin = {X = 0, Y = 6750, Z = 6600}
-- The stock capsule sits on the pitched mount, which is attached to the raised socket.
local capsule_center = {X = 0, Y = 6662.06, Z = 6719.47}
local core_origin = {X = 714.3901, Y = -1686.9511, Z = 118.3686}

local function refuse(reason) error("native_mindcore_fixture_refused_" .. reason, 0) end
local function valid(value) return not not (value and value:IsValid()) end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) <= 10000000 end
local function point(p) return p and finite(p.X) and finite(p.Y) and finite(p.Z) end
local function within(a, b, distance)
    return point(a) and point(b) and (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= distance^2
end
local function coordinates(value)
    return point(value) and string.format("%.1f,%.1f,%.1f", value.X, value.Y, value.Z) or "unavailable"
end

local carry, diagnostic

local function vector(value)
    if point(value) then return {X = value.X, Y = value.Y, Z = value.Z} end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll}
    end
end

local function object_name(value)
    return valid(value) and value:GetFullName():gsub("[%c]", " "):sub(1, 180) or "-"
end

local function exact(value, path)
    return valid(value) and same(value:GetClass(), StaticFindObject(path))
end

local function component(value, owner, name)
    return exact(value, "/Script/Engine." .. name) and same(value:GetOwner(), owner)
end

local function simulating(frame)
    return frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find))
end

local function local_seat(state, phase)
    local pawn, controller, world = state.local_pawn and state.local_pawn.pawn, state.controller, state.world
    local host = phase == "hosting" and not state.server
    local guest = phase == "connected" and state.server
    if not (host or guest) or state.map ~= map or not state.listening or not valid(world)
        or world:GetFullName() ~= world_name or not valid(world.NetDriver) or not same(world.NetDriver.World, world)
        or valid(world.NetDriver.ServerConnection) ~= (guest == true)
        or not exact(controller, "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")
        or not controller:IsLocalController() or not same(controller:GetWorld(), world)
        or not exact(pawn, "/Game/Classes/Player/Player_FP.Player_FP_C") or not pawn:IsLocallyControlled()
        or not same(pawn:GetWorld(), world) or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or pawn:IsActorBeingDestroyed() or pawn:HasAuthority() ~= (host == true) then return end
    return pawn, controller, host and "host" or "guest"
end

local function carry_context(state, phase, name)
    local pawn, controller, role = local_seat(state, phase)
    if role ~= "host" or pawn:GetFullName() ~= name or pawn.Dead ~= false or players.is_downed(pawn) ~= false then
        refuse("carry_exact_standing_host_required")
    end
    local scene = mindcore.scene(state, phase)
    if not same(pawn, scene.host) or scene.status ~= 0 or scene.installed then refuse("carry_console_not_dormant") end
    local handle, camera, capsule = pawn.PhysicsHandle, pawn.Camera, pawn.CapsuleComponent
    if not component(handle, pawn, "PhysicsHandleComponent") or not component(camera, pawn, "CameraComponent")
        or not component(capsule, pawn, "CapsuleComponent") then refuse("carry_stock_components_unavailable") end
    local half, range, max_weight = capsule:GetScaledCapsuleHalfHeight(), pawn.InteractionRange, pawn.MaxHoldWeight
    local mass = scene.frame:GetMass()
    if not finite(half) or half < 20 or half > 150 or not finite(range) or range < 200 or range > 600
        or not finite(mass) or mass <= 0 or not finite(max_weight) or max_weight < mass
        or simulating(scene.frame) ~= true or scene.core:GetActorEnableCollision() ~= true then
        refuse(("carry_stock_physics:half=%s:range=%s:mass=%s:max=%s:sim=%s:collision=%s"):format(
            tostring(half), tostring(range), tostring(mass), tostring(max_weight),
            tostring(simulating(scene.frame)), tostring(scene.core:GetActorEnableCollision())))
    end
    return {pawn = pawn, controller = controller, world = scene.world, core = scene.core, frame = scene.frame,
        handle = handle, camera = camera, half = half, range = range}
end

local function holding(context)
    return same(context.pawn.HeldObject, context.core)
        and same(context.handle:GetGrabbedComponent(), context.frame)
end

local function vacant(context)
    return not valid(context.pawn.HeldObject) and not valid(context.handle:GetGrabbedComponent())
end

local function hold_diagnostic(context)
    return "held=" .. object_name(context.pawn.HeldObject) .. ":handle=" .. object_name(context.handle:GetGrabbedComponent())
end

local function clock(context)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local now = valid(statics) and statics:GetRealTimeSeconds(context.pawn)
    if not finite(now) then refuse("carry_stock_clock_unavailable") end
    return now
end

local color = {R = 0, G = 0, B = 0, A = 0}
local function floor_at(context, x, y, z)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local hit = {}
    if not valid(library) or library:LineTraceSingle(context.pawn, {X = x, Y = y, Z = z + 95},
        {X = x, Y = y, Z = z - 235}, 0, false, {context.pawn, context.core}, 0, hit, true, color, color, 0) ~= true then return end
    hit = hit.OutHit or hit
    if point(hit.ImpactPoint) and point(hit.ImpactNormal) and hit.ImpactNormal.Z >= 0.7 then return hit.ImpactPoint.Z end
end

local function aim_at(context, eye, target, delta_z)
    local dx, dy, dz = target.X-eye.X, target.Y-eye.Y, target.Z + delta_z-eye.Z
    local angle = {Pitch = math.deg(math.atan(dz, math.sqrt(dx*dx + dy*dy))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
    context.controller:SetControlRotation(angle)
    context.camera:K2_SetWorldRotation(angle, false, {}, true)
    context.pawn:LookTrace()
end

local function stock_focus(context)
    local pawn = context.pawn
    return same(pawn.ActorBeingLookedAt, context.core) and not valid(pawn.InteractionTarget_Component)
        and finite(pawn.ActorLookedAt_Distance) and pawn.ActorLookedAt_Distance > 0
        and pawn.ActorLookedAt_Distance < context.range
end

local function approach_core(context)
    local pawn, target = context.pawn, vector(context.core:K2_GetActorLocation())
    local prior = {position = vector(pawn:K2_GetActorLocation()), facing = rotation(pawn:K2_GetActorRotation()),
        control = rotation(context.controller:GetControlRotation()), camera = rotation(context.camera.RelativeRotation)}
    if not within(target, core_origin, 300) or not prior.position or not prior.facing or not prior.control or not prior.camera then
        refuse("carry_stock_source_pose:" .. coordinates(target))
    end
    local floors, poses, moved = 0, 0, false
    for _, distance in ipairs({150, 185, 220}) do
        for bearing = 0, 315, 45 do
            local angle = math.rad(bearing)
            local dx, dy = math.cos(angle), math.sin(angle)
            local x, y = target.X + distance*dx, target.Y + distance*dy
            local z = floor_at(context, x, y, target.Z)
            local behind = z and floor_at(context, x + 85*dx, y + 85*dy, target.Z)
            if behind and math.abs(behind-z) <= 25 then
                floors = floors + 1
                local position = {X = x, Y = y, Z = z + context.half + 2}
                if pawn:K2_TeleportTo(position, prior.facing) == true then
                    moved = true
                    local eye = vector(context.camera:K2_GetComponentLocation())
                    if eye and within(position, pawn:K2_GetActorLocation(), 12) then
                        poses = poses + 1
                        for _, vertical in ipairs({0, 24, -24, 48, -48}) do
                            aim_at(context, eye, target, vertical)
                            if stock_focus(context) and
                                within(context.core:K2_GetActorLocation(), target, 15) then
                                return {X = dx, Y = dy}, prior
                            end
                        end
                    end
                end
            end
        end
    end
    local focus = object_name(pawn.ActorBeingLookedAt) .. ":component=" .. object_name(pawn.InteractionTarget_Component)
        .. ":distance=" .. tostring(pawn.ActorLookedAt_Distance)
    if moved then
        if pawn:K2_TeleportTo(prior.position, prior.facing) ~= true
            or not within(prior.position, pawn:K2_GetActorLocation(), 12) then
            error("mutation_unverified_native_mindcore_carry_pawn_restore", 0)
        end
        context.controller:SetControlRotation(prior.control)
        context.camera:K2_SetRelativeRotation(prior.camera, false, {}, true)
        pawn:LookTrace()
    end
    refuse(("carry_no_stock_focus:floor=%d:pose=%d:look=%s"):format(floors, poses, focus))
end

local function stock_press(context, operation)
    local address = context.pawn:GetAddress()
    local ok, reason, sequence = SilverfishProbeNativeInteractInput(address, false)
    if ok ~= true or reason ~= "stock_started" or math.type(sequence) ~= "integer" or sequence < 1 then
        refuse(("carry_%s_stock_input:%s:%s"):format(operation, tostring(reason), hold_diagnostic(context)))
    end
    local seen, pawn_address = SilverfishTakeNativeInteractPress(address)
    if seen ~= sequence or pawn_address ~= address then
        error("mutation_unverified_native_mindcore_carry_stock_receipt:" .. operation, 0)
    end
end

local function carry_matches(context)
    return carry and same(carry.world, context.world) and same(carry.pawn, context.pawn)
        and same(carry.core, context.core) and same(carry.handle, context.handle)
end

local function perform_carry(state, phase, name, operation)
    if not input_fixture or type(SilverfishProbeNativeInteractInput) ~= "function"
        or type(SilverfishTakeNativeInteractPress) ~= "function" then refuse("carry_stock_input_fixture_unavailable") end
    local context = carry_context(state, phase, name)
    if operation == "grab" then
        if carry_matches(context) or not vacant(context) then refuse("carry_already_started:" .. hold_diagnostic(context)) end
        local direction, prior = approach_core(context)
        stock_press(context, "grab")
        if not holding(context) then refuse("carry_stock_grab_readback:" .. hold_diagnostic(context)) end
        carry = {world = context.world, pawn = context.pawn, core = context.core, handle = context.handle,
            name = name, direction = direction, prior = prior, status = "held", pawn_move = 0, core_move = 0}
        return "stock Interact bound the exact Core to the host physics handle"
    end
    if not carry_matches(context) then refuse("carry_previous_grab_required") end
    if not holding(context) then refuse("carry_stock_hold_lost:" .. hold_diagnostic(context)) end
    if operation == "step" then
        if carry.status ~= "held" then refuse("carry_step_already_requested") end
        local now = clock(context)
        carry.status, carry.expires = "carrying", now + 4
        carry.next_sample = now + 0.3
        return "stock movement pending after physics-handle settling"
    end
    if carry.status ~= "held" and carry.status ~= "carried" then refuse("carry_release_not_ready") end
    context.pawn:LookTrace()
    if valid(context.pawn.InteractionTarget_Component) then
        refuse("carry_release_other_stock_target:" .. object_name(context.pawn.InteractionTarget_Component))
    end
    stock_press(context, "release")
    if not vacant(context) or simulating(context.frame) ~= true then
        error("mutation_unverified_native_mindcore_carry_stock_release:" .. hold_diagnostic(context), 0)
    end
    context.controller:SetControlRotation(carry.prior.control)
    context.camera:K2_SetRelativeRotation(carry.prior.camera, false, {}, true)
    context.pawn:LookTrace()
    carry.status = "released"
    return "stock Interact released the exact Core and left its physics active"
end

local function horizontal(a, b)
    return math.sqrt((a.X-b.X)^2 + (a.Y-b.Y)^2)
end

local function along(a, b, direction)
    return (a.X-b.X)*direction.X + (a.Y-b.Y)*direction.Y
end

local function settle_step(state, phase)
    local context = carry_context(state, phase, carry.name)
    if not carry_matches(context) or not holding(context) then refuse("carry_step_stock_hold_lost:" .. hold_diagnostic(context)) end
    local now = clock(context)
    local pawn, core = vector(context.pawn:K2_GetActorLocation()), vector(context.core:K2_GetActorLocation())
    if not within(pawn, core, context.range) then
        refuse("carry_step_seat_or_core_pose:pawn=" .. coordinates(pawn) .. ":core=" .. coordinates(core))
    end
    if now >= carry.expires then
        refuse(("carry_motion_timeout:pawn=%.1f:core=%.1f:walk=%s"):format(
            carry.pawn_move, carry.core_move, tostring(carry.pawn_start ~= nil)))
    end
    if now < carry.next_sample then return end
    if not carry.pawn_start then
        local prior = carry.sample
        carry.sample, carry.next_sample = core, now + 0.2
        if not prior or horizontal(core, prior) > 8 or math.abs(core.Z-prior.Z) > 8 then return end
        carry.pawn_start, carry.core_start = pawn, core
        players.command({"mindcore-carry", "walk", tostring(carry.direction.X), tostring(carry.direction.Y), "240"}, state, phase)
        return
    end
    carry.pawn_move, carry.core_move = horizontal(pawn, carry.pawn_start), horizontal(core, carry.core_start)
    local pawn_along, core_along = along(pawn, carry.pawn_start, carry.direction), along(core, carry.core_start, carry.direction)
    if carry.pawn_move > 300 or carry.core_move > 300
        or math.abs(pawn.Z-carry.pawn_start.Z) > 85 or math.abs(core.Z-carry.core_start.Z) > 85 then
        refuse("carry_motion_outside_short_walk:pawn=" .. coordinates(pawn) .. ":core=" .. coordinates(core))
    end
    if pawn_along >= 20 and core_along >= 20 then carry.status = "carried" end
end

local function report_failure(state, operation, reason)
    diagnostic = {world = state.world, operation = operation, reason = tostring(reason):gsub("[%c]", " "):sub(1, 500)}
end

function fixture.clear()
    carry, diagnostic = nil, nil
end

function fixture.sync(state, phase)
    if not enabled or not (carry or diagnostic) then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "hosting" or state.map ~= map or not valid(pawn)
        or carry and (not same(state.world, carry.world) or not same(pawn, carry.pawn))
        or diagnostic and not same(state.world, diagnostic.world) then
        fixture.clear()
        return
    end
    if carry and carry.status == "carrying" then
        local ok, reason = pcall(settle_step, state, phase)
        if not ok then
            carry.status = "held"
            report_failure(state, "step", reason)
        end
    end
end

local function core_pose_row(state, phase, status_row)
    if not input_fixture then return end
    local _, _, role = local_seat(state, phase)
    if not role then return end
    local core = StaticFindObject(core_path)
    if not exact(core, "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C") or core:GetFullName() ~= core_name
        or not same(core:GetWorld(), state.world) or core:IsActorBeingDestroyed() then return end
    local frame, pose = core.PropMesh, vector(core:K2_GetActorLocation())
    if not pose or not component(frame, core, "StaticMeshComponent") or not same(core.RootComponent, frame) then return end
    local sim, collision, replication, movement, local_role = simulating(frame), core:GetActorEnableCollision(),
        core.bReplicates, core.bReplicateMovement, core:GetLocalRole()
    if type(sim) ~= "boolean" or type(collision) ~= "boolean" or type(replication) ~= "boolean"
        or type(movement) ~= "boolean" or math.type(local_role) ~= "integer" or local_role < 0 or local_role > 3 then return end
    return status_row("native-mindcore-core-pose", map, role, core_name,
        string.format("%.3f", pose.X), string.format("%.3f", pose.Y), string.format("%.3f", pose.Z),
        sim and 1 or 0, collision and 1 or 0, replication and 1 or 0, movement and 1 or 0, local_role)
end

function fixture.rows(state, phase, status_row)
    local rows = {}
    if state.map ~= map then return rows end
    if phase == "hosting" and carry and same(state.world, carry.world) then
        rows[#rows + 1] = status_row("native-mindcore-carry", map, carry.name, core_name, carry.status,
            string.format("%.3f", carry.pawn_move), string.format("%.3f", carry.core_move))
    end
    if phase == "hosting" and diagnostic and same(state.world, diagnostic.world) then
        rows[#rows + 1] = status_row("native-mindcore-carry-diagnostic", map, diagnostic.operation, diagnostic.reason)
    end
    local readable, row = pcall(core_pose_row, state, phase, status_row)
    if readable and row then rows[#rows + 1] = row end
    return rows
end

local function move_core(scene, pawn)
    if pawn ~= scene.host:GetFullName() then refuse("exact_host_pawn_changed") end
    if scene.host.Dead ~= false then refuse("host_not_living") end
    if valid(scene.host.HeldObject) then refuse("host_holding_object") end
    -- Send_PowerSignal's Powered argument is transient. The stock USaveGame flag and
    -- the cradle's live Status already agree in mindcore.scene.
    if scene.status ~= 0 or scene.installed then refuse("console_not_dormant") end
    local cap = scene.cradle.Capsule
    local capsule_class = StaticFindObject("/Script/Engine.CapsuleComponent")
    if not valid(cap) or not same(cap:GetClass(), capsule_class) or not same(cap:GetOwner(), scene.cradle) then
        refuse("stock_capsule_unavailable")
    end
    local socket = scene.cradle.SM_MindCoffin_Socket
    if not valid(socket) or not same(socket:GetOwner(), scene.cradle)
        or not same(cap:GetAttachParent(), scene.mount) or not same(scene.mount:GetAttachParent(), socket)
        or not same(socket:GetAttachParent(), scene.cradle.RootComponent) then refuse("stock_capsule_attachment_changed") end
    if cap:GetGenerateOverlapEvents() ~= true then refuse("stock_capsule_overlap_disabled") end
    if scene.core:GetActorEnableCollision() ~= true then refuse("stock_core_collision_disabled") end
    if scene.frame:GetGenerateOverlapEvents() ~= true then refuse("stock_core_overlap_disabled") end
    if cap:IsOverlappingActor(scene.core) ~= false then refuse("stock_core_already_overlapping") end
    if scene.frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then refuse("stock_core_not_simulating") end

    local center, source = cap:K2_GetComponentLocation(), scene.core:K2_GetActorLocation()
    if not within(source, core_origin, 300) then
        refuse("stock_core_displaced:" .. coordinates(source))
    end
    local anchor = scene.cradle:K2_GetActorLocation()
    if not within(anchor, cradle_origin, 3) or not within(center, capsule_center, 3) then
        refuse("stock_capsule_displaced:cradle=" .. coordinates(anchor) .. ":capsule=" .. coordinates(center))
    end
    local moved, result = pcall(function()
        return scene.core:K2_SetActorLocation({X = center.X, Y = center.Y, Z = center.Z}, false, {}, true)
    end)
    if not moved or result ~= true then error("mutation_unverified_native_mindcore_fixture_core_move", 0) end
    if cap:IsOverlappingActor(scene.core) ~= true
        and not within(scene.core:K2_GetActorLocation(), scene.mount:K2_GetComponentLocation(), 12) then
        error("mutation_unverified_native_mindcore_fixture_stock_overlap", 0)
    end
    return "headless exact stock Core moved into authored Capsule overlap"
end

local function place_guest(scene, state, phase, name, variant)
    local pawn, detached = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(detached) or same(detached, scene.canonical)
        or pawn.Dead ~= false or not players.is_standing_guest(pawn, name) then refuse("standing_guest_unavailable") end
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local height = valid(pawn.CapsuleComponent) and pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local range, origin = pawn.InteractionRange, scene.controls:K2_GetComponentLocation()
    if not valid(library) or not point(origin) or not finite(height) or height < 20 or height > 150
        or not finite(range) or range <= 0 or range > 1000 then refuse("stock_controls_geometry_unavailable") end
    local old, rotation = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local anchor, color = scene.cradle:K2_GetActorLocation(), {R = 0, G = 0, B = 0, A = 0}
    local moved = false
    local offsets = {{0, -130}, {0, 130}, {-130, 0}, {130, 0},
                              {-95, -95}, {95, -95}, {-95, 95}, {95, 95},
                              {0, -170}, {0, 170}, {-170, 0}, {170, 0}}
    if variant then
        if not offsets[variant] then refuse("invalid_console_approach") end
        offsets = {offsets[variant]}
    end
    for _, offset in ipairs(offsets) do
        local x, y = origin.X + offset[1], origin.Y + offset[2]
        local hit = {}
        local traced = library:LineTraceSingle(pawn, {X = x, Y = y, Z = origin.Z + 90},
            {X = x, Y = y, Z = origin.Z - 300}, 0, false, {pawn, scene.host, scene.cradle, scene.core},
            0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        if traced == true and point(hit.ImpactPoint) and point(hit.ImpactNormal) and hit.ImpactNormal.Z >= .7 then
            local target = {X = x, Y = y, Z = hit.ImpactPoint.Z + height + 4}
            if within(target, origin, range) and within(target, anchor, range) and pawn:K2_TeleportTo(target, rotation) then
                moved = true
                local actual = pawn:K2_GetActorLocation()
                if within(actual, target, 12) and within(actual, origin, range) and within(actual, anchor, range) then
                    pawn:ForceNetUpdate()
                    return "headless detached guest reached the exact stock console controls"
                end
            end
        end
    end
    if moved then
        if not pawn:K2_TeleportTo(old, rotation) or not within(pawn:K2_GetActorLocation(), old, 12) then
            error("mutation_unverified_native_mindcore_fixture_guest_restore", 0)
        end
        pawn:ForceNetUpdate()
    end
    refuse("console_approach_no_safe_floor")
end

function fixture.command(fields, state, phase)
    local verb, name = fields[2], fields[3]
    if verb ~= install and verb ~= approach and verb ~= carry_verb then return end
    if not enabled then refuse("not_enabled") end
    if verb == carry_verb then
        local operation = fields[4]
        if #fields ~= 4 or type(name) ~= "string" or #name > 512 or not name:match("^Player_FP_C ")
            or (operation ~= "grab" and operation ~= "step" and operation ~= "release") then
            refuse("carry_exact_pawn_and_operation_required")
        end
        diagnostic = nil
        local ok, result = pcall(perform_carry, state, phase, name, operation)
        if not ok then
            report_failure(state, operation, result)
            error(result, 0)
        end
        return result
    end
    local variant = fields[4] and math.tointeger(tonumber(fields[4]))
    local shape = #fields == 3 or verb == approach and #fields == 4 and variant
    if not shape or type(name) ~= "string" or #name > 512 or not name:match("^Player_FP_C ") then
        refuse("exact_pawn_required")
    end
    local scene = mindcore.scene(state, phase)
    if verb == install then return move_core(scene, name) end
    return place_guest(scene, state, phase, name, variant)
end

return fixture
