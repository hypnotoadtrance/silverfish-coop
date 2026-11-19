local canary = {}
local guest_save = require("guest_save")

local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local enabled = (fixtures or os.getenv("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE") == "1")
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local scopes = {}
local actions = {
    ["native-guest-smg-hold-start"] = true,
    ["native-guest-smg-hold-observe"] = true,
    ["native-guest-smg-hold-stop"] = true,
    ["native-guest-smg-hold-renew"] = true,
}
local states = { active = true, closing = true, released = true, expired = true, empty = true, ineligible = true }

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
        if #fields ~= 5 or type(guid) ~= "string" or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]")
            or not duration or duration < 250 or duration > 2400 then
            error("native_guest_smg_hold_exact_weapon_required", 0)
        end
        local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
        if not qualified or not pawn or not save then error("native_guest_smg_hold_exact_owner_required", 0) end
        local ok, status, serial, before, after, rounds, projectiles =
            SilverfishStartNativeGuestSmgHold(pawn:GetAddress(), save:GetAddress(), guid, duration)
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
        scopes[serial] = {map = state.map, pawn = name, guid = guid, frame = observation}
        return "stock guest SMG hold observed"
    end
    local serial = math.tointeger(fields[3])
    local scope = scopes[serial]
    local renew = action == "native-guest-smg-hold-renew"
    local duration = renew and math.tointeger(fields[4])
    if #fields ~= (renew and 4 or 3) or not scope or scope.map ~= state.map or
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

function canary.rows(state, phase, status_row)
    local rows = {}
    if phase ~= "hosting" then return rows end
    for serial, scope in pairs(scopes) do
        if scope.map ~= state.map then
            scopes[serial] = nil
        else
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

return canary
