local carry = {}
local corpses = require("native_corpse_view")
local guest_save = require("guest_save")
local observers = require("native_observer_target")
local players = require("players")
local loot = require("native_guest_drop_projection")
local flares = require("native_flare")

local prop_path = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C"
local item_path = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local core_path = "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"
local grab_path = "/Game/Classes/Player/Player_FP.Player_FP_C:Grab_Release_Object"
local release_component_path = "/Script/Engine.PhysicsHandleComponent:ReleaseComponent"
local final_capture_age = .10
local max_handoff_offset = 175
local max_release_reach = 375
local max_linear_velocity = 6000
local max_angular_velocity = 7200
local pawn_channel, ignore_collision, block_collision = 2, 0, 2
local color = {R = 0, G = 0, B = 0, A = 0}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local world, state, phase, hook, release_hook, mailbox, sequence, next_read, next_local_pose
local enrolled, holdings, edges, local_hold, latest = {}, {}, {}, nil, nil
local host_release
local fixture_actor, loose_fixtures = nil, {}
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local grip_filter = os.getenv("SILVERFISH_COOP_PROP_GRIP_FILTER") ~= "0"
local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local offering_fixture = headless and observers.enabled and os.getenv("SILVERFISH_COOP_OBSERVER_THROW_FIXTURE") == "1"

local function valid(value) return value and value ~= false and value:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function finite(value, limit) return type(value) == "number" and value == value and math.abs(value) <= limit end
local function vector(value, limit)
    return value and finite(value.X, limit) and finite(value.Y, limit) and finite(value.Z, limit)
end
local function point(value) return vector(value, 1e7) end
local function pose(value)
    return value and finite(value.Pitch, 360) and finite(value.Yaw, 360) and finite(value.Roll, 360)
end
local function dist2(left, right)
    return (left.X - right.X)^2 + (left.Y - right.Y)^2 + (left.Z - right.Z)^2
end
local function copy_rot(value) return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll} end
local function angle_delta(angle, goal) return (goal - angle + 180) % 360 - 180 end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") end

local function actor_class(actor)
    return actor:GetClass():GetFullName():match("^%S+ (/Game/[%w_./%-]+)$")
end

local function item_box(actor)
    if not valid(actor) or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return end
    local base = StaticFindObject(item_path)
    if not valid(base) or not actor:IsA(base) then return end
    local box = actor.Box
    if same(box, actor.RootComponent) and same(box:GetOwner(), actor) then return box end
end

local function placed_item(actor)
    return actor.Physics == true and actor.bNetLoadOnClient == true and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
        and same(actor:GetOuter(), world.PersistentLevel)
end

local function item_body(actor)
    local box = item_box(actor)
    if box and actor.bReplicateMovement == true and (placed_item(actor) or offering_fixture) then return box end
    if box and actor.Physics == true then return loot.carry_body(actor) end
end

local function unflagged_body(actor)
    return item_body(actor) or flares.carry_root(actor, world)
end

local function prop_mesh(actor)
    local base = StaticFindObject(prop_path)
    if not valid(base) or not valid(actor) or not actor:IsA(base) or actor_class(actor) == core_path
        or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return end
    local root = actor.PropMesh
    if same(root, actor.RootComponent) and same(root:GetOwner(), actor) then return root end
end

local function mesh(actor)
    return prop_mesh(actor) or unflagged_body(actor)
end

local function handle(pawn)
    local found = valid(pawn) and pawn.PhysicsHandle
    if valid(found) and same(found:GetOwner(), pawn) then return found end
end

local function held(pawn, actor, grip, corpse)
    local unflagged = not corpse and unflagged_body(actor)
    local part = corpse and corpse.part or unflagged or mesh(actor)
    return valid(pawn) and valid(actor) and not actor:IsActorBeingDestroyed()
        and valid(part) and valid(grip) and same(grip:GetGrabbedComponent(), part) and same(pawn.HeldObject, actor)
        and (corpse and actor.Alive == false
            or not corpse and (unflagged ~= nil or actor.BeingHeld == true and same(actor["Holding Actor"], pawn)))
end

local function rigid_root(entry, grip, bone)
    local pawn, actor = entry.pawn, entry.actor
    if entry.corpse or entry.loot_id or not valid(actor) or actor.bReplicateMovement ~= true
        or not held(pawn, actor, grip) then return end
    local root = mesh(actor)
    if same(root, actor.RootComponent) and root:IsSimulatingPhysics(bone) then return root end
end

local function filter_root(entry, grip)
    local actor = entry.actor
    local root = rigid_root(entry, grip, FName("None", EFindName.FNAME_Find))
    if root and not item_body(actor) and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
        and same(actor:GetOuter(), world.PersistentLevel) then return root end
end

local function filter_available()
    return grip_filter and release_hook
        and type(SilverfishSetNativePropCarryMovement) == "function"
        and type(SilverfishReleaseNativePropCarryMovement) == "function"
        and type(SilverfishReadNativePropCarryMovement) == "function"
end

local function release_grip_filter()
    if local_hold and local_hold.filter_lease
        and type(SilverfishReleaseNativePropCarryMovement) == "function" then
        SilverfishReleaseNativePropCarryMovement()
        local_hold.filter_lease = nil
    end
end

local function solved_pose(root, bone)
    local location, rotation = root:K2_GetComponentLocation(), root:K2_GetComponentRotation()
    local velocity = root:GetPhysicsLinearVelocity(bone)
    local spin = root:GetPhysicsAngularVelocityInDegrees(bone)
    if point(location) and pose(rotation) and vector(velocity, max_linear_velocity)
        and vector(spin, max_angular_velocity) then
        return {location = location, rotation = rotation, velocity = velocity, spin = spin}
    end
end

local function capture_release(grip)
    if phase == "hosting" then
        local pawn = state.local_pawn and state.local_pawn.pawn
        if state.listening and same(grip, handle(pawn)) and pawn:IsLocallyControlled() and pawn:HasAuthority() and pawn.Dead == false then
            host_release = nil
            local actor = pawn.HeldObject
            if same(prop_mesh(actor), grip:GetGrabbedComponent()) and actor:HasAuthority()
                and actor.BeingHeld == true and same(actor["Holding Actor"], pawn) then
                host_release = {actor = actor, pawn = pawn, time = os.clock()}
            end
        end
        return
    end
    if phase ~= "connected" or not local_hold or not state.listening or state.server ~= true then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not same(pawn, local_hold.pawn) or not same(grip, handle(pawn)) or not pawn:IsLocallyControlled()
        or pawn:HasAuthority() or pawn.Dead ~= false or players.is_downed(pawn) then return end
    -- Drop the bounded receive filter in the same native pre-release call.
    release_grip_filter()
    local_hold.release = nil
    local bone = FName("None", EFindName.FNAME_Find)
    local root = rigid_root(local_hold, grip, bone)
    if not root or local_hold.actor:HasAuthority() then return end
    local final = solved_pose(root, bone)
    if final then local_hold.release = {time = os.clock(), pose = final} end
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

local function emit(pawn, kind, class, position, pitch, yaw, corpse, placement, loot_id, final)
    if not point(position) or not class or not pitch or not yaw then return end
    sequence = (sequence or 0) + 1
    edges[#edges + 1] = {sequence, pawn:GetFullName(), kind, class, position.X, position.Y, position.Z, pitch, yaw}
    if corpse then
        local edge = edges[#edges]
        edge[#edge + 1], edge[#edge + 2] = corpse.number, corpse.name
    elseif placement then
        local edge = edges[#edges]
        edge[#edge + 1] = placement
    elseif loot_id then
        local edge = edges[#edges]
        edge[#edge + 1] = "loot:" .. loot_id
    elseif final then
        local edge = edges[#edges]
        for _, value in ipairs({final.location.X, final.location.Y, final.location.Z, final.rotation.Pitch,
            final.rotation.Yaw, final.rotation.Roll, final.velocity.X, final.velocity.Y, final.velocity.Z,
            final.spin.X, final.spin.Y, final.spin.Z}) do edge[#edge + 1] = value end
    end
    if #edges > 6 then table.remove(edges, 1) end
    return sequence
end

local function attribute_prop_throw(pawn, actor)
    local root, bone = prop_mesh(actor), FName("None", EFindName.FNAME_Find)
    if not valid(root) or not actor:HasAuthority() or same(pawn.HeldObject, actor) or actor.BeingHeld ~= false
        or not root:IsSimulatingPhysics(bone) then return end
    -- Stock sleep can clear the prop's instigator while the physics handle is still holding it.
    -- Use the pawn that just released this exact grip so stock impact damage carries its source.
    if not same(actor.Instigator, pawn) then actor.Instigator = pawn end
    if actor.IgnorePawn_WhenAsleep == true and root:GetCollisionResponseToChannel(pawn_channel) == ignore_collision then
        root:SetCollisionResponseToChannel(pawn_channel, block_collision)
    end
end

local function observe(pawn, throwing, dropping)
    local local_pawn = state and state.local_pawn and state.local_pawn.pawn
    if phase == "hosting" and same(pawn, local_pawn) then
        local release = host_release
        host_release = nil
        if throwing and not dropping and release and same(release.pawn, pawn) and os.clock() - release.time <= final_capture_age then
            attribute_prop_throw(pawn, release.actor)
        end
    end
    if phase == "hosting" and not throwing and not dropping and same(pawn, local_pawn) then
        -- Dead NPCs, loose items and stock flare projectiles lack the prop ownership fields.
        -- Discard a competing local grip before the next physics step.
        for _, entry in pairs(holdings) do
            if (entry.corpse or unflagged_body(entry.actor)) and same(pawn.HeldObject, entry.actor) then
                pawn:Grab_Release_Object(false, true, false)
                return
            end
        end
        if valid(pawn.HeldObject) then loot.start_carry(pawn, pawn.HeldObject) end
    end
    if phase ~= "connected" or not state.listening or state.server ~= true or not same(pawn, local_pawn)
        or not pawn:IsLocallyControlled() or pawn:HasAuthority() or pawn.Dead ~= false then return end
    local actor, grip = pawn.HeldObject, handle(pawn)
    if local_hold and not held(pawn, local_hold.actor, grip, local_hold.corpse) then
        local pitch, yaw = camera_aim(pawn)
        local capture = local_hold.release
        local age = capture and os.clock() - capture.time
        local final = (throwing or dropping) and age and age >= 0 and age <= final_capture_age and capture.pose
        emit(pawn, throwing and "throw" or "drop", local_hold.class,
            pawn:K2_GetActorLocation(), pitch, yaw, nil, nil, nil, final)
        release_grip_filter()
        if local_hold.loot_id then loot.stop_carry(pawn, local_hold.actor) end
        local_hold = nil
    end
    if local_hold then
        local_hold.release = nil
        return
    end
    if not valid(actor) then return end
    local loot_id = actor:HasAuthority() and loot.start_carry(pawn, actor) or nil
    if not loot_id and actor:HasAuthority() then return end
    local number, part = corpses.binding(actor, true)
    local corpse
    if number then
        local bone = pawn["Hit Bone Name"]
        local name = bone_name(bone)
        if not name or part:GetBoneIndex(bone) < 0 or not part:IsSimulatingPhysics(bone) then return end
        corpse = {number = number, part = part, name = name, bone = bone}
    end
    if not held(pawn, actor, grip, corpse) or not loot_id and not corpse and actor.bReplicateMovement ~= true then return end
    -- The stock call has bound the grip and the physics step has yet to move its root.
    local class, pitch, yaw = actor_class(actor), camera_aim(pawn)
    local position = corpse and part:GetSocketLocation(corpse.bone) or actor:K2_GetActorLocation()
    local placement
    if not corpse and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) and same(actor:GetOuter(), world.PersistentLevel) then
        placement = actor:GetFullName():match("^%S+ (/Game/Classes/Maps/[%w_/]+%.[%w_]+:PersistentLevel%.[%w_%-]+)$")
    end
    local birth = emit(pawn, "grab", class, position, pitch, yaw, corpse, placement, loot_id)
    if birth then local_hold = {actor = actor, pawn = pawn, class = class, serial = birth, corpse = corpse, loot_id = loot_id} end
end

function carry.enroll(actor)
    if phase ~= "hosting" or not mesh(actor) or not actor:HasAuthority() or actor.bReplicateMovement ~= true then return end
    enrolled[actor:GetAddress()] = actor
    return true
end

function carry.enroll_loose(actor)
    local box = phase == "hosting" and item_box(actor)
    if not box or not placed_item(actor) or not actor:HasAuthority()
        or not box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) then return end
    if same(actor, enrolled[actor:GetAddress()]) then return true end
    actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 15)
    if not actor.bReplicates then actor:SetReplicates(true) end
    if not actor.bReplicateMovement then actor:SetReplicateMovement(true) end
    actor:ForceNetUpdate()
    if not carry.enroll(actor) then return end
    if fixtures then
        local mass, class = box:GetMass(), actor_class(actor)
        if class and mass > 0 and mass <= 10000 then
            loose_fixtures[actor:GetAddress()] = {actor = actor, name = actor:GetFullName(), class = class, mass = mass}
        end
    end
    return true
end

function carry.item_is_held(actor)
    if not item_body(actor) then return false end
    local pawn = state and state.local_pawn and state.local_pawn.pawn
    if valid(pawn) and same(pawn.HeldObject, actor) then return true end
    for _, entry in pairs(holdings) do
        if same(entry.actor, actor) then return true end
    end
    return false
end

local function trace(pawn, pitch, yaw, number, expected_bone, claimed, expected_class, placement)
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
    local function impact(target, channel)
        local hit = {}
        if trace_api:LineTraceSingle(pawn, origin, target, channel or 1, channel ~= 4, {pawn}, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            return hit.Component and hit.Component:Get(), hit.ImpactPoint
        end
    end
    if not number then
        local selected = placement and StaticFindObject(placement)
        if valid(selected) and same(selected, enrolled[selected:GetAddress()]) and selected:HasAuthority()
            and actor_class(selected) == expected_class and mesh(selected) and point(claimed) then
            local center = selected:K2_GetActorLocation()
            if point(center) and dist2(center, claimed) <= 35^2 and dist2(origin, center) < range^2 then
                -- The stock guest grip already selected this placed actor. Use its
                -- enrolled identity when the authority camera is on a different frame.
                return selected, math.sqrt(dist2(origin, center))
            end
        end
        local part, collision = impact(along(range))
        local actor = valid(part) and part:GetOwner()
        if valid(actor) and (not placement or same(actor, selected)) and point(collision) then
            local tracked = same(actor, enrolled[actor:GetAddress()]) and same(part, mesh(actor))
            local lit_flare = not tracked and not placement and same(part, flares.carry_root(actor, world))
                and actor:HasAuthority() and actor.Lit == true and part:IsAnySimulatingPhysics()
            if tracked or lit_flare then return actor, math.sqrt(dist2(origin, collision)) end
        end
        if expected_class and expected_class:match("^/Game/Classes/") and point(claimed) then
            local matched, center
            for _, candidate in pairs(enrolled) do
                if (not placement or same(candidate, selected)) and item_body(candidate) and actor_class(candidate) == expected_class then
                    local at = candidate:K2_GetActorLocation()
                    if point(at) and dist2(at, claimed) <= 35^2 then
                        if matched then return nil, "ambiguous loose item" end
                        matched, center = candidate, at
                    end
                end
            end
            if matched and dist2(origin, center) < range^2 and not impact(center, 1) then
                -- A remote pawn's camera is evaluated on a different frame. Verify
                -- the matching item's native interaction component from the current eye.
                local box, item_impact = impact(center, 4)
                if same(box, item_body(matched)) and point(item_impact) then
                    return matched, math.sqrt(dist2(origin, item_impact))
                end
            end
            return nil, "loose item interaction unavailable"
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
    -- Stock client Interact already traced the requested body. The host pose
    -- can settle behind different geometry, so match the bone and verify reach.
    local projection = (claimed.X - origin.X) * ray.X + (claimed.Y - origin.Y) * ray.Y + (claimed.Z - origin.Z) * ray.Z
    if not point(location) then return nil, "invalid body location" end
    if projection <= 0 or projection > range or dist2(along(projection), claimed) > 55^2
        or dist2(origin, location) >= range^2 then
        return nil, ("skeletal aim %.1f / %.1f, ray offset %.1f, body separation %.1f, distance %.1f"):format(
            projection, range, math.sqrt(dist2(along(projection), claimed)), math.sqrt(dist2(claimed, location)),
            math.sqrt(dist2(origin, location)))
    end
    return actor, math.sqrt(dist2(origin, location)), {number = number, part = part, bone = bone, name = expected_bone,
        location = location}
end

local function stock(pawn, kind, actor, range, pitch, yaw, corpse)
    local camera = pawn.Camera
    if not valid(camera) or not pose(camera.RelativeRotation) then return false end
    local previous_angle = copy_rot(camera.RelativeRotation)
    local previous_actor, previous_distance = pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance
    local pickup_body = kind == "grab" and item_body(actor)
    local previous_component = pickup_body and pawn.InteractionTarget_Component
    local previous_bone, previous_point
    if corpse then
        previous_bone, previous_point = pawn["Hit Bone Name"], pawn["WhereI'mLooking"]
        previous_point = {X = previous_point.X, Y = previous_point.Y, Z = previous_point.Z}
    end
    local invoked = pcall(function()
        camera:K2_SetWorldRotation({Pitch = pitch, Yaw = yaw, Roll = 0}, false, {}, true)
        if kind == "grab" then pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, range end
        if pickup_body then pawn.InteractionTarget_Component = pickup_body end
        if corpse then pawn["Hit Bone Name"], pawn["WhereI'mLooking"] = corpse.bone, corpse.location end
        pawn:Grab_Release_Object(kind == "throw", kind == "drop", pickup_body and true or false)
        if kind == "throw" and item_body(actor) and not loot.carry_body(actor) and not same(pawn.HeldObject, actor) then
            observers.catch_offering(state, pawn, actor, camera:GetForwardVector())
        end
    end)
    if invoked and kind == "throw" and not corpse then
        pcall(attribute_prop_throw, pawn, actor)
    end
    local restored = pcall(function()
        camera:K2_SetRelativeRotation(previous_angle, false, {}, true)
        pawn.ActorBeingLookedAt = valid(previous_actor) and previous_actor or nil
        pawn.ActorLookedAt_Distance = previous_distance
        if pickup_body then pawn.InteractionTarget_Component = valid(previous_component) and previous_component or nil end
        if corpse then pawn["Hit Bone Name"], pawn["WhereI'mLooking"] = previous_bone, previous_point end
    end)
    return invoked and restored
end

local function read_release_pose(fields)
    local location = {X = tonumber(fields[9]), Y = tonumber(fields[10]), Z = tonumber(fields[11])}
    local rotation = {Pitch = tonumber(fields[12]), Yaw = tonumber(fields[13]), Roll = tonumber(fields[14])}
    local velocity = {X = tonumber(fields[15]), Y = tonumber(fields[16]), Z = tonumber(fields[17])}
    local spin = {X = tonumber(fields[18]), Y = tonumber(fields[19]), Z = tonumber(fields[20])}
    if point(location) and pose(rotation) and vector(velocity, max_linear_velocity)
        and vector(spin, max_angular_velocity) then
        return {location = location, rotation = rotation, velocity = velocity, spin = spin}
    end
end

local function write_release_pose(root, bone, target)
    root:K2_SetWorldLocationAndRotation(target.location, target.rotation, false, {}, true)
    local location, rotation = root:K2_GetComponentLocation(), root:K2_GetComponentRotation()
    if not point(location) or dist2(location, target.location) > 2^2 or not pose(rotation)
        or math.abs(angle_delta(rotation.Pitch, target.rotation.Pitch)) > 2
        or math.abs(angle_delta(rotation.Yaw, target.rotation.Yaw)) > 2
        or math.abs(angle_delta(rotation.Roll, target.rotation.Roll)) > 2 then return false end

    root:SetPhysicsLinearVelocity(target.velocity, false, bone)
    root:SetPhysicsAngularVelocityInDegrees(target.spin, false, bone)
    local velocity = root:GetPhysicsLinearVelocity(bone)
    local spin = root:GetPhysicsAngularVelocityInDegrees(bone)
    return vector(velocity, max_linear_velocity + 8) and dist2(velocity, target.velocity) <= 8^2
        and vector(spin, max_angular_velocity + 10) and dist2(spin, target.spin) <= 10^2
end

local function apply_release_pose(entry, fields)
    if os.clock() >= entry.deadline or not valid(entry.actor) or not entry.actor:HasAuthority() then return false end
    local final = read_release_pose(fields)
    local bone = FName("None", EFindName.FNAME_Find)
    local root = final and rigid_root(entry, entry.grip, bone)
    local prior = root and solved_pose(root, bone)
    local origin = prior and entry.pawn:K2_GetActorLocation()
    -- Reject a handoff far from either the host's live grip or its holder.
    if not point(origin) or dist2(origin, final.location) > max_release_reach^2
        or dist2(prior.location, final.location) > max_handoff_offset^2 then return false end
    -- Seed the body before the same stock call releases the constraint and adds the drop/throw velocity.
    local ok, applied = pcall(write_release_pose, root, bone, final)
    if not ok or not applied then
        pcall(write_release_pose, root, bone, prior)
        return false
    end
    return true
end

local function remove(entry, kind, pitch, yaw)
    local pawn, actor, grip = entry.pawn, entry.actor, entry.grip
    if held(pawn, actor, grip, entry.corpse) then stock(pawn, kind, actor, 0, pitch, yaw) end
    local part = entry.corpse and entry.corpse.part or mesh(actor)
    if not valid(pawn) and valid(grip) and same(grip:GetGrabbedComponent(), part) then
        grip:ReleaseComponent()
    end
    if not entry.corpse and not unflagged_body(actor) and not valid(pawn) and mesh(actor) and actor:HasAuthority() and actor.BeingHeld == true
        and not valid(actor["Holding Actor"]) then
        actor:PickUp_Object(false, nil)
        if actor.BeingHeld ~= false then return false end
    end
    if valid(grip) and same(grip:GetGrabbedComponent(), part) then return false end
    if valid(pawn) and same(pawn.HeldObject, actor) then return false end
    if not entry.corpse and not unflagged_body(actor) and valid(actor) and same(actor["Holding Actor"], pawn) then return false end
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
        if item_body(actor) and valid(actor.Instigator) and guest_save.matches(actor.Instigator) then carry.enroll(actor) end
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
    if seat == "hosting" and (mode == "approach-placed" or mode == "tip") and mesh(fixture_actor) then
        local center = fixture_actor:K2_GetActorLocation()
        if mode == "tip" then
            local shape = fixture_actor.PropMesh
            local mass = shape:GetMass()
            shape:AddImpulseAtLocation({X = mass * 460, Y = 0, Z = 0},
                {X = center.X, Y = center.Y, Z = center.Z + 40}, FName("None"))
            shape:AddAngularImpulseInDegrees({X = 0, Y = 700, Z = 0}, FName("None"), true)
            return "Applied a stock physics impulse above the placed barrel's center"
        end
        local guest = guest_save.exact_item_context(current, seat, fields[4])
        if valid(guest) then
            for _, offset in ipairs({{X = 145, Y = 0}, {X = -145, Y = 0}, {X = 0, Y = 145}, {X = 0, Y = -145}}) do
                local at = {X = center.X + offset.X, Y = center.Y + offset.Y, Z = center.Z + 65}
                if guest:K2_TeleportTo(at, guest:K2_GetActorRotation()) then
                    guest:ForceNetUpdate()
                    return "Guest staged near the original placed barrel"
                end
            end
        end
        error("native_prop_carry_fixture_approach", 0)
    end
    if mode == "sample" and #fields == 4 then
        local actor = StaticFindObject(fields[4])
        local shape = mesh(actor)
        if shape and same(actor:GetWorld(), world) and actor.bReplicateMovement == true then
            local position, rotation = actor:K2_GetActorLocation(), actor:K2_GetActorRotation()
            return string.format("%.3f|%.3f|%.3f|%.3f|%.3f|%.3f",
                position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw, rotation.Roll)
        end
        error("native_prop_carry_fixture_sample_unavailable", 0)
    end
    if seat == "connected" and (mode == "grab" or mode == "pickup" or mode == "lamp-use") then
        local class = StaticFindObject(fields[4])
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if not valid(class) or not valid(api) then error("native_prop_carry_fixture_class", 0) end
        local output = {}
        api:GetAllActorsOfClass(world, class, output)
        local candidates = output.OutActors or output
        local expected = (mode == "pickup" or mode == "grab") and math.tointeger(fields[5])
        local selected = expected and fields[6] or fields[5]
        if expected and #candidates ~= expected then
            error("native_prop_carry_fixture_actor_count:" .. #candidates .. ":" .. expected, 0)
        end
        local origin, picked, best = pawn:K2_GetActorLocation(), nil, 250^2
        for _, candidate in ipairs(candidates) do
            if candidate and type(candidate.type) == "function" and candidate:type() == "RemoteUnrealParam" then candidate = candidate:get() end
            if mesh(candidate) and candidate.bReplicateMovement == true
                and (not selected or candidate:GetFullName() == selected) then
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
            local focused = pawn.ActorBeingLookedAt
            local matches = valid(component) and same(component:GetOwner(), picked)
                or not valid(component) and same(focused, picked)
            if not matches
                or type(SilverfishTakeNativeLanternPress) ~= "function" then
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
        local item = item_body(picked)
        if not (item and mode == "pickup") and not same(pawn.ActorBeingLookedAt, picked) then
            local target = pawn.ActorBeingLookedAt
            error("native_prop_carry_fixture_focus:" .. (valid(target) and target:GetFullName() or "none"), 0)
        end
        local reason = "stock_pickup_function"
        if mode == "pickup" then
            local distance = pawn.ActorLookedAt_Distance
            local component = item and pawn.InteractionTarget_Component
            if item then
                -- The fixture supplies the selected actor's focus while it is staged in midair.
                pawn.InteractionTarget_Component = item
                pawn.ActorLookedAt_Distance = math.sqrt(dist2(pawn.Camera:K2_GetComponentLocation(), item:K2_GetComponentLocation()))
            end
            local invoked = pcall(pawn.Grab_Release_Object, pawn, false, false, true)
            if item then
                pawn.InteractionTarget_Component = valid(component) and component or nil
                pawn.ActorLookedAt_Distance = distance
            end
            if not invoked then error("native_prop_carry_fixture_stock_pickup_unavailable", 0) end
        else
            local _, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            reason = status
        end
        if not held(pawn, picked, handle(pawn)) then error("native_prop_carry_fixture_input:" .. tostring(reason), 0) end
        return "Stock input grabbed the selected physics prop"
    end
    if seat == "connected" and local_hold and (mode == "turn" or mode == "throw" or mode == "drop") then
        if mode == "turn" then
            local angle = copy_rot(pawn.Controller:GetControlRotation())
            angle.Yaw = (angle.Yaw + 70 + 180) % 360 - 180
            pawn.Controller:SetControlRotation(angle)
            pawn:K2_SetActorRotation({Pitch = 0, Yaw = angle.Yaw, Roll = 0}, true)
            pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
        else
            if mode == "throw" and fields[4] == "up" then
                local angle = copy_rot(pawn.Controller:GetControlRotation())
                angle.Pitch = 26
                pawn.Controller:SetControlRotation(angle)
                pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
            end
            pawn:Grab_Release_Object(mode == "throw", mode == "drop", false)
        end
        return "stock prop carry fixture dispatched"
    end
    error("native_prop_carry_fixture_unavailable", 0)
end

function carry.command(fields, current, seat)
    if fields[2] == "native-prop-carry-fixture" then return fixture(fields, current, seat) end
    if fields[2] == "native-prop-carry-arm-filter" then
        local lease, serial, name, ms = fields[3], math.tointeger(fields[4]), fields[5], math.tointeger(fields[6])
        local pawn = current.local_pawn and current.local_pawn.pawn
        if not filter_available() or not enabled or seat ~= "connected" or not current.server or not current.listening
            or #fields ~= 6 or not guid(lease) or not ms or ms < 150 or ms > 900
            or not same(world, current.world) or not local_hold or not same(pawn, local_hold.pawn)
            or serial ~= local_hold.serial or not valid(local_hold.actor) or local_hold.actor:GetFullName() ~= name
            or not pawn:IsLocallyControlled() or pawn:HasAuthority() or pawn.Dead ~= false or players.is_downed(pawn)
            or local_hold.filter_lease and local_hold.filter_lease ~= lease then
            error("native_prop_carry_filter_unavailable", 0)
        end
        local root = filter_root(local_hold, handle(pawn))
        if not root or local_hold.actor:HasAuthority() then error("native_prop_carry_filter_root_unavailable", 0) end
        local armed, reason = SilverfishSetNativePropCarryMovement(
            pawn:GetAddress(), local_hold.actor:GetAddress(), root:GetAddress(), lease, ms)
        if not armed then error(reason or "native_prop_carry_filter_refused", 0) end
        local_hold.filter_lease = lease
        return "stock grip filter armed for the acknowledged carry"
    end
    if fields[2] == "native-prop-carry-refuse" and enabled and seat == "connected" and current.server
        and same(world, current.world) and #fields == 3 then
        local pawn = current.local_pawn and current.local_pawn.pawn
        if local_hold and math.tointeger(fields[3]) == local_hold.serial
            and same(pawn, local_hold.pawn) and pawn:IsLocallyControlled() and not pawn:HasAuthority() then
            local refused = local_hold
            release_grip_filter()
            local_hold = nil
            if held(pawn, refused.actor, handle(pawn), refused.corpse) then pawn:Grab_Release_Object(false, true, false) end
            if refused.loot_id then loot.stop_carry(pawn, refused.actor) end
        end
        return "stale stock grip cleared"
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
    local release_pose_applied = false
    if kind == "grab" and (#fields == 12 or #fields == 13 or #fields == 14) then
        local position = {X = tonumber(fields[10]), Y = tonumber(fields[11]), Z = tonumber(fields[12])}
        local number = #fields == 14 and math.tointeger(fields[13]) or nil
        local placement = #fields == 13 and fields[13] or nil
        local loot_id = placement and placement:match("^loot:([0-9a-f]+)$")
        if #fields == 14 and (not number or number < 1 or number > 65535) then
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        local actor, distance, corpse
        if loot_id then
            actor = guid(loot_id) and loot.carry_actor(loot_id)
            local center = valid(actor) and actor:K2_GetActorLocation()
            local eye = valid(pawn.Camera) and pawn.Camera:K2_GetComponentLocation()
            if point(center) and point(eye) and point(position) and dist2(center, position) <= 35^2
                and loot.authority_sight(current, seat, pawn, loot_id, center) then
                distance = math.sqrt(dist2(center, eye))
            else
                actor = nil
            end
        else
            actor, distance, corpse = trace(pawn, pitch, yaw, number, fields[14], position, fields[9], placement)
        end
        local grip = handle(pawn)
        if entry or not actor or not point(position) or actor_class(actor) ~= fields[9]
            or not valid(grip) or valid(pawn.HeldObject) or valid(grip:GetGrabbedComponent()) then
            if fixtures and (number or offering_fixture) then
                error("native_prop_carry_stock_target_unavailable:" .. (not actor and tostring(distance) or "grip or class"), 0)
            end
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        local item = loot_id and item_box(actor) or item_body(actor)
        local unflagged = item or flares.carry_root(actor, world)
        if corpse or unflagged then
            local host = current.local_pawn and current.local_pawn.pawn
            if valid(host) and same(host.HeldObject, actor) then
                if fixtures then error("native_prop_carry_stock_target_unavailable:host grip", 0) end
                error("native_prop_carry_stock_target_unavailable", 0)
            end
            for _, holder in pairs(holdings) do
                if same(holder.actor, actor) then error("native_prop_carry_wrong_holder", 0) end
            end
        end
        if not corpse and (dist2(position, actor:K2_GetActorLocation()) > 35^2
            or not unflagged and actor.BeingHeld ~= false) then
            if fixtures and item then
                error(("native_prop_carry_stock_target_unavailable: item moved %.1f cm"):format(
                    math.sqrt(dist2(position, actor:K2_GetActorLocation()))), 0)
            end
            error("native_prop_carry_stock_target_unavailable", 0)
        end
        if loot_id and not loot.arm_carry(pawn, actor) then error("native_prop_carry_wrong_holder", 0) end
        if not stock(pawn, kind, actor, distance, pitch, yaw, corpse) or not held(pawn, actor, grip, corpse) then
            if same(pawn.HeldObject, actor) then stock(pawn, "drop", actor, 0, pitch, yaw) end
            if loot_id then loot.stop_carry(pawn, actor) end
            error("native_prop_carry_stock_grab_unverified", 0)
        end
        entry = {lease = lease, pawn = pawn, actor = actor, actor_name = actor:GetFullName(), grip = grip,
            serial = serial, deadline = os.clock() + 1.5, corpse = corpse, loot_id = loot_id}
        holdings[lease] = entry
    elseif (kind == "drop" or kind == "throw") and (#fields == 8 or #fields == 20)
        and entry and same(pawn, entry.pawn) and serial > entry.serial then
        if #fields == 20 then
            local ok, applied = pcall(apply_release_pose, entry, fields)
            release_pose_applied = ok and applied
        end
        if not remove(entry, kind, pitch, yaw) then error("native_prop_carry_stock_release_unverified", 0) end
    else
        error("native_prop_carry_wrong_holder", 0)
    end
    latest = {lease, serial, kind, name, entry.actor_name}
    if entry.corpse then latest[6], latest[7] = entry.corpse.number, entry.corpse.name end
    if entry.loot_id then latest[6] = "loot:" .. entry.loot_id end
    if release_pose_applied then latest[6] = "release-pose" end
    return "stock physics prop action verified"
end

local function interpolate_rotation(angle, goal, alpha)
    local difference = angle_delta(angle, goal)
    return (angle + difference * alpha + 180) % 360 - 180
end

local function guide_handle(entry, now)
    local target, rotation = entry.target, entry.rotation
    local previous, previous_rotation = entry.applied_target, entry.applied_rotation
    if previous and previous_rotation then
        local seconds = math.min(math.max(now - entry.applied_at, 0), .05)
        local alpha = 1 - math.exp(-seconds / .04)
        target = {X = previous.X + (target.X - previous.X) * alpha,
            Y = previous.Y + (target.Y - previous.Y) * alpha,
            Z = previous.Z + (target.Z - previous.Z) * alpha}
        rotation = {Pitch = interpolate_rotation(previous_rotation.Pitch, rotation.Pitch, alpha),
            Yaw = interpolate_rotation(previous_rotation.Yaw, rotation.Yaw, alpha),
            Roll = interpolate_rotation(previous_rotation.Roll, rotation.Roll, alpha)}
    end
    entry.grip:SetTargetLocationAndRotation(target, rotation)
    entry.applied_target, entry.applied_rotation, entry.applied_at = target, rotation, now
end

local function read_targets(now)
    if now < (next_read or 0) then return end
    next_read = now + .05
    local active, frame
    if type(SilverfishTakeLocalPropPoses) == "function" then
        active, frame = SilverfishTakeLocalPropPoses()
    end
    local frame_limit = 4 * 257
    if not active and mailbox then
        local input = io.open(mailbox, "rb")
        if input then
            frame = input:read(frame_limit + 1)
            input:close()
        end
    end
    if not frame or #frame > frame_limit then return end
    local row_count = 0
    for line in frame:gmatch("[^\r\n]+") do
        row_count = row_count + 1
        if row_count > 4 or #line > 256 then break end
        local lease, serial, x, y, z, pitch, yaw, roll = line:match("^([a-f0-9]+)\t(%d+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)\t([^\t]+)$")
        local entry, number = holdings[lease], math.tointeger(serial)
        local target = {X = tonumber(x), Y = tonumber(y), Z = tonumber(z)}
        local rotation = {Pitch = tonumber(pitch), Yaw = tonumber(yaw), Roll = tonumber(roll)}
        local origin = entry and valid(entry.pawn) and entry.pawn:K2_GetActorLocation()
        if entry and now <= entry.deadline and number and number > (entry.sample or 0)
            and held(entry.pawn, entry.actor, entry.grip, entry.corpse)
            and point(origin) and point(target) and pose(rotation) and dist2(origin, target) <= 375^2 then
            entry.sample, entry.deadline, entry.target, entry.rotation = number, now + 1.5, target, rotation
        end
    end
end

local function publish_local_pose(now)
    if not local_hold or now < (next_local_pose or 0) or type(SilverfishPublishLocalPropPose) ~= "function" then return end
    next_local_pose = now + .05
    local pawn = local_hold.pawn
    if not state.listening or state.server ~= true or not same(state.local_pawn and state.local_pawn.pawn, pawn)
        or pawn.Dead ~= false or not pawn:IsLocallyControlled() or pawn:HasAuthority() or players.is_downed(pawn) ~= false
        or not held(pawn, local_hold.actor, handle(pawn), local_hold.corpse) then return end
    local position, rotation = stock_target(pawn)
    if point(position) and pose(rotation) then
        SilverfishPublishLocalPropPose(string.format("%s\t%d\t%.1f\t%.1f\t%.1f\t%.1f\t%.1f\t%.1f",
            pawn:GetFullName(), local_hold.serial, position.X, position.Y, position.Z,
            rotation.Pitch, rotation.Yaw, rotation.Roll))
    end
end

function carry.update()
    if phase == "connected" then
        if not local_hold then return end
        if local_hold.filter_lease
            and (not valid(local_hold.pawn) or players.is_downed(local_hold.pawn)) then release_grip_filter() end
        publish_local_pose(os.clock())
        return
    end
    if phase ~= "hosting" or not next(holdings) then return end
    local now = os.clock()
    read_targets(now)
    for _, entry in pairs(holdings) do
        if now > entry.deadline or not valid(entry.pawn) or entry.pawn.Dead ~= false
            or not held(entry.pawn, entry.actor, entry.grip, entry.corpse) then
            remove(entry, "drop", 0, 0)
        elseif entry.target then
            guide_handle(entry, now)
        end
    end
end

function carry.rows(current, seat, row)
    local rows = {}
    if not enabled or not same(world, current.world) then return rows end
    if seat == "hosting" and fixtures then
        for address, entry in pairs(loose_fixtures) do
            if same(enrolled[address], entry.actor) and not entry.actor:IsActorBeingDestroyed() then
                rows[#rows + 1] = row("native-authored-physics-prop", current.map, entry.name, entry.class, entry.mass)
            end
        end
    end
    local fixture_mesh = seat == "hosting" and fixtures and mesh(fixture_actor)
    if fixture_mesh then
        local position = fixture_actor:K2_GetActorLocation()
        local velocity = fixture_mesh:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
        local item = item_body(fixture_actor)
        local grip, carrying = {}, not item and fixture_actor.BeingHeld == true
        for _, entry in pairs(holdings) do
            if same(entry.actor, fixture_actor) then
                if item then carrying = held(entry.pawn, fixture_actor, entry.grip, entry.corpse) end
                if entry.target then grip = {entry.sample, entry.target.X, entry.target.Y, entry.target.Z} end
                break
            end
        end
        rows[#rows + 1] = row("native-prop-carry-fixture", current.map, actor_class(fixture_actor),
            fixture_actor:GetFullName(), position.X, position.Y, position.Z, velocity.X, velocity.Y, velocity.Z,
            carrying and 1 or 0, table.unpack(grip))
    end
    if seat == "hosting" and latest then rows[#rows + 1] = row("native-prop-carry", current.map, table.unpack(latest)) end
    if seat == "hosting" and filter_available() then
        local now = os.clock()
        for id, entry in pairs(holdings) do
            local remaining = math.min(1500, math.floor((entry.deadline - now) * 1000))
            if remaining > 0 and filter_root(entry, entry.grip)
                and players.is_standing_guest(entry.pawn, entry.pawn:GetFullName()) then
                rows[#rows + 1] = row("native-prop-carry-held", current.map, id, entry.serial,
                    entry.pawn:GetFullName(), entry.actor_name, remaining)
            end
        end
    end
    if seat ~= "connected" then return rows end
    if filter_available() then
        rows[#rows + 1] = row("native-prop-carry-filter-ready", current.map)
        local lease, active, suppressed, passed = SilverfishReadNativePropCarryMovement()
        if guid(lease) then
            rows[#rows + 1] = row("native-prop-carry-filter", current.map, lease, active and 1 or 0, suppressed, passed)
        end
    end
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
    if seat ~= "connected" or not same(world, current.world) then release_grip_filter() end
    if not same(world, current.world) then
        world, enrolled, holdings, edges, local_hold, latest = current.world, {}, {}, {}, nil, nil
        fixture_actor, loose_fixtures, next_read, next_local_pose = nil, {}, nil, nil
        host_release = nil
    end
    if seat ~= "hosting" then host_release = nil end
    state, phase, mailbox = current, seat, bridge and bridge .. "/native-prop-carry-poses"
    if not hook and type(RegisterHook) == "function" and valid(StaticFindObject(grab_path)) then
        if valid(StaticFindObject(release_component_path)) then
            -- The Blueprint hook runs after release. Both release branches call this native method
            -- while HeldObject still identifies the grip and before applying stock velocity.
            release_hook = pcall(RegisterHook, release_component_path, function(context) pcall(capture_release, context:get()) end)
        end
        RegisterHook(grab_path, function(context, throwing, dropping)
            pcall(observe, context:get(), throwing:get() == true, dropping:get() == true)
        end)
        hook = true
    end
end

return carry
