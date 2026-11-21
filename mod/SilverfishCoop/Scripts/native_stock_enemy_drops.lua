local drops = {}
local guest_save = require("guest_save")
local shell_view = require("native_enemy_shell_view")
local transport = require("native_transport")
local world, latest, observed
local proof, bank, claim

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
        proof, bank, claim = nil, nil, nil
    end
    local address = pawn:GetAddress()
    for _ = 1, 64 do
        local sequence, roll, npc, killer, actor, class, live = SilverfishReadStockEnemyDrop(address, latest)
        if math.type(sequence) ~= "integer" or sequence <= latest then break end
        latest = sequence
        observed[#observed + 1] = {sequence, roll, npc, killer, actor, class, live and 1 or 0}
        if #observed > 128 then
            local expired = table.remove(observed, 1)[1]
            if proof and proof[1] == expired then proof = nil end
            if bank and bank[1] == expired then bank = nil end
        end
    end
    local rows = {}
    for _, record in ipairs(observed) do
        rows[#rows + 1] = row("native-stock-enemy-drop", state.map, table.unpack(record))
    end
    if proof then rows[#rows + 1] = row("native-stock-enemy-escrow", state.map, table.unpack(proof)) end
    if bank then rows[#rows + 1] = row("native-stock-enemy-loot-banked", state.map, table.unpack(bank)) end
    if claim then rows[#rows + 1] = row("native-stock-enemy-loot-claimed", state.map, table.unpack(claim)) end
    return rows
end

local function valid_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$")
end

local function finite(value, limit)
    return type(value) == "number" and value == value and math.abs(value) <= limit
end

local function transform(actor)
    local at, angle, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
    if not at or not angle or not scale then return end
    if not finite(at.X, 1e7) or not finite(at.Y, 1e7) or not finite(at.Z, 1e7)
        or not finite(angle.Pitch, 360) or not finite(angle.Yaw, 360) or not finite(angle.Roll, 360)
        or not finite(scale.X, 100) or not finite(scale.Y, 100) or not finite(scale.Z, 100)
        or scale.X == 0 or scale.Y == 0 or scale.Z == 0 then return end
    return {at.X, at.Y, at.Z, angle.Pitch, angle.Yaw, angle.Roll, scale.X, scale.Y, scale.Z}
end

local function capture(fields, state, phase, fixture)
    proof, bank = nil, nil
    local sequence, slot = math.tointeger(fields[3]), fields[4]
    local host, fresh = transport.scoped_host(state, phase)
    local pawn = host and host.pawn
    if phase ~= "hosting" or #fields ~= 4 or not valid_slot(slot)
        or not sequence or sequence < 1 or not same(world, state.world) or not pawn or not pawn:IsValid()
        or type(SilverfishCaptureStockEnemyDrop) ~= "function" or type(SilverfishVerifyNativeLootEscrow) ~= "function"
        or (fixture and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
        or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1")) then
        error("stock_enemy_escrow_scope_unavailable", 0)
    end
    if not fresh then error("stock_enemy_escrow_host_not_ready", 0) end
    local match
    for _, record in ipairs(observed) do
        if record[1] == sequence then match = record break end
    end
    local name = match and match[5]
    local actor = name and StaticFindObject(name:match("^%S+ (.+)$") or "")
    if not actor or not actor:IsValid() or actor:GetFullName() ~= name or not same(actor:GetWorld(), world) then
        error("stock_enemy_escrow_actor_unavailable", 0)
    end
    local pose = not fixture and transform(actor)
    if not fixture and not pose then error("stock_enemy_escrow_transform_unavailable", 0) end
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
    if fixture or not verified then
        local removed = api:DeleteGameInSlot(slot, 0)
        if not verified or not removed or api:DoesSaveGameExist(slot, 0) or not actor:IsValid()
            or actor:GetFullName() ~= name or actor.Ammo ~= ammo then error("stock_enemy_escrow_readback_failed", 0) end
        proof = {sequence, name, match[6], guid, ammo}
        return "stock NPC drop completed an exact escrow round trip"
    end

    actor:SetActorEnableCollision(false)
    actor:SetActorHiddenInGame(true)
    if actor:GetActorEnableCollision() ~= false or actor.bHidden ~= true then
        error("mutation_unverified_stock_enemy_drop_retirement", 0)
    end
    actor:K2_DestroyActor()
    bank = {sequence, name, match[6], guid, ammo, slot, table.unpack(pose)}
    return "exact stock NPC item banked and retired"
end

local function transfer(fields, state, phase)
    claim = nil
    local name, slot, guid = fields[3], fields[4], fields[5]
    local x, y, z = tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
    local host, fresh = transport.scoped_host(state, phase)
    local stock = host and host.pawn.Save
    if phase ~= "hosting" or #fields ~= 8 or not fresh or not stock or not stock:IsValid()
        or not valid_slot(slot) or type(guid) ~= "string" or #guid ~= 32 or not guid:match("^[0-9A-F]+$")
        or not finite(x, 1e7) or not finite(y, 1e7) or not finite(z, 1e7)
        or type(SilverfishClaimNativeLootEscrow) ~= "function" then
        error("stock_enemy_loot_claim_scope_unavailable", 0)
    end
    local pawn, save
    if name == host.pawn:GetFullName() then
        pawn, save = host.pawn, stock
    else
        local ok, guest, detached = pcall(guest_save.exact_item_context, state, phase, name)
        if ok then pawn, save = guest, detached end
    end
    if not pawn or not save or not save:IsValid() or not same(pawn:GetWorld(), state.world)
        or not same(pawn.Save, save) or pawn.Dead ~= false then error("stock_enemy_loot_claimant_unavailable", 0) end
    if not shell_view.clear_line(pawn, {X = x, Y = y, Z = z}, true) then
        error("stock_enemy_loot_out_of_reach_or_obstructed", 0)
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pool = api and api:IsValid() and api:LoadGameFromSlot(slot, 0)
    if not pool or not pool:IsValid() then error("stock_enemy_loot_escrow_unavailable", 0) end
    local ok, status, amount, before, after = SilverfishClaimNativeLootEscrow(pawn:GetAddress(), save:GetAddress(),
        stock:GetAddress(), pool:GetAddress(), guid)
    if not ok then error(tostring(status), 0) end
    claim = {name, slot, guid, status, amount or 0, before or 0, after or 0}
    return "exact stock NPC item transferred"
end

function drops.command(fields, state, phase)
    if fields[2] == "native-stock-enemy-escrow-fixture" then return capture(fields, state, phase, true) end
    if fields[2] == "native-stock-enemy-loot-bank" then return capture(fields, state, phase, false) end
    if fields[2] == "native-stock-enemy-loot-claim" then return transfer(fields, state, phase) end
end

return drops
