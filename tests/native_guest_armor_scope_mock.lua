local chest = "/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C"
local head = "/Game/Classes/Armor_Items/Helm_Basic.Helm_Basic_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local function owned(address)
    return {GetAddress = function() return address end}
end

local function worn(path, id)
    return {
        [class_field] = {IsValid = function() return true end, GetFullName = function()
            return "BlueprintGeneratedClass " .. path
        end},
        [guid_field] = {A = 0, B = 0, C = 0, D = id},
    }
end

local observed, explicit, projections = 0, 0, {}
local guest_save = {}
function guest_save.observed_item_context(state, phase, detail)
    assert(state.guests[detail.index] == detail and phase == "hosting")
    observed = observed + 1
    if detail.bound then return detail.pawn, detail.save end
end
function guest_save.exact_item_context(state, phase, name)
    explicit = explicit + 1
    assert(phase == "hosting")
    for _, detail in ipairs(state.guests) do
        if detail.bound and detail.name == name then return detail.pawn, detail.save end
    end
end

local function projected(slot, pawn, save, guid)
    projections[#projections + 1] = {slot, pawn, save, guid}
    return true, "verified", 250, 250, 15, 15, 0, 0, 0
end

local mutated
local env = setmetatable({
    require = function(name) assert(name == "guest_save"); return guest_save end,
    os = {time = os.time, getenv = function(name)
        if name == "SILVERFISH_COOP_NATIVE_ZONE" or name == "SILVERFISH_COOP_NATIVE_GUEST_SAVE" then return "1" end
    end},
    SilverfishProjectNativeGuestArmor = function(...) return projected("body", ...) end,
    SilverfishProjectNativeGuestHelmet = function(...) return projected("head", ...) end,
    SilverfishEquipNativeGuestArmor = function(pawn, save, guid)
        mutated = {pawn, save, guid}
        return true, "verified", 250, 250, 15, 15, 0, 0, 0
    end,
}, {__index = _G})
local armor = assert(loadfile("mod/SilverfishCoop/Scripts/native_guest_armor.lua", "t", env))()
local state = {map = "/Game/Example", guests = {}}
for index = 1, 4 do
    local pawn, save = owned(index), owned(index + 4)
    save.PlayerArmor, save.PlayerHelmet = worn(chest, index), worn(head, index + 4)
    state.guests[index] = {index = index, name = "Guest " .. index, bound = true, pawn = pawn, save = save}
end

armor.sync(state, "hosting")
armor.sync(state, "hosting")
assert(observed == 8 and explicit == 0, "Routine armor must use the current, previously qualified guest observation")
assert(#armor.rows(state, "hosting", table.pack) == 8 and #projections == 8)

local last = state.guests[4]
last.save.PlayerHelmet = worn(head, 10)
armor.sync(state, "hosting")
assert(#projections == 10 and projections[9][1] == "body" and projections[10][1] == "head",
    "Replacing either worn item must refresh both combined protection projections")

last.bound = false
armor.sync(state, "hosting")
assert(#armor.rows(state, "hosting", table.pack) == 6 and #projections == 10,
    "A guest who lost the current binding must lose their old projection")
last.pawn, last.bound = owned(44), true
armor.sync(state, "hosting")
assert(#projections == 12 and projections[11][2] == 44 and projections[12][2] == 44,
    "Returning under a new pawn must recheck both projections")

local guid = ("0"):rep(31) .. "1"
armor.command({"ticket", "native-guest-armor-equip", "Guest 1", guid}, state, "hosting")
assert(explicit == 1 and mutated[1] == 1 and mutated[2] == 5 and mutated[3] == guid,
    "An explicit equip command must still qualify its own exact guest")
armor.sync(state, "ready")
assert(#armor.rows(state, "hosting", table.pack) == 1 and #projections == 12)
print("Worn armor reused the current guest observation and requalified changes")
