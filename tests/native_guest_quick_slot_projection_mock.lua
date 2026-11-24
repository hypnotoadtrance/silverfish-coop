package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local character = string.rep("a", 32)
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local weapon_path = "/Game/Classes/Weapons/Wep_RustyPistol.Wep_RustyPistol_C"
local class_key = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_key = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_key = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"

local function object(fields)
    fields.IsValid = function(self) return not self.destroyed end
    fields.GetAddress = function(self) return self end
    return fields
end

local function array(fields)
    fields.GetArrayNum = function(self) return #self end
    return fields
end

local save_class = object({})
local item_class = object({})
local weapon_class = object({GetFullName = function() return "BlueprintGeneratedClass " .. weapon_path end,
    GetCDO = function() return {IsA = function(_, class) return class == item_class end} end})
local function stock_item(number, ammunition)
    return {[class_key] = weapon_class, [guid_key] = {A = number, B = 0, C = 0, D = 0}, [ammo_key] = ammunition}
end

local function stock_save()
    return setmetatable(object({GetClass = function() return save_class end,
        JournalEntries = array({}), ActiveQuests = {}, Character = {name = {ToString = function() return "" end}}}), {
        __newindex = function(self, key, value)
            if key == "PlayerEquipment" or key == "PlayerBackpack" then value = array(value) end
            rawset(self, key, value)
        end,
    })
end

local standing = true
local world = object({OwningGameInstance = {Calc_CarryWeight = function() end}})
local pawn = object({Dead = false, GetWorld = function() return world end})
local state = {map = map, world = world, controller = {MenuStack = array({})}}

package.loaded.guest_save = {}
package.loaded.native_guest_creator = {name_field = "name"}
package.loaded.native_avatar_character_view = {}
package.loaded.native_world = {}
package.loaded.players = {is_downed = function(current) assert(current == pawn); return not standing end}
package.loaded.native_save_scope = {bind_guest = function(current, phase)
    assert(current == state)
    if phase == "connected" then return pawn.Save, pawn, character end
end}
local statics = object({CreateSaveGameObject = function(_, class) assert(class == save_class); return stock_save() end,
    GetPlayerCharacter = function() return pawn end})
StaticFindObject = function(path)
    return ({[weapon_path] = weapon_class, ["/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"] = item_class,
        ["/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"] = save_class,
        ["/Script/Engine.Default__GameplayStatics"] = statics})[path]
end
local ui = require("native_guest_ui")

local function local_slot(actor_world)
    local save = stock_save()
    save.PlayerEquipIndex = 1
    save.PlayerEquipment = {stock_item(1, 6), stock_item(2, 5)}
    local held = object({Ammo = 5, Instigator = pawn, User = pawn, HeldByPlayer = true,
        GetClass = function() return weapon_class end, HasAuthority = function() return true end,
        IsActorBeingDestroyed = function(self) return self.destroyed == true end, GetOwner = function() return pawn end,
        GetWorld = function() return actor_world or world end, K2_DestroyActor = function(self) self.destroyed = true end})
    pawn.Save, pawn.EquipedItem = save, held
    return save, held
end

local function project(selected, second_item, phase)
    local payload = {map .. "\t" .. character, ("inventory-layout\t0\t2\t%d"):format(selected)}
    local function slot(index, number, ammunition)
        payload[#payload + 1] = table.concat({"item", "equipment", index, weapon_path, ammunition, 1, 10, 0,
            number, 0, 0, 0}, "\t")
    end
    slot(1, 1, 6)
    if second_item then slot(2, second_item, 7) end
    return ui.memory_command({string.rep("b", 32), "native-guest-ui-project-memory", character}, state,
        phase or "connected", table.concat(payload, "\n") .. "\n")
end

local save, held = local_slot()
assert(project(0, 2):match("^stock co%-op inventory and Journal projected"))
assert(save.PlayerEquipIndex == 1 and pawn.EquipedItem == held and save.PlayerEquipment[2][ammo_key] == 5)

save = local_slot()
project(0, 3)
assert(save.PlayerEquipIndex == 0 and save.PlayerEquipment[2][guid_key].A == 3 and save.PlayerEquipment[2][ammo_key] == 7)

save, held = local_slot()
project(0)
assert(save.PlayerEquipIndex == 0 and not save.PlayerEquipment[2][class_key] and not held.destroyed)

save, held = local_slot()
project(1)
assert(save.PlayerEquipIndex == 1 and not pawn.EquipedItem and held.destroyed)

save = local_slot(object({}))
assert(not pcall(project, 0, 2, "joining") and save.PlayerEquipIndex == 1)
project(0, 2)
assert(save.PlayerEquipIndex == 0 and save.PlayerEquipment[2][ammo_key] == 7)

standing = false
save = local_slot()
project(0, 2)
assert(save.PlayerEquipIndex == 0 and save.PlayerEquipment[2][ammo_key] == 7)
