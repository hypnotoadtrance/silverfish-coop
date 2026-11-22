local guest_save = require("guest_save")
local weapon_view = require("native_avatar_weapon_view")
local character_view = require("native_avatar_character_view")
local avatar = {}

local body_path = "/Game/Classes/NPCs/NPC_Assets/CharacterModels/Player_NoArmor.Player_NoArmor"
local head_path = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/SM_Head_UV_01.SM_Head_UV_01"
local head_class = "/Script/Engine.StaticMeshComponent"
local scene_class = "/Script/Engine.SceneComponent"
local animation_root = "/Game/Classes/NPCs/NPC_Assets/Animations/"
local crouched_idle = "Crouching_Idle"
local crouched_walk = "Crouched_Walking"
local firearm_class = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local aimed = {pistol = {"HandGun_Idle", "HandGun_Walk"}, rifle = {"Rifle_Aiming_Idle", "Rifle_Walk"}}
local melee_clips = {slash = "Standing_Melee_Attack_Horizontal", backhand = "Standing_Melee_Attack_Backhand",
    overhead = "Standing_Melee_Attack_Downward"}
local identity = {
    Rotation = { X = 0, Y = 0, Z = 0, W = 1 },
    Translation = { X = 0, Y = 0, Z = 0 },
    Scale3D = { X = 1, Y = 1, Z = 1 },
}
local avatars = {}
local swings = {}
local observations, client_outfits = {}, {}
local observed_weapons = {}
local client_world, client_map, client_receipt
local host_world, host_aim_receipt, host_aims
local holding_aim
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local armor_root = "/Game/Classes/Armor_Items/"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function player_id(details)
    local state = details.pawn.PlayerState
    if not valid(state) and valid(details.controller_object) then state = details.controller_object.PlayerState end
    if not valid(state) then return end
    local ok, value = pcall(function() return state:GetPlayerId() end)
    if not ok or value == nil then value = state.PlayerId end
    if type(value) == "number" and math.tointeger(value) and value >= 0 and value <= 2147483647 then
        return math.tointeger(value)
    end
end

local function outfit_path(path)
    local package, class = path:match("^/Game/Classes/Armor_Items/([%w_]+)%.([%w_]+)_C$")
    return #path <= 160 and (path == "-" or package ~= nil and package == class)
end

local function worn(row)
    local class = row and row[item_class]
    local path = valid(class) and class:GetFullName():match("^%S+ (%S+)$")
    return path and outfit_path(path) and path or "-"
end

local function aim_kind(equipped)
    local weapon = equipped and equipped.actor
    if not valid(weapon) then return "-" end
    local base = StaticFindObject(firearm_class)
    if not valid(base) or not weapon:IsA(base) then return "-" end
    -- The stock player animation graph identifies handguns by ItemType 3.
    return weapon.ItemType == 3 and "pistol" or "rifle"
end

local function firearm_path(path)
    local package, class = path:match("^(/Game/Classes/Weapons/[%w_/%-]+)%.([%w_%-]+)_C$")
    return path == "-" or #path <= 160 and package and package:match("([%w_%-]+)$") == class
end

local function sync_local_aim(state, phase)
    local pawn = phase == "connected" and state.local_pawn and state.local_pawn.pawn or nil
    holding_aim = nil
    if valid(pawn) and type(SilverfishReadNativeGuestAim) == "function" then
        holding_aim = SilverfishReadNativeGuestAim(pawn:GetAddress()) == true
    end
end

local function observe(state, phase)
    observations = {}
    if phase ~= "hosting" then
        observed_weapons = {}
        return
    end
    local known, weapons = {}, {}
    for _, details in ipairs(state.pawns) do
        local ok, id = pcall(player_id, details)
        if ok and id ~= nil then
            if known[id] then
                observations[id] = nil
            else
                known[id] = true
                local read, body, head, pawn, save = pcall(function()
                    local pawn, save = details.pawn
                    if pawn:IsLocallyControlled() then
                        save = state.world.OwningGameInstance.SaveSlot_Ref
                        if not pawn:HasAuthority() or not valid(save) or not valid(pawn.Save)
                            or pawn.Save:GetAddress() ~= save:GetAddress() then return end
                    else
                        pawn, save = guest_save.exact_item_context(state, phase, details.name)
                    end
                    if valid(pawn) and valid(save) then return worn(save.PlayerArmor), worn(save.PlayerHelmet), pawn, save end
                end)
                if read and body then
                    local address = pawn:GetAddress()
                    local read_weapon, weapon, cached = pcall(weapon_view.observe, pawn, save, observed_weapons[address])
                    local read_appearance, appearance = pcall(character_view.observe, save.Character)
                    if read_weapon then weapons[address] = cached end
                    local gun = aim_kind(read_weapon and cached)
                    local firearm = gun ~= "-" and cached.actor:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
                    if not firearm or not firearm_path(firearm) then firearm = "-" end
                    observations[id] = { pawn = details.name, body = body, head = head, weapon = read_weapon and weapon or "-",
                        appearance = read_appearance and appearance or "-", gun = gun, aim = pawn.Aiming and gun or "-",
                        firearm = firearm }
                end
            end
        end
    end
    observed_weapons = weapons
end

local function attach_component(entry, field, parent, socket_name, class)
    local socket = FName(socket_name or "None", EFindName.FNAME_Find)
    local component = entry.pawn:AddComponentByClass(StaticFindObject(class), true, identity, true)
    if not valid(component) then error("Remote avatar component could not be created") end
    if class ~= scene_class then
        component:SetCollisionEnabled(0)
        component:SetGenerateOverlapEvents(false)
    end
    component:SetMobility(2)
    entry.pawn:FinishAddComponent(component, true, identity)
    if not component:K2_AttachToComponent(parent, socket, 2, 2, 0, false) then
        component:K2_DestroyComponent(entry.pawn)
        error("Remote avatar component could not attach to its stock socket")
    end
    entry[field] = component
    return component
end

local function visual_component(entry, field, parent, socket_name, skeletal)
    local class = skeletal and "/Script/Engine.SkeletalMeshComponent" or head_class
    return attach_component(entry, field, parent, socket_name, class)
end

local function position_head(entry)
    -- The stock head meshes have an offset origin. Preserve the stock mesh offset
    -- from HeadSocket to HelmetSocket so aim rotates the face around the neck.
    local head_socket = FName("HeadSocket", EFindName.FNAME_Find)
    local helmet_socket = FName("HelmetSocket", EFindName.FNAME_Find)
    if not entry.body:DoesSocketExist(head_socket) or not entry.body:DoesSocketExist(helmet_socket) then
        error("Remote avatar head sockets were unavailable")
    end
    local math_library = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    local anchor = entry.body:GetSocketTransform(head_socket, 0)
    local origin = entry.body:GetSocketLocation(helmet_socket)
    local offset = math_library:InverseTransformLocation(anchor, origin)
    entry.head:K2_SetRelativeLocation(offset, false, {}, true)
end

local function restore_locomotion(entry)
    if entry.pose_animation and valid(entry.body) then
        entry.body:SetAnimationMode(entry.animation_mode, true)
        entry.pose_animation, entry.pose_mesh = nil, nil
    end
end

local function apply_locomotion(entry, aim)
    if swings[entry] then return end
    local crouched = entry.pawn.bIsCrouched == true
    local profile = aimed[aim]
    if not crouched and not profile then
        restore_locomotion(entry)
        return
    end
    local velocity = entry.pawn:GetVelocity()
    local moving = velocity.X^2 + velocity.Y^2 > 20^2
    local name
    if crouched then
        if profile then
            name = moving and "Walk_Crouching_Forward" or "Idle_Crouching_Aiming"
        else
            name = moving and crouched_walk or crouched_idle
        end
    else
        name = profile[moving and 2 or 1]
    end
    local mesh = entry.body.SkinnedAsset
    if not valid(mesh) then return end
    if entry.pose_animation == name and entry.pose_mesh == mesh:GetAddress() then return end
    local animation = asset(animation_root .. name .. "." .. name)
    if not valid(animation) or not valid(animation.Skeleton) or not valid(mesh.Skeleton)
        or animation.Skeleton:GetAddress() ~= mesh.Skeleton:GetAddress() then
        restore_locomotion(entry)
        return
    end

    -- The stock first-person AnimBP reads the replicated crouch flag but its
    -- pose keeps the visible remote body upright. Use the stock full-body clip.
    entry.body:PlayAnimation(animation, true)
    entry.pose_animation, entry.pose_mesh = name, mesh:GetAddress()
end

local function remove(entry)
    swings[entry] = nil
    weapon_view.clear(entry)
    character_view.clear(entry)
    if valid(entry.helmet) and valid(entry.pawn) then
        entry.helmet:K2_DestroyComponent(entry.pawn)
    end
    if valid(entry.head) and valid(entry.pawn) then
        entry.head:K2_DestroyComponent(entry.pawn)
    end
    if valid(entry.head_pivot) and valid(entry.pawn) then
        entry.head_pivot:K2_DestroyComponent(entry.pawn)
    end
    if entry.installed_body and valid(entry.body) then
        restore_locomotion(entry)
        character_view.clear_body(entry)
        if entry.material_override then entry.body:SetMaterial(0, nil) end
        entry.body:SetVisibility(entry.visible, false)
        entry.body:SetHiddenInGame(entry.hidden, false)
        entry.body:SetSkeletalMeshAsset(entry.original_asset)
        entry.body:SetCollisionEnabled(entry.collision)
        entry.body:SetGenerateOverlapEvents(entry.overlap)
    end
end

function avatar.play_melee(world, id, kind, serial, duration, start_fraction)
    local name = melee_clips[kind]
    if not name then return end
    for _, entry in pairs(avatars) do
        if entry.id == id and entry.status == "default_body" and valid(entry.pawn)
            and entry.pawn:GetWorld():GetAddress() == world:GetAddress()
            and valid(entry.body) and entry.weapon_parts and #entry.weapon_parts > 0 then
            local clip = asset(animation_root .. name .. "." .. name)
            local mesh = entry.body.SkinnedAsset
            if not valid(clip) or not valid(clip.Skeleton) or not valid(mesh) or not valid(mesh.Skeleton)
                or clip.Skeleton:GetAddress() ~= mesh.Skeleton:GetAddress() then return end
            local length = clip:GetPlayLength()
            local clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
            if length <= 0 or not valid(clock) then return end
            entry.body:SetAnimationMode(1, false)
            entry.body:SetAnimation(clip)
            entry.body:Stop()
            -- Advance the mesh pose explicitly so stock combat notifies never
            -- run on the observing player's replica.
            local first = start_fraction * length
            entry.body:SetPosition(first, false)
            entry.pose_animation, entry.pose_mesh = name, mesh:GetAddress()
            local hand = FName("Weapon_Socket01", EFindName.FNAME_Find)
            local position = entry.body:GetSocketTransform(hand, 2).Translation
            local swing = {serial = serial, clip = name, world = world:GetAddress(), start = clock:GetRealTimeSeconds(world),
                first = first, length = length, duration = duration, hand = hand,
                x = position.X, y = position.Y, z = position.Z, peak = 0}
            swings[entry], entry.last_melee = swing, swing
            return name
        end
    end
end

function avatar.animate_melee(get_world)
    if not next(swings) then return end
    local world = get_world()
    if not valid(world) then return end
    local clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(clock) then return end
    local now, scope = clock:GetRealTimeSeconds(world), world:GetAddress()
    for entry, swing in pairs(swings) do
        local mesh = valid(entry.body) and entry.body.SkinnedAsset
        if swing.world ~= scope or not valid(mesh) or mesh:GetAddress() ~= entry.pose_mesh
            or now - swing.start >= swing.duration then
            swings[entry] = nil
            restore_locomotion(entry)
        else
            local position = entry.body:GetSocketTransform(swing.hand, 2).Translation
            swing.peak = math.max(swing.peak,
                math.sqrt((position.X - swing.x)^2 + (position.Y - swing.y)^2 + (position.Z - swing.z)^2))
            local fraction = math.max(0, now - swing.start) / swing.duration
            entry.body:SetPosition(swing.first + fraction * (swing.length - swing.first), false)
        end
    end
end

function avatar.play_recoil(world, id, path, serial, values)
    for _, entry in pairs(avatars) do
        if entry.id == id and valid(entry.pawn) and entry.pawn:GetWorld():GetAddress() == world:GetAddress() then
            return weapon_view.recoil(entry, world, path, serial, values)
        end
    end
    return false
end

function avatar.animate_recoil(get_world)
    weapon_view.animate_recoil(get_world)
end

local function aim_head(entry)
    local rotation = entry.pawn:GetBaseAimRotation()
    local pitch = (rotation.Pitch + 180) % 360 - 180
    local yaw = (rotation.Yaw + 180) % 360 - 180
    pitch = math.max(-45, math.min(45, pitch))
    if entry.aim_pitch == pitch and entry.aim_yaw == yaw then return end
    local facing = {Pitch = pitch, Yaw = yaw, Roll = 0}
    entry.head_pivot:K2_SetWorldRotation(facing, false, {}, true)
    local shown = entry.head_pivot:K2_GetComponentRotation()
    entry.aim_pitch, entry.aim_yaw = pitch, yaw
    entry.aim_roll = shown.Roll
end

local function create(entry)
    local body = entry.pawn.Mesh
    if not valid(body) then error("Player body component was unavailable") end
    entry.body = body
    entry.original_asset = body.SkinnedAsset
    if valid(entry.original_asset) then
        entry.status = "stock_body"
        return
    end
    local body_asset, head_asset = asset(body_path), asset(head_path)
    local component_type = StaticFindObject(head_class)
    if not valid(body_asset) or not valid(head_asset) or not valid(component_type) then
        error("Stock avatar assets were unavailable")
    end
    entry.visible, entry.hidden = body.bVisible, body.bHiddenInGame
    entry.animation_mode = body.AnimationMode
    entry.default_asset, entry.default_head = body_asset, head_asset
    entry.collision, entry.overlap = body:GetCollisionEnabled(), body:GetGenerateOverlapEvents()
    entry.installed_body = true
    body:SetCollisionEnabled(0)
    body:SetGenerateOverlapEvents(false)
    body:SetSkeletalMeshAsset(body_asset)
    body:SetHiddenInGame(false, false)
    body:SetVisibility(true, false)

    local pivot = attach_component(entry, "head_pivot", body, "HeadSocket", scene_class)
    -- The first-person animation still drives the neck position.
    pivot:SetAbsolute(false, true, false)
    local head = visual_component(entry, "head", pivot)
    position_head(entry)
    head:SetStaticMesh(head_asset)
    if not valid(body.SkinnedAsset) or not valid(head.StaticMesh) or not body:IsVisible() then
        error("Stock avatar did not pass mesh readback")
    end
    entry.status = "default_body"
end

local function outfit_mesh(path, base_name, field, mesh_class)
    if path == "-" then return end
    local base = asset(armor_root .. base_name .. "." .. base_name .. "_C")
    local class = asset(path)
    if not valid(base) or not valid(class) then return end
    local defaults = class:GetCDO()
    if not valid(defaults) or not defaults:IsA(base) then return end
    local mesh = defaults[field]
    if not valid(mesh) or not mesh:IsA(StaticFindObject(mesh_class)) then return end
    return mesh, defaults
end

local function apply_outfit(entry, outfit, cosmetics)
    local body_path = outfit and outfit.body or "-"
    local head_path = outfit and outfit.head or "-"
    if entry.request_body ~= body_path or entry.request_nude ~= cosmetics.assets[3] then
        local mesh, defaults = outfit_mesh(body_path, "Item_ArmorMaster_BP", "SkeliMesh", "/Script/Engine.SkeletalMesh")
        local target = mesh or character_view.body(cosmetics, asset) or entry.default_asset
        local material = defaults and defaults.Default_Material
        character_view.clear_body(entry)
        entry.body:SetSkeletalMeshAsset(target)
        if valid(material) and material:IsA(StaticFindObject("/Script/Engine.MaterialInterface")) then
            entry.body:SetMaterial(0, material)
            entry.material_override = true
        elseif entry.material_override then
            entry.body:SetMaterial(0, nil)
            entry.material_override = nil
        end
        if not valid(entry.body.SkinnedAsset) or entry.body.SkinnedAsset:GetAddress() ~= target:GetAddress() then
            error("Stock armor mesh did not pass readback")
        end
        position_head(entry)
        entry.body_worn = mesh and body_path or "-"
        entry.request_body, entry.request_nude = body_path, cosmetics.assets[3]
    end
    if entry.request_head ~= head_path then
        local mesh, defaults = outfit_mesh(head_path, "Item_HelmetMaster_BP", "HelmMesh", "/Script/Engine.StaticMesh")
        if mesh then
            local component = valid(entry.helmet) and entry.helmet or visual_component(entry, "helmet", entry.head)
            component:SetStaticMesh(mesh)
            component:SetVisibility(true, false)
            if not valid(component.StaticMesh) or component.StaticMesh:GetAddress() ~= mesh:GetAddress() then
                error("Stock helmet mesh did not pass readback")
            end
        elseif valid(entry.helmet) then
            entry.helmet:SetVisibility(false, false)
        end
        entry.hide_plumage = defaults and defaults.HideHair == true
        entry.head_worn = mesh and head_path or "-"
        entry.request_head = head_path
    end
end

function avatar.sync(state, phase)
    local world = phase == "connected" and state.world:GetAddress() or nil
    if client_world ~= world or client_map ~= state.map then
        client_world, client_map, client_outfits, client_receipt = world, state.map, {}, nil
    end
    local authority = phase == "hosting" and state.world:GetAddress() or nil
    if authority ~= host_world then host_world, host_aim_receipt, host_aims = authority, nil, {} end
    sync_local_aim(state, phase)
    observe(state, phase)
    local outfits = phase == "hosting" and observations or client_outfits
    local seen = {}
    if (phase == "hosting" or phase == "connected") and state.local_pawn then
        for _, details in ipairs(state.guests) do
            local pawn = details.pawn
            if valid(pawn) and not pawn:IsActorBeingDestroyed() and not pawn:IsLocallyControlled() then
                local address = pawn:GetAddress()
                seen[address] = true
                local entry = avatars[address]
                if not entry or not valid(entry.pawn) or entry.name ~= details.name then
                    entry = { pawn = pawn, name = details.name, map = state.map }
                    avatars[address] = entry
                    local ok, failure = pcall(create, entry)
                    if not ok then
                        pcall(remove, entry)
                        entry.body, entry.head, entry.installed_body = nil, nil, false
                        entry.status, entry.failure = "unavailable", tostring(failure)
                    end
                end
                if entry.status == "default_body" or entry.status == "stock_body" then
                    local ok, failure = pcall(function()
                        entry.id = player_id(details)
                        local outfit = outfits[entry.id]
                        if entry.status == "default_body" then
                            local appearance = outfit and outfit.appearance or "-"
                            local cosmetics = character_view.decode(appearance)
                            if not cosmetics then error("Invalid remote character appearance") end
                            apply_outfit(entry, outfit, cosmetics)
                            character_view.apply(entry, cosmetics, appearance, asset, visual_component)
                            local aim = outfit and outfit.aim or "-"
                            if phase == "hosting" then
                                aim = outfit and host_aims[entry.id] == outfit.gun and outfit.gun or "-"
                            end
                            apply_locomotion(entry, aim)
                            aim_head(entry)
                        end
                        weapon_view.apply(entry, outfit and outfit.weapon or "-", outfit and outfit.firearm or "-")
                        if entry.weapon_parts and #entry.weapon_parts > 0 then
                            local socket = entry.body:GetSocketTransform(FName("Weapon_Socket01", EFindName.FNAME_Find), 2)
                            entry.weapon_socket_height = socket.Translation.Z
                        else
                            entry.weapon_socket_height = nil
                        end
                    end)
                    entry.failure = not ok and tostring(failure) or nil
                end
            end
        end
    end
    for address, entry in pairs(avatars) do
        if not seen[address] then
            pcall(remove, entry)
            avatars[address] = nil
        end
    end
end

function avatar.command(fields, state, phase)
    if fields[2] == "native-avatar-host-aims" then
        local receipt = fields[4]
        if phase ~= "hosting" or fields[3] ~= state.map or not receipt or #receipt ~= 32
            or not receipt:match("^[0-9a-f]+$") or (#fields - 4) % 2 ~= 0 or #fields > 12 then
            error("Invalid hosted avatar aim scope")
        end
        local aims = {}
        for index = 5, #fields, 2 do
            local id, mode = math.tointeger(tonumber(fields[index])), fields[index + 1]
            if not id or id < 0 or id > 2147483647 or aims[id] or not aimed[mode] then
                error("Invalid hosted avatar aim")
            end
            aims[id] = mode
        end
        host_world, host_aim_receipt, host_aims = state.world:GetAddress(), receipt, aims
        return "Guest avatar aim received"
    end
    if fields[2] ~= "native-avatar-outfits" then return end
    local receipt = fields[4]
    if phase ~= "connected" or fields[3] ~= state.map or not receipt or #receipt ~= 32
        or not receipt:match("^[0-9a-f]+$") or (#fields - 4) % 7 ~= 0 or #fields > 39 then
        error("Invalid remote avatar outfit scope")
    end
    local outfits = {}
    for index = 5, #fields, 7 do
        local id = math.tointeger(tonumber(fields[index]))
        local body, head, weapon, appearance = fields[index + 1], fields[index + 2], fields[index + 3], fields[index + 4]
        local aim, firearm = fields[index + 5], fields[index + 6]
        if not id or id < 0 or id > 2147483647 or outfits[id] or not outfit_path(body) or not outfit_path(head)
            or not weapon_view.decode(weapon) or not character_view.decode(appearance)
            or not firearm_path(firearm) or weapon == "-" and firearm ~= "-"
            or aim ~= "-" and (weapon == "-" or not aimed[aim]) then
            error("Invalid remote avatar outfit")
        end
        outfits[id] = { body = body, head = head, weapon = weapon, appearance = appearance, aim = aim, firearm = firearm }
    end
    client_world, client_map, client_outfits, client_receipt = state.world:GetAddress(), state.map, outfits, receipt
    return "Remote avatar outfits received"
end

function avatar.rows(row)
    local rows = {}
    for _, entry in pairs(avatars) do
        rows[#rows + 1] = row("native-player-avatar", entry.map, entry.name, entry.status, entry.failure or "",
            entry.id or "-", entry.body_worn or "-", entry.head_worn or "-", entry.aim_roll or "-",
            entry.weapon_model or "-", entry.appearance or "-", entry.pose_animation or "-", entry.weapon_socket_height or "-",
            valid(entry.body) and entry.body.AnimationMode or "-")
        if entry.last_melee then
            local swing = entry.last_melee
            rows[#rows + 1] = row("native-avatar-melee", entry.name, swing.serial, swing.clip, swing.peak,
                swings[entry] and 1 or 0)
        end
        local recoil = weapon_view.recoil_row(entry, row)
        if recoil then rows[#rows + 1] = recoil end
    end
    for id, outfit in pairs(observations) do
        rows[#rows + 1] = row("native-avatar-outfit", id, outfit.pawn, outfit.body, outfit.head, outfit.weapon,
            outfit.appearance, outfit.aim, outfit.gun, outfit.firearm)
    end
    if client_receipt then rows[#rows + 1] = row("native-avatar-outfits-receipt", client_receipt) end
    if host_aim_receipt then rows[#rows + 1] = row("native-avatar-host-aim-receipt", host_aim_receipt) end
    if holding_aim ~= nil then rows[#rows + 1] = row("native-avatar-local-aim", holding_aim and 1 or 0) end
    return rows
end

avatar.player_id = player_id

function avatar.visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.status == "default_body"
end

function avatar.weapon_visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.weapon_parts and #entry.weapon_parts > 0
end

return avatar
