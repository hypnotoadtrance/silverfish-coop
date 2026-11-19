local wall = {}
local zones = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE_WALLS") == "1"
local trigger_class = "/Script/Engine.TriggerBox"
local no_collision, query_only = 0, 1
local world_static, pawn_channel = 0, 2
local ignore, block = 0, 2
local range_squared = 1500 * 1500
local record
local status, failure = "inactive", ""
local observation
local retry_at = 0

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function distance_squared(first, second)
    local dx, dy, dz = first.X - second.X, first.Y - second.Y, first.Z - second.Z
    return dx * dx + dy * dy + dz * dz
end

local function capsule(pawn)
    if not valid(pawn) or not valid(pawn.CapsuleComponent) then return end
    local component = pawn.CapsuleComponent
    if component:GetCollisionObjectType() == pawn_channel and component:GetCollisionResponseToChannel(world_static) == block then
        return component
    end
end

local function set_ignore(component, actor, should_ignore)
    if valid(component) and valid(actor) then
        component:IgnoreActorWhenMoving(actor, should_ignore)
    end
end

function wall.clear()
    if record then
        pcall(set_ignore, record.host_capsule, record.actor, false)
        if valid(record.actor) then
            pcall(function() record.actor:K2_DestroyActor() end)
        end
    end
    record = nil
    observation = nil
    status, failure = "inactive", ""
end

local function create(world, fixture)
    local original = fixture.Box
    local class = StaticFindObject(trigger_class)
    if not valid(original) or not valid(class) then error("Expected the stock Box and Engine.TriggerBox", 0) end

    local center = original:K2_GetComponentLocation()
    local rotation = original:K2_GetComponentRotation()
    local extent = original:GetScaledBoxExtent()
    if not finite(extent.X) or not finite(extent.Y) or not finite(extent.Z) or extent.X < 1 or extent.Y < 1 or extent.Z < 1
        or extent.X > 10000 or extent.Y > 10000 or extent.Z > 10000 then
        error("The staged exit Box has invalid dimensions", 0)
    end
    local actor = world:SpawnActor(class, { X = center.X, Y = center.Y, Z = center.Z },
        { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll })
    if not valid(actor) then error("The local exit blocker could not spawn", 0) end

    local ok, result = pcall(function()
        local component = actor.CollisionComponent
        if not valid(component) or not same(actor.RootComponent, component) or actor.bReplicates ~= false
            or not same(actor:GetWorld(), world) then
            error("Expected a local TriggerBox with its own root shape", 0)
        end
        actor:SetActorEnableCollision(false)
        actor:SetActorHiddenInGame(true)
        component:SetCollisionEnabled(no_collision)
        component:SetGenerateOverlapEvents(false)
        component:SetBoxExtent({ X = extent.X, Y = extent.Y, Z = extent.Z }, false)
        component:SetCollisionObjectType(world_static)
        component:SetCollisionResponseToAllChannels(ignore)
        component:SetCollisionResponseToChannel(pawn_channel, block)
        component:SetCollisionEnabled(query_only)
        actor:SetActorEnableCollision(true)

        local cloned = component:GetScaledBoxExtent()
        if component:GetCollisionEnabled() ~= query_only or component:GetCollisionObjectType() ~= world_static
            or component:GetCollisionResponseToChannel(pawn_channel) ~= block or distance_squared(cloned, extent) > 0.01
            or distance_squared(component:K2_GetComponentLocation(), center) > 0.01 then
            error("The staged wall's collision geometry could not be verified", 0)
        end
        return { actor = actor, component = component, fixture = fixture, world = world }
    end)
    if not ok then
        pcall(function() actor:K2_DestroyActor() end)
        error(result, 0)
    end
    return result
end

local function host_can_pass(state, local_pawn, world, accepting)
    if not accepting or state.clients ~= 1 or not local_pawn:HasAuthority() then return false end
    local remote
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" and valid(details.pawn) then
            if remote then return false end
            remote = details.pawn
        end
    end
    if not valid(remote) or not same(remote:GetWorld(), world) or not remote:HasAuthority() then return false end
    local squared = distance_squared(local_pawn:K2_GetActorLocation(), remote:K2_GetActorLocation())
    return finite(squared) and squared <= range_squared
end

function wall.sync(state, phase, fixture, accepting)
    if not enabled then return end
    local hosting = phase == "hosting"
    local local_pawn = state.local_pawn and state.local_pawn.pawn
    if (not hosting and phase ~= "connected") or not zones[state.map] or not valid(local_pawn) or not valid(fixture) then
        wall.clear()
        return
    end
    local world = local_pawn:GetWorld()
    if not same(world, fixture:GetWorld()) or not capsule(local_pawn) then
        wall.clear()
        status, failure = "arming", "Waiting for a compatible pawn capsule and exit Box"
        return
    end
    if record and (not same(record.fixture, fixture) or not same(record.world, world) or not valid(record.actor)) then
        wall.clear()
    end

    local ok, reason = pcall(function()
        if not record then
            if os.time() < retry_at then return end
            retry_at = os.time() + 2
            record = create(world, fixture)
        end
        local desired = hosting and host_can_pass(state, local_pawn, world, accepting) and capsule(local_pawn) or nil
        if not same(record.host_capsule, desired) then
            set_ignore(record.host_capsule, record.actor, false)
            set_ignore(desired, record.actor, true)
            record.host_capsule = desired
        end
        status, failure = "ready", ""
    end)
    if not ok then
        wall.clear()
        status, failure = "error", tostring(reason)
    end
end

function wall.command(fields, state, phase)
    if fields[2] ~= "native-exit-wall-probe" then return end
    local slot = fields[3]
    if not enabled or phase ~= "hosting" or status ~= "ready" or not record or #fields ~= 3
        or (slot ~= "local" and slot ~= "guest") then
        error("Expected native-exit-wall-probe local|guest on the staged native zone host", 0)
    end
    local pawn
    for _, details in ipairs(state.pawns) do
        if details.kind == slot and valid(details.pawn) and details.pawn:HasAuthority() then
            if pawn then error("More than one pawn occupies the requested slot", 0) end
            pawn = details.pawn
        end
    end
    if not valid(pawn) or not same(pawn:GetWorld(), record.world) or not capsule(pawn) then
        error("The authoritative pawn capsule is unavailable", 0)
    end
    local center = record.component:K2_GetComponentLocation()
    local from = distance_squared(pawn:K2_GetActorLocation(), center)
    if not finite(from) or from > 1250 * 1250 or record.fixture.Box:IsOverlappingActor(pawn) then
        error("Position the pawn near the exit before the swept wall probe", 0)
    end
    local hit = {}
    pawn:K2_SetActorLocation({ X = center.X, Y = center.Y, Z = center.Z }, true, hit, false)
    pawn:ForceNetUpdate()
    local component = hit.Component and hit.Component:Get()
    local outcome = hit.bBlockingHit and (same(component, record.component) and "wall" or "other") or "clear"
    local remaining = distance_squared(pawn:K2_GetActorLocation(), center)
    observation = { slot = slot, outcome = outcome, remaining = finite(remaining) and math.sqrt(remaining) or -1 }
    return "native exit sweep " .. outcome
end

function wall.rows(status_row)
    if not enabled then return {} end
    return {status_row("native-zone-wall", status, record and valid(record.host_capsule) and 1 or 0,
        observation and observation.slot or "-", observation and observation.outcome or "-",
        observation and observation.remaining or "-", failure)}
end

return wall
