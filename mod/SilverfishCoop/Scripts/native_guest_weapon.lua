local guest_weapon = {}
local guest_save = require("guest_save")
local weapon_profiles = require("guest_weapon_registry")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local camera_map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local observation
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"

local function same(left, right)
    return left and right and left:IsValid() and right:IsValid() and left:GetAddress() == right:GetAddress()
end

local function equipped_guid(pawn, save, world)
    local held = pawn.EquipedItem
    if not held or not held:IsValid() then return nil end
    local held_class = held:GetClass()
    if not held_class or not held_class:IsValid() then return nil end
    local index = math.tointeger(save.PlayerEquipIndex)
    local equipment = save.PlayerEquipment
    local count = equipment and math.tointeger(equipment:GetArrayNum())
    if not index or not count or index < 0 or index >= count or count > 4096 then return nil end
    local row = equipment[index + 1]
    local class = row and row[class_field]
    local identity = row and row[guid_field]
    if not class or not class:IsValid() or class:GetAddress() ~= held_class:GetAddress() or not identity then return nil end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(identity[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return nil end
        words[#words + 1] = word & 0xffffffff
    end
    local guid = string.format("%08X%08X%08X%08X", table.unpack(words))
    local class_name = held_class:GetFullName():match("^%S+ (%S+)$")
    if world and class_name == camera_path and held:HasAuthority() and not held:IsActorBeingDestroyed()
        and same(held:GetWorld(), world) and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
        and same(held.User, pawn) and held.HeldByPlayer == true and pawn.IsWeaponBlocked == false then
        return guid:find("[^0]") and guid or nil, "camera"
    end
    return guid:find("[^0]") and guid or nil, weapon_profiles.kinds[class_name]
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
    local fixture = action == "seed" or action == "seed-ammo"
    local seed_profile = action == "seed" and #fields == 5 and fixtures and headless and weapon_profiles.seeds[fields[5]]
    local equipped_target = action == "reload" and guid == "equipped"
    observation = nil
    if not enabled or (#fields ~= 4 and not seed_profile) or phase ~= "hosting"
        or (not equipped_target and (not guid or #guid ~= 32 or guid:find("[^0-9A-F]"))) then
        error("A native weapon operation needs an exact guest and item GUID", 0)
    end
    if fixture and not fixtures then error("native_guest_weapon_fixtures_disabled", 0) end
    if type(invoke) ~= "function" or type(inspect) ~= "function" then
        error("The matched native weapon add-on is unavailable", 0)
    end
    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    if not qualified or not pawn or not detached then error("guest_weapon_refused_detached_save", 0) end
    if equipped_target then
        local readable, equipped, kind = pcall(equipped_guid, pawn, detached)
        if not readable or not equipped or not kind then
            observation = {map = state.map, action = action, pawn = name, guid = "equipped", ok = false,
                status = "guest_weapon_no_qualified_equipped_item"}
            return "native guest reload refused"
        end
        guid = equipped
    end
    if fixture then
        local path = seed_profile or weapon_profiles.paths.smg
        if action == "seed-ammo" then
            local readable, _, held_kind = pcall(equipped_guid, pawn, detached)
            path = (headless and readable and weapon_profiles.ammo[held_kind]) or weapon_profiles.default_ammo
        end
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path) end
    end

    local pawn_address, save_address = pawn:GetAddress(), detached:GetAddress()
    local seed_variant = seed_profile and { fields[5] } or {}
    local ok, status, first, second, fifth, sixth = invoke(pawn_address, save_address, guid, table.unpack(seed_variant))
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
    local rows = {}
    if not enabled or phase ~= "hosting" or not state.listening then return rows end
    for _, detail in ipairs(state.guests) do
        local ok, guid, kind = pcall(function()
            local pawn, save = guest_save.exact_item_context(state, phase, detail.name)
            if pawn and save then return equipped_guid(pawn, save, state.map == camera_map and state.world or nil) end
        end)
        if ok and guid and kind then
            rows[#rows + 1] = status_row("native-guest-equipped-weapon", detail.name, guid, kind)
        end
    end
    local row = observation
    if not row or row.map ~= state.map then return rows end
    rows[#rows + 1] = status_row("native-guest-weapon", row.map, row.action, row.pawn, row.guid, row.ok and 1 or 0,
        row.status, row.before or "-", row.after or "-", row.actor_ammo or "-", row.stored_ammo or "-",
        row.projectiles or "-", row.reserve_before or "-", row.reserve_after or "-")
    return rows
end

return guest_weapon
