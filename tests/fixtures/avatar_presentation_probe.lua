-- Stock appearance and weapon fixtures for a private, two-player headless campaign.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local pose_fixture = os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_POSE") == "1"
local rifle_fixture = os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_RIFLE") == "1"
local shotgun_fixture = os.getenv("SILVERFISH_COOP_STOCK_SHOTGUN_PROBE") == "1"
local sprint_fixture = os.getenv("SILVERFISH_COOP_MOVEMENT_PROBE") == "1"
local aim_movement_fixture = os.getenv("SILVERFISH_COOP_AIM_MOVEMENT_PROBE") == "1"
local sprint_trait_fixture = os.getenv("SILVERFISH_COOP_MOVEMENT_TRAIT_PROBE") == "1"
local guest_fixture = mailbox and mailbox:match("[/\\]clients[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and (pose_fixture or rifle_fixture or sprint_fixture or os.getenv("SILVERFISH_COOP_RENDER_CAPTURE_EQUIPMENT") == "1")
local host_fixture = mailbox and mailbox:match("[/\\]campaigns[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local portable_fixture = mailbox
    and mailbox:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
local portable_guest = mailbox
    and mailbox:match("[/\\]portable%-title%-%x+[/\\]second[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
pose_fixture = pose_fixture or portable_fixture or portable_guest
guest_fixture = guest_fixture or portable_guest
local wipe_fixture = mailbox and mailbox:match("[/\\]clients[/\\]stock%-wipe%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_WIPE_AIM_PROBE") == "1"
local wipe_host = mailbox and mailbox:match("[/\\]campaigns[/\\]stock%-wipe%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
    and os.getenv("SILVERFISH_COOP_WIPE_AIM_PROBE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
    or not (guest_fixture or host_fixture or portable_fixture or wipe_fixture or wipe_host) then return end

local command, reply = mailbox .. "/avatar-probe-command.tsv", mailbox .. "/avatar-probe-reply.tsv"
local map = (portable_fixture or portable_guest or wipe_fixture or wipe_host) and "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
    or "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local mesh_root = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local projectile_field = "Base_Projectile_6_619A2F0C42DB644A02A7B4B4594BC74A"
local long_gun = shotgun_fixture and "/Game/Classes/Weapons/Wep_BoltAction_Shotgun_Suppressed.Wep_BoltAction_Shotgun_Suppressed_C"
    or "/Game/Classes/Weapons/Wep_50BMP_BreakAction.Wep_50BMP_BreakAction_C"
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

local function unwrap(component)
    if type(component) == "userdata" and type(component.type) == "function" and component:type() == "RemoteUnrealParam" then
        return component:get()
    end
    return component
end

local function display_body(pawn)
    local meshes = pawn:K2_GetComponentsByClass(asset("/Script/Engine.SkeletalMeshComponent"))
    for index = 1, #meshes do
        local body = unwrap(meshes[index])
        if valid(body) and same(body:GetAttachParent(), pawn.Mesh) and valid(body.SkinnedAsset) then return body end
    end
    error("avatar_probe_rig_pending", 0)
end

local function rig(pawn)
    local body = display_body(pawn)
    assert(valid(body) and valid(body.SkinnedAsset), "avatar_probe_rig_pending")
    local head_bone = FName("Head", EFindName.FNAME_Find)
    local helmet_socket = FName("HelmetSocket", EFindName.FNAME_Find)
    local pivot, tail, head
    local components = pawn:K2_GetComponentsByClass(asset("/Script/Engine.SceneComponent"))
    for index = 1, #components do
        local component = unwrap(components[index])
        if valid(component) and same(component:GetAttachParent(), body) then
            local name = component:GetAttachSocketName():ToString()
            if name == "Head" or name == "HeadSocket" then pivot = component end
            if name == "TailSocket_01" or name == "Hips" and component:IsA(asset("/Script/Engine.SkeletalMeshComponent")) then
                tail = component
            end
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
    local base, location = pawn:GetBaseTranslationOffset(), pawn.Mesh.RelativeLocation
    local tail_at, tail_rot = tail.RelativeLocation, tail.RelativeRotation
    local skeletal_tail = tail:IsA(asset("/Script/Engine.SkeletalMeshComponent"))
    local math_library = asset("/Script/Engine.Default__KismetMathLibrary")
    local tail_gap
    if skeletal_tail then
        assert(same(tail.SkinnedAsset, asset(tail_model))
            and body.SkinnedAsset:GetFullName():match("%.Armor_HunterJacket$"), "avatar_probe_rig_pending")
        -- Measured from the stock render mesh and Hunter Jacket's rear surface.
        local seam = math_library:TransformLocation(tail:K2_GetComponentToWorld(), {X = -44, Y = 0, Z = 82})
        local hip = body:GetSocketTransform(FName("Hips", EFindName.FNAME_Find), 0)
        local posterior = math_library:TransformLocation(hip, {X = -15, Y = 9, Z = 0})
        tail_gap = distance(seam, posterior)
        local inside = math_library:InverseTransformLocation(hip, seam)
        assert(inside.X > -15, "avatar_probe_tail_seam_outside_armor")
    else
        tail_gap = distance(tail:K2_GetComponentLocation(),
            body:GetSocketLocation(FName("TailSocket_01", EFindName.FNAME_Find)))
    end
    local neck = body:GetSocketLocation(head_bone)
    local helmet = body:GetSocketTransform(helmet_socket, 0)
    local expected = math_library:InverseTransformLocation(helmet, neck)
    local actual = head.RelativeLocation
    local expected_location = { X = -expected.X, Y = -expected.Y, Z = -expected.Z }
    local aim = head:K2_GetComponentRotation()
    local stock = pawn:GetBaseAimRotation()
    local native_pitch, native_yaw
    local difference = function(value, expected) return math.abs((value - expected + 180) % 360 - 180) end
    if type(SilverfishReadNativeRemoteAim) == "function" then
        local world, actor = pawn:GetWorld():GetAddress(), pawn:GetAddress()
        native_pitch, native_yaw = SilverfishReadNativeRemoteAim(world, actor)
        assert(SilverfishReadNativeRemoteAim(world + 1, actor) == nil
            and SilverfishReadNativeRemoteAim(world, actor + 1) == nil, "remote aim accepted a different scope")
        if native_pitch then
            assert(difference(native_pitch, stock.Pitch) < 0.001 and difference(native_yaw, stock.Yaw) < 0.001,
                "native aim disagreed with engine readback")
        end
    end
    local head_aim = pivot:K2_GetComponentRotation()
    local wanted_pitch = math.max(-45, math.min(45, (stock.Pitch + 180) % 360 - 180))
    return string.format(
        "%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%.5f	%d	%d	%.5f	%d	%.5f	%.5f	%.5f",
        math.sqrt(base.X^2 + base.Y^2), math.sqrt(location.X^2 + location.Y^2),
        distance(pivot:K2_GetComponentLocation(), neck), distance(actual, expected_location), aim.Roll, aim.Yaw,
        tail_at.X, tail_at.Z, tail_rot.Yaw, pawn:K2_GetActorRotation().Yaw, pawn.CharacterMovement:IsFalling() and 1 or 0,
        skeletal_tail and 1 or 0, tail_gap, native_pitch and 1 or 0, wanted_pitch,
        difference(head_aim.Pitch, wanted_pitch), difference(head_aim.Yaw, stock.Yaw))
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

local function visible_weapon_copies(pawn)
    local held = pawn.EquipedItem
    assert(valid(held) and same(held:GetClass(), asset(long_gun)) and same(held:GetAttachParentActor(), pawn),
        "avatar_probe_stock_weapon_attachment_unavailable")
    local frame = held.Mesh_Frame
    assert(valid(frame) and valid(frame.StaticMesh), "avatar_probe_stock_weapon_frame_unavailable")
    local meshes = pawn:K2_GetComponentsByClass(asset("/Script/Engine.StaticMeshComponent"))
    local visible = 0
    for index = 1, #meshes do
        local mesh = unwrap(meshes[index])
        if valid(mesh) and same(mesh.StaticMesh, frame.StaticMesh) and mesh:IsVisible()
            and not mesh.bHiddenInGame and not mesh.bOnlyOwnerSee then
            visible = visible + 1
        end
    end
    local stock_visible = frame:IsVisible() and not frame.bHiddenInGame and not frame.bOnlyOwnerSee
        and not held.bHidden
    return table.concat({visible, stock_visible and 1 or 0, frame.bOnlyOwnerSee and 1 or 0}, "\t")
end

local trace_color = {R = 0, G = 0, B = 0, A = 0}

local function prepare_lane(pawn, y)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local x = 4600
    local hit = {}
    local found = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = 500}, {X = x, Y = y, Z = -450}, 0,
        false, {pawn}, 0, hit, true, trace_color, trace_color, 0)
    assert(found, "avatar_probe_lane_floor_unavailable")
    local floor = (hit.OutHit or hit).ImpactPoint
    local point = {X = x, Y = y, Z = floor.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() + 5}
    assert(pawn:K2_SetActorLocation(point, false, {}, true), "avatar_probe_lane_position_unavailable")
end

local bandage_sound = "SoundCue /Game/Assets/Sounds/CUEs/Equipment_Cues/Bandage_01_Cue.Bandage_01_Cue"
local bandage_audio = {}
local bandage_start
local bandage_ranges = {soundnear = 420, soundrange = 2200}

local function stage_bandage_listener(host, guest, radius)
    local trace = asset("/Script/Engine.Default__KismetSystemLibrary")
    local origin = guest:K2_GetActorLocation()
    local half_height = host.CapsuleComponent:GetScaledCapsuleHalfHeight()
    if not bandage_start or not same(bandage_start.pawn, host) then
        bandage_start = {pawn = host, position = host:K2_GetActorLocation()}
    end
    for yaw = 0, 315, 45 do
        local angle = math.rad(yaw)
        local x, y = origin.X + radius * math.cos(angle), origin.Y + radius * math.sin(angle)
        local hit = {}
        local found = trace:LineTraceSingle(host, {X = x, Y = y, Z = origin.Z + 80},
            {X = x, Y = y, Z = origin.Z - half_height - 140}, 0, false, {host, guest}, 0,
            hit, true, trace_color, trace_color, 0)
        if found then
            local floor = (hit.OutHit or hit).ImpactPoint
            local point = {X = x, Y = y, Z = floor.Z + half_height + 2}
            assert(host:K2_SetActorLocation(point, false, {}, true), "avatar_probe_bandage_position_unavailable")
            host:ForceNetUpdate()
            return "positioned"
        end
    end
    error("avatar_probe_bandage_floor_unavailable", 0)
end

if portable_guest then
    RegisterHook("/Script/Engine.GameplayStatics:PlaySoundAtLocation",
        function(_, source, sound, location, _, volume, pitch, start, attenuation)
            local pawn, cue = source:get(), sound:get()
            if not valid(pawn) or not valid(cue) or cue:GetFullName() ~= bandage_sound
                or pawn:GetClass():GetFName():ToString() ~= "Player_FP_C" or not pawn:IsLocallyControlled()
                or pawn:GetWorld():GetFullName() ~= "World " .. map then return end
            local ear, point = pawn:K2_GetActorLocation(), location:get()
            local distance = math.sqrt((ear.X-point.X)^2 + (ear.Y-point.Y)^2 + (ear.Z-point.Z)^2)
            bandage_audio[#bandage_audio + 1] = ("%s,%.3f,%.3f,%.3f,%.3f,%s,%.2f,%.2f,%.2f"):format(
                bandage_sound, distance, point.X, point.Y, point.Z, tostring(valid(attenuation:get())),
                volume:get(), pitch:get(), start:get())
        end)
end

local function face_open_lane(pawn, teammate, strafe)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local point = pawn:K2_GetActorLocation()
    local capsule = pawn.CapsuleComponent
    local radius, height = capsule:GetScaledCapsuleRadius(), capsule:GetScaledCapsuleHalfHeight()
    local start = {X = point.X, Y = point.Y, Z = point.Z + 12}
    for _, length in ipairs({2900, 1800, 900, 500}) do
        for yaw = 0, 345, 15 do
            local radians = math.rad(yaw)
            local target = {X = point.X + length * math.cos(radians), Y = point.Y + length * math.sin(radians), Z = start.Z}
            local blocked = trace:CapsuleTraceSingle(pawn, start, target, radius, height - 10, 0, false,
                {pawn, teammate}, 0, {}, true, trace_color, trace_color, 0)
            local top = {X = target.X, Y = target.Y, Z = start.Z + 150}
            local floor = {X = target.X, Y = target.Y, Z = start.Z - height - 180}
            if not blocked and trace:LineTraceSingle(pawn, top, floor, 0, false,
                {pawn, teammate}, 0, {}, true, trace_color, trace_color, 0) then
                pawn.Controller:SetControlRotation({Pitch = 0, Yaw = strafe and yaw - 90 or yaw, Roll = 0})
                return math.cos(radians), math.sin(radians)
            end
        end
    end
    error("avatar_probe_open_lane_unavailable", 0)
end

local function movement_row(pawn)
    local movement = pawn.CharacterMovement
    local point = pawn:K2_GetActorLocation()
    local mesh = pawn.Mesh:K2_GetComponentLocation()
    local velocity = pawn:GetVelocity()
    return table.concat({pawn.Sprinting and 1 or 0, movement.MaxWalkSpeed, pawn.Speed_Walking,
        pawn.Speed_Sprinting, pawn.MovementSpeedMod, valid(pawn.Save) and pawn.Save.PlayerFood or -1,
        math.sqrt(velocity.X^2 + velocity.Y^2), point.X, point.Y, point.Z, mesh.X, mesh.Y}, "\t")
end

local movement_operations = {localmovement = true, remotemovement = true, preparemovement = true,
    walk = true, run = true, stopmovement = true, resetlane = true, faceteammate = true, readpose = true}
local aim_movement_operations = {localstance = true, remotestance = true, aimwalk = true, aimstrafe = true,
    crouch = true, stand = true}

local function stance_row(pawn, observed)
    local point = pawn:K2_GetActorLocation()
    local rotation = observed and pawn:GetBaseAimRotation() or pawn.Controller:GetControlRotation()
    local body = observed and display_body(pawn)
    local instance = body and body:GetAnimInstance()
    local animation = valid(instance) and instance.CurrentAsset
    local clip, position, socket_height = "-", 0, 0
    if valid(animation) then
        local socket = FName("Weapon_Socket01", EFindName.FNAME_Find)
        assert(body:DoesSocketExist(socket), "avatar_probe_aim_socket_unavailable")
        clip, position = animation:GetFName():ToString(), body:GetPosition()
        socket_height = body:GetSocketTransform(socket, 2).Translation.Z
    end
    return table.concat({pawn.bIsCrouched and 1 or 0, pawn.CharacterMovement:IsMovingOnGround() and 1 or 0,
        point.X, point.Y, rotation.Yaw, clip, position, socket_height}, "\t")
end

local function movement_probe(operation, local_pawn, remote_pawn)
    assert(valid(local_pawn) and valid(remote_pawn), "avatar_probe_movement_pawns_unavailable")
    if operation == "localmovement" then return movement_row(local_pawn) end
    if operation == "remotemovement" then return movement_row(remote_pawn) end
    if operation == "localstance" then return stance_row(local_pawn, false) end
    if operation == "remotestance" then return stance_row(remote_pawn, true) end
    if operation == "readpose" then
        local body = display_body(remote_pawn)
        local instance = body:GetAnimInstance()
        local animation = valid(instance) and instance.CurrentAsset
        local mesh = body.SkinnedAsset
        local socket = FName("Weapon_Socket01", EFindName.FNAME_Find)
        local hand = body:GetSocketTransform(socket, 2).Translation
        return table.concat({valid(animation) and animation:GetFullName() or "-", body:GetPosition(),
            tostring(body:IsPlaying()), body.VisibilityBasedAnimTickOption, valid(mesh) and mesh:GetFullName() or "-",
            socket:ToString(), tostring(body:DoesSocketExist(socket)), hand.Z,
            body:GetBoneIndex(FName("RightHand", EFindName.FNAME_Find)), body:GetAnimationMode(), body.AnimationMode,
            valid(instance) and instance:GetClass():GetFullName() or "-", body:GetFullName()}, "\t")
    end
    if operation == "preparemovement" then
        for _, pawn in ipairs({local_pawn, remote_pawn}) do
            if valid(pawn.Save) and (pawn:HasAuthority() or same(pawn, local_pawn)) then
                pawn.Save.PlayerFood = 100
            end
        end
        if local_pawn:HasAuthority() then
            prepare_lane(local_pawn, 16060)
            prepare_lane(remote_pawn, 16240)
        end
        if sprint_trait_fixture then
            local pawn = local_pawn:HasAuthority() and remote_pawn or local_pawn
            local trait = FName("SpeedBoost", EFindName.FNAME_Find)
            local key = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
            local character = pawn.Save.Character
            character[key] = {trait}
            pawn.Save.Character = character
            local passives = pawn.Save.Character[key]
            assert(passives:GetArrayNum() == 1 and passives[1]:ToString() == "SpeedBoost",
                "avatar_probe_movement_trait_readback_failed")
            if not local_pawn:HasAuthority() then
                local instance = pawn:GetWorld().OwningGameInstance
                instance.PassiveEffects = {trait}
            end
            pawn:ApplyDynamicStats()
        end
    elseif operation == "resetlane" then
        assert(local_pawn:HasAuthority())
        prepare_lane(local_pawn, 16060)
        prepare_lane(remote_pawn, 16240)
    elseif operation == "walk" or operation == "run" then
        assert(local_pawn.CharacterMovement:IsMovingOnGround() and not local_pawn.bIsCrouched
            and local_pawn.MoveInputs_Enabled, "avatar_probe_walk_ground_unavailable")
        local x, y = face_open_lane(local_pawn, remote_pawn)
        local_pawn.MovementInput = {X = 0, Y = 1}
        local_pawn:Action_Sprint(operation == "run")
        return string.format("%.5f\t%.5f", x, y)
    elseif operation == "aimwalk" or operation == "aimstrafe" then
        assert(local_pawn.CharacterMovement:IsMovingOnGround() and local_pawn.MoveInputs_Enabled
            and not local_pawn.Sprinting, "avatar_probe_aim_movement_unavailable")
        local strafe = operation == "aimstrafe"
        local x, y = face_open_lane(local_pawn, remote_pawn, strafe)
        local_pawn.MovementInput = strafe and {X = 1, Y = 0} or {X = 0, Y = 1}
        return string.format("%.5f\t%.5f", x, y)
    elseif operation == "crouch" then
        assert(local_pawn.CharacterMovement:IsMovingOnGround() and local_pawn.MoveInputs_Enabled,
            "avatar_probe_aim_crouch_unavailable")
        local_pawn:Crouch(false)
    elseif operation == "stand" then
        local_pawn:UnCrouch(false)
    elseif operation == "stopmovement" then
        local_pawn.MovementInput = {X = 0, Y = 0}
        local_pawn:Action_Sprint(false)
    elseif operation == "faceteammate" then
        local look = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        local target = remote_pawn:K2_GetActorLocation()
        local_pawn.Controller:SetControlRotation(look:FindLookAtRotation(local_pawn.Camera:K2_GetComponentLocation(), target))
    else
        error("avatar_probe_unknown_movement_operation", 0)
    end
end

local function held_equipment_row(pawn)
    local slots, index = pawn.Save.PlayerEquipment, pawn.Save.PlayerEquipIndex
    local selected = index >= 0 and index < slots:GetArrayNum() and slots[index + 1][item_class]
    local held = pawn.EquipedItem
    return table.concat({valid(held) and not held:IsActorBeingDestroyed() and held:GetClass():GetFullName() or "-",
        valid(selected) and selected:GetFullName() or "-"}, "\t")
end

local optic_profiles = {
    binoculars = {item = "/Game/Classes/Equipment/Eqp_Binoculars.Eqp_Binoculars_C", slot = 5,
        mesh = "StaticMesh /Game/Classes/Equipment/Equipment_Assets/Equipment_Meshs/SM_Item_Binoculars_01.SM_Item_Binoculars_01"},
    viewfinder = {item = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C", slot = 6,
        mesh = "StaticMesh /Game/Classes/Equipment/Equipment_Assets/Equipment_Meshs/SM_Camera_01.SM_Camera_01"},
}
local optic_cues = {in_count = 0, out_count = 0}
local shutter_cues = 0
if portable_fixture or portable_guest then
    RegisterHook("/Script/Engine.GameplayStatics:PlaySoundAtLocation", function(_, source, sound)
        local pawn, cue = source:get(), sound:get()
        if not valid(pawn) or not valid(cue) or pawn:GetClass():GetFName():ToString() ~= "Player_FP_C"
            or pawn:IsLocallyControlled() then return end
        local name = cue:GetFName():ToString()
        if name == "Scope_In_Cue" then optic_cues.in_count = optic_cues.in_count + 1 end
        if name == "Scope_Out_Cue" then optic_cues.out_count = optic_cues.out_count + 1 end
        if name == "Cam_TakePhoto_Cue" then shutter_cues = shutter_cues + 1 end
    end)
end

local function optic_operation(operation, profile)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. map, "avatar_probe_world_unavailable")
    local local_pawn, remote_pawn
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and same(pawn:GetWorld(), world) and not pawn:IsActorBeingDestroyed() then
            if pawn:IsLocallyControlled() then local_pawn = pawn
            elseif pawn:GetLocalRole() ~= 0 then remote_pawn = pawn end
        end
    end
    assert(valid(local_pawn) and valid(remote_pawn), "avatar_probe_rig_pending")
    local item = asset(profile.item)
    local index = profile.slot
    if operation == "seed" then
        local host, guest = player_pair()
        for _, pawn in ipairs({host, guest}) do
            local rows = pawn.Save.PlayerEquipment
            assert(rows:GetArrayNum() >= index and not valid(rows[index][item_class]), "optic fixture slot occupied")
            local out = {}
            asset(inventory):MakeDefaultItem(item, pawn, out)
            local row = out.Item or out
            assert(same(row[item_class], item), "stock optic could not be created")
            rows[index] = row
        end
        return "seeded"
    end
    if operation == "select" then
        local rows = local_pawn.Save.PlayerEquipment
        if not rows or rows:GetArrayNum() < index or not same(rows[index][item_class], item) then
            error("avatar_probe_rig_pending", 0)
        end
        local_pawn:Equip_NextItem(index - 1, false)
        local equipped = local_pawn.EquipedItem
        assert(valid(equipped) and same(equipped:GetClass(), item) and same(equipped.User, local_pawn),
            "stock optic did not equip")
        return "selected"
    elseif operation == "raise" or operation == "lower" then
        local held = local_pawn.EquipedItem
        assert(valid(held) and same(held:GetClass(), item) and same(held.User, local_pawn), "stock optic not held")
        held:Use_Item_Secondary(operation == "raise")
        return "input"
    elseif operation == "shutter" and profile == optic_profiles.viewfinder then
        local held = local_pawn.EquipedItem
        assert(valid(held) and same(held:GetClass(), item) and same(held.User, local_pawn), "stock camera not held")
        held:Use_Item_Primary(true, 1)
        held:Use_Item_Primary(false, 1)
        return "input"
    end

    local meshes = remote_pawn:K2_GetComponentsByClass(asset("/Script/Engine.StaticMeshComponent"))
    local optic, count
    count = 0
    for i = 1, #meshes do
        local part = unwrap(meshes[i])
        if valid(part) and same(part:GetOwner(), remote_pawn) and valid(part.StaticMesh)
            and part.StaticMesh:GetFullName() == profile.mesh and part:IsVisible() and not part.bHiddenInGame then
            optic, count = part, count + 1
        end
    end
    if count == 0 then return "pending" end
    assert(count == 1 and valid(optic:GetAttachParent()), "the observer saw duplicate optics")
    local raised = optic:GetAttachSocketName():ToString() == "None"
    local at = optic.RelativeLocation
    local distance = math.sqrt(at.X^2 + at.Y^2 + at.Z^2)
    return table.concat({raised and "raised" or "lowered", optic:GetFullName(), optic:GetAttachSocketName():ToString(),
        ("%.3f"):format(distance), optic_cues.in_count, optic_cues.out_count}, "\t")
end

local function run(operation)
    if (portable_fixture or portable_guest) and operation == "shuttercount" then return tostring(shutter_cues) end
    if portable_guest and operation == "soundclear" then
        bandage_audio = {}
        return "cleared"
    end
    if portable_guest and operation == "soundread" then return table.concat(bandage_audio, "|") end
    if portable_fixture or portable_guest then
        for name, profile in pairs(optic_profiles) do
            if operation:sub(-#name) == name then
                return optic_operation(operation:sub(1, -#name - 1), profile)
            end
        end
    end
    if wipe_host then
        assert(operation == "heldstate", "avatar_probe_unknown_wipe_operation")
        local _, guest = player_pair()
        return held_equipment_row(guest)
    end
    if wipe_fixture then
        local viewport = FindFirstOf("GameViewportClient")
        local world = valid(viewport) and viewport.World
        local driver = valid(world) and world.NetDriver
        assert(valid(driver) and valid(driver.ServerConnection)
            and world:GetFullName() == "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P",
            "avatar_probe_wipe_connection_unavailable")
        local pawn
        for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
            if valid(candidate) and same(candidate:GetWorld(), world) and candidate:IsLocallyControlled()
                and not candidate:HasAuthority() and not candidate:IsActorBeingDestroyed() then
                assert(not pawn, "avatar_probe_duplicate_local_guest")
                pawn = candidate
            end
        end
        assert(pawn, "avatar_probe_rig_pending")
        if operation == "heldstate" then return held_equipment_row(pawn) end
        if operation == "aim" then
            assert(not pawn.IsWeaponBlocked and valid(pawn.Save), "avatar_probe_wipe_aim_unavailable")
            local held = pawn.EquipedItem
            if not valid(held) then
                local slots = pawn.Save.PlayerEquipment
                assert(slots:GetArrayNum() > 1 and valid(slots[2][item_class]), "avatar_probe_rig_pending")
                pawn:Equip_NextItem(1, false)
                held = pawn.EquipedItem
            end
            assert(valid(held) and same(held.User, pawn), "avatar_probe_rig_pending")
            held:Use_Item_Secondary(true)
            assert(pawn.Aiming, "avatar_probe_stock_aim_failed")
        else
            assert(operation == "aimstate", "avatar_probe_unknown_wipe_operation")
        end
        return pawn.Aiming and "aiming" or "lowered"
    end
    assert(not portable_fixture or operation == "consume" or operation == "rig" or operation == "style"
        or operation == "turn" or operation == "jump" or bandage_ranges[operation]
        or operation == "soundrestore" or operation == "soundready", "avatar_probe_unknown_portable_operation")
    local movement_operation = sprint_fixture and movement_operations[operation]
        or aim_movement_fixture and aim_movement_operations[operation]
    if guest_fixture then
        local clip_index, clip_sample = operation:match("^clip([1-4])([01])$")
        local rig_operation = pose_fixture and (operation == "rig" or operation == "wrists" or clip_index)
        assert(operation == "aim" or operation == "lower" or rig_operation
            or pose_fixture and operation == "jump" or rifle_fixture and operation == "selectrifle" or movement_operation,
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
                elseif rig_operation or movement_operation then
                    assert(not remote_pawn, "avatar_probe_duplicate_remote_host")
                    remote_pawn = candidate
                end
            end
        end
        if movement_operation then return movement_probe(operation, local_pawn, remote_pawn) end
        if rig_operation then
            assert(remote_pawn, "avatar_probe_rig_pending")
            if operation == "rig" then return rig(remote_pawn) end
            if operation == "wrists" then return sample_wrists(display_body(remote_pawn)) end
            local name = clips[tonumber(clip_index)]
            local path = "/Game/Classes/NPCs/NPC_Assets/Animations/" .. name .. "." .. name
            local animation = asset(path)
            local body = display_body(remote_pawn)
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
        if operation == "selectrifle" then
            local rows = local_pawn.Save.PlayerEquipment
            if not rows or rows:GetArrayNum() < 1 or not same(rows[1][item_class], asset(long_gun)) then
                error("avatar_probe_rig_pending", 0)
            end
            local_pawn:Equip_NextItem(0, false)
            local held = local_pawn.EquipedItem
            assert(valid(held) and same(held:GetClass(), asset(long_gun)) and held.Ammo == 1,
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
    if portable_fixture and bandage_ranges[operation] then
        return stage_bandage_listener(host, guest, bandage_ranges[operation])
    end
    if portable_fixture and operation == "soundready" then return tostring(host.UsingItem == false) end
    if portable_fixture and operation == "soundrestore" then
        if bandage_start and same(bandage_start.pawn, host) then
            assert(host:K2_SetActorLocation(bandage_start.position, false, {}, true), "avatar_probe_bandage_restore_failed")
            host:ForceNetUpdate()
            bandage_start = nil
        end
        return "restored"
    end
    if movement_operation then return movement_probe(operation, host, guest) end
    if rifle_fixture and operation == "seedrifle" then
        local class = asset(long_gun)
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
        assert(valid(held) and same(held:GetClass(), asset(long_gun)) and held:HasAuthority()
            and same(held.User, guest) and guest.Save.PlayerEquipIndex == 0 and held.Ammo == 1,
            "avatar_probe_authority_rifle_unavailable")
        return rifle_profile(held)
    elseif shotgun_fixture and operation == "weaponcopies" then
        return visible_weapon_copies(guest)
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
    elseif operation == "consume" then
        local class = asset("/Game/Classes/Items/Item_Bandage.Item_Bandage_C")
        local statics = asset("/Script/Engine.Default__GameplayStatics")
        local transform = host.Prop_Arrow:K2_GetComponentToWorld()
        local actor = statics:BeginDeferredActorSpawnFromClass(host, class, transform, 1, host, 2)
        assert(valid(actor), "avatar_probe_bandage_spawn_failed")
        actor.Instigator, actor.Item_RespawnMode = host, 0
        actor = statics:FinishSpawningActor(actor, transform, 2)
        assert(valid(actor), "avatar_probe_stock_bandage_missing")
        actor.Player_Ref = host
        assert(actor:K2_AttachToComponent(host.Prop_Arrow, FName("None", EFindName.FNAME_Find), 2, 2, 2, true),
            "avatar_probe_bandage_attach_failed")
        actor:Use_Item_Primary(true, 0)
        assert(actor.BeingUsed == true and host.UsingItem == true and valid(actor.UseSound),
            "avatar_probe_stock_bandage_use_unverified")
        if portable_fixture then
            local state = valid(host.PlayerState) and host.PlayerState or host.Controller.PlayerState
            local ok, id = pcall(function() return state:GetPlayerId() end)
            if not ok or id == nil then id = state.PlayerId end
            assert(type(id) == "number" and math.tointeger(id) and id >= 0 and same(actor:GetOwner(), host),
                "avatar_probe_host_identity_unavailable")
            local point = actor:K2_GetActorLocation()
            return ("%d\t%s\t%.3f,%.3f,%.3f"):format(id, actor:GetClass():GetFullName(), point.X, point.Y, point.Z)
        end
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
LoopInGameThreadWithDelay(sprint_fixture and 16 or pose_fixture and 40 or 250, function()
    local file = io.open(command, "rb")
    if not file then return false end
    local content = file:read(100) or ""
    file:close()
    local id, operation = content:match("^(%x+)\t([a-z0-9]+)\r?\n$")
    if not id or #id ~= 32 or id == last then return false end
    last = id
    local success, issue = pcall(run, operation)
    local output = io.open(reply, "wb")
    if output then
        local result = success and ("ok\t" .. (issue or "")) or ("error\t" .. tostring(issue))
        output:write(id .. "\t" .. result .. "\n")
        output:close()
    end
    return false
end)
