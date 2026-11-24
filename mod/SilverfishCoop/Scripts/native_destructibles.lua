local props = {}
local class_path = "/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP.Static_DestructableObject_Master_BP_C"
local max_actors, max_roster_bytes = 512, 30 * 1024
local visit

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function live(actor)
    return valid(actor) and not actor:IsActorBeingDestroyed()
end

local function actor_id(id)
    return id and #id <= 96 and id:match("^[A-Za-z0-9_]+$")
end

local function visible(entry, show)
    local actor = entry.actor
    actor:SetActorHiddenInGame(not show or entry.hidden)
    actor:SetActorEnableCollision(show and entry.collision)
    actor:SetActorTickEnabled(show and entry.ticking)
    entry.retired = not show
end

function props.clear()
    if visit and visit.phase == "connected" and valid(visit.world) then
        for _, entry in pairs(visit.actors) do
            if entry.retired and live(entry.actor) then visible(entry, true) end
        end
    end
    visit = nil
end

local function census(state, phase)
    if visit and same(visit.world, state.world) and visit.map == state.map and visit.phase == phase then
        return visit
    end
    -- Discard outgoing actors on travel, when the engine owns their teardown.
    if visit and same(visit.world, state.world) then props.clear() else visit = nil end
    if type(SilverfishQualifyNativePlacedDestructible) ~= "function" then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local class = StaticFindObject(class_path)
    if not valid(statics) or not valid(class) then return end
    local output = {}
    statics:GetAllActorsOfClass(state.world, class, output)
    local actors, ids = {}, {}
    for _, actor in ipairs(output.OutActors or output) do
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        -- Client copies of authored non-replicating actors can have a simulated role.
        if live(actor) and actor.bReplicates == false and (phase == "connected" or actor:HasAuthority()) then
            local path = actor:GetFullName():match("^%S+ (.+)$")
            local placed, guest
            if path then placed, guest = SilverfishQualifyNativePlacedDestructible(path) end
            if placed and guest == (phase == "connected") then
                local id = path:match(":PersistentLevel%.([^%.]+)$")
                -- A truncated or ambiguous census must never retire guest obstacles.
                if not actor_id(id) or actors[id] or #ids == max_actors then return end
                actors[id] = {actor = actor, hidden = actor.bHidden == true,
                    collision = actor:GetActorEnableCollision(), ticking = actor:IsActorTickEnabled()}
                ids[#ids + 1] = id
            end
        end
    end
    table.sort(ids)
    visit = {actors = actors, ids = ids, phase = phase, world = state.world, map = state.map, statics = statics}
    return visit
end

function props.rows(state, phase, row)
    local current = census(state, phase)
    if not current then return end
    if phase == "connected" then return row("native-destructible-view", current.map, current.epoch or "-") end
    local ids = {}
    for _, id in ipairs(current.ids) do
        if live(current.actors[id].actor) then ids[#ids + 1] = id end
    end
    local roster = #ids > 0 and table.concat(ids, "|") or "-"
    if #roster <= max_roster_bytes then return row("native-destructible-roster", current.map, roster) end
end

function props.command(args, state, phase)
    if args[2] ~= "native-destructible-apply" then return end
    local epoch, roster = args[4], args[5]
    if #args ~= 5 or phase ~= "connected" or args[3] ~= state.map or not epoch or #epoch ~= 32
        or not epoch:match("^[0-9a-f]+$") or not roster or #roster > max_roster_bytes then
        error("Invalid placed destructible roster", 0)
    end
    local current = census(state, phase)
    if not current then error("Placed destructibles are not ready", 0) end
    local intact = {}
    if roster ~= "-" then
        if roster == "" or roster:match("^|") or roster:match("|$") or roster:find("||", 1, true) then
            error("Invalid placed destructible actor list", 0)
        end
        local previous, count = "", 0
        for id in roster:gmatch("[^|]+") do
            if not actor_id(id) or id <= previous or count == max_actors then
                error("Invalid placed destructible actor", 0)
            end
            intact[id], previous, count = true, id, count + 1
        end
    end
    for _, id in ipairs(current.ids) do
        local entry = current.actors[id]
        local show = intact[id] == true
        if live(entry.actor) and (entry.retired == true) == show then
            visible(entry, show)
            if not show and current.epoch == epoch then
                local sound = entry.actor.Sound_Break
                local pawn = state.local_pawn and state.local_pawn.pawn
                if valid(sound) and valid(pawn) then
                    current.statics:PlaySoundAtLocation(pawn, sound, entry.actor:K2_GetActorLocation(),
                        {Pitch = 0, Yaw = 0, Roll = 0}, 1, 1, 0, nil, nil, nil, nil)
                end
            end
        end
    end
    current.epoch = epoch
    return "Applied the host's placed destructible roster"
end

return props
