local carry = {}
local corpses = require("native_corpse_view")
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

local function held(pawn, actor, grip, corpse)
    local part = corpse and corpse.part or mesh(actor)
    return valid(pawn) and valid(actor) and not actor:IsActorBeingDestroyed()
        and valid(part) and valid(grip) and same(grip:GetGrabbedComponent(), part) and same(pawn.HeldObject, actor)
        and (corpse and actor.Alive == false or not corpse and actor.BeingHeld == true and same(actor["Holding Actor"], pawn))
end

local function bone_name(value)
    if not value then return end
    local name = value:ToString()
    if #name <= 64 and name ~= "None" and name:match("^[%w_ .%-]+$") then return name end
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

local function emit(pawn, kind, class, position, pitch, yaw, corpse)
    if not point(position) or not class or not pitch or not yaw then return end
    sequence = (sequence or 0) + 1
    edges[#edges + 1] = {sequence, pawn:GetFullName(), kind, class, position.X, position.Y, position.Z, pitch, yaw}
    if corpse then
        local edge = edges[#edges]
        edge[#edge + 1], edge[#edge + 2] = corpse.number, corpse.name
    end
    if #edges > 6 then table.remove(edges, 1) end
    return sequence
end

local function observe(pawn, throwing, dropping)
    local local_pawn = state and state.local_pawn and state.local_pawn.pawn
    if phase == "hosting" and not throwing and not dropping and same(pawn, local_pawn) then
        -- Stock props mark themselves as held; the dead NPC Blueprint does not.
        -- Discard a competing local grip before the next physics step.
        for _, entry in pairs(holdings) do
            if entry.corpse and same(pawn.HeldObject, entry.actor) then
                pawn:Grab_Release_Object(false, true, false)
                return
            end
        end
    end
    if phase ~= "connected" or not state.listening or state.server ~= true or not same(pawn, local_pawn)
        or not pawn:IsLocallyControlled() or pawn:HasAuthority() or pawn.Dead ~= false then return end
    local actor, grip = pawn.HeldObject, handle(pawn)
    if local_hold and not held(pawn, local_hold.actor, grip, local_hold.corpse) then
        local pitch, yaw = camera_aim(pawn)
        emit(pawn, throwing and "throw" or "drop", local_hold.class,
            pawn:K2_GetActorLocation(), pitch, yaw)
        local_hold = nil
    end
    if local_hold or not valid(actor) or actor:HasAuthority() then return end
    local number, part = corpses.binding(actor, true)
    local corpse
    if number then
        local bone = pawn["Hit Bone Name"]
        local name = bone_name(bone)
        if not name or part:GetBoneIndex(bone) < 0 or not part:IsSimulatingPhysics(bone) then return end
        corpse = {number = number, part = part, name = name, bone = bone}
    end
    if not held(pawn, actor, grip, corpse) or not corpse and actor.bReplicateMovement ~= true then return end
    -- The stock call has bound the grip and the physics step has yet to move its root.
    local class, pitch, yaw = actor_class(actor), camera_aim(pawn)
    local position = corpse and part:GetSocketLocation(corpse.bone) or actor:K2_GetActorLocation()
    local birth = emit(pawn, "grab", class, position, pitch, yaw, corpse)
    if birth then local_hold = {actor = actor, pawn = pawn, class = class, serial = birth, corpse = corpse} end
end

function carry.enroll(actor)
    if phase ~= "hosting" or not mesh(actor) or not actor:HasAuthority() or actor.bReplicateMovement ~= true then return end
    enrolled[actor:GetAddress()] = actor
    return true
end

local function trace(pawn, pitch, yaw, number, expected_bone, claimed)
    local trace_api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local camera = pawn.Camera
    local origin = valid(camera) and camera:K2_GetComponentLocation()
    local body, range = pawn:K2_GetActorLocation(), pawn.InteractionRange
    if not valid(trace_api) or not point(origin) or not point(body) or dist2(origin, body) > 150^2
        or not finite(range, 1000) or range <= 0 then return nil, "origin" end
    local p, y = math.rad(pitch), math.rad(yaw)
    local ray = {X = math.cos(p) * math.cos(y), Y = math.cos(p) * math.sin(y), Z = math.sin(p)}
    local function along(distance)
        return {X = origin.X + ray.X * distance, Y = origin.Y + ray.Y * distance, Z = origin.Z + ray.Z * distance}
    end
    local function impact(target)
        local hit = {}
        if trace_api:LineTraceSingle(pawn, origin, target, 1, true, {pawn}, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            return hit.Component and hit.Component:Get(), hit.ImpactPoint
        end
    end
    if not number then
        local part, collision = impact(along(range))
        local actor = valid(part) and part:GetOwner()
        if valid(actor) and same(actor, enrolled[actor:GetAddress()]) and same(part, mesh(actor)) and point(collision) then
            return actor, math.sqrt(dist2(origin, collision))
        end
        return
    end
    local actor, part = corpses.authoritative(number)
    if not actor or not point(claimed) or not expected_bone
        or #expected_bone > 64 or not expected_bone:match("^[%w_ .%-]+$") then
        return nil, "marker or body name"
    end
    local bone = FName(expected_bone, EFindName.FNAME_Find)
    local resolved, index = bone_name(bone), part:GetBoneIndex(bone)
    if not resolved or resolved:lower() ~= expected_bone:lower() or index < 0 or not part:IsSimulatingPhysics(bone) then
        return nil, ("bone %s (resolved %s, index %d, simulation %s)"):format(expected_bone, tostring(resolved),
            index, tostring(part:IsSimulatingPhysics(bone)))
    end
    local location = part:GetSocketLocation(bone)
    -- The replicas can settle in different poses. Check that the guest aimed
    -- near their selected body, then check the same host body for reach and occlusion.
    local projection = (claimed.X - origin.X) * ray.X + (claimed.Y - origin.Y) * ray.Y + (claimed.Z - origin.Z) * ray.Z
    if not point(location) then return nil, "invalid body location" end
    if projection <= 0 or projection > range or dist2(along(projection), claimed) > 55^2
        or dist2(origin, location) >= range^2 then
        return nil, ("skeletal aim %.1f / %.1f, ray offset %.1f, body separation %.1f, distance %.1f"):format(
            projection, range, math.sqrt(dist2(along(projection), claimed)), math.sqrt(dist2(claimed, location)),
            math.sqrt(dist2(origin, location)))
    end
    local surface, collision = impact(location)
    if valid(surface) and not same(surface, part) and (not point(collision) or dist2(location, collision) > 9^2) then
        return nil, "skeletal body occluded"
    end
    return actor, math.sqrt(dist2(origin, location)), {number = number, part = part, bone = bone, name = expected_bone,
        location = location}
end

local function stock(pawn, kind, actor, range, pitch, yaw, corpse)
    local camera = pawn.Camera
    if not valid(camera) or not pose(camera.RelativeRotation) then return false end
    local previous_angle = copy_rot(camera.RelativeRotation)
    local previous_actor, previous_distance = pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance
    local previous_bone, previous_point
    if corpse then
        previous_bone, previous_point = pawn["Hit Bone Name"], pawn["WhereI'mLooking"]
        previous_point = {X = previous_point.X, Y = previous_point.Y, Z = previous_point.Z}
    end
    local invoked = pcall(function()
        camera:K2_SetWorldRotation({Pitch = pitch, Yaw = yaw, Roll = 0}, false, {}, true)
        if kind == "grab" then pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, range end
        if corpse then pawn["Hit Bone Name"], pawn["WhereI'mLooking"] = corpse.bone, corpse.location end
        pawn:Grab_Release_Object(kind == "throw", kind == "drop", false)
    end)
    local restored = pcall(function()
        camera:K2_SetRelativeRotation(previous_angle, false, {}, true)
        pawn.ActorBeingLookedAt = valid(previous_actor) and previous_actor or nil
        pawn.ActorLookedAt_Distance = previous_distance
        if corpse then pawn["Hit Bone Name"], pawn["WhereI'mLooking"] = previous_bone, previous_point end
    end)
    return invoked and restored
end

local function remove(entry, kind, pitch, yaw)
    local pawn, actor, grip = entry.pawn, entry.actor, entry.grip
    if held(pawn, actor, grip, entry.corpse) then stock(pawn, kind, actor, 0, pitch, yaw) end
    if entry.corpse and not valid(pawn) and valid(grip) and same(grip:GetGrabbedComponent(), entry.corpse.part) then
        grip:ReleaseComponent()
    end
    if not entry.corpse and not valid(pawn) and mesh(actor) and actor:HasAuthority() and actor.BeingHeld == true
        and not valid(actor["Holding Actor"]) then
        actor:PickUp_Object(false, nil)
        if actor.BeingHeld ~= false then return false end
    end
    if valid(grip) and same(grip:GetGrabbedComponent(), entry.corpse and entry.corpse.part or mesh(actor)) then return false end
    if valid(pawn) and same(pawn.HeldObject, actor) then return false end
    if not entry.corpse and valid(actor) and same(actor["Holding Actor"], pawn) then return false end
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
    if seat == "hosting" and mode == "select" and #fields == 4 and not valid(fixture_actor) then
        local actor = StaticFindObject(fields[4])
        if not mesh(actor) or not same(actor, enrolled[actor:GetAddress()]) then
            error("native_prop_carry_fixture_authored_prop_unavailable", 0)
        end
        fixture_actor = actor
        return "selected the enrolled authored physics prop"
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
    if seat == "connected" and (mode == "grab" or mode == "pickup" or mode == "lamp-use") then
        local class = StaticFindObject(fields[4])
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if not valid(class) or not valid(api) then error("native_prop_carry_fixture_class", 0) end
        local output = {}
        api:GetAllActorsOfClass(world, class, output)
        local candidates = output.OutActors or output
        local expected = mode == "pickup" and math.tointeger(fields[5])
        if expected and #candidates ~= expected then
            error("native_prop_carry_fixture_actor_count:" .. #candidates .. ":" .. expected, 0)
        end
        local origin, picked, best = pawn:K2_GetActorLocation(), nil, 250^2
        for _, candidate in ipairs(candidates) do
            if candidate and type(candidate.type) == "function" and candidate:type() == "RemoteUnrealParam" then candidate = candidate:get() end
            if mesh(candidate) and candidate.bReplicateMovement == true
                and (not expected or candidate:GetFullName() == fields[6]) then
                local distance = dist2(origin, candidate:K2_GetActorLocation())
                if distance < best then best, picked = distance, candidate end
            end
        end
        if not picked then error("native_prop_carry_fixture_prop_unavailable", 0) end
        local function aim(center)
            local angle = aim_point(pawn, center)
            pawn.Controller:SetControlRotation(angle)
            pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
            pawn:LookTrace()
        end
        if mode == "lamp-use" then
            aim(picked.PropMesh:K2_GetComponentLocation())
            local component = pawn.InteractionTarget_Component
            if not valid(component) or not same(component:GetOwner(), picked)
                or type(SilverfishTakeNativeLanternPress) ~= "function" then
                local focused = pawn.ActorBeingLookedAt
                error("native_prop_carry_fixture_lamp_focus:" .. (valid(component) and component:GetFullName() or "none")
                    .. ":" .. (valid(focused) and focused:GetFullName() or "none")
                    .. ":interact=" .. tostring(picked.PropMesh:GetCollisionResponseToChannel(16)), 0)
            end
            local _, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            if reason ~= "stock_started_consumed" or picked.Lit ~= true then
                error("native_prop_carry_fixture_lamp_input:" .. tostring(reason), 0)
            end
            return "normal stock Interact submitted the lantern action"
        end
        aim(picked:K2_GetActorLocation())
        if not same(pawn.ActorBeingLookedAt, picked) then
            local target = pawn.ActorBeingLookedAt
            error("native_prop_carry_fixture_focus:" .. (valid(target) and target:GetFullName() or "none"), 0)
        end
        local reason = "stock_pickup_function"
        if mode == "pickup" then
            pawn:Grab_Release_Object(false, false, true)
        else
            local _, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            reason = status
        end
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
    if fields[2] == "native-prop-carry-refuse" and enabled and seat == "connected" and current.server
        and same(world, current.world) and #fields == 3 then
        local pawn = current.local_pawn and current.local_pawn.pawn
        if local_hold and local_hold.corpse and math.tointeger(fields[3]) == local_hold.serial
            and same(pawn, local_hold.pawn) and pawn:IsLocallyControlled() and not pawn:HasAuthority() then
            local refused = local_hold
            local_hold = nil
            if held(pawn, refused.actor, handle(pawn), refused.corpse) then pawn:Grab_Release_Object(false, true, false) end
        end
        return "stale corpse grip cleared"
    end
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
    if kind == "grab" and (#fields == 12 or #fields == 14) then
        local position = {X = tonumber(fields[10]), Y = tonumber(fields[11]), Z = tonumber(fields[12])}
        local number = math.tointeger(fields[13])
        if #fields == 14 and (not number or number < 1 or number > 65535) then
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        local actor, distance, corpse = trace(pawn, pitch, yaw, number, fields[14], position)
        local grip = handle(pawn)
        if entry or not actor or not point(position) or actor_class(actor) ~= fields[9]
            or not valid(grip) or valid(pawn.HeldObject) or valid(grip:GetGrabbedComponent()) then
            if fixtures and number then
                error("native_prop_carry_stock_target_unavailable:" .. (not actor and tostring(distance) or "grip or class"), 0)
            end
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        if corpse then
            local host = current.local_pawn and current.local_pawn.pawn
            if valid(host) and same(host.HeldObject, actor) then
                if fixtures then error("native_prop_carry_stock_target_unavailable:host grip", 0) end
                error("native_prop_carry_stock_target_unavailable", 0)
            end
            for _, holder in pairs(holdings) do
                if same(holder.actor, actor) then error("native_prop_carry_wrong_holder", 0) end
            end
        elseif dist2(position, actor:K2_GetActorLocation()) > 35^2 or actor.BeingHeld ~= false then
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        if not stock(pawn, kind, actor, distance, pitch, yaw, corpse) or not held(pawn, actor, grip, corpse) then
            if same(pawn.HeldObject, actor) then stock(pawn, "drop", actor, 0, pitch, yaw) end
            error("native_prop_carry_stock_grab_unverified", 0)
        end
        entry = {lease = lease, pawn = pawn, actor = actor, grip = grip, serial = serial, deadline = os.clock() + 1.5,
            corpse = corpse}
        holdings[lease] = entry
    elseif (kind == "drop" or kind == "throw") and #fields == 8 and entry and same(pawn, entry.pawn)
        and serial > entry.serial then
        if not remove(entry, kind, pitch, yaw) then error("native_prop_carry_stock_release_unverified", 0) end
    else
        error("native_prop_carry_wrong_holder", 0)
    end
    latest = {lease, serial, kind, name, entry.actor:GetFullName()}
    if entry.corpse then latest[6], latest[7] = entry.corpse.number, entry.corpse.name end
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
            if entry and number and number > (entry.sample or 0) and held(entry.pawn, entry.actor, entry.grip, entry.corpse)
                and point(origin) and point(target) and pose(rotation) and dist2(origin, target) <= 375^2 then
                entry.sample, entry.deadline, entry.target, entry.rotation = number, now + 1.5, target, rotation
            end
        end
        input:close()
    end
    for _, entry in pairs(holdings) do
        if now > entry.deadline or not valid(entry.pawn) or entry.pawn.Dead ~= false
            or not held(entry.pawn, entry.actor, entry.grip, entry.corpse) then
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
    if not local_hold or not held(local_hold.pawn, local_hold.actor, grip, local_hold.corpse) then return rows end
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
        RegisterHook(grab_path, function(context, throwing, dropping)
            pcall(observe, context:get(), throwing:get() == true, dropping:get() == true)
        end)
        hook = true
    end
    if seat == "hosting" then follow() end
end

return carry
