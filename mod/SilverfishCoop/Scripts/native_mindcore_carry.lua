local carry = {}
local guest_save = require("guest_save")
local mindcore = require("native_mindcore")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local level = map .. ".DataSilo_01_P:PersistentLevel."
local core_name = "MindCore_BP_C " .. level .. "MindCore_BP_C_1"
local cradle_name = "MindCore_Cradle_BP_C " .. level .. "MindCore_Cradle_BP_C_1"
local pawn_prefix = "Player_FP_C " .. level
local verb = "native-mindcore-carry"
local max_sequence = 9007199254740991
local trace_color = {R = 0, G = 0, B = 0, A = 0}
local aim_path, active, last, failed_world
local used = {}
local used_order = {}

local function refuse(reason) error("native_mindcore_carry_refused_" .. reason, 0) end
local function valid(value) return not not (value and value:IsValid()) end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end
local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < 10000000
end
local function point(value) return value and finite(value.X) and finite(value.Y) and finite(value.Z) end
local function length_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end
local function component(value, owner, name)
    return valid(value) and same(value:GetClass(), StaticFindObject("/Script/Engine." .. name)) and same(value:GetOwner(), owner)
end

local function lease_id(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") and value:find("[^0]")
end

local function sequence(value)
    if type(value) ~= "string" or #value > 16 or not value:match("^[1-9]%d*$") then return end
    local number = math.tointeger(tonumber(value))
    if number and number <= max_sequence then return number end
end

local function angle(value, maximum)
    if type(value) ~= "string" or #value > 9 then return end
    local integer, fraction = value:match("^%-?(%d+)%.(%d+)$")
    integer = integer or value:match("^%-?(%d+)$")
    if not integer or #integer > 3 or #integer > 1 and integer:sub(1, 1) == "0"
        or fraction and #fraction > 4 then return end
    local number = tonumber(value)
    if finite(number) and math.abs(number) <= maximum then return number end
end

local function reason_token(value, default)
    return type(value) == "string" and #value <= 96 and value:match("^[a-z][a-z0-9_]+$") and value or default
end

local function native_available()
    return type(SilverfishRunNativeMindCoreCarry) == "function" and type(SilverfishReadNativeMindCoreCarry) == "function"
end

local function dormant(state, phase)
    if not enabled or not native_available() or phase ~= "hosting" or state.map ~= map then return end
    local ok, scene = pcall(mindcore.scene, state, phase)
    if ok and scene and scene.status == 0 and not scene.installed and scene.core:GetFullName() == core_name
        and scene.cradle:GetFullName() == cradle_name and scene.core.bReplicates == true
        and scene.core.bReplicateMovement == true then return scene end
end

local function guest(scene, state, phase, name)
    local ok, pawn, detached, character = pcall(guest_save.exact_item_context, state, phase, name)
    if not ok or not valid(pawn) or not valid(detached) or type(character) ~= "string" or character == ""
        or same(scene.host, pawn) or same(scene.canonical, detached) or not same(pawn.Save, detached)
        or not same(pawn:GetWorld(), scene.world) or pawn:GetFullName() ~= name or not pawn:HasAuthority()
        or pawn.Dead ~= false or pawn:IsActorBeingDestroyed() or not players.is_standing_guest(pawn, name) then return end
    local camera, handle = pawn.Camera, pawn.PhysicsHandle
    if not component(camera, pawn, "CameraComponent") or not component(handle, pawn, "PhysicsHandleComponent") then return end
    return {scene = scene, pawn = pawn, pawn_address = pawn:GetAddress(), name = name,
        save = detached, character = character, camera = camera, handle = handle}
end

local function qualified_body(context)
    local scene, pawn = context.scene, context.pawn
    local range, limit, mass = pawn.InteractionRange, pawn.MaxHoldWeight, scene.frame:GetMass()
    if not finite(range) or range <= 0 or range > 1000 or not finite(limit) or not finite(mass)
        or mass <= 0 or mass > limit or scene.core:GetActorEnableCollision() ~= true
        or scene.frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then return end
    return range
end

local function free(context)
    local core = context.scene.core
    return core.BeingHeld == false and not valid(core["Holding Actor"])
        and not valid(context.pawn.HeldObject) and not valid(context.handle:GetGrabbedComponent())
end

local function holding(context)
    local core = context.scene.core
    return core.BeingHeld == true and same(core["Holding Actor"], context.pawn)
        and same(context.pawn.HeldObject, core) and same(context.handle:GetGrabbedComponent(), context.scene.frame)
end

local function stock_sight(context, range, pitch, yaw)
    local eye, body = context.camera:K2_GetComponentLocation(), context.pawn:K2_GetActorLocation()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not point(eye) or not point(body) or length_squared(eye, body) > 150^2 or not valid(trace) then return end
    local p, y = math.rad(pitch), math.rad(yaw)
    local target = {X = eye.X + math.cos(p) * math.cos(y) * range,
        Y = eye.Y + math.cos(p) * math.sin(y) * range, Z = eye.Z + math.sin(p) * range}
    local hit = {}
    -- Stock LookTrace fills ActorBeingLookedAt from complex Visibility.
    if trace:LineTraceSingle(context.pawn, eye, target, 1, true, {}, 0, hit, true, trace_color, trace_color, 0) ~= true then return end
    hit = hit.OutHit or hit
    local part = hit.Component and hit.Component:Get()
    if not valid(part) or not same(part:GetOwner(), context.scene.core) or not point(hit.ImpactPoint) then return end
    local squared = length_squared(eye, hit.ImpactPoint)
    if squared > 1 and squared < range^2 then return math.sqrt(squared) end
end

local function run(context, action, pitch, yaw)
    return SilverfishRunNativeMindCoreCarry(context.pawn_address, context.save_address,
        context.canonical_address, context.core_address, context.cradle_address, action, pitch, yaw)
end

local function address_scope(context)
    context.save_address = context.save:GetAddress()
    context.canonical_address = context.scene.canonical:GetAddress()
    context.core_address = context.scene.core:GetAddress()
    context.cradle_address = context.scene.cradle:GetAddress()
end

local function report(context, state, reason)
    last = {world = context.scene.world, name = context.name, lease = context.lease, sequence = context.sequence,
        state = state, reason = reason}
end

local function fault(context, reason)
    failed_world = context.scene.world
    report(context, "failure", reason)
end

local function mutate(context, action, pitch, yaw)
    local invoked, ok, reason = pcall(run, context, action, pitch, yaw)
    if not invoked then reason = "mutation_unverified_native_mindcore_carry_call" end
    return invoked and ok == true, reason_token(reason, "mutation_unverified_native_mindcore_carry_reply")
end

local function focused_grab(context, pitch, yaw, distance)
    local pawn = context.pawn
    local old_actor = pawn.ActorBeingLookedAt
    local old_distance = pawn.ActorLookedAt_Distance
    if not finite(old_distance) then refuse("stock_focus_unavailable") end
    -- The authority guest may have no stock pawn Tick to refresh its focus.
    local called, success, reason = pcall(function()
        pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = context.scene.core, distance
        local readback = pawn.ActorLookedAt_Distance
        if not same(pawn.ActorBeingLookedAt, context.scene.core) or not finite(readback) or math.abs(readback - distance) > 0.01 then
            return false, "native_mindcore_carry_refused_stock_focus_binding"
        end
        return mutate(context, "grab", pitch, yaw)
    end)
    local restored, verified = pcall(function()
        pawn.ActorBeingLookedAt = valid(old_actor) and old_actor or nil
        pawn.ActorLookedAt_Distance = old_distance
        return (same(pawn.ActorBeingLookedAt, old_actor) or not valid(old_actor) and not valid(pawn.ActorBeingLookedAt))
            and pawn.ActorLookedAt_Distance == old_distance
    end)
    if not restored or not verified then return false, "mutation_unverified_native_mindcore_carry_focus_restore" end
    if not called then return false, "mutation_unverified_native_mindcore_carry_focus_call" end
    return success, reason
end

local function remember(lease)
    used[lease] = true
    used_order[#used_order + 1] = lease
    if #used_order > 64 then used[table.remove(used_order, 1)] = nil end
end

local function update_native()
    if not active then return true end
    local context = active
    local called, state, reason, address = pcall(SilverfishReadNativeMindCoreCarry)
    reason = reason_token(reason, "mutation_unverified_native_mindcore_carry_observation")
    if not called or address ~= context.pawn_address then
        fault(context, "mutation_unverified_native_mindcore_carry_identity")
        return false
    end
    if state == "held" then return true end
    if state == "released" then
        local released = reason == "stock_core_capsule_release" and "mount_released" or "released"
        if not same(failed_world, context.scene.world) then report(context, released, reason) end
        active = nil
        return true
    end
    fault(context, reason:match("^mutation_unverified_") and reason or "mutation_unverified_native_mindcore_carry_observation")
    return false
end

local function release(context, sequence_number)
    local success, reason = mutate(context, "release", 0, 0)
    local checked, vacant = pcall(free, context)
    if success and reason == "stock_core_released" and checked and vacant then
        context.sequence = sequence_number
        if not same(failed_world, context.scene.world) then report(context, "released", reason) end
        active = nil
        return true
    end
    fault(context, reason:match("^mutation_unverified_") and reason or "mutation_unverified_native_mindcore_carry_release")
    return false
end

function carry.start(bridge)
    aim_path = bridge .. "/native-mindcore-authority-aim.tsv"
end

function carry.clear(name)
    if active and (not name or name == active.name) then
        if update_native() and (not active or release(active, active.sequence)) then return true end
        error(last.reason, 0)
    end
    return true
end

function carry.command(fields, state, phase)
    if fields[2] ~= verb then return end
    local name, lease, action, number = fields[3], fields[4], fields[5], sequence(fields[6])
    if type(name) ~= "string" or #name > 512 or name:sub(1, #pawn_prefix) ~= pawn_prefix
        or not lease_id(lease) or not number or (action ~= "grab" and action ~= "drop")
        or #fields ~= (action == "grab" and 8 or 6) then refuse("request_unqualified") end
    if not enabled or not native_available() or phase ~= "hosting" or state.map ~= map then refuse("scope_unavailable") end
    if not update_native() or same(failed_world, state.world) then refuse("scope_fault") end

    if action == "drop" then
        if not active or not same(active.scene.world, state.world) or name ~= active.name or lease ~= active.lease
            or number <= active.sequence then refuse("holder_or_sequence_changed") end
        if not release(active, number) then error(last.reason, 0) end
        return "exact stock Core released"
    end
    if active or used[lease] then refuse("holder_or_sequence_changed") end
    local pitch, yaw = angle(fields[7], 89.9), angle(fields[8], 180)
    if not pitch or not yaw then refuse("aim_unqualified") end
    local scene = dormant(state, phase)
    local context = scene and guest(scene, state, phase, name)
    if not context then refuse("owner_or_core_unavailable") end
    if not free(context) then refuse("stock_core_occupied") end
    local range = qualified_body(context)
    if not range then refuse("stock_body_unqualified") end
    local distance = stock_sight(context, range, pitch, yaw)
    if not distance then refuse("stock_core_not_first_visible_hit") end
    context.lease, context.sequence = lease, number
    address_scope(context)
    local ok, reason = focused_grab(context, pitch, yaw, distance)
    local checked, held = pcall(holding, context)
    if ok and reason == "stock_core_grabbed" and checked and held then
        active = context
        remember(lease)
        report(context, "held", reason)
        return "exact stock Core held"
    end
    local checked_free, vacant = pcall(free, context)
    if ok or reason:match("^mutation_unverified_") or not checked_free or not vacant then
        fault(context, reason:match("^mutation_unverified_") and reason or "mutation_unverified_native_mindcore_carry_grab")
        active = context
        error(last.reason, 0)
    end
    error(reason, 0)
end

local function read_aim(context)
    if not aim_path then return end
    local file = io.open(aim_path, "rb")
    if not file then return end
    local data = file:read(129)
    file:close()
    if not data or #data > 128 then return end
    local lease, raw_sequence, raw_pitch, raw_yaw = data:match("^1\t([0-9a-f]+)\t(%d+)\t([^\t\r\n]+)\t([^\t\r\n]+)\n$")
    if lease ~= context.lease then return end
    local number, pitch, yaw = sequence(raw_sequence), angle(raw_pitch, 89.9), angle(raw_yaw, 180)
    if number and number > context.sequence and pitch and yaw then return number, pitch, yaw end
end

function carry.sync(state, phase)
    local context = active
    if not context or not update_native() or active ~= context then return end
    if same(failed_world, context.scene.world) then
        release(context, context.sequence)
        return
    end
    local scene = dormant(state, phase)
    local current = scene and guest(scene, state, phase, context.name)
    if not current or not same(scene.world, context.scene.world) or not same(scene.core, context.scene.core)
        or not same(current.pawn, context.pawn) or not same(current.save, context.save) or current.character ~= context.character
        or not same(current.camera, context.camera) or not same(current.handle, context.handle) or not holding(current) then
        release(context, context.sequence)
        return
    end
    local number, pitch, yaw = read_aim(context)
    if not number then return end
    local ok, reason = mutate(context, "hold", pitch, yaw)
    if ok and reason == "stock_core_renewed" then
        context.sequence = number
        report(context, "held", reason)
    elseif update_native() and active == context then
        release(context, context.sequence)
    end
end

function carry.rows(state, phase, row)
    if not enabled or phase ~= "hosting" or state.map ~= map then return {} end
    local rows = {}
    if not same(failed_world, state.world) and dormant(state, phase) then
        rows[#rows + 1] = row("native-mindcore-carry-capability", map, core_name)
    end
    if last and same(last.world, state.world) then
        rows[#rows + 1] = row("native-mindcore-authority-carry", map, core_name, last.name, last.lease,
            last.state, last.sequence, last.reason)
    end
    return rows
end

return carry
