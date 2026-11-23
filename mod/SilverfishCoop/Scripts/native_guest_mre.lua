local mre = {}
local guest_save = require("guest_save")
local use_presentation = require("native_weapon_fire_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MRE") == "1"
local headless_test = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"
local meals, labels = {}, {}
local function register_meal(slug, asset, label, food, gain, infection, fatigue, medicine)
    local class = string.format("/Game/Classes/Items/Item_%s.Item_%s_C", asset, asset)
    labels[class] = label
    meals["native-guest-" .. slug] = {kind = slug:gsub("-", "_"), entry = "use-" .. slug,
        class = class, food = food, gain = gain or 0, infection = infection or 0, fatigue = fatigue, medicine = medicine}
end
register_meal("mre", "MRE", "MRE", 30, 10)
register_meal("canned-peaches", "CannedPeaches", "Canned Peaches", 12)
register_meal("canned-beans", "CannedBeans", "Canned Beans", 14)
register_meal("brown-mushroom", "BrownMushroom", "Brown Mushroom", 3.5)
register_meal("canned-beef", "CannedBeef", "Canned Beef", 16, 0, 8)
register_meal("honey-egg", "HoneyEgg", "Clyde's Honey Egg", 5, 0, 0, 5)
register_meal("leftovers", "Leftovers", "Leftovers", 18)
register_meal("canned-soup", "CannedSoup", "Canned Soup", 10, 0, 3)
register_meal("canned-tuna", "CannedTuna", "Canned Tuna", 6, 0, 4)
register_meal("dead-beetle", "DeadBeetle", "Dead Beetle", 3, 0, 3)
register_meal("dog-meat", "DogMeat", "Dog Meat", 5, 0, 40)
register_meal("milk-bites", "MilkBites", "Milk Bites", 4)
register_meal("pork-ribs", "PorkRibs", "Pork Ribs", 22, 0, 25)
register_meal("sausage", "Sausage", "Sausage", 10, 0, 20)
register_meal("venison", "Venison", "Venison", 13, 0, 25)
register_meal("dirty-bandage", "DirtyBandage", "Dirty Bandage", 0, 45, 5, nil, {instant = 0, stim = 0})
register_meal("gauze", "Gauze", "Gauze", 0, 60, 0, nil, {instant = 25, stim = 0})
register_meal("medical-salve", "MedSalve", "Medical Salve", 0, 30, -5, nil, {instant = 20, stim = 3})
register_meal("large-medkit", "MedKit_Large", "Large Medkit", 0, 100, -5, nil, {instant = 50, stim = 4})
register_meal("stim-pills", "StimPills", "Stim Pills", 0, 0, -5, nil, {instant = 0, stim = 4})
register_meal("antibiotics", "Antibiotics", "Antibiotics", 0, 0, -100, nil, {instant = 0, stim = 0, antibiotic = true})
function mre.label(class) return labels[class] end

local observation, fixture, ready

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function exact_guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end
local function bounded(number, minimum, maximum)
    return type(number) == "number" and number >= minimum and number <= maximum
end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function check_wound_fixture(state, pawn, detached)
    local world = state.world
    if not headless_test or not valid(world) or world:GetFullName() ~= "World " .. borough
        or not same(pawn:GetWorld(), world) or not pawn:HasAuthority() or pawn.Dead ~= false
        or pawn.MaxHealth ~= 250 or detached.PlayerHealth ~= 250 or detached.PlayerRegen ~= 250 then
        error("guest_mre_refused_fixture_wound_baseline", 0)
    end
end

function mre.sync(state, phase)
    if not ready and enabled and phase == "hosting" and state.server == false and state.listening
        and type(SilverfishPrepareNativeGuestMre) == "function" then
        ready = SilverfishPrepareNativeGuestMre() == true
    end
end

function mre.reset() observation, fixture = nil, nil end

function mre.command(fields, state, phase)
    local verb = fields[2]
    local test, meal = verb == "native-guest-mre-fixture", meals[verb]
    if not test and not meal then return end
    local kind = meal and meal.kind or "mre"
    local refused, unverified = "guest_" .. kind .. "_refused_", "mutation_unverified_guest_" .. kind .. "_"
    local use = meal and SilverfishUseNativeGuestMeal
    if test then fixture = nil else observation = nil end
    local name, guid = fields[3], fields[4]
    if not enabled or phase ~= "hosting" or state.server ~= false or not state.listening
        or (test and (#fields ~= 3 or type(SilverfishSeedNativeGuestMreFixture) ~= "function"))
        or (not test and (#fields ~= 4 or not exact_guid(guid) or type(use) ~= "function")) then
        error(refused .. "scope", 0)
    end
    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    local host = state.local_pawn and state.local_pawn.pawn
    local stock = valid(host) and host.Save
    if not qualified or not valid(pawn) or pawn:GetFullName() ~= name or not valid(detached)
        or not valid(stock) or detached:GetAddress() == stock:GetAddress() then
        error(refused .. "exact_detached_save", 0)
    end
    if test then
        check_wound_fixture(state, pawn, detached)
        local host_health, host_regen = stock.PlayerHealth, stock.PlayerRegen
        local hp_before, regen_before = detached.PlayerHealth, detached.PlayerRegen
        local invoked, ok, status, food, intox, stock_intox = pcall(
            SilverfishSeedNativeGuestMreFixture, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress())
        if invoked and ok == false and type(status) == "string" and status:match("^guest_mre_refused_[a-z0-9_]+$") then
            error(status, 0)
        end
        if not invoked or ok ~= true or status ~= "verified" or food ~= 17 or intox ~= 42
            or math.type(stock_intox) ~= "integer" or not bounded(stock_intox, 0, 100) then
            error("mutation_unverified_guest_mre_fixture", 0)
        end
        local hp_after, regen_after = detached.PlayerHealth, detached.PlayerRegen
        if hp_after ~= 210 or regen_after ~= 230 or pawn.Dead ~= false or not same(pawn.Save, detached)
            or not same(host.Save, stock) or stock.PlayerHealth ~= host_health or stock.PlayerRegen ~= host_regen then
            error("mutation_unverified_guest_mre_fixture_wound_baseline", 0)
        end
        local effects = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
        local character = detached.Character
        character[effects] = {FName("Gourmand"), FName("HealthyEating"), FName("Hemophilia")}
        detached.Character = character
        local stored = detached.Character[effects]
        if stored[1]:ToString() ~= "Gourmand" or stored[2]:ToString() ~= "HealthyEating"
            or stored[3]:ToString() ~= "Hemophilia" then
            error("mutation_unverified_guest_mre_fixture_traits", 0)
        end
        local tuna_path = meals["native-guest-canned-tuna"].class
        local tuna = StaticFindObject(tuna_path)
        if not valid(tuna) then LoadAsset(tuna_path); tuna = StaticFindObject(tuna_path) end
        local factory = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
        if not valid(tuna) or not valid(factory) then error("mutation_unverified_guest_mre_fixture_factory", 0) end
        local generated = {}
        factory:MakeItemStack(tuna, 2, pawn, generated)
        local item = generated.Item or generated
        if not same(item.Item_Class_3_2813641E42AA35FC0693029E4EE49F81, tuna)
            or item.Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788 ~= 2 then
            error("mutation_unverified_guest_mre_fixture_tuna", 0)
        end
        detached.PlayerBackpack[detached.PlayerBackpack:GetArrayNum() + 1] = item
        fixture = {map = state.map, name = name, food = food, intox = intox, stock_intox = stock_intox,
            hp_before = hp_before, hp_after = hp_after, regen_before = regen_before, regen_after = regen_after}
        return "isolated guest MRE wound and food baseline verified"
    end
    local called, used, status, before, after, food_before, food_after, regen_before, regen_after,
        health, maximum, intox_before, intox_after, host_intox, trait, gourmand, healthy_eating,
        sickness_before, sickness_after, dose, effect_first, effect_second, effect_third, resistance_before, resistance_after =
        pcall(use, pawn:GetAddress(), detached:GetAddress(), stock:GetAddress(), guid, meal.entry)
    if not called then error(unverified .. "call", 0) end
    if used == false and type(status) == "string" and (status:match("^" .. refused .. "[a-z0-9_]+$")
        or status:match("^" .. unverified .. "[a-z0-9_]+$")) then error(status, 0) end
    if math.type(gourmand) ~= "integer" or not bounded(gourmand, 0, 1025)
        or math.type(healthy_eating) ~= "integer" or not bounded(healthy_eating, 0, 1025) then
        error(unverified .. "food_traits", 0)
    end
    local medicine, nominal_dose = meal.medicine, meal.infection
    if medicine and medicine.antibiotic then
        if math.type(resistance_before) ~= "integer" or not bounded(resistance_before, 0, 2147483644)
            or math.type(resistance_after) ~= "integer" or not bounded(resistance_after - resistance_before, 1, 3) then
            error(unverified .. "antibiotic_resistance_proof", 0)
        end
        nominal_dose = nominal_dose + resistance_before
    end
    if math.type(sickness_before) ~= "integer" or math.type(sickness_after) ~= "integer" or math.type(dose) ~= "integer"
        or not bounded(sickness_before, -2147483648, 2147483647) or not bounded(sickness_after, -2147483648, 2147483647)
        or (dose ~= nominal_dose and (dose ~= 0 or nominal_dose < 0))
        or (dose == 0 and sickness_after ~= sickness_before)
        or (dose > 0 and not bounded(sickness_after - sickness_before, 1, dose))
        or (dose < 0 and sickness_after ~= sickness_before + (sickness_before > 0 and dose or 0)) then
        error(unverified .. "infection_proof", 0)
    end
    local fatigue_before, fatigue_after, fatigue_minimum
    if meal.fatigue then fatigue_before, fatigue_after, fatigue_minimum = effect_first, effect_second, effect_third end
    if meal.fatigue and (math.type(fatigue_before) ~= "integer" or not bounded(fatigue_before, 0, 100)
        or (fatigue_minimum ~= 0 and fatigue_minimum ~= 15)
        or fatigue_after ~= math.min(math.max(fatigue_before + meal.fatigue, fatigue_minimum), 100)) then
        error(unverified .. "fatigue_proof", 0)
    end
    local appetite = meal.food * (1 - gourmand * 0.2)
    local gain = meal.gain + healthy_eating * appetite * 0.3
    if trait and gain > 0 then gain = gain * 0.6 end
    local instant = medicine and medicine.instant * (trait and 0.6 or 1) or 0
    if used ~= true or status ~= "used" or math.type(before) ~= "integer" or before < 1
        or math.type(after) ~= "integer" or after ~= before - 1 or not bounded(maximum, 25, 10000)
        or not bounded(health, 0, maximum) or health == 0 or not bounded(food_before, 0, 200)
        or food_after ~= math.min(math.max(food_before + appetite, 0), 200) or not bounded(regen_before, 0, maximum)
        or type(trait) ~= "boolean"
        or regen_after ~= math.min(math.max(math.min(regen_before + instant, maximum) + gain, 25), maximum)
        or math.type(intox_before) ~= "integer" or not bounded(intox_before, 0, 100)
        or math.type(intox_after) ~= "integer" or intox_after ~= math.min(math.max(intox_before + math.modf(appetite * -0.1), 0), 100)
        or math.type(host_intox) ~= "integer" or not bounded(host_intox, 0, 100) then
        error(unverified .. "proof", 0)
    end
    if medicine and (effect_first ~= math.min(health + instant, maximum) or not bounded(effect_second, 0, math.huge)
        or effect_third ~= math.max(effect_second, medicine.stim)) then error(unverified .. "medicine_proof", 0) end
    observation = {verb = verb, map = state.map, name = name, guid = guid, before = before, after = after,
        food_before = food_before, food_after = food_after, regen_before = regen_before, regen_after = regen_after,
        health = health, maximum = maximum, intox_before = intox_before, intox_after = intox_after,
        host_intox = host_intox, trait = trait, gourmand = gourmand, healthy_eating = healthy_eating,
        sickness_before = sickness_before, sickness_after = sickness_after, dose = dose,
        fatigue_before = fatigue_before, fatigue_after = fatigue_after, fatigue_minimum = fatigue_minimum,
        health_after = medicine and effect_first, stim_before = medicine and effect_second, stim_after = medicine and effect_third,
        resistance_before = resistance_before, resistance_after = resistance_after}
    pcall(use_presentation.consumed, pawn, meal.class, false)
    return "exact stock guest consumable effects verified"
end

function mre.rows(state, phase, status_row)
    local row = observation
    if not enabled or phase ~= "hosting" or state.server ~= false or not state.listening then return {} end
    local rows = {}
    if fixture and fixture.map == state.map then
        rows[#rows + 1] = status_row("native-guest-mre-fixture", fixture.map, fixture.name, fixture.food, fixture.intox,
            fixture.stock_intox, fixture.hp_before, fixture.hp_after, fixture.regen_before, fixture.regen_after)
    end
    local function real(value) return string.format("%.17g", value) end
    if row and row.map == state.map then
        local effects = row.health_after and {real(row.health_after), real(row.stim_before), real(row.stim_after),
            row.resistance_before, row.resistance_after} or {row.fatigue_before, row.fatigue_after, row.fatigue_minimum}
        rows[#rows + 1] = status_row(row.verb, "used", row.map, row.name, row.guid, row.before, row.after,
            real(row.food_before), real(row.food_after), real(row.regen_before), real(row.regen_after),
            real(row.health), real(row.maximum), row.intox_before, row.intox_after, row.host_intox, row.trait and 1 or 0,
            row.gourmand, row.healthy_eating, row.sickness_before, row.sickness_after, row.dose,
            table.unpack(effects))
    end
    return rows
end

return mre
