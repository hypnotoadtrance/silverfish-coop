local fixture = {}
local host_save = require("native_host_save")
local players = require("players")
local maps = require("native_guest_drop_maps")
local drop_items = require("native_guest_drop_items")

local menu_command = "native-host-stack-drop-menu-fixture"
local close_command = "native-host-stack-drop-menu-close-fixture"
local inventory_path = "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"
local panel_path = "/Game/Classes/GUI/New/Components/InventoryPanel.InventoryPanel_C"
local manager_path = "/Game/Classes/GUI/New/Components/ItemManager.ItemManager_C"
local card_path = "/Game/Classes/GUI/New/Components/ItemCard.ItemCard_C"
local context_path = "/Game/Classes/GUI/New/Components/ItemContextMenu.ItemContextMenu_C"
local split_popup_path = "/Game/Classes/GUI/New/Components/SplitStackPopup.SplitStackPopup_C"
local slot_path = "/Game/Classes/GUI/New/Components/ItemSlot.ItemSlot_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_count = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local entries = { ctrl_click = true, context_menu = true, drag_to_floor = true, split_drag_to_floor = true,
    equipped_ctrl_click = true, equipped_context_menu = true, equipped_drag_to_floor = true }

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function row_guid(item)
    local parts = item and item[item_guid]
    if not parts then return false end
    local a, b, c, d = math.tointeger(parts.A), math.tointeger(parts.B), math.tointeger(parts.C), math.tointeger(parts.D)
    return a and b and c and d and string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff,
        c & 0xffffffff, d & 0xffffffff)
end

local function named_row(item, guid) return row_guid(item) == guid end

local function select_row(rows, guid, read_item)
    local count = rows and rows:GetArrayNum()
    if type(count) ~= "number" or count < 0 or count > 1024 then
        error("host_stack_drop_menu_fixture_rows_unavailable", 0)
    end
    local match
    for index = 1, count do
        local row = rows[index]
        if named_row(read_item(row), guid) then
            if match then error("host_stack_drop_menu_fixture_ambiguous_guid", 0) end
            match = row
        end
    end
    return match, count
end

local function row_item(row) return row end

local function card_item(card)
    return exact(card, card_path) and card.Item or nil
end

local function stock_payload(item, quantity, partial)
    if not item then return end
    local class, ammo = item[item_class], math.tointeger(item[item_count])
    local path = valid(class) and class:GetFullName():match("^%S+ (/Game/%S+)$")
    if not drop_items.stock_actor_class(path) or not ammo then return end
    local defaults = class:GetCDO()
    if not valid(defaults) or not same(defaults:GetClass(), class) or defaults.Important ~= false then return end
    if defaults.Stacks == true and (partial and ammo > quantity or not partial and ammo == quantity) then
        return class, ammo
    end
    if not partial and defaults.Stacks == false and quantity == 1 then
        local capacity = defaults.MagSize
        return class, ammo, type(capacity) == "number" and capacity > 0
    end
end

local function screen(controller, pawn, instance)
    local stack = controller.MenuStack
    if stack:GetArrayNum() ~= 1 then error("host_stack_drop_menu_fixture_inventory_unavailable", 0) end
    local menu = stack[1]
    if not exact(menu, inventory_path) or not menu:IsInViewport()
        or not same(menu:GetOwningPlayer(), controller) or not same(menu:GetOwningPlayerPawn(), pawn) then
        error("host_stack_drop_menu_fixture_inventory_unavailable", 0)
    end
    local panel, manager = menu.BackpackPanel, menu.ItemManager
    if not exact(panel, panel_path) or not exact(manager, manager_path)
        or not same(panel.Target, instance) or panel.InventoryIndex ~= 0 or not same(panel.ItemManager, manager)
        or not same(manager.ParentScreen, menu) or not same(manager.DropLocation, menu.FloorPanel)
        or manager.CanDropItems ~= true then error("host_stack_drop_menu_fixture_backpack_unavailable", 0) end
    return menu, panel, manager
end

local function open_stock_menu(controller, pawn, instance)
    if controller.MenuStack:GetArrayNum() ~= 0 then error("host_stack_drop_menu_fixture_menu_already_open", 0) end
    local class = StaticFindObject(inventory_path)
    if not valid(class) then
        LoadAsset(inventory_path)
        class = StaticFindObject(inventory_path)
    end
    if not valid(class) then error("host_stack_drop_menu_fixture_inventory_unavailable", 0) end
    local result = {}
    controller:OpenMenu(class, result)
    local menu, panel, manager = screen(controller, pawn, instance)
    if not same(menu, result.CreatedMenu) then error("host_stack_drop_menu_fixture_inventory_unverified", 0) end
    return panel, manager
end

local function stock_drop(instance, pawn, controller, guid, count, entry)
    local save = instance.SaveSlot_Ref
    local row, rows = select_row(save.PlayerBackpack, guid, row_item)
    local partial = entry == "split_drag_to_floor"
    local class, ammo, pistol = stock_payload(row, count, partial)
    if not class then error("host_stack_drop_menu_fixture_stock_stack_unavailable", 0) end
    local function matches(item)
        return item and same(item[item_class], class) and item[item_count] == ammo
    end
    local panel, manager = open_stock_menu(controller, pawn, instance)
    local card = select_row(panel.Cards, guid, card_item)
    if not valid(card) or not same(card.ItemManager, manager) or not same(card.InventoryWidget, panel)
        or not matches(card.Item) then error("host_stack_drop_menu_fixture_card_unavailable", 0) end

    if partial then
        manager.HoveredCard = card
        manager:BeginSplitStack()
        local popup = manager.SplitStackPopup
        if manager.IsSplittingStack ~= true or not exact(popup, split_popup_path)
            or not popup:IsVisible() or not same(popup.ItemCard, card) then
            error("host_stack_drop_menu_fixture_split_popup_unavailable", 0)
        end
        popup:SetCount(count)
        manager:FinalSplitStack(count)
        local held = manager.HeldItem
        local portion = row_guid(held)
        if manager.IsSplittingStack ~= false or manager.HoldingItem ~= true or not portion or portion == guid
            or not same(held[item_class], class) or held[item_count] ~= count then
            error("host_stack_drop_menu_fixture_split_cursor_unavailable", 0)
        end
        manager:InsertItem(manager.DropLocation, -1)
        local remaining, row_count = select_row(save.PlayerBackpack, guid, row_item)
        local staged = select_row(save.PlayerBackpack, portion, row_item)
        if manager.HoldingItem ~= false or not same(instance.SaveSlot_Ref, save) or not same(pawn.Save, save)
            or row_count ~= rows + 1 or not remaining or not staged or not same(remaining[item_class], class)
            or not same(staged[item_class], class) or remaining[item_count] ~= ammo - count or staged[item_count] ~= count then
            error("mutation_unverified_host_stack_drop_menu_fixture_split", 0)
        end
        return "stock split drop invoked for " .. portion
    end

    local equipped = entry:match("^equipped_") ~= nil
    local origin = panel
    if equipped then
        if not pistol then error("host_stack_drop_menu_fixture_requires_stock_pistol", 0) end
        entry = entry:sub(10)
        manager.HoveredCard = card
        manager:EquipToNumberSlot(3)
        local slots = manager.EquipSlots
        for index = 1, slots:GetArrayNum() do
            local slot = slots[index]
            if exact(slot, slot_path) and named_row(card_item(slot.ItemCard), guid) then
                origin, card = slot, slot.ItemCard
                break
            end
        end
        local equipped_row = select_row(save.PlayerEquipment, guid, row_item)
        if same(origin, panel) or not same(origin.Target, pawn) or not matches(equipped_row) or not matches(card_item(card))
            or select_row(save.PlayerBackpack, guid, row_item) then
            error("host_stack_drop_menu_fixture_equipped_item_unavailable", 0)
        end
    end

    if entry ~= "context_menu" then
        manager.HoveredCard = card
        if not same(manager.HoveredCard, card) then error("host_stack_drop_menu_fixture_selection_unverified", 0) end
    else
        manager:OpenContextMenu(card)
        local context = manager.ItemContextMenu
        if not exact(context, context_path) or not same(context["Item Card"], card) then
            error("host_stack_drop_menu_fixture_context_unverified", 0)
        end
    end

    -- Immediate stock commands can repeat before the coordinator settles.
    local presses = (entry == "drag_to_floor" or equipped) and 1 or 2
    for _ = 1, presses do
        if entry == "ctrl_click" then
            manager:DropItem()
        elseif entry == "context_menu" then
            manager:ActionDrop()
        else
            manager:InitiateItemTake()
            manager:FinalTakeItem()
            if manager.HoldingItem ~= true or not same(manager.ActiveReturnLocation, origin)
                or not matches(manager.HeldItem) then error("host_stack_drop_menu_fixture_drag_unavailable", 0) end
            manager:InsertItem(manager.DropLocation, -1)
            if manager.HoldingItem ~= false then error("host_stack_drop_menu_fixture_drag_not_returned", 0) end
        end
        local after, remaining = select_row(save.PlayerBackpack, guid, row_item)
        if not same(instance.SaveSlot_Ref, save) or not same(pawn.Save, save) or remaining ~= rows
            or not matches(after) or (equipped and select_row(save.PlayerEquipment, guid, row_item)) then
            error("mutation_unverified_host_stack_drop_menu_fixture_debit", 0)
        end
    end
    if pistol then return "stock pistol drop invoked with stored ammo " .. tostring(ammo) end
    return "stock drop invoked without direct inventory mutation"
end

function fixture.command(fields, state, phase)
    local command, guid = fields[2], fields[3]
    if command ~= menu_command and command ~= close_command then return end
    if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
        or not maps[state.map] or phase ~= "hosting" or state.server ~= false or not state.listening
        or type(guid) ~= "string" or #guid ~= 32 or not guid:match("^[0-9A-F]+$") or not guid:find("[^0]") then
        error("host_stack_drop_menu_fixture_unavailable", 0)
    end
    local instance, pawn = host_save.canonical_host(state, phase)
    local controller = state.controller
    if not valid(instance) or not valid(pawn) or pawn.Dead ~= false or players.is_downed(pawn)
        or controller.Loading ~= false then error("host_stack_drop_menu_fixture_unavailable", 0) end

    if command == close_command and #fields == 3 then
        local menu, panel, manager = screen(controller, pawn, instance)
        if select_row(instance.SaveSlot_Ref.PlayerBackpack, guid, row_item) or select_row(panel.Cards, guid, card_item) then
            error("host_stack_drop_menu_fixture_card_not_refreshed", 0)
        end
        for _, inventory_panel in ipairs({panel, menu.FloorPanel}) do
            local cards = inventory_panel.Cards
            for index = 1, cards:GetArrayNum() do
                local item = card_item(cards[index])
                if not item or not valid(item[item_class]) then
                    error("host_stack_drop_menu_fixture_invalid_card", 0)
                end
            end
        end
        if valid(manager.ItemContextMenu) then manager:CloseContextMenu() end
        local result = {}
        controller:CloseMenu(result)
        if result.Success ~= true or controller.MenuStack:GetArrayNum() ~= 0 or (valid(menu) and menu:IsInViewport()) then
            error("host_stack_drop_menu_fixture_close_unverified", 0)
        end
        return "canonical inventory and refreshed host menu agree"
    end
    local count, entry = math.tointeger(tonumber(fields[4])), fields[5]
    if command ~= menu_command or #fields ~= 5 or not count or count < 1 or count > 200 or tostring(count) ~= fields[4]
        or not entries[entry] then
        error("host_stack_drop_menu_fixture_arguments", 0)
    end
    return stock_drop(instance, pawn, controller, guid, count, entry)
end

return fixture
