local inventory_view = {}
local weapon_profiles = require("guest_weapon_registry")
local guest_weapon = require("native_guest_weapon")
local throwables = require("native_flare").equipment
local drop_maps = require("native_guest_drop_maps")
local drop_items = require("native_guest_drop_items")
local save_scope = require("native_save_scope")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_INVENTORY_VIEW") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local inventory_action_path = "/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory"
local inventory_action_event = "InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0"
local inventory_path = "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"
local card_path = "/Game/Classes/GUI/New/Components/ItemCard.ItemCard_C"
local context_menu_path = "/Game/Classes/GUI/New/Components/ItemContextMenu.ItemContextMenu_C"
local open_context_menu_path = "/Game/Classes/GUI/New/Components/ItemManager.ItemManager_C:OpenContextMenu"
local split_popup_path = "/Game/Classes/GUI/New/Components/SplitStackPopup.SplitStackPopup_C"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local display_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local text_path = "/Script/UMG.TextBlock"
local bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local starter_bags = {
    ["/Game/Classes/Items/Random_GearBag_Starter.Random_GearBag_Starter_C"] = "Dufflebag",
    ["/Game/Classes/Items/HyperRandom_GearBag_Starter.HyperRandom_GearBag_Starter_C"] = "Dufflebag",
}
local meals = require("native_guest_mre")
local mre_enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MRE") == "1"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local pry_tool_paths = {
    ["/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C"] = true,
    ["/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C"] = true,
    ["/Game/Classes/Equipment/Eqp_LockPick_Hev.Eqp_LockPick_Hev_C"] = true,
    ["/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C"] = true,
}
local body_armor = require("native_guest_armor")
local sources = {backpack = "Backpack", equipment = "Equipment", armor = "Armor", helmet = "Helmet"}
local keys = {up = "PageUp", down = "PageDown", confirm = "Home", back = "End", drop = "Delete"}
local input_keys, held = {}, {}
local page_size, max_items = 16, 8194
local event_path, equip_path, is_downed, binding, observation, display
local binding_state, stock_drop, stock_session, sync_stock
local stock_context_hook

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function load_class(path)
    local class = StaticFindObject(path)
    if not valid(class) then
        LoadAsset(path)
        class = StaticFindObject(path)
    end
    return valid(class) and class or nil
end

local function context(state, phase)
    if not enabled or not event_path or phase ~= "connected" or not state.listening or state.server ~= true
        or not valid(state.world) or type(state.map) ~= "string" or not state.map:match("^/Game/Classes/Maps/[%w_/]+$") then
        return nil
    end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_path) or not exact(controller, controller_path) or not same(detail.controller_object, controller)
        or pawn:HasAuthority() or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return nil end
    return {world = state.world, map = state.map, name = detail.name, pawn = pawn, controller = controller}
end

local function same_context(current, previous)
    return previous and current.map == previous.map and current.name == previous.name
        and same(current.world, previous.world) and same(current.pawn, previous.pawn)
        and same(current.controller, previous.controller)
end

local function remove_display()
    local previous = display
    display = nil
    held = {}
    if valid(previous) then previous:RemoveFromParent() end
end

function inventory_view.clear()
    remove_display()
    if stock_session or stock_drop then
        local previous = stock_session or stock_drop
        stock_session, stock_drop = nil, nil
        pcall(function()
            local controller = previous.context.controller
            local stack = valid(controller) and controller.MenuStack
            if stack and stack:GetArrayNum() == 1 and same(stack[1], previous.screen) then controller:CloseMenu({}) end
        end)
        SilverfishClearNativeHostDropScope()
    end
    if binding and type(SilverfishClearNativeInventoryViewScope) == "function" then
        SilverfishClearNativeInventoryViewScope()
    end
    binding, observation = nil, nil
    binding_state = nil
end

local function can_view(current)
    return current and current.controller.Loading == false
        and type(is_downed) == "function" and is_downed(current.pawn) == false
end

local function emit_line(path, fields)
    local file = io.open(path, "ab")
    if not file then return false end
    local written = file:write(table.concat(fields, "\t"), "\n")
    local closed = file:close()
    return written and closed
end

local function emit(current, sequence, event, offset)
    return emit_line(event_path, {"inventory-view", sequence, event, current.map, current.name, offset})
end

local function take(current)
    local accepted, sequence, open = SilverfishTakeNativeInventoryViewRequest()
    if not accepted then return false end
    remove_display()
    observation = {context = current, sequence = sequence, state = open and "pending" or "closed"}
    if not can_view(current) then observation.state = "closed" end
    if observation.state == "pending" then observation.pending_offset = 0 end
    if not emit(current, sequence, observation.state == "pending" and "open" or "closed") then
        observation.state = "error"
    end
    return true
end

function inventory_view.sync(state, phase)
    save_scope.bind_guest(state, phase)
    local current = context(state, phase)
    if not current or type(SilverfishSetNativeInventoryViewScope) ~= "function"
        or type(SilverfishTakeNativeInventoryViewRequest) ~= "function" then
        inventory_view.clear()
        if current then binding_state = "hooks_unavailable" end
        return
    end
    if not same_context(current, binding) then
        inventory_view.clear()
        if not load_class(inventory_path) then
            binding_state = "class_unavailable"
            return
        end
    end
    local allowed = can_view(current)
    if not SilverfishSetNativeInventoryViewScope(current.pawn:GetAddress(), allowed) then
        inventory_view.clear()
        binding_state = "scope_unavailable"
        return
    end
    binding = current
    binding_state = allowed and "ready" or "blocked"
    if stock_session then sync_stock(current, state, phase, allowed) end
    if observation and observation.state ~= "closed" and not allowed then
        remove_display()
        observation.state = "closed"
    end
    take(current)
end

local function integer(text, maximum)
    if type(text) ~= "string" or not text:match("^%d+$") or #text > 16 then return nil end
    local value = math.tointeger(tonumber(text))
    if value and value >= 0 and value <= maximum then return value end
end

local function page(fields)
    local offset, total = integer(fields[5], max_items), integer(fields[6], max_items)
    local count = #fields - 6
    if not offset or not total or offset > total or (offset > 0 and offset == total)
        or count ~= math.min(page_size, total - offset) then
        error("The authenticated inventory view page did not match its bounds", 0)
    end
    local records, seen, slots = {}, {}, {}
    for index = 7, #fields do
        local marked, options = fields[index]:match("^(.+)|([a-z,]+)$")
        if options and options ~= "split" and options ~= "drop" and options ~= "split,drop" then
            error("The authenticated inventory row contains an unsupported action", 0)
        end
        local guid, source, slot, amount, class = (marked or fields[index]):match(
            "^([0-9A-F]+)|([a-z]+)|(%d+)|(%d+)|(/Game/[%w_./%-]+)$")
        local quantity = integer(amount, 2147483647)
        local item = class and #class <= 512 and class:match("%.([%w_%-]+)_C$")
        local index_in_source = integer(slot, 4095)
        if not guid or #guid ~= 32 or guid == string.rep("0", 32) or seen[guid] or not sources[source]
            or not index_in_source or not quantity or quantity < 1 or not item then
            error("The authenticated inventory view contains an invalid stock item", 0)
        end
        local position = source .. "|" .. index_in_source
        if slots[position] then error("The authenticated inventory view repeats a stock inventory position", 0) end
        slots[position] = true
        seen[guid] = true
        local equippable = ((source == "backpack" or source == "equipment")
            and (weapon_profiles.kinds[class] ~= nil or class == camera_path or pry_tool_paths[class] or throwables[class]
                or class == "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"))
            or (source == "backpack" and (body_armor.is_body_path(class) or body_armor.is_helmet_path(class)))
        local name = item:gsub("_", " ")
        local consumable = class == bandage_path and "Bandage" or starter_bags[class] or mre_enabled and meals.label(class)
        local usable = source == "backpack" and consumable ~= false and consumable ~= nil
        local splittable = options == "split" or options == "split,drop"
        if splittable and (source ~= "backpack" or not drop_items.stock_actor_class(class) or quantity <= 1) then
            error("The authenticated inventory split row did not qualify", 0)
        end
        local droppable = options == "drop" or options == "split,drop"
        local drop_profile = drop_items.profiles[class]
        if droppable and (not drop_maps[fields[4]] or source ~= "backpack" or not drop_items.stock_actor_class(class)
            or quantity > (drop_profile and drop_profile.maximum or 200)) then
            error("The authenticated inventory drop row did not qualify", 0)
        end
        if usable then name = consumable end
        if droppable and drop_profile then name = drop_profile.label end
        records[#records + 1] = {guid = guid, class = class, equippable = equippable, usable = usable,
            droppable = droppable, name = name, quantity = quantity, splittable = splittable,
            label = string.format("%s   %d x %s", sources[source], quantity, name)}
    end
    return offset, total, records
end

local function show()
    local row = observation
    local records = row.records
    local lines = { "Co-op inventory", row.total == 0 and "Empty"
        or string.format("Items %d-%d of %d", row.offset + 1, row.offset + #records, row.total) }
    for index, entry in ipairs(records) do
        lines[#lines + 1] = (index == row.selection and "> " or "  ") .. entry.label
    end
    local selected = records[row.selection]
    if row.pending_offset then
        lines[#lines + 1] = "Loading inventory page. End closes."
    elseif row.drop_state == "confirming" then
        local description = selected.quantity > 1
            and string.format("%d × %s", selected.quantity, selected.name) or selected.name
        lines[#lines + 1] = "Drop " .. description .. "? Home confirms. End cancels."
    elseif row.drop_state == "dropping" then
        lines[#lines + 1] = "Drop pending"
    elseif row.drop_state == "dropped" then
        lines[#lines + 1] = "Item dropped. Reopen inventory to refresh."
    elseif row.drop_state == "refused" then
        lines[#lines + 1] = "Drop unavailable. Reopen inventory to refresh."
    elseif row.split_state == "confirming" then
        lines[#lines + 1] = string.format("Split off %d rounds? Home confirms. End cancels.", selected.quantity // 2)
    elseif row.split_state == "splitting" then
        lines[#lines + 1] = "Ammo stack split pending"
    elseif row.split_state == "split" then
        lines[#lines + 1] = "Ammo stack split. Reopen inventory to refresh."
    elseif row.split_state == "refused" then
        lines[#lines + 1] = "Ammo stack split unavailable. Reopen inventory to refresh."
    elseif selected and selected.droppable and (not selected.equippable or row.equip_state == "selecting") then
        local other = selected.splittable and " Home splits half the stack."
            or selected.equippable and " Home equips the selected item." or ""
        lines[#lines + 1] = "Page Up / Page Down selects. Delete prepares a drop." .. other .. " End closes."
    elseif selected and selected.splittable then
        lines[#lines + 1] = "Page Up / Page Down selects. Home splits half the stack. End closes."
    elseif row.use_state == "confirming" then
        lines[#lines + 1] = "Use one " .. selected.name .. "? Home confirms. End cancels."
    elseif row.use_state == "using" then
        lines[#lines + 1] = selected.name .. " use pending"
    elseif row.use_state == "used" then
        lines[#lines + 1] = selected.name .. " used. Reopen inventory to refresh."
    elseif row.use_state == "refused" then
        lines[#lines + 1] = selected.name .. " use unavailable. Reopen inventory to refresh."
    elseif row.use_state == "selecting" then
        lines[#lines + 1] = "Page Up / Page Down selects. Home uses the selected item. End closes."
    elseif row.equip_state == "confirming" then
        lines[#lines + 1] = "Equip " .. selected.name .. "? Home confirms. End cancels."
    elseif row.equip_state == "equipping" then
        lines[#lines + 1] = "Equip pending"
    elseif row.equip_state == "equipped" then
        lines[#lines + 1] = "Item equipped. Reopen inventory to refresh."
    elseif row.equip_state == "refused" or row.equip_state == "error" then
        lines[#lines + 1] = "Equip unavailable. Reopen inventory to refresh."
    elseif selected and selected.equippable then
        lines[#lines + 1] = "Page Up / Page Down selects. Home equips. End closes."
    elseif selected then
        lines[#lines + 1] = "Read-only item. Page Up / Page Down selects. End closes."
    end
    local message = table.concat(lines, "\n")
    display.Text = FText(message)
    display.TextBlock_Notes:SetText(FText(message))
    if display.TextBlock_Notes:GetText():ToString() ~= message then
        error("The passive inventory widget text did not verify", 0)
    end
end

local function render(current)
    local class, widgets = load_class(display_path), StaticFindObject(widgets_path)
    if not class or not valid(widgets) then error("The stock passive text widget is unavailable", 0) end
    local widget = widgets:Create(current.pawn, class, current.controller)
    if not exact(widget, display_path) or not same(widget:GetOwningPlayer(), current.controller)
        or not exact(widget.TextBlock_Notes, text_path) then
        error("The local passive stock inventory widget did not qualify", 0)
    end
    display = widget
    widget:SetVisibility(3)
    if widget:AddToPlayerScreen(30) ~= true or not widget:IsInViewport() then
        error("The passive inventory widget could not be added to the local screen", 0)
    end
    widget:SetDesiredSizeInViewport({X = 540, Y = 560})
    widget:SetPositionInViewport({X = 36, Y = 124}, false)
    show()
end

local function ready(current, close)
    return current and observation and observation.state == "rendered" and same_context(current, observation.context)
        and same_context(current, binding) and can_view(current) and valid(display)
        and same(display:GetOwningPlayer(), current.controller) and display:IsInViewport()
        and type(SilverfishNativeInventoryViewInput) == "function"
        and SilverfishNativeInventoryViewInput(current.pawn:GetAddress(), observation.sequence, close) == true
end

local function select_item()
    local selected = observation.records[observation.selection]
    observation.equip_state = selected and selected.equippable and "selecting" or "read_only"
    observation.use_state = selected and selected.usable and "selecting" or nil
    observation.split_state = nil
    observation.drop_state = nil
    observation.confirm_guid = nil
end

local function choose(current, action)
    if not ready(current, false) then return false end
    local row = observation
    local selected = row.records[row.selection]
    if action == "back" then
        if row.equip_state == "confirming" or row.use_state == "confirming" or row.split_state == "confirming"
            or row.drop_state == "confirming" then
            select_item()
        elseif ready(current, true) then
            remove_display()
            row.state, row.equip_state, row.use_state, row.split_state, row.drop_state = "closed", nil, nil, nil, nil
            row.pending_offset = nil
            emit(current, row.sequence, "closed")
            return true
        end
    elseif not row.request_guid and not row.pending_offset and selected then
        if action == "up" or action == "down" then
            local direction = action == "up" and -1 or 1
            local selection = row.selection + direction
            if selection >= 1 and selection <= #row.records then
                row.selection = selection
                select_item()
            else
                local offset = row.offset + direction * page_size
                select_item()
                if offset >= 0 and offset < row.total and emit(current, row.sequence, "page", offset) then
                    row.pending_offset = offset
                end
            end
        elseif action == "drop" and selected.droppable then
            select_item()
            row.drop_state, row.confirm_guid = "confirming", selected.guid
        elseif action == "confirm" and selected.droppable and row.drop_state == "confirming"
            and row.confirm_guid == selected.guid then
            row.request_guid, row.drop_state = selected.guid, "dropping"
        elseif action == "confirm" and selected.usable then
            if row.use_state == "confirming" and row.confirm_guid == selected.guid then
                row.request_guid, row.use_state = selected.guid, "using"
            else
                row.use_state, row.confirm_guid = "confirming", selected.guid
            end
        elseif action == "confirm" and selected.splittable then
            if row.split_state == "confirming" and row.confirm_guid == selected.guid then
                row.request_guid, row.split_state = selected.guid, "splitting"
            else
                row.split_state, row.confirm_guid = "confirming", selected.guid
            end
        elseif action == "confirm" and selected.equippable then
            if row.equip_state == "confirming" and row.confirm_guid == selected.guid then
                row.request_guid = selected.guid
                row.equip_state = emit_line(equip_path, {"native-guest-inventory-equip", row.sequence,
                    current.map, current.name, selected.guid, selected.class}) and "equipping" or "error"
            else
                row.equip_state, row.confirm_guid = "confirming", selected.guid
            end
        end
    end
    show()
    return true
end

local function poll_keys()
    local current = observation and observation.context
    if not ready(current, false) then return end
    local changed
    for _, action in ipairs({"back", "up", "down", "drop", "confirm"}) do
        if not input_keys[action] then
            local key = FName(keys[action], EFindName.FNAME_Find)
            if key:GetComparisonIndex() == 0 then error("The stock inventory key did not resolve", 0) end
            input_keys[action] = {KeyName = key}
        end
        local pressed = current.controller:IsInputKeyDown(input_keys[action])
        if type(pressed) ~= "boolean" then error("The stock inventory key state could not verify", 0) end
        if pressed and held[action] == false and not changed then changed = action end
        held[action] = pressed
    end
    if changed then choose(current, changed) end
end

local function widget_at(array, index)
    local widget = array[index]
    if widget and type(widget.type) == "function" and widget:type() == "RemoteUnrealParam" then
        return widget:get()
    end
    return widget
end

local function stock_item_guid(item)
    local id = item and item[item_guid]
    if not id then return end
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function stock_card_guid(card)
    return stock_item_guid(exact(card, card_path) and card.Item)
end

local function stock_equipped_card(screen, guid)
    if not guid then return end
    local slots = screen.ItemManager.EquipSlots
    for index = 1, math.min(8, slots:GetArrayNum()) do
        local slot = widget_at(slots, index)
        if valid(slot) and slot.ItemIndex == index - 1 and slot.InventoryIndex == 0 then
            local card = slot.ItemCard
            if stock_card_guid(card) == guid then return card, index end
        end
    end
end

local function stock_cards(screen, save, guid, remaining, ordered, raw_ammo)
    local expected, displayed = 0, 0
    local saved_count, card_count
    local order = ordered and {} or nil
    for index = 1, save.PlayerBackpack:GetArrayNum() do
        local row = save.PlayerBackpack[index]
        if valid(row[item_class]) then
            expected = expected + 1
            if order then
                order[expected] = stock_item_guid(row)
                if not order[expected] then return end
            end
        end
        if guid and stock_item_guid(row) == guid then
            if saved_count ~= nil or not valid(row[item_class]) then return end
            saved_count = raw_ammo and row[item_ammo] or math.max(1, row[item_ammo])
        end
    end
    local cards = screen.BackpackPanel.Cards
    for index = 1, cards:GetArrayNum() do
        local card = widget_at(cards, index)
        if valid(card) and valid(card.Item[item_class]) then
            displayed = displayed + 1
            if order and stock_card_guid(card) ~= order[displayed] then return end
        end
        if guid and stock_card_guid(card) == guid then
            if card_count ~= nil or not valid(card.Item[item_class]) then return end
            card_count = raw_ammo and card.Item[item_ammo] or math.max(1, card.Item[item_ammo])
        end
    end
    local item_matches = not guid or (saved_count == card_count and
        (remaining == nil and saved_count ~= nil or remaining ~= nil and
            (saved_count == remaining or not raw_ammo and saved_count == nil and remaining == 0)))
    if displayed == expected and item_matches then
        return displayed, order and table.concat(order, "|") or nil
    end
end

local function take_ammo_button(screen)
    local manager, panel = screen.ItemManager, screen.BackpackPanel
    local menu = manager.ItemContextMenu
    if not exact(menu, context_menu_path) or not menu:IsVisible() then return end
    local card = menu["Item Card"]
    if not exact(card, card_path) or not same(card.ItemManager, manager)
        or (not same(card.InventoryWidget, panel) and not same(card, stock_equipped_card(screen, stock_card_guid(card)))) then return end
    local class = card.Item[item_class]
    local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass (.+)$")
    local count, maximum = card.Item[item_ammo], weapon_profiles.magazine_capacity[path]
    if not maximum or math.type(count) ~= "integer" or count < 1 or count > maximum then return end
    local button = menu.Button_SplitStack
    if exact(button, "/Game/Classes/GUI/SubWidgets/Button_Default_Widget.Button_Default_Widget_C") then return button end
end

local function install_stock_context_hook()
    if stock_context_hook or type(RegisterHook) ~= "function" or not valid(StaticFindObject(open_context_menu_path)) then return end
    stock_context_hook = RegisterHook(open_context_menu_path, function(context)
        local session = stock_session
        if not session or not valid(session.screen) or not can_view(session.context)
            or not same(context:get(), session.screen.ItemManager) then return end
        local button = take_ammo_button(session.screen)
        if button then
            button:UpdateText(FText("TAKE AMMO"))
            button:SetVisibility(0)
        end
    end)
end

local function stock_sort_fixture(screen, current, save, epoch, pawn)
    local before, order = stock_cards(screen, save, nil, nil, true)
    if not before then error("The stock backpack order could not be verified", 0) end
    local owner = current.world.OwningGameInstance
    owner:SortItems(5)
    owner:SortItems(5)
    local sequence, guid, count, entry = SilverfishTakeNativeGuestDropRequest(pawn, epoch)
    local after, after_order = stock_cards(screen, save, nil, nil, true)
    if math.type(sequence) ~= "integer" or sequence < 1 or guid ~= string.rep("0", 32) or count ~= 0
        or entry ~= "sort" or after ~= before or after_order ~= order
        or SilverfishTakeNativeGuestDropRequest(pawn, epoch) ~= nil then
        error("The stock guest sort did not produce one intent without reordering the local backpack", 0)
    end
    stock_drop = {context = current, screen = screen, save = save, epoch = epoch, sequence = sequence, sort = true}
    return ("stock guest sort - 0 %d"):format(sequence)
end

local function stock_action_fixture(screen, current, save, epoch, item, use, split, equip)
    if type(SilverfishSetNativeGuestDropScope) ~= "function"
        or type(SilverfishTakeNativeGuestDropRequest) ~= "function"
        or type(SilverfishFinishNativeGuestDropRequest) ~= "function" then
        error("The native stock drop hooks are unavailable", 0)
    end
    local manager, panel = screen.ItemManager, screen.BackpackPanel
    local pawn = current.pawn:GetAddress()
    if not SilverfishSetNativeGuestDropScope(pawn, epoch, save:GetAddress()) then
        error("The stock backpack action could not bind the exact guest", 0)
    end
    if item == "-" then return stock_sort_fixture(screen, current, save, epoch, pawn) end
    local card
    for index = 1, panel.Cards:GetArrayNum() do
        local candidate = widget_at(panel.Cards, index)
        if stock_card_guid(candidate) == item then
            if card then error("The stock backpack contains a duplicate item identity", 0) end
            card = candidate
        end
    end
    if not valid(card) or not same(card.ItemManager, manager) or not same(card.InventoryWidget, panel) then
        error("The stock backpack drop could not bind its exact item and guest", 0)
    end
    local item_type = card.Item[item_class]
    local class = valid(item_type) and item_type:GetFullName():match("^BlueprintGeneratedClass (.+)$")
    local rows, ammo = save.PlayerBackpack:GetArrayNum(), card.Item[item_ammo]
    if split then
        local defaults = valid(item_type) and item_type:GetCDO()
        if not valid(defaults) or defaults.Stacks ~= true or defaults.Important ~= false
            or math.type(ammo) ~= "integer" or ammo <= split then
            error("The stock guest item has no eligible stack to split", 0)
        end
        -- The context-menu action moves the OS cursor before calling this
        -- stock popup entry. Use the popup entry directly in muted replicas.
        manager.HoveredCard = card
        manager:BeginSplitStack()
        local popup = manager.SplitStackPopup
        if manager.IsSplittingStack ~= true or not exact(popup, split_popup_path)
            or not popup:IsVisible() or not same(popup.ItemCard, card) then
            error("The stock stack popup did not select the exact backpack card", 0)
        end
        popup:SetCount(split)
        if popup.Count ~= split then error("The stock stack popup changed the selected amount", 0) end
        manager:FinalSplitStack(split)
        manager:FinalSplitStack(split)
    else
        if use and class ~= bandage_path and not starter_bags[class] and not (mre_enabled and meals.label(class)) then
            error("The stock guest item has no host-authoritative Use action", 0)
        end
        manager:OpenContextMenu(card)
        local menu = manager.ItemContextMenu
        if not exact(menu, context_menu_path) or not same(menu["Item Card"], card) then
            error("The stock item context menu did not select that backpack card", 0)
        end
        if use then
            manager:ActionUse()
            manager:ActionUse()
        elseif equip then
            manager:ActionEquip()
            manager:ActionEquip()
        else
            manager:ActionDrop()
            manager:ActionDrop()
        end
    end
    local sequence, guid, quantity, entry, expected = SilverfishTakeNativeGuestDropRequest(pawn, epoch)
    local kind = use and type(entry) == "string" and entry:match("^use%-([a-z%-]+)$")
    if math.type(sequence) ~= "integer" or sequence < 1 or guid ~= item or math.type(quantity) ~= "integer"
        or quantity < 1 or (split and (entry ~= "split" or quantity ~= split or expected ~= ammo))
        or (use and (not kind or quantity ~= 1))
        or (equip and (entry ~= "equip" or quantity ~= 1))
        or (not split and not use and not equip and (entry ~= "context_menu" or quantity > 200))
        or save.PlayerBackpack:GetArrayNum() ~= rows
        or stock_card_guid(card) ~= item or card.Item[item_ammo] ~= ammo then
        error("The stock guest action did not produce one intent without changing the local backpack", 0)
    end
    if SilverfishTakeNativeGuestDropRequest(pawn, epoch) ~= nil then
        error("The stock guest drop returned more than one intent", 0)
    end
    stock_drop = {context = current, screen = screen, save = save, epoch = epoch, guid = guid, sequence = sequence,
        split = split, expected = expected, equip = equip}
    if split then return ("stock guest split %s %d %d %d"):format(guid, quantity, sequence, expected) end
    return ("stock guest %s %s %d %d"):format(entry, guid, quantity, sequence)
end

local function open_stock_screen(state, current, action, stack, save)
    local owner = state.world.OwningGameInstance
    local controller = current.controller
    controller[inventory_action_event](controller, {}, 0, 0, action)
    local screen = stack:GetArrayNum() == 1 and widget_at(stack, 1) or nil
    if not exact(screen, inventory_path) or not same(screen:GetOwningPlayer(), controller)
        or not same(screen.BackpackPanel.Target, owner) or not same(owner.SaveSlot_Ref, save)
        or not same(screen.ItemManager.ParentScreen, screen) then
        error("The stock inventory did not bind the guest's isolated backpack", 0)
    end
    local displayed = stock_cards(screen, save)
    if not displayed then error("The stock inventory did not render the isolated backpack") end
    return screen, displayed
end

local function stock_preview(state, phase, current, action, stack, drop_epoch, drop_item, use, split, equip)
    local save = save_scope.bind_guest(state, phase)
    if not valid(save) or not same(current.pawn.Save, save) then
        error("The stock inventory preview requires the isolated guest save", 0)
    end
    local controller = current.controller
    SilverfishClearNativeInventoryViewScope()
    local success, proof = pcall(function()
        local screen, displayed = open_stock_screen(state, current, action, stack, save)
        if drop_item then return stock_action_fixture(screen, current, save, drop_epoch, drop_item, use, split, equip) end
        return ("verified stock guest inventory with %d backpack cards"):format(displayed)
    end)
    local guarded = SilverfishSetNativeInventoryViewScope(current.pawn:GetAddress(), true)
    if success and drop_item and guarded then return proof end
    local closed = pcall(function()
        if stack:GetArrayNum() == 1 and exact(widget_at(stack, 1), inventory_path) then controller:CloseMenu({}) end
    end)
    stock_drop = nil
    if drop_item and type(SilverfishClearNativeHostDropScope) == "function" then SilverfishClearNativeHostDropScope() end
    if not closed or not guarded or stack:GetArrayNum() ~= 0 then
        error("The stock inventory preview could not restore its menu scope", 0)
    end
    if not success then error(proof, 0) end
    return proof
end

local function open_stock_inventory(fields, current, state, phase)
    local sequence = integer(fields[3], 9007199254740991)
    local epoch = fields[4]
    local stack, action = current.controller.MenuStack, load_class(inventory_action_path)
    local save = save_scope.bind_guest(state, phase)
    local pawn = current.pawn:GetAddress()
    if #fields ~= 4 or not observation or observation.state ~= "pending" or observation.sequence ~= sequence
        or not same_context(current, observation.context) or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$")
        or stock_session or stock_drop or not valid(save) or not action or not stack or stack:GetArrayNum() ~= 0
        or type(SilverfishSetNativeGuestDropScope) ~= "function"
        or not SilverfishSetNativeGuestDropScope(pawn, epoch, save:GetAddress(), true) then
        error("The stock inventory could not open for the current co-op guest", 0)
    end
    SilverfishClearNativeInventoryViewScope()
    local ok, screen, displayed = pcall(open_stock_screen, state, current, action, stack, save)
    local guarded = SilverfishSetNativeInventoryViewScope(pawn, true)
    if not ok or not guarded then
        if stack:GetArrayNum() == 1 and exact(widget_at(stack, 1), inventory_path) then current.controller:CloseMenu({}) end
        SilverfishClearNativeHostDropScope()
        error(ok and "The stock inventory input could not be restored" or screen, 0)
    end
    stock_session = {context = current, screen = screen, save = save, epoch = epoch}
    install_stock_context_hook()
    observation.state, observation.pending_offset = "stock", nil
    return ("opened stock guest inventory with %d backpack cards"):format(displayed)
end

local function preview_stock_layout(action)
    local pawn, save = action.context.pawn:GetAddress(), action.save:GetAddress()
    local target = (action.expected or 0) - 1
    local slot, receipt
    if action.sort and type(SilverfishSortLocalGuestBackpack) == "function" then
        local ok = SilverfishSortLocalGuestBackpack(pawn, save)
        if ok then receipt = "sorted" end
    elseif action.equip and target < 8 and type(SilverfishStoreLocalGuestEquipment) == "function" then
        local ok, _, stored, _, displaced = SilverfishStoreLocalGuestEquipment(pawn, save, action.guid, target)
        if ok then slot, receipt = stored, displaced end
    elseif (action.equip or action.entry == "unequip") and type(SilverfishPreviewLocalGuestGear) == "function" then
        slot, receipt = SilverfishPreviewLocalGuestGear(pawn, save, action.guid, target, action.entry == "unequip")
    end
    if not receipt then return end

    -- Keep the exact destination and outcome for the host report. Rebuild the
    -- stock cards while the native input receipt is still occupied.
    action.layout_preview = receipt
    if slot then action.expected = slot + 1 end
    local screen = action.screen
    if not action.sort then screen.ItemManager:CloseContextMenu() end
    screen.BackpackPanel:FullRefresh()
    if slot then
        local widget = widget_at(screen.ItemManager.EquipSlots, slot + 1)
        if valid(widget) then widget:FullRefresh() end
    end
    if slot and slot >= 8 then
        local instance = action.context.world.OwningGameInstance
        if slot == 8 then instance:UpdateHelmet() else instance:UpdateArmor() end
        instance:Calc_CarryWeight()
    end
end

sync_stock = function(current, state, phase, allowed)
    local session, stack = stock_session, current.controller.MenuStack
    local top, present = stack:GetArrayNum(), false
    for index = 1, math.min(top, 32) do
        if same(widget_at(stack, index), session.screen) then present = true; break end
    end
    local exposed = top > 0 and same(widget_at(stack, top), session.screen)
    if not present or (exposed and not allowed) then
        if exposed then current.controller:CloseMenu({}) end
        stock_session = nil
        if not stock_drop then SilverfishClearNativeHostDropScope() end
        if observation then observation.state = "closed" end
        return
    end
    local save = save_scope.bind_guest(state, phase)
    local pawn = current.pawn:GetAddress()
    if not same(save, session.save) or not SilverfishSetNativeGuestDropScope(pawn, session.epoch, save:GetAddress(), true) then
        if exposed then current.controller:CloseMenu({}) end
        stock_session, stock_drop = nil, nil
        SilverfishClearNativeHostDropScope()
        if observation then observation.state = "closed" end
        return
    end
    if stock_drop or not exposed or not allowed then return end
    local sequence, guid, quantity, entry, expected = SilverfishTakeNativeGuestDropRequest(pawn, session.epoch)
    if not sequence then return end
    stock_drop = {context = current, screen = session.screen, save = save, epoch = session.epoch, sequence = sequence,
        guid = entry == "sort" and "-" or guid, quantity = quantity, entry = entry, expected = expected,
        sort = entry == "sort", split = (entry == "split" or entry == "take-ammo") and quantity or nil,
        unload = entry == "take-ammo", equip = entry == "equip", worn_drop = entry:match("^worn_drop_") ~= nil, live = true}
    -- A UI refresh failure still leaves a valid report for a typed local move.
    pcall(preview_stock_layout, stock_drop)
end

local function preview_personal_consumable(fields, current, state, phase)
    local pending, session = stock_drop, stock_session
    local stack = current.controller.MenuStack
    if #fields ~= 4 or not pending or pending.entry ~= "use-personal-consumable" or pending.preview
        or pending.epoch ~= fields[3] or pending.sequence ~= integer(fields[4], 9007199254740991)
        or not same_context(current, pending.context) or not session or not same(session.screen, pending.screen)
        or stack:GetArrayNum() ~= 1 or not same(widget_at(stack, 1), pending.screen)
        or type(SilverfishPreviewNativeGuestPersonalConsumable) ~= "function" then
        error("The personal consumable no longer matches the current backpack action", 0)
    end
    local save = save_scope.bind_guest(state, phase)
    local pawn = current.pawn:GetAddress()
    if not same(save, pending.save) or not SilverfishSetNativeGuestDropScope(pawn, pending.epoch, save:GetAddress(), true) then
        error("The personal consumable lost its isolated guest backpack", 0)
    end
    local cards, manager = pending.screen.BackpackPanel.Cards, pending.screen.ItemManager
    local card
    for index = 1, cards:GetArrayNum() do
        local candidate = widget_at(cards, index)
        if stock_card_guid(candidate) == pending.guid then
            card = candidate
            break
        end
    end
    local class = card and card.Item[item_class]
    if not valid(class) then error("The personal consumable is no longer in the backpack", 0) end
    local class_path = class:GetFullName():match(" (%S+)$")
    local menu = manager.ItemContextMenu
    if not valid(menu) or not same(menu["Item Card"], card) then manager:OpenContextMenu(card) end
    local ok, status, preview_class, before, after, food, health, regen, stim, fatigue, intox, sickness =
        SilverfishPreviewNativeGuestPersonalConsumable(pawn, pending.epoch, pending.sequence)
    if not ok or not class_path or preview_class ~= class_path or not same(current.pawn.Save, save)
        or math.type(sickness) ~= "integer" then
        error("The game could not preview the personal consumable: " .. tostring(status), 0)
    end
    pending.preview = {class_path, before, after, string.format("%.17g", food), string.format("%.17g", health),
        string.format("%.17g", regen), string.format("%.17g", stim), fatigue, intox, sickness}
    return "measured personal consumable use"
end

local function finish_stock_drop(fields, current, state, phase)
    local pending = stock_drop
    local remaining = integer(fields[5], 2147483647)
    local sorted = pending and pending.sort and fields[5] == "sorted"
    local created = #fields == 6 and fields[6]
    local split = pending and pending.split and remaining == pending.expected - pending.split
        and created and #created == 32 and created:match("^[0-9A-F]+$") and created:find("[^0]") and created ~= pending.guid
    local slot = pending and pending.equip and fields[5] == "equipped" and #fields == 7 and integer(fields[6], 9)
    local displaced = slot and fields[7]
    local equipped = slot and (not pending.expected or pending.expected == 0 or slot + 1 == pending.expected)
        and (displaced == "-" or #displaced == 32 and displaced:match("^[0-9A-F]+$")
        and displaced:find("[^0]") and displaced ~= pending.guid)
    local source = pending and pending.entry == "unequip" and fields[5] == "unequipped" and #fields == 7
        and integer(fields[6], 9)
    local rounds = source and fields[7]:match("^%-?%d+$") and math.tointeger(fields[7])
    local unequipped = source and pending.expected == source + 1 and rounds and rounds >= -1 and rounds <= 2147483647
    local dropped_worn = pending and pending.worn_drop and remaining == 0 and (pending.expected == 9 or pending.expected == 10)
    if (not split and not equipped and not unequipped and #fields ~= 5) or not pending or not same_context(current, pending.context)
        or fields[3] ~= pending.epoch or integer(fields[4], 9007199254740991) ~= pending.sequence
        or (not sorted and not split and not equipped and not unequipped
            and (not remaining or pending.sort or pending.split or pending.equip or pending.entry == "unequip"
                or pending.worn_drop and not dropped_worn)
            and fields[5] ~= "refused") then
        error("The stock inventory settlement belongs to another request", 0)
    end
    stock_drop = nil
    local ok, result = pcall(function()
        local save = save_scope.bind_guest(state, phase)
        local pawn = current.pawn:GetAddress()
        if not same(save, pending.save) or not SilverfishSetNativeGuestDropScope(pawn, pending.epoch, save:GetAddress(),
                                                                                  pending.live == true) then
            error("The stock inventory settlement lost its isolated backpack", 0)
        end
        local finished, status
        if unequipped then
            finished, status = SilverfishFinishNativeGuestDropRequest(pawn, pending.epoch, pending.sequence, rounds)
        elseif dropped_worn then
            finished, status = SilverfishFinishNativeGuestDropRequest(pawn, pending.epoch, pending.sequence, 0)
        else
            finished, status = SilverfishFinishNativeGuestDropRequest(pawn, pending.epoch, pending.sequence)
        end
        if pending.live and finished and status == "screen_closed" then return "verified stock inventory settlement" end
        if dropped_worn then
            local widget = widget_at(pending.screen.ItemManager.EquipSlots, pending.expected)
            local item = pending.expected == 9 and save.PlayerHelmet or save.PlayerArmor
            if not finished or status ~= "refreshed" or not valid(widget) or widget.InventoryIndex ~= 1
                or widget.ItemIndex ~= pending.expected - 9 or valid(item[item_class])
                or stock_card_guid(widget.ItemCard) == pending.guid
                or not stock_cards(pending.screen, save, pending.guid, 0) then
                error("The stock worn slot did not reflect the settled drop: " .. tostring(status), 0)
            end
            return ("verified stock world drop from slot %d"):format(pending.expected - 1)
        end
        if unequipped then
            if not finished or status ~= "refreshed" then
                error("Stock unequip could not refresh: " .. tostring(status), 0)
            end
            local widget = widget_at(pending.screen.ItemManager.EquipSlots, source + 1)
            local item = source < 8 and save.PlayerEquipment[source + 1] or source == 8 and save.PlayerHelmet or save.PlayerArmor
            local index = source < 8 and source or source - 8
            if not valid(widget) or widget.ItemIndex ~= index
                or widget.InventoryIndex ~= (source < 8 and 0 or 1)
                or valid(item[item_class]) or stock_card_guid(widget.ItemCard) == pending.guid
                or not stock_cards(pending.screen, save, pending.guid, rounds, false, true) then
                error("The stock equipment slot and returned backpack item did not verify", 0)
            end
            return ("verified stock unequip from slot %d"):format(source)
        end
        if equipped then
            local widget = widget_at(pending.screen.ItemManager.EquipSlots, slot + 1)
            local item = slot < 8 and save.PlayerEquipment[slot + 1] or slot == 8 and save.PlayerHelmet or save.PlayerArmor
            local index = slot < 8 and slot or slot - 8
            if not finished or status ~= "refreshed" or not valid(widget) or widget.ItemIndex ~= index
                or widget.InventoryIndex ~= (slot < 8 and 0 or 1)
                or stock_card_guid(widget.ItemCard) ~= pending.guid or stock_item_guid(item) ~= pending.guid
                or not stock_cards(pending.screen, save, pending.guid, 0) then
                error("The stock equipment slot did not reflect the host's saved item", 0)
            end
            if displaced ~= "-" then
                local found = false
                for index = 1, save.PlayerBackpack:GetArrayNum() do
                    if stock_item_guid(save.PlayerBackpack[index]) == displaced then found = true; break end
                end
                if not found then error("The displaced stock item did not return to the backpack", 0) end
            end
            return ("verified stock equipment slot %d"):format(slot)
        end
        local displayed
        if finished and status == "refreshed" then
            local card, index
            if pending.unload then card, index = stock_equipped_card(pending.screen, pending.guid) end
            if card then
                local item = save.PlayerEquipment[index]
                if stock_item_guid(item) == pending.guid and item[item_ammo] == remaining and card.Item[item_ammo] == remaining then
                    displayed = stock_cards(pending.screen, save)
                end
            else
                displayed = stock_cards(pending.screen, save, remaining and pending.guid or nil, remaining, sorted, pending.unload)
            end
        end
        if not displayed then error("The open stock backpack did not reflect the settled action", 0) end
        if split then
            local manager = pending.screen.ItemManager
            if manager.IsSplittingStack or manager.SplitStackPopup:IsVisible()
                or not stock_cards(pending.screen, save, created, pending.split) then
                error("The stock backpack did not show both settled stacks", 0)
            end
            return (pending.unload and "verified stock empty weapon and recovered ammo with %d cards"
                or "verified two stock stacks in an open backpack with %d cards"):format(displayed)
        end
        if sorted then
            if SilverfishTakeNativeGuestDropRequest(pawn, pending.epoch) ~= nil then
                error("Refreshing the stock backpack queued another sort", 0)
            end
            return ("verified open stock backpack sort with %d cards"):format(displayed)
        end
        return ("verified open stock backpack refresh with %d cards"):format(displayed)
    end)
    if pending.live then
        if not stock_session then SilverfishClearNativeHostDropScope() end
        if not ok then error(result, 0) end
        return result
    end
    local stack = current.controller.MenuStack
    if stack:GetArrayNum() == 1 and same(widget_at(stack, 1), pending.screen) then current.controller:CloseMenu({}) end
    SilverfishClearNativeHostDropScope()
    if not ok then error(result, 0) end
    if stack:GetArrayNum() ~= 0 then error("The verified stock inventory fixture did not close", 0) end
    return result
end

local function fixture(current, state, phase, preview, drop_epoch, drop_item, use, split, equip)
    if not headless or not can_view(current) or not same_context(current, binding) then
        error("The fixture requires an enrolled headless stock inventory viewer", 0)
    end
    local action, stack = load_class(inventory_action_path), current.controller.MenuStack
    if action and stock_session and not preview then
        current.controller[inventory_action_event](current.controller, {}, 0, 0, action)
        sync_stock(current, state, phase, true)
        if not stock_session then return "verified stock inventory closed through Inventory" end
        error("The stock Inventory action did not close the mounted backpack", 0)
    end
    if not action or not stack or stack:GetArrayNum() ~= 0 then
        error("The exact stock inventory menu fixture is unavailable", 0)
    end
    if preview then return stock_preview(state, phase, current, action, stack, drop_epoch, drop_item, use, split, equip) end
    current.controller[inventory_action_event](current.controller, {}, 0, 0, action)
    if stack:GetArrayNum() ~= 0 or not take(current)
        or not observation or observation.state == "error" then
        error("The stock inventory action did not publish the isolated key request", 0)
    end
    return "verified exact stock inventory key path"
end

function inventory_view.command(fields, state, phase)
    local operation = fields[2]
    if operation ~= "native-inventory-view-render" and operation ~= "native-inventory-view-unavailable"
        and operation ~= "native-inventory-view-fixture" and operation ~= "native-inventory-view-key-fixture"
        and operation ~= "native-inventory-stock-drop-finish"
        and operation ~= "native-inventory-stock-project-unloaded"
        and operation ~= "native-inventory-stock-open"
        and operation ~= "native-inventory-stock-refresh"
        and operation ~= "native-inventory-stock-nearby-fixture"
        and operation ~= "native-inventory-stock-use-click-fixture"
        and operation ~= "native-inventory-stock-personal-preview"
        and operation ~= "native-inventory-view-equip-result"
        and operation ~= "native-inventory-view-use-result"
        and operation ~= "native-inventory-view-split-result"
        and operation ~= "native-inventory-view-drop-result" then return end
    local current = context(state, phase)
    if current and operation == "native-inventory-stock-drop-finish" then return finish_stock_drop(fields, current, state, phase) end
    if operation == "native-inventory-stock-project-unloaded" then
        local pending = stock_drop
        if #fields ~= 4 or not current or not pending or not pending.unload or not same_context(current, pending.context)
            or pending.epoch ~= fields[3] or pending.sequence ~= tonumber(fields[4])
            or not same(current.pawn.Save, pending.save) or not guest_weapon.project_unloaded(current.pawn, pending.save, pending.guid) then
            error("The stock unloaded magazine could not be projected", 0)
        end
        return "stock unloaded magazine projected"
    end
    if not current or not same_context(current, binding) or not can_view(current) then
        error("Stock inventory view requires the exact standing connected guest", 0)
    end
    if operation == "native-inventory-stock-open" then return open_stock_inventory(fields, current, state, phase) end
    if operation == "native-inventory-stock-personal-preview" then
        return preview_personal_consumable(fields, current, state, phase)
    end
    if operation == "native-inventory-stock-refresh" and #fields == 3 then
        local live = stock_session
        local stack = current.controller.MenuStack
        if not live or not same_context(current, live.context) or live.epoch ~= fields[3]
            or stack:GetArrayNum() ~= 1 or not same(widget_at(stack, 1), live.screen) then
            return "stock inventory closed before refresh"
        end
        live.screen.BackpackPanel:FullRefresh()
        if not stock_cards(live.screen, live.save) then error("stock backpack contents did not refresh", 0) end
        return "verified stock backpack refresh"
    end
    if operation == "native-inventory-stock-nearby-fixture" and (#fields == 3 or #fields == 4) and headless and stock_session
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local screen = stock_session.screen
        local actor = StaticFindObject(fields[3])
        local guid
        for index = 1, screen.FloorActors:GetArrayNum() do
            if same(screen.FloorActors[index], actor) then guid = stock_item_guid(screen.FloorItems[index]); break end
        end
        local drag = fields[4] == "drag"
        if #fields == 4 and not drag then
            local probe = stock_session.floor_probe
            local displayed = probe and stock_cards(screen, stock_session.save, fields[4])
            local removed = probe and probe.path == fields[3]
            for index = 1, removed and screen.FloorPanel.Cards:GetArrayNum() or 0 do
                if stock_card_guid(widget_at(screen.FloorPanel.Cards, index)) == probe.guid then removed = false; break end
            end
            if not guid and displayed and removed and (not valid(actor) or actor.bHidden) then
                return "verified stock Nearby Items transfer"
            end
            error("The stock Nearby Items transfer is not visible in the open backpack", 0)
        end
        for index = 1, guid and screen.FloorPanel.Cards:GetArrayNum() or 0 do
            local card = widget_at(screen.FloorPanel.Cards, index)
            if stock_card_guid(card) == guid then
                local count = math.max(1, card.Item[item_ammo])
                local label = count > 1 and "x" .. count or nil
                if label and card.Count:GetText():ToString() ~= label then
                    error("The stock Nearby Items stack label does not match the item card", 0)
                end
                local manager = screen.ItemManager
                if drag then
                    manager.HoveredCard = card
                    manager:InitiateItemTake()
                    manager:FinalTakeItem()
                    if not manager.HoldingItem or stock_item_guid(manager.HeldItem) ~= guid then
                        error("The stock cursor did not hold the Nearby Items card", 0)
                    end
                    manager:InsertItem(screen.BackpackPanel, -1)
                    if manager.HoldingItem then error("The stock cursor kept the Nearby Items card", 0) end
                else
                    manager:OpenContextMenu(card)
                    manager:ActionPickup()
                end
                if valid(actor) and actor.bHidden == false then
                    stock_session.floor_probe = {path = fields[3], guid = guid}
                    return "stock Nearby Items card requested" .. (label and " " .. label or "")
                end
                break
            end
        end
        local object_type = valid(actor) and valid(actor.Box) and actor.Box:GetCollisionObjectType() or "-"
        error(("The actor did not produce a selectable Nearby Items card. Floor match %s, object type %s"):
            format(guid or "-", tostring(object_type)), 0)
    end
    if operation == "native-inventory-stock-use-click-fixture"
        and (#fields == 3 or #fields == 4 and (fields[4] == "drag" or fields[4] == "take-ammo"))
        and headless and stock_session and not stock_drop
        and (os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" or (mre_enabled and #fields == 3) or fields[4] == "take-ammo"
            and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1") then
        local screen = stock_session.screen
        local cards, manager = screen.BackpackPanel.Cards, screen.ItemManager
        local card = fields[4] == "take-ammo" and stock_equipped_card(screen, fields[3]) or nil
        for index = 1, not card and cards:GetArrayNum() or 0 do
            local candidate = widget_at(cards, index)
            if stock_card_guid(candidate) == fields[3] then card = candidate; break end
        end
        if card then
            if fields[4] == "drag" then
                manager.HoveredCard = card
                manager:InitiateItemTake()
                manager:FinalTakeItem()
                if manager.HoldingItem ~= true then error("The stock card did not enter the cursor", 0) end
                manager:InsertItem(screen.PlayerCard, -1)
            elseif fields[4] == "take-ammo" then
                manager:OpenContextMenu(card)
                local button = take_ammo_button(screen)
                if not valid(button) or not button:IsVisible() or button.Button_Text:ToString() ~= "TAKE AMMO" then
                    error("The loaded stock weapon has no Take Ammo button after opening the context menu", 0)
                end
                manager.ItemContextMenu:BndEvt__ItemContextMenu_Button_SplitStack_K2Node_ComponentBoundEvent_7_Pressed__DelegateSignature()
            else
                manager:OpenContextMenu(card)
                manager:ActionUse()
            end
            sync_stock(current, state, phase, true)
            if stock_drop and stock_drop.guid == fields[3] then return "verified stock use click" end
        end
        error("The stock use click did not queue the chosen inventory item", 0)
    end
    if operation == "native-inventory-view-fixture" and (#fields == 2 or #fields == 3 and fields[3] == "stock") then
        return fixture(current, state, phase, fields[3] == "stock")
    end
    if operation == "native-inventory-view-fixture" and #fields == 5
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
        and #fields[4] == 32 and fields[4]:match("^[0-9a-f]+$") then
        if fields[3] == "stock-sort" and fields[5] == "-" then
            return fixture(current, state, phase, true, fields[4], fields[5])
        end
        if (fields[3] == "stock-drop" or fields[3] == "stock-use" or fields[3] == "stock-equip")
            and #fields[5] == 32 and fields[5]:match("^[0-9A-F]+$") and fields[5]:find("[^0]") then
            return fixture(current, state, phase, true, fields[4], fields[5], fields[3] == "stock-use", nil, fields[3] == "stock-equip")
        end
    end
    if operation == "native-inventory-view-fixture" and #fields == 6 and fields[3] == "stock-split"
        and headless and #fields[4] == 32 and fields[4]:match("^[0-9a-f]+$")
        and #fields[5] == 32 and fields[5]:match("^[0-9A-F]+$") and fields[5]:find("[^0]") then
        local amount = integer(fields[6], 2147483647)
        if amount and amount > 0 then return fixture(current, state, phase, true, fields[4], fields[5], false, amount) end
    end
    local sequence = integer(fields[3], 9007199254740991)
    if not observation or not same_context(current, observation.context)
        or sequence ~= observation.sequence or fields[4] ~= current.map then
        error("The authenticated inventory view response is stale or belongs to a later keypress", 0)
    end
    if operation == "native-inventory-view-key-fixture" then
        if not headless or #fields ~= 5 or not keys[fields[5]] or not choose(current, fields[5]) then
            error("The staged inventory keyboard action requires the exact displayed sequence", 0)
        end
        return "qualified stock inventory input observed"
    elseif operation == "native-inventory-view-equip-result" then
        if #fields ~= 6 or observation.equip_state ~= "equipping" or fields[5] ~= observation.request_guid
            or (fields[6] ~= "equipped" and fields[6] ~= "refused") or not ready(current, false) then
            error("The authenticated inventory equip result does not match the pending request", 0)
        end
        observation.equip_state = fields[6]
        show()
        return "verified inventory equip result displayed"
    elseif operation == "native-inventory-view-use-result" then
        if #fields ~= 6 or observation.use_state ~= "using" or fields[5] ~= observation.request_guid
            or (fields[6] ~= "used" and fields[6] ~= "refused") or not ready(current, false) then
            error("The authenticated result does not match the pending item use", 0)
        end
        observation.use_state = fields[6]
        show()
        return "verified inventory item result displayed"
    elseif operation == "native-inventory-view-drop-result" then
        if #fields ~= 6 or observation.drop_state ~= "dropping" or fields[5] ~= observation.request_guid
            or (fields[6] ~= "dropped" and fields[6] ~= "refused") or not ready(current, false) then
            error("The authenticated result does not match the pending inventory drop", 0)
        end
        observation.drop_state = fields[6]
        show()
        return "verified inventory drop result displayed"
    elseif operation == "native-inventory-view-split-result" then
        if #fields ~= 6 or observation.split_state ~= "splitting" or fields[5] ~= observation.request_guid
            or (fields[6] ~= "split" and fields[6] ~= "refused") or not ready(current, false) then
            error("The authenticated result does not match the pending inventory split", 0)
        end
        observation.split_state = fields[6]
        show()
        return "verified inventory split result displayed"
    end
    local pending_offset = observation.pending_offset
    if not pending_offset or observation.state ~= "pending" and observation.state ~= "rendered" then
        error("No inventory page is awaiting an authenticated response", 0)
    end
    if operation == "native-inventory-view-unavailable" and #fields == 5
        and integer(fields[5], max_items) == pending_offset then
        observation.pending_offset = nil
        if observation.state == "pending" then observation.state = "error" else show() end
        return "authenticated stock inventory view unavailable"
    end
    if operation ~= "native-inventory-view-render" or #fields < 6 or #fields > 6 + page_size then
        error("The authenticated stock inventory view response is incomplete", 0)
    end
    local offset, total, records = page(fields)
    if offset ~= pending_offset then error("The inventory page response does not match the requested offset", 0) end
    local previous = observation.offset
    observation.pending_offset = nil
    observation.offset, observation.total, observation.count, observation.records = offset, total, #records, records
    observation.selection = previous and offset < previous and #records or (#records > 0 and 1 or 0)
    select_item()
    local ok, failure
    if observation.state == "rendered" then ok, failure = pcall(show)
    else ok, failure = pcall(render, current) end
    observation.state = ok and "rendered" or "error"
    if not ok then
        remove_display()
        error(failure, 0)
    end
    return "verified stock inventory summary on the local screen"
end

function inventory_view.rows(state, phase, status_row)
    local current = context(state, phase)
    if not current then return {} end
    local rows = {status_row("native-inventory-split-view-capability", 1)}
    if binding_state then rows[#rows + 1] = status_row("native-inventory-view-binding", binding_state) end
    if stock_drop and stock_drop.live and same_context(current, stock_drop.context) then
        rows[#rows + 1] = status_row("native-inventory-stock-action", current.map, current.name, stock_drop.epoch,
            stock_drop.sequence, stock_drop.entry, stock_drop.guid, stock_drop.quantity, stock_drop.expected or 0,
            stock_drop.layout_preview or "")
        if stock_drop.preview then
            rows[#rows + 1] = status_row("native-guest-personal-consumable-preview", current.map, current.name,
                stock_drop.guid, table.unpack(stock_drop.preview))
        end
    end
    if not observation or not same_context(current, observation.context) then return rows end
    rows[#rows + 1] = status_row("native-inventory-view", observation.state, observation.sequence, current.map, current.name,
        observation.offset or 0, observation.total or 0, observation.count or 0)
    if observation.state == "rendered" and observation.equip_state then
        local selected = observation.records[observation.selection]
        rows[#rows + 1] = status_row("native-guest-inventory-equip-view", observation.sequence, current.map,
            current.name, selected and selected.guid or "-", observation.equip_state)
    end
    if observation.use_state and ready(current, false) then
        local selected = observation.records[observation.selection]
        rows[#rows + 1] = status_row("native-guest-inventory-use-view", observation.sequence, current.map,
            current.name, selected.guid, observation.use_state)
    end
    if observation.drop_state and ready(current, false) then
        local selected = observation.records[observation.selection]
        rows[#rows + 1] = status_row("native-guest-inventory-drop-view", observation.sequence, current.map,
            current.name, selected.guid, selected.class, observation.drop_state, selected.quantity)
    end
    if observation.split_state and ready(current, false) then
        local selected = observation.records[observation.selection]
        rows[#rows + 1] = status_row("native-guest-inventory-split-view", observation.sequence, current.map,
            current.name, selected.guid, selected.class, observation.split_state, selected.quantity)
    end
    return rows
end

function inventory_view.start(bridge, downed)
    event_path = enabled and bridge and bridge ~= "" and bridge .. "/native-inventory-view-events" or nil
    equip_path = event_path and bridge .. "/native-guest-inventory-equip-events" or nil
    is_downed = downed
    if not event_path then return end
    LoopInGameThreadWithDelay(16, function()
        local ok, failure = pcall(poll_keys)
        if not ok then
            if observation then observation.state = "error" end
            remove_display()
            print("[SilverfishCoop] Inventory keys unavailable: " .. tostring(failure) .. "\n")
        end
    end)
end

return inventory_view
