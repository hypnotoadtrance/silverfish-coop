-- Stock appearance and weapon fixtures for a private, two-player headless campaign.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local pose_fixture = os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_POSE") == "1"
local rifle_fixture = os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_RIFLE") == "1"
local guest_fixture = mailbox and mailbox:match("[/\\]clients[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and (pose_fixture or rifle_fixture or os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_EQUIPMENT") == "1")
local host_fixture = mailbox and mailbox:match("[/\\]campaigns[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not (guest_fixture or host_fixture) then return end

local command, reply = mailbox .. "/avatar-probe-command.tsv", mailbox .. "/avatar-probe-reply.tsv"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local mesh_root = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local projectile_field = "Base_Projectile_6_619A2F0C42DB644A02A7B4B4594BC74A"
local break_action = "/Game/Classes/Weapons/Wep_50BMP_BreakAction.Wep_50BMP_BreakAction_C"
local face_field = "Mesh_Head_60_630BB776410363C64BE2C89A3F72B1C5"
local plumage_field = "Mesh_Plumage_80_4B6C3DD94D27DEC901D6829472D5F546"
local skin_field = "Color_Skin_53_0B756DAC41D1CF33A2BB2480770F4445"
local tail_static_field = "Mesh_Tail_SM_74_4FD875FB4D8D71A94E98888810B6EA64"
local tail_skeletal_field = "Mesh_Tail_SK_77_F2B7A1A441682020D88D3AA95D84A47E"
local tail_model = "/Game/Classes/NPCs/NPC_Assets/NPC_Tails/SK_Tail_01.SK_Tail_01"
local docked_tail = "/Game/Classes/NPCs/NPC_Assets/CharacterModels/Heads_Gear/Tail_Docked01.Tail_Docked01"

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    assert(valid(object), "avatar_probe_asset_unavailable")
    return object
end

local function player_pair()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver)
        and not valid(driver.ServerConnection) and #driver.ClientConnections == 1, "avatar_probe_world_unavailable")
    local host, guest
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and pawn:GetFullName():match("^Player_FP_C /Game/") and same(pawn:GetWorld(), world)
            and pawn:HasAuthority() and not pawn:IsActorBeingDestroyed() then
            if pawn:IsLocallyControlled() then
                assert(not host, "avatar_probe_duplicate_host")
                host = pawn
            else
                assert(not guest, "avatar_probe_duplicate_guest")
                guest = pawn
            end
        end
    end
    assert(host and guest and valid(guest.Save) and same(host.Save, world.OwningGameInstance.SaveSlot_Ref)
        and not same(host.Save, guest.Save), "avatar_probe_detached_guest_unavailable")
    return host, guest
end

local function style(pawn, face, plumage, tint)
    local selection = pawn.Save.Character
    selection[face_field] = asset(mesh_root .. face .. "." .. face)
    selection[plumage_field] = asset(mesh_root .. plumage .. "." .. plumage)
    selection[skin_field] = tint
    local jacket = asset("/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C")
    local out = {}
    asset(inventory):MakeDefaultItem(jacket, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], jacket), "avatar_probe_hunter_jacket_factory_unavailable")
    pawn.Save.PlayerArmor = row
end

local function equip(pawn, blueprint, rounds)
    local save = pawn.Save
    local slots = save.PlayerEquipment
    assert(slots:GetArrayNum() >= 2 and not valid(slots[1][item_class]) and not valid(slots[2][item_class]),
        "avatar_probe_empty_equipment_slots_unavailable")
    local expected = asset("/Game/Classes/Weapons/" .. blueprint .. "." .. blueprint:match("([^/]+)$") .. "_C")
    local out = {}
    asset(inventory):MakeDefaultItem(expected, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], expected), "avatar_probe_item_factory_unavailable")
    if rounds then row[item_ammo] = rounds end
    local result = {}
    pawn:GetWorld().OwningGameInstance:EquipItemToLoadout(row, result)
    assert(result.EquippedSuccessfully == true or result[1] == true, "avatar_probe_stock_loadout_refused")
    local equipment = pawn.EquipedItem
    assert(valid(equipment) and same(equipment:GetClass(), expected) and same(equipment:GetOwner(), pawn),
        "avatar_probe_stock_equip_unavailable")
end

local function rig(pawn)
    local body = pawn.Mesh
    assert(valid(body) and valid(body.SkinnedAsset), "avatar_probe_rig_pending")
    local head_bone = FName("Head", EFindName.FNAME_Find)
    local helmet_socket = FName("HelmetSocket", EFindName.FNAME_Find)
    local pivot, tail, head
    local components = pawn:K2_GetComponentsByClass(asset("/Script/Engine.SceneComponent"))
    local function unwrap(component)
        if type(component) == "userdata" and type(component.type) == "function" and component:type() == "RemoteUnrealParam" then
            return component:get()
        end
        return component
    end
    for index = 1, #components do
        local component = unwrap(components[index])
        if valid(component) and same(component:GetAttachParent(), body) then
            local name = component:GetAttachSocketName():ToString()
            if name == "Head" or name == "HeadSocket" then pivot = component end
            if name == "TailSocket_01" then tail = component end
        end
    end
    assert(valid(pivot) and valid(tail), "avatar_probe_rig_pending")
    local meshes = pawn:K2_GetComponentsByClass(asset("/Script/Engine.StaticMeshComponent"))
    for index = 1, #meshes do
        local component = unwrap(meshes[index])
        if valid(component) and same(component:GetAttachParent(), pivot) then head = component end
    end
    assert(valid(head) and valid(head.StaticMesh), "avatar_probe_rig_pending")
    local function distance(a, b)
        return math.sqrt((a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2)
    end
    local base, location = pawn:GetBaseTranslationOffset(), body.RelativeLocation
    local tail_at, tail_rot = tail.RelativeLocation, tail.RelativeRotation
    local neck = body:GetSocketLocation(head_bone)
    local helmet = body:GetSocketTransform(helmet_socket, 0)
    local math_library = asset("/Script/Engine.Default__KismetMathLibrary")
    local expected = math_library:InverseTransformLocation(helmet, neck)
    local actual = head.RelativeLocation
    local expected_location = { X = -expected.X, Y = -expected.Y, Z = -expected.Z }
    local aim = head:K2_GetComponentRotation()
    return string.format(
        "%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%d",
        math.sqrt(base.X^2 + base.Y^2), math.sqrt(location.X^2 + location.Y^2),
        distance(pivot:K2_GetComponentLocation(), neck), distance(actual, expected_location), aim.Roll, aim.Yaw,
        tail_at.X, tail_at.Z, tail_rot.Yaw, pawn:K2_GetActorRotation().Yaw, pawn.CharacterMovement:IsFalling() and 1 or 0)
end

local clips = {"HandGun_Idle", "HandGun_Idle_2", "HandGun_Idle_3", "Rifle_Aiming_Idle"}

local function sample_wrists(body)
    local points = {}
    for _, name in ipairs({"Weapon_Socket01", "RightHand", "LeftHand"}) do
        local socket = FName(name, EFindName.FNAME_Find)
        assert(body:DoesSocketExist(socket), "avatar_probe_wrist_unavailable:" .. name)
        local at = body:GetSocketTransform(socket, 2).Translation
        points[#points + 1] = string.format("%.4f,%.4f,%.4f", at.X, at.Y, at.Z)
    end
    return table.concat(points, "\t")
end

local function rifle_profile(weapon)
    local defaults = weapon:GetClass():GetCDO()
    local projectile = weapon.AmmoType[projectile_field]
    assert(valid(projectile), "avatar_probe_rifle_projectile_unavailable")
    return table.concat({defaults.MagSize, defaults.Caliber, defaults.FireRate, defaults.BurstLength,
        tostring(defaults.Action_OnRelease), tostring(defaults.SingleReload), projectile:GetFullName()}, "\t")
end

local function run(operation)
    if guest_fixture then
        local clip_index, clip_sample = operation:match("^clip([1-4])([01])$")
        local rig_operation = pose_fixture and (operation == "rig" or operation == "wrists" or clip_index)
        assert(operation == "aim" or operation == "lower" or rig_operation
            or pose_fixture and operation == "jump"
            or rifle_fixture and (operation == "selectrifle" or operation == "riflerange"),
            "avatar_probe_unknown_guest_operation")
        local viewport = FindFirstOf("GameViewportClient")
        local world = valid(viewport) and viewport.World
        local driver = valid(world) and world.NetDriver
        assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver)
            and valid(driver.ServerConnection), "avatar_probe_guest_connection_unavailable")
        local local_pawn, remote_pawn
        for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
            if valid(candidate) and same(candidate:GetWorld(), world) and not candidate:HasAuthority()
                and not candidate:IsActorBeingDestroyed() then
                if candidate:IsLocallyControlled() then
                    assert(not local_pawn, "avatar_probe_duplicate_local_guest")
                    local_pawn = candidate
                elseif rig_operation or operation == "riflerange" then
                    assert(not remote_pawn, "avatar_probe_duplicate_remote_host")
                    remote_pawn = candidate
                end
            end
        end
        if rig_operation then
            assert(remote_pawn, "avatar_probe_rig_pending")
            if operation == "rig" then return rig(remote_pawn) end
            if operation == "wrists" then return sample_wrists(remote_pawn.Mesh) end
            local name = clips[tonumber(clip_index)]
            local path = "/Game/Classes/NPCs/NPC_Assets/Animations/" .. name .. "." .. name
            local animation = asset(path)
            local body = remote_pawn.Mesh
            assert(same(body.SkinnedAsset.Skeleton, animation.Skeleton), "avatar_probe_clip_rig_mismatch")
            body:PlayAnimation(animation, false)
            body:SetPlayRate(0)
            body:SetPosition(tonumber(clip_sample) * .15, false)
            return
        end
        assert(local_pawn and local_pawn.MoveInputs_Enabled and not local_pawn.IsWeaponBlocked,
            "avatar_probe_guest_input_unavailable")
        if operation == "jump" then
            assert(local_pawn.CharacterMovement:IsMovingOnGround(), "avatar_probe_jump_ground_unavailable")
            local_pawn:Jump()
            return
        end
        if operation == "riflerange" then
            assert(valid(local_pawn) and valid(local_pawn.Controller) and valid(remote_pawn),
                "avatar_probe_rifle_controller_unavailable")
            local origin, teammate = local_pawn:K2_GetActorLocation(), remote_pawn:K2_GetActorLocation()
            local yaw = math.deg(math.atan(origin.Y - teammate.Y, origin.X - teammate.X))
            local_pawn.Controller:SetControlRotation({Pitch = 12, Yaw = yaw, Roll = 0})
            return
        end
        if operation == "selectrifle" then
            local rows = local_pawn.Save.PlayerEquipment
            if not rows or rows:GetArrayNum() < 1 or not same(rows[1][item_class], asset(break_action)) then
                error("avatar_probe_rig_pending", 0)
            end
            local_pawn:Equip_NextItem(0, false)
            local held = local_pawn.EquipedItem
            assert(valid(held) and same(held:GetClass(), asset(break_action)) and held.Ammo == 1,
                "avatar_probe_local_rifle_selection_unavailable")
            return rifle_profile(held)
        end
        local action = asset("/Game/Classes/Player/Player_Inputs/IA_Use_Secondary.IA_Use_Secondary")
        local input = asset("/Script/EnhancedInput.Default__EnhancedInputLibrary")
        local action_value = input:MakeInputActionValueOfType(operation == "aim" and 1 or 0, 0, 0, 0)
        if operation == "aim" then
            local_pawn:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_33(action_value, 0, 0, action)
        else
            local_pawn:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_35(action_value, .5, .5, action)
        end
        return
    end
    local host, guest = player_pair()
    if rifle_fixture and operation == "seedrifle" then
        local class = asset(break_action)
        local rows = guest.Save.PlayerEquipment
        assert(rows:GetArrayNum() >= 1 and not valid(rows[1][item_class]), "avatar_probe_rifle_slot_occupied")
        local out = {}
        asset(inventory):MakeDefaultItem(class, guest, out)
        local row = out.Item or out
        assert(same(row[item_class], class), "avatar_probe_stock_rifle_factory_unavailable")
        row[item_ammo] = 1
        guest.Save.PlayerEquipment[1] = row
        local guid = rows[1][item_guid]
        assert(same(rows[1][item_class], class) and rows[1][item_ammo] == 1, "avatar_probe_rifle_slot_readback")
        return string.format("%08X%08X%08X%08X", guid.A & 0xffffffff, guid.B & 0xffffffff,
            guid.C & 0xffffffff, guid.D & 0xffffffff)
    elseif rifle_fixture and operation == "heldrifle" then
        local held = guest.EquipedItem
        assert(valid(held) and same(held:GetClass(), asset(break_action)) and held:HasAuthority()
            and same(held.User, guest) and guest.Save.PlayerEquipIndex == 0 and held.Ammo == 1,
            "avatar_probe_authority_rifle_unavailable")
        return rifle_profile(held)
    elseif pose_fixture and operation == "style" then
        style(host, "SM_Head_UV_02_EarRing", "SM_Plumage_007", { R = .2, G = .7, B = .35, A = 1 })
        style(guest, "SM_Head_UV_Fangs_01", "SM_Plumage_012", { R = .8, G = .25, B = .33, A = 1 })
        host.Save.Character[tail_static_field] = nil
        host.Save.Character[tail_skeletal_field] = asset(tail_model)
        guest.Save.Character[tail_static_field] = asset(docked_tail)
        guest.Save.Character[tail_skeletal_field] = nil
    elseif pose_fixture and operation == "rig" then
        return rig(guest)
    elseif pose_fixture and operation == "turn" then
        local rotation = {Pitch = 15, Yaw = host:K2_GetActorRotation().Yaw + 90, Roll = 0}
        host.Controller:SetControlRotation(rotation)
        host:K2_SetActorRotation({Pitch = 0, Yaw = rotation.Yaw, Roll = 0}, false)
    elseif pose_fixture and operation == "jump" then
        assert(host.CharacterMovement:IsMovingOnGround(), "avatar_probe_jump_ground_unavailable")
        host:Jump()
    elseif operation == "equip" then
        style(host, "SM_Head_UV_02_EarRing", "SM_Plumage_007", { R = .2, G = .7, B = .35, A = 1 })
        style(guest, "SM_Head_UV_Fangs_01", "SM_Plumage_012", { R = .8, G = .25, B = .33, A = 1 })
        equip(host, "Wep_Pistol", 3)
    elseif operation == "aim" or operation == "lower" then
        local weapon = host.EquipedItem
        assert(valid(weapon) and same(weapon.User, host), "avatar_probe_stock_equip_unavailable")
        weapon:Use_Item_Secondary(operation == "aim")
        assert(host.Aiming == (operation == "aim"), "avatar_probe_stock_aim_failed")
    elseif operation == "holster" then
        local save = host.Save
        local index = save.PlayerEquipIndex + 1
        local row = save.PlayerEquipment[index]
        assert(valid(row[item_class]), "avatar_probe_stock_loadout_unavailable")
        host:GetWorld().OwningGameInstance:RemoveItemFromLoadout(row)
        assert(not valid(save.PlayerEquipment[index][item_class]), "avatar_probe_stock_holster_unavailable")
    elseif operation == "bat" then
        equip(host, "MeleeWeapons/MeleeWep_Bat")
    elseif operation == "swing" or operation == "cycle" then
        local weapon = host.EquipedItem
        local expected = operation == "swing" and "MeleeWep_Bat_C$" or "Wep_Pistol_C$"
        assert(valid(weapon) and same(weapon.User, host) and weapon:GetClass():GetFullName():match(expected),
            "avatar_probe_stock_weapon_unavailable")
        local eye, teammate = host:K2_GetActorLocation(), guest:K2_GetActorLocation()
        host.Controller:SetControlRotation({Pitch = 0, Yaw = math.deg(math.atan(eye.Y - teammate.Y, eye.X - teammate.X)), Roll = 0})
        local before = weapon.Ammo
        weapon:Use_Item_Primary(true, 0)
        weapon:Use_Item_Primary(false, 0)
        if operation == "cycle" then assert(weapon.Ammo == before - 1, "avatar_probe_stock_fire_failed") end
    else
        error("avatar_probe_unknown_operation", 0)
    end
end

local last
LoopInGameThreadWithDelay(pose_fixture and 40 or 250, function()
    local file = io.open(command, "rb")
    if not file then return false end
    local content = file:read(100) or ""
    file:close()
    local id, operation = content:match("^(%x+)\t([a-z0-9]+)\n$")
    if not id or #id ~= 32 or id == last then return false end
    last = id
    local success, issue = pcall(run, operation)
    local output = io.open(reply, "wb")
    if output then
        local result = success and (issue and "ok\t" .. issue or "ok") or tostring(issue)
        output:write(id .. "\t" .. result .. "\n")
        output:close()
    end
    return false
end)
