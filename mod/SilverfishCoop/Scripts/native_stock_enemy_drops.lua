local drops = {}
local guest_save = require("guest_save")
local shell_view = require("native_enemy_shell_view")
local world_loot = require("native_guest_drop_projection")
local transport = require("native_transport")
local motion = require("native_enemy_drop_motion")
local world, latest, observed
local proof, bank, claim, broker_defeated
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local black_market_level = black_market .. ".Shop_BlackMarket_01:PersistentLevel."
local broker_path = black_market_level .. "NPC_Broker_C_1"
local broker_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Broker.NPC_Broker_C"
local broker_cue_path = black_market_level .. "Generic_Interact_BP_C_1"
local broker_cue_class = "/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C"

local function same(a, b)
    return a and b and a:IsValid() and b:IsValid() and a:GetAddress() == b:GetAddress()
end

local function live_actor(record)
    local name = record[5]
    local actor = record.actor or StaticFindObject(name:match("^%S+ (.+)$") or "")
    if not actor or not actor:IsValid() or actor:IsActorBeingDestroyed()
        or actor:GetFullName() ~= name or not same(actor:GetWorld(), world) then return end
    record.actor = actor
    return actor
end

local function market_scope(state, phase)
    return state.map == black_market and state.listening and (phase == "hosting" or phase == "connected")
        and state.server == (phase == "connected") and same(state.world, StaticFindObject(black_market .. ".Shop_BlackMarket_01"))
end

local function broker_cue_removed()
    local cue = StaticFindObject(broker_cue_path)
    return not cue or not cue:IsValid() or cue:IsActorBeingDestroyed()
        or (cue:GetActorEnableCollision() == false and cue.bHidden == true)
end

local function remove_broker_cue(fields, state, phase)
    if #fields ~= 2 or phase ~= "connected" or not market_scope(state, phase) then
        error("stock_broker_cue_scope_unavailable", 0)
    end
    if not broker_cue_removed() then
        local cue = StaticFindObject(broker_cue_path)
        if not same(cue:GetClass(), StaticFindObject(broker_cue_class)) or not same(cue:GetWorld(), state.world)
            or not same(cue:GetOuter(), state.world.PersistentLevel) or cue:GetFullName() ~= "Generic_Interact_BP_C " .. broker_cue_path then
            error("stock_broker_cue_identity_unavailable", 0)
        end
        -- The host's stock BrokerDeath destroys this interaction actor. A stale guest copy blocks the key's trace.
        cue:SetActorEnableCollision(false)
        cue:SetActorHiddenInGame(true)
        if not broker_cue_removed() then error("stock_broker_cue_removal_unverified", 0) end
    end
    return "stock Broker dialogue collision removed"
end

function drops.rows(state, phase, row)
    local rows = {}
    if phase == "connected" and market_scope(state, phase) and broker_cue_removed() then
        rows[#rows + 1] = row("native-stock-broker-cue-removed", black_market)
    end
    if phase ~= "hosting" or state.server ~= false or not state.listening
        or type(SilverfishReadStockEnemyDrop) ~= "function" then return rows end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not pawn or not pawn:IsValid() or not same(pawn:GetWorld(), state.world) then return {} end
    if not same(world, state.world) then
        world, latest, observed = state.world, 0, {}
        proof, bank, claim, broker_defeated = nil, nil, nil, nil
    end
    local address = pawn:GetAddress()
    for _ = 1, 64 do
        local sequence, roll, npc, killer, actor, class, live = SilverfishReadStockEnemyDrop(address, latest)
        if math.type(sequence) ~= "integer" or sequence <= latest then break end
        latest = sequence
        observed[#observed + 1] = {sequence, roll, npc, killer, actor, class, live and 1 or 0, sample = {}}
        if live and npc == "NPC_Broker_C " .. broker_path
            and class == "/Game/Classes/Items/Key_Items/BrokersKey.BrokersKey_C" then
            broker_defeated = true
        end
        if #observed > 128 then
            local expired = table.remove(observed, 1)[1]
            if proof and proof[1] == expired then proof = nil end
            if bank and bank[1] == expired then bank = nil end
        end
    end
    if broker_defeated and market_scope(state, phase) then
        rows[#rows + 1] = row("native-stock-broker-cue-removed", black_market)
    end
    for _, record in ipairs(observed) do
        if not record.finished then
            local actor = record[7] == 1 and live_actor(record)
            record[8] = not actor and "gone" or motion.settled(actor, record.sample) and "settled" or "moving"
            record[9] = record.sample.origin_z or "-"
            if not actor then record.finished = true end
        end
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
    local actor = match and live_actor(match)
    if not actor then error("stock_enemy_escrow_actor_unavailable", 0) end
    if not fixture and not motion.settled(actor, match.sample) then
        error("stock_enemy_escrow_drop_settling", 0)
    end
    local name = match[5]
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
    match.finished = true
    bank = {sequence, name, match[6], guid, ammo, slot, table.unpack(pose)}
    return "exact stock NPC item banked and retired"
end

local function transfer(fields, state, phase)
    claim = nil
    local name, slot, guid = fields[3], fields[4], fields[5]
    local x, y, z, identity = tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8]), fields[9]
    local host, fresh = transport.scoped_host(state, phase)
    local stock = host and host.pawn.Save
    local ground = #fields == 10 and fields[10] == "legacy-ground"
    if phase ~= "hosting" or (#fields ~= 9 and not ground) or not fresh or not stock or not stock:IsValid()
        or not valid_slot(slot) or type(guid) ~= "string" or #guid ~= 32 or not guid:match("^[0-9A-F]+$")
        or not finite(x, 1e7) or not finite(y, 1e7) or not finite(z, 1e7)
        or #identity ~= 32 or not identity:match("^[0-9a-f]+$")
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
    if ground then z = world_loot.floor_z(state, {x, y, z}) end
    local position = {X = x, Y = y, Z = z}
    if not shell_view.clear_line(pawn, position, true)
        and not world_loot.authority_sight(state, phase, pawn, identity, position) then
        error("stock_enemy_loot_out_of_reach_or_obstructed", 0)
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pool = api and api:IsValid() and api:LoadGameFromSlot(slot, 0)
    if not pool or not pool:IsValid() then error("stock_enemy_loot_escrow_unavailable", 0) end
    local ok, status, amount, before, after, inventory_guid = SilverfishClaimNativeLootEscrow(pawn:GetAddress(), save:GetAddress(),
        stock:GetAddress(), pool:GetAddress(), guid)
    if not ok then error(tostring(status), 0) end
    claim = {name, slot, guid, status, amount or 0, before or 0, after or 0}
    if status == "merged" then claim[8] = inventory_guid or "-" end
    return "exact stock NPC item transferred"
end

local function broker_key_fixture(fields, state, phase)
    if #fields ~= 2 or phase ~= "hosting" or not market_scope(state, phase)
        or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then
        error("stock_broker_key_fixture_unavailable", 0)
    end
    local host, ready = transport.scoped_host(state, phase)
    local broker = StaticFindObject(broker_path)
    local kind = StaticFindObject(broker_class)
    if not ready or not host or not broker or not broker:IsValid() or not same(broker:GetClass(), kind)
        or not same(broker:GetWorld(), state.world) or not same(broker:GetOuter(), state.world.PersistentLevel)
        or not broker:HasAuthority() or broker:IsActorBeingDestroyed() or not finite(broker.Health, 100000)
        or broker.Health <= 0 then error("stock_broker_key_fixture_scope_unavailable", 0) end
    broker:InstantKill(host.pawn)
    return "stock Broker death invoked in the isolated loot fixture"
end

function drops.command(fields, state, phase)
    if fields[2] == "native-stock-broker-cue-remove" then return remove_broker_cue(fields, state, phase) end
    if fields[2] == "native-stock-broker-key-fixture" then return broker_key_fixture(fields, state, phase) end
    if fields[2] == "native-stock-enemy-escrow-fixture" then return capture(fields, state, phase, true) end
    if fields[2] == "native-stock-enemy-loot-bank" then return capture(fields, state, phase, false) end
    if fields[2] == "native-stock-enemy-loot-claim" then return transfer(fields, state, phase) end
end

return drops
