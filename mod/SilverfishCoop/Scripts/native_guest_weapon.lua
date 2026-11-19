local guest_weapon = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local weapon_path = "/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C"
local ammo_path = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local observation
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local function equipped_guid(pawn, save)
    local held = pawn.EquipedItem
    local stock = StaticFindObject(weapon_path)
    if not held or not held:IsValid() or not stock or not stock:IsValid()
        or held:GetClass():GetAddress() ~= stock:GetAddress() then return nil end
    local index = math.tointeger(save.PlayerEquipIndex)
    local equipment = save.PlayerEquipment
    local count = equipment and math.tointeger(equipment:GetArrayNum())
    if not index or not count or index < 0 or index >= count or count > 4096 then return nil end
    local row = equipment[index + 1]
    local class = row and row[class_field]
    local identity = row and row[guid_field]
    if not class or not class:IsValid() or class:GetAddress() ~= stock:GetAddress() or not identity then return nil end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(identity[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return nil end
        words[#words + 1] = word & 0xffffffff
    end
    local guid = string.format("%08X%08X%08X%08X", table.unpack(words))
    return guid:find("[^0]") and guid or nil
end

function guest_weapon.command(fields, state, phase)
    local action = fields[2]:match("^native%-guest%-weapon%-([a-z%-]+)$")
    if not action then return end
    local operations = {
        seed = SilverfishSeedNativeGuestWeapon,
        ["seed-ammo"] = SilverfishSeedNativeGuestAmmo,
        inspect = SilverfishInspectNativeGuestWeapon,
        equip = SilverfishEquipNativeGuestWeapon,
        fire = SilverfishFireNativeGuestWeapon,
        reload = SilverfishReloadNativeGuestWeapon,
    }
    local invoke = operations[action]
    local inspect = operations.inspect
    local name, guid = fields[3], fields[4]
    local equipped_target = action == "reload" and guid == "equipped"
    observation = nil
    if not enabled or #fields ~= 4 or phase ~= "hosting"
        or (not equipped_target and (not guid or #guid ~= 32 or guid:find("[^0-9A-F]"))) then
        error("A staged native weapon operation needs an exact guest and item GUID", 0)
    end
    if type(invoke) ~= "function" or type(inspect) ~= "function" then
        error("The matched native weapon add-on is unavailable", 0)
    end
    local pawn, detached = guest_save.exact_item_context(state, phase, name)
    if equipped_target then
        local readable, equipped = false, nil
        if pawn and detached then readable, equipped = pcall(equipped_guid, pawn, detached) end
        if not readable or not equipped then
            observation = {map = state.map, action = action, pawn = name, guid = "equipped", ok = false,
                status = "guest_weapon_no_qualified_equipped_item"}
            return "native guest reload refused"
        end
        guid = equipped
    end
    if not pawn or not detached then error("The exact guest weapon save is unavailable", 0) end
    if action == "seed" or action == "seed-ammo" then
        local path = action == "seed-ammo" and ammo_path or weapon_path
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path) end
    end

    local pawn_address, save_address = pawn:GetAddress(), detached:GetAddress()
    local ok, status, first, second, fifth, sixth = invoke(pawn_address, save_address, guid)
    local before, after, actor_ammo, stored_ammo, projectiles, reserve_before, reserve_after
    if action == "fire" then projectiles = fifth end
    if action == "reload" then before, after, reserve_before, reserve_after = first, second, fifth, sixth end
    if ok and action == "inspect" then
        actor_ammo, stored_ammo = first, second
    elseif ok and action ~= "seed-ammo" then
        if action == "fire" then before, after = first, second end
        local verified, state_name, actor, stored = inspect(pawn_address, save_address, guid)
        if verified then
            state_name, actor_ammo, stored_ammo = tostring(state_name), actor, stored
            status = state_name
        else
            ok, status = false, "mutation_unverified_stock_readback"
        end
    end
    observation = { map = state.map, action = action, pawn = name, guid = guid,
        ok = ok == true, status = status, before = before, after = after,
        actor_ammo = actor_ammo, stored_ammo = stored_ammo, projectiles = projectiles,
        reserve_before = reserve_before, reserve_after = reserve_after }
    return "native guest weapon operation reported"
end

function guest_weapon.reset()
    observation = nil
end

function guest_weapon.rows(state, phase, status_row)
    local row = observation
    if not row or phase ~= "hosting" or row.map ~= state.map then return {} end
    return { status_row("native-guest-weapon", row.map, row.action, row.pawn, row.guid, row.ok and 1 or 0,
        row.status, row.before or "-", row.after or "-", row.actor_ammo or "-", row.stored_ammo or "-",
        row.projectiles or "-", row.reserve_before or "-", row.reserve_after or "-") }
end

return guest_weapon
