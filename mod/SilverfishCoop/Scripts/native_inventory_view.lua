local inventory_view = {}
local weapon_profiles = require("guest_weapon_registry")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_INVENTORY_VIEW") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local inventory_path = "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"
local display_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local text_path = "/Script/UMG.TextBlock"
local bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local sources = {backpack = "Backpack", equipment = "Equipment", armor = "Armor", helmet = "Helmet"}
local keys = {up = "PageUp", down = "PageDown", confirm = "Home", back = "End"}
local input_keys, held = {}, {}
local page_size, max_items = 16, 8194
local event_path, equip_path, is_downed, binding, observation, display

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
    if binding and type(SilverfishClearNativeInventoryViewScope) == "function" then
        SilverfishClearNativeInventoryViewScope()
    end
    binding, observation = nil, nil
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

local function emit(current, sequence, open)
    return emit_line(event_path, {"inventory-view", sequence, open and "open" or "closed", current.map, current.name})
end

local function take(current)
    local accepted, sequence, open = SilverfishTakeNativeInventoryViewRequest()
    if not accepted then return false end
    remove_display()
    observation = {context = current, sequence = sequence, state = open and "pending" or "closed"}
    if not can_view(current) then observation.state = "closed" end
    if not emit(current, sequence, observation.state == "pending") then observation.state = "error" end
    return true
end

function inventory_view.sync(state, phase)
    local current = context(state, phase)
    if not current or type(SilverfishSetNativeInventoryViewScope) ~= "function"
        or type(SilverfishTakeNativeInventoryViewRequest) ~= "function" then
        inventory_view.clear()
        return
    end
    if not same_context(current, binding) then
        inventory_view.clear()
        if not load_class(inventory_path) then return end
    end
    local allowed = can_view(current)
    if not SilverfishSetNativeInventoryViewScope(current.pawn:GetAddress(), allowed) then
        inventory_view.clear()
        return
    end
    binding = current
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
    if not offset or not total or offset > total or count ~= math.min(page_size, total - offset) then
        error("The authenticated inventory view page did not match its bounds", 0)
    end
    local records, seen, slots = {}, {}, {}
    for index = 7, #fields do
        local guid, source, slot, amount, class = fields[index]:match("^([0-9A-F]+)|([a-z]+)|(%d+)|(%d+)|(/Game/[%w_./%-]+)$")
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
        local equippable = (source == "backpack" or source == "equipment")
            and (weapon_profiles.kinds[class] ~= nil or class == camera_path)
        local name = item:gsub("_", " ")
        local usable = source == "backpack" and class == bandage_path
        if usable then name = "Bandage" end
        records[#records + 1] = {guid = guid, class = class, equippable = equippable, usable = usable,
            name = name, label = string.format("%s   %d x %s", sources[source], quantity, name)}
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
    if row.use_state == "confirming" then
        lines[#lines + 1] = "Use one Bandage? Home confirms. End cancels."
    elseif row.use_state == "using" then
        lines[#lines + 1] = "Bandage use pending"
    elseif row.use_state == "used" then
        lines[#lines + 1] = "Bandage used. Reopen inventory to refresh."
    elseif row.use_state == "refused" then
        lines[#lines + 1] = "Bandage use unavailable. Reopen inventory to refresh."
    elseif row.use_state == "selecting" then
        lines[#lines + 1] = "Page Up / Page Down selects. Home uses one Bandage. End closes."
    elseif row.equip_state == "confirming" then
        lines[#lines + 1] = "Equip " .. selected.name .. "? Home confirms. End cancels."
    elseif row.equip_state == "equipping" then
        lines[#lines + 1] = "Equip pending"
    elseif row.equip_state == "equipped" then
        lines[#lines + 1] = "Weapon equipped. Reopen inventory to refresh."
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
    observation.confirm_guid = nil
end

local function choose(current, action)
    if not ready(current, false) then return false end
    local row = observation
    local selected = row.records[row.selection]
    if action == "back" then
        if row.equip_state == "confirming" or row.use_state == "confirming" then
            select_item()
        elseif ready(current, true) then
            remove_display()
            row.state, row.equip_state, row.use_state = "closed", nil, nil
            emit(current, row.sequence, false)
            return true
        end
    elseif not row.request_guid and selected then
        if action == "up" or action == "down" then
            local direction = action == "up" and -1 or 1
            row.selection = math.min(#row.records, math.max(1, row.selection + direction))
            select_item()
        elseif action == "confirm" and selected.usable then
            if row.use_state == "confirming" and row.confirm_guid == selected.guid then
                row.request_guid, row.use_state = selected.guid, "using"
            else
                row.use_state, row.confirm_guid = "confirming", selected.guid
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
    for _, action in ipairs({"back", "up", "down", "confirm"}) do
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

local function fixture(current)
    if not headless or not can_view(current) or not same_context(current, binding) then
        error("The fixture requires an enrolled headless stock inventory viewer", 0)
    end
    local class, stack = load_class(inventory_path), current.controller.MenuStack
    if not class or not stack or stack:GetArrayNum() ~= 0 then
        error("The exact stock inventory menu fixture is unavailable", 0)
    end
    local created = {}
    current.controller:OpenMenu(class, created)
    if stack:GetArrayNum() ~= 0 or valid(created.CreatedMenu) or not take(current)
        or not observation or observation.state == "error" then
        error("The exact stock inventory menu did not publish the isolated key request", 0)
    end
    return "verified exact stock inventory key path"
end

function inventory_view.command(fields, state, phase)
    local operation = fields[2]
    if operation ~= "native-inventory-view-render" and operation ~= "native-inventory-view-unavailable"
        and operation ~= "native-inventory-view-fixture" and operation ~= "native-inventory-view-key-fixture"
        and operation ~= "native-inventory-view-equip-result"
        and operation ~= "native-inventory-view-use-result" then return end
    local current = context(state, phase)
    if not current or not same_context(current, binding) or not can_view(current) then
        error("Stock inventory view requires the exact standing connected guest", 0)
    end
    if operation == "native-inventory-view-fixture" and #fields == 2 then return fixture(current) end
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
            error("The authenticated Bandage result does not match the pending use", 0)
        end
        observation.use_state = fields[6]
        show()
        return "verified inventory Bandage result displayed"
    end
    if observation.state ~= "pending" then
        error("The authenticated inventory view is already displayed", 0)
    end
    if operation == "native-inventory-view-unavailable" and #fields == 4 then
        observation.state = "error"
        return "authenticated stock inventory view unavailable"
    end
    if operation ~= "native-inventory-view-render" or #fields < 6 or #fields > 6 + page_size then
        error("The authenticated stock inventory view response is incomplete", 0)
    end
    local offset, total, records = page(fields)
    observation.offset, observation.total, observation.count, observation.records = offset, total, #records, records
    observation.selection = #records > 0 and 1 or 0
    select_item()
    local ok, failure = pcall(render, current)
    observation.state = ok and "rendered" or "error"
    if not ok then
        remove_display()
        error(failure, 0)
    end
    return "verified stock inventory summary on the local screen"
end

function inventory_view.rows(state, phase, status_row)
    local current = context(state, phase)
    if not current or not observation or not same_context(current, observation.context) then return {} end
    local rows = {status_row("native-inventory-view", observation.state, observation.sequence, current.map, current.name,
        observation.offset or 0, observation.total or 0, observation.count or 0)}
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
