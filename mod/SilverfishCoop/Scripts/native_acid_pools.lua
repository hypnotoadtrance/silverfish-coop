local pools = {}
local max_pools = 64
local visit

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function live(actor) return valid(actor) and not actor:IsActorBeingDestroyed() end

local function pool_id(id)
    return id and #id <= 96 and id:match("^[A-Za-z0-9_]+$")
end

local function stock_phase(actor)
    if actor.FizzledOut == true then return "e" end
    local timeline = actor.FizzleOut_Mat
    if valid(timeline) and timeline:IsPlaying() then return "f" end
    return "a"
end

function pools.clear()
    if visit and visit.guest and valid(visit.world) then
        for _, entry in pairs(visit.entries) do
            local actor = entry.actor
            if entry.retired and live(actor) and same(actor:GetWorld(), visit.world) then
                actor:SetActorHiddenInGame(entry.hidden)
                actor:SetActorEnableCollision(entry.collision)
                local sound = actor.Sizzle_SFX
                if entry.playing and valid(sound) and same(sound:GetOwner(), actor) then sound:Play(0) end
            end
        end
    end
    visit = nil
end

local function census(state, phase)
    if visit and same(visit.world, state.world) and visit.area == state.map and visit.phase == phase then return visit end
    if visit and same(visit.world, state.world) then pools.clear() else visit = nil end
    if type(SilverfishNativeAuthoredAcidPool) ~= "function" or not valid(state.world) then return end
    local guest = phase == "connected"
    local entries, ids, addresses = {}, {}, {}
    local complete = true
    for _, actor in ipairs(FindAllOf("AcidPool_BP_C") or {}) do
        if live(actor) and same(actor:GetWorld(), state.world) then
            local path = actor:GetFullName():match("^AcidPool_BP_C (.+)$")
            local authored, remote
            if path then authored, remote = SilverfishNativeAuthoredAcidPool(path, state.world:GetAddress()) end
            if authored and remote == guest and (guest or actor:HasAuthority()) then
                local id = path:match(":PersistentLevel%.([^%.]+)$")
                if not pool_id(id) or entries[id] or #ids >= max_pools then complete = false; break end
                local entry = {actor = actor, path = path}
                entries[id], addresses[actor:GetAddress()] = entry, entry
                ids[#ids + 1] = id
            end
        end
    end
    table.sort(ids)
    visit = {world = state.world, area = state.map, phase = phase, guest = guest, entries = entries,
        ids = ids, addresses = addresses, complete = complete}
    return visit
end

function pools.ended(address, destroyed)
    if not destroyed or not visit then return end
    local entry = visit.addresses[address]
    if entry then entry.ended = true end
end

function pools.rows(state, phase, row)
    local current = census(state, phase)
    if not current or not current.complete or #current.ids == 0 then return end
    if current.guest then
        return row("native-acid-pool-view", current.area, current.epoch or "-")
    else
        local states = {}
        for _, id in ipairs(current.ids) do
            local entry = current.entries[id]
            if not entry.ended and live(entry.actor) then states[#states + 1] = id .. ":" .. stock_phase(entry.actor) end
        end
        return row("native-acid-pool-roster", current.area, #states > 0 and table.concat(states, "|") or "-")
    end
end

local function retire(entry)
    if entry.retired then return end
    local actor = entry.actor
    local sound = actor.Sizzle_SFX
    entry.hidden, entry.collision = actor.bHidden == true, actor:GetActorEnableCollision()
    if valid(sound) and same(sound:GetOwner(), actor) then
        entry.playing = sound:IsPlaying()
        sound:Stop()
    end
    actor:SetActorHiddenInGame(true)
    actor:SetActorEnableCollision(false)
    if actor.bHidden ~= true or actor:GetActorEnableCollision() ~= false then
        error("The stock acid pool could not retire locally", 0)
    end
    entry.retired = true
end

function pools.command(args, state, phase)
    if args[2] ~= "native-acid-pools-apply" then return end
    local epoch = args[4]
    if phase ~= "connected" or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$")
        or #args < 5 or #args > 4 + max_pools then error("Acid pools are outside the current guest visit", 0) end
    local current = census(state, phase)
    if not current or not current.complete or not current.guest or #current.ids == 0 then
        error("Authored acid pools are not ready", 0)
    end

    local desired = {}
    if #args ~= 5 or args[5] ~= "-" then
        local previous = ""
        for index = 5, #args do
            local id, target = args[index]:match("^([A-Za-z0-9_]+):([afe])$")
            if not pool_id(id) or id <= previous then error("Invalid authored acid pool state", 0) end
            desired[id], previous = target, id
        end
    end

    for _, id in ipairs(current.ids) do
        local entry = current.entries[id]
        local actor, target = entry.actor, desired[id]
        if not entry.ended and live(actor) then
            if not target then
                retire(entry)
            elseif not entry.retired and target ~= "a" then
                local observed = stock_phase(actor)
                if observed == "a" or observed == "f" and target == "e" then
                    if not SilverfishNativeAuthoredAcidPool(entry.path, state.world:GetAddress(), target) then
                        error("The stock acid pool transition refused", 0)
                    end
                    local new_phase = live(actor) and stock_phase(actor)
                    if new_phase and new_phase ~= target and new_phase ~= "e" then
                        error("The stock acid pool transition did not verify", 0)
                    end
                end
            end
        end
    end
    current.epoch = epoch
    return "Applied the host's authored acid pool roster"
end

return pools
