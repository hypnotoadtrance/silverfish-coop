-- Private observation of the authored Crossroads C_33 stock field.
local probe = {}
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_FUEL_GROUND_PROBE") ~= "1" then return probe end

local area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local source_path = area .. ".CrossRoads_P:PersistentLevel.Prop_FeulDrum_C_33"
local stock_class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Molotov.Fire_Molotov_C"
local stock_particle = "NiagaraSystem /Game/Assets/PFX/NI_FireMolotov.NI_FireMolotov"
local scope, host, guest_origin, guest_created

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function distance_squared(a, b) return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2 end
local function xyz(v) return ("%.3f,%.3f,%.3f"):format(v.X, v.Y, v.Z) end
local function angles(v) return ("%.3f,%.3f,%.3f"):format(v.Pitch, v.Yaw, v.Roll) end
local function clock(world)
    return StaticFindObject("/Script/Engine.Default__GameplayStatics"):GetTimeSeconds(world)
end

function probe.created(actor, class, seat)
    if class ~= stock_class or not same(actor:GetWorld(), scope) then return end
    local origin = actor:K2_GetActorLocation()
    if seat == "client" then
        if guest_origin and distance_squared(origin, guest_origin) < 100^2 then
            guest_created = guest_created + 1
        end
        return
    end
    if host or not actor:HasAuthority() or actor:GetLocalRole() ~= 3
        or actor.Size ~= 8 or actor.BurnTime < 8 or actor.BurnTime > 13 then return end
    local drum = StaticFindObject(source_path)
    if not valid(drum) or not same(drum:GetWorld(), scope) or drum.Health > 0
        or distance_squared(origin, drum:K2_GetActorLocation()) >= 9 then return end
    host = {actor = actor, path = actor:GetFullName(), started = clock(scope), origin = xyz(origin),
        burn = actor.BurnTime, max_spheres = actor.MaxSpheres, next_sample = 0, peak = 0, peak_spheres = 0,
        snapshots = {}}
end

function probe.ended(actor, destroyed)
    if host and same(actor, host.actor) then
        host.finished = clock(scope) - host.started
        host.destroyed = destroyed
        host.actor = nil
    end
end

local function sample(now)
    local actor, elapsed = host.actor, now - host.started
    local particles, spheres = actor.Particles, actor.Spheres
    local count, sphere_count = particles:GetArrayNum(), spheres:GetArrayNum()
    if count < host.peak and not host.first_fizzle then host.first_fizzle = {elapsed, count, sphere_count} end
    if count <= host.peak and sphere_count <= host.peak_spheres then return end
    host.peak, host.peak_spheres = math.max(count, host.peak), math.max(sphere_count, host.peak_spheres)
    host.peak_age = elapsed
    host.snapshots = {}
    for index = 1, math.min(count, sphere_count, 64) do
        local particle, sphere = particles[index], spheres[index]
        if valid(particle) and same(particle:GetOwner(), actor) and valid(particle.Asset)
            and particle.Asset:GetFullName() == stock_particle and valid(sphere) and same(sphere:GetOwner(), actor) then
            local collision = sphere:GetCollisionEnabled()
            host.snapshots[#host.snapshots + 1] = {index, stock_particle, xyz(particle:K2_GetComponentLocation()),
                angles(particle:K2_GetComponentRotation()), xyz(particle:K2_GetComponentScale()),
                tostring(particle:IsActive()), xyz(sphere:K2_GetComponentLocation()), sphere:GetUnscaledSphereRadius(), collision}
        end
    end
end

function probe.rows(state, phase, row)
    if state.map ~= area or not valid(state.world) then return {} end
    if not same(state.world, scope) then
        scope, host, guest_origin, guest_created = state.world, nil, nil, 0
    end
    if phase == "connected" then
        local drum = not guest_origin and StaticFindObject(source_path)
        if valid(drum) and same(drum:GetWorld(), scope) then guest_origin = drum:K2_GetActorLocation() end
        return {row("fixture-drum-ground-fire", "guest", tostring(guest_origin ~= nil), guest_created)}
    end
    if not host then return {} end
    if valid(host.actor) then
        local now = clock(scope)
        if now >= host.next_sample then
            host.next_sample = now + .10
            sample(now)
        end
    end
    local out = {row("fixture-drum-ground-fire", "host", source_path, host.path, host.origin, host.burn, host.max_spheres,
        host.peak, host.peak_spheres, #host.snapshots, host.peak_age or "-", host.finished or "-", tostring(host.destroyed))}
    if host.first_fizzle then out[#out + 1] = row("fixture-drum-ground-fire", "fizzle", table.unpack(host.first_fizzle)) end
    for _, part in ipairs(host.snapshots) do out[#out + 1] = row("fixture-drum-ground-fire", "particle", table.unpack(part)) end
    return out
end

return probe
