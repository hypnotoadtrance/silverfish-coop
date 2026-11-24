local view = {}
local npc_melee = require("native_npc_melee_view")
local weapon_view = require("native_avatar_weapon_view")
local bolt_view = require("native_short_bolt_view")

local npc_base = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local turret_base = "/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C"
local weapon_base = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local consumable_base = "/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C"
local melee_base = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Master_BP.MeleeWep_Master_BP_C"
local melee_clips = {Melee_Slash_01 = "slash", Melee_Slash_Combo01 = "backhand", Melee_Charged_Smash_01 = "overhead"}
local melee_clip_root = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleWep_Anim/"
local cycle_clip_root = "/Game/Classes/Weapons/Wep_Assets/Wep_Anim/"
local play_animation = "/Script/Engine.SkeletalMeshComponent:PlayAnimation"
local attack_animation = "Animation_2_C8CAEADF40C9F2C76A885BA8E5E9AA27"
local attack_rate = "SwingRate_17_17DEA9B2431C48BC143445BD3516142E"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local attached_sound = "/Script/Engine.GameplayStatics:SpawnSoundAttached"
local sound_at_location = "/Script/Engine.GameplayStatics:PlaySoundAtLocation"
local trigger_click = "/Game/Assets/Sounds/CUEs/Weapon_Cues/Reload_Cues/TriggerClick_Cue.TriggerClick_Cue"
local math_path = "/Script/Engine.Default__KismetMathLibrary"
local stock_rest_inverse = {Pitch = 0, Yaw = 90, Roll = 0}
local turret_concurrency = "/Game/Assets/Sounds/SoundClasses/Weapons_SoundConcurrency.Weapons_SoundConcurrency"
local attenuation_path = "/Game/Assets/Sounds/SoundClasses/Weapon_Attenuation.Weapon_Attenuation"
local local_attenuation = "/Game/Assets/Sounds/SoundClasses/SmallSound_Attenuation.SmallSound_Attenuation"
local npc_impact_attenuation = "/Game/Assets/Sounds/SoundClasses/Default_Attenuation.Default_Attenuation"
local sound_types = {SoundCue = true, SoundWave = true, MetaSoundSource = true}
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local maximum, range = 36, 30000
local current, hooked, melee_hooked, recoil_hooked, cycle_hooked, visit, last_played, last_round
local serial, published, pending = 0, 0, {}
local use_hooked, location_sound_hooked, turret_hooked

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function stock_weapon(path)
    if type(path) ~= "string" or #path > 160 then return false end
    local package, class = path:match("^(/Game/Classes/Weapons/[%w_/%-]+)%.([%w_%-]+)_C$")
    return package and package:match("([%w_%-]+)$") == class
end

local function stock_turret(path)
    if type(path) ~= "string" or #path > 160 then return false end
    local package, class = path:match("^(/Game/Classes/NPCs/[%w_/%-]+)%.(Turret_[%w_%-]+)_C$")
    return package and package:match("([%w_%-]+)$") == class
end

local function bounded(n, minimum, maximum)
    return type(n) == "number" and n >= minimum and n <= maximum
end

local function stock_npc_impact(path)
    if type(path) ~= "string" or #path > 192 then return false end
    local package, name = path:match("^(/Game/Assets/Sounds/[%w_/%-]+)%.([%w_%-]+)$")
    return package and package:match("([%w_%-]+)$") == name
end

local function stock_consumable(path)
    if type(path) ~= "string" or #path > 160 then return false end
    local package, class = path:match("^(/Game/Classes/Items/[%w_/%-]+)%.([%w_%-]+)_C$")
    return package and package:match("([%w_%-]+)$") == class
end

local function consumable_defaults(path)
    local class, base = asset(path), asset(consumable_base)
    local defaults = valid(class) and class:GetCDO()
    if valid(defaults) and valid(base) and defaults:IsA(base) then return defaults end
end

local function play_quiet_sound(sound, listener, point)
    local statics, attenuation = StaticFindObject(statics_path), asset(local_attenuation)
    if not valid(statics) or not valid(attenuation) or not valid(sound) then return false end
    local origin = listener:K2_GetActorLocation()
    if (origin.X - point.X)^2 + (origin.Y - point.Y)^2 + (origin.Z - point.Z)^2 > 1800^2 then return false end
    statics:PlaySoundAtLocation(listener, sound, point, {Pitch = 0, Yaw = 0, Roll = 0},
        1, 1, 0, attenuation, nil, nil, nil)
    return true
end

local function stock_cycle(path)
    if type(path) ~= "string" or #path > 192 or path:sub(1, #cycle_clip_root) ~= cycle_clip_root then return false end
    local package, object = path:sub(#cycle_clip_root + 1):match("^([%w_%-]+)%.([%w_%-]+)$")
    return package and package == object
end

local function equipped_player(weapon)
    if not current then return end
    if not valid(weapon) or not weapon:HasAuthority() or not same(weapon:GetWorld(), current.world) then return end
    local pawn = weapon.User
    local pawn_class = StaticFindObject("/Game/Classes/Player/Player_FP.Player_FP_C")
    if not valid(pawn) or not valid(pawn_class) or not pawn:IsA(pawn_class) or not pawn:HasAuthority()
        or not same(pawn.EquipedItem, weapon) then return end
    return weapon, pawn
end

local function record(row)
    pending[#pending + 1] = row
    if #pending > maximum then table.remove(pending, 1) end
end

function view.short_bolt(world, origin, finish, rotation, from_guest)
    if not current or not same(current.world, world) or type(SilverfishPublishLocalWeaponCue) ~= "function" then return end
    serial = serial + 1
    record({serial, "bolt-flight", origin.X, origin.Y, origin.Z, finish.X, finish.Y, finish.Z,
        rotation.Pitch, rotation.Yaw, rotation.Roll, memory_only = true,
        expires = current.statics:GetRealTimeSeconds(world) + .25})
    if from_guest and valid(current.listener) then pcall(bolt_view.play, world, current.listener, origin, finish, rotation) end
end

function view.animate_bolts(get_world)
    bolt_view.animate(get_world)
end

local function record_npc_melee(now, id, path, rate, blend_in, blend_out)
    serial = serial + 1
    record({serial, "npc-melee", id, path, rate, blend_in, blend_out, expires = now + .25})
end

local function record_dry_fire(pawn, point)
    if not point or not bounded(point.X, -1e7, 1e7) or not bounded(point.Y, -1e7, 1e7)
        or not bounded(point.Z, -1e7, 1e7) then return end
    serial = serial + 1
    record({serial, "dry", point.X, point.Y, point.Z, expires = current.statics:GetRealTimeSeconds(pawn) + .25})
end

local function capture_location_sound(_, source, sound, location, _, volume, pitch, start, attenuation)
    if not current then return end
    local cue = sound:get()
    if not valid(cue) then return end
    local sound_class, path = cue:GetFullName():match("^(%S+) (%S+)$")
    local actor = source:get()
    if sound_class == "SoundCue" and path == trigger_click then
        local weapon, pawn = equipped_player(actor)
        if weapon then record_dry_fire(pawn, location:get()) end
        return
    end
    if not sound_types[sound_class] or not stock_npc_impact(path) or not valid(actor) then return end
    if not valid(current.npc_class) then current.npc_class = StaticFindObject(npc_base) end
    if not valid(current.npc_class) or not actor:IsA(current.npc_class) or not actor:HasAuthority()
        or not same(actor:GetWorld(), current.world) or not same(actor.Melee_SFX, cue) then return end
    local radius, point = attenuation:get(), location:get()
    -- ResolveMelee plays the NPC's stock cue at the verified trace contact.
    if not valid(radius) or radius:GetFullName() ~= "SoundAttenuation " .. npc_impact_attenuation
        or volume:get() ~= 1 or pitch:get() ~= 1 or start:get() ~= 0 or not point
        or not bounded(point.X, -1e7, 1e7) or not bounded(point.Y, -1e7, 1e7)
        or not bounded(point.Z, -1e7, 1e7) then return end
    serial = serial + 1
    record({serial, "npc-impact", path, point.X, point.Y, point.Z,
        expires = current.statics:GetRealTimeSeconds(actor) + .25})
end

local function play_npc_impact(pawn, path, point)
    local sound, radius = asset(path), asset(npc_impact_attenuation)
    local statics = StaticFindObject(statics_path)
    if not valid(sound) or not sound_types[sound:GetFullName():match("^(%S+) ")]
        or not valid(radius) or not valid(statics) then return false end
    statics:PlaySoundAtLocation(pawn, sound, point, {Pitch = 0, Yaw = 0, Roll = 0},
        1, 1, 0, radius, nil, nil, nil)
    return true
end

function view.dry_fired(pawn)
    if not current or not valid(pawn) or pawn:IsLocallyControlled() then return end
    local weapon = equipped_player(pawn.EquipedItem)
    if not weapon or weapon.Ammo ~= 0 or not bounded(weapon.AmmoCost_PerShot, 1, 16) then return end
    local point = weapon:K2_GetActorLocation()
    record_dry_fire(pawn, point)
    if valid(current.listener) then play_quiet_sound(asset(trigger_click), current.listener, point) end
end

function view.consumed(pawn, path, stock_played)
    if not current or not valid(pawn) or not pawn:HasAuthority() or not stock_consumable(path)
        or not same(pawn:GetWorld(), current.world) then return end
    local point = pawn:K2_GetActorLocation()
    local player = current.avatar.player_id({pawn = pawn, controller_object = pawn.Controller})
    serial = serial + 1
    record({serial, "consume", path, point.X, point.Y, point.Z, player,
        expires = current.statics:GetRealTimeSeconds(pawn) + .25})
    if not stock_played and valid(current.listener) then
        local defaults = consumable_defaults(path)
        if not defaults then return end
        pcall(play_quiet_sound, defaults.UseSound, current.listener, point)
        pcall(current.avatar.play_consumable, current.world, player, path, defaults, serial)
    end
end

local function capture_use(context)
    local item = context:get()
    if not current or not valid(item) or not item:HasAuthority()
        or not same(item:GetWorld(), current.world) or item.BeingUsed ~= true then return end
    local pawn, pawn_class = item.Player_Ref, StaticFindObject("/Game/Classes/Player/Player_FP.Player_FP_C")
    if not valid(pawn) or not valid(pawn_class) or not pawn:IsA(pawn_class)
        or not pawn:IsLocallyControlled() or pawn.UsingItem ~= true then return end
    local root = item.RootComponent
    if not valid(root) or not same(item:GetOwner(), pawn) or not same(item:GetInstigator(), pawn)
        or not same(root:GetAttachParent(), pawn.Prop_Arrow) or same(item, current.latest_use) then return end
    local path = item:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
    current.latest_use = item
    view.consumed(pawn, path, true)
end

local function npc_shooter(weapon)
    if not current or not valid(weapon) or not weapon:HasAuthority() or not same(weapon:GetWorld(), current.world) then
        return
    end
    local npc, base = weapon.Instigator, StaticFindObject(npc_base)
    if valid(npc) and valid(base) and npc:IsA(base) and npc:HasAuthority() and npc.Alive == true
        and same(npc:GetWorld(), current.world) and same(npc.Weapon_Ranged, weapon) then
        return npc
    end
end

local loki_path = "/Game/Classes/Weapons/Wep_Assets/Weapon_UnderBarrel_Loki.Weapon_UnderBarrel_Loki_C"

local function capture(context)
    local weapon = context:get()
    if not valid(weapon) then return end
    local path = weapon:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
    local receiver, player = equipped_player(weapon)
    if not player and path == loki_path then
        receiver, player = equipped_player(weapon.ParentWeapon)
        if not player or not same(require("native_guest_weapon").loki_child(receiver, player), weapon) then return end
    end
    local shooter = player or npc_shooter(weapon)
    if not shooter then return end
    local muzzle = weapon.Arrow_Muzzle
    if not valid(muzzle) or not same(muzzle:GetOwner(), weapon) then return end
    if not stock_weapon(path) then return end
    local position, rotation = muzzle:K2_GetComponentLocation(), muzzle:K2_GetComponentRotation()
    local scale, volume, pitch = weapon.MuzzleFlash_Scale, weapon.Fire_Volume, weapon.Fire_PitchShift
    if not bounded(scale, 0, 10) or not bounded(volume, 0, 10) or not bounded(pitch, -24, 24) then return end
    serial = serial + 1
    local now = current.statics:GetRealTimeSeconds(shooter)
    local row = {serial, path, position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw,
        rotation.Roll, scale, volume, pitch - 2, expires = now + .25}
    if player then
        local id = current.avatar.player_id({pawn = player, controller_object = player.Controller})
        if id then
            row[12] = id
            last_round = {row = row, weapon = receiver, at = now}
        end
    end
    record(row)
end

local function capture_turret(_, sound, attachment)
    if not current then return end
    local muzzle = attachment:get()
    local turret = valid(muzzle) and muzzle:GetOwner()
    if not valid(turret) then return end
    local path = turret:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
    if not stock_turret(path) then return end
    local base = StaticFindObject(turret_base)
    if not valid(base) or not turret:IsA(base) or not turret:HasAuthority()
        or turret.Alive ~= true or not same(turret:GetWorld(), current.world)
        or not same(turret.Arrow_Muzzle, muzzle) or not same(turret.FireSound, sound:get()) then return end
    local volume = turret.Fire_Volume
    if not bounded(volume, 0, 10) then return end
    local position, rotation = muzzle:K2_GetComponentLocation(), muzzle:K2_GetComponentRotation()
    serial = serial + 1
    record({serial, path, position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw,
        rotation.Roll, 1, volume, 0, expires = current.statics:GetRealTimeSeconds(turret) + .25})
end

local function valid_recoil(player, values)
    return player and player >= 0 and player <= 2147483647
        and bounded(values[1], -30, 30) and bounded(values[2], -30, 30) and bounded(values[3], -30, 30)
        and bounded(values[4], -45, 45) and bounded(values[5], -45, 45) and bounded(values[6], -45, 45)
        and bounded(values[7], .02, 1.5)
end

local function capture_recoil(context, target, orientation)
    local weapon, pawn = equipped_player(context:get())
    if not weapon or not last_round or not same(last_round.weapon, weapon) or #last_round.row ~= 12
        or current.statics:GetRealTimeSeconds(pawn) - last_round.at > .06 then return end
    local offset, rotation = target:get(), orientation:get()
    local math_api = StaticFindObject(math_path)
    if not valid(math_api) then return end
    -- The parts retain their stock rest transforms. Apply the delta from the
    -- stock mesh's -90 degree yaw in the parent frame.
    local delta = math_api:ComposeRotators(stock_rest_inverse, rotation)
    local values = {offset.X, offset.Y, offset.Z, delta.Pitch, delta.Yaw, delta.Roll, weapon.Anim_Recoil_RecoveryTime}
    local avatar = current.avatar
    local player = avatar.player_id({pawn = pawn, controller_object = pawn.Controller})
    if not valid_recoil(player, values) then return end
    local row = last_round.row
    row[12] = player
    for index, value in ipairs(values) do row[12 + index] = value end
    if not pawn:IsLocallyControlled() then avatar.play_recoil(current.world, player, row[2], row[1], values) end
end

local function capture_melee(context)
    local weapon, pawn = equipped_player(context:get())
    if not weapon then return end
    local animator, mesh = weapon["As Melee Wep Anim BP"], weapon.Wep_Skeli
    if not valid(animator) or not valid(mesh) or not same(mesh:GetAnimInstance(), animator)
        or not same(animator.Wep_Object, weapon) or not animator.IsMidSwing then return end
    local active = animator.ActiveAttack
    local animation = active and active[attack_animation]
    if not valid(animation) then return end
    local path = animation:GetFullName():match("^AnimSequence (%S+)$")
    local name = path and path:match("/([%w_]+)%.")
    if not name or path ~= melee_clip_root .. name .. "." .. name then return end
    local kind = melee_clips[name]
    local avatar = current.avatar
    local id = avatar.player_id({pawn = pawn, controller_object = pawn.Controller})
    if not kind or not id then return end
    local duration, fraction = .85, 0
    local montage, length, rate = animator.ActiveMontage, animation:GetPlayLength(), active[attack_rate]
    if valid(montage) and length > 0 and bounded(rate, .1, 10) then
        local position = animator:Montage_GetPosition(montage)
        if bounded(position, 0, length) then
            duration = math.max(.25, math.min(1.5, (length - position) / rate))
            fraction = math.min(.75, position / length)
        end
    end
    serial = serial + 1
    record({serial, "melee", id, kind, duration, fraction, expires = current.statics:GetRealTimeSeconds(pawn) + .25})
    if not pawn:IsLocallyControlled() then avatar.play_melee(current.world, id, kind, serial, duration, fraction) end
end

local function record_cycle(weapon, pawn, animation)
    if not valid(animation) then return end
    local weapon_path = weapon:GetClass():GetFullName():match("^BlueprintGeneratedClass (.+)$")
    local clip_path = animation:GetFullName():match("^AnimSequence (.+)$")
    if not stock_weapon(weapon_path) or not stock_cycle(clip_path) then return end
    local avatar = current.avatar
    local id = avatar.player_id({pawn = pawn, controller_object = pawn.Controller})
    if not id then return end
    serial = serial + 1
    record({serial, "cycle", id, weapon_path, clip_path, expires = current.statics:GetRealTimeSeconds(pawn) + .25})
    if not pawn:IsLocallyControlled() then avatar.play_cycle(current.world, id, weapon_path, serial, clip_path) end
end

local function capture_cycle(context, clip, looping)
    if not current or looping:get() then return end
    local rig, animation = context:get(), clip:get()
    if not valid(rig) or not valid(animation) then return end
    local weapon, pawn = equipped_player(rig:GetOwner())
    if not weapon or not same(weapon.Wep_Skeli, rig) then return end
    if not same(weapon.Anim_Primary, animation) and not same(weapon.Reload_Wet_Anim, animation)
        and not same(weapon.Reload_Dry_Anim, animation) then return end
    record_cycle(weapon, pawn, animation)
end

function view.reloaded(pawn, previous_ammo)
    if not current or not valid(pawn) then return end
    local weapon = equipped_player(pawn.EquipedItem)
    if not weapon then return end
    record_cycle(weapon, pawn, weapon_view.reload_clip(weapon, previous_ammo))
end

local function clear_local_cues()
    if current and type(SilverfishClearLocalWeaponCues) == "function" then SilverfishClearLocalWeaponCues() end
end

function view.sync(state, phase, avatar)
    npc_melee.sync(state, phase, record_npc_melee)
    if phase ~= "hosting" or not valid(state.world) then
        clear_local_cues()
        current, pending, last_round = nil, {}, nil
        return
    end
    if not current or not same(current.world, state.world) then
        clear_local_cues()
        current = {world = state.world, map = state.map, statics = StaticFindObject(statics_path), avatar = avatar}
        pending, last_round = {}, nil
    end
    current.listener = state.local_pawn and state.local_pawn.pawn
    local use = consumable_base .. ":Use_Item_Primary"
    if not use_hooked and not current.use_checked then
        current.use_checked = true
        if valid(asset(consumable_base)) and valid(StaticFindObject(use)) then
            RegisterHook(use, function(context) pcall(capture_use, context) end)
            use_hooked = true
        end
    end
    local recoil = weapon_base .. ":ApplyRecoil"
    if not recoil_hooked and valid(StaticFindObject(recoil)) then
        RegisterHook(recoil, function(context, target, orientation) pcall(capture_recoil, context, target, orientation) end)
        recoil_hooked = true
    end
    local path = weapon_base .. ":SpawnProjectile"
    if not hooked and valid(StaticFindObject(path)) then
        -- The stock firearm calls this once, after CheckAmmoCost succeeds, even for a multi-pellet shot.
        RegisterHook(path, function(context) pcall(capture, context) end)
        hooked = true
    end
    local swing = melee_base .. ":SwingStart"
    if not melee_hooked and valid(StaticFindObject(swing)) then
        RegisterHook(swing, function(context) pcall(capture_melee, context) end)
        melee_hooked = true
    end
    if not cycle_hooked and valid(StaticFindObject(play_animation)) then
        RegisterHook(play_animation, function(context, clip, looping) pcall(capture_cycle, context, clip, looping) end)
        cycle_hooked = true
    end
    if not turret_hooked and valid(StaticFindObject(attached_sound)) then
        RegisterHook(attached_sound, function(...) pcall(capture_turret, ...) end)
        turret_hooked = true
    end
    if not location_sound_hooked and valid(StaticFindObject(sound_at_location)) then
        RegisterHook(sound_at_location, function(...) pcall(capture_location_sound, ...) end)
        location_sound_hooked = true
    end
end

function view.flush(get_world)
    if not current or not bridge or published == serial or #pending == 0 then return end
    local world = get_world()
    if not same(world, current.world) then return end
    local now = current.statics:GetRealTimeSeconds(world)
    while pending[1] and pending[1].expires <= now do table.remove(pending, 1) end
    if #pending == 0 then return end
    if type(SilverfishPublishLocalWeaponCue) == "function" then
        for _, entry in ipairs(pending) do
            if entry[1] > published then
                local remaining = math.max(1, math.min(250, math.floor((entry.expires - now) * 1000)))
                local offered = SilverfishPublishLocalWeaponCue(current.map, table.concat(entry, "|"), remaining)
                if offered == false then break end
                current.memory_cues = true
            end
        end
        if current.memory_cues then
            published, pending = serial, {}
            return
        end
    end
    local rows = {current.map}
    for _, entry in ipairs(pending) do
        if not entry.memory_only then rows[#rows + 1] = table.concat(entry, "|") end
    end
    if #rows == 1 then published, pending = serial, {}; return end
    local target = bridge .. "/native-weapon-shots"
    local temporary = target .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return end
    local written = file:write(table.concat(rows, "\n"), "\n")
    local closed = file:close()
    if written and closed then
        if not os.rename(temporary, target) then
            os.remove(target)
            if not os.rename(temporary, target) then return end
        end
        published = serial
    end
end

local function local_muzzle(pawn, path)
    local weapon = pawn.EquipedItem
    if path == loki_path then weapon = require("native_guest_weapon").loki_child(weapon, pawn) end
    if not valid(weapon) or not same(weapon:GetWorld(), pawn:GetWorld()) or not same(weapon.User, pawn)
        or weapon:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. path then return end
    local muzzle = weapon.Arrow_Muzzle
    if valid(muzzle) and same(muzzle:GetOwner(), weapon) then
        return muzzle, weapon
    end
end

local function play(pawn, path, point, rotation, scale, volume, pitch, distance, muzzle, firing_weapon)
    local turret = stock_turret(path)
    local class, base = asset(path), asset(turret and turret_base or weapon_base)
    local defaults = valid(class) and class:GetCDO()
    if not valid(defaults) or not valid(base) or not defaults:IsA(base) then return end
    local source = firing_weapon or defaults
    local attenuation, sound = asset(attenuation_path), source.FireSound
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local statics = StaticFindObject(statics_path)
    local particles, audio = false, false
    if scale > 0 and valid(niagara) and valid(source.MuzzleFlash) then
        local size, component = {X = scale, Y = scale, Z = scale}
        if muzzle then
            -- Match the stock first-person effect in the held weapon's render space.
            component = niagara:SpawnSystemAttached(source.MuzzleFlash, muzzle, FName("None"),
                {X = 0, Y = 0, Z = 0}, {Pitch = 0, Yaw = 0, Roll = 0}, 0, true, true, 0, true)
            if valid(component) then component:SetWorldScale3D(size) end
        else
            component = niagara:SpawnSystemAtLocation(pawn, source.MuzzleFlash, point, rotation, size, true, true, 0, true)
        end
        particles = valid(component) == true
    end
    if volume > 0 and valid(statics) and valid(sound) and valid(attenuation) then
        local concurrency
        if turret then concurrency = asset(turret_concurrency) else concurrency = defaults["Concurrency Settings"] end
        local component = statics:SpawnSoundAtLocation(pawn, sound, point, rotation, volume, 1, 0,
            attenuation, concurrency, true)
        if valid(component) then
            component:SetFloatParameter(FName("Distance"), distance)
            component:SetFloatParameter(FName("PitchShift"), pitch)
            audio = true
        end
    end
    -- A muted process has no audio device, but can still verify the selected stock sound.
    return particles, audio, valid(sound) == true
end

function view.command(args, state, phase, avatar)
    if args[2] ~= "native-weapon-play-shots" then return end
    local pawn, epoch = state.local_pawn and state.local_pawn.pawn, args[4]
    if phase ~= "connected" or not valid(pawn) or not pawn:IsLocallyControlled() or not valid(state.world)
        or state.map ~= args[3] or not epoch or #epoch ~= 32 or not epoch:match("^[a-f0-9]+$")
        or #args < 5 or #args > maximum + 4 then error("Weapon shot playback scope is unavailable", 0) end
    local scope = tostring(state.world:GetAddress()) .. epoch
    if scope ~= visit then visit, last_played = scope, 0 end
    local origin, receipts = pawn:K2_GetActorLocation(), {}
    local local_id = avatar.player_id(state.local_pawn)
    for index = 5, #args do
        local values = {}
        for field in (args[index] .. "|"):gmatch("([^|]*)|") do values[#values + 1] = field end
        local id, path = math.tointeger(tonumber(values[1])), values[2]
        if #values == 11 and id and id > last_played and path == "bolt-flight" then
            local shot = {}
            for axis = 3, 11 do
                local value, limit = tonumber(values[axis]), axis <= 8 and 1e7 or 180
                if not bounded(value, -limit, limit) then break end
                shot[#shot + 1] = value
            end
            if #shot == 9 then
                last_played = id
                local ok, shown = pcall(bolt_view.play, state.world, pawn,
                    {X = shot[1], Y = shot[2], Z = shot[3]}, {X = shot[4], Y = shot[5], Z = shot[6]},
                    {Pitch = shot[7], Yaw = shot[8], Roll = shot[9]})
                if ok and shown then receipts[#receipts + 1] = ("%d:bolt_flight"):format(id) end
            end
            goto next_cue
        end
        if #values == 6 and id and id > last_played and path == "npc-impact" and stock_npc_impact(values[3]) then
            local x, y, z = tonumber(values[4]), tonumber(values[5]), tonumber(values[6])
            if bounded(x, -1e7, 1e7) and bounded(y, -1e7, 1e7) and bounded(z, -1e7, 1e7) then
                last_played = id
                if (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 <= range^2 then
                    local ok, heard = pcall(play_npc_impact, pawn, values[3], {X = x, Y = y, Z = z})
                    if ok and heard then receipts[#receipts + 1] = ("%d:npc_impact"):format(id) end
                end
            end
            goto next_cue
        end
        if #values == 7 and id and id > last_played and path == "npc-melee" then
            local member = math.tointeger(tonumber(values[3]))
            last_played = id
            local ok, accepted = pcall(npc_melee.offer, state.world, member, values[4],
                tonumber(values[5]), tonumber(values[6]), tonumber(values[7]))
            if ok and accepted then receipts[#receipts + 1] = ("%d:npc_melee"):format(id) end
            goto next_cue
        end
        if #values == 5 and id and id > last_played and path == "dry" then
            local x, y, z = tonumber(values[3]), tonumber(values[4]), tonumber(values[5])
            if bounded(x, -1e7, 1e7) and bounded(y, -1e7, 1e7) and bounded(z, -1e7, 1e7) then
                last_played = id
                local ok, heard = pcall(function()
                    return play_quiet_sound(asset(trigger_click), pawn, {X = x, Y = y, Z = z})
                end)
                if ok and heard then receipts[#receipts + 1] = ("%d:dry"):format(id) end
            end
            goto next_cue
        end
        if (#values == 6 or #values == 7) and id and id > last_played and path == "consume"
            and stock_consumable(values[3]) then
            local x, y, z = tonumber(values[4]), tonumber(values[5]), tonumber(values[6])
            if bounded(x, -1e7, 1e7) and bounded(y, -1e7, 1e7) and bounded(z, -1e7, 1e7) then
                last_played = id
                local resolved, defaults = pcall(consumable_defaults, values[3])
                if resolved and defaults then
                    local ok, heard = pcall(play_quiet_sound, defaults.UseSound, pawn, {X = x, Y = y, Z = z})
                    if ok and heard then receipts[#receipts + 1] = ("%d:consume"):format(id) end
                    local player = math.tointeger(tonumber(values[7]))
                    if player and player >= 0 and player <= 2147483647 then
                        local shown, visual = pcall(avatar.play_consumable, state.world, player, values[3], defaults, id)
                        if shown and visual then receipts[#receipts + 1] = ("%d:consume_visual"):format(id) end
                    end
                end
            end
            goto next_cue
        end
        if #values == 5 and id and id > last_played and path == "cycle" then
            local player = math.tointeger(tonumber(values[3]))
            if player and player >= 0 and player <= 2147483647 and stock_weapon(values[4]) and stock_cycle(values[5]) then
                last_played = id
                -- The owning guest plays the reload from its confirmed action reply.
                if player ~= local_id then
                    local ok, played = pcall(avatar.play_cycle, state.world, player, values[4], id, values[5])
                    if ok and played then receipts[#receipts + 1] = ("%d:cycle"):format(id) end
                end
            end
            goto next_cue
        end
        if #values == 6 and id and id > last_played and path == "melee" then
            local player = math.tointeger(tonumber(values[3]))
            local duration, fraction = tonumber(values[5]), tonumber(values[6])
            if player and player >= 0 and player <= 2147483647
                and bounded(duration, .25, 1.5) and bounded(fraction, 0, .75) then
                last_played = id
                local ok, clip = pcall(avatar.play_melee, state.world, player, values[4], id, duration, fraction)
                if ok and clip then receipts[#receipts + 1] = ("%d:%s"):format(id, clip) end
            end
            goto next_cue
        end
        local x, y, z, pitch_angle, yaw, roll, scale, volume, pitch = table.unpack(values, 3, 11)
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        pitch_angle, yaw, roll = tonumber(pitch_angle), tonumber(yaw), tonumber(roll)
        scale, volume, pitch = tonumber(scale), tonumber(volume), tonumber(pitch)
        if id and id > last_played and ((#values == 11 and (stock_weapon(path) or stock_turret(path)))
            or ((#values == 12 or #values == 19) and stock_weapon(path)
                and math.tointeger(tonumber(values[12])) and tonumber(values[12]) >= 0
                and tonumber(values[12]) <= 2147483647))
            and bounded(x, -1e7, 1e7) and bounded(y, -1e7, 1e7) and bounded(z, -1e7, 1e7)
            and bounded(pitch_angle, -180, 180) and bounded(yaw, -180, 180) and bounded(roll, -180, 180)
            and bounded(scale, 0, 10) and bounded(volume, 0, 10) and bounded(pitch, -26, 24) then
            last_played = id
            local point, rotation = {X = x, Y = y, Z = z}, {Pitch = pitch_angle, Yaw = yaw, Roll = roll}
            local source = tonumber(values[12])
            local muzzle, firing_weapon
            if source ~= nil and source == local_id then
                muzzle, firing_weapon = local_muzzle(pawn, path)
                if muzzle then
                    point, rotation = muzzle:K2_GetComponentLocation(), muzzle:K2_GetComponentRotation()
                else
                    -- The shooter may already have holstered or switched weapons.
                    point, scale = origin, 0
                end
            end
            local distance = math.sqrt((origin.X - point.X)^2 + (origin.Y - point.Y)^2 + (origin.Z - point.Z)^2)
            if distance <= range then
                local ok, particles, audio, stock_sound = pcall(play, pawn, path, point, rotation,
                    scale, volume, pitch, distance, muzzle, firing_weapon)
                local recoil = false
                if #values == 19 and source ~= local_id then
                    local kick = {}
                    for index = 1, 7 do kick[index] = tonumber(values[12 + index]) end
                    if valid_recoil(source, kick) then
                        local applied, moved = pcall(avatar.play_recoil, state.world, source, path, id, kick)
                        recoil = applied and moved
                    end
                end
                if ok then
                    receipts[#receipts + 1] = string.format("%d:%d:%d:%d:%d", id,
                        particles and 1 or 0, audio and 1 or 0, stock_sound and 1 or 0, recoil and 1 or 0)
                end
            end
        end
        ::next_cue::
    end
    return table.concat(receipts, ",")
end

return view
