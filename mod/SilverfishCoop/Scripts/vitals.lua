local vitals = {}

local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local playtest_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local copied_fields = { "PlayerFood", "PlayerRegen", "PlayerStim", "PlayerRads", "PlayerShieldHealth" }
local probe

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function guest_pawn(state)
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" then return details.pawn end
    end
end

local function valid_scene(state, phase)
    return phase == "hosting" and playtest_maps[state.map] and state.clients == 1
        and state.local_pawn and valid(state.local_pawn.pawn)
end

function vitals.active()
    return probe ~= nil
end

function vitals.reset()
    if not probe then return end
    if valid(probe.pawn) and same(probe.pawn.Save, probe.experimental)
        and valid(probe.original) then
        probe.pawn.Save = probe.original
    end
    probe = nil
end

function vitals.sync(state, phase)
    if not probe then return end
    if not valid_scene(state, phase) or state.map ~= probe.map
        or not same(guest_pawn(state), probe.pawn)
        or not valid(probe.owner) or not same(probe.owner.SaveSlot_Ref, probe.original) then
        vitals.reset()
    end
end

local function begin_probe(state)
    if probe then error("Reset the existing vitals probe first", 0) end
    local host, guest = state.local_pawn.pawn, guest_pawn(state)
    if not valid(guest) or not host:HasAuthority() or not guest:HasAuthority() or same(host, guest) then
        error("Two distinct authoritative pawns are required", 0)
    end
    local stock = guest.Save
    local statics = StaticFindObject(gameplay_statics)
    local class = StaticFindObject(save_class)
    if not valid(statics) or not valid(class) then
        error("The native save object class is unavailable", 0)
    end
    local instance = statics:GetGameInstance(host)
    if not valid(instance) or not same(host.Save, stock) or not same(instance.SaveSlot_Ref, stock) then
        error("The two pawns do not share a stock save owned by this game instance", 0)
    end

    local maximum = guest.MaxHealth
    if not finite(stock.PlayerHealth) or stock.PlayerHealth <= 2 or not finite(maximum) or maximum < 1 then
        error("Current health is outside the safe range for this probe", 0)
    end
    local experimental = statics:CreateSaveGameObject(class)
    if not valid(experimental) or same(experimental, stock) then
        error("Could not allocate an independent in-memory save object", 0)
    end
    for _, field in ipairs(copied_fields) do experimental[field] = stock[field] end
    experimental.PlayerHealth = math.max(1, math.min(maximum, stock.PlayerHealth * 0.5))
    -- The original remains a strong GameInstance.SaveSlot_Ref. The new object
    -- is kept alive by the guest pawn's typed Save property.
    guest.Save = experimental
    if not same(guest.Save, experimental) then
        error("Could not bind the independent save object to the guest pawn", 0)
    end

    probe = { pawn = guest, map = state.map, original = stock, experimental = experimental, owner = instance }
    return "guest vitals ownership probe active"
end

function vitals.command(fields, state, phase)
    local command = fields[2]
    if command ~= "vitals-probe" and command ~= "vitals-reset" then return end
    if #fields ~= 2 or phase ~= "hosting" or not playtest_maps[state.map] then
        error("Vitals probes are limited to an active playtest listen host", 0)
    end
    if command == "vitals-reset" then
        vitals.reset()
        return "guest vitals restored to stock ownership"
    end
    if not valid_scene(state, phase) then error("Wait until the playtest guest joins", 0) end
    return begin_probe(state)
end

function vitals.row(state, phase, status_row)
    if not valid_scene(state, phase) then return end
    local guest = guest_pawn(state)
    if not valid(guest) then return end
    local host = state.local_pawn.pawn
    local local_save, guest_save = host.Save, guest.Save
    if not valid(local_save) or not valid(guest_save) then return end
    local statics = StaticFindObject(gameplay_statics)
    local instance
    if valid(statics) then instance = statics:GetGameInstance(host) end
    return status_row("vitals", probe and "detached" or "stock",
        local_save.PlayerHealth, guest_save.PlayerHealth,
        same(local_save, valid(instance) and instance.SaveSlot_Ref or nil) and 1 or 0,
        same(local_save, guest_save) and 1 or 0)
end

return vitals
