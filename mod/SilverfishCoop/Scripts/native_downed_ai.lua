local ai = {}
local guest_save = require("guest_save")

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local aggro_path = npc_path .. ":Agro_Buffer"
local remember_path = npc_path .. ":AddTargetToList"
local remembered_target = "CombatTarget_7_5EB8D2544EEE9E5E6FB88485135FB2B4"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, candidates, sampled_at
local threat_session, threat_downed, threat_viewport, threat_hook
local turret_mask = ""

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_downed(actor, downed)
    return valid(actor) and downed[actor:GetAddress()] == true
end

local function remember_stock_attacker(npc, source, from_damage)
    local state = threat_session
    if not state or from_damage ~= true or not valid(npc) or not valid(source)
        or not valid(threat_viewport) or not same(threat_viewport.World, state.world)
        or not npc:HasAuthority() or npc.Alive ~= true or npc:IsActorBeingDestroyed()
        or not source:HasAuthority() or source.Dead ~= false or source:IsActorBeingDestroyed()
        or not same(npc:GetWorld(), state.world) or not same(source:GetWorld(), state.world)
        or not same(npc.CombatTarget, source) then return end
    local driver = state.world.NetDriver
    if not valid(driver) or valid(driver.ServerConnection) or #driver.ClientConnections == 0 then return end
    local controller = source.Controller
    if not valid(controller) or not same(controller.Pawn, source) or not same(controller:GetWorld(), state.world)
        or not controller:HasAuthority() or controller:IsActorBeingDestroyed() then return end
    local record = threat_downed[source:GetFullName()]
    if record and same(record.pawn, source) and same(record.world, state.world) then return end

    local targets = npc.TargetList
    for index = 1, targets:GetArrayNum() do
        if same(targets[index][remembered_target], source) then return end
    end

    local locally_controlled = source:IsLocallyControlled()
    local is_host = locally_controlled and same(state.local_pawn.pawn, source)
        and same(state.local_pawn.controller_object, controller)
    for _, guest in ipairs(state.guests) do
        local is_guest = not locally_controlled and same(guest.pawn, source)
            and same(guest.controller_object, controller)
        if is_host or is_guest then
            local pawn = guest_save.exact_item_context(state, "hosting", guest.name)
            if same(pawn, guest.pawn) then
                -- Some stock combat reactions set CombatTarget directly.
                -- Keep the already accepted attacker available to stock retargeting.
                npc:AddTargetToList(source, {})
                return
            end
        end
    end
end

local function arm_stock_threat_hook(state, desired)
    if not threat_session or not same(threat_session.world, state.world) or not valid(threat_viewport) then
        threat_viewport = FindFirstOf("GameViewportClient")
    end
    threat_session, threat_downed = state, desired
    if not threat_hook and valid(StaticFindObject(aggro_path)) and valid(StaticFindObject(remember_path)) then
        RegisterHook(aggro_path, function(context, source, _, _faction, _force, from_damage)
            -- UE4SS calls Blueprint hooks after the original script.
            if threat_session then
                pcall(function() remember_stock_attacker(context:get(), source:get(), from_damage:get()) end)
            end
        end)
        threat_hook = true
    end
end

local function npc_candidates(current)
    local now = os.clock()
    if not same(world, current) or now - sampled_at >= 1 then
        local statics, class = StaticFindObject(statics_path), StaticFindObject(npc_path)
        if not valid(statics) or not valid(class) then return {} end
        local found = {}
        statics:GetAllActorsOfClass(current, class, found)
        candidates, world, sampled_at = found.OutActors or found, current, now
    end
    return candidates
end

local function sync_turret_targets(world, downed)
    if type(SilverfishSetTurretInactiveTargets) ~= "function" then return end
    local keys = {}
    if world then
        for address in pairs(downed) do keys[#keys + 1] = address end
        table.sort(keys)
        if #keys > 0 then table.insert(keys, 1, world:GetAddress()) end
    end
    local mask = table.concat(keys, ":")
    if mask == turret_mask then return end
    SilverfishSetTurretInactiveTargets(table.unpack(keys))
    turret_mask = mask
end

function ai.sync(state, phase, desired)
    if phase ~= "hosting" or not state.listening or state.server or not valid(state.world) then
        world, candidates, sampled_at = nil, nil, nil
        threat_session, threat_downed, threat_viewport = nil, nil, nil
        sync_turret_targets()
        return
    end
    if state.clients > 0 and state.local_pawn and #state.guests > 0 then
        arm_stock_threat_hook(state, desired)
    else
        threat_session, threat_downed, threat_viewport = nil, nil, nil
    end
    if next(desired) == nil then
        sync_turret_targets()
        return
    end
    local downed, standing = {}, 0
    for _, details in ipairs(state.pawns) do
        local pawn, record = details.pawn, desired[details.name]
        if valid(pawn) and pawn:HasAuthority() and same(pawn:GetWorld(), state.world) then
            if record and same(record.pawn, pawn) and same(record.world, state.world) then
                downed[pawn:GetAddress()] = true
            else
                standing = standing + 1
            end
        end
    end
    sync_turret_targets(state.world, downed)
    if standing == 0 or next(downed) == nil then return end

    for _, value in ipairs(npc_candidates(state.world)) do
        local npc = value
        if npc and type(npc.type) == "function" and npc:type() == "RemoteUnrealParam" then npc = npc:get() end
        if valid(npc) and npc:HasAuthority() and npc.Alive == true and not npc:IsActorBeingDestroyed()
            and same(npc:GetWorld(), state.world) and is_downed(npc.CombatTarget, downed) then
            -- Each stock selection discards the previous target from the
            -- threat list. Re-run if the next threat is another downed player.
            for _ = 1, #state.pawns do
                if not is_downed(npc.CombatTarget, downed) then break end
                local chosen = {}
                npc:GetNewCombatTarget(chosen)
                if chosen.NewTargetFound == false then break end
            end
            if is_downed(npc.CombatTarget, downed) then
                npc.CombatTarget = nil
                npc.HasTargetLOS = false
            end
        end
    end
end

return ai
