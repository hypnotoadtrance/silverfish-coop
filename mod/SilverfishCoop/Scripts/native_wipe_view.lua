local wipe_view = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_WIPE_VIEW") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local display_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local black_stone = "/Game/Classes/Items/Artifacts/Art_BlackStone.Art_BlackStone_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local text_path = "/Script/UMG.TextBlock"
local sources = {backpack = "Backpack", equipment = "Equipment", armor = "Armor", helmet = "Helmet"}
local keys = {up = "PageUp", down = "PageDown", toggle = "SpaceBar", submit = "Home", back = "End"}
local actions = {"back", "up", "down", "toggle", "submit"}
local page_size, max_items, lease_seconds = 16, 8194, 2
local input_keys, held, sequence = {}, {}, 0
local event_path, is_downed, view, display

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function authority_is_current(current)
    local pawn, controller, world = current.pawn, current.controller, current.world
    return valid(world) and valid(world.NetDriver)
        and world:GetFullName():match("^%S+%s+(/[^%.:]+)%.") == current.map
        and valid(world.NetDriver.ServerConnection) == current.client
        and valid(pawn) and valid(controller) and not pawn:IsActorBeingDestroyed()
        and pawn:HasAuthority() == (not current.client) and pawn:IsLocallyControlled()
        and controller:IsLocalController() and same(pawn.Controller, controller) and same(controller.Pawn, pawn)
        and same(pawn:GetWorld(), world) and same(controller:GetWorld(), world)
        and pawn:GetFullName() == current.name
end

local function context(state, phase)
    local client = phase == "connected"
    if not event_path or (not client and phase ~= "hosting") or state.listening ~= true
        or state.server ~= client or type(state.map) ~= "string" or #state.map > 512
        or not state.map:match("^/Game/Classes/Maps/[%w_/]+$") then return nil end
    local detail, controller = state.local_pawn, state.controller
    if not detail or not exact(detail.pawn, pawn_path) or not exact(controller, controller_path)
        or not same(detail.controller_object, controller) or type(detail.name) ~= "string"
        or #detail.name > 512 or detail.name:find("%c") then return nil end
    local current = {world = state.world, map = state.map, name = detail.name, pawn = detail.pawn,
        controller = controller, client = client}
    if authority_is_current(current) then return current end
end

local function same_context(current, previous)
    return current and previous and current.map == previous.map and current.name == previous.name
        and current.client == previous.client and same(current.world, previous.world)
        and same(current.pawn, previous.pawn) and same(current.controller, previous.controller)
end

local function downed(current)
    return type(is_downed) == "function" and is_downed(current.pawn) == true
end

local function clock(current)
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return nil end
    local seconds = statics:GetRealTimeSeconds(current.pawn)
    if type(seconds) == "number" and seconds >= 0 and seconds < math.huge then return seconds end
end

local function remove_display()
    local previous = display
    display, held = nil, {}
    if valid(previous) then previous:RemoveFromParent() end
end

function wipe_view.clear()
    view = nil
    remove_display()
end

local function invalidate(reason)
    if view then
        print("[SilverfishCoop] Closed wipe selection " .. view.id .. " after " .. reason .. "\n")
        wipe_view.clear()
    end
end

local function current_time(current, row)
    if not current or not authority_is_current(current) then return nil, "local player or map changed" end
    if not downed(current) then return nil, "local downed state ended" end
    if row.deadline ~= 0 and os.time() >= row.deadline then return nil, "the selection deadline passed" end
    local now = clock(current)
    if now and now >= row.leased_at and now < row.leased_at + lease_seconds then return now end
    return nil, "the draft lease expired"
end

local function can_choose(row)
    local current = row.context
    return (row.state == "selecting" or row.state == "confirming" or row.state == "refused")
        and current.controller.Loading == false and current.controller.MenuStack:GetArrayNum() == 0
        and valid(display) and same(display:GetOwningPlayer(), current.controller) and display:IsInViewport()
end

local function integer(value, maximum)
    if type(value) ~= "string" or #value > 16 or not value:match("^%d+$") then return nil end
    local n = math.tointeger(tonumber(value))
    if n and n >= 0 and n <= maximum then return n end
end

local function hex32(value, pattern)
    return type(value) == "string" and #value == 32 and value ~= string.rep("0", 32)
        and value:match(pattern) ~= nil
end

local function character_id(value, client)
    if not client then return value == "host" end
    if type(value) ~= "string" then return false end
    local a, b, c, d, e = value:match("^([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)$")
    return a and #a == 8 and #b == 4 and #c == 4 and #d == 4 and #e == 12
        and hex32(a .. b .. c .. d .. e, "^[0-9a-f]+$")
end

local function page(fields, row, offset)
    local count = #fields - 11
    if (offset % page_size ~= 0) or (offset ~= 0 and offset >= row.total)
        or count ~= math.min(page_size, row.total - offset) then
        error("The frozen wipe inventory page is incomplete", 0)
    end
    local records, locked_ordinal, guids, positions = {}, row.locked_ordinal, {}, {}
    for index = 12, #fields do
        local guid, source, raw_slot, raw_amount, class, policy =
            fields[index]:match("^([0-9A-F]+)|([a-z]+)|(%d+)|(%d+)|(/Game/[%w_/%-%%]+%.[%w_%-%%]+_C)|([a-z]+)$")
        local slot = integer(raw_slot, (source == "armor" or source == "helmet") and 0 or 4095)
        local amount = integer(raw_amount, 2147483647)
        local is_locked = source == "backpack" and class == black_stone
        if not hex32(guid, "^[0-9A-F]+$") or not sources[source] or not slot or not amount or amount < 1
            or #class > 512 or is_locked ~= (policy == "locked")
            or (policy ~= "selectable" and policy ~= "locked" and (policy ~= "excluded" or source ~= "backpack")) then
            error("The frozen wipe inventory contains an invalid stock item", 0)
        end
        local ordinal, position = offset + index - 11, source .. "|" .. slot
        if guids[guid] or positions[position] or (row.guid_ordinals[guid] and row.guid_ordinals[guid] ~= ordinal)
            or (row.position_ordinals[position] and row.position_ordinals[position] ~= ordinal)
            or (row.verified[ordinal] and row.verified[ordinal] ~= fields[index]) then
            error("The frozen wipe inventory repeats or changes a stock item", 0)
        end
        guids[guid], positions[position] = ordinal, ordinal
        if is_locked then
            if row.locked ~= 1 or (locked_ordinal and locked_ordinal ~= ordinal) then
                error("The frozen wipe inventory changed its required stock retention", 0)
            end
            locked_ordinal = ordinal
        end
        local item = class:match("%.([%w_%-%%]+)_C$"):gsub("_", " ")
        records[#records + 1] = {guid = guid, policy = policy,
            label = string.format("%s   %d x %s", sources[source], amount, item:sub(1, 52))}
    end
    if row.total == count and (locked_ordinal ~= nil) ~= (row.locked == 1) then
        error("The frozen wipe inventory changed its required stock retention", 0)
    end
    row.locked_ordinal = locked_ordinal
    for guid, ordinal in pairs(guids) do row.guid_ordinals[guid] = ordinal end
    for position, ordinal in pairs(positions) do row.position_ordinals[position] = ordinal end
    for index = 12, #fields do row.verified[offset + index - 11] = fields[index] end
    return records
end

local function show(row)
    local lines = {"Party wipe    Choose what to keep",
        string.format("Selected %d of %d%s", row.count, row.cap,
            row.locked > 0 and "    Black Stone kept automatically" or "")}
    lines[#lines + 1] = row.total == 0 and "Your frozen inventory is empty."
        or string.format("Items %d-%d of %d", row.offset + 1, row.offset + #row.records, row.total)
    for index, item in ipairs(row.records) do
        local mark = row.selected[item.guid] and "[x]" or "[ ]"
        if item.policy == "locked" then mark = "[Kept]" end
        if item.policy == "excluded" then mark = "[Unavailable]" end
        lines[#lines + 1] = (index == row.focus and "> " or "  ") .. mark .. " " .. item.label
    end
    if row.state == "confirming" then
        lines[#lines + 1] = row.count == 0 and "Submit without selecting any items? Home confirms. End cancels."
            or "Submit your selection? Home confirms. End cancels."
    elseif row.state == "submitting" then
        lines[#lines + 1] = "Waiting for the host to confirm your selection."
    elseif row.state == "paging" then
        lines[#lines + 1] = "Loading the next frozen inventory page."
    else
        if row.notice then lines[#lines + 1] = row.notice end
        lines[#lines + 1] = "Page Up / Page Down selects. Space toggles. Home reviews."
    end
    local message = table.concat(lines, "\n")
    display.Text = FText(message)
    display.TextBlock_Notes:SetText(FText(message))
    if display.TextBlock_Notes:GetText():ToString() ~= message then
        error("The passive wipe widget text did not verify", 0)
    end
end

local function render(current, row)
    local class = StaticFindObject(display_path)
    if not valid(class) then LoadAsset(display_path) end
    class = StaticFindObject(display_path)
    local widgets = StaticFindObject(widgets_path)
    if not valid(class) or not valid(widgets) then error("The passive wipe widget is unavailable", 0) end
    local widget = widgets:Create(current.pawn, class, current.controller)
    if not exact(widget, display_path) or not same(widget:GetOwningPlayer(), current.controller)
        or not exact(widget.TextBlock_Notes, text_path) then
        error("The passive wipe widget did not qualify", 0)
    end
    display = widget
    widget:SetVisibility(3)
    if widget:AddToPlayerScreen(35) ~= true or not widget:IsInViewport() then
        error("The passive wipe widget could not be added to the local screen", 0)
    end
    widget:SetDesiredSizeInViewport({X = 640, Y = 620})
    widget:SetPositionInViewport({X = 36, Y = 88}, false)
    show(row)
end

local function emit(kind, row, values)
    local file = io.open(event_path, "ab")
    if not file then return false end
    sequence = sequence + 1
    local fields = {kind, sequence, row.id, row.context.map, row.context.name, row.character}
    for _, value in ipairs(values) do fields[#fields + 1] = value end
    local written = file:write(table.concat(fields, "\t"), "\n")
    local closed = file:close()
    row.sequence = sequence
    return written and closed
end

local function choose(row, action)
    if not can_choose(row) then return false end
    local item = row.records[row.focus]
    row.notice = nil
    if action == "submit" then
        if row.state == "confirming" then
            local selected = {}
            for guid in pairs(row.selected) do selected[#selected + 1] = guid end
            table.sort(selected)
            table.insert(selected, 1, #selected)
            row.state = emit("native-wipe-view-submit", row, selected) and "submitting" or "refused"
            if row.state == "refused" then row.notice = "Your selection could not be sent." end
        else
            row.state = "confirming"
        end
    else
        row.state = "selecting"
        if action == "toggle" and item and item.policy == "selectable" then
            if row.selected[item.guid] then
                row.selected[item.guid], row.count = nil, row.count - 1
            elseif row.count < row.cap then
                row.selected[item.guid], row.count = true, row.count + 1
            else
                row.notice = "Retention slots are full."
            end
        elseif (action == "up" or action == "down") and item then
            local step = action == "up" and -1 or 1
            local focus = row.focus + step
            if focus >= 1 and focus <= #row.records then
                row.focus = focus
            else
                local offset = row.offset + step * page_size
                if offset >= 0 and offset < row.total and emit("native-wipe-view-page", row, {offset}) then
                    row.state, row.pending_offset = "paging", offset
                end
            end
        end
    end
    show(row)
    return true
end

local function poll_keys()
    local row = view
    if not row then return end
    local now, reason = current_time(row.context, row)
    if not now then invalidate(reason) return end
    if not can_choose(row) then held = {} return end
    local changed
    for _, action in ipairs(actions) do
        if not input_keys[action] then
            local key = FName(keys[action], EFindName.FNAME_Find)
            if key:GetComparisonIndex() == 0 then error("The stock wipe key did not resolve", 0) end
            input_keys[action] = {KeyName = key}
        end
        local pressed = row.context.controller:IsInputKeyDown(input_keys[action])
        if type(pressed) ~= "boolean" then error("The stock wipe key state did not verify", 0) end
        if pressed and held[action] == false and not changed then changed = action end
        held[action] = pressed
    end
    if changed then choose(row, changed) end
end

function wipe_view.sync(state, phase)
    if not view then return end
    local current = context(state, phase)
    if not same_context(current, view.context) then invalidate("local session identity changed") return end
    local now, reason = current_time(current, view)
    if not now then invalidate(reason) end
end

function wipe_view.command(fields, state, phase)
    local op = fields[2]
    if op ~= "native-wipe-view-render" and op ~= "native-wipe-view-lease" and op ~= "native-wipe-view-clear"
        and op ~= "native-wipe-view-result" and op ~= "native-wipe-view-key-fixture" then return end
    local current = context(state, phase)
    local id, character = fields[3], fields[6]
    if not current or fields[4] ~= current.map or fields[5] ~= current.name
        or not hex32(id, "^[0-9a-f]+$") or not character_id(character, current.client) then
        error("The wipe view command does not match the exact local player", 0)
    end
    local matches = view and view.id == id and view.character == character and same_context(current, view.context)
    if op == "native-wipe-view-clear" and #fields == 6 then
        if matches then wipe_view.clear() end
        return "qualified wipe view cleared"
    end
    if not downed(current) then error("The wipe view requires the exact downed local player", 0) end
    if op == "native-wipe-view-render" then
        local deadline, limit = integer(fields[7], 9007199254740991), integer(fields[8], 10)
        local locked, offset, total = integer(fields[9], 1), integer(fields[10], max_items), integer(fields[11], max_items)
        local now = clock(current)
        if not now or not deadline or (deadline ~= 0 and os.time() >= deadline) or not limit or limit == 0
            or not locked or not offset or not total or locked > total then
            error("The authoritative wipe draft deadline or inventory limits are invalid", 0)
        end
        local row
        if not matches then
            if offset ~= 0 then error("The wipe view must begin with the first frozen page", 0) end
            row = {id = id, character = character, context = current, deadline = deadline, limit = limit,
                locked = locked, cap = math.max(0, limit - locked), total = total, leased_at = now,
                state = "selecting", count = 0, sequence = 0, selected = {}, verified = {},
                guid_ordinals = {}, position_ordinals = {}}
        else
            row = view
            if not current_time(current, row) or row.state ~= "paging" or row.pending_offset ~= offset
                or row.total ~= total or row.limit ~= limit or row.locked ~= locked or row.deadline ~= deadline then
                error("The frozen wipe page does not match the current request", 0)
            end
        end
        local records = page(fields, row, offset)
        row.focus = #records == 0 and 0 or (row.offset and offset < row.offset and #records or 1)
        row.offset, row.records, row.pending_offset, row.state, row.leased_at = offset, records, nil, "selecting", now
        if not matches then
            invalidate("the active wipe draft changed")
            view = row
        end
        local ok, failure = pcall(function()
            if matches then show(row) else render(current, row) end
        end)
        if not ok then wipe_view.clear() error(failure, 0) end
        return "verified frozen wipe inventory displayed"
    end
    local now = matches and current_time(current, view)
    if not now then error("The wipe view command does not match a fresh active draft", 0) end
    if op == "native-wipe-view-lease" and #fields == 7 and integer(fields[7], 9007199254740991) == view.deadline then
        view.leased_at = now
        return "verified wipe view lease renewed"
    end
    if op == "native-wipe-view-key-fixture" and #fields == 7 and headless and keys[fields[7]]
        and choose(view, fields[7]) then
        return "verified headless wipe selection key"
    end
    if op == "native-wipe-view-result" and #fields == 7 then
        if fields[7] == "accepted" and view.state == "submitting" then
            view.state = "accepted"
            remove_display()
            return "verified wipe selection accepted"
        end
        if fields[7] == "refused" and (view.state == "submitting" or view.state == "paging") then
            view.state, view.pending_offset, view.notice = "refused", nil, "The host declined the last request."
            show(view)
            return "verified wipe request refused"
        end
    end
    error("Invalid wipe view command for the current draft", 0)
end

function wipe_view.rows(state, phase, status_row)
    local row = view
    if not row or not same_context(context(state, phase), row.context) then return {} end
    local focus = row.records[row.focus]
    return {status_row("native-wipe-view", row.state, row.id, row.context.map, row.context.name, row.character,
        row.deadline, row.offset, row.total, focus and focus.guid or "-", row.count, row.cap, row.sequence)}
end

function wipe_view.start(bridge, downed_check)
    event_path = enabled and type(bridge) == "string" and bridge ~= "" and bridge .. "/native-wipe-view-events" or nil
    is_downed = downed_check
    if not event_path then return end
    LoopInGameThreadWithDelay(16, function()
        local ok, failure = pcall(poll_keys)
        if not ok then
            wipe_view.clear()
            print("[SilverfishCoop] Wipe selection unavailable: " .. tostring(failure) .. "\n")
        end
    end)
end

return wipe_view
