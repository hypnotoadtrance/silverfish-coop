local native_death = {}
local vitals = require("vitals")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "1"
local die_path = "/Game/Classes/Player/Player_FP.Player_FP_C:Die"
local lab_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local hook_status = "arming"
local hook_failure = ""
local slots = {}
local file_path
local sequence = 0
local last_damage

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function active(state, phase)
    return enabled and phase == "hosting" and lab_maps[state.map]
end

function native_death.sync(state, phase)
    if not active(state, phase) then
        slots = {}
        last_damage = nil
        return
    end
    local current = {}
    for _, details in ipairs(state.pawns) do
        local pawn = details.pawn
        if valid(pawn) and pawn:HasAuthority() then
            local previous = slots[details.kind]
            if previous and same(previous.pawn, pawn) then
                current[details.kind] = previous
            else
                current[details.kind] = { pawn = pawn, emitted = false }
            end
        end
    end
    slots = current
end

function native_death.rearm(slot)
    if slots[slot] then slots[slot].emitted = false end
end

local function emit(slot, record)
    local file = io.open(file_path, "ab")
    if not file then return end
    local next_sequence = sequence + 1
    local written = file:write("downed\t", slot, "\t", tostring(next_sequence), "\n")
    local closed = file:close()
    if written and closed then
        sequence = next_sequence
        record.emitted = true
    end
end

local function on_die(context)
    if not next(slots) then return end
    local pawn = context:get()
    for slot, record in pairs(slots) do
        if not record.emitted and same(record.pawn, pawn) and pawn:HasAuthority() then
            emit(slot, record)
            return
        end
    end
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function direct_damage_probe(state, phase, guest)
    if vitals.active() then error("Reset the current vitals probe before direct damage", 0) end
    local host = slots["local"] and slots["local"].pawn
    if not valid(host) or not host:HasAuthority() or same(host, guest) then
        error("Two distinct authoritative pawns are required", 0)
    end
    local original = guest.Save
    if not same(original, host.Save) or not finite(original.PlayerHealth) then
        error("The guest and host have no initialized stock save to isolate", 0)
    end
    local host_before = original.PlayerHealth
    local prior_sequence = sequence
    last_damage = nil

    local ok, sample = pcall(function()
        vitals.command({ "native-damage", "vitals-probe" }, state, phase)
        local detached = guest.Save
        if not valid(detached) or same(detached, original) or not same(host.Save, original) then
            error("Could not verify isolated guest damage ownership", 0)
        end
        local before = detached.PlayerHealth
        if not finite(before) or before <= 0 or before > 1000000 then
            error("The detached guest health is outside the safe canary range", 0)
        end
        guest:Deal_DirectDamage(before + 1, host, FText("Co-op native direct-damage probe"), false)
        local after = detached.PlayerHealth
        if not same(guest.Save, detached) or not valid(guest) or guest.Dead ~= false
            or not finite(after) or after > 0 or sequence ~= prior_sequence + 1 then
            error("Native damage did not reach the qualified co-op death wrapper", 0)
        end
        return { before = before, after = after }
    end)
    vitals.reset()
    if not ok then error(sample, 0) end
    if not same(guest.Save, original) or not same(host.Save, original)
        or original.PlayerHealth ~= host_before then
        error("The stock save binding or host health changed unexpectedly", 0)
    end
    last_damage = { before = sample.before, after = sample.after, host_before = host_before,
        host_after = original.PlayerHealth, sequence = sequence }
    return "native direct damage reached co-op death and stock binding was restored"
end

function native_death.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-death-probe" and command ~= "native-damage-probe" then return end
    local slot = fields[3]
    if #fields ~= 3 or (slot ~= "local" and slot ~= "guest") then
        error("Expected one pawn slot, local or guest", 0)
    end
    if command == "native-damage-probe" and slot ~= "guest" then
        error("Native direct damage probing is limited to the isolated guest", 0)
    end
    if not active(state, phase) or hook_status ~= "ready" then
        error("Native death probing requires the active patched playtest host", 0)
    end
    local record = slots[slot]
    if not record or not valid(record.pawn) or not record.pawn:HasAuthority() then
        error("The requested authoritative pawn is unavailable", 0)
    end
    if record.emitted then error("This pawn already reported a native death", 0) end
    if command == "native-damage-probe" then return direct_damage_probe(state, phase, record.pawn) end
    local previous = sequence
    -- Call the real reflected Blueprint wrapper in the launcher-verified lab stage.
    -- Its patch returns before stock respawn and leaves game state to the host.
    record.pawn:Die(record.pawn, FText("Co-op native death probe"), {})
    if sequence == previous then
        error("The native death event was not observed", 0)
    end
    return "native death event observed"
end

function native_death.rows(state, phase, status_row)
    if not active(state, phase) then return {} end
    local rows = { status_row("native-death", hook_status, sequence, hook_failure) }
    if last_damage then
        rows[#rows + 1] = status_row("native-damage-canary", "guest",
            last_damage.before, last_damage.after, last_damage.host_before, last_damage.host_after,
            1, last_damage.sequence)
    end
    for slot, record in pairs(slots) do
        local pawn = record.pawn
        if valid(pawn) and pawn:HasAuthority() and type(pawn.Dead) == "boolean" then
            rows[#rows + 1] = status_row("native-death-pawn", slot, pawn.Dead and 1 or 0)
        end
    end
    return rows
end

function native_death.start(bridge)
    if not enabled then return end
    file_path = bridge .. "/death.tsv"
    local attempts = 0
    LoopAsync(500, function()
        if not next(slots) or not valid(StaticFindObject(die_path)) then return false end
        local ok, failure = pcall(RegisterHook, die_path, on_die)
        if ok then
            hook_status, hook_failure = "ready", ""
            return true
        end
        attempts = attempts + 1
        hook_failure = tostring(failure)
        if attempts < 8 then return false end
        hook_status = "error"
        print("[SilverfishCoop] Native death hook unavailable: " .. hook_failure .. "\n")
        return true
    end)
end

return native_death
