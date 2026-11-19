local actions = {}
local loot = require("native_loot")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local observation
local statics = "/Script/Engine.Default__GameplayStatics"
local save = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"

local function valid_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function valid_object(value)
    return value and value:IsValid()
end

local function capture(scope, slot, first, second, items)
    local api, class = StaticFindObject(statics), StaticFindObject(save)
    if not valid_object(api) or not valid_object(class) or api:DoesSaveGameExist(slot, 0) then
        error("native_loot_escrow_slot_unavailable", 0)
    end
    local pool = api:CreateSaveGameObject(class)
    if not valid_object(pool) then error("native_loot_escrow_could_not_create_save", 0) end
    local ok, reason, count, first_guid, second_guid = SilverfishCaptureNativeLootEscrow(
        scope.crate:GetAddress(), pool:GetAddress(), first, second)
    if not ok or count ~= #items then error(reason or "native_loot_escrow_capture_unverified", 0) end
    if not api:SaveGameToSlot(pool, slot, 0) then error("native_loot_escrow_write_failed", 0) end
    local restored = api:LoadGameFromSlot(slot, 0)
    if not valid_object(restored) or not SilverfishVerifyNativeLootEscrow(pool:GetAddress(), restored:GetAddress()) then
        error("native_loot_escrow_typed_roundtrip_unverified", 0)
    end
    local index = 1
    for _, guid in ipairs({ first_guid, second_guid }) do
        if #guid > 0 then
            local item = items[index]
            if not item or not valid_object(item.object) then error("native_loot_escrow_item_unverified", 0) end
            item.guid = guid
            item.object:SetActorEnableCollision(false)
            item.object:SetActorHiddenInGame(true)
            if item.object:GetActorEnableCollision() ~= false or item.object.bHidden ~= true then
                error("native_loot_escrow_item_retirement_unverified", 0)
            end
            item.object:K2_DestroyActor()
            index = index + 1
        end
    end
    if index - 1 ~= #items then error("native_loot_escrow_item_identity_count_unverified", 0) end
end

function actions.command(fields, state, phase)
    local action = fields[2]:match("^native%-loot%-(%l+)$")
    if action ~= "inspect" and action ~= "open" and action ~= "approach" and action ~= "seed" and action ~= "claim" then
        return
    end
    local pawn, crate = fields[3], fields[4]
    local fixture_nonempty = action == "open" and fields[6] == "fixture-nonempty"
    local count = action == "open" and (fixture_nonempty and 6 or 5) or action == "claim" and 6 or 4
    if not enabled or phase ~= "hosting" or #fields ~= count or crate ~= loot.crate or state.map ~= loot.map then
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
        local checked, scope = pcall(loot.claim_context, state, phase, pawn, crate)
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
        result.ok, result.status = SilverfishClaimNativeLootEscrow(scope.pawn:GetAddress(), scope.save:GetAddress(),
            scope.stock:GetAddress(), pool:GetAddress(), guid)
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
    local ok, status, looted, first, second, calls, invoked = SilverfishOpenNativeLootCrate(
        scope.crate:GetAddress(), scope.pawn:GetAddress(), scope.box:GetAddress(),
        scope.save:GetAddress(), scope.stock:GetAddress(), scope.lucky_looter, table.unpack(fixture_args))
    result.ok, result.status, result.looted, result.calls, result.invoked = ok, status, looted, calls, invoked
    if ok then
        local addresses = {}
        if first > 0 then addresses[#addresses + 1] = first end
        if second > 0 then addresses[#addresses + 1] = second end
        local verified, items = pcall(loot.results, state, phase, scope, addresses)
        local saved, problem = false, items
        if verified then saved, problem = pcall(capture, scope, slot, first, second, items) end
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
    observation = nil
end

function actions.rows(state, phase, status_row)
    local row = observation
    if not row or phase ~= "hosting" or row.map ~= state.map then return {} end
    local records = { status_row("native-loot", row.action, row.map, row.crate, row.pawn, row.ok and 1 or 0,
        row.status or "", row.looted and 1 or 0, row.lucky and 1 or 0, row.trait or "", row.calls,
        row.distance or "-", #row.items, row.invoked and 1 or 0, row.escrow_slot or "-") }
    for _, item in ipairs(row.items) do
        records[#records + 1] = status_row("native-loot-item", item.actor, item.class_path, item.guid)
    end
    return records
end

return actions
