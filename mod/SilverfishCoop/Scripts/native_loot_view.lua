local loot = require("native_loot")
local crate_input = require("native_loot_crate_input")
local view = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_LOOT") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local helper_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local actions = { "close", "previous", "next", "take" }
local keys = { close = "End", previous = "PageUp", next = "PageDown", take = "Home" }
local event_path, is_downed, current, selection, widget, dismissed
local held, names, sequence = {}, {}, 0

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function bounded_id(text, lower)
    return type(text) == "string" and #text == 32 and text:find("[^0]")
        and text:match(lower and "^[0-9a-f]+$" or "^[0-9A-F]+$")
end

local function scope(state, phase)
    if not event_path or state.map ~= loot.map then return end
    local binding = crate_input.update(state, phase)
    local controller = state.controller
    if not binding or not valid(controller) then return end
    local pawn = binding.pawn
    if not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), state.world) or binding.name ~= pawn:GetFullName()
        or binding.crate.Looted ~= true then return end
    local here, target = pawn:K2_GetActorLocation(), binding.crate:K2_GetActorLocation()
    local range, stack = pawn.InteractionRange, controller.MenuStack
    if not here or not target or type(range) ~= "number" or range <= 0 or range > 1000
        or controller.Loading ~= false or not stack or stack:GetArrayNum() ~= 0
        or is_downed(pawn) ~= false
        or (here.X - target.X)^2 + (here.Y - target.Y)^2 + (here.Z - target.Z)^2 >= range^2 then return end
    return { pawn = pawn, controller = controller, name = binding.name }
end

local function remove_widget()
    local old = widget
    widget, held = nil, {}
    if valid(old) then old:RemoveFromParent() end
end

function view.clear()
    current, selection, dismissed = nil, nil, nil
    remove_widget()
end

local function paint()
    if not valid(widget) then
        local class = StaticFindObject(widget_path)
        if not valid(class) then
            LoadAsset(widget_path)
            class = StaticFindObject(widget_path)
        end
        local library = StaticFindObject(helper_path)
        if not valid(library) or not valid(class) then error("stock_crate_widget_unavailable", 0) end
        local candidate = library:Create(current.pawn, class, current.controller)
        if not exact(candidate, widget_path) or not same(candidate:GetOwningPlayer(), current.controller)
            or not exact(candidate.TextBlock_Notes, "/Script/UMG.TextBlock") then
            error("stock_crate_widget_unqualified", 0)
        end
        widget = candidate
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(29) ~= true or not widget:IsInViewport() then
            error("stock_crate_widget_unavailable", 0)
        end
        widget:SetDesiredSizeInViewport({ X = 520, Y = 210 })
        widget:SetPositionInViewport({ X = 42, Y = 138 }, false)
    end
    local lines = { "Shared crate" }
    for index, item in ipairs(current.items) do
        lines[#lines + 1] = string.format("%s %s", selection.index == index and ">" or " ", item.name)
    end
    lines[#lines + 1] = selection.mode == "claiming" and "Taking item…"
        or selection.mode == "confirming" and "Home confirms. End cancels."
        or "Page Up / Page Down selects. Home takes. End closes."
    local text = table.concat(lines, "\n")
    widget.TextBlock_Notes:SetText(FText(text))
    if widget.TextBlock_Notes:GetText():ToString() ~= text then error("stock_crate_widget_text_unverified", 0) end
end

local function submit(item)
    local file = io.open(event_path, "ab")
    if not file then return false end
    sequence = sequence + 1
    local ok = file:write(table.concat({ "crate-view", sequence, current.id, current.name, item.guid }, "\t"), "\n")
    local closed = file:close()
    return ok and closed
end

local function choose(action)
    if action == "close" then
        if selection.mode == "confirming" then
            selection.mode = "ready"
        elseif selection.mode == "ready" then
            dismissed = current.id
            remove_widget()
            return
        end
    elseif selection.mode ~= "claiming" then
        if action == "take" then
            if selection.mode == "confirming" then
                selection.mode = submit(current.items[selection.index]) and "claiming" or "ready"
            else
                selection.mode = "confirming"
            end
        else
            local step = action == "previous" and -1 or 1
            selection.index = math.max(1, math.min(#current.items, selection.index + step))
            selection.mode = "ready"
        end
    end
    paint()
end

local function items(fields)
    local parsed, seen = {}, {}
    for index = 5, #fields do
        local guid, path = fields[index]:match("^([0-9A-F]+)|(/Game/[%w_./%-]+)$")
        local name = path and #path <= 512 and path:match("%.([%w_%-]+)_C$")
        if not bounded_id(guid, false) or seen[guid] or not name then
            error("stock_crate_view_items_invalid", 0)
        end
        parsed[#parsed + 1] = { guid = guid, name = name:gsub("_", " ") }
        seen[guid] = true
    end
    return parsed
end

function view.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-loot-view-render" and command ~= "native-loot-view-fixture"
        and command ~= "native-loot-view-result" then return end
    local active = scope(state, phase)
    if not active or active.name ~= fields[4] or not bounded_id(fields[3], true) then
        error("stock_crate_view_scope_unavailable", 0)
    end
    if command == "native-loot-view-render" and #fields >= 4 and #fields <= 6 then
        local id, signature = fields[3], table.concat(fields, "\t", 5)
        if id ~= dismissed and #fields > 4 then
            if not current or current.id ~= id or current.name ~= active.name or current.signature ~= signature then
                remove_widget()
                selection = { index = 1, mode = "ready" }
            end
            active.id, active.signature, active.expires = id, signature, os.time() + 2
            active.items, current = items(fields), active
            paint()
        elseif #fields == 4 then
            view.clear()
        elseif current and current.id == id then
            current.expires = os.time() + 2
        end
        return "stock crate panel refreshed"
    end
    if not current or current.id ~= fields[3] or current.name ~= active.name or #fields ~= 5 then
        error("stock_crate_view_selection_unavailable", 0)
    end
    if command == "native-loot-view-fixture" and headless and valid(widget) and keys[fields[5]] then
        choose(fields[5])
        return "staged stock crate panel key dispatched"
    end
    if command == "native-loot-view-result" and fields[5] == "refresh" and selection.mode == "claiming" then
        selection.mode = "ready"
        paint()
        return "stock crate panel claim completed"
    end
    error("stock_crate_view_arguments_invalid", 0)
end

function view.sync(state, phase)
    if not current then return end
    local active = scope(state, phase)
    if not active or not same(active.pawn, current.pawn) or os.time() > current.expires then
        view.clear()
        return
    end
    if not valid(widget) then return end
    local pressed_action
    for _, action in ipairs(actions) do
        if not names[action] then
            local name = FName(keys[action], EFindName.FNAME_Find)
            if name:GetComparisonIndex() == 0 then error("stock_crate_view_key_unavailable", 0) end
            names[action] = { KeyName = name }
        end
        local pressed = current.controller:IsInputKeyDown(names[action])
        if pressed and held[action] == false and not pressed_action then pressed_action = action end
        held[action] = pressed
    end
    if pressed_action then choose(pressed_action) end
end

function view.rows(state, phase, status_row)
    local active = scope(state, phase)
    if not active then return {} end
    local mode = dismissed and current and dismissed == current.id and "closed"
        or current and selection.mode or "nearby"
    return { status_row("native-loot-view", active.name, current and current.id or "-", mode,
        mode == "claiming" and sequence or "-", current and current.items[selection.index].guid or "-") }
end

function view.start(bridge, downed)
    if enabled then
        event_path = bridge .. "/native-loot-view-events"
        is_downed = downed
    end
end

return view
