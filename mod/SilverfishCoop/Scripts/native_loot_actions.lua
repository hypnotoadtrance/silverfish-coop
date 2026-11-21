local actions = {}
local loot = require("native_loot")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local observation, placement, daily_update
local statics = "/Script/Engine.Default__GameplayStatics"
local save = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"

local function valid_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function valid_object(value)
    return value and value:IsValid()
end

local function capture(scope, slot, outputs, items)
    local api, class = StaticFindObject(statics), StaticFindObject(save)
    if not valid_object(api) or not valid_object(class) or api:DoesSaveGameExist(slot, 0) then
        error("native_loot_escrow_slot_unavailable", 0)
    end
    local pool = api:CreateSaveGameObject(class)
    if not valid_object(pool) then error("native_loot_escrow_could_not_create_save", 0) end
    local captured = table.pack(SilverfishCaptureNativeLootEscrow(
        scope.crate:GetAddress(), pool:GetAddress(), table.unpack(outputs)))
    if captured[1] ~= true or captured[3] ~= #items or captured.n ~= 3 + #outputs then
        error(captured[2] or "native_loot_escrow_capture_unverified", 0)
    end
    local index, seen = 1, {}
    for slot_index = 1, #outputs do
        local guid = captured[3 + slot_index]
        if type(guid) ~= "string" or (#guid > 0) ~= (outputs[slot_index] > 0) then
            error("native_loot_escrow_item_slot_unverified", 0)
        end
        if #guid > 0 then
            local item = items[index]
            if #guid ~= 32 or not guid:match("^[0-9A-F]+$") or not guid:find("[^0]") or seen[guid]
                or not item or not valid_object(item.object) then error("native_loot_escrow_item_unverified", 0) end
            seen[guid] = true
            item.guid = guid
            index = index + 1
        end
    end
    if index - 1 ~= #items then error("native_loot_escrow_item_identity_count_unverified", 0) end
    if not api:SaveGameToSlot(pool, slot, 0) then error("native_loot_escrow_write_failed", 0) end
    local restored = api:LoadGameFromSlot(slot, 0)
    local source = scope.profile.capacity ~= 2 and {scope.crate:GetAddress()} or {}
    if not valid_object(restored) or not SilverfishVerifyNativeLootEscrow(
        pool:GetAddress(), restored:GetAddress(), table.unpack(source)) then
        error("native_loot_escrow_typed_roundtrip_unverified", 0)
    end
    for _, item in ipairs(items) do
        item.object:SetActorEnableCollision(false)
        item.object:SetActorHiddenInGame(true)
        if item.object:GetActorEnableCollision() ~= false or item.object.bHidden ~= true then
            error("native_loot_escrow_item_retirement_unverified", 0)
        end
        item.object:K2_DestroyActor()
    end
end

function actions.command(fields, state, phase)
    if fields[2] == "native-loot-replenishment-fixture" then
        if not enabled or not fixtures or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
            or #fields ~= 3 or phase ~= "hosting" or not loot.profile(state.map, fields[3]) then
            error("native_loot_replenishment_fixture_unavailable", 0)
        end
        daily_update = {map = state.map, crate = fields[3], days = loot.replenish_fixture(state, phase, fields[3])}
        return "stock daily update expired the temporary activation"
    end
    local action = fields[2]:match("^native%-loot%-(%l+)$")
    if action == "placement" then
        local crate = fields[3]
        if not enabled or phase ~= "hosting" or #fields ~= 3 or not loot.profile(state.map, crate) then
            error("native_loot_child_placement_unavailable", 0)
        end
        placement = { map = state.map, crate = crate, positions = loot.placement(state, phase, crate) }
        return "stock crate child placements reported"
    end
    if action ~= "inspect" and action ~= "open" and action ~= "approach" and action ~= "seed" and action ~= "claim" then
        return
    end
    local pawn, crate = fields[3], fields[4]
    local fixture_nonempty = action == "open" and fields[6] == "fixture-nonempty"
    local count = action == "open" and (fixture_nonempty and 6 or 5) or action == "claim" and 6 or 4
    local positioned_claim = action == "claim" and #fields == 9
    if positioned_claim then count = 9 end
    if not enabled or phase ~= "hosting" or #fields ~= count or not crate or not loot.profile(state.map, crate) then
        error("The exact stock crate operation is unavailable", 0)
    end
    if (action == "seed" or action == "approach" or fixture_nonempty)
        and (not fixtures or fixture_nonempty and os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1") then
        error("The exact stock crate operation is unavailable", 0)
    end
    observation = { action = action, map = state.map, crate = crate, pawn = pawn,
        ok = false, invoked = false, looted = false, calls = 0, items = {} }
    local result = observation
    if action == "claim" then
        local guid, slot = fields[5], fields[6]
        if not valid_slot(slot) or not guid:match("^[0-9A-F]+$") or #guid ~= 32
            or type(SilverfishClaimNativeLootEscrow) ~= "function" then
            result.status = "native_loot_escrow_claim_unavailable"
            return "stock loot claim unavailable"
        end
        local point = positioned_claim and {X = tonumber(fields[7]), Y = tonumber(fields[8]), Z = tonumber(fields[9])} or nil
        local checked, scope = pcall(loot.claim_context, state, phase, pawn, crate, point)
        if not checked then
            result.status = tostring(scope)
            return "stock loot claim scope unavailable"
        end
        local api = StaticFindObject(statics)
        local pool = valid_object(api) and api:LoadGameFromSlot(slot, 0)
        if not valid_object(pool) then
            result.status = "native_loot_escrow_generation_missing"
            return "stock loot escrow missing"
        end
        result.distance, result.escrow_slot = scope.distance, slot
        local source = scope.profile.capacity ~= 2 and {scope.crate:GetAddress()} or {}
        local ok, status, amount, before, after = SilverfishClaimNativeLootEscrow(scope.pawn:GetAddress(),
            scope.save:GetAddress(), scope.stock:GetAddress(), pool:GetAddress(), guid, table.unpack(source))
        result.ok, result.status = ok, status
        if ok and status == "cash_credited" then
            result.cash = { guid = guid, amount = amount, before = before, after = after }
        end
        result.invoked = result.ok or tostring(result.status):match("^mutation_unverified_") ~= nil
        return "stock loot claim reported"
    end
    local inspect = action == "approach" and loot.approach_fixture or loot.qualify
    local qualified, scope = pcall(inspect, state, phase, pawn, crate, action == "inspect" or action == "seed")
    if not qualified then
        result.status = tostring(scope)
        return "stock loot unavailable"
    end
    result.distance, result.lucky, result.trait = scope.distance, scope.lucky_looter, scope.lucky_source
    if action == "seed" then
        if type(SilverfishSeedNativeLootLuckyLooter) ~= "function" then
            result.status = "native_loot_trait_fixture_unavailable"
            return "stock loot trait fixture unavailable"
        end
        result.ok, result.status = SilverfishSeedNativeLootLuckyLooter(scope.pawn:GetAddress(),
            scope.save:GetAddress(), scope.stock:GetAddress())
        result.invoked = result.ok or tostring(result.status):match("^mutation_unverified_") ~= nil
        if result.ok then
            result.lucky, result.trait = loot.lucky_looter(scope.save)
            if not result.lucky then
                result.ok, result.status = false, "mutation_unverified_lucky_looter_readback"
            end
        end
        return "stock loot trait fixture reported"
    end
    if action ~= "open" then
        result.ok, result.status = true, "ready"
        return "stock loot fixture qualified"
    end
    local slot = fields[5]
    if type(SilverfishOpenNativeLootCrate) ~= "function"
        or type(SilverfishCaptureNativeLootEscrow) ~= "function" or type(SilverfishVerifyNativeLootEscrow) ~= "function"
        or not valid_slot(slot) then
        result.status = "native_loot_crate_hook_unavailable"
        return "stock loot hook unavailable"
    end
    local fixture_args = fixture_nonempty and { true } or {}
    local reply = table.pack(SilverfishOpenNativeLootCrate(
        scope.crate:GetAddress(), scope.pawn:GetAddress(), scope.box:GetAddress(),
        scope.save:GetAddress(), scope.stock:GetAddress(), scope.lucky_looter, table.unpack(fixture_args)))
    result.ok, result.status, result.looted, result.calls, result.invoked = reply[1], reply[2], reply[3], reply[6], reply[7]
    if result.ok then
        local outputs = {}
        local verified, items = pcall(function()
            local capacity = scope.profile.capacity
            if reply.n ~= 7 + math.max(0, capacity - 2) or capacity == 1 and reply[5] ~= 0 then
                error("native_loot_unexpected_child_output", 0)
            end
            local addresses = {}
            for index = 1, capacity do
                local address = reply[index <= 2 and index + 3 or index + 5]
                if math.type(address) ~= "integer" or address < 0 then
                    error("native_loot_unverified_child_output", 0)
                end
                outputs[index] = address
                if address > 0 then addresses[#addresses + 1] = address end
            end
            return loot.results(state, phase, scope, addresses)
        end)
        local saved, problem = false, items
        if verified then saved, problem = pcall(capture, scope, slot, outputs, items) end
        if verified and saved then
            result.items = items
            result.escrow_slot = slot
        else
            result.ok, result.status = false, "mutation_unverified_" .. tostring(problem)
        end
    end
    return "stock crate opening reported"
end

function actions.reset()
    observation, placement, daily_update = nil, nil, nil
end

function actions.rows(state, phase, status_row)
    local row = observation
    local records = {}
    if phase ~= "hosting" then return records end
    if daily_update and daily_update.map == state.map then
        records[#records + 1] = status_row("native-loot-replenishment-fixture", daily_update.map,
            daily_update.crate, daily_update.days)
    end
    if placement and placement.map == state.map then
        for index, position in ipairs(placement.positions) do
            records[#records + 1] = status_row("native-loot-placement", placement.map, placement.crate,
                index, table.unpack(position))
        end
    end
    if not row or row.map ~= state.map then return records end
    records[#records + 1] = status_row("native-loot", row.action, row.map, row.crate, row.pawn, row.ok and 1 or 0,
        row.status or "", row.looted and 1 or 0, row.lucky and 1 or 0, row.trait or "", row.calls,
        row.distance or "-", #row.items, row.invoked and 1 or 0, row.escrow_slot or "-")
    if row.cash then
        local cash = row.cash
        records[#records + 1] = status_row("native-loot-cash", cash.guid, cash.amount, cash.before, cash.after)
    end
    for _, item in ipairs(row.items) do
        records[#records + 1] = status_row("native-loot-item", item.actor, item.class_path, item.guid, table.unpack(item.transform))
    end
    return records
end

return actions
