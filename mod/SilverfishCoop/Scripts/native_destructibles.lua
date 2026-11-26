local props = {}
local world_path = require("world_path")
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
        return visit.ready and visit or nil
    end
    -- Discard outgoing actors on travel, when the engine owns their teardown.
    if visit and same(visit.world, state.world) then props.clear() else visit = nil end
    if type(SilverfishQualifyNativePlacedDestructible) ~= "function" then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local paths = valid(statics) and world_path.actor_paths(state.world, "destructible")
    if not paths then return end
    visit = {actors = {}, addresses = {}, ids = {}, phase = phase, world = state.world, map = state.map,
        statics = statics, pending = paths, cursor = 1}
end

function props.update(current_world)
    local current = visit
    if not current or not current.pending then return end
    if not same(current.world, current_world()) then
        visit = nil
        return
    end
    local started = os.clock()
    local guest = current.phase == "connected"
    for _ = 1, 16 do
        if current.cursor > #current.pending then
            table.sort(current.ids)
            current.pending, current.ready = nil, true
            return
        end
        local path = current.pending[current.cursor]
        current.cursor = current.cursor + 1
        local actor = StaticFindObject(path)
        -- Client copies of authored non-replicating actors can have a simulated role.
        if live(actor) and same(actor:GetWorld(), current.world) and actor.bReplicates == false
            and (guest or actor:HasAuthority()) then
            local placed, remote = SilverfishQualifyNativePlacedDestructible(path)
            if placed and remote == guest then
                local id = path:match(":PersistentLevel%.([^%.]+)$")
                -- Publish only after the entire roster is qualified.
                if not actor_id(id) or current.actors[id] or #current.ids == max_actors then
                    current.pending = nil
                    return
                end
                local entry = {actor = actor, hidden = actor.bHidden == true,
                    collision = actor:GetActorEnableCollision(), ticking = actor:IsActorTickEnabled()}
                current.actors[id], current.addresses[actor:GetAddress()] = entry, entry
                current.ids[#current.ids + 1] = id
            end
        end
        if os.clock() - started >= 0.002 then break end
    end
end

function props.ended(address, destroyed)
    local entry = destroyed and visit and visit.addresses[address]
    if entry then
        entry.ended = true
        visit.roster = nil
    end
end

function props.rows(state, phase, row)
    local current = census(state, phase)
    if not current then return end
    if phase == "connected" then return row("native-destructible-view", current.map, current.epoch or "-") end
    if current.roster == nil then
        local ids = {}
        for _, id in ipairs(current.ids) do
            local entry = current.actors[id]
            if not entry.ended and live(entry.actor) then ids[#ids + 1] = id end
        end
        local roster = #ids > 0 and table.concat(ids, "|") or "-"
        current.roster = #roster <= max_roster_bytes and roster or false
    end
    if current.roster then return row("native-destructible-roster", current.map, current.roster) end
end

function props.command(args, state, phase)
    if args[2] ~= "native-destructible-apply" then return end
    local epoch = args[4]
    if #args < 5 or #args > 4 + max_actors or phase ~= "connected" or args[3] ~= state.map
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") then
        error("Invalid placed destructible roster", 0)
    end
    local current = census(state, phase)
    if not current then error("Placed destructibles are not ready", 0) end
    local intact = {}
    if #args ~= 5 or args[5] ~= "-" then
        local previous, size = "", 0
        for index = 5, #args do
            local id = args[index]
            if not actor_id(id) or id <= previous then error("Invalid placed destructible actor", 0) end
            size = size + #id + 1
            if size > max_roster_bytes + 1 then error("Placed destructible roster exceeds the bridge limit", 0) end
            intact[id], previous = true, id
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
