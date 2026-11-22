local stock = {}
local screen_class = "/Game/Classes/GUI/Menu_Trade_Widget.Menu_Trade_Widget_C"
local card_class = "/Game/Classes/GUI/SubWidgets/TradeOffer_Widget.TradeOffer_Widget_C"
local post_class = "/Game/Classes/Actors/TradePost_BP.TradePost_BP_C"
local trade_offers = "TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F"
local give_field = "Items_Give_20_B4929C76420B737D08B6E7A56384E8EC"
local receive_field = "Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD"
local hide_field = "Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174"
local remove_field = "RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2"
local give_cash = "Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F"
local receive_cash = "Cash_Recive_12_FE560CBF4176840FDE3480B7FFB4C4F2"
local chance_field = "ChanceShown_4_18C5CA7A480470CDF14CAD8FBF228FF7"
local widget_library = "/Script/UMG.Default__WidgetBlueprintLibrary"
local table_path = "/Game/Classes/Libraries/DataTables/TradeInventories_DT.TradeInventories_DT"
local screen, controller, cards

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function require_expected(condition, detail)
    if not condition then error("native_sewer_stock_screen_" .. detail, 0) end
end

local function amounts(values, maximum, reward)
    require_expected(values and #values >= 1 and #values <= maximum, "items_size")
    local entries = {}
    values:ForEach(function(key, quantity)
        local class, count = key:get(), math.tointeger(quantity:get())
        local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass (/Game/Classes/[^|%s]+)$")
        require_expected(path and count and count >= (reward and 0 or 1) and count <= 100, "item")
        entries[#entries + 1] = path .. "|" .. math.max(count, 1)
    end)
    table.sort(entries)
    return table.concat(entries, "|")
end

local function key(row)
    require_expected(row and row[chance_field] == 1.0 and row[give_cash] == 0 and row[receive_cash] == 0
        and row[remove_field] == false and type(row[hide_field]) == "boolean", "recipe")
    return (row[hide_field] and "1" or "0") .. "|" .. amounts(row[receive_field], 1, true)
        .. "|" .. amounts(row[give_field], 3, false)
end

function stock.available()
    return type(SilverfishSetNativeSewerTradeScreen) == "function" and type(SilverfishTakeNativeSewerTradePress) == "function"
end

function stock.present(current)
    local stack = valid(controller) and controller.MenuStack
    return same(controller, current.controller) and stack and stack:GetArrayNum() == 1 and same(stack[1], screen)
end

function stock.exists() return valid(screen) end

function stock.close()
    if not valid(screen) then screen, controller, cards = nil, nil, nil; return end
    screen:SetIsEnabled(false)
    local stack = valid(controller) and controller.MenuStack
    if stack and stack:GetArrayNum() == 1 and same(stack[1], screen) then controller:CloseMenu({}) end
    if screen:IsInViewport() then screen:RemoveFromParent() end
    screen, controller, cards = nil, nil, nil
end

function stock.open(current, offers)
    stock.close()
    require_expected(current.controller.MenuStack:GetArrayNum() == 0, "another_menu")
    local post = StaticFindObject(current.profile.world .. ":PersistentLevel.TradePost_BP_C_1")
    local table_asset = StaticFindObject(table_path)
    require_expected(exact(post, post_class) and same(post:GetWorld(), current.world)
        and same(post:GetOuter(), current.world.PersistentLevel) and post.Generate_RandomTrades == false
        and post.ShopInventory and same(post.ShopInventory.DataTable, table_asset)
        and post.ShopInventory.RowName:ToString() == "SewerThing", "post")
    local authored = table_asset:FindRow("SewerThing")
    local canonical, live = authored and authored[trade_offers], post.ShopInv and post.ShopInv[trade_offers]
    require_expected(canonical and canonical:GetArrayNum() == 26 and live and live:GetArrayNum() == 26, "offer_count")
    local by_key, visible = {}, 0
    for ordinal = 1, 26 do
        local offer = offers[ordinal]
        local signature = key(canonical[ordinal])
        require_expected(offer and offer.key == signature and not by_key[signature], "host_offer")
        by_key[signature] = {ordinal = ordinal, ready = offer.ready, shown = offer.ready or signature:sub(1, 1) == "0"}
        if by_key[signature].shown then visible = visible + 1 end
    end
    local seen = {}
    for index = 1, 26 do
        local signature = key(live[index])
        require_expected(by_key[signature] and not seen[signature], "local_offer")
        seen[signature] = true
    end
    local class = StaticFindObject(screen_class)
    if not valid(class) then LoadAsset(screen_class); class = StaticFindObject(screen_class) end
    local library = StaticFindObject(widget_library)
    require_expected(valid(class) and valid(library), "class")
    screen = library:Create(current.pawn, class, current.controller)
    controller = current.controller
    require_expected(exact(screen, screen_class) and same(screen:GetOwningPlayer(), controller) and not valid(screen.Shop), "owner")
    screen:SetIsEnabled(false)
    screen.TradeInventory = post.ShopInv
    controller:AddMenu(screen)
    require_expected(stock.present(current) and not valid(screen.Shop), "mount")
    screen:RefreshInventoryList()
    local panel = screen.ScrollBox_Inventory
    require_expected(exact(panel, "/Script/UMG.ScrollBox") and panel:GetChildrenCount() == visible, "visible_offers")
    local parameters = {screen:GetAddress()}
    cards, seen = {}, {}
    for index = 0, visible - 1 do
        local card = panel:GetChildAt(index)
        require_expected(exact(card, card_class) and same(card.ParentWidget, screen), "card")
        local offer = by_key[key(card.TradeOffer)]
        require_expected(offer and offer.shown and not seen[offer.ordinal] and valid(card.Button_ConfirmTrade), "card_offer")
        card.Button_ConfirmTrade:SetIsEnabled(offer.ready)
        require_expected(card.Button_ConfirmTrade:GetIsEnabled() == offer.ready, "card_button")
        if offer.ready then
            cards[offer.ordinal] = card
            parameters[#parameters + 1] = offer.ordinal
            parameters[#parameters + 1] = card:GetAddress()
        end
        seen[offer.ordinal] = true
    end
    require_expected(SilverfishSetNativeSewerTradeScreen(table.unpack(parameters)) == true, "purchase_guard")
    screen:SetIsEnabled(true)
end

function stock.take(current)
    if not stock.present(current) then return end
    return SilverfishTakeNativeSewerTradePress(screen:GetAddress())
end

function stock.freeze()
    if valid(screen) then screen:SetIsEnabled(false) end
end

function stock.fixture(ordinal)
    local card = cards and cards[ordinal]
    require_expected(valid(card), "fixture_offer")
    card:BndEvt__TradeOffer_Widget_Button_ConfirmTrade_K2Node_ComponentBoundEvent_0_Pressed__DelegateSignature()
end

return stock
