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

local function viewer(state, phase)
    local controller = state.controller
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    if not event_path or (phase ~= "hosting" and phase ~= "connected") or not valid(controller) or not valid(pawn)
        or not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), state.world) or not same(pawn:GetWorld(), state.world)
        or detail.name ~= pawn:GetFullName() then return end
    local here = pawn:K2_GetActorLocation()
    local range, stack = pawn.InteractionRange, controller.MenuStack
    if not here or type(range) ~= "number" or range ~= range or range <= 0 or range > 1000
        or controller.Loading ~= false or not stack or stack:GetArrayNum() ~= 0
        or is_downed(pawn) ~= false then return end
    return {pawn = pawn, controller = controller, here = here, range = range}
end

local function nearby(binding, observer)
    if not binding or not same(binding.pawn, observer.pawn) or not valid(binding.crate)
        or binding.crate.Looted ~= true then return end
    local target = binding.crate:K2_GetActorLocation()
    if not target then return end
    local here = observer.here
    local distance = math.sqrt((here.X - target.X)^2 + (here.Y - target.Y)^2 + (here.Z - target.Z)^2)
    if distance ~= distance or distance >= observer.range then return end
    return { pawn = binding.pawn, controller = observer.controller, name = binding.name, crate = binding.profile.name,
        actor = binding.crate, distance = distance, capacity = binding.profile.capacity }
end

local function scope(state, phase, crate)
    local observer = viewer(state, phase)
    if not observer or not crate then return end
    return nearby(crate_input.update(state, phase, loot.profile(state.map, crate)), observer)
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
    local ok = file:write(table.concat({ "crate-view", sequence, current.id, current.name, current.crate, item.guid }, "\t"), "\n")
    local closed = file:close()
    return ok and closed
end

local function choose(action)
    if action == "close" then
        if selection.mode == "confirming" then
            selection.mode = "ready"
        elseif selection.mode == "ready" then
            dismissed = { id = current.id, crate = current.crate }
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
    for index = 6, #fields do
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
    local active = scope(state, phase, fields[5])
    if not active or active.name ~= fields[4] or not bounded_id(fields[3], true) then
        error("stock_crate_view_scope_unavailable", 0)
    end
    if command == "native-loot-view-render" and #fields >= 5 and #fields <= 5 + active.capacity then
        local id, signature = fields[3], table.concat(fields, "\t", 6)
        local closed = dismissed and dismissed.id == id and dismissed.crate == active.crate
        if not closed and #fields > 5 then
            if not current or current.id ~= id or current.crate ~= active.crate or not same(current.actor, active.actor)
                or current.name ~= active.name or current.signature ~= signature then
                remove_widget()
                selection = { index = 1, mode = "ready" }
            end
            active.id, active.signature, active.expires = id, signature, os.time() + 2
            active.items, current = items(fields), active
            paint()
        elseif #fields == 5 and current and current.crate == active.crate then
            view.clear()
        elseif current and current.id == id and current.crate == active.crate then
            current.expires = os.time() + 2
        end
        return "stock crate panel refreshed"
    end
    if not current or current.id ~= fields[3] or current.name ~= active.name or current.crate ~= active.crate
        or not same(current.actor, active.actor) or #fields ~= 6 then
        error("stock_crate_view_selection_unavailable", 0)
    end
    if command == "native-loot-view-fixture" and headless and valid(widget) and keys[fields[6]] then
        choose(fields[6])
        return "staged stock crate panel key dispatched"
    end
    if command == "native-loot-view-result" and fields[6] == "refresh" and selection.mode == "claiming" then
        selection.mode = "ready"
        paint()
        return "stock crate panel claim completed"
    end
    error("stock_crate_view_arguments_invalid", 0)
end

function view.sync(state, phase)
    if not current then return end
    local active = scope(state, phase, current.crate)
    if not active or not same(active.pawn, current.pawn) or not same(active.actor, current.actor)
        or os.time() > current.expires then
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
    local observer = viewer(state, phase)
    if not observer then return {} end
    local rows = {}
    for _, binding in pairs(crate_input.bound_in_world(state.world)) do
        local active = nearby(binding, observer)
        if active then
            rows[#rows + 1] = status_row("native-loot-view-nearby", state.map, active.crate, active.name, active.distance)
            if current and current.crate == active.crate and same(current.actor, active.actor)
                and same(current.pawn, active.pawn) then
                local mode = dismissed and dismissed.id == current.id and dismissed.crate == current.crate
                    and "closed" or selection.mode
                rows[#rows + 1] = status_row("native-loot-view", active.name, current.id, mode,
                    mode == "claiming" and sequence or "-", current.items[selection.index].guid, active.crate)
            end
        end
    end
    return rows
end

function view.start(bridge, downed)
    if enabled then
        event_path = bridge .. "/native-loot-view-events"
        is_downed = downed
    end
end

return view
