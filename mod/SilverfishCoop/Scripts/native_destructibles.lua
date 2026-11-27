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

local function stream_id(id)
    return id and id:match(":PersistentLevel$") and world_path.level_actor_package(id .. ".Actor") and id
end

local function actor_stream(id)
    return id and world_path.level_actor_package(id) and id:match("^(.+:PersistentLevel)%.[A-Za-z0-9_%-]+$")
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
    if not visit or not same(visit.world, state.world) or visit.map ~= state.map or visit.phase ~= phase then
        -- Discard outgoing actors on travel, when the engine owns their teardown.
        if visit and same(visit.world, state.world) then props.clear() else visit = nil end
        if type(SilverfishQualifyNativePlacedDestructible) ~= "function" then return end
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if not valid(statics) then return end
        visit = {actors = {}, addresses = {}, phase = phase, world = state.world, map = state.map, statics = statics}
    end
    local streams = world_path.visible_streams(state.world)
    if visit.streams ~= streams and not visit.pending then
        local paths = world_path.actor_paths(state.world, "destructible")
        if not paths then return end
        local scopes = {}
        for _, level in pairs(streams) do
            local id = valid(level) and stream_id(level:GetFullName():match("^Level (.+)$"))
            if id then scopes[id] = true end
        end
        visit.streams, visit.ready, visit.applied = streams, false, false
        visit.pending = {paths = paths, cursor = 1, actors = {}, addresses = {}, scopes = scopes}
    end
    return visit.ready and visit or nil
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
    local scan = current.pending
    for _ = 1, 16 do
        if scan.cursor > #scan.paths then
            if guest then
                for id, entry in pairs(current.actors) do
                    if entry.retired and not scan.actors[id] and live(entry.actor) then visible(entry, true) end
                end
            end
            current.actors, current.addresses, current.scopes = scan.actors, scan.addresses, scan.scopes
            current.pending, current.roster, current.ready = nil, nil, true
            return
        end
        local path = scan.paths[scan.cursor]
        scan.cursor = scan.cursor + 1
        local actor = StaticFindObject(path)
        local level = live(actor) and world_path.visible_actor_level(actor, current.world)
        -- Client copies of authored non-replicating actors can have a simulated role.
        if level and actor.bReplicates == false and (guest or actor:HasAuthority()) then
            local placed, remote = SilverfishQualifyNativePlacedDestructible(path, current.world:GetAddress())
            if placed and remote == guest then
                local scope = not same(level, current.world.PersistentLevel) and actor_stream(path) or nil
                local id
                if scope then
                    scan.scopes[scope] = true
                    world_path.remember_level(current.world, level)
                    id = path
                elseif same(level, current.world.PersistentLevel) then
                    id = actor_id(path:match(":PersistentLevel%.([^%.]+)$"))
                end
                if id and not scan.actors[id] then
                    local entry = current.actors[id]
                    if not entry or not same(entry.actor, actor) then
                        entry = {actor = actor, scope = scope, hidden = actor.bHidden == true,
                            collision = actor:GetActorEnableCollision(), ticking = actor:IsActorTickEnabled()}
                    end
                    scan.actors[id], scan.addresses[actor:GetAddress()] = entry, entry
                end
            end
        end
        if os.clock() - started >= 0.002 then break end
    end
end

function props.ended(address, destroyed)
    local entry = destroyed and visit and (visit.addresses[address] or visit.pending and visit.pending.addresses[address])
    if entry then
        entry.ended = true
        visit.roster = nil
    end
end

function props.rows(state, phase, row)
    local current = census(state, phase)
    if not current then return end
    if phase == "connected" then
        return row("native-destructible-view", current.map, current.applied and current.epoch or "-")
    end
    if current.roster == nil then
        local ids = {}
        for scope in pairs(current.scopes) do ids[#ids + 1] = scope end
        for id, entry in pairs(current.actors) do
            if not entry.ended and live(entry.actor) then ids[#ids + 1] = id end
        end
        table.sort(ids)
        local roster = #ids > 0 and table.concat(ids, "|") or "-"
        current.roster = #ids <= max_actors and #roster <= max_roster_bytes and roster or false
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
    local intact, scopes = {}, {}
    if #args ~= 5 or args[5] ~= "-" then
        local previous, size = "", 0
        for index = 5, #args do
            local id = args[index]
            local scope = actor_stream(id)
            if id <= previous or not (actor_id(id) or stream_id(id) or scope and scopes[scope]) then
                error("Invalid placed destructible actor", 0)
            end
            size = size + #id + 1
            if size > max_roster_bytes + 1 then error("Placed destructible roster exceeds the bridge limit", 0) end
            if stream_id(id) then scopes[id] = true else intact[id] = true end
            previous = id
        end
    end
    for id, entry in pairs(current.actors) do
        local covered = not entry.scope or scopes[entry.scope] == true
        local show = not covered or intact[id] == true
        if live(entry.actor) and (entry.retired == true) == show then
            visible(entry, show)
            if not show and entry.epoch == epoch then
                local sound = entry.actor.Sound_Break
                local pawn = state.local_pawn and state.local_pawn.pawn
                if valid(sound) and valid(pawn) then
                    current.statics:PlaySoundAtLocation(pawn, sound, entry.actor:K2_GetActorLocation(),
                        {Pitch = 0, Yaw = 0, Roll = 0}, 1, 1, 0, nil, nil, nil, nil)
                end
            end
        end
        entry.epoch = covered and epoch or nil
    end
    current.epoch, current.applied = epoch, true
    return "Applied the host's placed destructible roster"
end

return props
