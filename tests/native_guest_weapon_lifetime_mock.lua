local registry = dofile("mod/SilverfishCoop/Scripts/guest_weapon_registry.lua")
local kind = "type33-shorty"
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local pawn_name = "fixture-guest"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local weight_field = "Weight_12_11AB9ED547504E78A849A692A36DC938"

local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end,
        IsActorBeingDestroyed = function(self) return self.retiring == true end}
end

local world, pawn, class, rifle = object(1), object(2), object(3), object(4)
local save = object(6)
save.PlayerEquipIndex = 0
local identity = {A = 1, B = 2, C = 3, D = 4}
local row = {[class_field] = class, [guid_field] = identity, [ammo_field] = 0}
save.PlayerEquipment = {[1] = row, GetArrayNum = function() return 1 end}
pawn.Save, pawn.EquipedItem = save, rifle
pawn.GetWorld, rifle.GetWorld = function() return world end, function() return world end
rifle.GetClass, class.GetFullName = function() return class end, function()
    return "BlueprintGeneratedClass " .. registry.paths[kind]
end
rifle.User, rifle.Player_Ref, rifle.Instigator, rifle.HeldByPlayer = pawn, pawn, pawn, true
rifle.GetOwner, rifle.HasAuthority = function() return pawn end, function() return true end
rifle.Ammo, rifle.Weight, rifle.MagSize, rifle.AmmoType = 0, 1, 5, {}

local magazine_updates, destroys = 0, 0
function rifle:Update_Mag()
    assert(not self.retiring, "Tried to update the magazine after weapon EndPlay began")
    magazine_updates = magazine_updates + 1
end
function rifle:K2_DestroyActor()
    assert(not self.retiring, "Tried to destroy a weapon that started retiring during CancelScope")
    destroys = destroys + 1
    self.retiring = true
end

local modules = {
    guest_save = {status_context = function() return pawn, save end},
    native_save_scope = {},
    guest_weapon_registry = registry,
}
local env = setmetatable({
    require = function(name) return assert(modules[name], name) end,
    os = {getenv = function(name) return name == "SILVERFISH_COOP_NATIVE_GUEST_SAVE" and "1" or nil end},
}, {__index = _G})
local weapon = assert(loadfile("mod/SilverfishCoop/Scripts/native_guest_weapon.lua", "t", env))()
local state = {listening = true, world = world, map = area, guests = {{name = pawn_name}}}
local projected = {{[class_field] = class, [guid_field] = identity, [ammo_field] = 0, [weight_field] = 5}}

local rows = weapon.rows(state, "hosting", table.pack)
assert(#rows == 1 and rows[1][1] == "native-guest-equipped-weapon" and rows[1][4] == kind
    and rows[1][5] == 0, "A live rifle must still report its exact empty magazine")
weapon.preserve_magazine(pawn, save, projected, 0)
assert(rifle.Weight == 5 and magazine_updates == 1)

rifle.retiring, rifle.Weight = true, 1
assert(rifle:IsValid() and #weapon.rows(state, "hosting", table.pack) == 0,
    "A still-valid rifle in EndPlay must not publish an actionable equipped scope")
weapon.preserve_magazine(pawn, save, projected, 0)
assert(rifle.Weight == 1 and magazine_updates == 1)

save.PlayerEquipment[1] = {}
rifle.retiring, pawn.Aiming = false, true
local replacement = object(5)
function pawn:CancelScope()
    rifle.retiring = true
    self.EquipedItem = replacement
    self.Aiming = false
end
weapon.clear_empty_slot_hold(pawn, save)
assert(pawn.EquipedItem == replacement and destroys == 0, "Defer to stock when canceling aim starts retiring the old weapon")

rifle.retiring, pawn.Aiming, pawn.EquipedItem = false, true, rifle
function pawn:CancelScope() self.Aiming = false end
weapon.clear_empty_slot_hold(pawn, save)
assert(not pawn.EquipedItem and not pawn.Aiming and destroys == 1, "An owned live weapon still clears from its empty slot")
print("Retiring guest weapon observations and aimed-slot cleanup passed")
