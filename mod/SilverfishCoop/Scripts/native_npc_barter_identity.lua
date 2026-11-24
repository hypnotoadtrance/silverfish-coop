local identity = {}

local marker_path = "/Script/Engine.TargetPoint"
local trader_path = "/Game/Classes/Actors/Barter_BP.Barter_BP_C"
local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local step = 360 / 256
-- Use a pitch outside the range used by the stock co-op corpse markers.
local marker_code = 62
local max_number = 65535
local prefix = "5346434F4F504241525445520000"

local marker_class, trader_class, npc_class, world, phase
local number, markers, pending = 0, {}, {}

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function placed(npc)
    return npc:HasAnyFlags(EObjectFlags.RF_WasLoaded)
end

local function byte(angle)
    return math.floor(angle / step + .5) % 256
end

local function actor_in_world(actor)
    return valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), world)
end

local function exact_npc(actor)
    return actor_in_world(actor) and valid(npc_class) and actor:IsA(npc_class)
end

local function number_on(marker)
    local rotation = marker:K2_GetActorRotation()
    if byte(rotation.Pitch) ~= marker_code then return end
    local value = byte(rotation.Yaw) + 256 * byte(rotation.Roll)
    if value > 0 then return value end
end

local function remember(marker)
    if not actor_in_world(marker) or not same(marker:GetClass(), marker_class) or marker:HasAuthority() then return end
    local npc = marker:GetOwner()
    local value = exact_npc(npc) and number_on(marker)
    if value then markers[npc:GetAddress()] = {marker = marker, number = value} end
end

local function publish(npc, trader)
    if not exact_npc(npc) or placed(npc) or not npc:HasAuthority() or npc.bReplicates ~= true
        or not actor_in_world(trader) or not same(trader:GetClass(), trader_class) or not same(trader.LinkedNPC, npc)
        or not same(npc.BarterRef, trader) or valid(trader.LinkedShop) or npc.Alive ~= true or not valid(trader.MerchantData)
        or number == max_number then return end
    local address = npc:GetAddress()
    local previous = markers[address]
    if previous and actor_in_world(previous.marker) then return end

    local next_number = number + 1
    local marker = world:SpawnActor(marker_class, npc:K2_GetActorLocation(), {
        Pitch = marker_code * step, Yaw = (next_number % 256) * step, Roll = (next_number // 256) * step,
    })
    if not actor_in_world(marker) then return end
    marker:SetOwner(npc)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker.NetUpdateFrequency = 2
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    number = next_number
    markers[address] = {marker = marker, number = number}
end

local function install()
    if type(RegisterHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function"
        or type(RegisterEndPlayPreHook) ~= "function" then return false end
    marker_class = StaticFindObject(marker_path)
    trader_class = StaticFindObject(trader_path)
    npc_class = StaticFindObject(npc_path)
    if not valid(marker_class) or not valid(trader_class) or not valid(npc_class) then return false end
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if phase ~= "connected" then return end
        local marker = context:get()
        if actor_in_world(marker) and same(marker:GetClass(), marker_class) then
            pending[marker:GetAddress()] = marker
        end
    end)
    RegisterBeginPlayPostHook(function(context)
        if phase ~= "hosting" then return end
        local actor = context:get()
        if exact_npc(actor) and not placed(actor) then
            pending[actor:GetAddress()] = actor
        elseif actor_in_world(actor) and same(actor:GetClass(), trader_class) and exact_npc(actor.LinkedNPC) then
            pending[actor.LinkedNPC:GetAddress()] = actor.LinkedNPC
        end
    end)
    RegisterEndPlayPreHook(function(context)
        if not phase then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        local entry = markers[address]
        markers[address], pending[address] = nil, nil
        if phase == "hosting" and entry and valid(entry.marker) then entry.marker:K2_DestroyActor() end
    end)
    return true
end

local installed = false

function identity.sync(state, current)
    if (current ~= "hosting" and current ~= "connected") or not valid(state.world) then return end
    if not installed then
        installed = install()
        if not installed then return end
    end
    if phase ~= current or not same(state.world, world) then
        world, phase, number, markers, pending = state.world, current, 0, {}, {}
        if phase == "hosting" then
            for _, trader in ipairs(FindAllOf("Barter_BP_C") or {}) do
                if actor_in_world(trader) then publish(trader.LinkedNPC, trader) end
            end
        else
            -- A marker may have arrived before the guest entered the active session.
            for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do remember(marker) end
        end
    end
    for address, actor in pairs(pending) do
        if phase == "connected" then
            remember(actor)
        elseif exact_npc(actor) then
            publish(actor, actor.BarterRef)
        end
        pending[address] = nil
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
