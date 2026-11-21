local form = {}
local editor = require("native_title_settings_widget")
local controls = "\nF8 Next field   F7 Start   F9 Back"
local fields = {
    {key = "campaign", label = "Campaign", hint = "Use letters, numbers, underscores or hyphens."},
    {key = "port", label = "UDP port", hint = "Enter a UDP port from 1 to 65535."},
    {key = "bind", label = "Listening IP", hint = "Enter a numeric IPv4 or IPv6 address."},
    {key = "password", label = "Password", hint = "Enter up to 128 characters.", default = "Blank lets anyone with the address join."},
}

local function ipv4(text)
    local octets = {text:match("^([0-9]+)%.([0-9]+)%.([0-9]+)%.([0-9]+)$")}
    if #octets ~= 4 then return false end
    for _, octet in ipairs(octets) do
        if #octet > 3 or (#octet > 1 and octet:sub(1, 1) == "0") or tonumber(octet) > 255 then return false end
    end
    return true
end

local function ipv6_groups(text, embedded_ipv4)
    if text == "" then return 0 end
    local total, consumed = 0, 0
    for group in (text .. ":"):gmatch("([^:]*):") do
        consumed = consumed + #group + 1
        if #group <= 4 and group:match("^[0-9A-Fa-f]+$") then
            total = total + 1
        elseif embedded_ipv4 and consumed == #text + 1 and ipv4(group) then
            total = total + 2
        else
            return
        end
    end
    return total
end

local function numeric_address(value)
    if ipv4(value) then return true end
    if not value:find(":", 1, true) then return false end
    local index = value:find("::", 1, true)
    if not index then return ipv6_groups(value, true) == 8 end
    local before = ipv6_groups(value:sub(1, index - 1), false)
    local after = ipv6_groups(value:sub(index + 2), true)
    return before and after and before + after < 8
end

local function valid(field, value)
    if value == "" then return true end
    if field == "campaign" then return #value <= 64 and value:match("^[A-Za-z0-9][A-Za-z0-9_-]*$") ~= nil end
    if field == "port" then
        return #value <= 5 and value:match("^[1-9][0-9]*$") ~= nil and tonumber(value) <= 65535
    end
    if field == "password" then return utf8.len(value) <= 128 end
    return #value <= 45 and numeric_address(value)
end

function form.remove(draft)
    if not draft.handle then return true end
    local handle = draft.handle
    draft.handle = nil
    return editor.remove(handle)
end

local function render(draft, state, phase, hint, text)
    local field = fields[draft.index]
    if not form.remove(draft) then error("title_host_form_editor_unavailable", 0) end
    local caption = "Host co-op / " .. field.label .. "\n" .. (hint or field.default or "Blank keeps the configured value.") .. controls
    local handle = editor.mount(state, phase, field.key, caption, text or draft.values[field.key])
    draft.handle = handle
    editor.focus(handle, state, phase)
end

function form.open(state, phase, direct)
    local draft = {index = 1, count = direct and #fields or #fields - 1,
        values = {campaign = "", port = "", bind = "", password = direct and "" or nil}}
    local ok, failure = pcall(render, draft, state, phase)
    if not ok then
        form.remove(draft)
        error(failure, 0)
    end
    return draft
end

local function capture(draft, state, phase)
    local field = fields[draft.index]
    local ok, text = pcall(editor.read, draft.handle, state, phase)
    if not ok and text ~= "title_settings_invalid_text" then return false, "title_host_form_editor_unavailable" end
    if not ok or not valid(field.key, text) then
        render(draft, state, phase, field.hint, ok and text or nil)
        return false, "title_host_form_invalid_" .. field.key
    end
    draft.values[field.key] = text
    return true
end

function form.next(draft, state, phase)
    local captured, reason = capture(draft, state, phase)
    if not captured then return false, reason end
    draft.index = draft.index % draft.count + 1
    render(draft, state, phase)
    return true
end

function form.selection(draft, state, phase)
    local captured, reason = capture(draft, state, phase)
    if not captured then return nil, reason end
    local values = draft.values
    return {values.campaign, values.bind, values.port, values.password}
end

function form.fixture_write(draft, state, phase, field, value)
    if fields[draft.index].key ~= field then return false end
    return editor.write(draft.handle, state, phase, value) == value
end

return form
