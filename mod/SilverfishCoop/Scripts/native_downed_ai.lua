local ai = {}

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local world, candidates, sampled_at

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_downed(actor, downed)
    return valid(actor) and downed[actor:GetAddress()] == true
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

function ai.sync(state, phase, desired)
    if phase ~= "hosting" or not state.listening or state.server or not valid(state.world) then
        world, candidates, sampled_at = nil, nil, nil
        return
    end
    if next(desired) == nil then return end
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
