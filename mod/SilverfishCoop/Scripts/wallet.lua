-- Observe local stock cash edits and apply each guest's reported change to the shared wallet.
local wallet = {}
local zones = require("zone_maps")
local save_scope = require("native_save_scope")

local playtest_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local max_cash = 2147483647
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local last_read_error
local min_cash = -2147483648
local max_total = 9007199254740991
local guest
local ledger_session, reports

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function host_pawn(state, phase)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase == "hosting" and (playtest_maps[state.map] or (authored_routes and zones[state.map]))
        and valid(pawn) and pawn:HasAuthority() then
        return pawn
    end
end

local function game_instance(pawn)
    local statics = StaticFindObject(gameplay_statics_path)
    if not valid(statics) then error("Wallet GameplayStatics is unavailable", 0) end
    local instance = statics:GetGameInstance(pawn)
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ") then
        error("Wallet GameInstance_FP is unavailable", 0)
    end
    if not valid(instance.SaveSlot_Ref) then error("Wallet host save object is unavailable", 0) end
    return instance
end

local function read_cash(instance, key)
    -- UE4SS writes each primitive out parameter to the supplied table by name.
    local output = {}
    instance:Check_SaveFlag(key, output, output)
    if type(output.Found) ~= "boolean" or type(output.Value) ~= "number"
        or not math.tointeger(output.Value) or output.Value < -2147483648 or output.Value > max_cash then
        error("Wallet Check_SaveFlag returned incompatible Found/Value fields", 0)
    end
    if not output.Found then return 0, false end
    return output.Value, true
end

local function parse_amount(value)
    if type(value) ~= "string" or #value > 10 or not value:match("^[0-9]+$") then
        error("Wallet amount must be a nonnegative signed 32-bit integer", 0)
    end
    local amount = tonumber(value)
    if not amount or amount > max_cash then
        error("Wallet amount must be a nonnegative signed 32-bit integer", 0)
    end
    return amount
end

local function clamp_cash(value)
    return math.max(min_cash, math.min(max_cash, value))
end

local function write_cash(instance, key, amount)
    -- Overwrite and Force_Decrease select stock's absolute Map_Add path.
    instance:Add_PointTo_SaveFlag(key, amount, true, true)
    local observed, found = read_cash(instance, key)
    if not found or observed ~= amount then error("Wallet native balance failed read-back", 0) end
end

local function token(value)
    return type(value) == "string" and #value == 32 and value:match("^[a-f0-9]+$")
end

local function integer(value, low, high)
    local n = type(value) == "string" and #value <= 17 and value:match("^%-?%d+$") and math.tointeger(tonumber(value))
    if n and n >= low and n <= high then return n end
end

local function read_projected(save, key)
    return save.Flags:Contains(key) and save.Flags:Find(key):get() or 0
end

local function write_projected(save, key, balance)
    local flags = save.Flags
    if flags:Contains(key) then
        if flags:Find(key):get() == balance then return true end
        flags:Remove(key)
    end
    flags:Add(key, balance)
    return flags:Contains(key) and flags:Find(key):get() == balance
end

function wallet.project_guest(save, balance)
    -- Inventory, merchants and barter may finish using older snapshots.
    -- Once tracking starts the dedicated shared-wallet frame owns this flag.
    if guest and guest.save == save:GetAddress() and guest.character == save_scope.selected() then return true end
    return write_projected(save, FName("Player_Cash"), balance)
end

local function observe_guest(current)
    if guest.balance ~= current then
        guest.total = guest.total + current - guest.balance
        guest.sequence = guest.sequence + 1
        guest.balance = current
    end
end

local function sync_guest(fields, state, phase)
    local session, stream = fields[3], fields[4]
    local balance = integer(fields[5], min_cash, max_cash)
    local sequence = integer(fields[6], 0, max_total)
    local total = integer(fields[7], -max_total, max_total)
    if #fields ~= 7 or not token(session) or not token(stream) or not balance or not sequence or not total then
        error("Invalid guest wallet frame", 0)
    end
    local save, _, character = save_scope.bind_guest(state, phase)
    if not save then error("The private guest wallet is unavailable", 0) end
    local key, address = FName("Player_Cash"), save:GetAddress()
    local current = read_projected(save, key)
    if not guest or guest.save ~= address or guest.character ~= character or guest.session ~= session or guest.stream ~= stream then
        guest = {save = address, character = character, session = session, stream = stream,
            balance = current, sequence = 0, total = 0, acknowledged = 0}
    end
    observe_guest(current)
    if sequence < guest.acknowledged or sequence > guest.sequence or sequence == 0 and total ~= 0
        or sequence == guest.sequence and total ~= guest.total then
        error("The guest wallet acknowledgement did not match", 0)
    end
    local projected = clamp_cash(balance + guest.total - total)
    if not write_projected(save, key, projected) then error("The attached co-op wallet failed readback", 0) end
    guest.balance, guest.acknowledged = projected, sequence
    return "guest wallet verified"
end

function wallet.command(fields, state, phase)
    local action = fields[2]
    if action == "wallet-guest-sync" then return sync_guest(fields, state, phase) end
    if action ~= "wallet-set" and action ~= "wallet-guest-report" then return nil end
    local pawn = host_pawn(state, phase)
    if not pawn then error("Only the active host can change the shared wallet", 0) end
    local instance = game_instance(pawn)
    local key = FName("Player_Cash")
    if action == "wallet-set" then
        if #fields ~= 3 then error("Invalid wallet-set command", 0) end
        local amount = parse_amount(fields[3])
        local current, found = read_cash(instance, key)
        if not found or current ~= amount then write_cash(instance, key, amount) end
        return "native wallet verified"
    end
    local session, character, stream = fields[3], fields[4], fields[5]
    local sequence = integer(fields[6], 1, max_total)
    local total = integer(fields[7], -max_total, max_total)
    if #fields ~= 7 or not token(session) or not token(character) or not token(stream) or not sequence or not total
        or not save_scope.selected() then error("Invalid shared wallet report", 0) end
    if ledger_session ~= session then ledger_session, reports = session, {} end
    local owner = character .. stream
    local prior = reports[owner]
    if prior and sequence <= prior.sequence then
        if sequence == prior.sequence and total ~= prior.total then error("Conflicting shared wallet report", 0) end
        return ("%d %d"):format(prior.sequence, prior.total)
    end
    local current = read_cash(instance, key)
    local after = clamp_cash(current + total - (prior and prior.total or 0))
    if after ~= current then write_cash(instance, key, after) end
    reports[owner] = {sequence = sequence, total = total}
    return ("%d %d"):format(sequence, total)
end

function wallet.release()
    guest, ledger_session, reports = nil, nil, nil
end

function wallet.rows(state, phase, status_row)
    if phase == "connected" and save_scope.selected() then
        local ok, amount, address = pcall(function()
            local save = save_scope.bind_guest(state, phase)
            local key = FName("Player_Cash")
            if save and save.Flags:Contains(key) then
                return math.tointeger(read_projected(save, key)), save:GetAddress()
            end
            if save and guest and guest.save == save:GetAddress() and guest.character == save_scope.selected() then
                return 0, guest.save
            end
        end)
        if not ok or not amount or amount < min_cash or amount > max_cash then return {} end
        local rows = {status_row("wallet", amount)}
        if guest and guest.save == address and guest.character == save_scope.selected() then
            observe_guest(amount)
            if guest.sequence > guest.acknowledged then
                rows[2] = status_row("wallet-guest-change", guest.session, guest.stream, guest.sequence, guest.total)
            end
        end
        return rows
    end
    local pawn = host_pawn(state, phase)
    if not pawn then
        last_read_error = nil
        return {}
    end
    local ok, current = pcall(function()
        return read_cash(game_instance(pawn), FName("Player_Cash"))
    end)
    if not ok and current ~= last_read_error then print("[SilverfishCoop] Wallet read failed: " .. tostring(current) .. "\n") end
    last_read_error = not ok and current or nil
    return { status_row("wallet", ok and current or "-") }
end

return wallet
