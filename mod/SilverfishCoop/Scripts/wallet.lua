-- Project the durable co-op wallet into the staged host's stock UI state.
local wallet = {}
local zones = require("zone_maps")

local playtest_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local max_cash = 2147483647
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local last_read_error

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

function wallet.command(fields, state, phase)
    if fields[2] ~= "wallet-set" then return nil end
    if #fields ~= 3 then error("Invalid wallet-set command", 0) end
    local amount = parse_amount(fields[3])
    local pawn = host_pawn(state, phase)
    if not pawn then error("Only the active test-map host can project the wallet", 0) end

    local instance = game_instance(pawn)
    local key = FName("Player_Cash")
    local current, found = read_cash(instance, key)
    if not found or current ~= amount then
        -- Overwrite=true and Force_Decrease=true select stock's absolute Map_Add path.
        instance:Add_PointTo_SaveFlag(key, amount, true, true)
        current, found = read_cash(instance, key)
        if not found or current ~= amount then error("Wallet native balance failed read-back", 0) end
    end
    return "native wallet verified"
end

function wallet.rows(state, phase, status_row)
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
