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
local observation
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local weight_field = "Weight_12_11AB9ED547504E78A849A692A36DC938"
local stock_selector = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:ToggleSemiAuto"
local stock_barrel_selector = "/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C:ToggleAndSaveMode"
local mode_watch, projecting_mode
local mode_hooks, mode_projection = {}, {}
local mode_sequence = 0
local mode_pending, local_modes = {}, {}

local function same(left, right)
    return left and right and left:IsValid() and right:IsValid() and left:GetAddress() == right:GetAddress()
end

local function live_actor(actor)
    return actor and actor:IsValid() and not actor:IsActorBeingDestroyed()
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
    return live_actor(pawn) and live_actor(held) and same(pawn.Save, save)
        and held:HasAuthority() and same(held:GetWorld(), pawn:GetWorld())
        and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
        and same(held.User, pawn) and held.HeldByPlayer == true
end

function guest_weapon.project_stock_hold(state, phase, path, guid)
    local save, pawn = save_scope.bind_guest(state, phase)
    if not save or live_actor(pawn.EquipedItem) then return end
    local slots = save.PlayerEquipment
    local count = slots and math.tointeger(slots:GetArrayNum())
    if not count or count < 1 or count > 4096 then return end

    local index
    for i = 1, count do
        local row = slots[i]
        if item_identity(row) == guid then
            local class = row[class_field]
            if index or not class or not class:IsValid() or class:GetFullName():match("^%S+ (%S+)$") ~= path then
                return
            end
            index = i - 1
        end
    end
    if not index or not pawn:Check_CanUseEquipment() then return end
    -- The host's held actor reference is absent on an autonomous character.
    -- The selected local inventory row is sufficient for the stock first-person equip graph.
    pawn:Equip_NextItem(index, false)
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

local function equipped_guid(pawn, save, world)
    if not live_actor(pawn) then return end
    local held = pawn.EquipedItem
    if not live_actor(held) then return end
    local held_class = held:GetClass()
    if not held_class or not held_class:IsValid() then return nil end
    local row = selected_row(save)
    local class = row and row[class_field]
    if not same(class, held_class) then return end
    local guid = item_identity(row)
    if not guid then return end
    local class_name = held_class:GetFullName():match("^%S+ (%S+)$")
    local melee_path = class_name and class_name:sub(1, #melee_folder) == melee_folder
    if world and (class_name == blowtorch_path or class_name == camera_path
        or melee_path or class_name == spade_path)
        and held:HasAuthority() and same(held:GetWorld(), world) and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
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

local function automatic_weapon(pawn)
    local save, held = pawn.Save, pawn.EquipedItem
    if not same(save, save) or not owned_hold(pawn, save, held) or held.HasSelectFire ~= true
        or type(held.SemiAutoSelected) ~= "boolean" then return end
    local guid, kind = equipped_guid(pawn, save)
    if guid and weapon_profiles.held_fire[kind] then return guid, kind, held end
end

local function mode_key(guid, group, add)
    local prefix = group == "barrel" and "SFCoopLokiBarrel_" or "SFCoopFireMode_"
    return FName(prefix .. guid, add and EFindName.FNAME_Add or EFindName.FNAME_Find)
end

local function mode_group(mode)
    if mode == "loki-primary" or mode == "loki-underbarrel" then return "barrel" end
    if mode == "semi" or mode == "auto" then return "fire" end
end

local function stock_mode(group, class)
    if group == "barrel" then return "loki-primary" end
    return class:GetCDO().SemiAutoSelected == true and "semi" or "auto"
end

local function saved_mode(save, guid, class, group)
    group = group or "fire"
    local key = mode_key(guid, group, false)
    if key:GetComparisonIndex() ~= 0 and save.Flags:Contains(key) then
        local active = save.Flags:Find(key):get() == 1
        if group == "barrel" then return active and "loki-underbarrel" or "loki-primary" end
        return active and "semi" or "auto"
    end
    return stock_mode(group, class)
end

local function remember_mode(fields, state, phase)
    local name, guid, mode = fields[3], fields[4], fields[5]
    local group = mode_group(mode)
    if not enabled or phase ~= "hosting" or #fields ~= 5 or #guid ~= 32 or guid:find("[^0-9A-F]") or not group then
        error("guest_fire_mode_unavailable", 0)
    end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not pawn or not save then error("guest_fire_mode_unavailable", 0) end
    local matched
    -- A mode change can arrive just after the guest switches slots. Keep it with the owned GUID.
    for _, array in ipairs({save.PlayerEquipment, save.PlayerBackpack}) do
        local size = array:GetArrayNum()
        if size > 4096 then error("guest_fire_mode_unavailable", 0) end
        for index = 1, size do
            local row = array[index]
            if item_identity(row) == guid then
                local class = row[class_field]
                local path = class and class:IsValid() and class:GetFullName():match("^%S+ (%S+)$")
                local kind = path and weapon_profiles.kinds[path]
                local qualified = group == "barrel" and weapon_profiles.underbarrel[kind]
                    or group == "fire" and weapon_profiles.held_fire[kind] and class:GetCDO().HasSelectFire == true
                if matched or not qualified then
                    error("guest_fire_mode_unavailable", 0)
                end
                matched = class
            end
        end
    end
    if not matched then error("guest_fire_mode_item_missing", 0) end
    if saved_mode(save, guid, matched, group) == mode then return mode end
    if mode ~= stock_mode(group, matched) then
        local key, value = mode_key(guid, group, true), (mode == "semi" or mode == "loki-underbarrel") and 1 or 0
        if save.Flags:Contains(key) then save.Flags:Find(key):Set(value) else save.Flags:Add(key, value) end
    else
        local key = mode_key(guid, group, false)
        if key:GetComparisonIndex() ~= 0 and save.Flags:Contains(key) then save.Flags:Remove(key) end
    end
    if saved_mode(save, guid, matched, group) ~= mode then error("guest_fire_mode_write_unverified", 0) end
    return mode
end

local function weapon_for_mode(pawn, group)
    if group == "fire" then return automatic_weapon(pawn) end
    local save, held = pawn.Save, pawn.EquipedItem
    if not same(save, save) or not owned_hold(pawn, save, held) then return end
    local guid, kind = equipped_guid(pawn, save)
    if guid and guest_weapon.loki_child(held, pawn, kind) then return guid, kind, held end
end

local function read_mode(held, group)
    if group == "barrel" then return held.Tertiary_Mode == true and "loki-underbarrel" or "loki-primary" end
    return held.SemiAutoSelected and "semi" or "auto"
end

local function observe_mode(context, group)
    if projecting_mode or not mode_watch then return end
    local owner = mode_watch.pawn
    if not owner:IsValid() or owner.Dead or not same(owner:GetWorld(), mode_watch.world) then return end
    local guid, _, held = weapon_for_mode(owner, group)
    if not guid or not same(held, context:get()) then return end
    local mode = read_mode(held, group)
    local key = group .. guid
    mode_sequence = mode_sequence + 1
    mode_pending[key] = {guid = guid, sequence = mode_sequence, mode = mode}
    local_modes[key] = mode
    mode_projection[group] = held
end

local function watch_mode(pawn, group, path)
    if mode_hooks[group] or (group == "fire" and not weapon_for_mode(pawn, group)) then return end
    local selector = StaticFindObject(path)
    if selector and selector:IsValid() then
        RegisterHook(path, function(context) observe_mode(context, group) end)
        mode_hooks[group] = true
    end
end

local function update_mode_watch(state, phase)
    local detail = phase == "connected" and state.server == true and state.local_pawn
    local pawn = detail and detail.pawn
    if not pawn or not pawn:IsValid() or pawn:HasAuthority() or not pawn:IsLocallyControlled() then
        pawn = nil
    end
    if not pawn or not mode_watch or not same(mode_watch.pawn, pawn) or not same(mode_watch.world, state.world) then
        mode_watch = pawn and {pawn = pawn, world = state.world, name = detail.name, area = state.map} or nil
        mode_projection, mode_pending, local_modes = {}, {}, {}
    end
    if mode_watch then
        watch_mode(pawn, "fire", stock_selector)
        watch_mode(pawn, "barrel", stock_barrel_selector)
    end
end

function guest_weapon.project_fire_mode(state, phase, guid, mode)
    update_mode_watch(state, phase)
    local group = mode_group(mode)
    if not mode_watch or not group then error("guest_fire_mode_view_unavailable", 0) end
    local selected, _, held = weapon_for_mode(mode_watch.pawn, group)
    if selected ~= guid or not held then error("guest_fire_mode_view_unavailable", 0) end
    -- A late host frame must not overwrite a local selector change on the current item.
    if same(mode_projection[group], held) then return "stock fire mode projected" end
    local expected = local_modes[group .. guid] or mode
    if read_mode(held, group) ~= expected then
        projecting_mode = true
        local changed = pcall(function()
            if group == "barrel" then held:ToggleAndSaveMode() else held:ToggleSemiAuto() end
        end)
        projecting_mode = false
        if not changed or read_mode(held, group) ~= expected then error("guest_fire_mode_view_unverified", 0) end
    end
    mode_projection[group] = held
    return "stock fire mode projected"
end

local function acknowledge_mode(fields, state, phase)
    update_mode_watch(state, phase)
    if #fields ~= 6 or not mode_watch or mode_watch.area ~= fields[3] or mode_watch.name ~= fields[4] then
        error("guest_fire_mode_view_unavailable", 0)
    end
    for _, group in ipairs({"fire", "barrel"}) do
        local key = group .. fields[5]
        local pending = mode_pending[key]
        if pending and pending.sequence == tonumber(fields[6]) then mode_pending[key] = nil end
    end
    return "stock fire mode acknowledged"
end

function guest_weapon.clear_empty_slot_hold(pawn, save)
    if not live_actor(pawn) then return end
    local held, row = pawn.EquipedItem, selected_row(save)
    if not row or item_identity(row)
        or (row[class_field] and row[class_field]:IsValid()) then return end
    if not owned_hold(pawn, save, held) then return end
    if pawn.Aiming then
        pawn:CancelScope()
        -- Stock scope cancellation can replace or retire the original actor.
        if not owned_hold(pawn, save, held) then return end
    end
    if same(pawn.EquipedItem, held) then pawn.EquipedItem = nil end
    held:K2_DestroyActor()
end

local function valid_rounds(weapon, amount)
    return math.type(amount) == "integer" and math.type(weapon.MagSize) == "integer"
        and 0 <= amount and amount <= weapon.MagSize and weapon.MagSize <= 4096
end

local ammo_table_path = "/Game/Classes/Libraries/DataTables/AmmoTypes_DT.AmmoTypes_DT"
local ammo_item_field = "Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582"

local function mounted_ammo_path(held)
    local class = held.AmmoType[ammo_item_field]
    return class and class:IsValid() and class:GetFullName():match("^%S+ (%S+)$")
end

local function allowed_ammo(kind, path)
    return path and weapon_profiles.ammo_options[kind] and weapon_profiles.ammo_options[kind][path]
end

local loki_class = "BlueprintGeneratedClass /Game/Classes/Weapons/Wep_Assets/Weapon_UnderBarrel_Loki.Weapon_UnderBarrel_Loki_C"

function guest_weapon.loki_child(held, pawn, kind)
    if not held or not held:IsValid() then return end
    if not kind then kind = weapon_profiles.kinds[held:GetClass():GetFullName():match("^%S+ (%S+)$")] end
    local field = weapon_profiles.underbarrel[kind]
    local mount = field and held and held[field]
    local child = mount and mount:IsValid() and mount.ChildActor
    if child and child:IsValid() and not child:IsActorBeingDestroyed()
        and child:GetClass():GetFullName() == loki_class
        and same(child.ParentWeapon, held) and same(child.User, pawn) and same(child.Player_Ref, pawn)
        and same(child:GetWorld(), pawn:GetWorld()) and valid_rounds(child, child.Ammo) then
        return child
    end
end

local function project_underbarrel(fields, pawn, held, kind, guid)
    local rounds, path = math.tointeger(tonumber(fields[9])), fields[10]
    local child = guest_weapon.loki_child(held, pawn, kind)
    if not child or not valid_rounds(child, rounds) or not allowed_ammo("bolt-action-shotgun-loki", path) then
        error("The co-op magazine no longer matches the selected underbarrel", 0)
    end
    if mounted_ammo_path(child) ~= path then
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path); class = StaticFindObject(path) end
        if not class or not class:IsValid() or type(SilverfishProjectNativeLokiRound) ~= "function" then
            error("The co-op shell data is unavailable", 0)
        end
        local ok, status = SilverfishProjectNativeLokiRound(pawn:GetAddress(), guid, path)
        if ok ~= true then error(status, 0) end
    end
    if child.Ammo ~= rounds then
        child.Ammo = rounds
        child:Update_Mag()
    end
    if child.Ammo ~= rounds or mounted_ammo_path(child) ~= path then
        error("The stock underbarrel projection failed readback", 0)
    end
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
    if (#fields ~= 7 and #fields ~= 8 and #fields ~= 10 and #fields ~= 11) or not save
        or state.map ~= area or pawn:GetFullName() ~= name then
        error("The co-op magazine belongs to another guest or visit", 0)
    end
    local selected, actual_kind, row = equipped_guid(pawn, save)
    local held = pawn.EquipedItem
    if not weapon_profiles.paths[kind] or selected ~= guid or actual_kind ~= kind or not row
        or not same(held:GetWorld(), state.world) or not same(held.User, pawn) or not same(held.Player_Ref, pawn)
        or held:IsActorBeingDestroyed() or not valid_rounds(held, rounds) then
        error("The co-op magazine no longer matches the selected weapon", 0)
    end
    local ammo_path = fields[8]
    if ammo_path and ammo_path ~= "-" then
        if not allowed_ammo(kind, ammo_path) then error("The co-op round does not match the receiver", 0) end
        if mounted_ammo_path(held) ~= ammo_path then
            local class = StaticFindObject(ammo_path)
            if not class or not class:IsValid() then
                LoadAsset(ammo_path)
                class = StaticFindObject(ammo_path)
            end
            if not class or not class:IsValid() then error("The co-op round is not loaded", 0) end
            local handle = class:GetCDO().AmmoType
            local source = handle and handle.DataTable
            local name = handle and handle.RowName
            local stock = same(source, StaticFindObject(ammo_table_path)) and name and source:FindRow(name:ToString())
            if not stock or not same(stock[ammo_item_field], class) then error("The co-op round data is unavailable", 0) end
            -- The guest presents the host's stock row. The reload itself runs on the host.
            held.AmmoType = stock
            if mounted_ammo_path(held) ~= ammo_path then error("The stock co-op ammo projection failed readback", 0) end
        end
    end
    write_magazine(held, row, rounds)
    if #fields >= 10 then project_underbarrel(fields, pawn, held, kind, guid) end
    if #fields == 11 then
        if mode_group(fields[11]) ~= "barrel" then error("The co-op barrel mode is unavailable", 0) end
        guest_weapon.project_fire_mode(state, phase, guid, fields[11])
    end
    return "stock co-op magazine projected"
end

local function play_reload(fields, state, phase)
    local area, name, guid, previous = fields[3], fields[4], fields[5], tonumber(fields[6])
    local save, pawn = save_scope.bind_guest(state, phase)
    local underbarrel = #fields == 7 and fields[7] == "loki"
    if (#fields ~= 6 and not underbarrel) or not save or state.map ~= area or pawn:GetFullName() ~= name
        or pawn.Dead ~= false then return "" end
    local selected, kind = equipped_guid(pawn, save)
    local held = pawn.EquipedItem
    if not weapon_profiles.paths[kind] or selected ~= guid or not same(held:GetWorld(), state.world)
        or not same(held.User, pawn) or not same(held.Player_Ref, pawn)
        or held:IsActorBeingDestroyed() then return "" end
    local weapon = held
    if underbarrel then weapon = guest_weapon.loki_child(held, pawn, kind) end
    if not weapon or not valid_rounds(weapon, previous) or previous >= weapon.MagSize then return "" end
    return require("native_avatar_weapon_view").local_reload(pawn, state.world, weapon, previous, held) or ""
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
    if fields[2] == "native-guest-weapon-mode-store" then return remember_mode(fields, state, phase) end
    if fields[2] == "native-guest-weapon-mode-ack" then return acknowledge_mode(fields, state, phase) end
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
    local view = action == "fire" and #fields == 5 and fields[5]
    local underbarrel = (view and view:sub(1, 5) == "loki:") or (action == "reload" and fields[5] == "loki")
    local shot_view = view and require("guest_shot_view").parse(underbarrel and view:sub(6) or view)
    local underbarrel_reload = underbarrel and action == "reload" and #fields == 5
    local selected_round = action == "reload" and #fields == 6 and (fields[5] == "primary" or fields[5] == "loki")
        and fields[6]:match("^/Game/Classes/Items/[%w_./%-]+_C$") and fields[6]
    if underbarrel then
        invoke = action == "fire" and SilverfishFireNativeGuestUnderbarrel or SilverfishReloadNativeGuestUnderbarrel
        inspect = SilverfishInspectNativeGuestUnderbarrel
    end
    local equipped_target = action == "reload" and guid == "equipped"
    observation = nil
    if not enabled or (#fields ~= 4 and not seed_profile and not shot_view and not underbarrel_reload and not selected_round)
        or phase ~= "hosting"
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
    local arguments = seed_profile and {fields[5]} or shot_view or selected_round and {selected_round} or {}
    if selected_round then
        local class = StaticFindObject(selected_round)
        if not class or not class:IsValid() then LoadAsset(selected_round) end
    end
    local restore, refusal
    local spratt = spratt_fixture and state.map == spratt_fixture.map
    local shot_fixture = spratt and spratt_fixture or flayer_fixture
    local shot_tag = spratt and "spratt" or "flayer"
    if action == "fire" and shot_fixture and not underbarrel then
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
        ok, status, first, second, fifth, sixth = invoke(pawn_address, save_address, guid, table.unpack(arguments))
    end
    local before, after, actor_ammo, stored_ammo, projectiles, reserve_before, reserve_after, ammo_class
    if action == "fire" then projectiles = fifth end
    if action == "reload" then before, after, reserve_before, reserve_after = first, second, fifth, sixth end
    if ok and (action == "inspect" or action == "select") then
        actor_ammo, stored_ammo = first, second
    elseif ok and action ~= "seed-ammo" then
        if action == "fire" then before, after = first, second end
        local verified, state_name, actor, stored, mounted_ammo = inspect(pawn_address, save_address, guid)
        if verified then
            state_name, actor_ammo, stored_ammo = tostring(state_name), actor, stored
            status = state_name
            if underbarrel then ammo_class = mounted_ammo
            elseif selected_round then ammo_class = mounted_ammo_path(pawn.EquipedItem) end
        else
            ok, status = false, "mutation_unverified_stock_readback"
        end
    end
    if ok and action == "reload" and not underbarrel then
        pcall(require("native_weapon_fire_view").reloaded, pawn, before)
    end
    if not ok and action == "fire" and status == "guest_weapon_preloaded_magazine_required"
        and first == 0 and second == 0 and projectiles == 0 then
        pcall(require("native_weapon_fire_view").dry_fired, pawn)
    end
    observation = { map = state.map, action = action, pawn = name, guid = guid,
        ok = ok == true, status = status, before = before, after = after,
        actor_ammo = actor_ammo, stored_ammo = stored_ammo, projectiles = projectiles,
        reserve_before = reserve_before, reserve_after = reserve_after, ammo_class = ammo_class }
    return "native guest weapon operation reported", pawn, detached
end

local function weapon_result_row(row, status_row)
    return status_row("native-guest-weapon", row.map, row.action, row.pawn, row.guid, row.ok and 1 or 0,
        row.status, row.before or "-", row.after or "-", row.actor_ammo or "-", row.stored_ammo or "-",
        row.projectiles or "-", row.reserve_before or "-", row.reserve_after or "-", row.ammo_class or "-")
end

local function equipped_row(state, name, pawn, save, status_row)
    local ok, guid, kind, rounds, mode, ammo_path, shell_count, shell_path, barrel_mode = pcall(function()
        if not pawn or not save then return end
        local id, profile, row = equipped_guid(pawn, save, state.world)
        local held = row and pawn.EquipedItem
        local amount = row and row[ammo_field]
        if held and weapon_profiles.paths[profile] and amount == held.Ammo and valid_rounds(held, amount) then
            local mode = weapon_profiles.held_fire[profile] and held.HasSelectFire == true
                and saved_mode(save, id, row[class_field])
            local path = weapon_profiles.ammo_options[profile] and mounted_ammo_path(held)
            local child = guest_weapon.loki_child(held, pawn, profile)
            local shell = child and mounted_ammo_path(child)
            local count = allowed_ammo("bolt-action-shotgun-loki", shell) and child.Ammo
            return id, profile, amount, mode, allowed_ammo(profile, path) and path or nil,
                count or nil, count and shell or nil, count and saved_mode(save, id, row[class_field], "barrel") or nil
        end
        return id, profile
    end)
    if not ok or not guid or not kind then return end
    local shell = shell_count and {shell_count, shell_path, barrel_mode} or {}
    return status_row("native-guest-equipped-weapon", name, guid, kind,
        rounds or "-", mode or "-", ammo_path or "-", table.unpack(shell))
end

local receipt = require("native_combat_receipts").cache("native_guest_weapon_operation_conflict")

local function observe_command(fields, state, phase, status_row)
    local previous = observation
    observation = nil
    local accepted, message, pawn, save = pcall(guest_weapon.command, fields, state, phase)
    local result = observation
    observation = previous
    if accepted and not result then accepted, message = false, "guest_weapon_readback_unavailable" end
    return {accepted = accepted, message = message,
        retryable = not accepted and message == "guest_weapon_refused_detached_save",
        report = accepted and weapon_result_row(result, status_row),
        equipped = accepted and result.ok and equipped_row(state, result.pawn, pawn, save, status_row)}
end

function guest_weapon.local_reply(fields, state, phase, sequence, status_row)
    local world = phase == "hosting" and state.listening and state.server == false and state.world
    local outcome = receipt(fields, world, function(command)
        return observe_command(command, state, phase, status_row)
    end)
    local rows = {"native-guest-weapon-reply\t1", status_row("schema", 1), status_row("seq", sequence),
        status_row("ack", fields[1], outcome.accepted and "accepted" or "error", outcome.message),
        status_row("session", "host", phase, state.map, state.clients, state.server and 1 or 0)}
    if outcome.report then rows[#rows + 1] = outcome.report end
    if outcome.equipped then rows[#rows + 1] = outcome.equipped end
    return table.concat(rows, "\n") .. "\n"
end

function guest_weapon.reset()
    observation = nil
end

function guest_weapon.rows(state, phase, status_row)
    local rows = {}
    update_mode_watch(state, phase)
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
        for _, pending in pairs(mode_pending) do
            rows[#rows + 1] = status_row("native-guest-fire-mode", state.map, local_pawn.name, pending.sequence,
                pending.guid, pending.mode)
        end
        return rows
    end
    if not enabled or phase ~= "hosting" then return rows end
    for _, detail in ipairs(state.guests) do
        local pawn, save = guest_save.status_context(state, phase, detail, true)
        if not pawn then
            local ok, current, detached = pcall(guest_save.exact_item_context, state, phase, detail.name)
            if ok then pawn, save = current, detached end
        end
        if pawn then rows[#rows + 1] = equipped_row(state, detail.name, pawn, save, status_row) end
    end
    local row = observation
    if row and row.map == state.map then rows[#rows + 1] = weapon_result_row(row, status_row) end
    return rows
end

return guest_weapon
