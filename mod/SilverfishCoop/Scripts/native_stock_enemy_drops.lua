local drops = {}
local world, latest, observed
local proof

local function same(a, b)
    return a and b and a:IsValid() and b:IsValid() and a:GetAddress() == b:GetAddress()
end

function drops.rows(state, phase, row)
    if phase ~= "hosting" or state.server ~= false or not state.listening
        or type(SilverfishReadStockEnemyDrop) ~= "function" then return {} end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not pawn or not pawn:IsValid() or not same(pawn:GetWorld(), state.world) then return {} end
    if not same(world, state.world) then
        world, latest, observed = state.world, 0, {}
        proof = nil
    end
    local address = pawn:GetAddress()
    for _ = 1, 64 do
        local sequence, roll, npc, killer, actor, class, live = SilverfishReadStockEnemyDrop(address, latest)
        if math.type(sequence) ~= "integer" or sequence <= latest then break end
        latest = sequence
        observed[#observed + 1] = {sequence, roll, npc, killer, actor, class, live and 1 or 0}
        if #observed > 128 then table.remove(observed, 1) end
    end
    local rows = {}
    for _, record in ipairs(observed) do
        rows[#rows + 1] = row("native-stock-enemy-drop", state.map, table.unpack(record))
    end
    if proof then rows[#rows + 1] = row("native-stock-enemy-escrow", state.map, table.unpack(proof)) end
    return rows
end

function drops.command(fields, state, phase)
    if fields[2] ~= "native-stock-enemy-escrow-fixture" then return end
    proof = nil
    local sequence, slot = math.tointeger(fields[3]), fields[4]
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "hosting" or #fields ~= 4 or not slot:match("^SFCoop_[0-9a-f]+$") or #slot ~= 39
        or not sequence or sequence < 1 or not same(world, state.world) or not pawn or not pawn:IsValid()
        or type(SilverfishCaptureStockEnemyDrop) ~= "function" or type(SilverfishVerifyNativeLootEscrow) ~= "function"
        or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then
        error("stock_enemy_escrow_fixture_scope_unavailable", 0)
    end
    local match
    for _, record in ipairs(observed) do
        if record[1] == sequence then match = record break end
    end
    local name = match and match[5]
    local actor = name and StaticFindObject(name:match("^%S+ (.+)$") or "")
    if not actor or not actor:IsValid() or actor:GetFullName() ~= name or not same(actor:GetWorld(), world) then
        error("stock_enemy_escrow_fixture_actor_unavailable", 0)
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local save_class = StaticFindObject("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
    if not api or not api:IsValid() or not save_class or not save_class:IsValid() or api:DoesSaveGameExist(slot, 0) then
        error("stock_enemy_escrow_fixture_save_unavailable", 0)
    end
    local scratch = api:CreateSaveGameObject(save_class)
    if not scratch or not scratch:IsValid() then error("stock_enemy_escrow_fixture_save_unavailable", 0) end
    local ok, reason, guid, ammo = SilverfishCaptureStockEnemyDrop(pawn:GetAddress(), scratch:GetAddress(), actor:GetAddress(), sequence)
    if not ok then error(tostring(reason), 0) end
    if not api:SaveGameToSlot(scratch, slot, 0) then error("stock_enemy_escrow_fixture_save_failed", 0) end
    local restored = api:LoadGameFromSlot(slot, 0)
    local verified = restored and restored:IsValid() and SilverfishVerifyNativeLootEscrow(scratch:GetAddress(), restored:GetAddress())
    local removed = api:DeleteGameInSlot(slot, 0)
    if not verified or not removed or api:DoesSaveGameExist(slot, 0) or not actor:IsValid()
        or actor:GetFullName() ~= name or actor.Ammo ~= ammo then error("stock_enemy_escrow_fixture_readback_failed", 0) end
    proof = {sequence, name, match[6], guid, ammo}
    return "stock NPC drop completed an exact escrow round trip"
end

return drops
