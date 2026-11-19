local fixture = {}
local color = {R=0, G=0, B=0, A=0}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < 1e8
end

local function vector(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then return value end
end

local function delta(first, second)
    return {X=first.X-second.X, Y=first.Y-second.Y, Z=first.Z-second.Z}
end

local function add(point, offset, scale)
    return {X=point.X+offset.X*scale, Y=point.Y+offset.Y*scale, Z=point.Z+offset.Z*scale}
end

local function length(value)
    return math.sqrt(value.X^2 + value.Y^2 + value.Z^2)
end

local function camera(pawn)
    local object = pawn.Camera
    if valid(object) and same(object:GetOwner(), pawn)
        and same(object:GetClass(), StaticFindObject("/Script/Engine.CameraComponent")) then
        local eye = vector(object:K2_GetComponentLocation())
        if eye then return object, eye end
    end
end

local function targets(box)
    local center, extent = vector(box:K2_GetComponentLocation()), vector(box:GetScaledBoxExtent())
    if not center or not extent then return {} end
    local results = {center}
    for _, edge in ipairs({{box:GetUpVector(), extent.Z}, {box:GetRightVector(), extent.Y}}) do
        local axis, size = vector(edge[1]), edge[2]
        if axis and size > 0 and size <= 250 then
            results[#results + 1] = add(center, axis, size * 0.5)
            results[#results + 1] = add(center, axis, -size * 0.5)
        end
    end
    return results
end

local function trace(library, pawn, from, to, channel, complex, ignored)
    local hit = {}
    if library:LineTraceSingle(pawn, from, to, channel, complex, ignored, 0, hit, true, color, color, 0) then
        return hit.OutHit or hit
    end
end

function fixture.has_sight(pawn, box, position)
    local _, current_eye = camera(pawn)
    local current = vector(pawn:K2_GetActorLocation())
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local range = pawn.InteractionRange
    if not current_eye or not current or not vector(position) or not valid(library)
        or not finite(range) or range <= 0 or range > 1000 then return false end
    local eye = add(position, delta(current_eye, current), 1)
    local ignored = {pawn}
    if valid(pawn.EquipedItem) then ignored[#ignored + 1] = pawn.EquipedItem end
    for _, point in ipairs(targets(box)) do
        local ray = delta(point, eye)
        local distance = length(ray)
        if distance >= 1 and distance < range then
            local depth = range
            local visible = trace(library, pawn, eye, add(eye, ray, range/distance), 1, true, ignored)
            if visible then
                local impact = vector(visible.ImpactPoint)
                depth = impact and math.min(depth, length(delta(impact, eye))) or 0
            end
            if depth >= 1 then
                local hit = trace(library, pawn, eye, add(eye, ray, depth/distance), 4, false, ignored)
                if hit and hit.Component and same(hit.Component:Get(), box) then return true end
            end
        end
    end
    return false
end

function fixture.press(pawn, box, probe)
    local object, eye = camera(pawn)
    local before = object and object.RelativeRotation
    if not before or not finite(before.Pitch) or not finite(before.Yaw) or not finite(before.Roll) then
        error("The exact stock merchant camera is unavailable", 0)
    end
    local original = {Pitch=before.Pitch, Yaw=before.Yaw, Roll=before.Roll}
    local checked, accepted, reason = pcall(function()
        for _, point in ipairs(targets(box)) do
            local ray = delta(point, eye)
            local horizontal = math.sqrt(ray.X^2 + ray.Y^2)
            if horizontal >= 1 then
                object:K2_SetWorldRotation({Pitch=math.deg(math.atan(ray.Z, horizontal)),
                    Yaw=math.deg(math.atan(ray.Y, ray.X)), Roll=0}, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, box) then
                    return probe(pawn:GetAddress(), true)
                end
            end
        end
        return false, "stock_looktrace_did_not_focus_merchant_box"
    end)
    local restored, verified = pcall(function()
        if not same(camera(pawn), object) then return false end
        object:K2_SetRelativeRotation(original, false, {}, true)
        pawn:LookTrace()
        local after = object.RelativeRotation
        for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
            if not after or not finite(after[axis]) or math.abs((after[axis] - original[axis] + 180) % 360 - 180) > 0.05 then
                return false
            end
        end
        return true
    end)
    if not restored or not verified then error("mutation_unverified_stock_merchant_fixture_camera_restore", 0) end
    if not checked then error(accepted, 0) end
    return accepted, reason
end

return fixture
