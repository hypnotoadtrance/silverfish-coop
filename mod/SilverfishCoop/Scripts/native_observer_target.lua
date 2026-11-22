local observers = {}
local guest_save = require("guest_save")

observers.class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Observers_BP.Observers_BP_C"
observers.enabled = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_OBSERVER_FIXTURE") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local trace_path = "/Script/Engine.Default__KismetSystemLibrary"
local clock_path = "/Script/Engine.Default__GameplayStatics"
local color = {R = 0, G = 0, B = 0, A = 0}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
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
    if not observers.enabled or phase ~= "hosting" or not state.listening or state.server or state.map ~= crossroads
        or type(SilverfishSetNativeObserverTarget) ~= "function" or #(state.guests or {}) == 0 then return end
    local host = state.local_pawn and state.local_pawn.pawn
    if not valid(host) or not host:IsLocallyControlled() then return end
    local trace, clock = StaticFindObject(trace_path), StaticFindObject(clock_path)
    if not valid(trace) or not valid(clock) then return end
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

return observers
