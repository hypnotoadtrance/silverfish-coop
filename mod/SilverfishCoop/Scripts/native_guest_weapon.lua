local guest_weapon = {}
local guest_save = require("guest_save")
local save_scope = require("native_save_scope")
local weapon_profiles = require("guest_weapon_registry")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local flayer_fixture = fixtures and headless and os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW") == "flayer"
    and require("native_campaign_npc")
local spratt_fixture = fixtures and headless and require("native_spratt_fixture")
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local blowtorch_path = "/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C"
local crowbar_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C"
local spade_path = "/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C"
local pry_tool_paths = {blowtorch = blowtorch_path, crowbar = crowbar_path}
local flare_fixture_paths = {
    ["stock-flare"] = "/Game/Classes/Equipment/Eqp_Flare.Eqp_Flare_C",
    ["stock-flare-blue"] = "/Game/Classes/Equipment/Eqp_Flare_Blue.Eqp_Flare_Blue_C",
    ["stock-flare-yellow"] = "/Game/Classes/Equipment/Eqp_Flare_Yellow.Eqp_Flare_Yellow_C",
    ["stock-bolt"] = "/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C",
    ["stock-pipe-bomb"] = "/Game/Classes/Equipment/Gren_PipeBomb.Gren_PipeBomb_C",
}
local old_pipe_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"
local melee_folder = "/Game/Classes/Weapons/MeleeWeapons/"
local melee_master = melee_folder .. "MeleeWep_Master_BP.MeleeWep_Master_BP_C"
local camera_map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local observation
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local weight_field = "Weight_12_11AB9ED547504E78A849A692A36DC938"

local function same(left, right)
    return left and right and left:IsValid() and right:IsValid() and left:GetAddress() == right:GetAddress()
end

local function item_identity(row)
    local identity = row and row[guid_field]
    if not identity then return end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(identity[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return end
        words[#words + 1] = word & 0xffffffff
    end
    local guid = string.format("%08X%08X%08X%08X", table.unpack(words))
    return guid:find("[^0]") and guid or nil
end

local function selected_row(save)
    local index = math.tointeger(save.PlayerEquipIndex)
    local equipment = save.PlayerEquipment
    local count = equipment and math.tointeger(equipment:GetArrayNum())
    if index and count and index >= 0 and index < count and count <= 4096 then
        return equipment[index + 1]
    end
end

local function owned_hold(pawn, save, held)
    return held and held:IsValid() and same(pawn.Save, save) and held:HasAuthority()
        and not held:IsActorBeingDestroyed() and same(held:GetWorld(), pawn:GetWorld())
        and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
        and same(held.User, pawn) and held.HeldByPlayer == true
end

function guest_weapon.local_projected_selection(pawn, save, projected)
    local held = pawn.EquipedItem
    if not owned_hold(pawn, save, held) then return end
    local row = selected_row(save)
    local guid = item_identity(row)
    local index = save.PlayerEquipIndex
    local incoming = guid and index < projected:GetArrayNum() and projected[index + 1]
    if incoming and item_identity(incoming) == guid and same(held:GetClass(), row[class_field])
        and same(incoming[class_field], row[class_field]) then return index end
end

local function equipped_guid(pawn, save, world, area)
    local held = pawn.EquipedItem
    if not held or not held:IsValid() then return nil end
    local held_class = held:GetClass()
    if not held_class or not held_class:IsValid() then return nil end
    local row = selected_row(save)
    local class = row and row[class_field]
    if not same(class, held_class) then return end
    local guid = item_identity(row)
    if not guid then return end
    local class_name = held_class:GetFullName():match("^%S+ (%S+)$")
    local melee_path = class_name and class_name:sub(1, #melee_folder) == melee_folder
    if world and (class_name == blowtorch_path or (class_name == camera_path and area == camera_map)
        or melee_path or class_name == spade_path)
        and held:HasAuthority() and not held:IsActorBeingDestroyed()
        and same(held:GetWorld(), world) and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
        and same(held.User, pawn) and held.HeldByPlayer == true and pawn.IsWeaponBlocked == false then
        if class_name == camera_path then return guid, "camera" end
        if class_name == blowtorch_path and math.type(held.Ammo) == "integer" and 0 <= held.Ammo and held.Ammo <= 3
            and held.Ammo == row[ammo_field] then return guid, "blowtorch" end
        if melee_path and row[ammo_field] == -1 and held.Ammo == -1 then
            if class_name == old_pipe_path then return guid, "old-pipe" end
            if class_name == crowbar_path then return guid, "crowbar" end
            local base = StaticFindObject(melee_master)
            if base and base:IsValid() and held:IsA(base) and not same(held_class, base)
                and held.ComboAttacks:GetArrayNum() > 0 then return guid, "melee" end
        end
        if class_name == spade_path and row[ammo_field] == -1 and held.Ammo == -1 and same(held.Player_Ref, pawn) then
            return guid, "spade"
        end
    end
    return guid, weapon_profiles.kinds[class_name], row
end

function guest_weapon.clear_empty_slot_hold(pawn, save)
    local held, row = pawn.EquipedItem, selected_row(save)
    if not row or item_identity(row)
        or (row[class_field] and row[class_field]:IsValid()) then return end
    if not owned_hold(pawn, save, held) then return end
    if pawn.Aiming then pawn:CancelScope() end
    if same(pawn.EquipedItem, held) then pawn.EquipedItem = nil end
    held:K2_DestroyActor()
end

local function valid_rounds(weapon, amount)
    return math.type(amount) == "integer" and math.type(weapon.MagSize) == "integer"
        and 0 <= amount and amount <= weapon.MagSize and weapon.MagSize <= 4096
end

local function write_magazine(held, row, rounds)
    if held.Ammo ~= rounds then
        held.Ammo = rounds
        held:Update_Mag()
    end
    if row[ammo_field] ~= rounds then row[ammo_field] = rounds end
end

function guest_weapon.project_unloaded(pawn, save, guid)
    local row = selected_row(save)
    if item_identity(row) ~= guid then return true end
    local held = pawn.EquipedItem
    if not held or not held:IsValid() then return true end
    local selected, kind = equipped_guid(pawn, save)
    if selected ~= guid or not weapon_profiles.paths[kind] or not same(held.User, pawn)
        or not same(held.Player_Ref, pawn) or not same(held:GetWorld(), pawn:GetWorld())
        or held:IsActorBeingDestroyed() or not valid_rounds(held, 0) then return false end
    write_magazine(held, row, 0)
    return held.Ammo == 0 and row[ammo_field] == 0
end

local function project_magazine(fields, state, phase)
    local area, name, guid, kind, rounds = fields[3], fields[4], fields[5], fields[6], tonumber(fields[7])
    local save, pawn = save_scope.bind_guest(state, phase)
    if #fields ~= 7 or not save or state.map ~= area or pawn:GetFullName() ~= name then
        error("The co-op magazine belongs to another guest or visit", 0)
    end
    local selected, actual_kind, row = equipped_guid(pawn, save)
    local held = pawn.EquipedItem
    if not weapon_profiles.paths[kind] or selected ~= guid or actual_kind ~= kind or not row
        or not same(held:GetWorld(), state.world) or not same(held.User, pawn) or not same(held.Player_Ref, pawn)
        or held:IsActorBeingDestroyed() or not valid_rounds(held, rounds) then
        error("The co-op magazine no longer matches the selected weapon", 0)
    end
    write_magazine(held, row, rounds)
    return "stock co-op magazine projected"
end

local function play_reload(fields, state, phase)
    local area, name, guid, previous = fields[3], fields[4], fields[5], tonumber(fields[6])
    local save, pawn = save_scope.bind_guest(state, phase)
    if #fields ~= 6 or not save or state.map ~= area or pawn:GetFullName() ~= name or pawn.Dead ~= false then return "" end
    local selected, kind = equipped_guid(pawn, save)
    local held = pawn.EquipedItem
    if not weapon_profiles.paths[kind] or selected ~= guid or not same(held:GetWorld(), state.world)
        or not same(held.User, pawn) or not same(held.Player_Ref, pawn)
        or held:IsActorBeingDestroyed() or not valid_rounds(held, previous) or previous >= held.MagSize then return "" end
    return require("native_avatar_weapon_view").local_reload(pawn, state.world, held, previous) or ""
end

function guest_weapon.preserve_magazine(pawn, save, projected, selected_slot)
    if save.PlayerEquipIndex ~= selected_slot then return end
    local guid, kind, row = equipped_guid(pawn, save)
    local item = guid and row and weapon_profiles.paths[kind] and projected[selected_slot + 1]
    if item and item_identity(item) == guid and same(item[class_field], row[class_field]) then
        local held, weight = pawn.EquipedItem, item[weight_field]
        if item[ammo_field] == 0 and row[ammo_field] == 0 and held.Ammo == 0 and same(held.User, pawn)
            and type(weight) == "number" and weight >= 0 and weight < math.huge and held.Weight ~= weight then
            held.Weight = weight
            held:Update_Mag()
        end
        item[ammo_field] = row[ammo_field]
    end
end

function guest_weapon.command(fields, state, phase)
    if fields[2] == "native-guest-weapon-magazine" then return project_magazine(fields, state, phase) end
    if fields[2] == "native-guest-weapon-reload-view" then return play_reload(fields, state, phase) end
    local action = fields[2]:match("^native%-guest%-weapon%-([a-z%-]+)$")
    if not action then return end
    local operations = {
        seed = SilverfishSeedNativeGuestWeapon,
        ["seed-ammo"] = SilverfishSeedNativeGuestAmmo,
        inspect = SilverfishInspectNativeGuestWeapon,
        equip = SilverfishEquipNativeGuestWeapon,
        select = SilverfishSelectNativeGuestEquipment,
        fire = SilverfishFireNativeGuestWeapon,
        reload = SilverfishReloadNativeGuestWeapon,
    }
    local invoke = operations[action]
    local inspect = operations.inspect
    local name, guid = fields[3], fields[4]
    local fixture = action == "seed" or action == "seed-ammo"
    local fixture_kind = fields[5] == "smg-standard-full" and "smg-standard" or fields[5]
    local seed_profile = action == "seed" and #fields == 5 and fixtures and headless
        and (pry_tool_paths[fixture_kind] or weapon_profiles.seeds[fixture_kind] or flare_fixture_paths[fixture_kind])
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
    local restore, refusal
    local spratt = spratt_fixture and state.map == spratt_fixture.map
    local shot_fixture = spratt and spratt_fixture or flayer_fixture
    local shot_tag = spratt and "spratt" or "flayer"
    if action == "fire" and shot_fixture then
        local checked
        checked, restore, refusal = pcall(shot_fixture.before_guest_fire, state, phase, name, guid)
        if not checked then
            local reason = tostring(restore)
            restore = nil
            local scoped_reason = reason:match("^mutation_unverified_") or reason:match("^stock_spratt_")
                or reason:match("^stock_flood_jammer_aim_")
            refusal = spratt and scoped_reason and reason or ("stock_" .. shot_tag .. "_shot_ticket_check_failed")
        end
    end
    local ok, status, first, second, fifth, sixth
    if refusal then
        ok, status = false, refusal
    elseif restore then
        local result = table.pack(pcall(invoke, pawn_address, save_address, guid))
        if not pcall(restore) then
            ok, status = false, "mutation_unverified_stock_" .. shot_tag .. "_shot_pose_restore"
        elseif not result[1] then
            ok, status = false, "mutation_unverified_stock_" .. shot_tag .. "_shot_native_fire"
        else
            ok, status, first, second, fifth, sixth = table.unpack(result, 2, result.n)
        end
    else
        ok, status, first, second, fifth, sixth = invoke(pawn_address, save_address, guid, table.unpack(seed_variant))
    end
    local before, after, actor_ammo, stored_ammo, projectiles, reserve_before, reserve_after
    if action == "fire" then projectiles = fifth end
    if action == "reload" then before, after, reserve_before, reserve_after = first, second, fifth, sixth end
    if ok and (action == "inspect" or action == "select") then
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
    if ok and action == "reload" then pcall(require("native_weapon_fire_view").reloaded, pawn, before) end
    if not ok and action == "fire" and status == "guest_weapon_preloaded_magazine_required"
        and first == 0 and second == 0 and projectiles == 0 then
        pcall(require("native_weapon_fire_view").dry_fired, pawn)
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
    if not state.listening then return rows end
    if phase == "connected" and state.server == true and type(SilverfishReadLocalGuestEquipmentSelection) == "function" then
        local local_pawn = state.local_pawn
        local pawn = local_pawn and local_pawn.pawn
        if pawn and pawn:IsValid() then
            local sequence, guid = SilverfishReadLocalGuestEquipmentSelection(pawn:GetAddress())
            if sequence and guid then
                rows[1] = status_row("native-guest-equipment-selection", state.map, local_pawn.name, sequence, guid)
            end
        end
        return rows
    end
    if not enabled or phase ~= "hosting" then return rows end
    for _, detail in ipairs(state.guests) do
        local ok, guid, kind, rounds = pcall(function()
            local pawn, save = guest_save.exact_item_context(state, phase, detail.name)
            if not pawn or not save then return end
            local id, profile, row = equipped_guid(pawn, save, state.world, state.map)
            local held = row and pawn.EquipedItem
            local amount = row and row[ammo_field]
            if held and weapon_profiles.paths[profile] and amount == held.Ammo and valid_rounds(held, amount) then
                return id, profile, amount
            end
            return id, profile
        end)
        if ok and guid and kind then
            rows[#rows + 1] = status_row("native-guest-equipped-weapon", detail.name, guid, kind, rounds or "-")
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
