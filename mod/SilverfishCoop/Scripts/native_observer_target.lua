local observers = {}
local guest_save = require("guest_save")

observers.class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Observers_BP.Observers_BP_C"
observers.enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_OBSERVER_FIXTURE") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local trace_path = "/Script/Engine.Default__KismetSystemLibrary"
local clock_path = "/Script/Engine.Default__GameplayStatics"
local material_library = "/Script/Engine.Default__KismetMaterialLibrary"
local asset_path = "/Game/Assets/Materials/Mat_Observer.Mat_Observer"
local color = {R = 0, G = 0, B = 0, A = 0}
local visuals = {}
local fixture_box = os.getenv("SILVERFISH_COOP_BRIDGE")
local last_fixture_report
local active_observers

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function fraction(value)
    return type(value) == "number" and value >= 0 and value <= 1
end

local function difference(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function stock_parts(actor)
    local first, second, third = actor.Sphere1, actor.Sphere2, actor.Sphere3
    if not valid(first) or not valid(second) or not valid(third) then return end
    for _, component in ipairs({first, second, third}) do
        if not same(component:GetOwner(), actor)
            or component:GetClass():GetFullName() ~= "Class /Script/Engine.StaticMeshComponent" then return end
    end
    return first, second, third
end

local function view_material(actor)
    local first, second, third = stock_parts(actor)
    local library, source = StaticFindObject(material_library), StaticFindObject(asset_path)
    if not first or not valid(library) or not valid(source) or source:GetFullName() ~= "Material " .. asset_path then return end
    local material = library:CreateDynamicMaterialInstance(actor, source, FName("None"), 0)
    if not valid(material) then return end
    for _, component in ipairs({first, second, third}) do component:SetMaterial(0, material) end
    actor.DynamicMat = material
    return material
end

function observers.clear_visuals()
    visuals = {}
end

local function tick_visuals()
    if not next(visuals) then return end
    local clock = StaticFindObject(clock_path)
    if not valid(clock) then return end
    for address, view in pairs(visuals) do
        local actor = view.actor
        if not valid(actor) or actor:IsActorBeingDestroyed() or not valid(view.material)
            or actor:GetLocalRole() ~= 1 or not same(actor:GetWorld(), view.world) then
            visuals[address] = nil
        elseif view.opacity ~= view.target then
            local now = clock:GetTimeSeconds(view.world)
            local alpha = math.min(1, math.max(0, (now - view.clock) * 5))
            local opacity = view.opacity + (view.target - view.opacity) * alpha
            if math.abs(opacity - view.target) < .001 then opacity = view.target end
            view.material:SetScalarParameterValue(view.parameter, opacity)
            view.opacity, view.clock = opacity, now
        end
    end
end

function observers.start()
    if observers.enabled then LoopInGameThreadWithDelay(50, tick_visuals) end
end

function observers.opacity_row(actor, phase, row, position)
    local view = visuals[actor:GetAddress()]
    local material = phase == "hosting" and actor.DynamicMat or view and view.material
    if not valid(material) then return end
    local opacity = material:K2_GetScalarParameterValue(FName("Opacity"))
    local target = phase == "hosting" and actor.OpacityTarget or view and view.target
    if fraction(opacity) and fraction(target) then
        return row("native-observer-opacity", position.X, position.Y, position.Z, opacity, target)
    end
end

function observers.apply_opacity(args, state, phase, active)
    local epoch = args[4]
    if not observers.enabled or phase ~= "connected" or not state.server or state.map ~= crossroads or state.map ~= args[3]
        or not epoch or #epoch ~= 32 or not epoch:match("^[a-f0-9]+$") or #args < 5 or #args > 36 then
        error("Observer opacity is outside the current guest visit", 0)
    end
    local clock = StaticFindObject(clock_path)
    if not valid(clock) then return end
    for index = 5, #args do
        local x, y, z, opacity, target = args[index]:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z, opacity, target = tonumber(x), tonumber(y), tonumber(z), tonumber(opacity), tonumber(target)
        if x and y and z and math.abs(x) <= 1e6 and math.abs(y) <= 1e6 and math.abs(z) <= 1e6
            and fraction(opacity) and fraction(target) then
            local point, actor = {X = x, Y = y, Z = z}, nil
            for _, entry in pairs(active) do
                local candidate = entry.actor
                if entry.kind == "observer" and valid(candidate) and same(candidate:GetWorld(), state.world)
                    and candidate:GetLocalRole() == 1 and not candidate:IsActorBeingDestroyed()
                    and difference(candidate:K2_GetActorLocation(), point) < 180^2 then
                    if actor then actor = nil; break end
                    actor = candidate
                end
            end
            if actor then
                local address = actor:GetAddress()
                local view = visuals[address]
                if not view or not same(view.actor, actor) then
                    local material = view_material(actor)
                    if valid(material) then
                        view = {actor = actor, world = state.world, material = material, parameter = FName("Opacity")}
                        visuals[address] = view
                    end
                end
                if view then
                    view.material:SetScalarParameterValue(view.parameter, opacity)
                    view.opacity, view.target, view.clock = opacity, target, clock:GetTimeSeconds(state.world)
                end
            end
        end
    end
    return "Applied the host Observer opacity"
end

local function gaze_distance(actor, pawn, point, trace)
    local camera = valid(pawn) and pawn.Camera
    if not valid(camera) or not same(camera:GetOwner(), pawn) or not same(pawn:GetWorld(), actor:GetWorld()) then return end
    local position = pawn:K2_GetActorLocation()
    local player_distance = (point.X - position.X)^2 + (point.Y - position.Y)^2 + (point.Z - position.Z)^2
    if player_distance > 2000^2 then return end
    local eye, facing = camera:K2_GetComponentLocation(), camera:GetForwardVector()
    local x, y, z = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
    local length = math.sqrt(x*x + y*y + z*z)
    if length <= 1 then return end
    local dot = (x * facing.X + y * facing.Y + z * facing.Z) / length
    if dot <= math.sqrt(player_distance / (player_distance + 2500^2)) then return end
    if trace:LineTraceSingle(actor, eye, point, 0, true, {pawn}, 0, {}, true, color, color, 0) then return end
    return player_distance
end

function observers.target(active, state, phase)
    active_observers = nil
    if not observers.enabled or phase ~= "hosting" or not state.listening or state.server or state.map ~= crossroads
        or type(SilverfishSetNativeObserverTarget) ~= "function" or #(state.guests or {}) == 0 then return end
    local host = state.local_pawn and state.local_pawn.pawn
    if not valid(host) or not host:IsLocallyControlled() then return end
    local trace, clock = StaticFindObject(trace_path), StaticFindObject(clock_path)
    if not valid(trace) or not valid(clock) then return end
    active_observers = active
    local now = clock:GetTimeSeconds(state.world)
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.kind == "observer" and valid(actor) and not actor:IsActorBeingDestroyed() and actor:HasAuthority()
            and (not entry.observer_next or now >= entry.observer_next) then
            entry.observer_next = now + .25
            local point = actor:K2_GetActorLocation()
            local distance, nearest = gaze_distance(actor, host, point, trace), nil
            for _, guest in ipairs(state.guests) do
                if type(guest.health) == "number" and guest.health > 0 and guest_save.matches(guest.pawn) then
                    local result = gaze_distance(actor, guest.pawn, point, trace)
                    if result and (not distance or result < distance) then
                        distance, nearest = result, guest.name:match("^%S+ (.+)$")
                    end
                end
            end
            if nearest or entry.observer_target then
                local path = actor:GetFullName():match("^%S+ (.+)$")
                local accepted = path and SilverfishSetNativeObserverTarget(path, nearest or "")
                entry.observer_target = accepted and nearest or nil
            end
        end
    end
end

function observers.catch_offering(state, pawn, item, direction)
    if not active_observers or not state or state.map ~= crossroads or not guest_save.matches(pawn)
        or not valid(item) or not item:HasAuthority() or not same(item:GetWorld(), state.world) then return end
    local point = item:K2_GetActorLocation()
    for _, entry in pairs(active_observers) do
        local actor = entry.actor
        if entry.kind == "observer" and valid(actor) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
            and same(actor:GetWorld(), state.world) and difference(point, actor:K2_GetActorLocation()) < 8000^2 then
            actor:TryCatchItem(item, direction)
        end
    end
end

function observers.publish_rewards(state, phase)
    if not observers.enabled or phase ~= "hosting" or state.server or not state.listening or state.map ~= crossroads
        or type(SilverfishTakeNativeObserverReward) ~= "function" then return end
    if fixture_box and fixture_box:find("observer%-proof%-%x+") and type(SilverfishReadObserverOfferingFixture) == "function" then
        local calls, results = SilverfishReadObserverOfferingFixture()
        local report = tostring(calls) .. ":" .. tostring(results)
        if report ~= last_fixture_report then
            local file = io.open(fixture_box .. "/observer-native-reward", "w")
            if file then file:write(report, "\n"); file:close(); last_fixture_report = report end
        end
    end
    for _ = 1, 32 do
        local path = SilverfishTakeNativeObserverReward(state.world:GetAddress())
        if not path then break end
        local actor = StaticFindObject(path)
        if valid(actor) and actor:GetFullName():match("^%S+ (.+)$") == path and same(actor:GetWorld(), state.world)
            and actor:HasAuthority() and not actor:IsActorBeingDestroyed() then
            actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 15)
            if not actor.bReplicates then actor:SetReplicates(true) end
            if not actor.bReplicateMovement then actor:SetReplicateMovement(true) end
            actor:ForceNetUpdate()
        end
    end
end

return observers
