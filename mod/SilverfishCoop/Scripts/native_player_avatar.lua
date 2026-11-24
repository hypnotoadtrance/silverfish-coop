local guest_save = require("guest_save")
local weapon_view = require("native_avatar_weapon_view")
local handgun_view = require("native_avatar_handgun")
local character_view = require("native_avatar_character_view")
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
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local math_library = "/Script/Engine.Default__KismetMathLibrary"
local max_lean = 40
local lean_timeout = .45
local zero_rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local zero_location = {X = 0, Y = 0, Z = 0}
-- The stock handgun lowers through HandGun_Lowered_BlendSpace and uses the
-- alert gunman's RifleWalk_BlendSpace for the raised stance.
local aimed = {pistol = {"Rifle_Aiming_Low_Close1", "Walk_Forward"}, rifle = {"Rifle_Aiming_Idle", "Rifle_Walk"}}
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
local swings = {}
local uses = {}
local observations, client_outfits = {}, {}
local observed_weapons = {}
local client_world, client_map, client_receipt
local host_world, host_aim_receipt, host_aims
local holding_aim, lighter_mode
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local armor_root = "/Game/Classes/Armor_Items/"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
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
    local clock = StaticFindObject(gameplay_statics)
    if not valid(clock) then return false end
    target.lean_target = roll
    target.lean_until = clock:GetRealTimeSeconds(world) + lean_timeout
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
    holding_aim, lighter_mode = nil, nil
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    holding_aim = false
    if not pawn.Dead then
        local read, mode = pcall(weapon_view.local_lighter, pawn)
        if read then lighter_mode = mode end
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
                    local gun = aim_kind(read_weapon and cached and cached.actor or nil)
                    local firearm = gun ~= "-" and cached.actor:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
                    if not firearm or not firearm_path(firearm) then firearm = "-" end
                    observations[id] = { pawn = details.name, body = body, head = head, weapon = read_weapon and weapon or "-",
                        appearance = read_appearance and appearance or "-", gun = gun, aim = pawn.Aiming and gun or "-",
                        firearm = firearm, sprinting = pawn.Sprinting == true, moving = has_horizontal_motion(pawn) }
                end
            end
        end
    end
    observed_weapons = weapons
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
    if entry.pose_animation and valid(entry.body) then
        entry.body:SetAnimationMode(entry.animation_mode, true)
        entry.pose_animation, entry.pose_mesh, entry.pose_asset = nil, nil, nil
    end
    if entry.pose_tick_option ~= nil and valid(entry.body) then
        entry.body.VisibilityBasedAnimTickOption = entry.pose_tick_option
        entry.pose_tick_option = nil
    end
end

local function apply_locomotion(entry, aim, outfit)
    if swings[entry] then return end
    local crouched = entry.pawn.bIsCrouched == true
    local profile = aimed[aim]
    if entry.pawn.CharacterMovement:IsFalling() then
        restore_locomotion(entry)
        return
    end
    -- A simulated first-person pawn can receive a changing position with zero
    -- velocity. Its owner's authoritative movement sample drives the pose.
    local moving = outfit and outfit.moving
    if moving == nil then moving = has_horizontal_motion(entry.pawn) end
    local name
    if crouched then
        if profile then
            name = moving and "Walk_Crouching_Forward" or "Idle_Crouching_Aiming"
        else
            name = moving and crouched_walk or crouched_idle
        end
    elseif outfit and outfit.sprinting and moving then
        name = running[entry.weapon_stance] or "Running_Unarmed"
    elseif profile then
        name = profile[moving and 2 or 1]
    elseif not moving then
        name = lowered[entry.weapon_stance]
    end
    if not name then
        restore_locomotion(entry)
        return
    end
    local mesh = entry.body.SkinnedAsset
    if not valid(mesh) then return end
    local instance = entry.body.AnimScriptInstance
    local playing = valid(instance) and instance.CurrentAsset or nil
    if entry.pose_animation == name and entry.pose_mesh == mesh:GetAddress() and valid(playing)
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
    if entry.pose_tick_option == nil then
        entry.pose_tick_option = entry.body.VisibilityBasedAnimTickOption
        entry.body.VisibilityBasedAnimTickOption = 0
    end
    if entry.body.AnimationMode == 1 and not valid(instance) then
        entry.body:SetAnimationMode(1, true)
    end
    entry.body:PlayAnimation(animation, true)
    entry.pose_animation, entry.pose_mesh, entry.pose_asset = name, mesh:GetAddress(), animation:GetAddress()
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

function avatar.play_consumable(world, id, item_path, defaults, serial)
    if id == nil then return false end
    for _, entry in pairs(avatars) do
        if entry.id == id and valid(entry.body) and valid(entry.pawn)
            and entry.pawn:GetWorld():GetAddress() == world:GetAddress() then
            local template = StaticFindObject(item_path .. ":DisplayMesh_GEN_VARIABLE")
            local mesh = valid(template) and template.StaticMesh
            local duration, transform = defaults.UseTime, defaults.Anim_Trans
            local statics = StaticFindObject(gameplay_statics)
            local math_api = StaticFindObject(math_library)
            if not valid(mesh) or not transform or type(duration) ~= "number" or duration <= 0 or duration > 30
                or not valid(statics) or not valid(math_api) then return false end
            local rotation = math_api:Quat_Rotator(transform.Rotation)
            local position = {X = transform.Translation.X, Y = transform.Translation.Y, Z = transform.Translation.Z}
            local scale = {X = transform.Scale3D.X, Y = transform.Scale3D.Y, Z = transform.Scale3D.Z}
            stop_use(entry)
            local prop = visual_component(entry, "use_prop", entry.body, "Weapon_Socket01")
            prop:SetStaticMesh(mesh)
            prop:SetHiddenInGame(false, false)
            prop:SetRelativeScale3D(scale)
            if not valid(prop.StaticMesh) or mesh:GetAddress() ~= prop.StaticMesh:GetAddress() then
                stop_use(entry)
                return false
            end
            local use = {serial = serial, item = item_path, mesh = mesh:GetFullName(), duration = duration,
                world = world:GetAddress(), start = statics:GetRealTimeSeconds(world), peak = 0,
                position = position, rotation = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}}
            local food_class = StaticFindObject(health_item_class)
            local head = FName("Head", EFindName.FNAME_Find)
            if valid(food_class) and defaults:IsA(food_class) and type(defaults.Regen_Boost) == "number"
                and defaults.Regen_Boost > 0 and entry.body:DoesSocketExist(head) then
                use.food = {head = head, hand = FName("Weapon_Socket01", EFindName.FNAME_Find), math = math_api}
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
    local aim = valid(entry.head_pivot) and entry.head_pivot:K2_GetComponentRotation() or entry.pawn:GetBaseAimRotation()
    local forward = food.math:GetForwardVector(aim)
    local reach = 24 + 2 * math.sin(using * 2 * math.pi)
    local blend = lift * (1 - lower)
    blend = blend * blend * (3 - 2 * blend)
    local hold = hand.Translation
    local location = {
        X = hold.X + (head.X + forward.X * reach - hold.X) * blend,
        Y = hold.Y + (head.Y + forward.Y * reach - hold.Y) * blend,
        Z = hold.Z + (head.Z + forward.Z * reach + 6 - hold.Z) * blend,
    }
    local rotation = food.math:ComposeRotators(use.rotation, aim)
    local hand_rotation = food.math:Quat_Rotator(hand.Rotation)
    entry.use_prop:K2_SetWorldLocationAndRotation(location, food.math:RLerp(hand_rotation, rotation, blend, true),
        false, {}, true)
    if blend == 1 and not use.mouth_reach then
        local actual = entry.use_prop:K2_GetComponentLocation()
        use.mouth_reach = (actual.X - head.X) * forward.X + (actual.Y - head.Y) * forward.Y
            + (actual.Z - head.Z) * forward.Z
    end
end

function avatar.animate_use(get_world)
    if not next(uses) then return end
    local world = get_world()
    local clock = StaticFindObject(gameplay_statics)
    if not valid(world) or not valid(clock) then return end
    local now, scope = clock:GetRealTimeSeconds(world), world:GetAddress()
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
            end
            use.peak = math.max(use.peak, math.abs(entry.use_prop.RelativeLocation.Z))
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

local function aim_head(entry, now, lean_changed, math_api)
    local rotation = entry.pawn:GetBaseAimRotation()
    local pitch = math.max(-45, math.min(45, (rotation.Pitch + 180) % 360 - 180))
    local yaw, previous = rotation.Yaw, entry.head_aim
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
        local aim = {Pitch = pitch, Yaw = yaw, Roll = 0}
        if entry.lean_rotation then
            local basis = entry.movement_mesh:K2_GetComponentRotation()
            local lean = math_api:ComposeRotators(
                math_api:ComposeRotators(math_api:NegateRotator(basis), entry.lean_rotation), basis)
            aim = math_api:ComposeRotators(aim, lean)
        end
        entry.head_pivot:K2_SetWorldRotation(aim, false, {}, true)
        entry.aim_roll = entry.head_pivot:K2_GetComponentRotation().Roll
        entry.head_aim = {pitch = pitch, yaw = yaw, time = now}
    end
end

function avatar.animate_head(get_world)
    if not next(avatars) then return end
    local world, clock = get_world(), StaticFindObject(gameplay_statics)
    if not valid(world) or not valid(clock) then return end
    local scope, now = world:GetAddress(), clock:GetRealTimeSeconds(world)
    local math_api
    for _, entry in pairs(avatars) do
        if entry.world == scope and valid(entry.head_pivot) and valid(entry.pawn)
            and not entry.pawn:IsActorBeingDestroyed() then
            local lean_changed = false
            if entry.lean_until or entry.lean_rotation then
                math_api = math_api or StaticFindObject(math_library)
                if valid(math_api) and valid(entry.body) and valid(entry.movement_mesh) then
                    lean_changed = apply_lean(entry, now, math_api)
                end
            end
            aim_head(entry, now, lean_changed, math_api)
            if entry.handgun_view and entry.handgun_view.requested then
                math_api = math_api or StaticFindObject(math_library)
                if valid(math_api) and not pcall(handgun_view.update, entry, math_api) then
                    pcall(handgun_view.release, entry)
                end
            end
        end
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
                if not entry or not valid(entry.pawn) or entry.name ~= details.name or entry.world ~= state.world:GetAddress() then
                    entry = { pawn = pawn, name = details.name, map = state.map, world = state.world:GetAddress() }
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
                            local equipment = outfit and outfit.weapon or "-"
                            if phase == "hosting" then equipment = weapon_view.lighter_override(equipment, host_aims[entry.id]) end
                            weapon_view.apply(entry, equipment, outfit and outfit.firearm or "-")
                            weapon_view.suppress_stock(entry)
                            if uses[entry] then hide_held_item(entry) end
                            local aim = outfit and outfit.aim or "-"
                            if phase == "hosting" then
                                aim = outfit and host_aims[entry.id] == outfit.gun and outfit.gun or "-"
                            end
                            apply_locomotion(entry, aim, outfit)
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
            if not id or id < 0 or id > 2147483647 or aims[id]
                or not (aimed[mode] or mode == "closed" or mode == "open" or mode == "lit") then
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
        or not receipt:match("^[0-9a-f]+$") or (#fields - 4) % 9 ~= 0 or #fields > 49 then
        error("Invalid remote avatar outfit scope")
    end
    local outfits = {}
    for index = 5, #fields, 9 do
        local id = math.tointeger(tonumber(fields[index]))
        local body, head, weapon, appearance = fields[index + 1], fields[index + 2], fields[index + 3], fields[index + 4]
        local aim, firearm, sprint = fields[index + 5], fields[index + 6], fields[index + 7]
        local motion = fields[index + 8]
        if not id or id < 0 or id > 2147483647 or outfits[id] or not outfit_path(body) or not outfit_path(head)
            or not weapon_view.decode(weapon) or not character_view.decode(appearance)
            or not firearm_path(firearm) or weapon == "-" and firearm ~= "-" or sprint ~= "0" and sprint ~= "1"
            or motion ~= "0" and motion ~= "1" and motion ~= "-"
            or aim ~= "-" and (weapon == "-" or not aimed[aim]) then
            error("Invalid remote avatar outfit")
        end
        outfits[id] = {body = body, head = head, weapon = weapon, appearance = appearance, aim = aim,
            firearm = firearm, sprinting = sprint == "1"}
        if motion ~= "-" then outfits[id].moving = motion == "1" end
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
        if entry.displayed_lean then rows[#rows + 1] = row("native-avatar-lean", entry.name, entry.displayed_lean) end
        if entry.last_use then
            local use = entry.last_use
            rows[#rows + 1] = row("native-avatar-use", entry.name, use.serial, use.item, use.mesh,
                use.peak, uses[entry] and 1 or 0, use.mouth_reach or "-")
        end
        local recoil = weapon_view.recoil_row(entry, row)
        if recoil then rows[#rows + 1] = recoil end
        local cycle = weapon_view.cycle_row(entry, row)
        if cycle then rows[#rows + 1] = cycle end
    end
    for id, outfit in pairs(observations) do
        rows[#rows + 1] = row("native-avatar-outfit", id, outfit.pawn, outfit.body, outfit.head, outfit.weapon,
            outfit.appearance, outfit.aim, outfit.gun, outfit.firearm, outfit.sprinting and 1 or 0, outfit.moving and 1 or 0)
    end
    if client_receipt then rows[#rows + 1] = row("native-avatar-outfits-receipt", client_receipt) end
    if host_aim_receipt then rows[#rows + 1] = row("native-avatar-host-aim-receipt", host_aim_receipt) end
    if holding_aim ~= nil then rows[#rows + 1] = row("native-avatar-local-aim", holding_aim and 1 or 0) end
    if lighter_mode then rows[#rows + 1] = row("native-avatar-local-lighter", lighter_mode) end
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
