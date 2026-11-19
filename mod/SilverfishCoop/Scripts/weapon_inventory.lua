local weapon_inventory = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local weapon_path = "/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C"
local payload_path = "/Game/Classes/Libraries/Structs/WeaponData_Struct.WeaponData_Struct"
local result

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function guest_pawn(state, phase, name)
    if phase ~= "hosting" or state.clients ~= 1 or #state.pawns ~= 2 or not valid(state.world) then
        error("The weapon inventory proof requires an active two-player host", 0)
    end
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" and details.name == name and guest_save.matches(details.pawn) then
            return details.pawn
        end
    end
    error("The exact guest does not have a detached native save", 0)
end

local function take_stock_sample(pawn)
    local class = StaticFindObject(weapon_path)
    if not valid(class) then
        LoadAsset(weapon_path)
        class = StaticFindObject(weapon_path)
    end
    if not valid(class) then error("The stock input weapon class could not be loaded", 0) end
    local verified, guid, ammo, size, payload_size =
        SilverfishRoundTripWeaponItem(pawn:GetAddress(), pawn.Save:GetAddress())
    if not verified then error("Stock weapon inventory round trip failed (" .. tostring(guid) .. ")", 0) end
    return { guid = guid, ammo = ammo, size = size, payload_size = payload_size }
end

function weapon_inventory.command(fields, state, phase)
    if fields[2] ~= "native-weapon-roundtrip" then return end
    if #fields ~= 3 or not enabled or type(SilverfishRoundTripWeaponItem) ~= "function" then
        error("The isolated weapon inventory round trip is unavailable", 0)
    end
    local pawn = guest_pawn(state, phase, fields[3])
    result = nil
    result = take_stock_sample(pawn)
    return "stock weapon inventory was exported and restored identically"
end

function weapon_inventory.rows(state, phase, status_row)
    if not result or phase ~= "hosting" then return {} end
    return { status_row("native-weapon-roundtrip", "verified", weapon_path, payload_path, result.guid,
        result.ammo, result.size, result.payload_size) }
end

return weapon_inventory
