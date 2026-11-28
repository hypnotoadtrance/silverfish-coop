local crates = {}
local loot = require("native_loot")
local reference = require("replicated_actor_reference")
local world_path = require("world_path")

local container_path = "/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, phase, container_class, marker_class, installed, censused
local queued, entries, serial = {}, {}, 0
local ended = {}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function runtime_container(actor)
    return valid(container_class) and valid(actor) and actor:IsA(container_class)
        and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
        and world_path.visible_actor_level(actor, world) and same(actor:GetOuter(), world.PersistentLevel)
end

local function queue(actor)
    local address = actor:GetAddress()
    queued[address] = queued[address] or {actor = actor, next_at = 0, expires = os.clock() + 12}
end

local function bind(state, actor, number)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(pawn) then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    return path and SilverfishBindNativeRuntimeLootCrate(path, pawn:GetAddress(), number)
end

local function register(state, entry)
    local actor = entry.actor
    if phase == "hosting" then
        if not runtime_container(actor) or not actor:HasAuthority() then return true end
        local address = actor:GetAddress()
        if entries[address] and same(entries[address].actor, actor) then return true end
        if not entry.number then
            if serial >= reference.limit then return true end
            serial = serial + 1
            entry.number = serial
        end
        local index = bind(state, actor, entry.number)
        if not index then return end
        if not actor.bReplicates then actor:SetReplicates(true) end
        if not actor.bReplicates then return end
        local marker = reference.spawn(world, marker_class, "crate", actor, entry.number)
        if not valid(marker) then return end
        local profile = loot.profile_at(state.map, index)
        if not profile then
            marker:K2_DestroyActor()
            return
        end
        entries[address] = {actor = actor, marker = marker, profile = profile}
        return true
    end
    local marker = actor
    if marker:HasAuthority() then return true end
    local number, owner = reference.number(marker, "crate"), marker:GetOwner()
    if not number or not runtime_container(owner) or owner:GetLocalRole() ~= 1 then return end
    local index = bind(state, owner, number)
    return index and loot.profile_at(state.map, index) ~= nil
end

local function install()
    if installed then return end
    if type(RegisterBeginPlayPostHook) ~= "function" or type(RegisterEndPlayPreHook) ~= "function"
        or type(RegisterHook) ~= "function" then return end
    RegisterBeginPlayPostHook(function(context)
        local actor = context:get()
        if phase == "hosting" and valid(container_class) and valid(actor) and actor:IsA(container_class)
            and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) and same(actor:GetWorld(), world) then
            queue(actor)
        elseif phase == "connected" and valid(actor) and same(actor:GetClass(), marker_class) then
            queue(actor)
        end
    end)
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        local actor = context:get()
        if phase == "connected" and valid(actor) and same(actor:GetClass(), marker_class) then queue(actor) end
    end)
    RegisterEndPlayPreHook(function(context)
        local actor = context:get()
        if not phase or not valid(actor) then return end
        local address = actor:GetAddress()
        local entry = entries[address]
        queued[address], entries[address] = nil, nil
        if phase == "hosting" and entry then
            ended[#ended + 1] = entry.profile.name
            if #ended > 128 then table.remove(ended, 1) end
            if valid(entry.marker) and not entry.marker:IsActorBeingDestroyed() then entry.marker:K2_DestroyActor() end
        end
    end)
    installed = true
end

function crates.sync(state, seat)
    if (seat ~= "hosting" and seat ~= "connected") or not valid(state.world) then
        phase = nil
        return
    end
    if type(SilverfishBindNativeRuntimeLootCrate) ~= "function" then return end
    install()
    if not installed then return end
    if not same(world, state.world) or phase ~= seat then
        world, phase, queued, entries, ended, censused = state.world, seat, {}, {}, {}, false
    end
    if not censused then
        container_class, marker_class = StaticFindObject(container_path), StaticFindObject(reference.class)
        local statics = StaticFindObject(statics_path)
        if not valid(container_class) or not valid(marker_class) or not valid(statics) then return end
        local result = {}
        statics:GetAllActorsOfClass(world, phase == "hosting" and container_class or marker_class, result)
        for _, actor in ipairs(result.OutActors or result) do
            if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
            if valid(actor) and (phase == "connected" or runtime_container(actor)) then queue(actor) end
        end
        censused = true
    end
    local now, attempts = os.clock(), 0
    for address, entry in pairs(queued) do
        if now >= entry.expires or not valid(entry.actor) or entry.actor:IsActorBeingDestroyed() then
            queued[address] = nil
        elseif now >= entry.next_at then
            if register(state, entry) then queued[address] = nil else entry.next_at = now + .125 end
            attempts = attempts + 1
            if attempts >= 4 then break end
        end
    end
end

function crates.append_ended(rows, state, status_row)
    if phase ~= "hosting" or not same(world, state.world) then return end
    for _, name in ipairs(ended) do
        rows[#rows + 1] = status_row("native-runtime-crate-ended", state.map, name)
    end
end

return crates
