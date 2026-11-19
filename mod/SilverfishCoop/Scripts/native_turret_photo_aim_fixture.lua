local fixture = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local level = map .. ".DeadBridge_P:PersistentLevel."
local targets = { ["turret-13"] = "Turret_MechGun_01_BP_C_13", ["turret-15"] = "Turret_MechGun_01_BP_C_15" }
local stock_floors = {
    ["Class /Script/Engine.StaticMeshComponent"] = true,
    ["Class /Script/Engine.InstancedStaticMeshComponent"] = true,
    ["Class /Script/Engine.HierarchicalInstancedStaticMeshComponent"] = true,
    ["Class /Script/Engine.ModelComponent"] = true,
    ["Class /Script/Landscape.LandscapeHeightfieldCollisionComponent"] = true,
    ["Class /Script/Landscape.LandscapeMeshCollisionComponent"] = true,
}
local invisible = { R = 0, G = 0, B = 0, A = 0 }
local view_range = 18000
local floor_layers = { {260, -1900}, {-650, -2250}, {1650, 260} }
local floor_ranges = {510, 860, 1300, 1800, 2450, 3400, 4750, 6600, 9000, 12000, 15000}
local floor_offsets = {0, 45, -45, 90, -90, 135, -135, 180}
local counters = {"samples", "floor_miss", "floor_missing_vector", "floor_steep", "floor_out_of_segment",
    "floor_surface", "floor_obstructed", "floor_ok", "duplicate_floor",
    "teleport_refused", "teleport_readback", "view_unavailable", "pitch", "sight_range", "sight_blocked",
    "sight_unavailable", "lens_axis"}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function vector(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return { X = value.X, Y = value.Y, Z = value.Z }
    end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
    end
end

local function distance_squared(first, second)
    if not first or not second then return math.huge end
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function same_rotation(observed, expected)
    if not observed then return false end
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((observed[axis] - expected[axis] + 180) % 360 - 180) > 0.5 then return false end
    end
    return true
end

local function owned(object, owner, class)
    return valid(object) and object:GetClass():GetFullName() == "Class /Script/Engine." .. class
        and same(object:GetOwner(), owner)
end

local function fail(reason)
    error("stock_turret_photo_fixture_" .. reason, 0)
end

local function look_from(origin, point)
    local dx, dy, dz = point.X - origin.X, point.Y - origin.Y, point.Z - origin.Z
    local horizontal = math.sqrt(dx * dx + dy * dy)
    return { Pitch = math.deg(math.atan(dz, horizontal)), Yaw = math.deg(math.atan(dy, dx)), Roll = 0 }
end

local function record(pose, reason)
    pose.proof[reason] = (pose.proof[reason] or 0) + 1
end

local function collision_component(hit)
    local out = hit.OutHit or hit
    return out.Component and out.Component:Get()
end

local function diagnostic(pose)
    local info = {"target_z=" .. string.format("%.1f", pose.point.Z)}
    for _, reason in ipairs(counters) do info[#info + 1] = reason .. "=" .. (pose.proof[reason] or 0) end
    if pose.proof.surface then info[#info + 1] = "surface=" .. pose.proof.surface end
    if pose.proof.blocker then info[#info + 1] = "blocker=" .. pose.proof.blocker end
    if pose.proof.sample then info[#info + 1] = "first_floor=" .. pose.proof.sample end
    return table.concat(info, ", ")
end

local function on_axis(lens, point)
    local origin, forward = vector(lens:K2_GetComponentLocation()), vector(lens:GetForwardVector())
    if not origin or not forward then return false end
    local distance = math.sqrt(distance_squared(origin, point))
    return distance >= 130 and distance <= view_range and
        ((point.X - origin.X) * forward.X + (point.Y - origin.Y) * forward.Y +
            (point.Z - origin.Z) * forward.Z) / distance >= 0.997
end

local function visible(pose, origin)
    local distance = distance_squared(origin, pose.point)
    if distance < 130^2 or distance > view_range^2 then
        record(pose, "sight_range")
        return false
    end
    -- Mirror CameraTarget.TakePhoto's channel and ignore rules. This check never calls TakePhoto.
    local hit = {}
    local blocked = pose.trace:LineTraceSingle(pose.tracker, origin, pose.point, 0, true,
        {pose.host}, 0, hit, true, invisible, invisible, 0)
    if blocked == false then return true end
    record(pose, blocked == true and "sight_blocked" or "sight_unavailable")
    if blocked == true and not pose.proof.blocker then
        local component = collision_component(hit)
        if valid(component) then pose.proof.blocker = component:GetFullName():gsub("%c", "?"):sub(1, 196) end
    end
    return false
end

local function floor_at(pose, x, y, layer)
    local top = { X = x, Y = y, Z = pose.point.Z + layer[1] }
    local bottom = { X = x, Y = y, Z = pose.point.Z + layer[2] }
    local result = {}
    record(pose, "samples")
    if pose.trace:LineTraceSingle(pose.pawn, top, bottom, 0, false,
        {pose.host, pose.pawn, pose.camera, pose.owner}, 0, result, true, invisible, invisible, 0) ~= true then
        record(pose, "floor_miss")
        return
    end
    local hit = result.OutHit or result
    local at, normal = vector(hit.ImpactPoint), vector(hit.ImpactNormal)
    local reason = not at or not normal
    reason = reason and "floor_missing_vector" or normal.Z < 0.75 and "floor_steep"
        or (at.Z > top.Z - 1 or at.Z < bottom.Z) and "floor_out_of_segment"
    if reason then
        record(pose, reason)
        if not pose.proof.sample then
            pose.proof.sample = string.format("%s/%s/%s/%.0f/%.0f", reason, at and string.format("%.1f", at.Z) or "-",
                normal and string.format("%.3f", normal.Z) or "-", top.Z, bottom.Z)
        end
        return
    end
    local ground = collision_component(result)
    local class = valid(ground) and ground:GetClass():GetFullName()
    local owner = valid(ground) and ground:GetOwner()
    if not class or not stock_floors[class] or ground.Mobility ~= 0 or not valid(owner)
        or not same(owner:GetWorld(), pose.world) then
        record(pose, "floor_surface")
        if class and not pose.proof.surface then pose.proof.surface = class .. "/" .. tostring(ground.Mobility) end
        return
    end
    local point = {X = x, Y = y, Z = at.Z + pose.half_height + 4}
    local above = {X = x, Y = y, Z = point.Z + 2}
    local blocked = pose.trace:CapsuleTraceSingle(pose.pawn, point, above, pose.radius, pose.half_height,
        0, false, {pose.host, pose.pawn, pose.camera}, 0, {}, true, invisible, invisible, 0)
    if blocked ~= false then record(pose, "floor_obstructed"); return end
    record(pose, "floor_ok")
    return point
end

local function face(pose)
    local eye = vector(pose.view:K2_GetComponentLocation())
    if not eye then
        record(pose, "view_unavailable")
        return false
    end
    local view_angle = look_from(eye, pose.point)
    if math.abs(view_angle.Pitch) > 70 then
        record(pose, "pitch")
        return false
    end
    pose.touched = true
    pose.controller:SetControlRotation(view_angle)
    pose.view:K2_SetWorldRotation(view_angle, false, {}, true)

    local origin = vector(pose.lens:K2_GetComponentLocation())
    if not origin then
        record(pose, "view_unavailable")
        return false
    end
    if not visible(pose, origin) then return false end
    if not on_axis(pose.lens, pose.point) then
        -- Stock HoldArrow follows aim during its next tick. For this synchronous fixture only,
        -- align the real capture component before reading its normal stock transform.
        pose.lens:K2_SetWorldRotation(look_from(origin, pose.point), false, {}, true)
    end
    if on_axis(pose.lens, pose.point) then return true end
    record(pose, "lens_axis")
    return false
end

local function pose_at_floor(pose, at)
    local view = look_from(at, pose.point)
    pose.touched = true
    if pose.pawn:K2_TeleportTo(at, { Pitch = 0, Yaw = view.Yaw, Roll = 0 }) ~= true then
        record(pose, "teleport_refused")
        return false
    end
    pose.moved = true
    if distance_squared(vector(pose.pawn:K2_GetActorLocation()), at) > 15^2 then
        record(pose, "teleport_readback")
        return false
    end
    return face(pose)
end

local function position(pose)
    local starting_lens = vector(pose.lens:K2_GetComponentLocation())
    if starting_lens and distance_squared(starting_lens, pose.point) <= view_range^2 and face(pose) then return true end
    -- The authored alarm-tower bases are about 810 cm below their turret capsules.
    -- Try terrain beneath each tower and under overhangs before elevated approaches.
    for _, range in ipairs(floor_ranges) do
        for _, offset in ipairs(floor_offsets) do
            local angle = math.rad(pose.target_yaw + offset)
            local x, y = pose.point.X + range * math.cos(angle), pose.point.Y + range * math.sin(angle)
            local seen = {}
            for _, layer in ipairs(floor_layers) do
                local at = floor_at(pose, x, y, layer)
                if at then
                    local height = math.floor(at.Z / 10)
                    if seen[height] then
                        record(pose, "duplicate_floor")
                    else
                        seen[height] = true
                        if pose_at_floor(pose, at) then return true end
                    end
                end
            end
        end
    end
    return false
end

local function restore(pose)
    local pawn, old = pose.pawn, pose.old
    if not same(pawn:GetWorld(), pose.world) or not same(pawn.Controller, pose.controller)
        or not same(pawn.EquipedItem, pose.camera) or not same(pose.camera.SceneCaptureComponent2D, pose.lens) then return false end
    local located = not pose.moved or pawn:K2_TeleportTo(old.position, old.body) == true
    pose.controller:SetControlRotation(old.control)
    pose.view:K2_SetRelativeRotation(old.view, false, {}, true)
    pose.lens:K2_SetRelativeRotation(old.lens, false, {}, true)
    if pose.moved then pawn:ForceNetUpdate() end
    return located and distance_squared(vector(pawn:K2_GetActorLocation()), old.position) <= 5^2
        and distance_squared(vector(pawn:GetVelocity()), old.velocity) <= 3^2
        and same_rotation(rotation(pawn:K2_GetActorRotation()), old.body)
        and same_rotation(rotation(pose.controller:GetControlRotation()), old.control)
        and same_rotation(rotation(pose.view.RelativeRotation), old.view)
        and same_rotation(rotation(pose.lens.RelativeRotation), old.lens)
end

local function qualify_pose(context, camera, tracker, owner, target, host_only)
    local pawn, name = context.pawn, targets[target]
    if not enabled or context.map ~= map or not name or not valid(pawn)
        or same(pawn, context.host) ~= host_only or same(context.own_save, context.save) ~= host_only
        or not pawn:HasAuthority() or pawn.Dead ~= false or not same(pawn.Save, context.own_save)
        or not same(pawn.EquipedItem, camera) or not same(camera:GetOwner(), pawn)
        or not valid(tracker) or not same(tracker:GetOwner(), owner)
        or owner:GetFullName() ~= "Turret_MechGun_01_BP_C " .. level .. name
        or tracker:GetFullName() ~= "CameraTarget_BP_C " .. level .. name .. ".CameraTarget_BP" then
        fail("exact_photographer_and_target_required")
    end

    local controller, view, lens, body = pawn.Controller, pawn.Camera, camera.SceneCaptureComponent2D, pawn.CapsuleComponent
    local scene_targets = tracker.Targets
    local capsule = scene_targets and scene_targets:GetArrayNum() == 1 and scene_targets[1]
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(trace) or not valid(controller) or not same(controller.Pawn, pawn)
        or not same(controller:GetWorld(), context.world) or not owned(view, pawn, "CameraComponent")
        or not owned(lens, camera, "SceneCaptureComponent2D") or not owned(body, pawn, "CapsuleComponent")
        or not owned(capsule, owner, "CapsuleComponent") or not same(capsule, owner.Capsule) then
        fail("owned_camera_and_capsule_required")
    end

    local old = { position = vector(pawn:K2_GetActorLocation()), body = rotation(pawn:K2_GetActorRotation()),
        control = rotation(controller:GetControlRotation()), view = rotation(view.RelativeRotation),
        lens = rotation(lens.RelativeRotation), velocity = vector(pawn:GetVelocity()) }
    local point, angle = vector(capsule:K2_GetComponentLocation()), rotation(owner:K2_GetActorRotation())
    local half_height = body:GetScaledCapsuleHalfHeight()
    local radius = body:GetScaledCapsuleRadius()
    if not old.position or not old.body or not old.control or not old.view or not old.lens or not old.velocity
        or distance_squared(old.velocity, {X = 0, Y = 0, Z = 0}) > 5^2 or not point or not angle
        or vector(lens:K2_GetComponentLocation()) == nil or not finite(half_height)
        or half_height < 20 or half_height > 150 or not finite(radius) or radius < 10 or radius > half_height then
        fail("stable_stock_pose_required")
    end

    return { pawn = pawn, host = context.host, world = context.world, controller = controller, view = view,
        lens = lens, camera = camera, tracker = tracker, owner = owner, trace = trace, half_height = half_height, radius = radius,
        point = point, target_yaw = angle.Yaw, old = old, proof = {} }
end

local function with_position(pose, take_photo)
    local result = table.pack(pcall(function()
        if not position(pose) then fail("unobstructed_stock_pose_unavailable (" .. diagnostic(pose) .. ")") end
        if take_photo then return take_photo() end
        pose.pawn:ForceNetUpdate()
        return true
    end))
    if pose.touched and (take_photo or not result[1]) then
        local restored, checked = pcall(restore, pose)
        if not restored or checked ~= true then error("mutation_unverified_stock_turret_photo_fixture_pose", 0) end
    end
    if not result[1] then error(result[2], 0) end
    return table.unpack(result, 2, result.n)
end

function fixture.capture(context, camera, tracker, owner, target, take_photo)
    return with_position(qualify_pose(context, camera, tracker, owner, target, false), take_photo)
end

function fixture.aim_host(context, camera, tracker, owner, target)
    return with_position(qualify_pose(context, camera, tracker, owner, target, true))
end

return fixture
