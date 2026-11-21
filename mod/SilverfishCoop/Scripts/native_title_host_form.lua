local form = {}
local surface = require("native_title_surface")
local fields = {
    {key = "campaign", label = "Campaign name", hint = "Default campaign", limit = 64,
        error = "Use letters, numbers, underscores or hyphens for the campaign name."},
    {key = "port", label = "UDP port", hint = "Default port", limit = 5,
        error = "Enter a UDP port from 1 to 65535."},
    {key = "bind", label = "Listening IP", hint = "Default listening address", limit = 45,
        error = "Enter a numeric IPv4 or IPv6 listening address."},
    {key = "password", label = "Password", hint = "Optional", limit = 512, secret = true,
        error = "Enter a password of up to 128 characters."},
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

function form.rows(draft, direct, saved_name)
    local rows = {}
    for index = saved_name and 2 or 1, direct and #fields or #fields - 1 do
        local field = fields[index]
        rows[#rows + 1] = {field = {key = field.key, label = field.label, hint = field.hint,
            limit = field.limit, secret = field.secret, value = draft[field.key]}}
    end
    return rows
end

function form.selection(view, draft, direct, saved_name)
    local invalid
    for index = saved_name and 2 or 1, direct and #fields or #fields - 1 do
        local field = fields[index]
        local value = surface.read(view, field.key)
        if value then draft[field.key] = value end
        if not invalid and (not value or not valid(field.key, value)) then invalid = field end
    end
    if invalid then return nil, "title_host_form_invalid_" .. invalid.key, invalid.error end
    return {saved_name or draft.campaign, draft.bind, draft.port, direct and draft.password or nil}
end

return form
