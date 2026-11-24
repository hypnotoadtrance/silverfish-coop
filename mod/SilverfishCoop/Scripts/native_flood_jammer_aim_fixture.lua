local guest_save = require("guest_save")
local firearms = require("guest_weapon_registry")

local fixture = {}
local trace_path = "/Script/Engine.Default__KismetSystemLibrary"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local color = {R = 0, G = 0, B = 0, A = 0}
local last

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < math.huge end
local function vector(v) return v and finite(v.X) and finite(v.Y) and finite(v.Z) end
local function rotation(r)
    if r and finite(r.Pitch) and finite(r.Yaw) and finite(r.Roll) then
        return {Pitch = r.Pitch, Yaw = r.Yaw, Roll = r.Roll}
    end
end

local function rotation_matches(current, expected)
    current = rotation(current)
    if not current then return false end
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((current[axis] - expected[axis] + 180) % 360 - 180) > 1 then return false end
    end
    return true
end
local function look(origin, target)
    local x, y, z = target.X - origin.X, target.Y - origin.Y, target.Z - origin.Z
    local horizontal = math.sqrt(x * x + y * y)
    return {Pitch = math.deg(math.atan(z, horizontal)), Yaw = math.deg(math.atan(y, x)), Roll = 0},
        math.sqrt(horizontal * horizontal + z * z)
end
local function component(value, owner, kind)
    return valid(value) and same(value:GetOwner(), owner) and value:GetClass():GetFullName() == "Class /Script/Engine." .. kind
end
local function refuse(reason) error("stock_flood_jammer_aim_" .. reason, 0) end

local function equipped(state, phase, name, guid, tag)
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(save) or pawn.Dead ~= false or pawn.IsWeaponBlocked ~= false
        or not finite(save.PlayerHealth) or save.PlayerHealth <= 0 then refuse("standing_guest_required") end
    local weapon, camera, capsule, controller = pawn.EquipedItem, pawn.Camera, pawn.CapsuleComponent, pawn.Controller
    local assassination = tag == "native-lenny" or tag == "native-spratt"
    local path = firearms.paths[assassination and "sg-71-a" or "light-pistol"]
    if not valid(weapon) or not valid(controller) or not weapon:HasAuthority() or weapon:IsActorBeingDestroyed()
        or not same(weapon:GetWorld(), state.world) or weapon:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. path
        or not same(weapon:GetOwner(), pawn) or not same(weapon:GetInstigator(), pawn)
        or not same(weapon.User, pawn) or not same(weapon.Player_Ref, pawn) or weapon.HeldByPlayer ~= true
        or not component(camera, pawn, "CameraComponent") or not component(capsule, pawn, "CapsuleComponent")
        or not component(weapon.Arrow_Muzzle, weapon, "ArrowComponent") then refuse("equipment_unqualified") end
    if type(SilverfishInspectNativeGuestWeapon) ~= "function" then refuse("weapon_inspector_unavailable") end
    local ok, equipped_status, rounds, saved = SilverfishInspectNativeGuestWeapon(pawn:GetAddress(), save:GetAddress(), guid)
    if ok ~= true or equipped_status ~= "equipped" or not finite(rounds) or rounds ~= saved or rounds <= 0 then
        refuse("loaded_owned_weapon_required")
    end
    return {pawn = pawn, save = save, weapon = weapon, camera = camera, capsule = capsule,
        controller = controller, muzzle = weapon.Arrow_Muzzle}
end

local function trace(context, first, last, channel, complex, ignored)
    local hit = {}
    if context.trace:LineTraceSingle(context.pawn, first, last, channel, complex, ignored, 0,
        hit, true, color, color, 0) == true then return hit.OutHit or hit end
end

-- Match the stock weapon capture: complex visibility from the camera, then
-- stock projectile collision from the muzzle through the camera's impact.
local function fixed_contact(pawn, weapon, camera, mesh, center)
    local library = StaticFindObject(trace_path)
    local muzzle_component = weapon.Arrow_Muzzle
    if not valid(library) or not component(muzzle_component, weapon, "ArrowComponent") then return end
    local context = {trace = library, pawn = pawn}
    local ignored = {pawn, weapon}
    local eye = camera:K2_GetComponentLocation()
    for _, height in ipairs({0, 8, -8, 16, -16, 25, 45, 65, -35, -55, 90}) do
        local target = {X = center.X, Y = center.Y, Z = center.Z + height}
        local view, range = look(eye, target)
        if range > 1 and range <= 550 then
            local reach = (range + 100) / range
            local finish = {X = eye.X + (target.X - eye.X) * reach, Y = eye.Y + (target.Y - eye.Y) * reach,
                Z = eye.Z + (target.Z - eye.Z) * reach}
            local sight = trace(context, eye, finish, 1, true, ignored)
            if sight and vector(sight.ImpactPoint) and sight.Component and same(sight.Component:Get(), mesh) then
                local muzzle = muzzle_component:K2_GetComponentLocation()
                local point = sight.ImpactPoint
                local angle, distance = look(muzzle, point)
                if distance >= 75 and math.abs(angle.Pitch) < 56 then
                    local span = (distance + 100) / distance
                    local end_point = {X = muzzle.X + (point.X - muzzle.X) * span,
                        Y = muzzle.Y + (point.Y - muzzle.Y) * span, Z = muzzle.Z + (point.Z - muzzle.Z) * span}
                    local hit = trace(context, muzzle, end_point, 2, true, ignored)
                    if hit and hit.Component and same(hit.Component:Get(), mesh) then
                        return view, distance, angle
                    end
                end
            end
        end
    end
end

local function mesh_center(mesh)
    local bounds = {}
    StaticFindObject(trace_path):GetComponentBounds(mesh, bounds, {}, {})
    return bounds.Origin or bounds
end

function fixture.aim_fixed_guest(state, phase, mesh)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not enabled or phase ~= "connected" or not valid(pawn) or not pawn:IsLocallyControlled()
        or pawn:HasAuthority() or not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, state.controller) then
        refuse("local_guest_required")
    end
    local weapon, camera = pawn.EquipedItem, pawn.Camera
    if not valid(weapon) or weapon:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. firearms.paths["light-pistol"]
        or not component(camera, pawn, "CameraComponent") then return "waiting" end
    local view = fixed_contact(pawn, weapon, camera, mesh, mesh_center(mesh))
    if not view then return "waiting" end
    state.controller:SetControlRotation(view)
    camera:K2_SetWorldRotation(view, false, {}, true)
    return "the guest crosshair is on the tank"
end

local function floor_at(context, center, x, y, radius, height)
    local top, bottom = {X = x, Y = y, Z = center.Z + 115}, {X = x, Y = y, Z = center.Z - context.floor_depth}
    local floor = trace(context, top, bottom, 0, false, context.ignored)
    if not floor or not vector(floor.ImpactPoint) or not vector(floor.ImpactNormal) or floor.ImpactNormal.Z < 0.7
        or floor.ImpactPoint.Z >= top.Z - 1 or floor.ImpactPoint.Z <= bottom.Z then return end
    local point = {X = x, Y = y, Z = floor.ImpactPoint.Z + height + 4}
    local above = {X = x, Y = y, Z = point.Z + 4}
    local hit = {}
    if context.trace:CapsuleTraceSingle(context.pawn, point, above, radius, height, 0, false,
        context.ignored, 0, hit, true, color, color, 0) == false then return point end
end

local function align(context, center)
    if context.fixed then
        local view, distance, aim = fixed_contact(context.pawn, context.weapon, context.camera,
            context.target_mesh, center)
        if not view then return end
        context.controller:SetControlRotation(view)
        context.camera:K2_SetWorldRotation(view, false, {}, true)
        context.muzzle:K2_SetWorldRotation(aim, false, {}, true)
        return distance, context.target_mesh:GetFullName()
    end
    local eye = context.camera:K2_GetComponentLocation()
    if not vector(eye) then return end
    local view = look(eye, center)
    context.controller:SetControlRotation(view)
    context.camera:K2_SetWorldRotation(view, false, {}, true)
    local muzzle = context.muzzle:K2_GetComponentLocation()
    if not vector(muzzle) then return end

    for _, height in ipairs({0, 8, -8, 16, -16, 25}) do
        local target = {X = center.X, Y = center.Y, Z = center.Z + height}
        local aim, distance = look(muzzle, target)
        if distance >= 75 and distance <= context.aim_range and math.abs(aim.Pitch) < context.aim_pitch then
            context.muzzle:K2_SetWorldRotation(aim, false, {}, true)
            local forward = context.muzzle:GetForwardVector()
            if vector(forward) and (forward.X * (target.X - muzzle.X) + forward.Y * (target.Y - muzzle.Y)
                + forward.Z * (target.Z - muzzle.Z)) / distance >= 0.999 then
                local reach = distance + 35
                local finish = {X = muzzle.X + forward.X * reach, Y = muzzle.Y + forward.Y * reach,
                    Z = muzzle.Z + forward.Z * reach}
                local hit = trace(context, muzzle, finish, 2, true, {context.pawn, context.weapon})
                local contact = hit and hit.Component and hit.Component:Get()
                if same(contact, context.target_capsule) or same(contact, context.target_mesh) then
                    return distance, contact:GetFullName()
                end
            end
        end
    end
end

function fixture.aim_target(state, phase, name, guid, tag, actor, target_capsule, target_mesh, temporary)
    last = nil
    if not enabled or phase ~= "hosting" or type(guid) ~= "string" or #guid ~= 32 or guid:find("[^A-F0-9]") then
        refuse("headless_fixture_and_guid_required")
    end
    local fixed = tag == "native-fixed-explosive"
    if not valid(actor) or not same(actor:GetWorld(), state.world) or not actor:HasAuthority()
        or actor:IsActorBeingDestroyed() or not finite(actor.Health) or actor.Health <= 0
        or not component(fixed and target_mesh or target_capsule, actor,
            fixed and "StaticMeshComponent" or "CapsuleComponent") then refuse("living_authored_target_required") end
    local assassination = tag == "native-lenny" or tag == "native-spratt"
    local mesh_class = assassination and "SkeletalMeshComponent" or "StaticMeshComponent"
    if not component(target_mesh, actor, mesh_class) then target_mesh = nil end

    local context = equipped(state, phase, name, guid, tag)
    context.trace, context.target_capsule, context.target_mesh = StaticFindObject(trace_path), target_capsule, target_mesh
    context.fixed = fixed
    context.ignored = {context.pawn, context.weapon, actor}
    local rooftop = tag == "native-crossroads-jammer"
    context.floor_depth, context.aim_range, context.aim_pitch = rooftop and 1800 or 330, rooftop and 1600 or 550, rooftop and 70 or 56
    local center
    if fixed then
        center = mesh_center(target_mesh)
    else
        center = target_capsule:K2_GetComponentLocation()
    end
    local radius, height = context.capsule:GetScaledCapsuleRadius(), context.capsule:GetScaledCapsuleHalfHeight()
    local original = {point = context.pawn:K2_GetActorLocation(), actor = rotation(context.pawn:K2_GetActorRotation()),
        control = rotation(context.controller:GetControlRotation()), camera = rotation(context.camera.RelativeRotation),
        muzzle = rotation(context.muzzle.RelativeRotation)}
    if not valid(context.trace) or not vector(center) or not vector(original.point) or not original.actor or not original.control
        or not original.camera or not original.muzzle or not finite(radius) or not finite(height)
        or radius < 10 or radius > height or height > 150 then refuse("geometry_unqualified") end

    local moved = false
    local function restore_pose()
        if not same(context.pawn:GetWorld(), state.world) or not same(context.pawn.Controller, context.controller)
            or not same(context.pawn.EquipedItem, context.weapon) or not same(context.pawn.Camera, context.camera)
            or not same(context.weapon.Arrow_Muzzle, context.muzzle) then return false end
        local position = not moved or context.pawn:K2_TeleportTo(original.point, original.actor)
        context.controller:SetControlRotation(original.control)
        context.camera:K2_SetRelativeRotation(original.camera, false, {}, true)
        context.muzzle:K2_SetRelativeRotation(original.muzzle, false, {}, true)
        context.pawn:ForceNetUpdate()
        if not position or select(2, look(original.point, context.pawn:K2_GetActorLocation())) > 20 then return false end
        return not temporary or rotation_matches(context.pawn:K2_GetActorRotation(), original.actor)
            and rotation_matches(context.controller:GetControlRotation(), original.control)
            and rotation_matches(context.camera.RelativeRotation, original.camera)
            and rotation_matches(context.muzzle.RelativeRotation, original.muzzle)
    end
    local checked, distance, hit = pcall(function()
        local current_distance, exact_hit = align(context, center)
        if exact_hit then return current_distance, exact_hit end
        local bearing = math.atan(original.point.Y - center.Y, original.point.X - center.X)
        for _, range in ipairs(rooftop and {175, 245, 320, 410, 540, 670, 800, 1000, 1220} or {175, 245, 320, 410}) do
            for _, offset in ipairs({0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180}) do
                local direction = bearing + math.rad(offset)
                local point = floor_at(context, center, center.X + math.cos(direction) * range,
                    center.Y + math.sin(direction) * range, radius, height)
                if point then
                    local view = look(point, center)
                    if context.pawn:K2_TeleportTo(point, {Pitch = 0, Yaw = view.Yaw, Roll = 0}) then
                        moved = true
                        local actual = context.pawn:K2_GetActorLocation()
                        if vector(actual) and select(2, look(point, actual)) <= 20 then
                            local selected, contact = align(context, center)
                            if contact then return selected, contact end
                        end
                    end
                end
            end
        end
    end)
    if not checked or not hit then
        local restored, exact = pcall(restore_pose)
        if not restored or not exact then error("mutation_unverified_stock_flood_jammer_aim_pose", 0) end
        refuse(checked and "no_stock_sight_line" or "geometry_call_failed")
    end
    if not pcall(function() context.pawn:ForceNetUpdate() end) then error("mutation_unverified_stock_flood_jammer_aim_net_update", 0) end
    last = {world = state.world, map = state.map, pawn = name, target = actor:GetFullName(), tag = tag, guid = guid,
        distance = distance, component = hit}
    if temporary then
        return function()
            if not restore_pose() then error("mutation_unverified_stock_spratt_shot_pose_restore", 0) end
        end
    end
    return "verified stock line of fire to the authored target"
end

function fixture.aim(state, phase, name, guid, profile)
    local host = state.local_pawn and state.local_pawn.pawn
    if not valid(host) or type(SilverfishReadFloodJammer) ~= "function"
        or SilverfishReadFloodJammer(host:GetAddress()) ~= "watching" then refuse("exact_native_watch_required") end
    local actor = StaticFindObject(profile.jammer_path)
    if not valid(actor) or actor:GetFullName() ~= profile.jammer_name then refuse("living_authored_jammer_required") end
    return fixture.aim_target(state, phase, name, guid, profile.tag, actor, actor.Capsule, actor.StaticMesh)
end

function fixture.row(state, phase, status_row)
    local proof = last
    if not proof then return end
    if phase ~= "hosting" or not same(state.world, proof.world) then
        last = nil
        return
    end
    return status_row(proof.tag .. "-aim", proof.map, proof.pawn, proof.target, proof.guid, proof.distance, proof.component)
end

return fixture
