local form = {}
local surface = require("native_title_surface")
local fields = {
    {key = "campaign", label = "Save name", limit = 64,
        error = "Use letters, numbers, underscores or hyphens for the save name."},
    {key = "port", label = "UDP port", limit = 5,
        error = "Enter a UDP port from 1 to 65535."},
    {key = "bind", label = "Listening IP", limit = 45,
        error = "Enter a numeric IPv4 or IPv6 listening address."},
    {key = "password", label = "Password", limit = 512, secret = true,
        error = "Enter a password of up to 128 characters, or clear Require password."},
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

local function active_fields(draft, resident, show_name)
    local result = {}
    for index = show_name and 1 or 2, resident and #fields or #fields - 1 do
        if not draft.steam_access or index == 1 or fields[index].secret then
            result[#result + 1] = fields[index]
        end
    end
    return result
end

function form.rows(draft, resident, show_name)
    local rows = {}
    for _, field in ipairs(active_fields(draft, resident, show_name)) do
        if field.secret then
            rows[#rows + 1] = {toggle = {key = "require_password", label = "Require password",
                target = field.key, checked = draft.require_password == true}}
        end
        rows[#rows + 1] = {field = {key = field.key, label = field.label,
            limit = field.limit, secret = field.secret, value = draft[field.key]}}
    end
    if resident then
        rows[#rows + 1] = {toggle = {key = "friendly_fire", label = "Friendly fire",
            checked = draft.friendly_fire == true}}
    end
    return rows
end

function form.selection(view, draft, resident, saved_name, show_name)
    local invalid
    draft.require_password = resident and surface.checked(view, "require_password") == true
    draft.friendly_fire = resident and surface.checked(view, "friendly_fire") == true
    for _, field in ipairs(active_fields(draft, resident, show_name)) do
        local value = surface.read(view, field.key)
        if field.secret and not draft.require_password then value = "" end
        if value then draft[field.key] = value end
        if not invalid and (not value or not valid(field.key, value)) then invalid = field end
        if not invalid and field.secret and draft.require_password and value == "" then invalid = field end
    end
    if invalid then return nil, "title_host_form_invalid_" .. invalid.key, invalid.error end
    local name = saved_name or (show_name and draft.campaign) or ""
    if draft.steam_access then return {name, draft.steam_access, draft.password} end
    return {name, draft.bind, draft.port, resident and draft.password or nil}
end

return form
