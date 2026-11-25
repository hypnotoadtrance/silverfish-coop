local consumable = {}
local guest_save = require("guest_save")
local local_save = require("native_save_scope")
local use_presentation = require("native_weapon_fire_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local starter_bag = "native-guest-starter-bag"
local mail_package = "native-guest-mail-package"
local missing_contact_package = "native-guest-missing-contact-package"
local mail_classes = {
    regular = "/Game/Classes/Items/Package_Mail.Package_Mail_C",
    small = "/Game/Classes/Items/Package_Mail_Small.Package_Mail_Small_C",
}
local personal_consumable = "native-guest-personal-consumable"
local cooked_meal = "native-guest-cooked-meal"
local observation
local missing_contact = {
    quest = "Note_Farmhouse",
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    penalty = "FailPenalty_36_53777B2F407AD61DE139B3815A07F47F",
    class = "/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C",
}

local function valid(value)
    return value ~= nil and value:IsValid()
end

local function exact_guid(value)
    return type(value) == "string" and #value == 32 and not value:find("[^0-9A-F]") and value:find("[^0]")
end

local function finite(value)
    return type(value) == "number" and value == value and value > -math.huge and value < math.huge
end

local function settle_missing_contact(fields, state, pawn, detached, stock)
    local prefix = "guest_missing_contact_package_refused_"
    if type(SilverfishOpenNativeGuestMissingContactPackage) ~= "function" then error(prefix .. "addon_unavailable", 0) end
    local instance = state.world.OwningGameInstance
    if not valid(instance) or not valid(instance.SaveSlot_Ref) or instance.SaveSlot_Ref:GetAddress() ~= stock:GetAddress()
        or (fields[5] ~= "0" and fields[5] ~= "1") then error(prefix .. "scope", 0) end
    local wire = require("native_wire_quest")
    local key = FName(missing_contact.quest, EFindName.FNAME_Find)
    if key:GetComparisonIndex() == 0 or key:ToString():lower() ~= missing_contact.quest:lower() then
        error(prefix .. "quest", 0)
    end
    local function read()
        local row = wire.stock_row(stock.ActiveQuests, key)
        return {step = row and math.tointeger(row[missing_contact.step]), penalty = row and math.tointeger(row[missing_contact.penalty]),
            wallet = wire.stock_flag(instance, "Player_Cash"), xp = wire.stock_flag(instance, "Player_Xp"),
            reputation = wire.stock_flag(instance, "Rep_Freelancer")}
    end
    local scope_ok, before = pcall(read)
    if not scope_ok then error(prefix .. "quest_readback", 0) end
    if before.step ~= nil and fields[5] ~= "1" then error(prefix .. "confirmation_required", 0) end
    local called, used, status, kind, awards = pcall(SilverfishOpenNativeGuestMissingContactPackage,
        pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), fields[4])
    if called and used == false and type(status) == "string"
        and (status:match("^guest_missing_contact_package_refused_[a-z0-9_]+$")
            or status:match("^mutation_unverified_guest_missing_contact_package_[a-z0-9_]+$")) then error(status, 0) end
    if not called or used ~= true or status ~= "used" or kind ~= "sealed" or type(awards) ~= "string" then
        error("mutation_unverified_guest_missing_contact_package_contents", 0)
    end
    local after = read()
    local penalty = before.penalty or after.penalty
    if after.step ~= -7 or not penalty or after.wallet ~= before.wallet - penalty or after.xp ~= before.xp + 1
        or (before.step and after.reputation >= before.reputation or not before.step and after.reputation ~= before.reputation) then
        error("mutation_unverified_guest_missing_contact_package_quest", 0)
    end
    observation = {map = state.map, pawn = fields[3], guid = fields[4], sealed = true, awards = awards,
        step_before = before.step or "-", wallet_before = before.wallet, wallet_after = after.wallet,
        reputation_before = before.reputation, reputation_after = after.reputation}
    pcall(use_presentation.consumed, pawn, missing_contact.class, false)
    return "native guest Missing Contact package opened"
end

local function settle_personal(fields, state, pawn, detached, stock)
    local class, quantity = fields[5], math.tointeger(tonumber(fields[6]))
    local food, health, regen, stim = tonumber(fields[7]), tonumber(fields[8]), tonumber(fields[9]), tonumber(fields[10])
    local fatigue, intox, sickness = 0, 0, 0
    if #fields >= 12 then fatigue, intox = math.tointeger(fields[11]), math.tointeger(fields[12]) end
    if #fields == 13 then sickness = math.tointeger(fields[13]) end
    if type(SilverfishSettleNativeGuestPersonalConsumable) ~= "function" or type(class) ~= "string"
        or #class > 256 or not class:match("^/[%w_/%-]+%.[%w_%-]+_C$")
        or not quantity or quantity < 1 or quantity >= 2^31 or tostring(quantity) ~= fields[6]
        or not finite(food) or not finite(health) or not finite(regen) or not finite(stim)
        or not fatigue or not intox or not sickness or math.abs(fatigue) > 100 or math.abs(intox) > 100
        or math.abs(sickness) > 120 then
        error("guest_personal_consumable_refused_arguments", 0)
    end
    local called, used, status, before, after, remaining_food, remaining_health, remaining_regen, remaining_stim,
        remaining_fatigue, remaining_intox, remaining_sickness =
        pcall(SilverfishSettleNativeGuestPersonalConsumable, pawn:GetAddress(), detached:GetAddress(),
            stock:GetAddress(), fields[4], class, quantity, food, health, regen, stim, fatigue, intox, sickness)
    if called and used == false and type(status) == "string"
        and (status:match("^guest_personal_consumable_refused_[a-z0-9_]+$")
            or status:match("^mutation_unverified_guest_personal_consumable_[a-z0-9_]+$")) then error(status, 0) end
    if not called or used ~= true or status ~= "used" or before ~= quantity or after ~= quantity - 1
        or not finite(remaining_food) or not finite(remaining_health) or not finite(remaining_regen) or not finite(remaining_stim)
        or math.type(remaining_fatigue) ~= "integer" or not (0 <= remaining_fatigue and remaining_fatigue <= 100)
        or math.type(remaining_intox) ~= "integer" or not (0 <= remaining_intox and remaining_intox <= 100)
        or math.type(remaining_sickness) ~= "integer" or remaining_sickness < -2^31 or remaining_sickness >= 2^31 then
        error("mutation_unverified_guest_personal_consumable_proof", 0)
    end
    observation = {map = state.map, pawn = fields[3], guid = fields[4], before = before, after = after,
        personal_class = class, food = remaining_food, health = remaining_health, regen = remaining_regen, stim = remaining_stim,
        fatigue = remaining_fatigue, intox = remaining_intox, sickness = remaining_sickness}
    pcall(use_presentation.consumed, pawn, class, false)
    return "native guest personal consumable used"
end

function consumable.command(fields, state, phase)
    local action = fields[2]
    if action == cooked_meal then
        local name = fields[3]
        if not enabled or phase ~= "hosting" or not state.listening or #fields ~= 8
            or type(SilverfishSettleNativeGuestCookedMeal) ~= "function" then error("guest_cooked_meal_refused_scope", 0) end
        local pawn, detached = guest_save.exact_item_context(state, phase, name)
        local stock = state.local_pawn and state.local_pawn.pawn.Save
        if not valid(pawn) or not valid(detached) or not valid(stock) then error("guest_cooked_meal_refused_scope", 0) end
        local applied, food, health, regen, fatigue, intox = SilverfishSettleNativeGuestCookedMeal(
            pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), tonumber(fields[4]),
            tonumber(fields[5]), tonumber(fields[6]), math.tointeger(tonumber(fields[7])), math.tointeger(tonumber(fields[8])))
        if not applied then error("guest_cooked_meal_refused_scope", 0) end
        return ("cooked meal %.17g %.17g %.17g %d %d"):format(food, health, regen, fatigue, intox)
    end
    if action ~= "native-guest-bandage" and action ~= starter_bag and action ~= mail_package and action ~= missing_contact_package
        and action ~= personal_consumable then return end
    observation = nil
    local name, guid = fields[3], fields[4]
    local bag = action == starter_bag
    local mail = action == mail_package
    local sealed = action == missing_contact_package
    local personal = action == personal_consumable
    local prefix = personal and "guest_personal_consumable_refused_"
        or bag and "guest_starter_bag_refused_" or mail and "guest_mail_package_refused_"
        or sealed and "guest_missing_contact_package_refused_" or "guest_bandage_refused_"
    local valid_count = personal and (#fields == 10 or #fields == 12 or #fields == 13)
        or sealed and #fields == 5 or not personal and not sealed and #fields == 4
    if not enabled or not valid_count or phase ~= "hosting" or not state.listening or not exact_guid(guid) then
        error(prefix .. "scope", 0)
    end

    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    local host = state.local_pawn and state.local_pawn.pawn
    local stock = valid(host) and host.Save
    if not qualified or not valid(pawn) or not valid(detached) or not stock or not valid(stock)
        or detached:GetAddress() == stock:GetAddress() then
        error(prefix .. "exact_detached_save", 0)
    end
    if personal then return settle_personal(fields, state, pawn, detached, stock) end
    if sealed then return settle_missing_contact(fields, state, pawn, detached, stock) end
    if mail then
        if type(SilverfishOpenNativeGuestMailPackage) ~= "function" then error(prefix .. "addon_unavailable", 0) end
        local called, used, status, kind = pcall(SilverfishOpenNativeGuestMailPackage,
            pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), guid)
        if called and used == false and type(status) == "string"
            and (status:match("^guest_mail_package_refused_[a-z0-9_]+$")
                or status:match("^mutation_unverified_guest_mail_package_[a-z0-9_]+$")) then error(status, 0) end
        if not called or used ~= true or status ~= "used" or not mail_classes[kind] then
            error("mutation_unverified_guest_mail_package_proof", 0)
        end
        observation = {map = state.map, pawn = name, guid = guid, mail_class = mail_classes[kind]}
        pcall(use_presentation.consumed, pawn, observation.mail_class, false)
        return "native guest mail package opened"
    end

    local native_use = bag and SilverfishOpenNativeGuestStarterBag or SilverfishUseNativeGuestBandage
    if type(native_use) ~= "function" or bag and native_use ~= SilverfishOpenNativeGuestStarterBag then
        error(prefix .. "addon_unavailable", 0)
    end
    local called, used, status, before, after, regen_before, regen_after, hp_before, hp_after, maximum, trait =
        pcall(native_use, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), guid)
    if bag then
        if called and used == false and type(status) == "string"
            and (status:match("^guest_starter_bag_refused_[a-z0-9_]+$") or
                status:match("^mutation_unverified_guest_starter_bag_[a-z0-9_]+$")) then error(status, 0) end
        if not called or used ~= true or status ~= "used" or before ~= "random" and before ~= "hyperrandom"
            or type(after) ~= "string" or #after > 527 or after:sub(-1) == "," then
            error("mutation_unverified_guest_starter_bag_proof", 0)
        end
        local seen, count = {[guid] = true}, 0
        for reward in (after .. ","):gmatch("([^,]*),") do
            if not exact_guid(reward) or seen[reward] then error("mutation_unverified_guest_starter_bag_rewards", 0) end
            seen[reward], count = true, count + 1
        end
        if count < 1 or count > 16 then error("mutation_unverified_guest_starter_bag_rewards", 0) end
        observation = {map = state.map, pawn = name, guid = guid, kind = before, awards = after}
        return "native guest starter bag opened"
    end
    if not called then error("mutation_unverified_guest_bandage_call", 0) end
    if used == false and type(status) == "string"
        and (status:match("^guest_bandage_refused_[a-z0-9_]+$") or
            status:match("^mutation_unverified_guest_bandage_[a-z0-9_]+$")) then
        error(status, 0)
    end
    if used ~= true or status ~= "used" or math.type(before) ~= "integer" or before < 1
        or math.type(after) ~= "integer" or after ~= before - 1 or not finite(maximum) or maximum < 25
        or not finite(regen_before) or regen_before < 0 or regen_before > maximum
        or not finite(regen_after) or regen_after < 25 or regen_after > maximum
        or not finite(hp_before) or hp_before <= 0 or hp_before > maximum or hp_after ~= hp_before
        or type(trait) ~= "boolean" then
        error("mutation_unverified_guest_bandage_proof", 0)
    end
    observation = {
        map = state.map, pawn = name, guid = guid, before = before, after = after,
        regen_before = regen_before, regen_after = regen_after, hp_before = hp_before, hp_after = hp_after,
        max_health = maximum, hemophilia = trait,
    }
    pcall(use_presentation.consumed, pawn, "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", false)
    return "native guest Bandage used"
end

function consumable.sync(state, phase)
    if not enabled or type(SilverfishPollNativeGuestCookedMeal) ~= "function"
        or type(SilverfishEnqueueLocalBridgeInput) ~= "function" then return end
    if not local_save.guest_vitals_save(state, phase) then
        SilverfishPollNativeGuestCookedMeal()
        return
    end
    local owner = state.local_pawn
    for _ = 1, 8 do
        local sequence, food, health, regen, fatigue, intox =
            SilverfishPollNativeGuestCookedMeal(owner.pawn:GetAddress(), owner.pawn.Save:GetAddress())
        if not sequence then return end
        local record = ("%s\t%d\t%s\t%s\t%.17g,%.17g,%.17g,%d,%d\n"):format(
            cooked_meal, sequence, state.map, owner.name, food, health, regen, fatigue, intox)
        if not SilverfishEnqueueLocalBridgeInput(record) then return end
    end
end

function consumable.reset()
    observation = nil
end

function consumable.rows(state, phase, status_row)
    local row = observation
    if not enabled or phase ~= "hosting" or not state.listening or not row or row.map ~= state.map then return {} end
    if row.personal_class then
        return {status_row(personal_consumable, "used", row.map, row.pawn, row.guid, row.personal_class, row.before, row.after,
            string.format("%.17g", row.food), string.format("%.17g", row.health),
            string.format("%.17g", row.regen), string.format("%.17g", row.stim), row.fatigue, row.intox, row.sickness)}
    end
    if row.kind then
        return {status_row(starter_bag, "used", row.map, row.pawn, row.guid, row.kind, row.awards)}
    end
    if row.mail_class then
        return {status_row(mail_package, "used", row.map, row.pawn, row.guid, row.mail_class)}
    end
    if row.sealed then
        return {status_row(missing_contact_package, "used", row.map, row.pawn, row.guid, row.awards,
            row.step_before, row.wallet_before, row.wallet_after, row.reputation_before, row.reputation_after)}
    end
    return {
        status_row("native-guest-bandage", "used", row.map, row.pawn, row.guid, row.before, row.after,
            string.format("%.17g", row.regen_before), string.format("%.17g", row.regen_after),
            string.format("%.17g", row.hp_before), string.format("%.17g", row.hp_after),
            string.format("%.17g", row.max_health), row.hemophilia and 1 or 0),
    }
end

return consumable
