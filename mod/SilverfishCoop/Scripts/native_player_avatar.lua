local guest_save = require("guest_save")
local weapon_view = require("native_avatar_weapon_view")
local handgun_view = require("native_avatar_handgun")
local character_view = require("native_avatar_character_view")
local gait = require("native_avatar_gait")
local visor = require("native_avatar_visor")
local avatar = {}

local body_path = "/Game/Classes/NPCs/NPC_Assets/CharacterModels/Player_NoArmor.Player_NoArmor"
local head_path = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/SM_Head_UV_01.SM_Head_UV_01"
local player_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local lean_arrow_class = "/Script/Engine.ArrowComponent"
local head_class = "/Script/Engine.StaticMeshComponent"
local scene_class = "/Script/Engine.SceneComponent"
local animation_root = "/Game/Classes/NPCs/NPC_Assets/Animations/"
local crouched_idle = "Crouching_Idle"
local crouched_walk = "Crouched_Walking"
local firearm_class = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local health_item_class = "/Game/Classes/Items/HealthItem_Master_BP.HealthItem_Master_BP_C"
local honey_egg_class = "/Game/Classes/Items/Item_HoneyEgg.Item_HoneyEgg_C"
local honey_egg_size_cm = 10
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local math_library = "/Script/Engine.Default__KismetMathLibrary"
local max_lean = 40
local lean_timeout = .45
local zero_rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local zero_location = {X = 0, Y = 0, Z = 0}
-- The stock handgun lowers through HandGun_Lowered_BlendSpace and uses the
-- alert gunman's RifleWalk_BlendSpace for the raised stance.
local aimed = {pistol = {"Rifle_Aiming_Low_Close1", "Walk_Forward"}, rifle = {"Rifle_Aiming_Idle", "Rifle_Walk"},
    optic = {"Rifle_Aiming_Idle", "Rifle_Walk"}}
local lowered = {pistol = "HandGun_Idle_2", rifle = "Rifle_Idle_1_"}
local running = {pistol = "HandGun_Run", rifle = "Rifle_Run_Forward"}
local melee_clips = {slash = "Standing_Melee_Attack_Horizontal", backhand = "Standing_Melee_Attack_Backhand",
    overhead = "Standing_Melee_Attack_Downward"}
local identity = {
    Rotation = { X = 0, Y = 0, Z = 0, W = 1 },
    Translation = { X = 0, Y = 0, Z = 0 },
    Scale3D = { X = 1, Y = 1, Z = 1 },
}
local avatars = {}
local avatar_clock, avatar_math
local swings = {}
local uses = {}
local observations, client_outfits = {}, {}
local observed_weapons = {}
local observed_characters, observed_world = {}, nil
local client_world, client_map, client_receipt
local host_world, host_aim_receipt, host_aims
local holding_aim, lighter_mode
local local_visor
local client_visors, host_visors = {}, {}
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local armor_root = "/Game/Classes/Armor_Items/"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function stock_seconds(world, address)
    if type(SilverfishReadNativeAvatarSeconds) == "function" then
        local now = SilverfishReadNativeAvatarSeconds(address)
        if now then return now end
    end
    if not valid(avatar_clock) then avatar_clock = StaticFindObject(gameplay_statics) end
    if valid(avatar_clock) then return avatar_clock:GetRealTimeSeconds(world) end
end

local function valid_lean(roll)
    return type(roll) == "number" and roll >= -max_lean and roll <= max_lean
end

function avatar.local_lean(pawn, world)
    if not valid(pawn) or not same(pawn:GetClass(), StaticFindObject(player_path)) or not pawn:IsLocallyControlled()
        or pawn:IsActorBeingDestroyed() or pawn.Dead == true or not same(pawn:GetWorld(), world) then return end
    local arrow = pawn.LeanPivotArrow
    if not valid(arrow) or not same(arrow:GetClass(), StaticFindObject(lean_arrow_class))
        or not same(arrow:GetOwner(), pawn) or not same(arrow:GetAttachParent(), pawn.CapsuleComponent) then return end
    local roll = arrow.RelativeRotation.Roll
    if valid_lean(roll) then return roll end
end

function avatar.set_remote_lean(world, id, roll)
    if not valid(world) or type(id) ~= "number" or not math.tointeger(id) or id < 0 or id > 2147483647
        or not valid_lean(roll) then return false end
    local scope, target = world:GetAddress(), nil
    for _, entry in pairs(avatars) do
        if entry.world == scope and entry.id == id and entry.status == "default_body" and valid(entry.body)
            and valid(entry.pawn) and not entry.pawn:IsActorBeingDestroyed() and not entry.pawn:IsLocallyControlled()
            and same(entry.pawn:GetWorld(), world) then
            if target then return false end
            target = entry
        end
    end
    if not target then return false end
    if roll == 0 then
        target.lean_target, target.lean_until = nil, nil
        return true
    end
    local now = stock_seconds(world, world:GetAddress())
    if not now then return false end
    target.lean_target = roll
    target.lean_until = now + lean_timeout
    return true
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

local function aim_kind(weapon)
    if not valid(weapon) then return "-" end
    local base = StaticFindObject(firearm_class)
    if not valid(base) or not weapon:IsA(base) then
        return weapon_view.is_optic(weapon) and "optic" or "-"
    end
    -- The stock player animation graph identifies handguns by ItemType 3.
    return weapon.ItemType == 3 and "pistol" or "rifle"
end

local function firearm_path(path)
    local package, class = path:match("^(/Game/Classes/Weapons/[%w_/%-]+)%.([%w_%-]+)_C$")
    return path == "-" or #path <= 160 and package and package:match("([%w_%-]+)$") == class
end

local function sync_local_aim(state, phase)
    local pawn = phase == "connected" and state.local_pawn and state.local_pawn.pawn or nil
    holding_aim, lighter_mode, local_visor = nil, nil, nil
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    holding_aim = false
    if not pawn.Dead then
        local read, mode = pcall(weapon_view.local_lighter, pawn)
        if read then lighter_mode = mode end
        local read_visor, powered = pcall(visor.powered, pawn, state.world, true)
        if read_visor and powered == true then local_visor = worn(pawn.Save.PlayerHelmet) end
    end
    if pawn.Dead or pawn.Sprinting or pawn.IsWeaponBlocked then return end
    -- The stock ADS state works for all local firearms, including ones that
    -- have no registered interception path for primary-fire input.
    if aim_kind(pawn.EquipedItem) ~= "-" then
        holding_aim = pawn.Aiming == true
    end
    if not holding_aim and type(SilverfishReadNativeGuestAim) == "function" then
        holding_aim = SilverfishReadNativeGuestAim(pawn:GetAddress()) == true
    end
end

local function has_horizontal_motion(pawn)
    local velocity = pawn:GetVelocity()
    return velocity.X^2 + velocity.Y^2 > 20^2
end

local function stock_motion(pawn)
    return pawn.Aiming == true, pawn.Sprinting == true, has_horizontal_motion(pawn)
end

local function observe_motion(world_address, pawn, address)
    if type(SilverfishReadNativeAvatarMotion) == "function" then
        local aiming, sprinting, moving = SilverfishReadNativeAvatarMotion(world_address, address)
        if aiming ~= nil then return aiming, sprinting, moving end
    end
    return stock_motion(pawn)
end

local function observe_appearance(world_address, pawn_address, save, sample)
    if sample and sample.model and type(SilverfishNativeAvatarAppearanceMatches) == "function"
        and SilverfishNativeAvatarAppearanceMatches(world_address, pawn_address, save:GetAddress(), sample) then
        return sample.model, sample
    end
    return character_view.observe(save.Character, sample)
end

local function observe(state, phase)
    observations = {}
    if phase ~= "hosting" then
        observed_weapons, observed_characters, observed_world = {}, {}, nil
        return
    end
    if not same(observed_world, state.world) then
        observed_characters, observed_world = {}, state.world
    end
    local known, weapons, characters, player_ids = {}, {}, {}, {}
    local world_address = valid(state.world) and state.world:GetAddress() or 0
    for _, details in ipairs(state.pawns) do
        local ok, id = pcall(player_id, details)
        if ok and id ~= nil then
            player_ids[details] = id
            if known[id] then
                observations[id] = nil
            else
                known[id] = true
                local read, body, head, pawn, save, local_pawn = pcall(function()
                    local pawn, save = details.pawn
                    local local_pawn = pawn:IsLocallyControlled()
                    if local_pawn then
                        save = state.world.OwningGameInstance.SaveSlot_Ref
                        if not pawn:HasAuthority() or not valid(save) or not valid(pawn.Save)
                            or pawn.Save:GetAddress() ~= save:GetAddress() then return end
                    else
                        pawn, save = guest_save.observed_item_context(state, phase, details)
                    end
                    if valid(pawn) and valid(save) then
                        return worn(save.PlayerArmor), worn(save.PlayerHelmet), pawn, save, local_pawn
                    end
                end)
                if read and body then
                    local address = pawn:GetAddress()
                    local read_weapon, weapon, cached = pcall(weapon_view.observe, pawn, save, observed_weapons[address])
                    local previous = observed_characters[address]
                    local sample = previous and same(previous.save, save) and previous or nil
                    local read_appearance, appearance, appearance_sample =
                        pcall(observe_appearance, world_address, address, save, sample)
                    if read_appearance and appearance_sample then
                        appearance_sample.save = save
                        characters[address] = appearance_sample
                    end
                    if read_weapon then weapons[address] = cached end
                    local gun = aim_kind(read_weapon and cached and cached.actor or nil)
                    local firearm = gun ~= "-" and cached.actor:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
                    if not firearm or not firearm_path(firearm) then firearm = "-" end
                    local read_visor, powered = false, false
                    if local_pawn and head ~= "-" then
                        read_visor, powered = pcall(visor.powered, pawn, state.world, false)
                    end
                    local aiming, sprinting, moving = observe_motion(world_address, pawn, address)
                    observations[id] = { pawn = details.name, body = body, head = head, weapon = read_weapon and weapon or "-",
                        appearance = read_appearance and appearance or "-", gun = gun, aim = aiming and gun or "-",
                        firearm = firearm, sprinting = sprinting, moving = moving,
                        visor = read_visor and powered == true }
                end
            end
        end
    end
    observed_weapons, observed_characters = weapons, characters
    return player_ids
end

local function attach_component(entry, field, parent, socket_name, class)
    local socket = FName(socket_name or "None", EFindName.FNAME_Find)
    if socket_name and not parent:DoesSocketExist(socket) then
        error("Remote avatar stock socket was unavailable: " .. socket_name)
    end
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

local function create_handgun_hardpoint(entry)
    return attach_component(entry, "handgun_hardpoint", entry.body, "Weapon_Socket01", scene_class)
end

local function create_weapon_pivot(entry, field, parent, socket)
    return attach_component(entry, field, parent, socket, scene_class)
end

local function position_head(entry)
    -- The stock head meshes use HelmetSocket as their origin. Put the aim
    -- pivot on the Head bone itself so an animated socket offset cannot move
    -- the pivot away from the neck during a jump or a pose change.
    local head_bone = FName("Head", EFindName.FNAME_Find)
    local helmet_socket = FName("HelmetSocket", EFindName.FNAME_Find)
    if entry.body:GetBoneIndex(head_bone) < 0 or not entry.body:DoesSocketExist(helmet_socket) then
        error("Remote avatar head anchors were unavailable")
    end
    local math_api = StaticFindObject(math_library)
    local origin = entry.body:GetSocketTransform(helmet_socket, 0)
    local neck = entry.body:GetSocketLocation(head_bone)
    local offset = math_api:InverseTransformLocation(origin, neck)
    entry.head:K2_SetRelativeLocation({X = -offset.X, Y = -offset.Y, Z = -offset.Z}, false, {}, true)
end

local function offset_body(entry, x, y)
    local pawn, body = entry.pawn, entry.movement_mesh
    local base, relative = pawn:GetBaseTranslationOffset(), body.RelativeLocation
    local location = {X = relative.X + x - base.X, Y = relative.Y + y - base.Y, Z = relative.Z}
    -- Preserve the current smoothing displacement and change its baseline so
    -- CharacterMovement keeps rotating the visible body around the capsule.
    pawn:CacheInitialMeshOffset({X = x, Y = y, Z = base.Z}, pawn:GetBaseRotationOffsetRotator())
    body:K2_SetRelativeLocation(location, false, {}, true)
end

local function restore_locomotion(entry)
    entry.gait_blend, entry.pose_request, entry.pose_transition = nil, nil, nil
    if entry.pose_animation and valid(entry.body) then
        entry.body:SetAnimationMode(entry.animation_mode, false)
        entry.pose_animation, entry.pose_mesh, entry.pose_asset, entry.pose_is_blend = nil, nil, nil, nil
    end
    if entry.pose_tick_option ~= nil and valid(entry.body) then
        entry.body.VisibilityBasedAnimTickOption = entry.pose_tick_option
        entry.pose_tick_option = nil
    end
end

local function refresh_pose_when_hidden(entry)
    if entry.pose_tick_option == nil then
        entry.pose_tick_option = entry.body.VisibilityBasedAnimTickOption
        entry.body.VisibilityBasedAnimTickOption = 0
    end
end

local function apply_locomotion(entry, aim, outfit)
    local swing = swings[entry]
    if swing then
        if swing.parts == entry.weapon_parts then return end
        swings[entry] = nil
    end
    local pawn, body = entry.pawn, entry.body
    local movement = pawn.CharacterMovement
    local crouched = pawn.bIsCrouched == true
    local profile = aimed[aim]
    if pawn.Dead or body:IsAnySimulatingPhysics()
        or valid(entry.movement_mesh) and entry.movement_mesh:IsAnySimulatingPhysics() then
        entry.gait_blend, entry.pose_request, entry.pose_transition = nil, nil, nil
        return
    end
    if not valid(movement) then
        restore_locomotion(entry)
        return
    end
    -- A simulated first-person pawn can receive a changing position with zero
    -- velocity. Its owner's authoritative movement sample drives the pose.
    local moving = outfit and outfit.moving
    if moving == nil then moving = has_horizontal_motion(pawn) end
    local sprinting = moving and outfit and outfit.sprinting
    local mesh = body.SkinnedAsset
    if not valid(mesh) then return end
    local instance = body.AnimScriptInstance
    if valid(instance) and instance:IsAnyMontagePlaying() then
        entry.gait_blend, entry.pose_request, entry.pose_transition = nil, nil, nil
        return
    end
    local pace = sprinting and 2 or moving and 1 or 0
    local falling = movement:IsFalling()
    local grounded_gait = not falling and not crouched and not profile and not uses[entry] and movement:IsMovingOnGround()
    local name, group
    if falling then
        -- Both airborne states in the stock Player AnimBP use this pose.
        name, group = "Rifle_Falling", "airborne"
    elseif crouched then
        group = profile and "crouched_aim" or "crouched"
        if profile then
            name = moving and "Walk_Crouching_Forward" or "Idle_Crouching_Aiming"
        else
            name = moving and crouched_walk or crouched_idle
        end
    elseif grounded_gait then
        name, group = gait.pose_name(entry, pace), "grounded"
    elseif profile then
        name, group = profile[moving and 2 or 1], "aim"
    elseif sprinting then
        name = running[entry.weapon_stance] or "Running_Unarmed"
    elseif not moving then
        name = lowered[entry.weapon_stance]
    end
    if group and not uses[entry] and gait.transition(entry, mesh, group, name, asset, refresh_pose_when_hidden) then return end
    if grounded_gait then
        if gait.apply(entry, mesh, pace, asset, refresh_pose_when_hidden) then return end
        instance = body.AnimScriptInstance
        name = sprinting and running[entry.weapon_stance] or not moving and lowered[entry.weapon_stance] or nil
    end
    entry.gait_blend = nil
    if not name then
        restore_locomotion(entry)
        return
    end
    local playing = valid(instance) and instance.CurrentAsset or nil
    if not entry.pose_is_blend and entry.pose_animation == name and entry.pose_mesh == mesh:GetAddress() and valid(playing)
        and playing:GetAddress() == entry.pose_asset then return end
    local animation = asset(animation_root .. name .. "." .. name)
    if not valid(animation) or not valid(animation.Skeleton) or not valid(mesh.Skeleton)
        or animation.Skeleton:GetAddress() ~= mesh.Skeleton:GetAddress() then
        restore_locomotion(entry)
        return
    end

    -- The stock first-person AnimBP reads the replicated crouch flag but its
    -- pose keeps the visible remote body upright. Use the stock full-body clip.
    -- A single-node player's clock can advance while its socket bones retain
    -- the previous pose. The projected held items and head use those bones.
    refresh_pose_when_hidden(entry)
    if not gait.play(body, animation) then
        restore_locomotion(entry)
        return
    end
    entry.pose_animation, entry.pose_mesh, entry.pose_asset = name, mesh:GetAddress(), animation:GetAddress()
    entry.pose_is_blend = nil
end

local function stop_use(entry)
    uses[entry] = nil
    if valid(entry.use_prop) and valid(entry.pawn) then entry.use_prop:K2_DestroyComponent(entry.pawn) end
    entry.use_prop = nil
    for _, part in pairs(entry.hidden_for_use or {}) do
        if valid(part) then part:SetHiddenInGame(false, false) end
    end
    entry.hidden_for_use = nil
    weapon_view.hide_lighter(entry, false)
end

local function hide_held_item(entry)
    weapon_view.hide_lighter(entry, true)
    for _, part in ipairs(entry.weapon_parts or {}) do
        if valid(part) and part.bHiddenInGame == false then
            entry.hidden_for_use[part:GetAddress()] = part
            part:SetHiddenInGame(true, false)
        end
    end
end

local function remove(entry)
    swings[entry] = nil
    stop_use(entry)
    weapon_view.clear(entry)
    handgun_view.clear(entry)
    character_view.clear(entry)
    visor.clear(entry)
    if valid(entry.helmet) and valid(entry.pawn) then
        entry.helmet:K2_DestroyComponent(entry.pawn)
    end
    if valid(entry.head) and valid(entry.pawn) then
        entry.head:K2_DestroyComponent(entry.pawn)
    end
    if valid(entry.head_pivot) and valid(entry.pawn) then
        entry.head_pivot:K2_DestroyComponent(entry.pawn)
    end
    if entry.installed_body and valid(entry.pawn) then
        if valid(entry.body) then
            character_view.clear_body(entry)
            entry.body:K2_DestroyComponent(entry.pawn)
        end
        if entry.mesh_offset and valid(entry.movement_mesh) then offset_body(entry, entry.mesh_offset.X, entry.mesh_offset.Y) end
    end
end

local function swing_body_ready(entry, world)
    local pawn, body = entry.pawn, entry.body
    return valid(pawn) and not pawn:IsActorBeingDestroyed() and not pawn.Dead
        and same(pawn:GetWorld(), world) and valid(body) and not body:IsAnySimulatingPhysics()
        and not (valid(entry.movement_mesh) and entry.movement_mesh:IsAnySimulatingPhysics())
end

function avatar.play_melee(world, id, kind, serial, duration, start_fraction)
    local name = melee_clips[kind]
    if not name then return end
    for _, entry in pairs(avatars) do
        if entry.id == id and entry.status == "default_body" and entry.firearm_class == "-"
            and not uses[entry] and valid(entry.weapon_parts and entry.weapon_parts[1])
            and swing_body_ready(entry, world) then
            local clip = asset(animation_root .. name .. "." .. name)
            local mesh = entry.body.SkinnedAsset
            if not valid(clip) or not valid(clip.Skeleton) or not valid(mesh) or not valid(mesh.Skeleton)
                or clip.Skeleton:GetAddress() ~= mesh.Skeleton:GetAddress() then return end
            local length = clip:GetPlayLength()
            local clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
            if length <= 0 or not valid(clock) then return end
            refresh_pose_when_hidden(entry)
            entry.body:SetAnimationMode(1, false)
            entry.body:SetAnimation(clip)
            entry.body:Stop()
            -- Advance the mesh pose explicitly so stock combat notifies never
            -- run on the observing player's replica.
            local first = start_fraction * length
            entry.body:SetPosition(first, false)
            entry.gait_blend, entry.pose_request, entry.pose_transition = nil, nil, nil
            entry.pose_animation, entry.pose_mesh, entry.pose_asset = name, mesh:GetAddress(), clip:GetAddress()
            entry.pose_is_blend = nil
            local hand = FName("Weapon_Socket01", EFindName.FNAME_Find)
            local position = entry.body:GetSocketTransform(hand, 2).Translation
            local swing = {serial = serial, clip = name, world = world:GetAddress(), start = clock:GetRealTimeSeconds(world),
                parts = entry.weapon_parts, body = entry.body,
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
    local scope = world:GetAddress()
    local now = stock_seconds(world, scope)
    if not now then return end
    for entry, swing in pairs(swings) do
        if swing.world ~= scope or not same(swing.body, entry.body) or not swing_body_ready(entry, world) then
            swings[entry] = nil
        else
            local mesh = entry.body.SkinnedAsset
            if swing.parts ~= entry.weapon_parts or not valid(mesh)
                or mesh:GetAddress() ~= entry.pose_mesh or now - swing.start >= swing.duration then
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
end

local function use_prop_size(extent, scale)
    return 2 * math.max(math.abs(extent.X * scale.X), math.abs(extent.Y * scale.Y), math.abs(extent.Z * scale.Z))
end

local function consumable_display_mesh(item_path, defaults)
    local template = StaticFindObject(item_path .. ":DisplayMesh_GEN_VARIABLE")
    if valid(template) then return template.StaticMesh end
    local inherited = defaults.DisplayMesh
    if valid(inherited) then return inherited.StaticMesh end
    local class = defaults:GetClass()
    for _ = 1, 32 do
        if not valid(class) then return end
        class = class:GetSuperStruct()
        local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass (.+)$")
        if not path then return end
        template = StaticFindObject(path .. ":DisplayMesh_GEN_VARIABLE")
        if valid(template) then return template.StaticMesh end
    end
end

function avatar.play_consumable(world, id, item_path, defaults, serial)
    if id == nil then return false end
    for _, entry in pairs(avatars) do
        if entry.id == id and valid(entry.body) and valid(entry.pawn)
            and entry.pawn:GetWorld():GetAddress() == world:GetAddress() then
            local mesh = consumable_display_mesh(item_path, defaults)
            local duration, transform = defaults.UseTime, defaults.Anim_Trans
            local statics = StaticFindObject(gameplay_statics)
            local math_api = StaticFindObject(math_library)
            if not valid(mesh) or not transform or type(duration) ~= "number" or duration <= 0 or duration > 30
                or not valid(statics) or not valid(math_api) then return false end
            local rotation = math_api:Quat_Rotator(transform.Rotation)
            local position = {X = transform.Translation.X, Y = transform.Translation.Y, Z = transform.Translation.Z}
            local scale = {X = transform.Scale3D.X, Y = transform.Scale3D.Y, Z = transform.Scale3D.Z}
            local egg_extent
            if item_path == honey_egg_class then
                local bounds = mesh:GetBounds()
                egg_extent = bounds and bounds.BoxExtent
                if not egg_extent then return false end
            end
            stop_use(entry)
            local prop = visual_component(entry, "use_prop", entry.body, "Weapon_Socket01")
            prop:SetStaticMesh(mesh)
            prop:SetRelativeScale3D(scale)
            if not valid(prop.StaticMesh) or mesh:GetAddress() ~= prop.StaticMesh:GetAddress() then
                stop_use(entry)
                return false
            end
            local prop_size
            if egg_extent then
                -- The remote avatar uses the item's display mesh, so size the
                -- Honey Egg from its bounds after accounting for the hand socket.
                local current_size = use_prop_size(egg_extent, prop:K2_GetComponentScale())
                if not (current_size > 0 and current_size < math.huge) then
                    stop_use(entry)
                    return false
                end
                local multiplier = honey_egg_size_cm / current_size
                prop:SetRelativeScale3D({X = scale.X * multiplier, Y = scale.Y * multiplier, Z = scale.Z * multiplier})
                prop_size = use_prop_size(egg_extent, prop:K2_GetComponentScale())
            end
            prop:SetHiddenInGame(false, false)
            local use = {serial = serial, item = item_path, mesh = mesh:GetFullName(), duration = duration,
                world = world:GetAddress(), start = statics:GetRealTimeSeconds(world), peak = 0, prop_size = prop_size,
                position = position, rotation = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}}
            local food_class = StaticFindObject(health_item_class)
            local head = FName("Head", EFindName.FNAME_Find)
            if valid(food_class) and defaults:IsA(food_class) and type(defaults.Regen_Boost) == "number"
                and defaults.Regen_Boost > 0 and entry.body:DoesSocketExist(head) then
                local bounds = {}
                local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
                if valid(system) then system:GetComponentBounds(prop, {}, bounds, {}) end
                local extent = bounds.BoxExtent or bounds
                local size = extent.X and math.min(16, math.sqrt(extent.X^2 + extent.Y^2 + extent.Z^2)) or 0
                use.food = {head = head, hand = FName("Weapon_Socket01", EFindName.FNAME_Find), math = math_api,
                    reach = 24 + size * .75, side = 7 + size * .25, height = 4 - size * .5}
            end
            uses[entry], entry.last_use, entry.hidden_for_use = use, use, {}
            hide_held_item(entry)
            return true
        end
    end
    return false
end

local function animate_food(entry, use, lift, using, lower)
    local food, body = use.food, entry.body
    local head = body:GetSocketLocation(food.head)
    local hand = body:GetSocketTransform(food.hand, 0)
    local facing = valid(entry.head_pivot) and entry.head_pivot:K2_GetComponentRotation() or entry.pawn:GetBaseAimRotation()
    -- Leave room for the head to look up or down without pulling the food
    -- into the face or back toward the waist.
    local head_pitch = (facing.Pitch + 180) % 360 - 180
    local aim = {Yaw = facing.Yaw, Pitch = math.max(-20, math.min(20, head_pitch)), Roll = 0}
    local forward = food.math:GetForwardVector(aim)
    local right = food.math:GetRightVector(aim)
    local reach = food.reach + 2 * math.sin(using * 2 * math.pi)
    local blend = lift * (1 - lower)
    blend = blend * blend * (3 - 2 * blend)
    local hold = hand.Translation
    local location = {
        X = hold.X + (head.X + forward.X * reach + right.X * food.side - hold.X) * blend,
        Y = hold.Y + (head.Y + forward.Y * reach + right.Y * food.side - hold.Y) * blend,
        Z = hold.Z + (head.Z + forward.Z * reach + right.Z * food.side + food.height - hold.Z) * blend,
    }
    local rotation = food.math:ComposeRotators(use.rotation, aim)
    local hand_rotation = food.math:Quat_Rotator(hand.Rotation)
    entry.use_prop:K2_SetWorldLocationAndRotation(location, food.math:RLerp(hand_rotation, rotation, blend, true),
        false, {}, true)
    local actual = entry.use_prop:K2_GetComponentLocation()
    use.peak = math.max(use.peak, math.sqrt((actual.X - hold.X)^2 + (actual.Y - hold.Y)^2 + (actual.Z - hold.Z)^2))
    if blend == 1 and not use.mouth_reach then
        use.mouth_reach = (actual.X - head.X) * forward.X + (actual.Y - head.Y) * forward.Y
            + (actual.Z - head.Z) * forward.Z
        use.mouth_height, use.mouth_pitch = actual.Z - head.Z, head_pitch
    end
end

function avatar.animate_use(get_world)
    if not next(uses) then return end
    local world = get_world()
    if not valid(world) then return end
    local scope = world:GetAddress()
    local now = stock_seconds(world, scope)
    if not now then return end
    for entry, use in pairs(uses) do
        local elapsed = math.max(0, now - use.start)
        if use.world ~= scope or not valid(entry.pawn) or not valid(entry.body) or not valid(entry.use_prop)
            or elapsed >= use.duration + .8 then
            stop_use(entry)
        else
            local lift = math.min(1, elapsed / .3)
            local using = math.min(1, math.max(0, elapsed - .3) / use.duration)
            local lower = math.max(0, elapsed - .3 - use.duration) / .5
            if use.food then
                animate_food(entry, use, lift, using, lower)
            else
                local p, r = use.position, use.rotation
                local z = (p.Z * lift + 10 * using) * (1 - lower) - 100 * lower
                entry.use_prop:K2_SetRelativeLocationAndRotation(
                    {X = p.X * lift * (1 - lower), Y = p.Y * lift * (1 - lower), Z = z},
                    {Pitch = r.Pitch * lift, Yaw = r.Yaw * lift, Roll = r.Roll * lift}, false, {}, true)
                local actual = entry.use_prop.RelativeLocation
                use.peak = math.max(use.peak, math.sqrt(actual.X^2 + actual.Y^2 + actual.Z^2))
            end
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

function avatar.play_cycle(world, id, weapon, serial, clip)
    for _, entry in pairs(avatars) do
        if entry.id == id and valid(entry.pawn) and entry.pawn:GetWorld():GetAddress() == world:GetAddress() then
            return weapon_view.cycle(entry, world, weapon, serial, clip)
        end
    end
    return false
end

function avatar.animate_cycle(get_world)
    weapon_view.animate_cycle(get_world)
end

local function angle_delta(target, current)
    return (target - current + 180) % 360 - 180
end

local function apply_lean(entry, now, math_api)
    if entry.lean_until and entry.lean_until <= now then entry.lean_target, entry.lean_until = nil, nil end
    local before, target = entry.displayed_lean or 0, entry.lean_target or 0
    if before == 0 and target == 0 then return false end
    local elapsed = entry.lean_time and now - entry.lean_time or .025
    entry.lean_time = now
    if elapsed <= 0 then return false end
    local roll = target + (before - target) * math.exp(-elapsed / .045)
    if math.abs(roll - target) < .05 then roll = target end
    local pawn = entry.pawn
    local crouched = pawn.bIsCrouched == true
    if roll == before and crouched == entry.lean_crouched then return false end
    if roll == 0 then
        entry.body:K2_SetRelativeLocationAndRotation(zero_location, zero_rotation, false, {}, true)
        entry.lean_rotation, entry.lean_time = nil, nil
    else
        local base, basis = pawn:GetBaseTranslationOffset(), pawn:GetBaseRotationOffsetRotator()
        local pivot = math_api:LessLess_VectorRotator({X = -base.X, Y = -base.Y,
            Z = (crouched and -20 or 0) - base.Z}, basis)
        -- The cooked stock pivot drops 20 cm when crouched. Express that
        -- pivot and forward-axis roll in the smoothed mesh's authored frame.
        local lean = {Pitch = 0, Yaw = 0, Roll = roll}
        local relative = math_api:ComposeRotators(math_api:ComposeRotators(basis, lean), math_api:NegateRotator(basis))
        local rotated = math_api:GreaterGreater_VectorRotator(pivot, relative)
        entry.body:K2_SetRelativeLocationAndRotation({X = pivot.X - rotated.X, Y = pivot.Y - rotated.Y,
            Z = pivot.Z - rotated.Z}, relative, false, {}, true)
        entry.lean_rotation = relative
    end
    entry.displayed_lean, entry.lean_crouched = roll, crouched
    return true
end

local function read_base_aim(entry, address)
    local pitch, yaw
    if type(SilverfishReadNativeRemoteAim) == "function" then
        pitch, yaw = SilverfishReadNativeRemoteAim(entry.world, address)
    end
    if not pitch and not entry.pawn:IsActorBeingDestroyed() then
        local rotation = entry.pawn:GetBaseAimRotation()
        pitch, yaw = rotation.Pitch, rotation.Yaw
    end
    return pitch, yaw
end

local function aim_head(entry, pitch, yaw, now, lean_changed, math_api)
    pitch = math.max(-45, math.min(45, (pitch + 180) % 360 - 180))
    local previous = entry.head_aim
    if previous then
        local elapsed = now - previous.time
        if elapsed <= 0 then return end
        previous.time = now
        if elapsed < .25 then
            local fraction = 1 - math.exp(-elapsed / .05)
            pitch = previous.pitch + (pitch - previous.pitch) * fraction
            yaw = (previous.yaw + angle_delta(yaw, previous.yaw) * fraction + 180) % 360 - 180
        end
    end
    if lean_changed or entry.lean_rotation or not previous or math.abs(pitch - previous.pitch) > .01
        or math.abs(angle_delta(yaw, previous.yaw)) > .01 then
        local aim
        if entry.lean_rotation then
            local basis = entry.movement_mesh:K2_GetComponentRotation()
            local lean = math_api:ComposeRotators(
                math_api:ComposeRotators(math_api:NegateRotator(basis), entry.lean_rotation), basis)
            aim = math_api:ComposeRotators({Pitch = pitch, Yaw = yaw, Roll = 0}, lean)
        end
        if not (entry.head_pivot_path and type(SilverfishWriteNativeRemoteHeadRotation) == "function"
            and SilverfishWriteNativeRemoteHeadRotation(entry.world, entry.pawn_address, entry.head_pivot_path,
                aim and aim.Pitch or pitch, aim and aim.Yaw or yaw, aim and aim.Roll or 0) == true) then
            entry.head_pivot:K2_SetWorldRotation(aim or {Pitch = pitch, Yaw = yaw, Roll = 0}, false, {}, true)
        end
        character_view.update_eye_direction(entry)
        entry.head_roll_pending = true
        previous = previous or {}
        previous.pitch, previous.yaw, previous.time = pitch, yaw, now
        entry.head_aim = previous
    end
end

local function aim_math()
    if not valid(avatar_math) then avatar_math = StaticFindObject(math_library) end
    return avatar_math
end

function avatar.animate_head(get_world)
    if not next(avatars) then return end
    local world = get_world()
    if not valid(world) then return end
    local scope = world:GetAddress()
    local now = stock_seconds(world, scope)
    if not now then return end
    local math_api
    for address, entry in pairs(avatars) do
        if entry.world == scope and valid(entry.head_pivot) and valid(entry.pawn) then
            local aim_pitch, aim_yaw = read_base_aim(entry, address)
            if not aim_pitch then goto next_avatar end
            local lean_changed = false
            if entry.lean_until or entry.lean_rotation then
                math_api = math_api or aim_math()
                if valid(math_api) and valid(entry.body) and valid(entry.movement_mesh) then
                    lean_changed = apply_lean(entry, now, math_api)
                end
            end
            aim_head(entry, aim_pitch, aim_yaw, now, lean_changed, math_api)
            character_view.align_tail(entry)
            if not uses[entry] and not swings[entry] then gait.advance(entry, now) end
            if entry.rifle_grip and (entry.rifle_idle or entry.rifle_rotated) then
                math_api = math_api or aim_math()
                local movement = entry.pawn.CharacterMovement
                if valid(math_api) then
                    weapon_view.rifle_idle(entry, entry.rifle_idle and not uses[entry] and not swings[entry]
                        and not entry.pawn.Dead and valid(movement) and movement:IsMovingOnGround(),
                        math_api, create_weapon_pivot)
                end
            end
            if entry.handgun_view and entry.handgun_view.requested then
                math_api = math_api or aim_math()
                if valid(math_api) and not pcall(handgun_view.update, entry, math_api, aim_pitch, aim_yaw) then
                    pcall(handgun_view.release, entry)
                end
            end
        end
        ::next_avatar::
    end
end

local function create(entry)
    local movement_mesh = entry.pawn.Mesh
    if not valid(movement_mesh) then error("Player body component was unavailable") end
    if valid(movement_mesh.SkinnedAsset) then
        entry.body = movement_mesh
        entry.status = "stock_body"
        return
    end
    local body_asset, head_asset = asset(body_path), asset(head_path)
    local component_type = StaticFindObject(head_class)
    if not valid(body_asset) or not valid(head_asset) or not valid(component_type) then
        error("Stock avatar assets were unavailable")
    end
    -- Keep the stock network smoothing transform. Its first-person component
    -- can clear animation instances, so the display mesh owns its own pose.
    entry.movement_mesh = movement_mesh
    local body = visual_component(entry, "body", movement_mesh, nil, true)
    entry.animation_mode = movement_mesh.AnimationMode
    entry.default_asset, entry.default_head = body_asset, head_asset
    entry.installed_body = true
    body:SetSkeletalMeshAsset(body_asset)
    local anim_class = movement_mesh.AnimClass
    if valid(anim_class) then body:SetAnimClass(anim_class) end
    body:SetAnimationMode(entry.animation_mode, true)
    body.VisibilityBasedAnimTickOption = movement_mesh.VisibilityBasedAnimTickOption
    body:SetHiddenInGame(false, false)
    body:SetVisibility(true, false)

    -- CharacterMesh0 is authored behind the capsule for first-person play.
    -- A visible third-person body needs the stock humanoid's centered base.
    local base = entry.pawn:GetBaseTranslationOffset()
    entry.mesh_offset = {X = base.X, Y = base.Y}
    offset_body(entry, 0, 0)

    local pivot = attach_component(entry, "head_pivot", body, "Head", scene_class)
    -- Keep the animated bone position and use the remote player's aim rotation.
    pivot:SetAbsolute(false, true, false)
    if type(SilverfishWriteNativeRemoteHeadRotation) == "function" then
        entry.head_pivot_path = pivot:GetFullName():match("^SceneComponent (%S+)$")
    end
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
        visor.equip(entry, defaults)
        entry.head_worn = mesh and head_path or "-"
        entry.request_head = head_path
    end
end

function avatar.sync(state, phase)
    local world_address = (phase == "connected" or phase == "hosting") and state.world:GetAddress() or nil
    local world = phase == "connected" and world_address or nil
    if client_world ~= world or client_map ~= state.map then
        client_world, client_map, client_outfits, client_receipt = world, state.map, {}, nil
        client_visors = {}
        visor.clear_guest()
    end
    local authority = phase == "hosting" and world_address or nil
    if authority ~= host_world then host_world, host_aim_receipt, host_aims, host_visors = authority, nil, {}, {} end
    sync_local_aim(state, phase)
    local player_ids = observe(state, phase)
    local outfits = phase == "hosting" and observations or client_outfits
    local visors = phase == "hosting" and host_visors or client_visors
    local seen = {}
    if (phase == "hosting" or phase == "connected") and state.local_pawn then
        local stock_time
        for _, details in ipairs(state.guests) do
            local pawn = details.pawn
            if valid(pawn) and not pawn:IsActorBeingDestroyed() and not pawn:IsLocallyControlled() then
                local address = pawn:GetAddress()
                seen[address] = true
                local entry = avatars[address]
                if not entry or not valid(entry.pawn) or entry.name ~= details.name or entry.world ~= world_address then
                    entry = { pawn = pawn, pawn_address = address, name = details.name, map = state.map,
                        world = world_address }
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
                        entry.id = player_ids and player_ids[details] or player_id(details)
                        local outfit = outfits[entry.id]
                        if entry.status == "default_body" then
                            local appearance = outfit and outfit.appearance or "-"
                            if entry.cosmetic_model ~= appearance then
                                entry.cosmetics = outfit and outfit.cosmetics or character_view.decode(appearance)
                                entry.cosmetic_model = appearance
                            end
                            local cosmetics = entry.cosmetics
                            if not cosmetics then error("Invalid remote character appearance") end
                            apply_outfit(entry, outfit, cosmetics)
                            pcall(visor.sync, entry, visors[entry.id] == true and not pawn.Dead, state.local_pawn.pawn)
                            character_view.apply(entry, cosmetics, appearance, asset, visual_component)
                            local equipment = outfit and outfit.weapon or "-"
                            if phase == "hosting" then equipment = weapon_view.lighter_override(equipment, host_aims[entry.id]) end
                            weapon_view.apply(entry, equipment, outfit and outfit.firearm or "-")
                            if #entry.weapon_parts > 0 then
                                if stock_time == nil then
                                    stock_time = stock_seconds(state.world, world_address) or false
                                end
                                if stock_time then weapon_view.suppress_stock(entry, stock_time) end
                            end
                            if uses[entry] then hide_held_item(entry) end
                            local aim = outfit and outfit.aim or "-"
                            if phase == "hosting" then
                                aim = outfit and host_aims[entry.id] == outfit.gun and outfit.gun or "-"
                            end
                            if aim == "optic" and not entry.optic then aim = "-" end
                            local raised = aim == "optic" and not pawn.Dead and not uses[entry]
                            weapon_view.raise_optic(entry, raised)
                            apply_locomotion(entry, aim, outfit)
                            entry.rifle_idle = aim == "-" and entry.weapon_stance == "rifle"
                                and entry.gait_blend ~= nil and entry.gait_blend.target == 0
                            if not pcall(handgun_view.sync, entry, aim == "pistol" and not uses[entry], create_handgun_hardpoint) then
                                pcall(handgun_view.release, entry)
                            end
                        end
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

local function read_visors(fields)
    local visors = {}
    if fields[5] ~= "visor" then return visors, 5 end
    local list, count = fields[6], 0
    if not list or #list > 54 or not list:match("^%d[%d,]*%d$") and not list:match("^%d$") then
        error("Invalid remote visor list")
    end
    for value in list:gmatch("[^,]+") do
        local id = math.tointeger(tonumber(value))
        count = count + 1
        if not id or id < 0 or id > 2147483647 or visors[id] or count > 5 then error("Invalid remote visor") end
        visors[id] = true
    end
    return visors, 7
end

function avatar.command(fields, state, phase)
    if fields[2] == "native-avatar-host-aims" then
        local receipt = fields[4]
        local visors, first = read_visors(fields)
        if phase ~= "hosting" or fields[3] ~= state.map or not receipt or #receipt ~= 32
            or not receipt:match("^[0-9a-f]+$") or (#fields - first + 1) % 2 ~= 0 or #fields > first + 7 then
            error("Invalid hosted avatar aim scope")
        end
        local aims = {}
        for index = first, #fields, 2 do
            local id, mode = math.tointeger(tonumber(fields[index])), fields[index + 1]
            if not id or id < 0 or id > 2147483647 or aims[id]
                or not (aimed[mode] or mode == "closed" or mode == "open" or mode == "lit") then
                error("Invalid hosted avatar aim")
            end
            aims[id] = mode
        end
        host_world, host_aim_receipt, host_aims, host_visors = state.world:GetAddress(), receipt, aims, visors
        return "Guest avatar aim received"
    end
    if fields[2] ~= "native-avatar-outfits" then return end
    local receipt = fields[4]
    local visors, first = read_visors(fields)
    if phase ~= "connected" or fields[3] ~= state.map or not receipt or #receipt ~= 32
        or not receipt:match("^[0-9a-f]+$") or (#fields - first + 1) % 9 ~= 0 or #fields > first + 44 then
        error("Invalid remote avatar outfit scope")
    end
    local outfits = {}
    local previous_outfits = client_world == state.world:GetAddress() and client_map == state.map and client_outfits
    for index = first, #fields, 9 do
        local id = math.tointeger(tonumber(fields[index]))
        local body, head, weapon, appearance = fields[index + 1], fields[index + 2], fields[index + 3], fields[index + 4]
        local aim, firearm, sprint = fields[index + 5], fields[index + 6], fields[index + 7]
        local motion = fields[index + 8]
        local previous = previous_outfits and previous_outfits[id]
        local cosmetics = previous and previous.appearance == appearance and previous.cosmetics
            or character_view.decode(appearance)
        if not id or id < 0 or id > 2147483647 or outfits[id] or not outfit_path(body) or not outfit_path(head)
            or not (previous and previous.weapon == weapon or weapon_view.decode(weapon)) or not cosmetics
            or not firearm_path(firearm) or weapon == "-" and firearm ~= "-" or sprint ~= "0" and sprint ~= "1"
            or motion ~= "0" and motion ~= "1" and motion ~= "-"
            or aim ~= "-" and (weapon == "-" or not aimed[aim]) then
            error("Invalid remote avatar outfit")
        end
        outfits[id] = {body = body, head = head, weapon = weapon, appearance = appearance, cosmetics = cosmetics, aim = aim,
            firearm = firearm, sprinting = sprint == "1"}
        if motion ~= "-" then outfits[id].moving = motion == "1" end
    end
    client_world, client_map, client_outfits, client_receipt = state.world:GetAddress(), state.map, outfits, receipt
    client_visors = visors
    return "Remote avatar outfits received"
end

local function apply_pose_frame(fields, state, phase)
    local accepted, message = pcall(avatar.command, fields, state, phase)
    if accepted then
        local synced, failure = pcall(avatar.sync, state, phase)
        if not synced then accepted, message = false, failure end
    end
    local receipt = phase == "hosting" and host_aim_receipt or phase == "connected" and client_receipt
    if accepted and (not receipt or receipt ~= fields[4]) then
        return false, "Avatar pose readback changed", "-"
    end
    return accepted, message, accepted and receipt or "-"
end

function avatar.host_aim_reply(fields, state, phase)
    return apply_pose_frame(fields, state, phase)
end

function avatar.outfit_reply(fields, state, phase, sequence, row)
    local accepted, message, receipt = apply_pose_frame(fields, state, phase)
    return row("native-avatar-outfits-reply", 1, fields[1], phase, state.map, state.local_pawn.name,
        sequence, receipt, accepted and "accepted" or "error", message) .. "\n"
end

local function read_head_roll(pivot)
    return pivot:K2_GetComponentRotation().Roll
end

function avatar.rows(row)
    local rows = {}
    for _, entry in pairs(avatars) do
        -- Actual head roll is diagnostic and only read when publishing the avatar.
        if entry.head_roll_pending and valid(entry.head_pivot)
            and valid(entry.pawn) and not entry.pawn:IsActorBeingDestroyed() then
            local ok, roll = pcall(read_head_roll, entry.head_pivot)
            if ok then entry.aim_roll, entry.head_roll_pending = roll, nil end
        end
        rows[#rows + 1] = row("native-player-avatar", entry.map, entry.name, entry.status, entry.failure or "",
            entry.id or "-", entry.body_worn or "-", entry.head_worn or "-", entry.aim_roll or "-",
            entry.weapon_model or "-", entry.appearance or "-", entry.pose_animation or "-", entry.weapon_socket_height or "-",
            valid(entry.body) and entry.body.AnimationMode or "-")
        if entry.last_melee then
            local swing = entry.last_melee
            rows[#rows + 1] = row("native-avatar-melee", entry.name, swing.serial, swing.clip, swing.peak,
                swings[entry] and 1 or 0)
        end
        if entry.displayed_lean then rows[#rows + 1] = row("native-avatar-lean", entry.name, entry.displayed_lean) end
        if valid(entry.visor_light) and entry.visor_light:IsVisible() then
            rows[#rows + 1] = row("native-avatar-visor-view", entry.id)
        end
        if entry.last_use then
            local use = entry.last_use
            rows[#rows + 1] = row("native-avatar-use", entry.name, use.serial, use.item, use.mesh,
                use.peak, uses[entry] and 1 or 0, use.mouth_reach or "-", use.mouth_height or "-", use.mouth_pitch or "-",
                use.prop_size or "-")
        end
        local recoil = weapon_view.recoil_row(entry, row)
        if recoil then rows[#rows + 1] = recoil end
        local cycle = weapon_view.cycle_row(entry, row)
        if cycle then rows[#rows + 1] = cycle end
    end
    for id, outfit in pairs(observations) do
        rows[#rows + 1] = row("native-avatar-outfit", id, outfit.pawn, outfit.body, outfit.head, outfit.weapon,
            outfit.appearance, outfit.aim, outfit.gun, outfit.firearm, outfit.sprinting and 1 or 0, outfit.moving and 1 or 0)
        if outfit.visor then rows[#rows + 1] = row("native-avatar-visor-source", id) end
    end
    if client_receipt then rows[#rows + 1] = row("native-avatar-outfits-receipt", client_receipt) end
    if host_aim_receipt then rows[#rows + 1] = row("native-avatar-host-aim-receipt", host_aim_receipt) end
    if holding_aim ~= nil then rows[#rows + 1] = row("native-avatar-local-aim", holding_aim and 1 or 0) end
    if lighter_mode then rows[#rows + 1] = row("native-avatar-local-lighter", lighter_mode) end
    if local_visor and local_visor ~= "-" then rows[#rows + 1] = row("native-avatar-local-visor", local_visor) end
    return rows
end

avatar.player_id = player_id

function avatar.offhand_body(pawn)
    local entry = valid(pawn) and avatars[pawn:GetAddress()]
    if entry and entry.status == "default_body" and valid(entry.body) then
        return entry.body
    end
end

function avatar.equipment_mesh(pawn, path)
    local entry = valid(pawn) and avatars[pawn:GetAddress()]
    if not entry then return end
    local class = StaticFindObject("/Script/Engine.StaticMeshComponent")
    if not valid(class) then return end
    for _, component in ipairs(entry.weapon_parts or {}) do
        if valid(component) and component:IsA(class) then
            local mesh = component.StaticMesh
            if valid(mesh) and mesh:GetFullName() == "StaticMesh " .. path then return component end
        end
    end
end

function avatar.visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.status == "default_body"
end

function avatar.weapon_visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.weapon_parts and #entry.weapon_parts > 0
end

return avatar
