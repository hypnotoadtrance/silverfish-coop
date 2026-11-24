local canary = {}
local guest_save = require("guest_save")
local shot_view = require("guest_shot_view")

local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local native_scope = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local held_fire_enabled = native_scope and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE") == "1"
local enabled = native_scope and (fixtures or held_fire_enabled)
local fast_enabled = held_fire_enabled and os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") ~= "1"
local scopes = {}
local actions = {
    ["native-guest-smg-hold-start"] = true,
    ["native-guest-smg-hold-observe"] = true,
    ["native-guest-smg-hold-stop"] = true,
    ["native-guest-smg-hold-renew"] = true,
    ["native-guest-smg-hold-aim"] = true,
}
local states = { active = true, closing = true, released = true, expired = true, empty = true, ineligible = true }

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function check(ok, status, serial, before, after, rounds, projectiles)
    local dirty = type(status) == "string" and status:match("^mutation_unverified_guest_smg_hold_")
    if (ok ~= true and not dirty) or (ok == true and not states[status])
        or not math.tointeger(serial) or serial < 1 or serial >= 2 ^ 53
        or not math.tointeger(before) or before < 1 or before > 30
        or not math.tointeger(after) or after < 0 or after > before
        or not math.tointeger(rounds) or rounds ~= before - after or projectiles ~= rounds then
        error("mutation_unverified_guest_smg_hold_abi", 0)
    end
    return {serial = serial, status = status, before = before, after = after,
        rounds = rounds, projectiles = projectiles}
end

function canary.command(fields, state, phase)
    local action = fields[2]
    if not actions[action] then return end
    if not enabled or phase ~= "hosting" or type(state.map) ~= "string"
        or type(SilverfishStartNativeGuestSmgHold) ~= "function"
        or type(SilverfishReadNativeGuestSmgHold) ~= "function"
        or type(SilverfishStopNativeGuestSmgHold) ~= "function"
        or type(SilverfishRenewNativeGuestSmgHold) ~= "function" then
        error("native_guest_smg_hold_unavailable", 0)
    end
    if action == "native-guest-smg-hold-start" then
        local name, guid, duration = fields[3], fields[4], math.tointeger(fields[5])
        local view = #fields == 6 and shot_view.parse(fields[6])
        if (#fields ~= 5 and not view) or type(guid) ~= "string" or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]")
            or not duration or duration < 250 or duration > 2400 then
            error("native_guest_smg_hold_exact_weapon_required", 0)
        end
        local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
        if not qualified or not pawn or not save then error("native_guest_smg_hold_exact_owner_required", 0) end
        local ok, status, serial, before, after, rounds, projectiles =
            SilverfishStartNativeGuestSmgHold(pawn:GetAddress(), save:GetAddress(), guid, duration, table.unpack(view or {}))
        if ok ~= true and serial == 0 and type(status) == "string"
            and not status:match("^mutation_unverified") then error(status, 0) end
        local observation = check(ok, status, serial, before, after, rounds, projectiles)
        for id, scope in pairs(scopes) do
            if scope.pawn == name or scope.map ~= state.map then scopes[id] = nil end
        end
        local oldest, count
        count = 0
        for id, scope in pairs(scopes) do
            count = count + 1
            if not oldest or id < oldest then
                if scope.frame.status ~= "active" and scope.frame.status ~= "closing" then oldest = id end
            end
        end
        if count >= 4 and oldest then scopes[oldest] = nil end
        scopes[serial] = {map = state.map, pawn = name, guid = guid, frame = observation,
            world = state.world, driver = state.world and state.world.NetDriver}
        return "stock guest SMG hold observed"
    end
    local serial = math.tointeger(fields[3])
    local scope = scopes[serial]
    if action == "native-guest-smg-hold-aim" then
        local sequence, view = math.tointeger(fields[4]), shot_view.parse(fields[5])
        if #fields ~= 5 or not scope or scope.map ~= state.map or not same(scope.world, state.world)
            or not same(scope.driver, state.world.NetDriver) or not sequence or sequence < 1 or sequence >= 2 ^ 53
            or not view or type(SilverfishUpdateNativeGuestSmgAim) ~= "function"
            or SilverfishUpdateNativeGuestSmgAim(serial, sequence, table.unpack(view)) ~= true then
            error("native_guest_smg_hold_aim_unavailable", 0)
        end
        return "stock guest SMG aim observed"
    end
    local renew = action == "native-guest-smg-hold-renew"
    local duration = renew and math.tointeger(fields[4])
    if #fields ~= (renew and 4 or 3) or not scope or scope.map ~= state.map
        or not same(scope.world, state.world) or not same(scope.driver, state.world.NetDriver) or
        (renew and (not duration or duration < 250 or duration > 2400)) then
        error("native_guest_smg_hold_exact_serial_required", 0)
    end
    local observation
    if renew then
        observation = check(SilverfishRenewNativeGuestSmgHold(serial, duration))
    else
        local native = action == "native-guest-smg-hold-stop" and SilverfishStopNativeGuestSmgHold
            or SilverfishReadNativeGuestSmgHold
        observation = check(native(serial))
    end
    if observation.serial ~= serial then error("mutation_unverified_guest_smg_hold_owner", 0) end
    scope.frame = observation
    return "stock guest SMG hold observed"
end

function canary.rows(state, phase, status_row, exact_driver)
    local rows = {}
    if phase ~= "hosting" then return rows end
    for serial, scope in pairs(scopes) do
        if scope.map ~= state.map then
            scopes[serial] = nil
        elseif not exact_driver or same(scope.world, state.world) and same(scope.driver, exact_driver) then
            local observation = scope.frame
            if observation.status == "active" or observation.status == "closing" then
                observation = check(SilverfishReadNativeGuestSmgHold(serial))
                if observation.serial ~= serial then error("mutation_unverified_guest_smg_hold_owner", 0) end
                scope.frame = observation
            end
            rows[#rows + 1] = status_row("native-guest-smg-hold", scope.map, scope.pawn, scope.guid, serial,
                observation.status, observation.before, observation.after, observation.rounds, observation.projectiles)
        end
    end
    return rows
end

local fast_context

function canary.cache_context(state, phase)
    if not held_fire_enabled then return end
    fast_context = nil
    if phase ~= "hosting" or not state or state.listening ~= true or state.server ~= false or not valid(state.world) then return end
    local driver = state.world.NetDriver
    if valid(driver) then fast_context = {state = state, driver = driver, seen = os.time()} end
end

local field_counts = {
    ["native-guest-smg-hold-start"] = 5,
    ["native-guest-smg-hold-observe"] = 3,
    ["native-guest-smg-hold-stop"] = 3,
    ["native-guest-smg-hold-renew"] = 4,
}

local function parse_fast_command(content)
    local nonce = content and content:match("^([0-9a-f]+)\t")
    if not nonce or #nonce ~= 32 then return end
    if #content > 2048 or not content:match("\r?\n$") then return nonce end
    local line = content:gsub("\r?\n$", "")
    if line:find("[\r\n]") then return nonce end
    local fields = {}
    for value in (line .. "\t"):gmatch("([^\t]*)\t") do
        if #value > 512 or value:find("%c") then return nonce end
        fields[#fields + 1] = value
        if #fields > 6 then return nonce end
    end
    if field_counts[fields[2]] ~= #fields
        and not (fields[2] == "native-guest-smg-hold-start" and #fields == 6) then return nonce end
    return nonce, fields
end

local function replace_reply(path, content)
    local temporary = path .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local written = file:write(content)
    local closed = file:close()
    if not written or not closed then return false end
    if os.rename(temporary, path) then return true end
    -- Windows Lua cannot rename over an existing reply. Readers only see a complete reply.
    os.remove(path)
    return os.rename(temporary, path) ~= nil
end

function canary.is_command(action)
    return actions[action] == true
end

local function reply(status_row, nonce, accepted, area, message, rows)
    local lines = {status_row("held-fire-ack", nonce, accepted and "accepted" or "rejected", area, tostring(message):sub(1, 512))}
    for _, row in ipairs(rows or {}) do lines[#lines + 1] = row end
    return table.concat(lines, "\n") .. "\n"
end

function canary.command_reply(fields, state, phase, status_row)
    local nonce = fields[1]
    if not state then
        local cached, now = fast_context, os.time()
        if fields[2] ~= "native-guest-smg-hold-aim" or not cached or now < cached.seen or now - cached.seen > 1
            or not valid(cached.state.world) or not same(cached.state.world.NetDriver, cached.driver)
            or not same(cached.driver.World, cached.state.world) or valid(cached.driver.ServerConnection) then
            return reply(status_row, nonce, false, "-", "native_guest_smg_hold_fast_scope_unavailable")
        end
        state, phase = cached.state, "hosting"
    end
    local accepted, message = pcall(canary.command, fields, state, phase)
    if not accepted then return reply(status_row, nonce, false, state.map, message) end
    if fields[2] == "native-guest-smg-hold-aim" then return reply(status_row, nonce, true, state.map, message) end
    local reported, rows = pcall(canary.rows, state, phase, status_row, state.world.NetDriver)
    if not reported then return reply(status_row, nonce, false, state.map, "mutation_unverified_guest_smg_hold_readback") end
    return reply(status_row, nonce, true, state.map, message, rows)
end

function canary.start_fast(bridge, status_row, still_current)
    if not fast_enabled then return end
    local command_path, reply_path = bridge .. "/held-fire-command.tsv", bridge .. "/held-fire-reply.tsv"
    local seen, previous_content, pending_reply = {}, nil, nil

    local function handle(nonce, fields)
        if seen[nonce] then return reply(status_row, nonce, false, "-", "native_guest_smg_hold_duplicate_nonce") end
        seen[nonce] = true
        if not fields then return reply(status_row, nonce, false, "-", "native_guest_smg_hold_fast_command_unqualified") end
        local cached, now = fast_context, os.time()
        local current, state = pcall(function()
            if not cached or now < cached.seen or now - cached.seen > 1 or not valid(cached.state.world)
                or not same(cached.state.world.NetDriver, cached.driver) or not same(cached.driver.World, cached.state.world)
                or valid(cached.driver.ServerConnection) or not still_current(cached.state) then return end
            return cached.state
        end)
        if not current or not state then
            return reply(status_row, nonce, false, "-", "native_guest_smg_hold_fast_scope_unavailable")
        end
        return canary.command_reply(fields, state, "hosting", status_row)
    end

    LoopInGameThreadWithDelay(50, function()
        if pending_reply then
            if not replace_reply(reply_path, pending_reply) then return end
            pending_reply = nil
        end
        local file = io.open(command_path, "rb")
        if not file then previous_content = nil; return end
        local content = file:read(2049)
        file:close()
        if not content or content == previous_content then return end
        previous_content = content
        local nonce, fields = parse_fast_command(content)
        if not nonce then return end
        local checked, result = pcall(handle, nonce, fields)
        pending_reply = checked and result or reply(status_row, nonce, false, "-", "mutation_unverified_guest_smg_hold_fast_handler")
        if replace_reply(reply_path, pending_reply) then pending_reply = nil end
    end)
end

return canary
