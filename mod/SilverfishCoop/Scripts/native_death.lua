local native_death = {}
local guest_save = require("guest_save")
local vitals = require("vitals")

local configured_mode = os.getenv("SILVERFISH_COOP_NATIVE_DEATH")
local scoped = configured_mode == "cpp" and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
local backend = configured_mode == "1" and "overlay" or configured_mode == "cpp" and "cpp" or nil
if scoped then backend = "cpp-scoped" end
local die_path = "/Game/Classes/Player/Player_FP.Player_FP_C:Die"
local lab_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local hook_status = "arming"
local hook_failure = ""
local slots = {}
local scope_records = {}
local scope_map
local file_path
local heartbeat_path
local sequence = 0
local last_damage

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function active(state, phase)
    return backend and phase == "hosting" and lab_maps[state.map]
end

local function update_cpp_status()
    local helpers_ready
    if scoped then
        helpers_ready = type(SilverfishSetNativeDeathScope) == "function"
            and type(SilverfishTakeScopedNativeDeathHit) == "function"
            and type(SilverfishClearNativeDeathScope) == "function"
    else
        helpers_ready = type(SilverfishArmNativeDeath) == "function"
            and type(SilverfishDisarmNativeDeath) == "function"
            and type(SilverfishTakeNativeDeathHit) == "function"
    end
    if type(SilverfishNativeDeathReady) ~= "function" or not helpers_ready then
        hook_status, hook_failure = "arming", "Waiting for the C++ death hook exports"
        return
    end
    local ok, ready = pcall(SilverfishNativeDeathReady)
    if not ok then
        hook_status, hook_failure = "error", tostring(ready)
    elseif ready ~= true then
        hook_status, hook_failure = "arming", "The C++ death hook is not ready"
    else
        hook_status, hook_failure = "ready", ""
    end
end

local function emit(slot, record)
    local file = io.open(file_path, "ab")
    if not file then return false end
    local next_sequence = sequence + 1
    local written = file:write("downed\t", slot, "\t", tostring(next_sequence), "\t", record.name, "\n")
    local closed = file:close()
    if not written or not closed then return false end
    sequence = next_sequence
    record.emitted = true
    record.pending_native = nil
    return true
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function heartbeat_fresh()
    local file = io.open(heartbeat_path, "rb")
    if not file then return false end
    local value = file:read(32)
    file:close()
    local timestamp = value and value:match("^(%d+)%s*$")
    if not timestamp or #timestamp > 12 then return false end
    local age = os.time() - tonumber(timestamp)
    return age >= 0 and age <= 2
end

local function clear_scope(force)
    if not force and not next(scope_records) then return true end
    local cleared = pcall(SilverfishClearNativeDeathScope)
    scope_records, scope_map = {}, nil
    return cleared
end

function native_death.clear_scope()
    if not scoped then return true end
    return clear_scope(true)
end

local function publish_scope(eligible, map)
    if not next(eligible) then return clear_scope() end
    local guest = eligible.guest
    local refreshed, accepted = pcall(SilverfishSetNativeDeathScope, 0, guest.address)
    if not refreshed or accepted ~= true then
        clear_scope(true)
        return false
    end
    scope_records, scope_map = eligible, map
    return true
end

local function eligible_scope(current)
    local host = current["local"]
    if not host then return {} end
    local host_save = host.pawn.Save
    if not valid(host_save) or not finite(host_save.PlayerHealth) then return {} end

    local guest = current.guest
    if not guest then return {} end
    local guest_save = guest.pawn.Save
    if not valid(guest_save) or same(host_save, guest_save)
        or not finite(guest_save.PlayerHealth) then return {} end
    return { guest = guest }
end

local function drain_scope(current, map)
    for slot, previous in pairs(scope_records) do
        local observed, hit = pcall(SilverfishTakeScopedNativeDeathHit, previous.address)
        if not observed then return false end
        -- The native latch belongs to a pawn in the previous scope. A
        -- replacement in the same slot must never inherit that event.
        if hit == true and scope_map == map and current[slot] == previous and not previous.emitted then
            previous.pending_native = true
        end
    end
    for slot, record in pairs(current) do
        if record.pending_native and not emit(slot, record) then return false end
    end
    return true
end

function native_death.sync(state, phase)
    local session_active = active(state, phase)
    local current = {}
    local ambiguous = false
    if session_active then
        for _, details in ipairs(state.pawns) do
            local slot, pawn = details.kind, details.pawn
            if (slot == "local" or slot == "guest") and valid(pawn) and pawn:HasAuthority() then
                if scoped and current[slot] then
                    ambiguous = true
                    break
                end
                local previous = slots[slot]
                if previous and same(previous.pawn, pawn) and previous.map == state.map then
                    current[slot] = previous
                else
                    current[slot] = { pawn = pawn, address = pawn:GetAddress(), name = details.name,
                        map = state.map, emitted = false }
                end
            end
        end
    end
    if ambiguous then current = {} end

    -- Consume the previous scope before replacing identities or refreshing
    -- expiry. Authority and map came from this tick's game-thread inspection.
    local drained = not scoped or drain_scope(current, state.map)
    slots = current
    if not session_active then
        if scoped then clear_scope() end
        last_damage = nil
        return
    end
    if configured_mode == "cpp" then update_cpp_status() end
    if not scoped then return end

    if not drained or ambiguous then
        clear_scope()
        hook_status = "error"
        hook_failure = ambiguous and "Multiple authority pawns occupy a co-op slot"
            or "Could not deliver a scoped native death event"
    elseif hook_status ~= "ready" then
        clear_scope()
    elseif not heartbeat_fresh() then
        clear_scope()
        hook_status, hook_failure = "arming", "Waiting for a fresh host heartbeat"
    elseif not publish_scope(current.guest and guest_save.matches(current.guest.pawn)
            and eligible_scope(current) or {}, state.map) then
        hook_status, hook_failure = "error", "The C++ death hook declined the authority scope"
    end
end

function native_death.rearm(slot)
    if slots[slot] then slots[slot].emitted = false end
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

local function invoke_death_path(slot, record, invoke)
    if configured_mode ~= "cpp" then
        invoke()
        return
    end

    -- UE4SS may skip Lua's Blueprint callback when a native pre-hook
    -- prevents the body. Emit from the native one-shot hit instead.
    update_cpp_status()
    if hook_status ~= "ready" then error("The C++ death hook is unavailable", 0) end
    local pawn = record.pawn
    if slots[slot] ~= record or not valid(pawn) or not pawn:HasAuthority() then
        error("The authorized pawn no longer exists", 0)
    end
    if scoped then
        if not heartbeat_fresh() or eligible_scope(slots)[slot] ~= record then
            clear_scope()
            error("The scoped death hook needs a fresh host and independent pawn health", 0)
        end
        if not drain_scope(slots, record.map) or record.emitted then
            clear_scope()
            error("An earlier scoped death event is pending", 0)
        end
        if not publish_scope({ [slot] = record }, record.map) then
            error("The C++ death hook declined the diagnostic scope", 0)
        end

        local called, failure = pcall(invoke)
        local observed, hit = pcall(SilverfishTakeScopedNativeDeathHit, record.address)
        -- Remove this temporary scope before the caller restores the original
        -- save binding. This diagnostic never uses the explicit one-shot arm.
        local cleared = clear_scope()
        if not cleared then error("Could not clear the C++ diagnostic scope", 0) end
        if not called then error(failure, 0) end
        if not observed or hit ~= true then error("The C++ scope observed no qualified native hit", 0) end
        record.pending_native = true
        if not emit(slot, record) then error("Could not deliver the scoped native death event", 0) end
        return
    end

    local armed, accepted = pcall(SilverfishArmNativeDeath, pawn:GetAddress())
    if not armed or accepted ~= true then
        pcall(SilverfishDisarmNativeDeath)
        error("The C++ death hook declined the current pawn", 0)
    end

    local called, failure = pcall(invoke)
    local observed, hit = pcall(SilverfishTakeNativeDeathHit)
    local disarmed = pcall(SilverfishDisarmNativeDeath)
    if not disarmed then error("Could not disarm the C++ death hook", 0) end
    if not called then error(failure, 0) end
    if not observed or hit ~= true then error("The C++ death hook observed no qualified hit", 0) end
    emit(slot, record)
end

local function direct_damage_probe(state, phase, record)
    local guest = record.pawn
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
        invoke_death_path("guest", record, function()
            guest:Deal_DirectDamage(before + 1, host, FText("Co-op native direct-damage probe"), false)
        end)
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
        host_after = original.PlayerHealth, sequence = sequence, binding_restored = true }
    return "native direct damage reached co-op death and stock binding was restored"
end

local function ambient_damage_probe(record)
    if not scoped or not heartbeat_fresh() or scope_records.guest ~= record
        or not guest_save.matches(record.pawn) or not valid(slots["local"] and slots["local"].pawn) then
        error("The guest does not have a supervised native scope", 0)
    end
    local host = slots["local"] and slots["local"].pawn
    local guest = record.pawn
    local stock, detached = host.Save, guest.Save
    local host_before, before = stock.PlayerHealth, detached.PlayerHealth
    if not finite(before) or before <= 0 or before > 1000000 then
        error("The guest health is outside the canary range", 0)
    end
    -- Normal scope renewal owns interception and event delivery. This command
    -- leaves that scope alone and only introduces a deterministic stock hit.
    guest:Deal_DirectDamage(before + 1, host, FText("Co-op scoped damage probe"), false)
    local after = detached.PlayerHealth
    if not guest_save.matches(guest) or guest.Dead ~= false
        or not finite(after) or after > 0 or stock.PlayerHealth ~= host_before then
        error("Scoped lethal damage did not preserve independent live pawns", 0)
    end
    last_damage = { before = before, after = after, host_before = host_before,
        host_after = stock.PlayerHealth, sequence = sequence + 1, binding_restored = false }
    return "stock direct damage delivered to the enrolled guest"
end

function native_death.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-death-probe" and command ~= "native-damage-probe"
        and command ~= "native-guest-damage" then return end
    local slot = fields[3]
    if #fields ~= 3 or (slot ~= "local" and slot ~= "guest") then
        error("Expected one pawn slot, local or guest", 0)
    end
    if (command == "native-damage-probe" or command == "native-guest-damage") and slot ~= "guest" then
        error("Native direct damage probing is limited to the isolated guest", 0)
    end
    if not active(state, phase) or hook_status ~= "ready" then
        error("Native death probing requires an active playtest host with a ready death hook", 0)
    end
    local record = slots[slot]
    if not record or not valid(record.pawn) or not record.pawn:HasAuthority() then
        error("The requested authoritative pawn is unavailable", 0)
    end
    if record.emitted then error("This pawn already reported a native death", 0) end
    if command == "native-guest-damage" then return ambient_damage_probe(record) end
    if command == "native-damage-probe" then return direct_damage_probe(state, phase, record) end
    local previous = sequence
    invoke_death_path(slot, record, function()
        record.pawn:Die(record.pawn, FText("Co-op native death probe"), {})
    end)
    if sequence == previous then
        error("The native death event was not observed", 0)
    end
    return "native death event observed"
end

function native_death.rows(state, phase, status_row)
    if not active(state, phase) then return {} end
    local rows = { status_row("native-death", hook_status, sequence, hook_failure, backend,
        scope_records.guest and "guest" or "") }
    if last_damage then
        rows[#rows + 1] = status_row("native-damage-canary", "guest",
            last_damage.before, last_damage.after, last_damage.host_before, last_damage.host_after,
            last_damage.binding_restored and 1 or 0, last_damage.sequence)
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
    if not backend then return end
    file_path = bridge .. "/death.tsv"
    if scoped then heartbeat_path = bridge .. "/native-events-heartbeat" end
    if configured_mode == "cpp" then return end
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
