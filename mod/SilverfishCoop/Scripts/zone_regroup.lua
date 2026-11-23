local regroup = {}
local zone_lease = require("native_zone_lease")
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local path, notice, widget, widget_text, retry_at

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function remove_widget()
    local previous = widget
    widget, widget_text = nil, nil
    if valid(previous) then pcall(function() previous:RemoveFromParent() end) end
end

function regroup.start(bridge)
    path = bridge .. "/native-zone-roster"
end

function regroup.clear()
    notice = nil
    remove_widget()
end

local function labels(map)
    local result = {}
    local body = zone_lease.roster(path)
    if not body or #body >= 8192 or body:match("^[^\r\n]+") ~= map then return result end
    for pawn, role, name in body:gmatch("\n(Player_FP_C /Game/[^%c]+)\t(guest%d?)\t([^%c]+)") do
        if #pawn <= 512 and #name <= 256 and (role == "guest" or role:match("^guest[2-4]$")) then
            result[pawn] = {role = role, name = name}
        end
    end
    return result
end

local function paint(controller, pawn, text)
    if not valid(widget) or not same(widget:GetOwningPlayer(), controller) then
        remove_widget()
        local class = StaticFindObject(widget_path)
        if not valid(class) then
            LoadAsset(widget_path)
            class = StaticFindObject(widget_path)
        end
        local library = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(library) then return false end
        local candidate = library:Create(pawn, class, controller)
        if not valid(candidate) or not same(candidate:GetClass(), class) or not same(candidate:GetOwningPlayer(), controller)
            or not valid(candidate.TextBlock_Notes)
            or not same(candidate.TextBlock_Notes:GetClass(), StaticFindObject("/Script/UMG.TextBlock")) then
            return false
        end
        widget = candidate
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(28) ~= true or not widget:IsInViewport() then return false end
        widget:SetDesiredSizeInViewport({X = 600, Y = 115})
        widget:SetPositionInViewport({X = 40, Y = 95}, false)
    end
    if widget_text == text then return true end
    widget.TextBlock_Notes:SetText(FText(text))
    widget_text = text
    return widget.TextBlock_Notes:GetText():ToString() == text
end

function regroup.show(state, exit, blockers)
    local roster = labels(state.map)
    for _, blocker in ipairs(blockers) do
        local identity = roster[blocker.pawn]
        blocker.role = identity and identity.role or blocker.slot
        blocker.name = identity and identity.name or "Guest " .. (blocker.role:match("%d+$") or "1")
    end
    table.sort(blockers, function(first, second) return first.role < second.role end)
    notice = {map = state.map, exit = exit, blockers = blockers, display = headless and "headless" or "hidden"}
    if headless then return end

    local controller, pawn = state.controller, state.local_pawn.pawn
    if not valid(controller) or not same(controller:GetClass(), StaticFindObject(controller_path))
        or not controller:IsLocalController() or not same(controller.Pawn, pawn)
        or controller.Loading ~= false or not controller.MenuStack or controller.MenuStack:GetArrayNum() ~= 0 then
        remove_widget()
        return
    end
    if os.time() < (retry_at or 0) then return end
    local lines = {"Move within 15 m of these teammates to travel."}
    for _, blocker in ipairs(blockers) do lines[#lines + 1] = blocker.name end
    local ok, shown = pcall(paint, controller, pawn, table.concat(lines, "\n"))
    if ok and shown then
        notice.display = "visible"
    else
        retry_at = os.time() + 2
        remove_widget()
    end
end

function regroup.rows(rows, status_row)
    if not notice then return rows end
    for _, blocker in ipairs(notice.blockers) do
        rows[#rows + 1] = status_row("native-zone-regroup", notice.map, notice.exit, notice.display, blocker.pawn,
            blocker.role, blocker.name, blocker.meters)
    end
    return rows
end

return regroup
