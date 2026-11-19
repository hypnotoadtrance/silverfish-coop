local input = {}
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local sources = { ["city-copper-wire"] = true, ["city-paladin-brandy"] = true }
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"

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

local function points(box, center)
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
    return result
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
    if not headless or (not guest and not (host and host_fixtures)) or not state.listening or state.map ~= city
        or profile.map ~= city or not sources[profile.source] or type(inspect) ~= "function" or type(press) ~= "function" then
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
        for _, point in ipairs(points(box, center)) do
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
        error("authored_pickup_stock_input_focus_unavailable:"
            .. (valid(focused) and focused:GetFullName() or "-"), 0)
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
