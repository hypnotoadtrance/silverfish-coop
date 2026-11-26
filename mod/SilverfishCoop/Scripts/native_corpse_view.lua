local corpses = {}
local effects = require("native_anomaly_effects")
local actor_reference = require("replicated_actor_reference")
local turret_fire = require("native_turret_fire_view")

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local drone_path = "/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C"
local turret_path = "/Game/Classes/NPCs/NPC_Assets/Turret_Master_BP.Turret_Master_BP_C"
local turret_burnt = "/Game/Assets/Materials/Mat_Trimsheets/Mat_PolygonMilitary_VehBurnt_01.Mat_PolygonMilitary_VehBurnt_01"
local turret_smoke = "/Game/Assets/PFX/NI_SmokeStack_Large.NI_SmokeStack_Large"
local marker_path = "/Script/Engine.TargetPoint"
local npc_class, turret_class, marker_class, clock, death_hook, drone_death_hook, turret_death_hook, drone_seen
local next_turret_lookup = 0
local active_world, markers, numbered, mirrors, ended, anchors = nil, {}, {}, {}, {}, {}
local pending, corrections, observed_grips = {}, {}, {}
local turret_timers = {}
local extinguish_hook
local next_sample, corpse_number = 0, 0
local host_pawn, grip_marker, next_grip_sample
local sample_period = .1
local grip_period = .05
local settle_timeout = 2
local settle_stationary = .25
local settle_contacts = .35
local stopped = {X = 0, Y = 0, Z = 0}
local byte_angle = 360 / 256
local anchor_limit = 120
local host_grip_bit = 0x8000
local grip_class_path = "/Script/Engine.PhysicsHandleComponent"
local grip_transform = {
    Rotation = {X = 0, Y = 0, Z = 0, W = 1},
    Translation = {X = 0, Y = 0, Z = 0},
    Scale3D = {X = 1, Y = 1, Z = 1},
}
local blast_phase, smoke_phase = 32, 16
local fire_flag = 4

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_drone(actor)
    local class = actor:GetClass()
    return class:GetFName():ToString() == "NPC_Drone_C" and class:GetFullName() == "BlueprintGeneratedClass " .. drone_path
end

local function supported_corpse(actor)
    return valid(actor) and ((valid(npc_class) and actor:IsA(npc_class)) or is_drone(actor))
end

local function supported_turret(actor)
    return valid(actor) and valid(turret_class) and actor:IsA(turret_class)
end

local function world_driver(actor, guest)
    local world = valid(actor) and actor:GetWorld()
    local driver = valid(world) and world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) ~= guest then return end
    return world
end

local function rotation_byte(angle)
    return math.floor(angle / byte_angle + .5) % 256
end

local function anchor_code(pitch)
    local code = rotation_byte(pitch)
    if code <= 60 then return code end
    if code >= 196 then return code - 135 end
    return 0
end

local function anchor_pitch(index)
    local code = (index or -1) + 1
    -- Stay within the canonical rotator's pitch range so yaw and roll retain
    -- the corpse ID after Unreal converts through a quaternion.
    return (code <= 60 and code or code - 121) * byte_angle
end

local function anchor_index(actor)
    local mesh, address = actor.Mesh, actor:GetAddress()
    local mirror = mirrors[address]
    if mirror and valid(mirror.marker) then
        local index = anchor_code(mirror.marker:K2_GetActorRotation().Pitch) - 1
        return index >= 0 and index < mesh:GetNumBones() and index or nil
    end
    if anchors[address] then return anchors[address] end
    local pelvis = FName("pelvis", EFindName.FNAME_Find)
    local index = mesh:GetBoneIndex(pelvis)
    if index >= 0 and index < anchor_limit and mesh:IsSimulatingPhysics(pelvis) then
        anchors[address] = index
        return index
    end
    -- ResolveDeath can fire before the bodies begin simulating. Retry the
    -- selection on later samples and publish the chosen bone to all observers.
    for candidate = 0, math.min(anchor_limit - 1, mesh:GetNumBones() - 1) do
        if mesh:IsSimulatingPhysics(mesh:GetBoneName(candidate)) then
            anchors[address] = candidate
            return candidate
        end
    end
end

function corpses.position(actor)
    local mesh, index = actor.Mesh, anchor_index(actor)
    if index then
        return mesh:GetSocketLocation(mesh:GetBoneName(index)), index
    end
    return mesh:K2_GetComponentLocation(), nil
end

local function marker_state(rotation)
    -- Yaw and roll carry a world-local ID and the host's grip state.
    -- The marker's Owner remains the replicated corpse.
    local packed = rotation_byte(rotation.Roll) * 256 + rotation_byte(rotation.Yaw)
    return packed & (host_grip_bit - 1), (packed & host_grip_bit) ~= 0
end

local function marker_number(marker)
    local number = marker_state(marker:K2_GetActorRotation())
    return number
end

function corpses.binding(actor, guest)
    if not supported_corpse(actor)
        or not same(world_driver(actor, guest), active_world) or actor.Alive ~= false
        or actor:IsActorBeingDestroyed() then return end
    local address = actor:GetAddress()
    local mirror = guest and mirrors[address]
    local marker = guest and mirror and mirror.marker or not guest and markers[address]
    local mesh = actor.Mesh
    if not valid(marker) or not same(marker:GetOwner(), actor) or not same(active_world, marker:GetWorld())
        or not valid(mesh) or not same(mesh:GetOwner(), actor) or not mesh:IsAnySimulatingPhysics() then return end
    local number = marker_number(marker)
    if number > 0 then return number, mesh end
end

function corpses.authoritative(number)
    local marker = numbered[number]
    local actor = valid(marker) and marker:GetOwner()
    local identifier, mesh = corpses.binding(actor, false)
    if identifier == number then return actor, mesh end
end

local function between(point, target)
    local x, y, z = target.X - point.X, target.Y - point.Y, target.Z - point.Z
    return {X = x, Y = y, Z = z}, x * x + y * y + z * z
end

local function retire_host_fire(fire)
    if not valid(fire) then return end
    local turret = fire:GetAttachParentActor()
    local timer = valid(turret) and turret_timers[turret:GetAddress()]
    if timer and same(timer.fire, fire) then timer.fire = nil end
end

local function find_stock_turret_fire(turret)
    local fire = turret_fire.attached(turret)
    if valid(fire) and not extinguish_hook then
        extinguish_hook = turret_fire.hook_extinguish(retire_host_fire)
    end
    return fire
end

local function publish(actor)
    local world = world_driver(actor, false)
    if not same(world, active_world) or not actor:HasAuthority() or actor.bReplicates ~= true or actor.Alive ~= false
        or actor:IsActorBeingDestroyed() or not valid(marker_class) then return end
    local address = actor:GetAddress()
    if valid(markers[address]) and same(markers[address]:GetOwner(), actor) then return end
    -- The owner reference resolves to the same NPC over each connection even
    -- when Unreal assigns a different local name to the replicated actor.
    local point, index, fire
    local turret = supported_turret(actor)
    if turret then
        point = actor:K2_GetActorLocation()
        fire = find_stock_turret_fire(actor)
    else
        if not supported_corpse(actor) or not valid(actor.Mesh) then return end
        point, index = corpses.position(actor)
    end
    if corpse_number == host_grip_bit - 1 then return end
    corpse_number = corpse_number + 1
    local marker = world:SpawnActor(marker_class, point, {
        Pitch = turret and (blast_phase + (valid(fire) and fire_flag or 0)) * byte_angle or anchor_pitch(index),
        Yaw = (corpse_number % 256) * byte_angle, Roll = (corpse_number // 256) * byte_angle,
    })
    if not valid(marker) then return end
    marker:SetOwner(actor)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker.NetUpdateFrequency = 10
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    markers[address], numbered[corpse_number] = marker, marker
    if turret then
        turret_timers[address] = {blast_until = clock:GetRealTimeSeconds(world) + 1,
            smoke_until = clock:GetTimeSeconds(world) + 30, fire = fire, fire_pending = not valid(fire)}
    end
end

local function apply_destroyed_turret(actor)
    local gun, burnt = actor.Turret_Gun, StaticFindObject(turret_burnt)
    if not valid(gun) or not same(gun:GetOwner(), actor) or not valid(burnt) then return end
    -- Stock Die also changes reputation and spawns damaging explosion and fire
    -- actors. The replicated turret only presents the terminal state.
    actor.Alive = false
    actor.Health = 0
    actor.Search = false
    actor.FireGate = false
    gun:SetMaterial(0, burnt)
    local dead = FName("Dead", EFindName.FNAME_Find)
    if not actor:ActorHasTag(dead) then
        local tags, saved = {}, actor.Tags
        for i = 1, saved:GetArrayNum() do tags[i] = saved[i] end
        tags[#tags + 1] = dead
        actor.Tags = tags
    end
    return true
end

local function retire_turret(mirror)
    if not mirror or not mirror.turret then return end
    if valid(mirror.smoke) then mirror.smoke:Deactivate() end
    turret_fire.stop(mirror)
end

local function present_turret(mirror, actor, pawn)
    local marker = mirror.marker
    -- UE4SS SpawnActor and K2_SetActorRotation can deliver opposite pitch signs.
    local flags = rotation_byte(math.abs(marker:K2_GetActorRotation().Pitch))
    if flags == 0 then
        retire_turret(mirror)
        return true
    end
    local phase = flags & ~fire_flag
    if (flags & fire_flag) ~= 0 then
        if not mirror.fire_started then pcall(turret_fire.start, actor, mirror) end
    else
        turret_fire.stop(mirror)
    end
    if phase == blast_phase and not mirror.played_blast and valid(pawn) and pawn:IsLocallyControlled() then
        local ok, played = pcall(effects.play_turret_blast, pawn, marker:K2_GetActorLocation())
        mirror.played_blast = ok and played
    end
    if phase == 0 and valid(mirror.smoke) then
        mirror.smoke:Deactivate()
        mirror.smoke = nil
    end
    if mirror.smoke_started or phase ~= blast_phase and phase ~= smoke_phase then return end
    local gun = actor.Turret_Gun
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local asset = StaticFindObject(turret_smoke)
    if not valid(gun) or not same(gun:GetOwner(), actor) or not valid(niagara) or not valid(asset) then return end
    mirror.smoke = niagara:SpawnSystemAttached(asset, gun, FName("None"),
        {X = 0, Y = 0, Z = 0}, {Pitch = 0, Yaw = 0, Roll = 0}, 0, true, true, 0, false)
    mirror.smoke_started = true
end

local function apply(marker, world)
    if not valid(marker) or not same(marker:GetWorld(), world) then return true end
    if actor_reference.is_reference(marker) then return true end
    local actor = marker:GetOwner()
    if not valid(actor) or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return true end
    local body = supported_corpse(actor)
    if not body and not valid(turret_class) then turret_class = StaticFindObject(turret_path) end
    local turret = not body and supported_turret(actor)
    if not body and not turret then return true end
    -- The owner, simulated role and first movement update can arrive in separate frames.
    if marker:GetLocalRole() ~= 1 or actor:GetLocalRole() ~= 1 or not same(world_driver(marker, true), world)
        or marker_number(marker) == 0 then return end
    if turret then
        if not apply_destroyed_turret(actor) then return end
        local key = actor:GetAddress()
        if not mirrors[key] or not same(mirrors[key].marker, marker) then
            retire_turret(mirrors[key])
            mirrors[key] = {marker = marker, turret = true}
        end
        return true
    end
    local mesh, capsule, movement = actor.Mesh, actor.CapsuleComponent, actor.CharacterMovement
    if not valid(mesh) or not valid(capsule) or not valid(movement) then return end
    local key = actor:GetAddress()
    local mirror = mirrors[key] or {}
    mirror.marker = marker
    mirrors[key] = mirror
    actor.Health = 0
    if actor.Alive == false and mesh:IsAnySimulatingPhysics() then return true end
    movement:SetMovementMode(0, 0)
    capsule:SetCollisionEnabled(0)
    mesh:K2_DetachFromComponent(1, 1, 1, false)
    mesh:SetCollisionProfileName(FName("Ragdoll", EFindName.FNAME_Find), true)
    mesh:SetCollisionEnabled(3)
    -- Local physics presents the fall. The host retains death, loot, AI and saves.
    -- Keep the stock ragdoll's Pawn response. The visibility trace still
    -- selects a bone without the observer colliding with player capsules.
    mesh:SetCollisionResponseToChannel(3, 2)
    mesh:SetSimulatePhysics(true)
    mesh:SetAnimationMode(2, true)
    actor.Alive = false
    if is_drone(actor) then
        local engine_sound = actor.EngineSound
        if valid(engine_sound) and same(engine_sound:GetOwner(), actor) then engine_sound:Stop() end
    else
        actor.Agro = false
    end
    actor.Tags = {FName("Dead", EFindName.FNAME_Find)}
    return true
end

local function locally_carried(pawn, actor, mesh)
    local grip = valid(pawn) and same(pawn.HeldObject, actor) and pawn.PhysicsHandle
    return valid(grip) and same(grip:GetGrabbedComponent(), mesh)
end

local function stop_grip_motion(mesh)
    mesh:SetAllPhysicsLinearVelocity(stopped, false)
    mesh:SetAllPhysicsAngularVelocityInRadians(stopped, false)
end

local function restore_grip_collision(grip)
    if not grip.responses or not valid(grip.mesh) then return end
    for channel, response in pairs(grip.responses) do
        grip.mesh:SetCollisionResponseToChannel(channel, response)
    end
    grip.responses = nil
end

local function restore_corpse_physics(mirror, released_by_host)
    local original = mirror.host_grip
    if not original then return end
    mirror.host_grip = nil
    if valid(original.handle) then
        original.handle:ReleaseComponent()
        if valid(original.actor) then original.handle:K2_DestroyComponent(original.actor) end
    end
    if released_by_host and valid(original.mesh) then
        stop_grip_motion(original.mesh)
    end
    restore_grip_collision(original)
end

local function project_host_grip(mirror, actor, mesh)
    if mirror.host_grip then return mirror.host_grip end
    local class = StaticFindObject(grip_class_path)
    local point, index = corpses.position(actor)
    if not valid(class) or not index then return end
    local bone = mesh:GetBoneName(index)
    if not mesh:IsSimulatingPhysics(bone) then return end
    local handle = actor:AddComponentByClass(class, true, grip_transform, false)
    if not valid(handle) then return end
    local responses = {}
    local rotation = mesh:GetSocketRotation(bone)
    mirror.host_grip = {actor = actor, mesh = mesh, responses = responses, handle = handle, bone = bone, rotation = rotation}
    -- Keep the skeletal bodies in the physics scene so the guest still sees the
    -- ragdoll pose. The host solves contacts while the guest follows its position.
    for channel = 0, 31 do
        if channel ~= 3 then -- Preserve the stock visibility trace.
            local response = mesh:GetCollisionResponseToChannel(channel)
            if response ~= 0 then
                responses[channel] = response
                mesh:SetCollisionResponseToChannel(channel, 0)
            end
        end
    end
    stop_grip_motion(mesh)
    handle:SetInterpolationSpeed(20)
    handle:GrabComponentAtLocationWithRotation(mesh, bone, point, rotation)
    if not same(handle:GetGrabbedComponent(), mesh) then
        restore_corpse_physics(mirror)
        return
    end
    return mirror.host_grip
end

local function settle_host_release(grip, target, now)
    if not valid(grip.handle) or not valid(grip.mesh) or not grip.mesh:IsSimulatingPhysics(grip.bone) then return false end
    local anchor = grip.mesh:GetSocketLocation(grip.bone)
    local release = grip.release
    if not release then
        -- Follow the falling host anchor with a free pivot. Allow ground
        -- contact once the host settles, while the handle can absorb bounce.
        grip.handle:ReleaseComponent()
        stop_grip_motion(grip.mesh)
        grip.handle:GrabComponentAtLocation(grip.mesh, grip.bone, anchor)
        if not same(grip.handle:GetGrabbedComponent(), grip.mesh) then return false end
        release = {deadline = now + settle_timeout, still_since = now, target = target}
        grip.release = release
    end
    if now >= release.deadline then return false end
    local _, travel = between(release.target, target)
    if travel > 5^2 then
        release.still_since, release.target = now, target
    end
    grip.handle:SetTargetLocation(target)
    local _, gap = between(anchor, target)
    if not release.contacts and now - release.still_since >= settle_stationary and gap < 30^2 then
        restore_grip_collision(grip)
        release.contacts = now
    end
    return not release.contacts or now - math.max(release.contacts, release.still_since) < settle_contacts or gap > 16^2
end

local function follow_corpse(address, mirror, actor, pawn, now)
    local mesh = actor.Mesh
    local _, host_grip = marker_state(mirror.marker:K2_GetActorRotation())
    local local_grip = valid(mesh) and locally_carried(pawn, actor, mesh)
    if local_grip or not valid(mesh) then
        restore_corpse_physics(mirror)
        mirror.host_released = nil
        observed_grips[address] = nil
        corrections[address] = nil
        return
    end
    local observer = mirror.host_grip
    if not (observer or mesh:IsAnySimulatingPhysics()) then
        corrections[address] = nil
        return
    end
    local period = (host_grip or observer) and grip_period or sample_period
    if now < (mirror.next_pose or 0) then return end
    mirror.next_pose = now + period
    local target = mirror.marker:K2_GetActorLocation()
    if host_grip then
        mirror.host_released = nil
        mirror.pawn = pawn
        observed_grips[address] = mirror
        corrections[address] = nil
        if observer and observer.release then restore_corpse_physics(mirror, true) end
        local grip = project_host_grip(mirror, actor, mesh)
        if grip then grip.handle:SetTargetLocationAndRotation(target, grip.rotation) end
        return
    end
    if observer then
        corrections[address] = nil
        if settle_host_release(observer, target, now) then return end
        restore_corpse_physics(mirror, true)
        mirror.host_released, mirror.target_z = true, target.Z
        observed_grips[address] = nil
    end
    local offset, error_squared = between(corpses.position(actor), target)
    if mirror.host_released and mirror.target_z == target.Z and math.abs(offset.Z) < 100 then
        -- Once the host's height stops changing, let the locally simulated
        -- body settle. Driving its pelvis into a floor can bounce the whole rig.
        offset.Z = 0
        error_squared = offset.X * offset.X + offset.Y * offset.Y
    end
    mirror.target_z = target.Z
    if error_squared > 450^2 then
        corrections[address] = nil
        mesh:K2_AddWorldOffset(offset, false, {}, true)
    elseif error_squared > 15^2 then
        corrections[address] = {actor = actor, mesh = mesh, pawn = pawn, start = now, offset = offset,
            applied = 0, period = period}
    end
end

local function mirror_positions(world, pawn, now)
    for address, mirror in pairs(mirrors) do
        local marker = mirror.marker
        local actor = valid(marker) and marker:GetOwner()
        if not valid(actor) or not same(marker:GetWorld(), world) or actor:IsActorBeingDestroyed() then
            retire_turret(mirror)
            restore_corpse_physics(mirror)
            mirrors[address], corrections[address], observed_grips[address] = nil, nil, nil
        elseif mirror.turret then
            if actor:GetLocalRole() == 1 and present_turret(mirror, actor, pawn) then mirrors[address] = nil end
        elseif actor:GetLocalRole() == 1 and actor.Alive == false then
            follow_corpse(address, mirror, actor, pawn, now)
        end
    end
end

function corpses.animate(get_world)
    if not next(corrections) and not next(observed_grips) then return end
    local world = get_world()
    if not same(world, active_world) then corrections = {}; return end
    local now = clock:GetRealTimeSeconds(world)
    for address, correction in pairs(corrections) do
        local actor, mesh = correction.actor, correction.mesh
        if not valid(actor) or actor:IsActorBeingDestroyed() or not valid(mesh)
            or not mesh:IsAnySimulatingPhysics() or locally_carried(correction.pawn, actor, mesh) then
            corrections[address] = nil
        else
            local progress = math.min(1, math.max(0, (now - correction.start) / correction.period))
            local step, offset = progress - correction.applied, correction.offset
            if step > 0 then
                mesh:K2_AddWorldOffset({X = offset.X * step, Y = offset.Y * step, Z = offset.Z * step}, false, {}, true)
                correction.applied = progress
            end
            if progress == 1 then corrections[address] = nil end
        end
    end
    for address, mirror in pairs(observed_grips) do
        local actor = valid(mirror.marker) and mirror.marker:GetOwner()
        if valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), world) then
            follow_corpse(address, mirror, actor, mirror.pawn, now)
        else
            restore_corpse_physics(mirror)
            observed_grips[address] = nil
        end
    end
end

local function publish_corpse_position(actor, marker, host_grip)
    if not valid(actor.Mesh) or actor.Alive ~= false or not actor.Mesh:IsAnySimulatingPhysics() then return end
    local point, anchor = corpses.position(actor)
    local rotation = marker:K2_GetActorRotation()
    local number, last_grip = marker_state(rotation)
    local _, distance_squared = between(marker:K2_GetActorLocation(), point)
    if host_grip ~= last_grip then
        rotation.Roll = ((number // 256) + (host_grip and 128 or 0)) * byte_angle
        marker.NetUpdateFrequency = host_grip and 20 or 10
    elseif distance_squared <= 3^2 and anchor_code(rotation.Pitch) == (anchor or -1) + 1 then
        return
    end
    rotation.Pitch = anchor_pitch(anchor)
    marker:K2_SetActorLocationAndRotation(point, rotation, false, {}, true)
    marker:ForceNetUpdate()
end

function corpses.update_host_grip(get_world)
    if not next(markers) or not valid(host_pawn) then return end
    local held = host_pawn.HeldObject
    if not valid(held) and not grip_marker then return end
    local marker = valid(held) and markers[held:GetAddress()]
    if not marker and not grip_marker then return end
    local world = get_world()
    if not same(world, active_world) then return end
    if not valid(marker) or held:IsActorBeingDestroyed() or not same(marker:GetOwner(), held)
        or not locally_carried(host_pawn, held, held.Mesh) then marker = nil end
    if not same(marker, grip_marker) then
        local previous = valid(grip_marker) and grip_marker:GetOwner()
        if valid(previous) and not previous:IsActorBeingDestroyed() then publish_corpse_position(previous, grip_marker, false) end
        grip_marker, next_grip_sample = marker, 0
    end
    if not marker then return end
    local now = clock:GetRealTimeSeconds(world)
    if now < next_grip_sample then return end
    next_grip_sample = now + grip_period
    publish_corpse_position(held, marker, true)
end

local function publish_positions(world, now)
    for address, marker in pairs(markers) do
        local actor = valid(marker) and marker:GetOwner()
        if not valid(actor) or not same(marker:GetWorld(), world) or actor:IsActorBeingDestroyed() then
            turret_timers[address] = nil
            markers[address] = nil
        elseif turret_timers[address] then
            local timer = turret_timers[address]
            if timer.fire_pending then
                if now < timer.blast_until then timer.fire = find_stock_turret_fire(actor) end
                if valid(timer.fire) or now >= timer.blast_until then timer.fire_pending = nil end
            end
            local rotation = marker:K2_GetActorRotation()
            -- Send the burst to nearby peers once. Peers arriving later in
            -- the same visit see the remaining smoke without a second blast.
            local phase = now < timer.blast_until and blast_phase
                or clock:GetTimeSeconds(world) < timer.smoke_until and smoke_phase or 0
            if valid(timer.fire) and not timer.fire:IsActorBeingDestroyed() then phase = phase | fire_flag end
            if rotation_byte(math.abs(rotation.Pitch)) ~= phase then
                rotation.Pitch = phase * byte_angle
                marker:K2_SetActorRotation(rotation, true)
                marker:ForceNetUpdate()
            end
            if phase == 0 then turret_timers[address] = nil end
        elseif not supported_turret(actor) and not same(marker, grip_marker) then
            publish_corpse_position(actor, marker, false)
        end
    end
end

function corpses.start(role)
    if type(RegisterHook) ~= "function" or type(RegisterEndPlayPreHook) ~= "function" then return end
    marker_class = StaticFindObject(marker_path)
    clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(marker_class) then return end
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if role() ~= "client" then return end
        local marker = context:get()
        if valid(marker) and same(marker:GetClass(), marker_class) and valid(marker:GetOwner()) then
            pending[marker:GetAddress()] = marker
        end
    end)
    if type(NotifyOnNewObject) == "function" then
        NotifyOnNewObject(drone_path, function(actor)
            if role() ~= "host" or not valid(actor) or not is_drone(actor) then return end
            drone_seen = true
            return true
        end)
    end
    RegisterEndPlayPreHook(function(context)
        if next(markers) == nil and next(mirrors) == nil and next(pending) == nil then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        retire_turret(mirrors[address])
        mirrors[address], anchors[address], pending[address], turret_timers[address] = nil, nil, nil, nil
        corrections[address], observed_grips[address] = nil, nil
        local marker = markers[address]
        if valid(marker) and same(marker:GetOwner(), actor) then
            numbered[marker_number(marker)] = nil
            ended[address], markers[address] = marker, nil
        end
    end)
end

function corpses.sync(state, phase)
    if not valid(marker_class) or not valid(clock) then return end
    local hosting = phase == "hosting" and state.listening and not state.server
    local world = (hosting or phase == "connected" and state.server) and state.world or nil
    if not same(world, active_world) then
        for _, mirror in pairs(mirrors) do
            retire_turret(mirror)
            restore_corpse_physics(mirror)
        end
        turret_timers, corrections, observed_grips = {}, {}, {}
        grip_marker, next_grip_sample = nil, 0
        active_world, markers, numbered, mirrors, ended, anchors, next_sample, corpse_number =
            world, {}, {}, {}, {}, {}, 0, 0
    end
    local pawn = state.local_pawn and state.local_pawn.pawn
    host_pawn = hosting and pawn or nil
    if not valid(world) then return end
    if not valid(npc_class) then npc_class = StaticFindObject(npc_path) end
    if hosting and not turret_death_hook then
        local wall_seconds = os.time()
        if wall_seconds >= next_turret_lookup then
            next_turret_lookup = wall_seconds + 1
            turret_class = StaticFindObject(turret_path)
            if hosting and valid(turret_class) and not turret_death_hook and valid(StaticFindObject(turret_path .. ":Die")) then
                RegisterHook(turret_path .. ":Die", function(context) pcall(publish, context:get()) end)
                turret_death_hook = true
            end
        end
    end
    if hosting and drone_seen and not drone_death_hook and valid(StaticFindObject(drone_path .. ":Die")) then
        RegisterHook(drone_path .. ":Die", function(victim) pcall(publish, victim:get()) end)
        drone_death_hook = true
    end
    if hosting and not death_hook and valid(StaticFindObject(npc_path .. ":ResolveDeath")) then
        -- UE4SS invokes hooks on Blueprint functions after the original script.
        RegisterHook(npc_path .. ":ResolveDeath", function(context) pcall(publish, context:get()) end)
        death_hook = true
    end
    for address, marker in pairs(ended) do
        ended[address] = nil
        if valid(marker) and same(marker:GetWorld(), world) then marker:K2_DestroyActor() end
    end
    if next(hosting and markers or mirrors) ~= nil or not hosting and next(pending) ~= nil then
        local now = clock:GetRealTimeSeconds(world)
        if now >= next_sample then
            next_sample = now + sample_period
            if hosting then
                publish_positions(world, now)
            else
                for address, marker in pairs(pending) do
                    if apply(marker, world) then pending[address] = nil end
                end
                mirror_positions(world, pawn, now)
            end
        end
    end
end

return corpses
