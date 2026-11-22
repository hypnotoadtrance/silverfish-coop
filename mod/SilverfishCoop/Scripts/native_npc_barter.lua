local barter = {}
local guest_save = require("guest_save")
local wire = require("native_wire_quest")

local npc_class = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local trader_class = "/Game/Classes/Actors/Barter_BP.Barter_BP_C"
local merchant_class = "/Game/Classes/Libraries/DataAssets/Merchants/MerchantData.MerchantData_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_weight = "Weight_12_11AB9ED547504E78A849A692A36DC938"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local item_never = "NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local latest

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function fail(reason) error("barter_refused_" .. reason, 0) end

local function guid(raw)
    if raw and raw.A and raw.B and raw.C and raw.D then
        return ("%08X%08X%08X%08X"):format(raw.A & 0xffffffff, raw.B & 0xffffffff, raw.C & 0xffffffff, raw.D & 0xffffffff)
    end
end

local function close_to(pawn, target)
    local max, location, point = pawn.InteractionRange, pawn:K2_GetActorLocation(), target:K2_GetActorLocation()
    return type(max) == "number" and max > 0 and max <= 1000
        and (point.X - location.X)^2 + (point.Y - location.Y)^2 + (point.Z - location.Z)^2 <= max^2
end

local function trader_identity(actor, state)
    local npc = exact(actor, trader_class) and actor.LinkedNPC
    local base = StaticFindObject(npc_class)
    if not valid(npc) or not valid(base) or npc:IsA(base) ~= true or not same(npc.BarterRef, actor)
        or valid(actor.LinkedShop) or not exact(actor.MerchantData, merchant_class)
        or npc.Alive ~= true or npc:IsActorBeingDestroyed() or actor:IsActorBeingDestroyed()
        or not same(npc:GetWorld(), state.world) or not same(actor:GetWorld(), state.world) then return end
    local id = guid(actor.TraderID)
    if id and id:find("[^0]") then return id, npc end
end

local function find_trader(state, identifier)
    local candidates = FindAllOf("Barter_BP_C") or {}
    local match, npc
    if #candidates > 512 then fail("merchant_bounds") end
    for _, actor in ipairs(candidates) do
        local id, owner = trader_identity(actor, state)
        if id == identifier then
            if match then fail("merchant_identity_ambiguous") end
            match, npc = actor, owner
        end
    end
    return match, npc
end

local function host_context(state, phase, name, identifier)
    if phase ~= "hosting" or state.server ~= false or state.listening ~= true or not valid(state.world)
        or type(name) ~= "string" or type(identifier) ~= "string" or #identifier ~= 32
        or not identifier:match("^[0-9A-F]+$") or not identifier:find("[^0]") then fail("scene") end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    local actor, npc = find_trader(state, identifier)
    if not actor or actor.Save ~= true or npc:HasAuthority() ~= true or actor:HasAuthority() ~= true then
        fail("merchant_unavailable")
    end
    local instance = state.world.OwningGameInstance
    local stock = valid(instance) and instance.SaveSlot_Ref
    local host_stack = valid(state.controller) and state.controller.MenuStack
    if host_stack then
        for index = 1, host_stack:GetArrayNum() do
            local menu = host_stack[index]
            if exact(menu, "/Game/Classes/GUI/New/BarterScreen.BarterScreen_C") and same(menu.Trader, actor) then
                fail("host_is_trading")
            end
        end
    end
    if not valid(pawn) or pawn:GetFullName() ~= name or pawn.Dead ~= false or not valid(save) or not valid(stock)
        or same(save, stock) or not same(instance.SaveSlot_Ref, stock) or not same(actor["As Game Instance FP"], instance)
        or not close_to(pawn, npc) or not close_to(pawn, actor) then fail("guest_scope") end
    local library = StaticFindObject("/Script/GameplayTags.Default__BlueprintGameplayTagLibrary")
    local ignore_tag = FName("NpcSkills.IgnoreStim", EFindName.FNAME_Find)
    local ignores_reputation = valid(library) and ignore_tag:ToString() == "NpcSkills.IgnoreStim"
        and library:HasTag(npc.NpcSkills, {TagName = ignore_tag}, true) == true
    if not ignores_reputation then
        local relation = {}
        npc:IdentifyRelation(pawn, false, relation, relation)
        local allowed = math.tointeger(relation.Relationship)
        if not allowed or allowed < 0 or allowed > 2 then fail("faction") end
    end
    return pawn, save, stock, instance, actor
end

local function preferences(actor)
    local stock = actor.SpecificPreferences
    if not stock or #stock > 256 or not math.tointeger(actor.RngSeed) then fail("preferences") end
    local result = {}
    stock:ForEach(function(raw, weight)
        local class, value = raw:get(), weight:get()
        local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass (/Game/Classes/[^|%s]+)$")
        if not path or #path > 512 or type(value) ~= "number" or not (value > 0 and value <= 100) then fail("preference") end
        result[#result + 1] = {class, path, value}
    end)
    return result, actor.RngSeed
end

local function set_preferences(actor, catalog, seed)
    local values = actor.SpecificPreferences
    actor.RngSeed = seed
    values:Empty()
    for _, row in ipairs(catalog) do values:Add(row[1], row[3]) end
    return #values == #catalog and actor.RngSeed == seed
end

local function catalog(state, pawn, instance, actor, identifier)
    local inventory = actor.Inventory
    local count = inventory and inventory:GetArrayNum()
    local wallet = wire.stock_flag(instance, "Player_Cash")
    if not math.tointeger(wallet) or wallet < 0 or not count or count < 0 or count > 128
        or type(actor.AcceptCash) ~= "boolean" then fail("catalog") end
    local items, seen = {}, {}
    for index = 1, count do
        local item = inventory[index]
        local kind = item and item[item_class]
        local key = item and guid(item[item_guid])
        local path = valid(kind) and kind:GetFullName():match("^BlueprintGeneratedClass (/Game/Classes/[^|%s]+)$")
        local weight, ammo, value, never = item[item_weight], item[item_ammo], item[item_value], item[item_never]
        if not key or key == ("0"):rep(32) or seen[key] or not path or #path > 512 or not math.tointeger(ammo)
            or not math.tointeger(value) or value < 0 or not (weight >= 0 and weight <= 100000)
            or type(never) ~= "boolean" then fail("catalog_item") end
        local quote = {}
        actor:ValueItem(item, quote)
        if type(quote.TraderValue) ~= "number" or quote.TraderValue < 0 or quote.TraderValue > 1000000
            or quote.TraderValue ~= quote.TraderValue then fail("catalog_value") end
        seen[key] = true
        items[#items + 1] = {key, path, ammo, weight, value, never and 1 or 0, quote.TraderValue}
    end
    local taste, seed = preferences(actor)
    local minimum = actor.MinWeaponValue
    if type(minimum) ~= "number" or not (minimum >= 0 and minimum <= 1000000) then fail("minimum_weapon_value") end
    return {map = state.map, trader = identifier, pawn = pawn:GetFullName(), cash = wallet, accepts = actor.AcceptCash, items = items,
        preferences = taste, seed = seed, minimum = minimum}
end


local guest_scope = require("native_save_scope")
local session
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_SHOP_VIEW") == "1"
local barter_screen = "/Game/Classes/GUI/New/BarterScreen.BarterScreen_C"
local confirmation_screen = "/Game/Classes/GUI/New/ConfirmationScreen.ConfirmationScreen_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_base = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"

local function load(path)
    local value = StaticFindObject(path)
    if not valid(value) then LoadAsset(path); value = StaticFindObject(path) end
    return value
end

local function local_context(state, phase)
    if not enabled or phase ~= "connected" or state.server ~= true then return end
    local controller = state.controller
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(controller) or not valid(pawn) or controller:IsLocalController() ~= true or pawn.Dead ~= false
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), state.world) then return end
    local stack = controller.MenuStack
    local opened = stack:GetArrayNum() == 1 and stack[1]
    local actor = session and session.actor or exact(opened, barter_screen) and opened.Trader
    local identifier, npc = trader_identity(actor, state)
    if not identifier or not (session or actor.Save == true) or not close_to(pawn, npc) or not close_to(pawn, actor) then return end
    return {actor = actor, pawn = pawn, controller = controller, name = pawn:GetFullName(),
        identifier = identifier, map = state.map}
end

local function top(context, screen)
    local stack = valid(context.controller) and context.controller.MenuStack
    return stack and stack:GetArrayNum() == 1 and same(stack[1], screen)
end

local function confirmation(context, screen)
    local stack = valid(context.controller) and context.controller.MenuStack
    if stack and stack:GetArrayNum() == 2 and same(stack[1], screen) and exact(stack[2], confirmation_screen) then
        local modal = stack[2]
        local tree = modal:GetOuter()
        if valid(tree) and same(tree:GetOuter(), screen) then return modal end
    end
end

local function copy_items(from, to)
    local count = from:GetArrayNum()
    to:Empty()
    for index = 1, count do to[index] = from[index] end
    return to:GetArrayNum() == count
end

local function close_stock(committed)
    local current = session
    if not current then return end
    if valid(current.screen) then
        current.screen:SetIsEnabled(false)
        if committed then
            current.screen.PlayerOffer:Empty()
            current.screen.TraderOffer:Empty()
        end
        local modal = confirmation(current, current.screen)
        if modal then
            modal:BndEvt__ConfirmationScreen_Button_Cancel_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
        end
        if top(current, current.screen) then current.controller:CloseMenu({}) end
        if current.screen:IsInViewport() then current.screen:RemoveFromParent() end
    end
    if valid(current.actor) and valid(current.original_stock) and current.actor.Save == false then
        copy_items(current.original_stock.PlayerBackpack, current.actor.Inventory)
        set_preferences(current.actor, current.original_preferences, current.original_seed)
        current.actor.MinWeaponValue = current.original_minimum
        current.actor.Save = true
    end
    if not committed and valid(current.save) and valid(current.original_pack) and valid(current.pawn) and same(current.pawn.Save, current.save) then
        copy_items(current.original_pack.PlayerBackpack, current.save.PlayerBackpack)
    end
    if type(SilverfishSetNativeNpcBarterScreen) == "function" then SilverfishSetNativeNpcBarterScreen(0) end
    session = nil
end

function barter.clear() close_stock(); latest = nil end

local function read_item(text)
    local id, path, ammo, weight, value, never = text:match(
        "^([0-9A-F]+)|(/Game/Classes/[%w_./%-]+_C)|(-?%d+)|([^|]+)|(%d+)|([01])$")
    local mass, rounds, price = tonumber(weight), math.tointeger(tonumber(ammo)), math.tointeger(tonumber(value))
    if not id or #id ~= 32 or not id:find("[^0]") or #path > 512 or path:find("..", 1, true)
        or not mass or not (mass >= 0 and mass <= 100000) or not rounds or rounds < -2147483648 or rounds > 2147483647
        or not price or price < 0 or price > 2147483647 then fail("view_item") end
    local class, base = load(path), load(item_base)
    if not valid(class) or not valid(base) or class:GetCDO():IsA(base) ~= true then fail("view_item_class") end
    return id, {[item_class] = class, [item_ammo] = rounds, [item_weight] = mass, [item_value] = price,
        [item_never] = never == "1", [item_guid] = {A = tonumber(id:sub(1, 8), 16), B = tonumber(id:sub(9, 16), 16),
            C = tonumber(id:sub(17, 24), 16), D = tonumber(id:sub(25, 32), 16)}}
end

local function guest_command(action, fields, state, phase)
    local context = local_context(state, phase)
    local sequence = math.tointeger(tonumber(fields[3]))
    if not session or sequence ~= session.sequence or not (session.state == "opening" or session.state == "confirming")
        then fail("view_sequence") end
    if action == "native-npc-barter-close" then
        close_stock()
        return "exact NPC barter screen closed"
    end
    if not context or not same(context.pawn, session.pawn) or not same(context.actor, session.actor)
        or fields[4] ~= session.identifier or #fields < 7 or #fields > 391 or session.expires < os.time() then fail("view_scope") end
    local cash = math.tointeger(tonumber(fields[5]))
    local seed, minimum = math.tointeger(tonumber(fields[6])), tonumber(fields[7])
    if not cash or cash < 0 or cash > 2147483647 or not seed or seed < -2147483648 or seed > 2147483647
        or not minimum or not (minimum >= 0 and minimum <= 1000000) then fail("view_wallet_and_seed") end
    local entries, observed, taste = {}, {}, {}
    for index = 8, #fields do
        local encoded = fields[index]
        if encoded:sub(1, 2) == "p|" then
            local path, weight = encoded:match("^p|(/Game/Classes/[%w_./%-]+_C)|([^|]+)$")
            local value = tonumber(weight)
            if not path or #path > 512 or path:find("..", 1, true) or not value or not (value > 0 and value <= 100)
                or observed[path] or #taste >= 256 then fail("view_preference") end
            local class, base = load(path), load(item_base)
            if not valid(class) or not valid(base) or class:GetCDO():IsA(base) ~= true then fail("view_preference_class") end
            taste[#taste + 1], observed[path] = {class, path, value}, true
        else
            local id, item = read_item(encoded)
            if observed[id] or #entries >= 128 then fail("view_duplicate") end
            entries[#entries + 1], observed[id] = item, true
        end
    end

    close_stock(session.state == "confirming")
    local save, pawn = guest_scope.bind_guest(state, phase)
    local api, class = StaticFindObject("/Script/Engine.Default__GameplayStatics"), load(save_class)
    if not valid(save) or not same(pawn, context.pawn) or not valid(api) or not valid(class)
        or context.actor.Save ~= true or context.controller.MenuStack:GetArrayNum() ~= 0 then fail("view_guest_save") end
    local original_stock, original_pack = api:CreateSaveGameObject(class), api:CreateSaveGameObject(class)
    if not valid(original_stock) or not valid(original_pack)
        or not copy_items(context.actor.Inventory, original_stock.PlayerBackpack)
        or not copy_items(save.PlayerBackpack, original_pack.PlayerBackpack) then fail("view_before_image") end
    session = context
    session.sequence, session.state, session.save = sequence, "ready", save
    session.original_stock, session.original_pack, session.expires = original_stock, original_pack, os.time() + 35
    session.original_preferences, session.original_seed = preferences(context.actor)
    session.original_minimum = context.actor.MinWeaponValue
    local okay, reason = pcall(function()
        local flag = FName("Player_Cash")
        if save.Flags:Contains(flag) then save.Flags:Remove(flag) end
        save.Flags:Add(flag, cash)
        if not save.Flags:Contains(flag) or save.Flags:Find(flag):get() ~= cash then fail("view_wallet_projection") end
        context.actor.Save = false
        context.actor.MinWeaponValue = minimum
        if not set_preferences(context.actor, taste, seed) or context.actor.MinWeaponValue ~= minimum
            then fail("view_preferences_projection") end
        local inventory = context.actor.Inventory
        inventory:Empty()
        for i, item in ipairs(entries) do
            inventory[i] = item
            if not same(inventory[i][item_class], item[item_class]) or guid(inventory[i][item_guid]) ~= guid(item[item_guid])
                or inventory[i][item_ammo] ~= item[item_ammo] then fail("view_item_projection") end
        end
        local class = load(barter_screen)
        local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(widgets) then fail("view_stock_class") end
        local screen = widgets:Create(pawn, class, context.controller)
        if not exact(screen, barter_screen) or not same(screen:GetOwningPlayer(), context.controller) then fail("view_native_owner") end
        session.screen = screen
        screen:SetIsEnabled(false)
        screen.Trader = context.actor
        context.controller:AddMenu(screen)
        if not top(context, screen) or not same(screen.Trader, context.actor)
            or SilverfishSetNativeNpcBarterScreen(screen:GetAddress(), original_stock:GetAddress(), original_pack:GetAddress()) ~= true
            then fail("view_native_screen") end
        screen:SetIsEnabled(true)
    end)
    if not okay then close_stock(); error(reason, 0) end
    return "exact stock NPC barter screen shown"
end

local function choices(items)
    local selected = {}
    local count = items and items:GetArrayNum()
    if not count or count < 0 or count > 12 then fail("view_selection_bounds") end
    for index = 1, count do
        local row = items[index]
        local class = row and row[item_class]
        local id = row and guid(row[item_guid])
        local base = valid(class) and class:GetCDO()
        local amount = valid(base) and (base.Stacks == true and math.tointeger(row[item_ammo]) or 1)
        if not id or not id:find("[^0]") or not amount or amount < 1 or amount > 1000000 then fail("view_selection") end
        selected[#selected + 1] = id .. ":" .. amount
    end
    return #selected == 0 and "-" or table.concat(selected, ",")
end

function barter.sync(state, phase)
    if not enabled or type(SilverfishSetNativeNpcBarter) ~= "function" then return end
    local current = local_context(state, phase)
    if not current then close_stock(); return end
    if session and (session.expires < os.time() or not same(current.pawn, session.pawn)
        or not same(current.actor, session.actor) or session.screen and not top(current, session.screen)
            and not confirmation(current, session.screen)) then close_stock() end
    local stack = current.controller.MenuStack
    local opened = stack:GetArrayNum() == 1 and stack[1]
    local accepting = not session and current.controller.Loading == false and exact(opened, barter_screen)
        and same(opened.Trader, current.actor)
    local address = current.pawn:GetAddress()
    if SilverfishSetNativeNpcBarter(address, current.actor:GetAddress(), accepting == true) ~= true then close_stock(); return end
    if accepting then
        local sequence = SilverfishCaptureNativeNpcBarterMenu(opened:GetAddress())
        if sequence then
            opened:SetIsEnabled(false)
            session = current
            session.sequence, session.state, session.expires = sequence, "opening", os.time() + 12
            session.screen = opened
        end
    elseif session and session.state == "ready" then
        local sequence = SilverfishTakeNativeNpcBarterConfirm(address)
        if sequence then
            local okay, give, receive = pcall(function()
                return choices(session.screen.PlayerOffer), choices(session.screen.TraderOffer)
            end)
            local cash = math.tointeger(session.screen.PlayerCashOffer)
            if not okay or not cash or cash < 0 or cash > 2147483647 then close_stock(); return end
            session.screen:SetIsEnabled(false)
            session.sequence, session.state, session.expires = sequence, "confirming", os.time() + 25
            session.give, session.receive, session.cash = give, receive, cash
        end
    end
end

function barter.command(fields, state, phase)
    local action = fields[2]
    if action == "native-npc-barter-project" or action == "native-npc-barter-close" then return guest_command(action, fields, state, phase) end
    if action ~= "native-npc-barter-open" and action ~= "native-npc-barter-exchange" then return end
    latest = nil
    local listing = action == "native-npc-barter-open"
    if #fields ~= (listing and 4 or 7) then fail("arguments") end
    local identifier = fields[4]
    local pawn, save, stock, instance, actor = host_context(state, phase, fields[3], identifier)
    if listing then
        latest = catalog(state, pawn, instance, actor, identifier)
        return "exact native NPC barter stock read"
    end
    local cash = math.tointeger(tonumber(fields[7]))
    if not cash or tostring(cash) ~= fields[7] or type(SilverfishNativeNpcBarterExchange) ~= "function" then fail("exchange_arguments") end
    local okay, detail, receipt, before, after = SilverfishNativeNpcBarterExchange(pawn:GetAddress(), save:GetAddress(),
        stock:GetAddress(), actor:GetAddress(), identifier, fields[5], fields[6], cash)
    if okay ~= true then error(detail, 0) end
    local verified, report = pcall(catalog, state, pawn, instance, actor, identifier)
    if not verified or before - after ~= cash or after ~= report.cash or detail ~= "exchanged"
        or type(receipt) ~= "string" or #receipt == 0 or #receipt > 16000 then
        error("mutation_unverified_npc_barter_receipt", 0)
    end
    latest = report
    latest.exchange, latest.before = receipt, before
    return "exact native NPC barter reconciled"
end

function barter.rows(state, phase, row)
    if session and phase == "connected" and state.map == session.map and state.local_pawn
        and same(state.local_pawn.pawn, session.pawn) then
        return {row("native-npc-barter-view", state.map, session.name, session.sequence, session.state, session.identifier,
            session.give or "-", session.receive or "-", session.cash or 0)}
    end
    if not latest or phase ~= "hosting" or state.map ~= latest.map then return {} end
    local result = {row("native-npc-barter-frame", latest.map, latest.trader, latest.pawn,
        latest.cash, latest.accepts and 1 or 0, #latest.items, latest.before or "-", latest.seed, latest.minimum)}
    for _, item in ipairs(latest.preferences) do result[#result + 1] = row("native-npc-barter-preference", item[2], item[3]) end
    for _, item in ipairs(latest.items) do result[#result + 1] = row("native-npc-barter-item", table.unpack(item)) end
    if latest.exchange then
        for line in latest.exchange:gmatch("[^\n]+") do
            local fields = {}
            for value in line:gmatch("[^|]+") do fields[#fields + 1] = value end
            if fields[1] == "reputation" then
                result[#result + 1] = row("native-npc-barter-reputation", table.unpack(fields, 2))
            else
                result[#result + 1] = row("native-npc-barter-transfer", table.unpack(fields))
            end
        end
    end
    return result
end

return barter
