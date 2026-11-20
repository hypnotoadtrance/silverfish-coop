local panel = {}
local title = require("native_title_entry")

local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local keys = {host = "F7", join = "F8", hide = "F9"}
local actions = {
    ["host"] = {host = true}, ["join"] = {join = true}, ["host,join"] = {host = true, join = true},
}
local session_path, request_path, loop, current, session, report
local dismissed, submitted
local input_keys = {}

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function same_title(first, second)
    return first and second and same(first.world, second.world) and same(first.controller, second.controller)
        and same(first.menu, second.menu) and same(first.viewport, second.viewport)
end

local function read_offer()
    local file = io.open(session_path, "rb")
    if not file then return end
    local contents = file:read(129)
    file:close()
    if not contents or #contents > 128 then return end
    local generation, choices, timestamp =
        contents:match("^1\t([0-9a-f]+)\t([a-z,%-]+)\t([0-9]+)\r?\n$")
    local expires = timestamp and #timestamp <= 10 and math.tointeger(tonumber(timestamp))
    local now = os.time()
    if not generation or #generation ~= 32 or not actions[choices] or not expires
        or expires < now or expires > now + 10 then return end
    return {generation = generation, choices = choices, expires = expires}
end

local function remove_display()
    local old = session
    session = nil
    if old and valid(old.widget) then old.widget:RemoveFromParent() end
end

local function message(text)
    session.widget.TextBlock_Notes:SetText(FText(text))
    if session.widget.TextBlock_Notes:GetText():ToString() ~= text then error("title_panel_text_unavailable", 0) end
end

local function mount(context, offer)
    local class = StaticFindObject(widget_path)
    if not valid(class) then
        LoadAsset(widget_path)
        class = StaticFindObject(widget_path)
    end
    local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    local layout = StaticFindObject("/Script/UMG.Default__WidgetLayoutLibrary")
    if not valid(class) or not valid(widgets) or not valid(layout) then error("title_panel_widget_unavailable", 0) end
    local widget = widgets:Create(context.controller, class, context.controller)
    if not exact(widget, widget_path) or not same(widget:GetOwningPlayer(), context.controller)
        or not exact(widget.TextBlock_Notes, "/Script/UMG.TextBlock") then
        error("title_panel_widget_unqualified", 0)
    end
    session = {context = context, offer = offer, widget = widget, held = {}}
    widget:SetVisibility(3) -- Self and children stay out of stock pointer hit testing.
    widget.TextBlock_Notes:SetColorAndOpacity({SpecifiedColor = {R = 0.60, G = 0.90, B = 1.0, A = 1.0}, ColorUseRule = 0})
    if widget:AddToPlayerScreen(110) ~= true or not widget:IsInViewport() then error("title_panel_mount_unverified", 0) end
    local size, scale = layout:GetViewportSize(context.controller), layout:GetViewportScale(context.controller)
    local width = size and tonumber(size.X)
    if not width or width < 1 or width > 16384 or type(scale) ~= "number" or not (scale >= 0.25 and scale <= 8) then
        error("title_panel_viewport_size_unavailable", 0)
    end
    widget:SetDesiredSizeInViewport({X = 344, Y = 148})
    widget:SetPositionInViewport({X = math.max(20, width / scale - 364), Y = 62}, false)
    local lines = {"Direct IP co-op"}
    if actions[offer.choices].host then lines[#lines + 1] = "F7   Host a campaign" end
    if actions[offer.choices].join then lines[#lines + 1] = "F8   Join with an invite" end
    lines[#lines + 1] = "F9   Hide"
    message(table.concat(lines, "\n"))
end

local function request_id()
    local library = StaticFindObject("/Script/Engine.Default__KismetGuidLibrary")
    local value = valid(library) and library:NewGuid()
    if not value then return end
    local words = {}
    for _, key in ipairs({"A", "B", "C", "D"}) do
        local number = math.tointeger(value[key])
        if not number or number < -0x80000000 or number > 0xffffffff then return end
        words[#words + 1] = number & 0xffffffff
    end
    local id = string.format("%08x%08x%08x%08x", table.unpack(words))
    if id:find("[^0]") then return id end
end

local function write_request(generation, action)
    local pending = io.open(request_path, "rb")
    if pending then
        pending:close()
        return false
    end
    local id = request_id()
    if not id then return false end
    local temporary = request_path .. "." .. id .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local written = file:write(table.concat({"1", generation, id, action}, "\t"), "\n")
    local closed = file:close()
    -- Windows rename refuses an existing destination. Never remove or replace a pending request.
    if written and closed and os.rename(temporary, request_path) then return true end
    os.remove(temporary)
    return false
end

local function can_choose(context)
    return session and same_title(context, session.context) and os.time() <= session.offer.expires
        and valid(session.widget) and session.widget:IsInViewport()
end

local function choose(action, context)
    if not can_choose(context) then return false, "title_panel_not_ready" end
    if action == "hide" then
        dismissed = session.offer.generation
        report = {state = "hidden", choices = session.offer.choices}
        remove_display()
        return true
    end
    if not actions[session.offer.choices][action] then return false, "title_action_not_offered" end
    if submitted == session.offer.generation then return false, "title_request_already_pending" end
    local latest = read_offer()
    if not latest or latest.generation ~= session.offer.generation or latest.choices ~= session.offer.choices then
        remove_display()
        report = nil
        return false, "title_offer_changed"
    end
    if write_request(latest.generation, action) then
        submitted = latest.generation
        report = {state = "pending", choices = latest.choices}
        message("Direct IP co-op\nStarting " .. (action == "host" and "host" or "join") .. "...\nF9   Hide")
        return true
    end
    report = {state = "unavailable", choices = latest.choices, reason = "title_request_write_failed"}
    message("Could not start co-op. Try again or press F9 to hide.")
    return false, report.reason
end

local function poll()
    if not session then return end
    local context = current and title.probe(current.state, current.phase)
    if not can_choose(context) then
        remove_display()
        report = nil
        return
    end
    local pending = submitted == session.offer.generation
    local selected, rises = nil, 0
    for _, action in ipairs({"hide", "host", "join"}) do
        if action == "hide" or not pending and actions[session.offer.choices][action] then
            if not input_keys[action] then
                local key = FName(keys[action], EFindName.FNAME_Find)
                if key:GetComparisonIndex() == 0 then error("title_panel_key_unavailable", 0) end
                input_keys[action] = {KeyName = key}
            end
            local down = context.controller:IsInputKeyDown(input_keys[action])
            if type(down) ~= "boolean" then error("title_panel_key_unqualified", 0) end
            if down and session.held[action] == false then selected, rises = action, rises + 1 end
            session.held[action] = down
        end
    end
    if rises == 1 then choose(selected, context) end
end

function panel.sync(state, phase)
    current = nil
    if not session_path then return end
    local context = title.probe(state, phase)
    local offer = context and read_offer()
    if not offer then
        remove_display()
        report = nil
        return
    end
    current = {state = state, phase = phase}
    if dismissed == offer.generation then return end
    if session and same_title(context, session.context) and session.offer.generation == offer.generation
        and session.offer.choices == offer.choices then
        session.offer = offer
        return
    end
    remove_display()
    if submitted == offer.generation then return end
    local ok, failure = pcall(mount, context, offer)
    local reason = type(failure) == "string" and failure:match("^title_panel_[a-z_]+$") or "title_panel_mount_failed"
    report = {state = ok and "ready" or "unavailable", choices = offer.choices,
        reason = not ok and reason or nil}
    if not ok then remove_display() end
end

function panel.command(fields, state, phase)
    if fields[2] ~= "native-title-panel-press-fixture" then return end
    if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then
        error("title_panel_fixture_unavailable", 0)
    end
    local action = fields[3]
    if #fields ~= 3 or not keys[action] then error("title_panel_fixture_arguments", 0) end
    local accepted, reason = choose(action, title.probe(state, phase))
    if not accepted then error(reason, 0) end
    return "qualified staged title control dispatched"
end

function panel.rows(state, phase, status_row)
    if not report or not title.probe(state, phase) then return {} end
    local mounted = session and valid(session.widget) and session.widget:IsInViewport() or false
    return {status_row("native-title-panel", state.map, report.state, report.choices, mounted and 1 or 0, report.reason or "-")}
end

function panel.start(bridge)
    if loop or type(bridge) ~= "string" or bridge == "" then return end
    session_path = bridge .. "/title-handoff-session.tsv"
    request_path = bridge .. "/title-handoff-request.tsv"
    loop = LoopInGameThreadWithDelay(16, function()
        if not pcall(poll) then
            local choices = session and session.offer.choices
            remove_display()
            report = choices and {state = "unavailable", choices = choices, reason = "title_panel_input_unavailable"} or nil
        end
    end)
end

return panel
