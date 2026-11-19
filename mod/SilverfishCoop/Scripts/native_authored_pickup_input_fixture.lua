local input = {}
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local sources = {
    ["city-copper-wire"] = city,
    ["city-paladin-brandy"] = city,
    ["scientist-blank-data-tape"] = scientist,
    ["crossroads-pocket-watch"] = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P",
    ["savlo-estate-evil-eye-ring"] = "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P",
    ["borough-mre"] = "/Game/Classes/Maps/Maps_SafeZones/Borough",
    ["railyard-dust-filter"] = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P",
    ["northeast-rail-molasses"] = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P",
    ["trench-savlo-keycard"] = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P",
}
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local dust_filter = "railyard-dust-filter"
local trace_color = {R = 0, G = 0, B = 0, A = 0}

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function vector(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then return value end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
    end
end

local function owned(component, owner, class)
    return valid(component) and same(component:GetOwner(), owner)
        and same(component:GetClass(), StaticFindObject("/Script/Engine." .. class))
end

local function aim(eye, point)
    local x, y, z = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
    local horizontal = math.sqrt(x ^ 2 + y ^ 2)
    if horizontal < 1 then return end
    return { Pitch = math.deg(math.atan(z, horizontal)), Yaw = math.deg(math.atan(y, x)), Roll = 0 }
end

local function points(box, center, tall_aim)
    local result = { center }
    local extent = vector(box:GetScaledBoxExtent())
    if not extent then return result end
    for _, axis in ipairs({ { box:GetUpVector(), extent.Z }, { box:GetRightVector(), extent.Y } }) do
        local direction, radius = vector(axis[1]), axis[2]
        if direction and radius > 0 and radius <= 100 then
            for _, sign in ipairs({ 1, -1 }) do
                local offset = 0.35 * radius * sign
                result[#result + 1] = { X = center.X + direction.X * offset,
                    Y = center.Y + direction.Y * offset, Z = center.Z + direction.Z * offset }
            end
        end
    end
    if tall_aim and extent.Z > 0 and extent.Z <= 100 then
        local up = vector(box:GetUpVector())
        if up then
            local offset = 0.75 * extent.Z
            result[#result + 1] = {X = center.X + up.X * offset, Y = center.Y + up.Y * offset, Z = center.Z + up.Z * offset}
        end
    end
    return result
end

local function square(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function cast(trace, pawn, from, to, channel, complex, ignored)
    local hit = {}
    if trace:LineTraceSingle(pawn, from, to, channel, complex, ignored, 0, hit, true, trace_color, trace_color, 0) ~= true then return end
    return hit.OutHit or hit
end

local function component_name(component)
    return valid(component) and component:GetFullName() or "-"
end

local function stock_sight(trace, pawn, box, eye, point, limit, ignored)
    local distance = math.sqrt(square(eye, point))
    if distance < 1 or distance >= limit then return false, "range" end
    local function endpoint(length)
        local scale = length / distance
        return {X = eye.X + (point.X - eye.X) * scale, Y = eye.Y + (point.Y - eye.Y) * scale,
            Z = eye.Z + (point.Z - eye.Z) * scale}
    end
    -- LookTrace starts with complex Visibility and trims the ordinary Interact
    -- trace to that first contact when it is within InteractionRange.
    local visibility = cast(trace, pawn, eye, endpoint(limit), 1, true, ignored)
    local obstruction
    if visibility then
        local impact = vector(visibility.ImpactPoint)
        if not impact then return false, "visibility_geometry" end
        limit = math.min(limit, math.sqrt(square(eye, impact)))
        if limit < distance then
            obstruction = "visible:" .. component_name(visibility.Component and visibility.Component:Get())
        end
    end
    if limit < 1 then return false, obstruction or "visibility_range" end
    local hit = cast(trace, pawn, eye, endpoint(limit), 4, false, ignored)
    local component = hit and hit.Component and hit.Component:Get()
    if same(component, box) then return true end
    return false, valid(component) and "interact:" .. component_name(component) or obstruction or "interact:-"
end

function input.approach_dust_filter(state, phase, profile, pawn, actor)
    local world = state.world
    if not (headless and host_fixtures) or phase ~= "hosting" or not state.listening or state.server ~= false or profile.source ~= dust_filter
        or sources[dust_filter] ~= state.map or not same(pawn:GetWorld(), world) or not same(actor:GetWorld(), world)
        or pawn.Dead ~= false or not pawn:HasAuthority() then
        error("authored_pickup_dust_filter_fixture_scope_unavailable", 0)
    end
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local capsule, camera, box = pawn.CapsuleComponent, pawn.Camera, actor.Box
    if not valid(trace) or not owned(capsule, pawn, "CapsuleComponent") or not owned(camera, pawn, "CameraComponent")
        or not owned(box, actor, "BoxComponent") or not same(box, actor.RootComponent) then
        error("authored_pickup_dust_filter_fixture_components_unavailable", 0)
    end
    local origin, initial_eye = vector(pawn:K2_GetActorLocation()), vector(camera:K2_GetComponentLocation())
    local facing, center = rotation(pawn:K2_GetActorRotation()), vector(box:K2_GetComponentLocation())
    local half_height, limit = capsule:GetScaledCapsuleHalfHeight(), pawn.InteractionRange
    if not origin or not initial_eye or not facing or not center or not finite(half_height) or half_height < 20 or half_height > 150
        or not finite(limit) or limit <= 0 or limit > 1000 or square(initial_eye, origin) > 150^2 then
        error("authored_pickup_dust_filter_fixture_geometry_unavailable", 0)
    end
    local floor_ignored, sight_ignored = {actor}, {pawn}
    for _, detail in ipairs(state.pawns) do
        if valid(detail.pawn) then floor_ignored[#floor_ignored + 1] = detail.pawn end
    end
    if valid(pawn.EquipedItem) then sight_ignored[#sight_ignored + 1] = pawn.EquipedItem end
    local rays, blockers = points(box, center, true), {}
    local moved, candidates = false, 0
    local function clear_sight(eye)
        for _, target in ipairs(rays) do
            local clear, reason = stock_sight(trace, pawn, box, eye, target, limit, sight_ignored)
            if clear then return true end
            blockers[reason] = true
        end
        return false
    end
    local checked, placed = pcall(function()
        for _, distance in ipairs({125, 160, 195}) do
            -- The cabinet's cooked forward interaction face is world +X.
            for _, angle in ipairs({0, 45, -45, 90, -90, 135, -135, 180}) do
                local heading = math.rad(angle)
                local x, y = center.X + distance * math.cos(heading), center.Y + distance * math.sin(heading)
                local top, bottom = center.Z + 160, center.Z - 240
                local hit = cast(trace, pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom}, 0, false, floor_ignored)
                local floor, normal = hit and vector(hit.ImpactPoint), hit and vector(hit.ImpactNormal)
                if floor and normal and normal.Z >= 0.7 and floor.Z >= bottom and floor.Z < top - 1 then
                    local point = {X = x, Y = y, Z = floor.Z + half_height + 2}
                    local eye = {X = x + initial_eye.X - origin.X, Y = y + initial_eye.Y - origin.Y,
                        Z = point.Z + initial_eye.Z - origin.Z}
                    if square(point, center) <= 235^2 then
                        candidates = candidates + 1
                        if clear_sight(eye) and pawn:K2_TeleportTo(point, facing) then
                            moved = true
                            local actual, current_eye = vector(pawn:K2_GetActorLocation()), vector(camera:K2_GetComponentLocation())
                            if actual and current_eye and square(actual, point) <= 18^2 and square(actual, center) <= 240^2
                                and clear_sight(current_eye) then
                                pawn:ForceNetUpdate()
                                return true
                            end
                        end
                    end
                end
            end
        end
    end)
    if checked and placed then return "exact stock Dust Filter floor and interaction sight verified" end
    if moved then
        local returned = pawn:K2_TeleportTo(origin, facing) and vector(pawn:K2_GetActorLocation())
        if not returned or square(returned, origin) > 18^2 then
            error("mutation_unverified_authored_pickup_dust_filter_fixture_rollback", 0)
        end
        pawn:ForceNetUpdate()
    end
    if not checked then error(placed, 0) end
    local names = {}
    for reason in pairs(blockers) do names[#names + 1] = reason end
    table.sort(names)
    error(string.format("authored_pickup_dust_filter_fixture_no_clear_sight:floor=%d:%s", candidates,
        table.concat(names, "|", 1, math.min(#names, 4))), 0)
end

local function restored(camera, original)
    local current = rotation(camera.RelativeRotation)
    if not current then return false end
    for _, axis in ipairs({ "Pitch", "Yaw", "Roll" }) do
        if math.abs((current[axis] - original[axis] + 180) % 360 - 180) > 0.05 then return false end
    end
    return true
end

function input.press(state, phase, profile)
    local host = phase == "hosting" and state.server == false
    local guest = phase == "connected" and state.server == true
    local inspect, press = SilverfishReadNativeAuthoredPickup, SilverfishProbeNativeInteractInput
    if not headless or (not guest and not (host and host_fixtures)) or not state.listening or profile.map ~= state.map
        or sources[profile.source] ~= state.map or type(inspect) ~= "function" or type(press) ~= "function" then
        error("authored_pickup_stock_input_fixture_unavailable", 0)
    end
    local detail = state.local_pawn
    local pawn, controller = detail and detail.pawn, state.controller
    local actor = StaticFindObject(profile.path)
    if not valid(pawn) or not valid(controller) or not valid(actor) or type(detail.name) ~= "string"
        or pawn:GetFullName() ~= detail.name or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(actor:GetWorld(), state.world)
        or actor:GetFullName() ~= profile.name .. " " .. profile.path
        or not same(actor:GetClass(), StaticFindObject(profile.class)) or pawn.Dead ~= false then
        error("authored_pickup_stock_input_exact_pawn_or_actor_unavailable", 0)
    end
    local box, camera = actor.Box, pawn.Camera
    local active, claimed, before = inspect(profile.source)
    if active ~= true or claimed ~= false or not math.tointeger(before)
        or not owned(box, actor, "BoxComponent") or not same(box, actor.RootComponent)
        or not owned(camera, pawn, "CameraComponent") then
        error("authored_pickup_stock_input_exact_box_or_camera_unavailable", 0)
    end
    local original = rotation(camera.RelativeRotation)
    local eye, center, position = vector(camera:K2_GetComponentLocation()), vector(box:K2_GetComponentLocation()),
        vector(pawn:K2_GetActorLocation())
    if not original or not eye or not center or not position or
        (position.X - center.X) ^ 2 + (position.Y - center.Y) ^ 2 + (position.Z - center.Z) ^ 2 > 250 ^ 2 then
        error("authored_pickup_stock_input_outside_verified_reach", 0)
    end

    local ok, reason = pcall(function()
        -- LookTrace reads the Camera transform and fills the ordinary stock interaction focus.
        for _, point in ipairs(points(box, center, profile.source == dust_filter)) do
            local view = aim(eye, point)
            if view then
                camera:K2_SetWorldRotation(view, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, box) then
                    local consumed, why = press(pawn:GetAddress(), true)
                    local still_active, still_claimed, sequence, fixture = inspect(profile.source)
                    if consumed ~= true or why ~= "stock_started_consumed" or still_active ~= true
                        or still_claimed ~= false or fixture ~= false or not math.tointeger(sequence) or sequence <= before then
                        error("authored_pickup_stock_input_unverified:" .. tostring(why), 0)
                    end
                    return
                end
            end
        end
        local focused = pawn.InteractionTarget_Component
        local failure = "authored_pickup_stock_input_focus_unavailable:" .. component_name(focused)
        if profile.source == dust_filter then
            failure = failure .. string.format(":eye=(%.1f,%.1f,%.1f):box=(%.1f,%.1f,%.1f)",
                eye.X, eye.Y, eye.Z, center.X, center.Y, center.Z)
        end
        error(failure, 0)
    end)
    local recovered, match = pcall(function()
        if not owned(camera, pawn, "CameraComponent") or not same(pawn.Camera, camera)
            or not same(pawn:GetWorld(), state.world) then return false end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        pawn:LookTrace()
        return restored(camera, original)
    end)
    if not recovered or not match then error("mutation_unverified_authored_pickup_stock_camera_restore", 0) end
    if not ok then error(reason, 0) end
    return "exact stock loose pickup input consumed"
end

return input
