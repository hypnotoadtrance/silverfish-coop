local identity = {}

local reference = require("replicated_actor_reference")
local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local prefix = "5346434F4F504241525445520000"

local marker_class, npc_class, world, phase
local active = false
local number, markers, pending = 0, {}, {}
local census, cursor
local retry_interval = 1
local registration_window = 30

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function placed(npc)
    return npc:HasAnyFlags(EObjectFlags.RF_WasLoaded)
end

local function actor_in_world(actor)
    return active and valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
end

local function exact_npc(actor)
    return active and valid(npc_class) and valid(actor) and actor:IsA(npc_class) and actor_in_world(actor)
end

local function bind(npc, marker, value)
    markers[npc:GetAddress()] = {marker = marker, number = value}
    if type(SilverfishBindNativeShotTarget) == "function" then
        SilverfishBindNativeShotTarget(marker:GetFullName():match("^%S+ (.+)$"))
    end
end

local function remember(marker)
    if not actor_in_world(marker) or not same(marker:GetClass(), marker_class) or marker:HasAuthority() then return true end
    local npc = marker:GetOwner()
    local value = reference.number(marker, "barter")
    local owner_known = exact_npc(npc)
    if not value and not owner_known then return true end
    if not value or not owner_known then return false end
    bind(npc, marker, value)
    return true
end

local function publish(npc)
    if not exact_npc(npc) or placed(npc) or not npc:HasAuthority() or number == reference.limit then return true end
    if npc.Alive ~= true then return true end
    if npc.bReplicates ~= true then return false end
    local address = npc:GetAddress()
    local previous = markers[address]
    if previous and actor_in_world(previous.marker) then return true end

    local next_number = number + 1
    local marker = reference.spawn(world, marker_class, "barter", npc, next_number)
    if not actor_in_world(marker) then return false end
    number = next_number
    bind(npc, marker, number)
    return true
end

local function enqueue(actor)
    local address, now = actor:GetAddress(), os.time()
    local existing = pending[address]
    if existing and same(existing.actor, actor) then
        existing.next_attempt = now
    else
        pending[address] = {actor = actor, next_attempt = now, expires = now + registration_window}
    end
end

local function install()
    if type(RegisterHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function"
        or type(RegisterEndPlayPreHook) ~= "function" then return false end
    marker_class = StaticFindObject(reference.class)
    npc_class = StaticFindObject(npc_path)
    if not valid(marker_class) or not valid(npc_class) then return false end
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if not active or phase ~= "connected" then return end
        local marker = context:get()
        if valid(marker) and same(marker:GetClass(), marker_class) and actor_in_world(marker) then
            enqueue(marker)
        end
    end)
    RegisterBeginPlayPostHook(function(context)
        if not active or phase ~= "hosting" then return end
        local actor = context:get()
        if exact_npc(actor) and not placed(actor) then
            enqueue(actor)
        end
    end)
    RegisterEndPlayPreHook(function(context, reason)
        if not phase then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        local entry = markers[address]
        markers[address], pending[address] = nil, nil
        if active and phase == "hosting" and reason:get() == 0 and entry and valid(entry.marker) then
            entry.marker:K2_DestroyActor()
        end
    end)
    return true
end

local installed = false

function identity.sync(state, current)
    active = (current == "hosting" or current == "connected") and valid(state.world)
    -- The BeginPlay hooks persist across title returns. Suspend reflection
    -- while loading, then rebind to the new world once co-op is active.
    if not active then return end
    if not installed then
        installed = install()
        if not installed then return end
    end
    if phase ~= current or not same(state.world, world) then
        world, phase, number, markers, pending = state.world, current, 0, {}, {}
        if type(SilverfishBindNativeShotTarget) == "function" then SilverfishBindNativeShotTarget() end
        if phase == "hosting" then
            local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
            if valid(statics) then
                local output = {}
                statics:GetAllActorsOfClass(world, npc_class, output)
                census, cursor = output.OutActors or output, 1
            end
        else
            census = nil
            -- A marker may have arrived before the guest entered the active session.
            for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
                if not remember(marker) then enqueue(marker) end
            end
        end
    end
    if census then
        local limit = math.min(cursor + 7, #census)
        for index = cursor, limit do
            local npc = census[index]
            if npc and type(npc.type) == "function" and npc:type() == "RemoteUnrealParam" then npc = npc:get() end
            if exact_npc(npc) and not publish(npc) then enqueue(npc) end
        end
        cursor = limit + 1
        if cursor > #census then census = nil end
    end
    local now = os.time()
    for address, entry in pairs(pending) do
        if now >= entry.expires or not actor_in_world(entry.actor) then
            pending[address] = nil
        elseif now >= entry.next_attempt then
            local actor = entry.actor
            local complete
            if phase == "connected" then
                complete = remember(actor)
            else
                complete = publish(actor)
            end
            if complete then pending[address] = nil else entry.next_attempt = now + retry_interval end
        end
    end
end

function identity.key(npc)
    if not exact_npc(npc) or placed(npc) then return end
    local entry = markers[npc:GetAddress()]
    if entry and actor_in_world(entry.marker) and same(entry.marker:GetOwner(), npc) then
        return ("%s%04X"):format(prefix, entry.number)
    end
end

function identity.is_dynamic(npc)
    return exact_npc(npc) and not placed(npc)
end

return identity
