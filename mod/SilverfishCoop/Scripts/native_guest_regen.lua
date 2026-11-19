local regen = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_REGEN") == "1"
local guests = {}

local function valid(value)
    return value ~= nil and value:IsValid()
end

local function finite(value)
    return type(value) == "number" and value == value and value > -math.huge and value < math.huge
end

local function forget(entry)
    if entry and entry.address and type(SilverfishClearNativeGuestRegen) == "function" then
        SilverfishClearNativeGuestRegen(entry.address)
    end
end

function regen.clear(name)
    if name ~= nil then
        forget(guests[name])
        guests[name] = nil
    else
        if type(SilverfishClearNativeGuestRegen) == "function" then SilverfishClearNativeGuestRegen() end
        guests = {}
    end
end

local function host_live(state, phase)
    return enabled and phase == "hosting" and state.listening and not state.server
        and valid(state.world) and valid(state.local_pawn and state.local_pawn.pawn)
end

local function update(entry, guest, state, phase, standing, stock)
    entry.proof = nil
    if entry.blocked then return false end
    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, guest.name)
    if not qualified or not valid(pawn) or not valid(detached) or pawn:GetFullName() ~= guest.name
        or detached:GetAddress() == stock:GetAddress() then
        forget(entry)
        entry.address, entry.status = nil, "paused_binding"
        return false
    end
    local address = pawn:GetAddress()
    if entry.address and entry.address ~= address then forget(entry) end
    entry.address = address
    local projected, is_standing = pcall(standing, pawn, guest.name)
    if not projected or is_standing ~= true then
        forget(entry)
        entry.status = "paused_projection"
        return false
    end

    if type(SilverfishPulseNativeGuestRegen) ~= "function" then
        entry.status = "refused_addon_unavailable"
        return false
    end
    local called, status, count, total, seconds, hp_before, hp_after, ceiling, delay_before, delay_after,
        food_before, food_after, stim_before, stim_after =
        pcall(SilverfishPulseNativeGuestRegen, address, detached:GetAddress(), stock:GetAddress())
    if not called or type(status) ~= "string" or not status:match("^[a-z0-9_]+$") then
        entry.status, entry.blocked = "mutation_unverified_native_call", true
        return false
    end
    entry.status = status
    if status ~= "applied" and status ~= "anchored" and status ~= "paused_game_clock" then
        if status:match("^mutation_unverified_") then entry.blocked = true end
        return false
    end

    if math.type(count) ~= "integer" or count < 0 or not finite(total) or total < 0.0 or not finite(seconds)
        or seconds < 0.0 or seconds > 0.5 or (status == "applied") ~= (seconds > 0.0)
        or not finite(hp_before) or hp_before <= 1.0 or not finite(hp_after) or hp_after < hp_before
        or not finite(ceiling) or hp_after > ceiling or not finite(delay_before) or not finite(delay_after)
        or not finite(food_before) or not finite(food_after) or food_after < 0.0 or food_after > food_before
        or not finite(stim_before) or not finite(stim_after) or stim_after < 0.0 or stim_after > stim_before then
        entry.status, entry.blocked = "mutation_unverified_native_proof", true
        return false
    end
    entry.proof = {count, total, seconds, hp_before, hp_after, ceiling, delay_before, delay_after,
        food_before, food_after, stim_before, stim_after}
    return status == "applied"
end

function regen.sync(state, phase, standing)
    if not host_live(state, phase) or type(standing) ~= "function" then
        if next(guests) then regen.clear() end
        return false
    end
    local stock = state.local_pawn.pawn.Save
    if not valid(stock) then
        if next(guests) then regen.clear() end
        return false
    end
    local current, updated = {}, false
    for _, guest in ipairs(state.pawns) do
        if guest.kind == "guest" then
            local entry = guests[guest.name]
            if not entry or entry.map ~= state.map then
                forget(entry)
                entry = {map = state.map}
            end
            if update(entry, guest, state, phase, standing, stock) then updated = true end
            current[guest.name] = entry
        end
    end
    for name, entry in pairs(guests) do if not current[name] then forget(entry) end end
    guests = current
    return updated
end

function regen.rows(state, phase, status_row)
    local rows = {}
    if not host_live(state, phase) then return rows end
    local names = {}
    for name in pairs(guests) do names[#names + 1] = name end
    table.sort(names)
    for _, name in ipairs(names) do
        local entry = guests[name]
        if entry.map == state.map and entry.status then
            local proof = {}
            if entry.proof then
                proof[1] = tostring(entry.proof[1])
                for index = 2, #entry.proof do proof[index] = string.format("%.17g", entry.proof[index]) end
            end
            rows[#rows + 1] = status_row("native-guest-regen", entry.status, entry.map, name, table.unpack(proof))
        end
    end
    return rows
end

return regen
