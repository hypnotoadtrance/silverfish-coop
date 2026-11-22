local carry = {}
local guest_save = require("guest_save")
local players = require("players")

local prop_path = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C"
local core_path = "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"
local grab_path = "/Game/Classes/Player/Player_FP.Player_FP_C:Grab_Release_Object"
local color = {R = 0, G = 0, B = 0, A = 0}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local world, state, phase, hook, mailbox, sequence, next_read
local enrolled, holdings, edges, local_hold, latest = {}, {}, {}, nil, nil
local fixture_actor
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"

local function valid(value) return value and value ~= false and value:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function finite(value, limit) return type(value) == "number" and value == value and math.abs(value) <= limit end
local function point(value) return value and finite(value.X, 1e7) and finite(value.Y, 1e7) and finite(value.Z, 1e7) end
local function pose(value)
    return value and finite(value.Pitch, 360) and finite(value.Yaw, 360) and finite(value.Roll, 360)
end
local function dist2(left, right)
    return (left.X - right.X)^2 + (left.Y - right.Y)^2 + (left.Z - right.Z)^2
end
local function copy_rot(value) return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll} end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") end

local function actor_class(actor)
    return actor:GetClass():GetFullName():match("^%S+ (/Game/[%w_./%-]+)$")
end

local function mesh(actor)
    local base = StaticFindObject(prop_path)
    if not valid(base) or not valid(actor) or not actor:IsA(base) or actor_class(actor) == core_path
        or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return end
    local root = actor.PropMesh
    if same(root, actor.RootComponent) and same(root:GetOwner(), actor) then return root end
end

local function handle(pawn)
    local found = valid(pawn) and pawn.PhysicsHandle
    if valid(found) and same(found:GetOwner(), pawn) then return found end
end

local function held(pawn, actor, grip)
    local root = mesh(actor)
    return valid(pawn) and root and valid(grip) and same(grip:GetGrabbedComponent(), root)
        and same(pawn.HeldObject, actor) and actor.BeingHeld == true and same(actor["Holding Actor"], pawn)
end

local function camera_aim(pawn)
    local camera = pawn.Camera
    if not valid(camera) or not same(camera:GetOwner(), pawn) then return end
    local vector = camera:GetForwardVector()
    if not point(vector) then return end
    local horizontal = math.sqrt(vector.X^2 + vector.Y^2)
    local pitch, yaw = math.deg(math.atan(vector.Z, horizontal)), math.deg(math.atan(vector.Y, vector.X))
    if finite(pitch, 89.9) and finite(yaw, 180) then return pitch, yaw end
end

local function stock_target(pawn)
    local camera, arrow = pawn.Camera, pawn.Prop_Arrow
    if not valid(camera) or not valid(arrow) or not same(camera:GetOwner(), pawn)
        or not same(arrow:GetOwner(), pawn) then return end
    local eye, forward = camera:K2_GetComponentLocation(), camera:GetForwardVector()
    local rotation = arrow:K2_GetComponentRotation()
    if not point(eye) or not point(forward) or not pose(rotation) then return end
    -- HoldObject uses the pawn's horizontal heading when looking down.
    local horizontal = forward.Z > 0 and forward or pawn:GetActorForwardVector()
    if not point(horizontal) then return end
    return {X = eye.X + 110 * horizontal.X, Y = eye.Y + 110 * horizontal.Y, Z = eye.Z + 110 * forward.Z},
        {Pitch = 0, Yaw = rotation.Yaw, Roll = 0}
end

local function emit(pawn, kind, class, position, pitch, yaw)
    if not point(position) or not class or not pitch or not yaw then return end
    sequence = (sequence or 0) + 1
    edges[#edges + 1] = {sequence, pawn:GetFullName(), kind, class, position.X, position.Y, position.Z, pitch, yaw}
    if #edges > 6 then table.remove(edges, 1) end
    return sequence
end

local function observe(pawn, throwing)
    local local_pawn = state and state.local_pawn and state.local_pawn.pawn
    if phase ~= "connected" or not state.listening or state.server ~= true or not same(pawn, local_pawn)
        or not pawn:IsLocallyControlled() or pawn:HasAuthority() or pawn.Dead ~= false then return end
    local actor, grip = pawn.HeldObject, handle(pawn)
    if local_hold and not held(pawn, local_hold.actor, grip) then
        local pitch, yaw = camera_aim(pawn)
        emit(pawn, throwing and "throw" or "drop", local_hold.class,
            pawn:K2_GetActorLocation(), pitch, yaw)
        local_hold = nil
    end
    if local_hold or not held(pawn, actor, grip) or actor.bReplicateMovement ~= true or actor:HasAuthority() then return end
    -- The stock call has bound the grip and the physics step has yet to move its root.
    local class, pitch, yaw = actor_class(actor), camera_aim(pawn)
    local birth = emit(pawn, "grab", class, actor:K2_GetActorLocation(), pitch, yaw)
    if birth then local_hold = {actor = actor, pawn = pawn, class = class, serial = birth} end
end

function carry.enroll(actor)
    if phase ~= "hosting" or not mesh(actor) or not actor:HasAuthority() or actor.bReplicateMovement ~= true then return end
    enrolled[actor:GetAddress()] = actor
end

local function trace(pawn, pitch, yaw)
    local trace_api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local camera = pawn.Camera
    local origin = valid(camera) and camera:K2_GetComponentLocation()
    local body, range = pawn:K2_GetActorLocation(), pawn.InteractionRange
    if not valid(trace_api) or not point(origin) or not point(body) or dist2(origin, body) > 150^2
        or not finite(range, 1000) or range <= 0 then return end
    local p, y = math.rad(pitch), math.rad(yaw)
    local target = {X = origin.X + math.cos(p) * math.cos(y) * range,
        Y = origin.Y + math.cos(p) * math.sin(y) * range, Z = origin.Z + math.sin(p) * range}
    local hit = {}
    if trace_api:LineTraceSingle(pawn, origin, target, 1, true, {pawn}, 0, hit, true, color, color, 0) ~= true then return end
    hit = hit.OutHit or hit
    local part = hit.Component and hit.Component:Get()
    local actor = valid(part) and part:GetOwner()
    if not valid(actor) or not same(actor, enrolled[actor:GetAddress()]) or not same(part, mesh(actor))
        or not point(hit.ImpactPoint) then return end
    return actor, math.sqrt(dist2(origin, hit.ImpactPoint))
end

local function stock(pawn, kind, actor, range, pitch, yaw)
    local camera = pawn.Camera
    if not valid(camera) or not pose(camera.RelativeRotation) then return false end
    local previous_angle = copy_rot(camera.RelativeRotation)
    local previous_actor, previous_distance = pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance
    local invoked = pcall(function()
        camera:K2_SetWorldRotation({Pitch = pitch, Yaw = yaw, Roll = 0}, false, {}, true)
        if kind == "grab" then pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, range end
        pawn:Grab_Release_Object(kind == "throw", kind == "drop", false)
    end)
    local restored = pcall(function()
        camera:K2_SetRelativeRotation(previous_angle, false, {}, true)
        pawn.ActorBeingLookedAt = valid(previous_actor) and previous_actor or nil
        pawn.ActorLookedAt_Distance = previous_distance
    end)
    return invoked and restored
end

local function remove(entry, kind, pitch, yaw)
    local pawn, actor, grip = entry.pawn, entry.actor, entry.grip
    if held(pawn, actor, grip) then stock(pawn, kind, actor, 0, pitch, yaw) end
    if valid(grip) and same(grip:GetGrabbedComponent(), mesh(actor)) then return false end
    if valid(pawn) and same(pawn.HeldObject, actor) then return false end
    if valid(actor) and same(actor["Holding Actor"], pawn) then return false end
    holdings[entry.lease] = nil
    if valid(actor) then actor:ForceNetUpdate() end
    return true
end

local function aim_point(pawn, center)
    local eye = pawn.Camera:K2_GetComponentLocation()
    local dx, dy, dz = center.X - eye.X, center.Y - eye.Y, center.Z - eye.Z
    return {Pitch = math.deg(math.atan(dz, math.sqrt(dx^2 + dy^2))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
end

local function fixture(fields, current, seat)
    local mode = fields[3]
    local pawn = current.local_pawn and current.local_pawn.pawn
    if not (fixtures or headless and seat == "connected") or not same(world, current.world) or not valid(pawn) then
        error("native_prop_carry_fixture_unavailable", 0)
    end
    if seat == "hosting" and mode == "spawn" and not valid(fixture_actor) then
        local crate = StaticFindObject(fields[4])
        local spawner = valid(crate) and crate.Loot_Spawner
        if not valid(spawner) or not same(spawner.Container, crate) or not same(spawner:GetOwner(), crate)
            or not same(crate:GetWorld(), world) then error("native_prop_carry_fixture_crate", 0) end
        local path = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_CableSpool_01.Prop_CableSpool_01_C"
        for _ = 1, 18 do
            local output = {}
            spawner:SpawnLoot(crate["Loot Table_Items"], 1, 1, false, false, false, false, true, false, output)
            local candidate = output.Object
            if mesh(candidate) and actor_class(candidate) == path and same(candidate, enrolled[candidate:GetAddress()]) then
                fixture_actor = candidate
                return "stock loot spawner created a replicated junk prop"
            end
            if valid(candidate) and same(candidate:GetWorld(), world) and candidate:HasAuthority() then
                enrolled[candidate:GetAddress()] = nil
                candidate:K2_DestroyActor()
            end
        end
        error("native_prop_carry_fixture_junk_not_enrolled", 0)
    end
    if seat == "hosting" and mode == "approach" and valid(fixture_actor) then
        local ok, guest = pcall(guest_save.exact_item_context, current, seat, fields[4])
        if not ok or not valid(guest) then error("native_prop_carry_fixture_approach", 0) end
        local origin = guest:K2_GetActorLocation()
        local eye = guest.Camera:K2_GetComponentLocation()
        local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        if point(origin) and point(eye) and valid(api) then
            local forward = guest:GetActorForwardVector()
            local facing = math.deg(math.atan(forward.Y, forward.X))
            for _, heading in ipairs({0, 90, 180, 270}) do
                local bearing = math.rad(facing + heading)
                local target = {X = origin.X + 110 * math.cos(bearing), Y = origin.Y + 110 * math.sin(bearing), Z = origin.Z - 25}
                if api:LineTraceSingle(guest, eye, target, 1, true, {guest, fixture_actor}, 0, {}, true, color, color, 0) == false
                    and fixture_actor:K2_SetActorLocation(target, false, {}, true) == true then
                    fixture_actor:ForceNetUpdate()
                    return "stock crate prop placed in clear sight of the guest"
                end
            end
        end
        error("native_prop_carry_fixture_approach", 0)
    end
    if seat == "connected" and mode == "grab" then
        local class = StaticFindObject(fields[4])
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if not valid(class) or not valid(api) then error("native_prop_carry_fixture_class", 0) end
        local output = {}
        api:GetAllActorsOfClass(world, class, output)
        local origin, picked, best = pawn:K2_GetActorLocation(), nil, 250^2
        for _, candidate in ipairs(output.OutActors or output) do
            if candidate and type(candidate.type) == "function" and candidate:type() == "RemoteUnrealParam" then candidate = candidate:get() end
            if mesh(candidate) and candidate.bReplicateMovement == true then
                local distance = dist2(origin, candidate:K2_GetActorLocation())
                if distance < best then best, picked = distance, candidate end
            end
        end
        if not picked then error("native_prop_carry_fixture_prop_unavailable", 0) end
        local angle = aim_point(pawn, picked:K2_GetActorLocation())
        pawn.Controller:SetControlRotation(angle)
        pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
        pawn:LookTrace()
        if not same(pawn.ActorBeingLookedAt, picked) then
            local target = pawn.ActorBeingLookedAt
            error("native_prop_carry_fixture_focus:" .. (valid(target) and target:GetFullName() or "none"), 0)
        end
        local invoked, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if not held(pawn, picked, handle(pawn)) then error("native_prop_carry_fixture_input:" .. tostring(reason), 0) end
        return "normal stock Interact grabbed the crate prop"
    end
    if seat == "connected" and local_hold and (mode == "turn" or mode == "throw" or mode == "drop") then
        if mode == "turn" then
            local angle = copy_rot(pawn.Controller:GetControlRotation())
            angle.Yaw = (angle.Yaw + 70 + 180) % 360 - 180
            pawn.Controller:SetControlRotation(angle)
            pawn:K2_SetActorRotation({Pitch = 0, Yaw = angle.Yaw, Roll = 0}, true)
            pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
        else
            pawn:Grab_Release_Object(mode == "throw", mode == "drop", false)
        end
        return "stock prop carry fixture dispatched"
    end
    error("native_prop_carry_fixture_unavailable", 0)
end

function carry.command(fields, current, seat)
    if fields[2] == "native-prop-carry-fixture" then return fixture(fields, current, seat) end
    if fields[2] ~= "native-prop-carry" then return end
    local lease, name, kind, serial = fields[3], fields[4], fields[5], math.tointeger(fields[6])
    local pitch, yaw = tonumber(fields[7]), tonumber(fields[8])
    if not enabled or seat ~= "hosting" or current.server ~= false or not same(current.world, world)
        or not guid(lease) or not serial or serial < 1 or not finite(pitch, 89.9) or not finite(yaw, 180) then
        error("native_prop_carry_scope_unavailable", 0)
    end
    local qualified, pawn = pcall(guest_save.exact_item_context, current, seat, name)
    if not qualified or not valid(pawn) or not players.is_standing_guest(pawn, name) then
        error("native_prop_carry_guest_unavailable", 0)
    end
    local entry = holdings[lease]
    if kind == "grab" and #fields == 12 then
        local position = {X = tonumber(fields[10]), Y = tonumber(fields[11]), Z = tonumber(fields[12])}
        local actor, distance = trace(pawn, pitch, yaw)
        local grip = handle(pawn)
        if entry or not actor or not point(position) or actor_class(actor) ~= fields[9]
            or dist2(position, actor:K2_GetActorLocation()) > 35^2 or actor.BeingHeld ~= false
            or not valid(grip) or valid(pawn.HeldObject) or valid(grip:GetGrabbedComponent()) then
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        if not stock(pawn, kind, actor, distance, pitch, yaw) or not held(pawn, actor, grip) then
            if same(pawn.HeldObject, actor) then stock(pawn, "drop", actor, 0, pitch, yaw) end
            error("native_prop_carry_stock_grab_unverified", 0)
        end
        entry = {lease = lease, pawn = pawn, actor = actor, grip = grip, serial = serial, deadline = os.clock() + 1.5}
        holdings[lease] = entry
    elseif (kind == "drop" or kind == "throw") and #fields == 8 and entry and same(pawn, entry.pawn)
        and serial > entry.serial then
        if not remove(entry, kind, pitch, yaw) then error("native_prop_carry_stock_release_unverified", 0) end
    else
        error("native_prop_carry_wrong_holder", 0)
    end
    latest = {lease, serial, kind, name, entry.actor:GetFullName()}
    return "stock physics prop action verified"
end

local function follow()
    if not next(holdings) then return end
    local now = os.clock()
    local input = mailbox and now >= (next_read or 0) and io.open(mailbox, "rb")
    if input then
        next_read = now + .05
        local row_limit = 0
        for line in input:lines() do
            row_limit = row_limit + 1
            if row_limit > 4 or #line > 256 then break end
            local lease, serial, x, y, z, pitch, yaw, roll = line:match("^([a-f0-9]+)\t(%d+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)$")
            local entry, number = holdings[lease], math.tointeger(serial)
            local target = {X = tonumber(x), Y = tonumber(y), Z = tonumber(z)}
            local rotation = {Pitch = tonumber(pitch), Yaw = tonumber(yaw), Roll = tonumber(roll)}
            local origin = entry and valid(entry.pawn) and entry.pawn:K2_GetActorLocation()
            if entry and number and number > (entry.sample or 0) and held(entry.pawn, entry.actor, entry.grip)
                and point(origin) and point(target) and pose(rotation) and dist2(origin, target) <= 375^2 then
                entry.sample, entry.deadline, entry.target, entry.rotation = number, now + 1.5, target, rotation
            end
        end
        input:close()
    end
    for _, entry in pairs(holdings) do
        if now > entry.deadline or not valid(entry.pawn) or entry.pawn.Dead ~= false
            or not held(entry.pawn, entry.actor, entry.grip) then
            remove(entry, "drop", 0, 0)
        elseif entry.target then
            entry.grip:SetTargetLocationAndRotation(entry.target, entry.rotation)
        end
    end
end

function carry.rows(current, seat, row)
    local rows = {}
    if not enabled or not same(world, current.world) then return rows end
    if seat == "hosting" and fixtures and mesh(fixture_actor) then
        local position = fixture_actor:K2_GetActorLocation()
        local velocity = fixture_actor.PropMesh:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
        local grip = {}
        for _, entry in pairs(holdings) do
            if same(entry.actor, fixture_actor) and entry.target then
                grip = {entry.sample, entry.target.X, entry.target.Y, entry.target.Z}
                break
            end
        end
        rows[#rows + 1] = row("native-prop-carry-fixture", current.map, actor_class(fixture_actor),
            fixture_actor:GetFullName(), position.X, position.Y, position.Z, velocity.X, velocity.Y, velocity.Z,
            fixture_actor.BeingHeld == true and 1 or 0, table.unpack(grip))
    end
    if seat == "hosting" and latest then rows[#rows + 1] = row("native-prop-carry", current.map, table.unpack(latest)) end
    if seat ~= "connected" then return rows end
    for _, edge in ipairs(edges) do rows[#rows + 1] = row("native-prop-carry-input", current.map, table.unpack(edge)) end
    local grip = local_hold and handle(local_hold.pawn)
    if not local_hold or not held(local_hold.pawn, local_hold.actor, grip) then return rows end
    local position, rotation = stock_target(local_hold.pawn)
    if point(position) and pose(rotation) then
        rows[#rows + 1] = row("native-prop-carry-target", current.map, local_hold.serial, position.X, position.Y,
            position.Z, rotation.Pitch, rotation.Yaw, rotation.Roll)
    end
    return rows
end

function carry.sync(current, seat, bridge)
    if not enabled then return end
    if not same(world, current.world) then
        world, enrolled, holdings, edges, local_hold, latest = current.world, {}, {}, {}, nil, nil
        fixture_actor, next_read = nil, nil
    end
    state, phase, mailbox = current, seat, bridge and bridge .. "/native-prop-carry-poses"
    if not hook and type(RegisterHook) == "function" and valid(StaticFindObject(grab_path)) then
        RegisterHook(grab_path, function(context, throwing) pcall(observe, context:get(), throwing:get() == true) end)
        hook = true
    end
    if seat == "hosting" then follow() end
end

return carry
