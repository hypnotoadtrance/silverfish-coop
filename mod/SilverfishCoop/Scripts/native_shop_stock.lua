local stock = {}
local content_paths = require("content_paths")
local scope = require("native_save_scope")
local shared_wallet = require("wallet")
local screen_path = "/Game/Classes/GUI/New/ShopScreen.ShopScreen_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_base = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_weight = "Weight_12_11AB9ED547504E78A849A692A36DC938"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local item_never_drop = "NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local sides = {"shelf", "limited", "buyback"}
local cards = {shelf = "Cards_Infinite", limited = "Cards_Limited", buyback = "Cards_BuyBack"}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function load(path)
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    return valid(class) and class or nil
end

local function identity(item)
    local id = item and item[item_guid]
    if not id then return end
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

function stock.available()
    return scope.selected() ~= nil and type(SilverfishSetNativeShopScreen) == "function"
        and type(SilverfishReplaceNativeShopPreview) == "function"
end

function stock.present(session, top_only)
    local controller, screen = session.context.controller, session.screen
    if not valid(controller) or not valid(screen) then return false end
    local stack = controller.MenuStack
    local count = stack:GetArrayNum()
    for index = top_only and count or 1, count do
        if index > 0 and same(stack[index], screen) then return true end
    end
    return false
end

function stock.close(session)
    if not session or not session.screen then return end
    local controller = session.context.controller
    while stock.present(session) do
        local before = controller.MenuStack:GetArrayNum()
        controller:CloseMenu({})
        if controller.MenuStack:GetArrayNum() >= before then return end
    end
    SilverfishClearNativeStashScreen()
    session.screen = nil
end

local function integer(value, low, high)
    local number = math.tointeger(tonumber(value))
    if not number or number < low or number > high then error("Invalid stock merchant number", 0) end
    return number
end

local function read_rows(path, expected)
    local file = io.open(path, "rb")
    if not file then error("The authenticated merchant rows are unavailable", 0) end
    local data = file:read(90001) or ""
    file:close()
    if #data > 90000 or #data > 0 and data:sub(-1) ~= "\n" then error("The merchant rows exceed their bounds", 0) end
    local rows, total, seen = {shelf = {}, limited = {}, buyback = {}}, 0, {}
    for line in data:gmatch("([^\n]+)\n") do
        local guid, class, source, price, value, ammo, weight, never_drop, markup = line:match(
            "^([0-9A-F]+)\t([^\t]+)\t(%l+)\t(%d+)\t(%d+)\t(-?%d+)\t(%S+)\t([01])\t?(%S*)$")
        local kilograms = tonumber(weight)
        local exact_markup = tonumber(markup)
        total = total + 1
        if total > expected or not guid or #guid ~= 32 or not guid:find("[^0]") or seen[guid] or not rows[source]
            or not content_paths.item_class(class, 512) or not kilograms or not (kilograms >= 0 and kilograms <= 100000)
            or (markup ~= "" and (not exact_markup or not (exact_markup >= 0 and exact_markup < 1e8))) then
            error("Invalid authenticated merchant row", 0)
        end
        local type, base = load(class), load(item_base)
        if not valid(type) or not valid(base) or not type:GetCDO():IsA(base) then error("Invalid stock merchant class", 0) end
        local amount, total_price = integer(value, 0, 2147483647), integer(price, 0, 2147483647)
        local item = {[item_class] = type, [item_value] = amount, [item_ammo] = integer(ammo, -2147483648, 2147483647),
            [item_weight] = kilograms, [item_never_drop] = never_drop == "1",
            [item_guid] = {A = tonumber(guid:sub(1, 8), 16), B = tonumber(guid:sub(9, 16), 16),
                C = tonumber(guid:sub(17, 24), 16), D = tonumber(guid:sub(25), 16)}}
        if exact_markup and math.ceil(exact_markup * amount) ~= total_price then error("Invalid stock merchant markup", 0) end
        local row = {guid = guid, item = item, value = amount, price = total_price, source = source, markup = exact_markup}
        rows[source][#rows[source] + 1], seen[guid] = row, row
    end
    if total ~= expected then error("The merchant catalog is incomplete", 0) end
    return rows, seen
end

local function rebuild_stock(panel)
    for index, side in ipairs(sides) do
        panel[cards[side]]:Empty()
        panel:LayoutCards(index - 1)
    end
    panel:FullRefresh()
end

local function idle(screen)
    local manager = screen.ItemManager
    local menu = valid(manager) and manager.ItemContextMenu
    return valid(manager) and manager.HoldingItem == false and manager.IsTakingItem == false
        and manager.IsSplittingStack == false and (not valid(menu) or not menu:IsVisible())
end

function stock.idle(session)
    return stock.present(session, true) and idle(session.screen)
end

function stock.refresh_host(controller, actor)
    if not valid(controller) then return true end
    local stack = controller.MenuStack
    local count = stack:GetArrayNum()
    if count > 32 then return false end
    for index = count, 1, -1 do
        local screen = stack[index]
        if valid(screen) and same(screen:GetClass(), StaticFindObject(screen_path))
            and same(screen:GetOwningPlayer(), controller) and same(screen.Shop, actor)
            and valid(screen.ShopPanel) and same(screen.ShopPanel.Target, actor) then
            if index ~= count or not idle(screen) then return false end
            rebuild_stock(screen.ShopPanel)
            return true
        end
    end
    -- A reopened menu reads the already-updated authoritative actor.
    return true
end

function stock.render(session, actor, state, phase, wallet, path, total)
    local rows, offers = read_rows(path, total)
    local save, pawn = scope.bind_guest(state, phase)
    local controller = session.context.controller
    local api, class, widgets, widget_class = StaticFindObject(statics_path), load(save_path), StaticFindObject(widgets_path), load(screen_path)
    if not valid(save) or not same(pawn, session.context.pawn) or not valid(actor) or not valid(api)
        or not valid(class) or not valid(widgets) or not valid(widget_class) then
        error("The native merchant cannot bind the isolated guest", 0)
    end
    if not shared_wallet.project_guest(save, wallet) then error("The shared cash did not project", 0) end

    local first, frames, backups = not session.screen, {}, {}
    for i, side in ipairs(sides) do
        local frame = api:CreateSaveGameObject(class)
        if not valid(frame) then error("The merchant projection frame could not be created", 0) end
        frame.PlayerBackpack:Empty()
        for index, row in ipairs(rows[side]) do frame.PlayerBackpack[index] = row.item end
        frames[i] = frame
        backups[i] = first and api:CreateSaveGameObject(class) or false
    end
    local screen = session.screen
    if first then
        if controller.MenuStack:GetArrayNum() ~= 0 then error("Another menu is active", 0) end
        screen = widgets:Create(pawn, widget_class, controller)
        if not valid(screen) or not same(screen:GetOwningPlayer(), controller) then error("The game's shop screen is unavailable", 0) end
        session.screen, screen.Shop = screen, actor
        controller:AddMenu(screen)
    elseif screen.ItemManager.IsSplittingStack == true then
        screen.ItemManager:CancelSplitStack()
    end
    local address = screen:GetAddress()
    local inputs = {address}
    for i = 1, #sides do
        inputs[#inputs + 1] = frames[i]:GetAddress()
        inputs[#inputs + 1] = valid(backups[i]) and backups[i]:GetAddress() or 0
    end
    if not stock.present(session, true) or not same(screen.ShopPanel.Target, actor)
        or not SilverfishSetNativeShopScreen(address, false) or not SilverfishReplaceNativeShopPreview(table.unpack(inputs)) then
        error("The exact stock merchant screen could not bind its catalog", 0)
    end
    rebuild_stock(screen.ShopPanel)
    screen.BackpackPanel:FullRefresh()
    for _, side in ipairs(sides) do
        local displayed = screen.ShopPanel[cards[side]]
        if displayed:GetArrayNum() ~= #rows[side] then
            error(("The stock merchant %s category shows %d of %d rows"):format(side, displayed:GetArrayNum(), #rows[side]), 0)
        end
        local rendered = {}
        for index = 1, displayed:GetArrayNum() do
            local card = displayed[index]
            local item = valid(card) and card.Item
            local guid = identity(item)
            local row = guid and offers[guid]
            if not row or row.source ~= side or rendered[guid] or not same(item[item_class], row.item[item_class])
                or item[item_ammo] ~= row.item[item_ammo] or item[item_value] ~= row.value then
                error("A native merchant card did not match the host's stock", 0)
            end
            rendered[guid] = true
            if row.markup and card.ShopMarkup ~= row.markup then
                card.ShopMarkup = row.markup
                card:FillInContents()
            elseif math.ceil(card.ShopMarkup * row.value) ~= row.price then
                card.ShopMarkup = row.price > 0 and row.value > 0 and (row.price - 0.25) / row.value or 0
                card:FillInContents()
            end
            local label = "$" .. row.price
            card.Value:SetText(FText(label))
            card:PriceCheck(wallet)
            if math.ceil(card.ShopMarkup * row.value) ~= row.price or card.Value:GetText():ToString() ~= label then
                error("A native merchant card changed the quote", 0)
            end
        end
    end
    if not SilverfishSetNativeShopScreen(address, true) then error("The native merchant action guard did not arm", 0) end
    return offers
end

function stock.take(session)
    return SilverfishTakeNativeStashMove(session.screen:GetAddress())
end

function stock.move_fixture(session, side, wanted, gesture, amount)
    local screen, group = session.screen, cards[side]
    local rows = group and screen.ShopPanel[group] or screen.BackpackPanel.Cards
    for index = 1, rows:GetArrayNum() do
        local card = rows[index]
        if valid(card) and identity(card.Item) == wanted then
            local count, manager = rows:GetArrayNum(), screen.ItemManager
            if group then
                local popup = manager.SplitStackPopup
                local already_open = manager.IsSplittingStack == true and valid(popup) and same(popup.ItemCard, card)
                if not already_open then
                    manager:OpenContextMenu(card)
                    local menu = manager.ItemContextMenu
                    if not valid(menu) or not same(menu["Item Card"], card) then error("The stock Buy menu did not select the card", 0) end
                end
                if card.Item[item_class]:GetCDO().Stacks and (card.IsInfinite or card.Item[item_ammo] > 1) then
                    if not already_open then manager:ActionSplitStack() end
                    popup = manager.SplitStackPopup
                    if not valid(popup) or manager.IsSplittingStack ~= true or not same(popup.ItemCard, card) then
                        error("The stock Buy menu did not open the quantity popup", 0)
                    end
                    if gesture == "open" then return end
                    if gesture == "cancel" then
                        manager:CancelSplitStack()
                        if manager.IsSplittingStack ~= false then error("The stock quantity popup did not cancel", 0) end
                        return
                    end
                    local quantity = amount or card.Item[item_ammo]
                    popup:SetCount(quantity)
                    if popup.Count ~= quantity then error("The stock quantity popup refused this count", 0) end
                    manager:FinalSplitStack(quantity)
                    manager:FinalSplitStack(quantity)
                else
                    if gesture or amount then error("The selected stock card does not have a quantity popup", 0) end
                    manager:ActionQuickMove()
                    manager:ActionQuickMove()
                end
            else
                if gesture or amount then error("The quantity popup fixture requires a merchant card", 0) end
                manager:StartHoverCard(card)
                manager:InitiateItemTake()
                manager:EndHoverCard(card)
                if manager.HoldingItem ~= true or identity(manager.HeldItem) ~= wanted then
                    error("The stock shop cursor did not take the backpack card", 0)
                end
                manager:InsertItem(screen.ShopPanel, -1)
                manager:InsertItem(screen.ShopPanel, -1)
                if manager.HoldingItem ~= false then error("The stock shop did not clear its cursor", 0) end
            end
            if rows:GetArrayNum() ~= count then error("The native merchant moved an item before the host confirmed", 0) end
            return
        end
    end
    error("The exact stock merchant card is unavailable", 0)
end

return stock
