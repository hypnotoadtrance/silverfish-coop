local guest_weapon = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local weapon_path = "/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C"
local observation

function guest_weapon.command(fields, state, phase)
    local action = fields[2]:match("^native%-guest%-weapon%-(%l+)$")
    if not action then return end
    local operations = {
        seed = SilverfishSeedNativeGuestWeapon,
        inspect = SilverfishInspectNativeGuestWeapon,
        equip = SilverfishEquipNativeGuestWeapon,
        fire = SilverfishFireNativeGuestWeapon,
    }
    local invoke = operations[action]
    local inspect = operations.inspect
    local name, guid = fields[3], fields[4]
    observation = nil
    if not enabled or #fields ~= 4 or phase ~= "hosting"
        or not guid or #guid ~= 32 or guid:find("[^0-9A-F]") then
        error("A staged native weapon operation needs an exact guest and item GUID", 0)
    end
    if type(invoke) ~= "function" or type(inspect) ~= "function" then
        error("The matched native weapon add-on is unavailable", 0)
    end
    local pawn, detached = guest_save.exact_item_context(state, phase, name)
    if not pawn or not detached then error("The exact guest weapon save is unavailable", 0) end
    if action == "seed" then
        local class = StaticFindObject(weapon_path)
        if not class or not class:IsValid() then LoadAsset(weapon_path) end
    end

    local pawn_address, save_address = pawn:GetAddress(), detached:GetAddress()
    local ok, status, first, second, projectiles = invoke(pawn_address, save_address, guid)
    local before, after, actor_ammo, stored_ammo
    if ok and action == "inspect" then
        actor_ammo, stored_ammo = first, second
    elseif ok then
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
        actor_ammo = actor_ammo, stored_ammo = stored_ammo, projectiles = projectiles }
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
        row.projectiles or "-") }
end

return guest_weapon
