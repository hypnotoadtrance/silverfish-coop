local fields = {}
local effects = require("native_anomaly_effects")

local classes = {
    starburst = "/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C",
    burner = "/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C",
    vortex = "/Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C",
}
local kinds = {}
for kind, path in pairs(classes) do kinds["BlueprintGeneratedClass " .. path] = kind end
local fire_jet = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Jet_BP.Fire_Jet_BP_C"
local overlap = "BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local pending, active, pending_fires, fires, fixtures = {}, {}, {}, {}, {}
local world, world_map, fixture_kind

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function in_world(actor, current)
    return valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), current)
end

local function replica(actor)
    if actor:GetLocalRole() ~= 1 then return false end
    local current = actor:GetWorld()
    local driver = valid(current) and current.NetDriver
    local server = valid(driver) and driver.ServerConnection
    return valid(server) and same(driver.World, current) and same(server.Driver, driver)
end

local function placement(actor)
    if type(SilverfishQualifyNativePlacedTrap) ~= "function" then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if path then
        local placed, guest = SilverfishQualifyNativePlacedTrap(path)
        if placed then return path, guest end
    end
end

local function vortex_sensor(actor)
    local sensor = actor.Sphere_Hit
    if valid(sensor) and same(sensor:GetOwner(), actor)
        and sensor:GetClass():GetFullName() == "Class /Script/Engine.SphereComponent" then return sensor end
end

local function animate_vortex(point)
    local timeline
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.kind == "vortex" and in_world(actor, world) and not actor:GetActorEnableCollision()
            and valid(actor.Sphere1) and same(actor.Sphere1:GetOwner(), actor) and valid(actor.Anim)
            and same(actor.Anim:GetOwner(), actor) then
            local at = actor.Sphere1:K2_GetComponentLocation()
            if (at.X - point.X)^2 + (at.Y - point.Y)^2 + (at.Z - point.Z)^2 < 9 then
                if timeline then return false end
                timeline = actor.Anim
            end
        end
    end
    if not timeline then return false end
    timeline:PlayFromStart()
    return timeline:IsPlaying()
end

local function project_placed_sensors(args, state, phase)
    local visit = args[4]
    if phase ~= "connected" or state.map ~= args[3] or not same(state.world, world)
        or not visit or #visit ~= 32 or not visit:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Vortex sensors are outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, x, y, z = args[index]:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local actor = path and #path <= 512 and StaticFindObject(path)
        local entry = in_world(actor, world) and active[actor:GetAddress()]
        if entry and entry.placed == path and entry.kind == "vortex" and not actor:GetActorEnableCollision()
            and x and y and z and math.abs(x) <= 4096 and math.abs(y) <= 4096 and math.abs(z) <= 4096 then
            local sensor = vortex_sensor(actor)
            if sensor then sensor:K2_SetRelativeLocation({X = x, Y = y, Z = z}, false, {}, true) end
        end
    end
    return "Applied host placements to the passive Vortex sensors"
end

local function burner_source(actor)
    local point, source = actor:K2_GetActorLocation(), nil
    local duration = actor.BurnTime
    if type(duration) ~= "number" or not (duration > 0 and duration <= 30) then return end
    for _, entry in pairs(active) do
        if entry.kind == "burner" and in_world(entry.actor, world) then
            -- The stock trap spawns the jet 15.5 units above the trigger's captured hit point.
            local start = entry.actor.SpawnLocation
            if start and (point.X - start.X)^2 + (point.Y - start.Y)^2 + (point.Z - start.Z - 15.5)^2 <= .01 then
                if source then return end
                source = entry.actor
            end
        end
    end
    return source
end

function fields.start(role)
    if type(SilverfishSetNativeAnomalyGuest) ~= "function" or type(RegisterBeginPlayPreHook) ~= "function"
        or type(RegisterBeginPlayPostHook) ~= "function" then return end
    RegisterBeginPlayPreHook(function(context)
        local seat = role()
        if seat ~= "host" and seat ~= "client" then return end
        local actor = context:get()
        if not valid(actor) then return end
        local class = actor:GetClass():GetFullName()
        if seat == "host" then
            if class == effects.blast_class and in_world(actor, world) then effects.observe_blast(actor, active) end
        elseif kinds[class] or class == fire_jet then
            local _, local_copy = placement(actor)
            if local_copy or replica(actor) then actor:SetActorEnableCollision(false) end
        end
    end)
    RegisterBeginPlayPostHook(function(context)
        local seat = role()
        if seat ~= "host" and seat ~= "client" then return end
        local actor = context:get()
        if not valid(actor) or actor:IsActorBeingDestroyed() then return end
        local class = actor:GetClass():GetFullName()
        local kind = kinds[class]
        if not kind and class ~= fire_jet then return end
        local placed, local_copy
        if kind then placed, local_copy = placement(actor) end
        if seat == "host" and not actor:HasAuthority()
            or seat == "client" and not local_copy and not replica(actor) then return end
        if seat == "client" then actor:SetActorEnableCollision(false) end
        local address = actor:GetAddress()
        if class == fire_jet then
            if seat == "client" or in_world(actor, world) and burner_source(actor) then pending_fires[address] = actor end
        else
            pending[address] = {actor = actor, kind = kind, placed = placed}
            if seat == "host" and fixture_kind == kind then fixtures[kind] = actor end
        end
    end)
end

function fields.guest(enabled)
    if type(SilverfishSetNativeAnomalyGuest) == "function" then SilverfishSetNativeAnomalyGuest(enabled) end
end

function fields.command(args, state, phase)
    if args[2] == "native-vortex-apply-sensors" then return project_placed_sensors(args, state, phase) end
    local effect = effects.command(args, state, phase, animate_vortex)
    if effect ~= nil then return effect end
    if args[2] == "native-anomaly-trigger-fixture" or args[2] == "native-placed-anomaly-guest-fixture" then
        local guest_probe = args[2] == "native-placed-anomaly-guest-fixture"
        local trap = fixtures[args[3]]
        local kind = trap and args[3]
        if not trap then
            for _, entry in pairs(active) do
                if entry.placed == args[3] then trap, kind = entry.actor, entry.kind; break end
            end
        end
        local trigger = in_world(trap, state.world) and (kind == "vortex" and vortex_sensor(trap) or trap.Sphere)
        if #args ~= 3 or not headless or not valid(trigger) then
            error("Trap trigger probe unavailable", 0)
        end
        if guest_probe then
            local pawn = state.local_pawn and state.local_pawn.pawn
            if phase ~= "connected" or not in_world(pawn, state.world) or not pawn:IsLocallyControlled()
                or not valid(pawn.CapsuleComponent) or trap:GetActorEnableCollision() ~= false then
                error("Placed trap guest overlap probe unavailable", 0)
            end
            trap[overlap](trap, trigger, pawn, pawn.CapsuleComponent, 0, false, {})
            return "Stock placed trap guest overlap invoked"
        end
        local guest = #state.guests == 1 and state.guests[1].pawn
        if not host_fixture or phase ~= "hosting" or not in_world(guest, state.world)
            or not guest:HasAuthority() or guest:IsLocallyControlled() then error("Trap trigger probe unavailable", 0) end
        local center = trigger:K2_GetComponentLocation()
        if not guest:K2_TeleportTo({X = center.X, Y = center.Y, Z = center.Z + 45}, guest:K2_GetActorRotation()) then
            error("The stock guest could not enter the trap trigger", 0)
        end
        return "The stock guest entered the trap trigger"
    end
    if args[2] ~= "native-anomaly-field-fixture" then return end
    local path = classes[args[3]]
    if #args ~= 3 or not path or state.map ~= crossroads or not valid(state.world) or not headless
        or (phase ~= "connected" and (phase ~= "hosting" or not host_fixture)) then
        error("The stock trap probe requires a muted Crossroads session", 0)
    end
    local class = StaticFindObject("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C")
    local kind = StaticFindObject(path)
    if not valid(kind) then LoadAsset(path); kind = StaticFindObject(path) end
    local anchor = StaticFindObject(crossroads .. ".CrossRoads_P:PersistentLevel.AnomalyField_BP_C_"
        .. (args[3] == "burner" and "9" or "6"))
    if not valid(class) or not valid(kind) or not in_world(anchor, state.world) or not same(anchor:GetClass(), class) then
        error("The Crossroads field template is unavailable", 0)
    end
    local field = state.world:SpawnActor(class, anchor:K2_GetActorLocation(), {Pitch = 0, Yaw = 0, Roll = 0})
    if not in_world(field, state.world) or field.ForceSpawn then error("The stock probe field did not initialize", 0) end
    field.Anomalies = {kind}
    field.Min_Spawn, field.Max_Spawn, field.SpawnRadius = 1, 1, 500
    if phase == "connected" then
        -- Exercise the stock Blueprint call through the same ForceSpawn branch used by placed fields.
        field.ForceSpawn = true
        field:ReceiveBeginPlay()
    else
        fixture_kind = args[3]
        local accepted, failure = pcall(function() field:SpawnField() end)
        fixture_kind = nil
        if not accepted then field:K2_DestroyActor(); error(failure, 0) end
    end
    field:K2_DestroyActor()
    return "Stock anomaly field invoked"
end

function fields.rows(state, phase, row)
    if phase ~= "hosting" and phase ~= "connected" or not valid(state.world) then return {} end
    if not same(world, state.world) or world_map ~= state.map then
        active, fires, fixtures, world, world_map = {}, {}, {}, state.world, state.map
        effects.clear()
    end
    for address, entry in pairs(pending) do
        local actor = entry.actor
        if in_world(actor, world) then
            if phase == "connected" then
                if actor:GetActorEnableCollision() == false then active[address] = entry end
            elseif entry.placed or actor:ActorHasTag(FName("Anomaly")) then
                -- SpawnField applies its stock tag after finishing the spawned actor's BeginPlay.
                actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 5)
                actor:SetReplicates(true)
                actor:SetReplicateMovement(true)
                if actor.bReplicates and actor.bReplicateMovement then
                    if entry.kind == "vortex" then
                        local sensor = vortex_sensor(actor)
                        if sensor then sensor:SetIsReplicated(true) end
                    end
                    actor:ForceNetUpdate()
                    active[address] = entry
                end
            end
        end
        pending[address] = nil
    end
    for address, fire in pairs(pending_fires) do
        if in_world(fire, world) then
            if phase == "hosting" then
                fire.NetUpdateFrequency = math.min(fire.NetUpdateFrequency, 5)
                fire:SetReplicates(true)
                fire:SetReplicateMovement(true)
                fire:ForceNetUpdate()
            end
            if phase == "hosting" and fire.bReplicates or phase == "connected" and not fire:GetActorEnableCollision() then
                fires[address] = fire
            end
        end
        pending_fires[address] = nil
    end
    local rows, live = {}, 0
    for address, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() then
            if phase == "hosting" and entry.kind == "starburst" then effects.observe_charge(entry) end
            if phase == "connected" and entry.placed and actor:GetActorEnableCollision() then
                actor:SetActorEnableCollision(false)
            end
            live = live + 1
            local position = actor:K2_GetActorLocation()
            local collision = actor:GetActorEnableCollision() and 1 or 0
            rows[#rows + 1] = row("native-anomaly-position", entry.kind, position.X, position.Y, position.Z, collision)
            if entry.placed then
                rows[#rows + 1] = row("native-placed-anomaly", entry.placed, entry.kind, position.X, position.Y, position.Z,
                    collision, actor:GetLocalRole())
            end
            if entry.kind == "vortex" then
                local sensor, mesh = vortex_sensor(actor), actor.Sphere1
                if sensor and valid(mesh) and same(mesh:GetOwner(), actor) then
                    local relative, center, visual = sensor.RelativeLocation, sensor:K2_GetComponentLocation(), mesh:K2_GetComponentLocation()
                    rows[#rows + 1] = row("native-vortex-sensor", entry.placed or "-", position.X, position.Y, position.Z,
                        relative.X, relative.Y, relative.Z, center.X, center.Y, center.Z, visual.X, visual.Y, visual.Z)
                end
            end
        else
            active[address] = nil
        end
    end
    local skipped, blocked = 0, 0
    if phase == "connected" and type(SilverfishReadNativeAnomalySkips) == "function" then
        skipped, blocked = SilverfishReadNativeAnomalySkips(world:GetAddress())
    end
    rows[#rows + 1] = row("native-anomaly-field", live, skipped, blocked or 0)
    for address, fire in pairs(fires) do
        if valid(fire) and not fire:IsActorBeingDestroyed() then
            local position, particles = fire:K2_GetActorLocation(), fire.NI_FireMassive
            rows[#rows + 1] = row("native-burner-jet", position.X, position.Y, position.Z,
                fire:GetActorEnableCollision() and 1 or 0, valid(particles) and particles:IsActive() and 1 or 0)
        else
            fires[address] = nil
        end
    end
    if phase == "hosting" then
        for _, cue in ipairs(effects.rows(row)) do rows[#rows + 1] = cue end
    else
        effects.retire_charges()
    end
    return rows
end

return fields
