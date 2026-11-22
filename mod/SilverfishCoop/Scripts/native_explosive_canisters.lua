local canisters = {}
local fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and require("native_canister_fixture")

local props = "/Game/Assets/StaticMeshes/Mesh_Props/"
local types = {}
for _, name in ipairs({"Prop_PropaneTank_Small", "Prop_PropaneTank_Tall"}) do
    types["BlueprintGeneratedClass " .. props .. name .. "." .. name .. "_C"] =
        name == "Prop_PropaneTank_Small" and "OnFire" or "Fireing"
end
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
    local source = types[class]
    if not source or type(SilverfishQualifyNativePlacedCanister) ~= "function" or not mesh(actor) then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if not path then return end
    local placed, guest = SilverfishQualifyNativePlacedCanister(path)
    if seat == "client" and not guest and not is_replica(actor) or seat == "host" and not actor:HasAuthority() then return end
    local entry = {actor = actor, path = placed and path or nil, source = source}
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

function canisters.blast_source(position)
    local matches = 0
    for _, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() and actor[entry.source] then
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

function canisters.rows(state, phase, row)
    if not same(state.world, world) or state.map ~= area then
        world, area, active, removed = state.world, state.map, {}, {}
    end
    for address, entry in pairs(pending) do
        local actor = entry.actor
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if phase == "hosting" then
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
    for address, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() then
            if entry.path and removed[entry.path] and phase == "connected" then
                -- A stale actor channel can arrive after the direct map state.
                actor:K2_DestroyActor()
            elseif entry.path then
                local burning = phase == "hosting" and actor[entry.source] == true or phase == "connected" and entry.lit == true
                entry.changed = entry.changed or burning
                local particles = burning and phase == "connected" and fire_components(actor)
                local location = (burning or entry.sample_position) and actor:K2_GetActorLocation()
                rows[#rows + 1] = row("native-explosive-canister", entry.path, burning and 1 or 0,
                    particles and particles:IsActive() and 1 or 0, entry.changed and 1 or 0, actor.Health,
                    location and location.X or "-", location and location.Y or "-", location and location.Z or "-")
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
            local receipt = fixture.command(args, state, phase, target)
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
