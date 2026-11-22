local lamps = {}

lamps.class = "BlueprintGeneratedClass /Game/Classes/Anomalies/AnglerLamp_BP.AnglerLamp_BP_C"
local observer_fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_ANGLER_LAMP_FIXTURE") == "1"
lamps.enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and (os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1" or observer_fixture)
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local material_path = "/Game/Assets/Materials/Mat_Misc/PP_Angler_Lure.PP_Angler_Lure"
local material_library = "/Script/Engine.Default__KismetMaterialLibrary"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local math_path = "/Script/Engine.Default__KismetMathLibrary"
local system_path = "/Script/Engine.Default__KismetSystemLibrary"
local black = {R = 0, G = 0, B = 0, A = 0}
local viewer

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function owned(part, actor, class)
    return valid(part) and same(part:GetOwner(), actor) and part:GetClass():GetFullName() == class
end

local function clamp(value, lowest, highest)
    return math.min(highest, math.max(lowest, value))
end

local function delta(first, second)
    return {X = first.X - second.X, Y = first.Y - second.Y, Z = first.Z - second.Z}
end

local function distance_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function light_components(actor)
    local first, second = actor.PointLight, actor.PointLight1
    local class = "Class /Script/Engine.PointLightComponent"
    if owned(first, actor, class) and owned(second, actor, class) then return first, second end
end

local function material(actor, view)
    if valid(view.material) then return view.material end
    local library, source = StaticFindObject(material_library), StaticFindObject(material_path)
    if not valid(library) or not valid(source) or source:GetFullName() ~= "Material " .. material_path then return end
    local value = library:CreateDynamicMaterialInstance(actor, source, FName("None"), 0)
    if not valid(value) then return end
    view.post.Settings.WeightedBlendables.Array[1] = {Weight = 1, Object = value}
    view.material, view.origin = value, FName("Origin")
    return value
end

local function project_vision()
    local current = viewer
    if not current or #current.lamps == 0 then return end
    if not valid(current.world) or not valid(current.pawn) or not valid(current.controller)
        or not same(current.controller.Pawn, current.pawn) or not same(current.pawn:GetWorld(), current.world) then
        lamps.clear()
        return
    end
    local camera = current.pawn.Camera
    if not owned(camera, current.pawn, "Class /Script/Engine.CameraComponent") then
        lamps.clear()
        return
    end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return end
    local clock = statics:GetTimeSeconds(current.world)
    local dt = clamp(clock - (current.clock or clock), 0, .12)
    if dt == 0 then current.clock = clock; return end
    current.clock = clock
    local location, forward = camera:K2_GetComponentLocation(), camera:GetForwardVector()
    local selected, power, target = nil, 0, nil
    for _, entry in ipairs(current.lamps) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() and actor:GetLocalRole() == 1 then
            local state = entry.lure
            local point = actor:K2_GetActorLocation()
            local direction = delta(point, location)
            local length = math.sqrt(direction.X^2 + direction.Y^2 + direction.Z^2)
            local cone = length / math.sqrt(length^2 + state.range^2)
            local dot = length > 0 and (forward.X * direction.X + forward.Y * direction.Y + forward.Z * direction.Z) / length or 0
            local visible = false
            if dot > cone then
                local trace = StaticFindObject(system_path)
                visible = valid(trace) and not trace:LineTraceSingle(actor, location, point,
                    0, true, {actor, current.pawn}, 0, {}, true, black, black, 0)
            end
            state.alpha = clamp(state.alpha + dt * (visible and .2 or -.5), 0, 1)
            local strength = math.max(clamp((dot - cone) / (1 - cone), 0, 1), state.alpha^2)
            if state.alpha > 0 and strength > power then selected, power, target = entry, strength, point end
        end
    end
    for _, entry in ipairs(current.lamps) do
        local state = entry.lure
        if valid(state.post) then
            if entry == selected then
                local value = material(entry.actor, state)
                if valid(value) then
                    value:SetVectorParameterValue(state.origin, {R = target.X, G = target.Y, B = target.Z, A = state.alpha})
                    state.post.bEnabled = true
                end
            elseif state.post.bEnabled then
                state.post.bEnabled = false
            end
        end
    end
    if not selected then current.previous_aim = nil; return end
    local math_library = StaticFindObject(math_path)
    if not valid(math_library) then return end
    local rotation = current.controller:GetControlRotation()
    local aim = math_library:FindLookAtRotation(location, target)
    local correction = math_library:NormalizedDeltaRotator(aim, rotation)
    local movement = math_library:NormalizedDeltaRotator(current.previous_aim or rotation, rotation)
    local length = math.sqrt(correction.Pitch^2 + correction.Yaw^2)
    local extra = length > 0 and math.min(math.sqrt(movement.Pitch^2 + movement.Yaw^2), length * .5) / length or 0
    local offset = {Pitch = correction.Pitch * extra, Yaw = correction.Yaw * extra, Roll = 0}
    local result = math_library:ComposeRotators(math_library:RInterpTo(rotation, aim, dt, 2 * power), offset)
    current.controller:SetControlRotation(result)
    current.previous_aim = result
end

function lamps.start()
    if lamps.enabled then LoopInGameThreadWithDelay(50, project_vision) end
end

function lamps.clear()
    if viewer then
        for _, entry in ipairs(viewer.lamps) do
            if valid(entry.lure.post) then entry.lure.post.bEnabled = false end
        end
    end
    viewer = nil
end

function lamps.project(active, state, phase)
    if not lamps.enabled or phase ~= "connected" or state.server ~= true or state.map ~= crossroads then
        if viewer then lamps.clear() end
        return
    end
    local pawn = state.local_pawn and state.local_pawn.pawn
    local controller = state.controller
    if not valid(pawn) or not valid(controller) or not controller:IsLocalController() or not same(controller.Pawn, pawn) then
        lamps.clear()
        return
    end
    if not viewer or not same(viewer.pawn, pawn) or not same(viewer.world, state.world) then
        lamps.clear()
        viewer = {world = state.world, pawn = pawn, controller = controller, lamps = {}}
    end
    local candidates = {}
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.kind == "angler-lamp" and valid(actor) and not actor:IsActorBeingDestroyed() and actor:GetLocalRole() == 1 then
            local post, range = actor.PostProcessLure, actor.VisionLock_Range
            if owned(post, actor, "Class /Script/Engine.PostProcessComponent") and type(range) == "number" and range >= 1 and range <= 4000 then
                if not entry.lure then entry.lure = {post = post, range = range, alpha = 0} end
                candidates[#candidates + 1] = entry
            end
        end
    end
    viewer.lamps = candidates
end

function lamps.color_row(actor, row)
    local first, second = light_components(actor)
    if not first then return end
    local position, a, b = actor:K2_GetActorLocation(), first:GetLightColor(), second:GetLightColor()
    return row("native-angler-colors", position.X, position.Y, position.Z, a.R, a.G, a.B, b.R, b.G, b.B)
end

function lamps.apply_colors(args, state, phase, active)
    local epoch = args[4]
    if not lamps.enabled or phase ~= "connected" or state.map ~= crossroads or state.map ~= args[3]
        or not epoch or #epoch ~= 32 or not epoch:match("^[a-f0-9]+$") or #args < 5 or #args > 36 then
        error("AnglerLamp lights are outside the current guest visit", 0)
    end
    for index = 5, #args do
        local x, y, z, ar, ag, ab, br, bg, bb = args[index]:match(
            "^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        ar, ag, ab, br, bg, bb = tonumber(ar), tonumber(ag), tonumber(ab), tonumber(br), tonumber(bg), tonumber(bb)
        local function channel(value) return value and value >= 0 and value <= 1 end
        if x and y and z and math.abs(x) <= 1e6 and math.abs(y) <= 1e6 and math.abs(z) <= 1e6
            and channel(ar) and channel(ag) and channel(ab) and channel(br) and channel(bg) and channel(bb) then
            local point, actor = {X = x, Y = y, Z = z}, nil
            for _, entry in pairs(active) do
                local candidate = entry.actor
                if entry.kind == "angler-lamp" and valid(candidate) and same(candidate:GetWorld(), state.world)
                    and candidate:GetLocalRole() == 1 and not candidate:IsActorBeingDestroyed()
                    and distance_squared(candidate:K2_GetActorLocation(), point) < 180^2 then
                    if actor then actor = nil; break end
                    actor = candidate
                end
            end
            if actor then
                local first, second = light_components(actor)
                if first then
                    first:SetLightColor({R = ar, G = ag, B = ab, A = 1}, true)
                    second:SetLightColor({R = br, G = bg, B = bb, A = 1}, true)
                end
            end
        end
    end
    return "Applied the host AnglerLamp colors"
end

return lamps
