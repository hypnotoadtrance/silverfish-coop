local pools = {}
local pool_class = "/Game/Classes/Anomalies/AcidPool_BP.AcidPool_BP_C"
pools.class_name = "BlueprintGeneratedClass " .. pool_class
local max_pools = 64
local visit
local spawning = false

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

local function stop_sizzle(actor)
    local sound = actor.Sizzle_SFX
    if valid(sound) and same(sound:GetOwner(), actor) then
        local playing = sound:IsPlaying()
        sound:Stop()
        return playing
    end
end

local function conceal(actor)
    actor:SetActorHiddenInGame(true)
    actor:SetActorEnableCollision(false)
    if actor.bHidden ~= true or actor:GetActorEnableCollision() ~= false then
        error("The stock acid pool could not retire locally", 0)
    end
end

function pools.clear()
    local current = visit
    visit = nil
    if not current or not current.guest or not valid(current.world) then return end
    for _, entry in pairs(current.dynamic) do
        if live(entry.actor) and same(entry.actor:GetWorld(), current.world) then entry.actor:K2_DestroyActor() end
    end
    for _, entry in pairs(current.entries) do
        local actor = entry.actor
        if entry.retired and live(actor) and same(actor:GetWorld(), current.world) then
            actor:SetActorHiddenInGame(entry.hidden)
            actor:SetActorEnableCollision(entry.collision)
            local sound = actor.Sizzle_SFX
            if entry.playing and valid(sound) and same(sound:GetOwner(), actor) then sound:Play(0) end
        end
    end
end

local function qualify(actor, world, guest)
    if not live(actor) or not same(actor:GetWorld(), world) or (not guest and not actor:HasAuthority()) then return end
    local path = actor:GetFullName():match("^AcidPool_BP_C (.+)$")
    if not path then return end
    local exact, remote = SilverfishNativeAcidPool(path, world:GetAddress())
    if exact and remote == guest then return path end
end

local function track_dynamic(current, id, actor, path)
    local entry = {actor = actor, path = path, id = id, address = actor:GetAddress()}
    current.dynamic[id], current.addresses[entry.address] = entry, entry
    return entry
end

local function add_dynamic(current, actor, path)
    local id = "spawned_" .. actor:GetFName():ToString()
    if pool_id(id) then track_dynamic(current, id, actor, path) end
end

function pools.created(actor, seat)
    if spawning or type(SilverfishNativeAcidPool) ~= "function" then return end
    local current = visit
    local guest = seat == "client"
    local world = actor:GetWorld()
    local placed = actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
    if placed or not valid(world) then return end
    local path = qualify(actor, world, guest)
    if not path then return end
    if guest then
        stop_sizzle(actor)
        conceal(actor)
    elseif current and not current.guest and same(current.world, world) then
        add_dynamic(current, actor, path)
    end
end

local function census(state, phase)
    if visit and same(visit.world, state.world) and visit.area == state.map and visit.phase == phase then return visit end
    if visit and same(visit.world, state.world) then pools.clear() else visit = nil end
    if type(SilverfishNativeAcidPool) ~= "function" or not valid(state.world) then return end
    local guest = phase == "connected"
    local current = {world = state.world, area = state.map, phase = phase, guest = guest,
        entries = {}, ids = {}, addresses = {}, dynamic = {}, complete = true}
    for _, actor in ipairs(FindAllOf("AcidPool_BP_C") or {}) do
        local path = qualify(actor, state.world, guest)
        if path then
            if actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                local id = path:match(":PersistentLevel%.([^%.]+)$")
                if not pool_id(id) or current.entries[id] or #current.ids >= max_pools then
                    current.complete = false
                    break
                end
                local entry = {actor = actor, path = path}
                current.entries[id], current.addresses[actor:GetAddress()] = entry, entry
                current.ids[#current.ids + 1] = id
            elseif guest then
                stop_sizzle(actor)
                conceal(actor)
            else
                add_dynamic(current, actor, path)
            end
        end
    end
    table.sort(current.ids)
    visit = current
    return current
end

function pools.ended(address, destroyed)
    if not destroyed or not visit then return end
    local entry = visit.addresses[address]
    if not entry then return end
    visit.addresses[address] = nil
    if not entry.id then entry.ended = true; return end
    if visit.dynamic[entry.id] == entry then
        visit.dynamic[entry.id] = nil
        if visit.guest then visit.epoch = nil end
    end
end

local function transform(actor)
    local position, angles, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
    return ("%.3f,%.3f,%.3f:%.3f,%.3f,%.3f:%.3f,%.3f,%.3f"):format(
        position.X, position.Y, position.Z, angles.Pitch, angles.Yaw, angles.Roll, scale.X, scale.Y, scale.Z)
end

function pools.rows(state, phase, row)
    local current = census(state, phase)
    if not current or not current.complete then return end
    if current.guest then return row("native-acid-pool-view", current.area, current.epoch or "-") end

    local states, dynamic_ids = {}, {}
    for _, id in ipairs(current.ids) do
        local entry = current.entries[id]
        if not entry.ended and live(entry.actor) then states[#states + 1] = id .. ":" .. stock_phase(entry.actor) end
    end
    for id, entry in pairs(current.dynamic) do
        if live(entry.actor) then
            dynamic_ids[#dynamic_ids + 1] = id
        else
            current.dynamic[id], current.addresses[entry.address] = nil, nil
        end
    end
    table.sort(dynamic_ids)
    for _, id in ipairs(dynamic_ids) do
        if #states >= max_pools then break end
        local actor = current.dynamic[id].actor
        states[#states + 1] = id .. ":" .. stock_phase(actor) .. ":" .. transform(actor)
    end
    current.published = current.published or #current.ids > 0 or #states > 0
    if current.published then return row("native-acid-pool-roster", current.area, #states > 0 and table.concat(states, "|") or "-") end
end

local function retire(entry)
    if entry.retired then return end
    local actor = entry.actor
    entry.hidden, entry.collision = actor.bHidden == true, actor:GetActorEnableCollision()
    entry.playing = stop_sizzle(actor)
    conceal(actor)
    entry.retired = true
end

local function advance(entry, phase, world)
    local actor = entry.actor
    if entry.retired or phase == "a" or not live(actor) then return end
    local observed = stock_phase(actor)
    if observed == "a" or observed == "f" and phase == "e" then
        if not SilverfishNativeAcidPool(entry.path, world:GetAddress(), phase) then
            error("The stock acid pool transition refused", 0)
        end
        local updated = live(actor) and stock_phase(actor)
        if updated and updated ~= phase and updated ~= "e" then
            error("The stock acid pool transition did not verify", 0)
        end
    end
end

local function triplet(text, min, max)
    local x, y, z = text:match("^([%-%.%d]+),([%-%.%d]+),([%-%.%d]+)$")
    x, y, z = tonumber(x), tonumber(y), tonumber(z)
    if x and y and z and x >= min and x <= max and y >= min and y <= max and z >= min and z <= max then
        return x, y, z
    end
end

local function decode(args)
    local desired = {}
    if #args == 5 and args[5] == "-" then return desired end
    local previous = ""
    for index = 5, #args do
        local id, phase, tail = args[index]:match("^([A-Za-z0-9_]+):([afe])(.*)$")
        if not pool_id(id) or id <= previous then error("Invalid stock acid pool state", 0) end
        local target = {phase = phase}
        if id:match("^spawned_") then
            local at, angles, size = tail:match("^:([^:]+):([^:]+):([^:]+)$")
            local x, y, z, pitch, yaw, roll, sx, sy, sz
            if at then
                x, y, z = triplet(at, -1e7, 1e7)
                pitch, yaw, roll = triplet(angles, -360, 360)
                sx, sy, sz = triplet(size, .001, 100)
            end
            if not x or not pitch or not sx then error("Invalid stock acid pool transform", 0) end
            target.position, target.angles, target.scale = {X=x, Y=y, Z=z}, {Pitch=pitch, Yaw=yaw, Roll=roll},
                {X=sx, Y=sy, Z=sz}
            target.pose = tail
        elseif tail ~= "" then
            error("Invalid authored acid pool state", 0)
        end
        desired[id], previous = target, id
    end
    return desired
end

local function project_dynamic(current, id, target)
    local entry = current.dynamic[id]
    if not entry or not live(entry.actor) then
        local class = StaticFindObject(pool_class)
        if not valid(class) then LoadAsset(pool_class); class = StaticFindObject(pool_class) end
        if not valid(class) then error("The stock acid pool class is unavailable", 0) end
        spawning = true
        local ok, actor = pcall(function() return current.world:SpawnActor(class, target.position, target.angles) end)
        spawning = false
        if not ok or not live(actor) then error("The stock acid pool could not appear locally", 0) end
        actor:SetActorEnableCollision(false)
        local path = qualify(actor, current.world, true)
        if not path or not valid(actor.DotVolume) or actor.DotVolume.DotActive ~= false then
            actor:K2_DestroyActor()
            error("The local acid pool was not passive", 0)
        end
        entry = track_dynamic(current, id, actor, path)
    end
    if entry.pose ~= target.pose then
        entry.actor:K2_SetActorLocationAndRotation(target.position, target.angles, false, {}, true)
        entry.actor:SetActorScale3D(target.scale)
        entry.pose = target.pose
    end
    advance(entry, target.phase, current.world)
end

function pools.command(args, state, phase)
    if args[2] ~= "native-acid-pools-apply" then return end
    local epoch = args[4]
    if phase ~= "connected" or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$")
        or #args < 5 or #args > 4 + max_pools then error("Acid pools are outside the current guest visit", 0) end
    local current = census(state, phase)
    if not current or not current.complete or not current.guest then error("Stock acid pools are not ready", 0) end

    local desired = decode(args)
    for _, id in ipairs(current.ids) do
        local entry, target = current.entries[id], desired[id]
        if not entry.ended and live(entry.actor) then
            if target then advance(entry, target.phase, state.world) else retire(entry) end
        end
    end
    for id, entry in pairs(current.dynamic) do
        if not desired[id] then
            current.dynamic[id], current.addresses[entry.address] = nil, nil
            if live(entry.actor) then entry.actor:K2_DestroyActor() end
        end
    end
    for id, target in pairs(desired) do
        if target.position then project_dynamic(current, id, target) end
    end
    current.epoch = epoch
    return "Applied the host's stock acid pool roster"
end

return pools
