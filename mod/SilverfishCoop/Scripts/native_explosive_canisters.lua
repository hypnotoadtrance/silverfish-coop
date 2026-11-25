local canisters = {}
local fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and require("native_canister_fixture")

local types = {}
local function register(name, source, cue, uses_placed_motion)
    local class = "BlueprintGeneratedClass /Game/Assets/StaticMeshes/Mesh_Props/" .. name .. "." .. name .. "_C"
    types[class] = {source = source, cue = cue, uses_placed_motion = uses_placed_motion}
end
register("Prop_PropaneTank_Small", "OnFire", "canister")
register("Prop_PropaneTank_Tall", "Fireing", "canister")
register("Prop_FuelDrum", "Burning_0", "fueldrum", true)
local pending, active, removed = {}, {}, {}
local world, area

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function mesh(actor)
    local component = actor.PropMesh
    if valid(component) and same(component, actor.RootComponent) and same(component:GetOwner(), actor) then
        return component
    end
end

local function fire_components(actor)
    local particles, sound = actor.NI_FireSmall, actor.Fire01_Cue
    if valid(particles) and valid(sound) and same(particles:GetOwner(), actor) and same(sound:GetOwner(), actor) then
        return particles, sound
    end
end

local function fire_view(entry, enabled)
    local particles, sound = fire_components(entry.actor)
    if not particles then return false end
    if enabled then
        particles:Activate(false)
        sound:Activate(false)
    else
        particles:Deactivate()
        sound:Stop()
    end
    entry.lit = enabled
    return true
end

function canisters.created(actor, class, seat, is_replica)
    local spec = types[class]
    if not spec or type(SilverfishQualifyNativePlacedCanister) ~= "function" or not mesh(actor) then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if not path then return end
    local placed, guest = SilverfishQualifyNativePlacedCanister(path)
    if spec.uses_placed_motion and (not placed or not same(actor:GetOuter(), actor:GetWorld().PersistentLevel)) then return end
    if seat == "client" and not guest and not is_replica(actor) or seat == "host" and not actor:HasAuthority() then return end
    local entry = {actor = actor, path = placed and path or nil, spec = spec}
    if seat == "client" then fire_view(entry, false) end
    pending[actor:GetAddress()] = entry
end

function canisters.ended(address, destroyed)
    local entry = active[address] or pending[address]
    if entry and destroyed and entry.path and same(entry.actor:GetWorld(), world) then
        removed[entry.path] = true
    end
    active[address], pending[address] = nil, nil
end

function canisters.blast_source(position, cue)
    local matches = 0
    for _, entry in pairs(active) do
        local actor, spec = entry.actor, entry.spec
        -- The drum finishes spawning the stock explosion before its deferred destruction.
        if spec.cue == cue and valid(actor) and not actor:IsActorBeingDestroyed()
            and (actor[spec.source] == true or cue == "fueldrum" and actor.Health <= 0) then
            local component = mesh(actor)
            if component then
                local center = component:GetCenterOfMass(FName("None"))
                if (center.X - position.X)^2 + (center.Y - position.Y)^2 + (center.Z - position.Z)^2 < 9 then
                    matches = matches + 1
                    if matches > 1 then return false end
                end
            end
        end
    end
    return matches == 1
end

local function sample_lua(entry, hosting)
    local actor = entry.actor
    if not valid(actor) or actor:IsActorBeingDestroyed() then return false end
    if not entry.path then return true end
    local burning = (hosting and actor[entry.spec.source] == true) or (not hosting and entry.lit == true)
    local location = (burning or entry.sample_position) and actor:K2_GetActorLocation()
    return true, burning, actor.Health, location and location.X, location and location.Y, location and location.Z
end

function canisters.rows(state, phase, row)
    if not same(state.world, world) or state.map ~= area then
        world, area, active, removed = state.world, state.map, {}, {}
    end
    for address, entry in pairs(pending) do
        local actor = entry.actor
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if phase == "hosting" and not entry.spec.uses_placed_motion then
                actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 15)
                if not actor.bReplicates then actor:SetReplicates(true) end
                if not actor.bReplicateMovement then actor:SetReplicateMovement(true) end
                actor:ForceNetUpdate()
            end
            active[address] = entry
        end
        pending[address] = nil
    end

    local rows = {}
    local hosting = phase == "hosting"
    local native_sample = SilverfishReadNativePlacedCanister
    local world_address = type(native_sample) == "function" and valid(world) and world:GetAddress()
    for address, entry in pairs(active) do
        local alive, burning, health, x, y, z
        if entry.path and world_address then
            alive, burning, health, x, y, z = native_sample(address, world_address, hosting,
                entry.lit == true, entry.sample_position == true)
        end
        if alive == nil then alive, burning, health, x, y, z = sample_lua(entry, hosting) end
        if alive then
            if entry.path and removed[entry.path] and phase == "connected" then
                -- A stale actor channel can arrive after the direct map state.
                entry.actor:K2_DestroyActor()
            elseif entry.path then
                entry.changed = entry.changed or burning
                local particles = burning and phase == "connected" and fire_components(entry.actor)
                rows[#rows + 1] = row("native-explosive-canister", entry.path, burning and 1 or 0,
                    particles and particles:IsActive() and 1 or 0, entry.changed and 1 or 0, health,
                    x or "-", y or "-", z or "-")
            end
        else
            if entry.path then removed[entry.path] = true end
            active[address] = nil
        end
    end
    for path in pairs(removed) do
        rows[#rows + 1] = row("native-explosive-canister", path, 2, 0, 1, "-", "-", "-", "-")
    end
    return rows
end

function canisters.command(args, state, phase)
    if args[2] ~= "native-canister-apply" then
        if fixture and args[2]:match("^native%-canister%-.+%-fixture$") then
            local path = args[3]
            local actor = path and #path <= 512 and StaticFindObject(path)
            local entry = valid(actor) and active[actor:GetAddress()]
            local target = entry and entry.path == path and same(actor:GetWorld(), state.world) and actor
            local receipt = fixture.command(args, state, phase, target, nil, nil, entry and entry.spec.cue)
            if target and args[2] == "native-canister-prepare-fixture" then entry.sample_position = true end
            return receipt
        end
        return
    end
    local epoch = args[4]
    if phase ~= "connected" or state.map ~= args[3] or area ~= state.map or not same(state.world, world)
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Canister state is outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, mode = args[index]:match("^([^|]+)|([012])$")
        local actor = path and #path <= 512 and StaticFindObject(path)
        local entry = valid(actor) and active[actor:GetAddress()]
        if entry and entry.path == path and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if mode == "2" then
                removed[path] = true
                actor:K2_DestroyActor()
            elseif entry.lit ~= (mode == "1") then
                fire_view(entry, mode == "1")
            end
        end
    end
    return "Applied host canister state to stock guest props"
end

return canisters
