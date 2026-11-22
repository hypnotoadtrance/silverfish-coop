local actions = {}
local loot = require("native_loot")
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local title_fixtures = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local observation, placement, daily_update
local escrow_cache, escrow_order = {}, {}
local pending_escrows = {}
local max_cached_escrows = 16
local statics = "/Script/Engine.Default__GameplayStatics"
local save = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"

local function valid_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function valid_object(value)
    return value and value:IsValid()
end

local function remember_escrow(slot, pool)
    for index, cached in ipairs(escrow_order) do
        if cached == slot then table.remove(escrow_order, index); break end
    end
    escrow_cache[slot] = pool
    escrow_order[#escrow_order + 1] = slot
    if #escrow_order > max_cached_escrows then escrow_cache[table.remove(escrow_order, 1)] = nil end
end

local function save_escrow(api, slot, pool, source)
    if not api:SaveGameToSlot(pool, slot, 0) then error("native_loot_escrow_write_failed", 0) end
    local restored = api:LoadGameFromSlot(slot, 0)
    local verified
    if valid_object(restored) then
        if source then
            verified = SilverfishVerifyNativeLootEscrow(pool:GetAddress(), restored:GetAddress(), table.unpack(source))
        else
            verified = SilverfishVerifyNativeStashSave(pool:GetAddress(), restored:GetAddress())
        end
    end
    if not verified then
        error("native_loot_escrow_typed_roundtrip_unverified", 0)
    end
    return restored
end

function actions.create_pending_escrow(stock, instance, world, slot)
    local class = StaticFindObject(save)
    local transient = StaticFindObject("/Engine/Transient")
    if not valid_slot(slot) or pending_escrows[slot] or not valid_object(class)
        or not valid_object(transient) or not valid_object(stock) or not valid_object(instance)
        or not valid_object(world) or type(SilverfishReferenceNativeDetachedSave) ~= "function" then
        error("native_loot_escrow_slot_unavailable", 0)
    end
    local pool = StaticConstructObject(class, transient, 0, EObjectFlags.RF_Transient | EObjectFlags.RF_MarkAsRootSet)
    if not valid_object(pool) then error("native_loot_escrow_could_not_create_save", 0) end
    local retained, reason = SilverfishReferenceNativeDetachedSave(stock:GetAddress(), pool:GetAddress(), true, true)
    if not retained then error("native_loot_escrow_memory_" .. tostring(reason), 0) end
    pending_escrows[slot] = {pool = pool, owner = instance, world = world:GetAddress()}
    return pool
end

function actions.pending_escrow(world, slot)
    local entry = pending_escrows[slot]
    if entry and valid_object(world) and entry.world == world:GetAddress() and valid_object(entry.pool) then
        return entry.pool
    end
end

local function capture(scope, slot, outputs, items, pending)
    local api, class = StaticFindObject(statics), StaticFindObject(save)
    if not valid_object(api) or not valid_object(class) or pending_escrows[slot]
        or not pending and api:DoesSaveGameExist(slot, 0) then
        error("native_loot_escrow_slot_unavailable", 0)
    end
    local pool = pending and actions.create_pending_escrow(scope.stock, scope.instance, scope.world, slot)
        or api:CreateSaveGameObject(class)
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
    local restored
    if not pending then
        local source = scope.profile.capacity ~= 2 and {scope.crate:GetAddress()} or {}
        restored = save_escrow(api, slot, pool, source)
    end
    for _, item in ipairs(items) do
        item.object:SetActorEnableCollision(false)
        item.object:SetActorHiddenInGame(true)
        if item.object:GetActorEnableCollision() ~= false or item.object.bHidden ~= true then
            error("native_loot_escrow_item_retirement_unverified", 0)
        end
        item.object:K2_DestroyActor()
    end
    if restored and #items > 0 then remember_escrow(slot, restored) end
end

local function release_escrow(slot)
    local escrow = pending_escrows[slot]
    if not escrow then return end
    if valid_object(escrow.owner) and valid_object(escrow.pool) then
        local stock = escrow.owner.SaveSlot_Ref
        if not valid_object(stock) or not SilverfishReferenceNativeDetachedSave(stock:GetAddress(), escrow.pool:GetAddress(), false) then
            error("native_loot_escrow_release_failed", 0)
        end
    end
    pending_escrows[slot] = nil
end

actions.release_escrow = release_escrow

function actions.release_pending()
    for slot in pairs(pending_escrows) do release_escrow(slot) end
end

function actions.command(fields, state, phase)
    if fields[2] == "native-loot-flush-escrows" or fields[2] == "native-loot-release-escrows" then
        if not enabled or phase ~= "hosting" or #fields < 3 or #fields > 18 then
            error("native_loot_escrow_save_scope_unavailable", 0)
        end
        local api = StaticFindObject(statics)
        for index = 3, #fields do
            local slot = fields[index]
            local escrow = pending_escrows[slot]
            if not valid_slot(slot) then error("native_loot_escrow_save_slot_invalid", 0) end
            if fields[2] == "native-loot-release-escrows" then
                release_escrow(slot)
            else
                if not escrow then error("native_loot_escrow_not_pending", 0) end
                if not valid_object(api) then error("native_loot_escrow_save_api_unavailable", 0) end
                if not valid_object(escrow.pool) then error("native_loot_escrow_object_unavailable", 0) end
                if not valid_object(state.world) or escrow.world ~= state.world:GetAddress() then
                    error("native_loot_escrow_save_world_changed", 0)
                end
                save_escrow(api, slot, escrow.pool)
            end
        end
        return "native loot escrow save command complete"
    end
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
    local pried = action == "open" and fields[6] == "stock-vehicle-pried"
    local powered = action == "open" and fields[6] == "stock-electronic-powered"
    local pending = action == "open" and not fixture_nonempty and fields[#fields] == "memory-escrow"
    local count = action == "open" and ((fixture_nonempty or pried) and 6 or 5) or action == "claim" and 6 or 4
    if powered then count = 7 end
    if pending then count = count + 1 end
    local retain = action == "claim" and fields[#fields] == "defer-save"
    local positioned_claim = action == "claim" and #fields == (retain and 10 or 9)
    if positioned_claim then count = 9 end
    if retain then count = count + 1 end
    if not enabled or phase ~= "hosting" or #fields ~= count or not crate or not loot.profile(state.map, crate) then
        error("The exact stock crate operation is unavailable", 0)
    end
    if (action == "seed" and not fixtures) or (action == "approach" and not (fixtures or title_fixtures))
        or (fixture_nonempty and (not fixtures or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1")) then
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
        local pool = pending_escrows[slot] and pending_escrows[slot].pool or escrow_cache[slot]
        if not valid_object(pool) then
            local api = StaticFindObject(statics)
            pool = valid_object(api) and api:LoadGameFromSlot(slot, 0)
        end
        if not valid_object(pool) then
            result.status = "native_loot_escrow_generation_missing"
            return "stock loot escrow missing"
        end
        remember_escrow(slot, pool)
        result.distance, result.escrow_slot = scope.distance, slot
        local source = scope.profile.capacity ~= 2 and {scope.crate:GetAddress()} or {}
        local retained, new_retention
        if retain then
            retained, new_retention = guest_save.retain(state, phase, pawn)
            if not retained then
                result.status = "native_guest_save_retention_unavailable"
                return "stock loot claim unavailable"
            end
        end
        local ok, status, amount, before, after, inventory_guid = SilverfishClaimNativeLootEscrow(scope.pawn:GetAddress(),
            scope.save:GetAddress(), scope.stock:GetAddress(), pool:GetAddress(), guid, table.unpack(source))
        result.ok, result.status = ok, status
        if ok and status == "cash_credited" then
            result.cash = { guid = guid, amount = amount, before = before, after = after }
        elseif ok and status == "merged" then
            result.merge = { guid = guid, inventory_guid = inventory_guid, amount = amount, before = before, after = after }
        end
        result.invoked = result.ok or tostring(result.status):match("^mutation_unverified_") ~= nil
        if result.invoked then result.retained = retained
        else guest_save.cancel_retention(new_retention) end
        return "stock loot claim reported"
    end
    local inspect = action == "approach" and loot.approach_fixture or loot.qualify
    -- Stock Interact already chose the hit component before the shared open was queued.
    local qualified, scope = pcall(inspect, state, phase, pawn, crate, true)
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
    local open_args = pried and {false, true} or fixture_nonempty and {true} or {}
    if powered then
        local sequence = math.tointeger(fields[7])
        if not scope.profile.electronic or not sequence or sequence <= 0 or sequence >= 2^53 then
            result.status = "stock_electronic_power_receipt_unavailable"
            return "stock electronic locker receipt unavailable"
        end
        open_args = {false, false, sequence}
    end
    local reply = table.pack(SilverfishOpenNativeLootCrate(
        scope.crate:GetAddress(), scope.pawn:GetAddress(), scope.box:GetAddress(),
        scope.save:GetAddress(), scope.stock:GetAddress(), scope.lucky_looter, table.unpack(open_args)))
    result.ok, result.status, result.looted, result.calls, result.invoked = reply[1], reply[2], reply[3], reply[6], reply[7]
    if result.ok and result.status == "unlocked" then
        if not scope.profile.key_unlock_only or reply.n ~= 7 or result.looted or not result.invoked
            or reply[4] ~= 0 or reply[5] ~= 0 or result.calls ~= 0 or scope.crate.Locked ~= false or scope.crate.Looted ~= false then
            result.ok, result.status = false, "mutation_unverified_stock_secure_locker_unlock"
        end
        return "stock key unlock reported"
    end
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
        if verified then saved, problem = pcall(capture, scope, slot, outputs, items, pending) end
        if verified and saved then
            result.items = items
            result.escrow_slot = slot
            result.memory_escrow = pending
        else
            if pending then release_escrow(slot) end
            result.ok, result.status = false, "mutation_unverified_" .. tostring(problem)
        end
    end
    return "stock crate opening reported"
end

function actions.reset()
    observation, placement, daily_update = nil, nil, nil
    escrow_cache, escrow_order = {}, {}
end

function actions.rows(state, phase, status_row)
    local row = observation
    local records = {}
    if phase ~= "hosting" then return records end
    if enabled and type(SilverfishReferenceNativeDetachedSave) == "function" then
        records[#records + 1] = status_row("native-loot-special-memory", 1)
    end
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
        row.distance or "-", #row.items, row.invoked and 1 or 0, row.escrow_slot or "-",
        row.retained and 1 or 0, row.memory_escrow and 1 or 0)
    if row.cash then
        local cash = row.cash
        records[#records + 1] = status_row("native-loot-cash", cash.guid, cash.amount, cash.before, cash.after)
    end
    if row.merge then
        local merge = row.merge
        records[#records + 1] = status_row("native-loot-stack-merge", merge.guid, merge.inventory_guid,
            merge.amount, merge.before, merge.after)
    end
    for _, item in ipairs(row.items) do
        records[#records + 1] = status_row("native-loot-item", item.actor, item.class_path, item.guid, item.ammo,
            table.unpack(item.transform))
    end
    return records
end

return actions
