local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-npc-override-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local class_path = "/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C"
local damage_key = "Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5"
local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
local last, authority_drone, authority_turret
local shooters = {}
local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
local color = {R=0, G=0, B=0, A=0}
local pipe_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function heading(from, target)
    local x, y, z = target.X - from.X, target.Y - from.Y, target.Z - from.Z
    return {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
end

local function authoritative_guest(world)
    local guest
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and not candidate:IsActorBeingDestroyed() and candidate:HasAuthority()
            and not candidate:IsLocallyControlled() and same(candidate:GetWorld(), world) then
            assert(not guest, "expected one authoritative guest")
            guest = candidate
        end
    end
    assert(valid(guest) and valid(guest.Save) and not same(guest.Save, world.OwningGameInstance.SaveSlot_Ref),
        "the test needs a detached authoritative guest")
    return guest
end

local function gun_target(pawn, world)
    local guest = authoritative_guest(world)
    assert(valid(guest) and valid(guest.Camera) and valid(guest.EquipedItem), "the test needs an equipped stock guest")
    local eye = guest.Camera:K2_GetComponentLocation()
    local origin, weapon = guest:K2_GetActorLocation(), guest.EquipedItem
    local yaw = math.rad(guest:K2_GetActorRotation().Yaw)
    for _, offset in ipairs({0, 45, -45, 90, -90, 135, -135, 180}) do
        local bearing = yaw + math.rad(offset)
        local point = {X=origin.X+450*math.cos(bearing), Y=origin.Y+450*math.sin(bearing), Z=eye.Z+35}
        if trace:LineTraceSingle(pawn, eye, point, 0, true, {guest, weapon}, 0, {}, true, color, color, 0) == false then
            return point
        end
    end
    error("no clear test shot from the authoritative guest position")
end

local function guid_of(row)
    local id = row[item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function seed_pipe(world)
    local guest = authoritative_guest(world)
    local class = StaticFindObject(pipe_path)
    if not valid(class) then LoadAsset(pipe_path); class = StaticFindObject(pipe_path) end
    local factory = StaticFindObject(factory_path)
    assert(valid(class) and valid(factory), "stock Old Pipe factory unavailable")
    local defaults, output = class:GetCDO(), {}
    factory:ItemActorToStruct(defaults, guest, output)
    local row = output.Item or output
    assert(same(row[item_class], class), "stock factory returned the wrong melee class")
    local equipment = guest.Save.PlayerEquipment
    for index = 1, equipment:GetArrayNum() do
        if not valid(equipment[index][item_class]) then
            equipment[index] = row
            equipment[index][payload] = defaults.DataStruct
            return guid_of(row)
        end
    end
    error("stock Old Pipe needs an empty utility slot")
end

local function select_equipment(pawn, matches)
    local equipment = pawn.Save.PlayerEquipment
    for index = 1, equipment:GetArrayNum() do
        local row = equipment[index]
        if valid(row[item_class]) and matches(row) then
            pawn:Equip_NextItem(index - 1, false)
            return "selected"
        end
    end
    return "waiting"
end

local function position(text)
    local x, y, z = text:match("^([%d%.%-]+),([%d%.%-]+),([%d%.%-]+)$")
    assert(x, "expected a stock melee target position")
    return {X=tonumber(x), Y=tonumber(y), Z=tonumber(z)}
end

local function melee_target(world, host, center)
    if host then return shooters.state end
    local closest, squared = nil, 100^2
    for _, actor in ipairs(FindAllOf("NPC_State_Conscript_C") or {}) do
        if valid(actor) and actor:GetLocalRole() == 1 and same(actor:GetWorld(), world) then
            local point = actor:K2_GetActorLocation()
            local distance = (point.X-center.X)^2 + (point.Y-center.Y)^2 + (point.Z-center.Z)^2
            if distance < squared then closest, squared = actor, distance end
        end
    end
    return closest
end

local function melee_lane(guest, target, angle)
    local eye = guest.Camera:K2_GetComponentLocation()
    local pitch, yaw = math.rad(angle.Pitch), math.rad(angle.Yaw)
    local forward = {X=math.cos(pitch)*math.cos(yaw), Y=math.cos(pitch)*math.sin(yaw), Z=math.sin(pitch)}
    local function step(origin, distance)
        return {X=origin.X+forward.X*distance, Y=origin.Y+forward.Y*distance, Z=origin.Z+forward.Z*distance}
    end
    local line, sphere = {}, {}
    local pipe = guest.EquipedItem
    local ignored = {guest}
    local endpoint = step(eye, 230)
    if trace:LineTraceSingleForObjects(pipe, eye, endpoint, {0,4}, true, ignored, 0, line, true, color, color, 0) then
        endpoint = (line.OutHit or line).ImpactPoint
        if (endpoint.X-eye.X)^2 + (endpoint.Y-eye.Y)^2 + (endpoint.Z-eye.Z)^2 <= 65^2 then return false, "near surface" end
    end
    local blocking = trace:SphereTraceMulti(pipe, eye, step(endpoint, -65), 65, 6, false, ignored, 0, sphere, true, color, color, 0)
    local count = 0
    for _, raw in ipairs(sphere.OutHits or sphere) do
        local wrapped, value = pcall(function() return raw:get() end)
        local hit = wrapped and value or raw
        local component = hit.Component and hit.Component:Get()
        if not valid(component) or not same(component:GetOwner(), target) or hit.bStartPenetrating then
            return false, (valid(component) and component:GetFName():ToString() or "missing component")
                .. " overlap=" .. tostring(hit.bStartPenetrating)
        end
        count = count + 1
    end
    return count > 0, ("sphere hits=%d, blocking=%s, out=%s"):format(count, tostring(blocking), type(sphere.OutHits))
end

local function stage_melee(world)
    local guest = authoritative_guest(world)
    local target = melee_target(world, true)
    local eye = guest.Camera:K2_GetComponentLocation()
    local yaw = math.rad(guest:K2_GetActorRotation().Yaw)
    assert(valid(target) and target.Alive and target:HasAuthority() and target.bReplicates, "stock melee target unavailable")
    local blockers = {}
    for _, range in ipairs({160, 195, 125}) do
        for offset = 0, 315, 45 do
            local angle = yaw + math.rad(offset)
            local center = {X=eye.X+range*math.cos(angle), Y=eye.Y+range*math.sin(angle), Z=eye.Z-24}
            if target:K2_SetActorLocation(center, false, {}, true) then
                local clear, status = melee_lane(guest, target, heading(eye, center))
                if clear then
                    target.Health = 7
                    target:ForceNetUpdate()
                    return ("%.7f,%.7f,%.7f;%s"):format(center.X, center.Y, center.Z, target:GetFullName())
                end
                blockers[status] = true
            end
        end
    end
    local failures = {}
    for status in pairs(blockers) do failures[#failures+1] = status end
    error("no clear stock Old Pipe contact lane: " .. table.concat(failures, ", "))
end

local shooter_paths = {
    state = "/Game/Classes/NPCs/NPC_State_Conscript.NPC_State_Conscript_C",
    bandit = "/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C",
}

local function spawn_stock_npc(pawn, world, kind)
    local path = assert(shooter_paths[kind], "unknown NPC firing fixture")
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    assert(valid(class), "stock NPC class unavailable")
    local origin = pawn:K2_GetActorLocation()
    local x = kind == "state" and 170 or 570
    local transform = {Translation={X=origin.X+x,Y=origin.Y+180,Z=origin.Z+120},
        Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
    local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
    assert(valid(actor) and same(actor:GetClass(), class) and same(actor:GetWorld(), world), "stock shooter spawn unavailable")
    assert(same(actor, statics:FinishSpawningActor(actor, transform, 0)) and actor:HasAuthority() and actor.Alive,
        "stock shooter did not start on the host")
    actor:SetActorTickEnabled(false)
    actor.CharacterMovement:DisableMovement()
    local controller = actor.Controller
    if valid(controller) then controller:SetActorTickEnabled(false) end
    if not valid(actor.Weapon_Ranged) then actor:Equip_RangedWeapon() end

    shooters[kind] = actor
    local weapon = actor.Weapon_Ranged
    assert(valid(weapon) and same(weapon.Instigator, actor) and weapon:HasAuthority() and weapon.Ammo > 0,
        "NPC did not equip its stock ranged weapon")
    return actor, weapon, path
end

local function fire_stock_npc(pawn, world, kind)
    local actor, weapon, path = spawn_stock_npc(pawn, world, kind)
    local muzzle = weapon.Arrow_Muzzle
    assert(valid(muzzle) and same(muzzle:GetOwner(), weapon), "stock NPC muzzle unavailable")
    local before = weapon.Ammo
    weapon:FireWeapon()
    assert(weapon.Ammo == before - 1, ("stock NPC trigger did not spend one round: %s to %s"):format(before, weapon.Ammo))
    local position = muzzle:K2_GetComponentLocation()
    return ("%d;%s;%.3f;%.3f;%.3f"):format(actor.Faction, weapon:GetClass():GetFullName():match(" (%S+)$"),
        position.X, position.Y, position.Z)
end

local function npc_kill(pawn)
    local shooter, victim = shooters.state, shooters.bandit
    assert(valid(shooter) and valid(victim) and shooter:HasAuthority() and victim:HasAuthority()
        and shooter.Alive and victim.Alive and shooter.bReplicates and victim.bReplicates,
        "the fixture needs two living authoritative replicated NPCs")
    local weapon = shooter.Weapon_Ranged
    local muzzle, capsule = weapon.Arrow_Muzzle, victim.CapsuleComponent
    assert(valid(muzzle) and same(muzzle:GetOwner(), weapon) and valid(capsule) and same(capsule:GetOwner(), victim),
        "the registered NPCs no longer have their stock firing components")
    local origin = muzzle:K2_GetComponentLocation()
    local blockers, aim = {}, nil
    for index = 0, 7 do
        local angle = index * math.pi / 4
        local position = {X=origin.X+300*math.cos(angle), Y=origin.Y+300*math.sin(angle), Z=origin.Z+40}
        if victim:K2_SetActorLocation(position, false, {}, true) then
            local target = capsule:K2_GetComponentLocation()
            local hit = {}
            local found = trace:LineTraceSingle(pawn, origin, target, 0, true, {shooter, weapon, pawn},
                0, hit, true, color, color, 0)
            hit = hit.OutHit or hit
            local component = hit.Component and hit.Component:Get()
            if found == true and valid(component) and same(component:GetOwner(), victim) then
                aim = heading(origin, target)
                break
            end
            blockers[#blockers + 1] = valid(component) and component:GetFullName() or "no blocking component"
        end
    end
    assert(aim, "no clear stock NPC firing lane: " .. table.concat(blockers, ", "))
    muzzle:K2_SetWorldRotation(aim, false, {}, true)
    -- Give the real shot a deterministic lethal threshold. The stock projectile
    -- and the victim's own damage/death scripts still resolve the hit.
    victim.Health = 1
    local before = weapon.Ammo
    weapon:FireWeapon()
    assert(weapon.Ammo == before - 1, "the stock NPC rifle did not fire")
    return victim:GetFullName()
end

local function npc_combat_state(world, host)
    local actor = host and shooters.bandit
    if not host then
        for _, candidate in ipairs(FindAllOf("NPC_Bandit_C") or {}) do
            if valid(candidate) and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then
                assert(not actor, "expected a single bandit in the isolated test")
                actor = candidate
            end
        end
    end
    if not valid(actor) then return "absent" end
    assert(actor.bReplicates and actor:GetLocalRole() == (host and 3 or 1), "incorrect stock NPC owner")
    local mesh = actor.Mesh
    assert(valid(mesh) and same(mesh:GetOwner(), actor), "missing combatant mesh")
    local markers = 0
    for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
        if valid(marker) and same(marker:GetOwner(), actor) and marker:GetLocalRole() == (host and 3 or 1) then
            markers = markers + 1
        end
    end
    local shooter = host and same(actor.LastDamagedMe, shooters.state) and "state" or "-"
    return ("%s;%s;%d;%s;%s"):format(tostring(actor.Alive), tostring(mesh:IsAnySimulatingPhysics()),
        markers, shooter, actor:GetFullName())
end

local turret_class = "/Game/Classes/NPCs/Turret_MechGun_01_BP.Turret_MechGun_01_BP_C"

local shot_turret, fired_count, fire_hook

local function turret_fire(pawn, world, turret, host, with_downed_target)
    assert(turret.Alive, "the test turret is already dead")
    local muzzle = turret.Arrow_Muzzle
    assert(valid(muzzle) and same(muzzle:GetOwner(), turret), "stock turret muzzle unavailable")
    local start = muzzle:K2_GetComponentLocation()
    local center = pawn:K2_GetActorLocation()
    local lane
    for index = 0, 7 do
        local angle = index * math.pi / 4
        local here = turret:K2_GetActorLocation()
        local reserve = {X=center.X+430*math.cos(angle), Y=center.Y+430*math.sin(angle), Z=center.Z+95}
        if turret:K2_SetActorLocation(reserve, false, {}, true) then
            start = muzzle:K2_GetComponentLocation()
            local found, results = {}, {}
            local library = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C")
            assert(valid(library), "stock turret LOS library unavailable")
            library:CheckForLOS(pawn, start, 0, true, {turret, turret:GetParentActor()}, 0, 0, turret,
                found, results, results, results, results)
            lane = found.TargetSpotted
            if lane then break end
            turret:K2_SetActorLocation(here, false, {}, true)
        end
    end
    assert(lane == true, "fixture could not verify the stock turret's line of sight")
    -- Verify firing and selection independently of player damage.
    muzzle:K2_SetWorldRotation({Pitch=70, Yaw=0, Roll=0}, false, {}, true)
    if not fire_hook then
        RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAttached", function(_, sound, attachment)
            if valid(shot_turret) and same(attachment:get(), shot_turret.Arrow_Muzzle)
                and same(sound:get(), shot_turret.FireSound) then fired_count = fired_count + 1 end
        end)
        fire_hook = true
    end
    local other
    if with_downed_target then
        assert(host, "only the host tracks downed targets")
        for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
            if valid(candidate) and other == nil and candidate:HasAuthority() and not candidate:IsLocallyControlled()
                and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then other = candidate end
        end
        assert(valid(other), "expected a guest to put at the head of the threat list")
        assert(type(project_downed) == "function", "the host probe needs the stock player-state projection")
        project_downed(other, "downed")
    end
    turret.Targets = other and {other, pawn} or {pawn}
    assert(turret.Targets:GetArrayNum() == (other and 2 or 1), "stock target list could not be prepared")
    shot_turret, fired_count = turret, 0
    local ok, issue = pcall(function()
        turret:FireRound()
        if other then
            assert(fired_count == 0 and turret.Targets:GetArrayNum() == 1 and same(turret.Targets[1], pawn),
                "the turret must skip the shot when its first target is downed")
            turret:FireRound()
        end
    end)
    if other then project_downed(other, "standing") end
    assert(ok, issue)
    local remaining = turret.Targets:GetArrayNum()
    local selected = remaining == 1 and same(turret.Targets[1], pawn)
    turret.Targets = {}
    shot_turret = nil
    return ("%d,%d,%s"):format(fired_count, remaining, tostring(selected))
end

local function turret_state(pawn, world, host, action)
    local turret = host and authority_turret
    if action == "turret-spawn" then
        assert(host and not valid(turret), "the isolated test turret must be spawned once on the host")
        local class = StaticFindObject(turret_class)
        if not valid(class) then LoadAsset(turret_class); class = StaticFindObject(turret_class) end
        assert(valid(class), "stock turret is unavailable")
        local at = pawn:K2_GetActorLocation()
        local transform = {Translation={X=at.X-340,Y=at.Y,Z=at.Z+120},
            Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
        turret = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
        assert(valid(turret) and same(turret:GetClass(), class) and same(turret:GetWorld(), world), "stock turret spawn unavailable")
        turret.bAlwaysRelevant = true
        assert(same(turret, statics:FinishSpawningActor(turret, transform, 0)) and turret:HasAuthority() and turret.Alive,
            "stock turret did not start on the host")
        turret:SetActorTickEnabled(false)
        assert(turret.bReplicates, "the stock turret did not enable native replication")
        turret:ForceNetUpdate()
        authority_turret = turret
        return "true"
    end
    if not host then
        for _, candidate in ipairs(FindAllOf("Turret_MechGun_01_BP_C") or {}) do
            if valid(candidate) and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then
                assert(not turret, "expected one test turret")
                turret = candidate
            end
        end
    end
    if not valid(turret) then return "absent" end
    assert(turret.bReplicates and turret:GetLocalRole() == (host and 3 or 1), "test turret not bound to authoritative channel")
    if action == "turret-fire" or action == "turret-retarget" then
        return turret_fire(pawn, world, turret, host, action == "turret-retarget")
    elseif action == "turret-kill" then
        assert(host and turret.Alive and turret.Health > 0, "only the host may destroy the live turret")
        local total, lethal = {}, {}
        turret:DealDamage({[damage_key]=2000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
        -- The stock turret reports remaining health in the TotalDamage out slot.
        assert(lethal.IsLethal == true and turret.Alive == false and turret.Health <= 0,
            ("stock turret stayed alive at %.2f HP, lethal=%s"):format(turret.Health, tostring(lethal.IsLethal)))
    end
    local gun = turret.Turret_Gun
    assert(valid(gun) and same(gun:GetOwner(), turret), "stock turret gun unavailable")
    local material = gun:GetMaterial(0)
    local burned = valid(material) and material:GetFName():ToString() == "Mat_PolygonMilitary_VehBurnt_01"
    return ("%s,%s,%s,%s"):format(tostring(turret.Alive), tostring(turret.Health <= 0), tostring(burned == true),
        tostring(turret:ActorHasTag(FName("Dead", EFindName.FNAME_Find))))
end

local function dock_worker(world, name)
    for _, actor in ipairs(FindAllOf("NPC_Survivor_Worker_Light_C") or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
            and actor:GetFName():ToString() == name then return actor end
    end
end

local function dock_combat(pawn, world, host, action)
    local actor = dock_worker(world, "NPC_Survivor_Worker_C_1")
    if not valid(actor) then return "absent" end
    assert(actor.bReplicates and actor:GetLocalRole() == (host and 3 or 1), "the stock Dock worker needs one authority")
    if action == "dock-stage" then
        assert(host and actor.Alive and actor.Health > 0, "the test requires the placed, living host worker")
        local point = gun_target(pawn, world)
        assert(actor:K2_SetActorLocation(point, false, {}, true), "the worker could not move into the firing lane")
        actor.CharacterMovement:DisableMovement()
        actor:SetActorTickEnabled(false)
        if valid(actor.Controller) then actor.Controller:SetActorTickEnabled(false) end
        actor.Health = 1
        actor:ForceNetUpdate()
        return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
    end
    local markers = 0
    for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
        if valid(marker) and same(marker:GetOwner(), actor) and marker:GetLocalRole() == (host and 3 or 1) then
            markers = markers + 1
        end
    end
    local location = actor:K2_GetActorLocation()
    local eyes = ("%0.7f,%0.7f,%0.7f"):format(location.X, location.Y, location.Z)
    return ("%s;%s;%d;%s;%s"):format(tostring(actor.Alive), tostring(actor.Mesh:IsAnySimulatingPhysics()), markers,
        eyes, actor:GetFullName())
end

local attack_probe
local attack_probe_hooked = false
local dock_attacker = "NPC_Survivor_Worker_C_0"
local montage_probe, montage_probe_hooked
local live_worker = "NPC_Survivor_Worker_C_2"
local live_probe, live_probe_hooked

local function dock_fight_position(pawn, actor, ignored, distance, shot_lane)
    local center = actor:K2_GetActorLocation()
    local half_height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    for offset = 0, 11 do
        local angle = offset * math.pi / 6
        local x, y = center.X + math.cos(angle) * distance, center.Y + math.sin(angle) * distance
        local hit = {}
        if trace:LineTraceSingle(pawn, {X=x, Y=y, Z=center.Z+100}, {X=x, Y=y, Z=center.Z-175},
            0, false, ignored, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            local surface = hit.Component and hit.Component:Get()
            if valid(surface) and surface:GetCollisionObjectType() == 0 and hit.ImpactNormal.Z >= .75 then
                local point = {X=x, Y=y, Z=hit.ImpactPoint.Z + half_height + 3}
                local eye = {X=x, Y=y, Z=point.Z + half_height * .65}
                local obstructed = trace:LineTraceSingle(pawn, eye, center, 0, true, ignored, 0, {},
                    true, color, color, 0)
                if (not shot_lane or not obstructed) and pawn:K2_TeleportTo(point, heading(eye, center)) then
                    return pawn:K2_GetActorLocation()
                end
            end
        end
    end
end

local function stage_live_worker(pawn, world)
    local actor = dock_worker(world, live_worker)
    assert(valid(actor) and actor.Alive and not valid(actor.CombatTarget) and actor.Health > 1
        and actor:HasAuthority() and valid(actor.Controller), "expected an untouched warehouse worker with its stock brain")
    local guest = authoritative_guest(world)
    local original = actor:K2_GetActorLocation()
    local ignored = {pawn, guest, actor}
    assert(dock_fight_position(pawn, actor, ignored, 1800, false), "no host position away from the warehouse fight")
    local guest_point = assert(dock_fight_position(guest, actor, ignored, 350, true), "no clear guest shot in the warehouse")
    live_probe = {actor = actor, guest = guest, host = pawn, enemy_health = actor.Health,
        guest_health = guest.Save.PlayerHealth, guest_attacks = 0, host_attacks = 0}
    if not live_probe_hooked then
        RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage", function(context, _, _, instigator)
            if not live_probe or not same(instigator:get(), live_probe.actor) then return end
            local victim = context:get()
            if same(victim, live_probe.guest) then live_probe.guest_attacks = live_probe.guest_attacks + 1 end
            if same(victim, live_probe.host) then live_probe.host_attacks = live_probe.host_attacks + 1 end
        end)
        live_probe_hooked = true
    end
    return ("%.7f,%.7f,%.7f;%.7f,%.7f,%.7f"):format(original.X, original.Y, original.Z,
        guest_point.X, guest_point.Y, guest_point.Z)
end

local function sample_live_worker()
    local probe = assert(live_probe, "the warehouse fight has not started")
    local actor, guest = probe.actor, probe.guest
    assert(valid(actor) and valid(guest), "the warehouse participant disappeared")
    local target = actor.CombatTarget
    local chosen = same(target, guest) and "guest" or same(target, probe.host) and "host"
        or valid(target) and target:GetFullName() or "-"
    return ("%s;%s;%.3f;%.3f;%s;%d;%d;%.3f;%.3f"):format(
        tostring(actor.Alive), tostring(same(actor.LastDamagedMe, guest)), probe.enemy_health, actor.Health,
        chosen, probe.guest_attacks, probe.host_attacks, probe.guest_health, guest.Save.PlayerHealth)
end

local function live_worker_position(world)
    local actor = dock_worker(world, live_worker)
    if not valid(actor) or not actor.Alive then return "absent" end
    local point = actor:K2_GetActorLocation()
    return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
end

local function dock_swing(world, host, action)
    local actor = dock_worker(world, dock_attacker)
    assert(valid(actor), "the swing probe requires the mapped worker")
    assert(actor.Alive and actor.bReplicates and actor:GetLocalRole() == (host and 3 or 1),
        ("the swing worker has Alive=%s, bReplicates=%s, role=%s"):format(
            tostring(actor.Alive), tostring(actor.bReplicates), actor:GetLocalRole()))
    local animator = actor.Mesh:GetAnimInstance()
    assert(valid(animator), "the stock worker animator is unavailable")
    if action == "dock-arm-swing" then
        montage_probe = {animator=animator, count=0, clip="-", rate=0}
        if not montage_probe_hooked then
            RegisterHook("/Script/Engine.AnimInstance:PlaySlotAnimationAsDynamicMontage",
                function(context, asset, slot, _fade_in, _fade_out, rate)
                    if not montage_probe or not same(context:get(), montage_probe.animator)
                        or slot:get():ToString() ~= "DefaultSlot" then return end
                    local clip = asset:get()
                    montage_probe.count = montage_probe.count + 1
                    montage_probe.clip = clip:GetFullName()
                    montage_probe.rate = rate:get()
                end)
            montage_probe_hooked = true
        end
        return "ready"
    end
    if action == "dock-play-swing" then
        assert(host, "the original swing runs on the authoritative actor")
        actor.CombatTarget = nil
        local clip = actor.Anim_Melee[1]
        assert(valid(clip), "the worker has no configured swing clip")
        actor:Play_AnimInterupt(true, false, clip, 1.1, .2)
        return clip:GetFullName()
    end
    assert(montage_probe and same(montage_probe.animator, animator), "the swing probe is unarmed")
    local montage = animator:GetCurrentActiveMontage()
    local playing = valid(montage) and animator:Montage_IsPlaying(montage) == true
    return ("%d;%.3f;%s;%s"):format(montage_probe.count, montage_probe.rate, tostring(playing), montage_probe.clip)
end

local function observe_strike(actor)
    if not attack_probe_hooked then
        RegisterHook("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:Lunge_CoolDown", function(context)
            if attack_probe and same(context:get(), attack_probe.actor) then
                attack_probe.calls = attack_probe.calls + 1
            end
        end)
        attack_probe_hooked = true
    end
    local sample = {actor=actor, calls=0}
    attack_probe = sample
    actor:ResolveMelee()
    attack_probe = nil
    return sample.calls
end

local function place_striker(actor, guest)
    local center = guest:K2_GetActorLocation()
    local radius = math.min(actor.Melee_SweepRange - 12, 135)
    assert(radius > 75, "stock worker melee range is too short for the test lane")
    actor.CharacterMovement:DisableMovement()
    actor:SetActorTickEnabled(false)
    if valid(actor.Controller) then actor.Controller:SetActorTickEnabled(false) end
    for step = 0, 7 do
        local angle = step * math.pi / 4
        local point = {X=center.X+radius*math.cos(angle), Y=center.Y+radius*math.sin(angle), Z=center.Z}
        if actor:K2_SetActorLocation(point, false, {}, true) then
            actor:K2_SetActorRotation(heading(point, center), true)
            local hit = {}
            local found = trace:LineTraceSingle(actor, point, center, 0, false, {}, 0, hit, true, color, color, 0)
            hit = hit.OutHit or hit
            local component = hit.Component and hit.Component:Get()
            if found and valid(component) and same(component:GetOwner(), guest) then
                actor:ForceNetUpdate()
                return
            end
        end
    end
    error("no clear strike lane to the authoritative guest")
end

local function dock_strike(pawn, world, host)
    local actor = dock_worker(world, dock_attacker)
    if not valid(actor) then return "absent" end
    assert(actor.bReplicates and actor:GetLocalRole() == (host and 3 or 1) and actor.Alive,
        "the test requires the living placed worker")
    if not host then
        local prior = actor.CombatTarget
        actor.CombatTarget = pawn
        local calls = observe_strike(actor)
        actor.CombatTarget = valid(prior) and prior or nil
        return tostring(calls)
    end
    local guest = authoritative_guest(world)
    place_striker(actor, guest)
    actor.TargetList = {}
    actor.CombatTarget = nil
    actor:AddTargetToList(guest, {})
    local selection = {}
    actor:GetNewCombatTarget(selection)
    assert(selection.NewTargetFound == true and same(actor.CombatTarget, guest),
        "the stock threat selector did not pick the standing guest")
    local host_before, before = pawn.Save.PlayerHealth, guest.Save.PlayerHealth
    assert(observe_strike(actor) == 1, "the host stock melee did not run once")
    local after = guest.Save.PlayerHealth
    assert(after > 0 and after < before and pawn.Save.PlayerHealth == host_before,
        ("the stock strike did not damage the guest alone: %s to %s"):format(before, after))

    actor:AddTargetToList(pawn, {})
    project_downed(guest, "downed")
    local retargeted = same(actor.CombatTarget, pawn)
    actor:Agro_Buffer(guest, true, true, true, true)
    local stayed_on_host = same(actor.CombatTarget, pawn)
    actor.CombatTarget = guest
    local queued = observe_strike(actor)
    actor.CombatTarget = pawn
    project_downed(guest, "standing")
    assert(retargeted and stayed_on_host and queued == 0 and guest.Save.PlayerHealth == after,
        ("downed checks failed: retarget=%s ignore=%s strike=%s"):format(tostring(retargeted), tostring(stayed_on_host), queued))
    return ("%.6f;%.6f;%.6f;%s"):format(host_before, before, after, actor:GetFullName())
end

local function run(action, actor_path)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local at_dock = action:match("^dock%-") ~= nil
    if action == "dock-natural-stop" and (not valid(world) or world:GetFullName() ~= "World " .. dock) then
        live_probe = nil
        return "stopped"
    end
    assert(valid(world) and world:GetFullName() == "World " .. (at_dock and dock or map) and valid(world.NetDriver),
        "the test requires the expected world in an isolated co-op pair")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "local stock pawn unavailable")
    local host = pawn:HasAuthority()
    if action == "dock-equip" then
        assert(not host, "the guest must select their stock equipment")
        return select_equipment(pawn, function(row)
            return row[item_class]:GetFName():ToString() == "Wep_RustyPistol_C"
        end)
    elseif action == "dock-block-weapon" or action == "dock-unblock-weapon" then
        assert(not host and pawn.Dead == false and valid(pawn.EquipedItem), "the guest needs a live equipped pawn")
        local blocked = action == "dock-block-weapon"
        pawn.IsWeaponBlocked = blocked
        return tostring(pawn.IsWeaponBlocked)
    elseif action == "dock-natural-stage" then
        assert(host, "the test stages the stock host NPC")
        return stage_live_worker(pawn, world)
    elseif action == "dock-natural-position" then
        return live_worker_position(world)
    elseif action == "dock-natural-seat" then
        local point = pawn:K2_GetActorLocation()
        return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
    elseif action == "dock-natural-sample" then
        assert(host, "the host owns the fight")
        return sample_live_worker()
    elseif action == "dock-natural-stop" then
        assert(host, "only the host can stop the test NPC")
        local actor = live_probe and live_probe.actor
        if valid(actor) and actor.Alive then
            if valid(actor.Controller) then
                actor.Controller:StopMovement()
                actor.Controller:SetActorTickEnabled(false)
            end
            actor.CharacterMovement:DisableMovement()
            actor:SetActorTickEnabled(false)
        end
        live_probe = nil
        return "stopped"
    elseif action == "dock-aim" then
        local shooter = host and authoritative_guest(world) or pawn
        local angle = heading(shooter.Camera:K2_GetComponentLocation(), position(actor_path))
        shooter.Controller:SetControlRotation(angle)
        if host then shooter.Camera:K2_SetWorldRotation(angle, false, {}, true) end
        return "ready"
    elseif action == "dock-arm-swing" or action == "dock-play-swing" or action == "dock-read-swing" then
        return dock_swing(world, host, action)
    elseif action == "dock-disarm-swing" then
        montage_probe = nil
        return "disarmed"
    elseif action == "dock-strike" then
        return dock_strike(pawn, world, host)
    elseif action == "dock-stage" or action == "dock-worker" then
        return dock_combat(pawn, world, host, action)
    end
    if action == "fire-npc" then
        assert(host, "NPC shots must start on the host")
        return fire_stock_npc(pawn, world, actor_path)
    elseif action == "npc-kill" then
        assert(host, "NPC kills must start on the host")
        return npc_kill(pawn)
    elseif action == "npc-state" then
        return npc_combat_state(world, host)
    elseif action == "seed-pipe" then
        assert(host, "only the host can grant the test Old Pipe")
        spawn_stock_npc(pawn, world, "state")
        return seed_pipe(world)
    elseif action == "select-pipe" then
        assert(not host, "the guest must pick their test equipment")
        return select_equipment(pawn, function(row) return guid_of(row) == actor_path end)
    elseif action == "stage-melee" then
        assert(host, "only the host can stage the stock target")
        return stage_melee(world)
    elseif action == "face-melee" then
        assert(not host, "the guest must drive the test swing")
        local angle = heading(pawn.Camera:K2_GetComponentLocation(), position(actor_path))
        pawn.Controller:SetControlRotation(angle)
        return "ready"
    elseif action == "align-melee" then
        assert(host, "the test needs the host's replica camera")
        local guest = authoritative_guest(world)
        local angle = heading(guest.Camera:K2_GetComponentLocation(), position(actor_path))
        guest.Controller:SetControlRotation(angle)
        guest.Camera:K2_SetWorldRotation(angle, false, {}, true)
        local clear, status = melee_lane(guest, melee_target(world, true), guest.Camera:K2_GetComponentRotation())
        return clear and "true" or status
    elseif action == "melee-state" then
        local target = melee_target(world, host, position(actor_path))
        if not valid(target) then return "absent" end
        return ("%s;%s;%.3f;%s"):format(tostring(target.Alive), tostring(target.Mesh:IsAnySimulatingPhysics()),
            target.Health, target:GetFullName())
    elseif action == "melee-contact" then
        assert(host, "the authoritative contact runs on the host")
        return SilverfishReadNativeGuestMeleeContactStatus()
    elseif action:match("^turret%-") then
        return turret_state(pawn, world, host, action)
    end
    local class = StaticFindObject(class_path)
    local drone
    local spawn = action == "spawn" or action == "spawn-guest-target"
    if not host or action == "census" or spawn then
        for _, candidate in ipairs(FindAllOf("NPC_Drone_C") or {}) do
            if valid(candidate) and not candidate:IsActorBeingDestroyed() and same(candidate:GetWorld(), world) then
                assert(not drone, "unexpected stock drone already in the test world")
                drone = candidate
            end
        end
    end
    if action == "census" then return drone and drone:GetFullName() or "empty" end
    if spawn then
        assert(host and not drone and not valid(authority_drone), "only the isolated host may spawn one fixture drone")
        if not valid(class) then LoadAsset(class_path); class = StaticFindObject(class_path) end
        assert(valid(class), "stock drone class unavailable")
        local origin = pawn:K2_GetActorLocation()
        local point = action == "spawn-guest-target" and gun_target(pawn, world)
            or {X=origin.X+180,Y=origin.Y,Z=origin.Z+180}
        local transform = {Translation=point,
            Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
        local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
        assert(valid(actor) and same(actor:GetClass(), class) and same(actor:GetWorld(), world), "stock drone spawn unavailable")
        actor.bAlwaysRelevant = true
        assert(same(actor, statics:FinishSpawningActor(actor, transform, 0)) and actor:GetLocalRole() == 3,
            "stock drone did not finish on the authority")
        if action == "spawn-guest-target" then actor.Health = 1 end
        actor:SetReplicates(true)
        actor:SetReplicateMovement(true)
        actor:ForceNetUpdate()
        assert(actor.bReplicates and actor.Health > 0, "fixture drone is not alive and replicated")
        authority_drone = actor
        return actor:GetFullName():match("^NPC_Drone_C (.+)$")
    end

    assert(actor_path and actor_path:match("^" .. map:gsub("%.", "%%.") .. ":PersistentLevel%.NPC_Drone_C_%d+$"),
        "expected the exact test drone's path")
    -- Unreal may give a dynamically replicated actor a different name on the guest.
    local actor = host and StaticFindObject(actor_path) or drone
    if not valid(actor) or actor:IsActorBeingDestroyed() then return "absent" end
    assert(same(actor:GetClass(), class) and same(actor:GetWorld(), world) and actor.bReplicates
        and actor:GetLocalRole() == (host and 3 or 1), "expected the enrolled stock drone")
    if host then assert(same(actor, authority_drone), "the target is outside the fixture") end
    local before = actor.Health
    if action == "observe" then return ("%.6f"):format(before) end
    if action == "guest-aim" then
        assert(not host and valid(pawn.Controller) and valid(pawn.Camera), "only the guest may aim its camera")
        local target = actor.CapsuleComponent:K2_GetComponentLocation()
        local aim = heading(pawn.Camera:K2_GetComponentLocation(), target)
        pawn.Controller:SetControlRotation(aim)
        return ("%.2f,%.2f"):format(aim.Yaw, aim.Pitch)
    end
    if action == "view" then
        local shooter = pawn
        if host then
            for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
                if valid(candidate) and not candidate:IsActorBeingDestroyed() and not candidate:IsLocallyControlled()
                    and same(candidate:GetWorld(), world) then shooter = candidate end
            end
            assert(shooter ~= pawn, "authoritative guest pawn unavailable")
        end
        local muzzle = shooter.EquipedItem.Arrow_Muzzle
        assert(valid(muzzle) and same(muzzle:GetOwner(), shooter.EquipedItem), "guest stock muzzle unavailable")
        local camera = shooter.Camera:K2_GetComponentRotation()
        local base = shooter:GetBaseAimRotation()
        local barrel = muzzle:K2_GetComponentRotation()
        local target = actor.CapsuleComponent:K2_GetComponentLocation()
        local needed = heading(muzzle:K2_GetComponentLocation(), target)
        return ("base=%.2f/%.2f camera=%.2f/%.2f muzzle=%.2f/%.2f target=%.2f/%.2f"):format(
            base.Yaw, base.Pitch, camera.Yaw, camera.Pitch, barrel.Yaw, barrel.Pitch, needed.Yaw, needed.Pitch)
    end
    if action == "death" then
        local mesh = actor.Mesh
        assert(valid(mesh) and same(mesh:GetOwner(), actor), "stock drone mesh unavailable")
        local markers = 0
        for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
            if valid(marker) and same(marker:GetOwner(), actor) and marker:GetLocalRole() == (host and 3 or 1) then
                markers = markers + 1
            end
        end
        return ("%s,%s,%d"):format(tostring(actor.Alive), tostring(mesh:IsAnySimulatingPhysics()), markers)
    end

    assert((action == "guest-damage" and not host) or (action == "host-damage" and host), "wrong damage test seat")
    assert(before > 0, "stock drone was already dead")
    local total, lethal = {}, {}
    actor:DealDamage({[damage_key]=1000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
    if not host then
        assert(total.TotalDamage == 0 and lethal.IsLethal == false and actor.Health == before and actor.Alive,
            ("stock drone override dealt damage locally: %s %s (%.2f to %.2f)"):format(
                tostring(total.TotalDamage), tostring(lethal.IsLethal), before, actor.Health))
        return "damage denied"
    end
    assert(total.TotalDamage > 0 and lethal.IsLethal == true, "authority was unable to resolve a stock drone kill")
    return ("%.6f"):format(total.TotalDamage)
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(400)
    request:close()
    local ticket, action, actor_path = (data or ""):match("^(%x+)\t([a-z%-]+)\t?([^\r\n]*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, actor_path)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
