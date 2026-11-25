local fields = {}
local effects = require("native_anomaly_effects")
local electrodes = require("native_electrode_view")
local canisters = require("native_explosive_canisters")
local drum_fire = require("native_drum_fire")
local fixed = require("native_fixed_explosives")
local destructibles = require("native_destructibles")
local acid_pools = require("native_acid_pools")
local pyrogeist = require("native_pyrogeist_view")
local shadows = require("native_shadow_sphere_view")
local lamps = require("native_angler_lamp_view")
local observers = require("native_observer_target")
local flare = require("native_flare")
local crate_props = require("native_crate_props")

local classes = {
    starburst = "/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C",
    burner = "/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C",
    vortex = "/Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C",
    gravity = "/Game/Classes/Anomalies/GravityAnomaly_BP.GravityAnomaly_BP_C",
    electrode = "/Game/Classes/Anomalies/Electrode_BP.Electrode_BP_C",
    ["wandering-teleport"] = "/Game/Classes/Anomalies/Wandering_Teleport_BP.Wandering_Teleport_BP_C",
    ["lingering-teleport"] = "/Game/Classes/Anomalies/Lingering_Teleport_BP1.Lingering_Teleport_BP1_C",
    ["glass-thread"] = "/Game/Classes/Anomalies/GlassThread_BP.GlassThread_BP_C",
}
local kinds = {}
for kind, path in pairs(classes) do kinds["BlueprintGeneratedClass " .. path] = kind end
local fire_jet = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Jet_BP.Fire_Jet_BP_C"
local overlap = "BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"
local electrode_overlap = "BndEvt__Electrode_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"
local wandering_overlap = "BndEvt__Wandering_Teleport_BP_Capsule_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local rook = "/Game/Classes/Maps/Dreams/TheRook_P"
local pending, active, pending_fires, fires, fixtures = {}, {}, {}, {}, {}
local wandering_hooks, wandering_retry = {}, {}
local world, world_map, fixture_kind, wandering_probe
local recovery, glass_break_hook
local glass_pending, glass_live = {}, {}
local glass_work = {}
local glass_batch_limit, glass_time_budget = 8, .002

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function in_world(actor, current)
    return valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), current)
end

local function bind_wandering_effect(kind)
    if wandering_hooks[kind] or type(RegisterHook) ~= "function" then return end
    local now = os.clock()
    if now < (wandering_retry[kind] or 0) then return end
    local path = classes[kind] .. ":Tele_FX"
    if not valid(StaticFindObject(path)) then
        wandering_retry[kind] = now + .5
        return
    end
    RegisterHook(path, function(context, position)
        local actor = context:get()
        if not in_world(actor, world) or not actor:HasAuthority() then return end
        local entry = active[actor:GetAddress()]
        local driver = actor:GetWorld().NetDriver
        if entry and entry.kind == kind and same(entry.actor, actor)
            and valid(driver) and same(driver.World, world) and not valid(driver.ServerConnection) then
            effects.observe_wandering_teleport(position:get())
        end
    end)
    wandering_hooks[kind] = true
end

local function trap_kind(actor, class)
    if class == pyrogeist.body or shadows.enabled and class == shadows.class
        or lamps.enabled and class == lamps.class or observers.enabled and class == observers.class then
        local at = actor:GetWorld()
        if not valid(at) then return end
        local name = at:GetFullName()
        if name == "World " .. crossroads .. ".CrossRoads_P" then
            if class == pyrogeist.body then return "pyrogeist" end
            if class == observers.class then return "observer" end
            return class == shadows.class and "shadow-sphere" or "angler-lamp"
        end
        return
    end
    return kinds[class]
end

local function disables_replica_tick(kind)
    return kind == "gravity" or kind == "pyrogeist" or kind == "shadow-sphere" or kind == "angler-lamp"
        or kind == "observer" or kind == "lingering-teleport"
end

local function replica(actor)
    if actor:GetLocalRole() ~= 1 then return false end
    local current = actor:GetWorld()
    local driver = valid(current) and current.NetDriver
    local server = valid(driver) and driver.ServerConnection
    return valid(server) and same(driver.World, current) and same(server.Driver, driver)
end

local function placement(actor)
    if type(SilverfishQualifyNativePlacedTrap) ~= "function" then return end
    local path = actor:GetFullName():match("^%S+ (.+)$")
    if path then
        local placed, guest = SilverfishQualifyNativePlacedTrap(path)
        if placed then return path, guest end
    end
end

local function recover_family()
    local job = recovery
    if not job or not same(job.world, world) then
        recovery = nil
        return
    end
    local name = job.names[job.index]
    if not name then
        if job.pyrogeist then pyrogeist.recover(world, replica) end
        recovery = nil
        return
    end
    for _, actor in ipairs(FindAllOf(name) or {}) do
        if in_world(actor, world) then
            local address = actor:GetAddress()
            if not active[address] and not pending[address] then
                local kind = trap_kind(actor, actor:GetClass():GetFullName())
                if kind and (job.rook or disables_replica_tick(kind)) then
                    local placed, local_copy
                    if kind == "gravity" then placed, local_copy = placement(actor) end
                    local host = job.phase == "hosting" and actor:HasAuthority()
                        and (placed or actor:ActorHasTag(FName("Anomaly")))
                    local guest = job.phase == "connected" and (local_copy or replica(actor))
                    if host or guest then
                        if guest and kind == "gravity" then
                            actor:SetActorEnableCollision(false)
                            actor:SetActorTickEnabled(false)
                        end
                        pending[address] = {actor = actor, kind = kind, placed = placed}
                    end
                end
            end
        end
    end
    job.index = job.index + 1
end

local function vortex_sensor(actor)
    local sensor = actor.Sphere_Hit
    if valid(sensor) and same(sensor:GetOwner(), actor)
        and sensor:GetClass():GetFullName() == "Class /Script/Engine.SphereComponent" then return sensor end
end

local function animate_vortex(point)
    local timeline
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.kind == "vortex" and in_world(actor, world) and not actor:GetActorEnableCollision()
            and valid(actor.Sphere1) and same(actor.Sphere1:GetOwner(), actor) and valid(actor.Anim)
            and same(actor.Anim:GetOwner(), actor) then
            local at = actor.Sphere1:K2_GetComponentLocation()
            if (at.X - point.X)^2 + (at.Y - point.Y)^2 + (at.Z - point.Z)^2 < 9 then
                if timeline then return false end
                timeline = actor.Anim
            end
        end
    end
    if not timeline then return false end
    timeline:PlayFromStart()
    return timeline:IsPlaying()
end

local function project_placed_sensors(args, state, phase)
    local visit = args[4]
    if phase ~= "connected" or state.map ~= args[3] or not same(state.world, world)
        or not visit or #visit ~= 32 or not visit:match("^[0-9a-f]+$") or #args < 5 or #args > 36 then
        error("Vortex sensors are outside the current guest visit", 0)
    end
    for index = 5, #args do
        local path, x, y, z = args[index]:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local actor = path and #path <= 512 and StaticFindObject(path)
        local entry = in_world(actor, world) and active[actor:GetAddress()]
        if entry and entry.placed == path and entry.kind == "vortex" and not actor:GetActorEnableCollision()
            and x and y and z and math.abs(x) <= 4096 and math.abs(y) <= 4096 and math.abs(z) <= 4096 then
            local sensor = vortex_sensor(actor)
            if sensor then sensor:K2_SetRelativeLocation({X = x, Y = y, Z = z}, false, {}, true) end
        end
    end
    return "Applied host placements to the passive Vortex sensors"
end

local function glass_meshes(actor)
    local mesh_class = StaticFindObject("/Script/Engine.StaticMeshComponent")
    if not valid(mesh_class) then return {} end
    local meshes = {}
    for _, value in ipairs(actor:K2_GetComponentsByClass(mesh_class) or {}) do
        local mesh = value
        if type(mesh) == "userdata" and type(mesh.type) == "function" and mesh:type() == "RemoteUnrealParam" then
            mesh = mesh:get()
        end
        if not valid(mesh) or not same(mesh:GetOwner(), actor) then return {} end
        meshes[#meshes + 1] = mesh
    end
    return meshes
end

local function bind_glass_break()
    if glass_break_hook or type(RegisterHook) ~= "function" then return end
    local path = classes["glass-thread"] .. ":Break"
    if not valid(StaticFindObject(path)) then return end
    RegisterHook(path, function(context)
        local thread = context:get()
        if not in_world(thread, world) or not thread:HasAuthority() then return end
        local address = thread:GetAddress()
        local entry = glass_live[address] or glass_pending[address]
        if entry and same(entry.actor, thread) then entry.break_requested = true end
    end)
    glass_break_hook = true
end

local function share_glass_meshes(actor)
    local meshes = glass_meshes(actor)
    local main, root = actor.StaticMesh, actor.RootComponent
    if #meshes < 1 or #meshes > 7 or not valid(main) or not valid(root) then return false end
    local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    local mesh_class = StaticFindObject("/Script/Engine.StaticMeshComponent")
    if not valid(math_api) or not valid(mesh_class) then return false end
    actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 5)
    actor:SetReplicates(true)
    main:SetIsReplicated(true)
    for _, mesh in ipairs(meshes) do
        if not same(mesh, main) and valid(mesh.StaticMesh) and mesh:GetCollisionEnabled() == 0 then
            -- The stock construction script creates random, cosmetic branches as
            -- named subobjects. Net-created instances can reach a guest that did
            -- not run that random branch construction.
            local relative = math_api:MakeRelativeTransform(mesh:K2_GetComponentToWorld(), root:K2_GetComponentToWorld())
            local branch = actor:AddComponentByClass(mesh_class, true, relative, true)
            if valid(branch) then
                branch:SetCollisionEnabled(0)
                branch:SetGenerateOverlapEvents(false)
                branch:SetMobility(2)
                branch:SetStaticMesh(mesh.StaticMesh)
                actor:FinishAddComponent(branch, true, relative)
                if same(branch.StaticMesh, mesh.StaticMesh)
                    and branch:K2_AttachToComponent(root, FName("None"), 0, 0, 0, false) then
                    branch:K2_SetRelativeTransform(relative, false, {}, true)
                    branch:SetIsReplicated(true)
                    mesh:K2_DestroyComponent(actor)
                else
                    branch:K2_DestroyComponent(actor)
                end
            end
        end
    end
    return true
end

local function publish_glass(current)
    if not next(glass_pending) or not same(current(), world) then return end
    local started, count = os.clock(), 0
    for _ = 1, glass_batch_limit do
        local address, entry = next(glass_pending)
        if not entry then break end
        glass_pending[address] = nil
        local actor = entry.actor
        if in_world(actor, world) and actor:HasAuthority() and share_glass_meshes(actor) then
            actor:SetReplicateMovement(true)
            actor:ForceNetUpdate()
            glass_live[address] = entry
        end
        count = count + 1
        if os.clock() - started >= glass_time_budget then break end
    end
    if headless then
        glass_work.batches = (glass_work.batches or 0) + 1
        glass_work.peak_batch = math.max(glass_work.peak_batch or 0, count)
        glass_work.peak_us = math.max(glass_work.peak_us or 0, math.floor((os.clock() - started) * 1e6))
        if next(glass_pending) then glass_work.deferred = (glass_work.deferred or 0) + 1 end
    end
end

local function burner_source(actor)
    local point, source = actor:K2_GetActorLocation(), nil
    local duration = actor.BurnTime
    if type(duration) ~= "number" or not (duration > 0 and duration <= 30) then return end
    for _, entry in pairs(active) do
        if entry.kind == "burner" and in_world(entry.actor, world) then
            -- The stock trap spawns the jet 15.5 units above the trigger's captured hit point.
            local start = entry.actor.SpawnLocation
            if start and (point.X - start.X)^2 + (point.Y - start.Y)^2 + (point.Z - start.Z - 15.5)^2 <= .01 then
                if source then return end
                source = entry.actor
            end
        end
    end
    return source
end

function fields.start(role, current)
    if type(SilverfishSetNativeAnomalyGuest) ~= "function" or type(RegisterBeginPlayPreHook) ~= "function"
        or type(RegisterBeginPlayPostHook) ~= "function" then return end
    lamps.start()
    observers.start()
    LoopInGameThreadWithDelay(25, function()
        local seat = role()
        if recovery and seat == (recovery.phase == "hosting" and "host" or "client") then recover_family() end
        if seat == "host" and next(glass_pending) then publish_glass(current) end
    end)
    RegisterBeginPlayPreHook(function(context)
        local seat = role()
        if seat ~= "host" and seat ~= "client" then return end
        local actor = context:get()
        if not valid(actor) then return end
        local class = actor:GetClass():GetFullName()
        if seat == "host" then
            if class == effects.blast_class and in_world(actor, world) and not fixed.observe_spawned_blast(actor) then
                effects.observe_blast(actor, active, canisters.blast_source)
            end
        elseif class == pyrogeist.jet then
            if pyrogeist.in_scope(actor) and replica(actor) then
                actor:SetActorEnableCollision(false)
                actor:SetActorTickEnabled(false)
            end
        elseif class == "BlueprintGeneratedClass " .. classes["glass-thread"] then
            actor:SetActorEnableCollision(false)
        elseif trap_kind(actor, class) or class == fire_jet then
            local _, local_copy = placement(actor)
            if local_copy or replica(actor) then actor:SetActorEnableCollision(false) end
        end
    end)
    RegisterBeginPlayPostHook(function(context)
        local seat = role()
        if seat ~= "host" and seat ~= "client" then return end
        local actor = context:get()
        if not valid(actor) or actor:IsActorBeingDestroyed() then return end
        local class = actor:GetClass():GetFullName()
        local debris = crate_props.debris_created(actor, class, seat)
        if debris then
            effects.observe_wood_debris(debris)
            return
        end
        if seat == "host" and class == effects.blast_class and in_world(actor, world) then
            local source = effects.observe_pipe_bomb_blast(actor)
            if source then flare.retire_detonated_bomb(source) end
        end
        if class == acid_pools.class_name then
            pcall(acid_pools.created, actor, seat)
            return
        end
        if class == pyrogeist.jet then
            pyrogeist.created(actor, seat, replica)
            return
        end
        canisters.created(actor, class, seat, replica)
        fixed.created(actor, class, seat)
        local kind = trap_kind(actor, class)
        if not kind and class ~= fire_jet then return end
        if seat == "host" and kind == "lingering-teleport" then return end
        local placed, local_copy
        if kind then placed, local_copy = placement(actor) end
        if seat == "host" and not actor:HasAuthority() then return end
        if seat == "host" and kind == "glass-thread" then
            bind_glass_break()
            glass_pending[actor:GetAddress()] = {actor = actor}
            return
        end
        if seat == "client" and not local_copy and not replica(actor) then
            if (kind == "glass-thread" or disables_replica_tick(kind)) and actor:GetLocalRole() == 1 then
                pending[actor:GetAddress()] = {actor = actor, kind = kind}
            end
            return
        end
        if seat == "client" then
            actor:SetActorEnableCollision(false)
            if kind == "electrode" then electrodes.suspend(actor) end
            if disables_replica_tick(kind) then actor:SetActorTickEnabled(false) end
        end
        local address = actor:GetAddress()
        if class == fire_jet then
            if seat == "client" or in_world(actor, world) and burner_source(actor) then pending_fires[address] = actor end
        else
            pending[address] = {actor = actor, kind = kind, placed = placed}
            if seat == "host" and fixture_kind == kind then fixtures[kind] = actor end
        end
    end)
    if type(RegisterEndPlayPreHook) == "function" then
        RegisterEndPlayPreHook(function(context, reason)
            local seat = role()
            if seat ~= "host" and seat ~= "client" then return end
            local actor = context:get()
            if not valid(actor) then return end
            local address = actor:GetAddress()
            local entry = active[address]
            local destroyed = reason:get() == 0
            if seat == "host" and entry and entry.kind == "electrode" and destroyed then
                electrodes.retire(entry)
            end
            local strand = glass_live[address] or glass_pending[address]
            if seat == "host" and strand and strand.break_requested and destroyed then effects.observe_glass_break(actor) end
            glass_live[address], glass_pending[address] = nil, nil
            active[address], pending[address], fires[address], pending_fires[address] = nil, nil, nil, nil
            pyrogeist.ended(address)
            canisters.ended(address, destroyed)
            fixed.ended(address)
            destructibles.ended(address, destroyed)
            acid_pools.ended(address, destroyed)
        end)
    end
end

function fields.guest(enabled)
    if not enabled then
        drum_fire.clear()
        destructibles.clear()
        acid_pools.clear()
    end
    if type(SilverfishSetNativeAnomalyGuest) == "function" then SilverfishSetNativeAnomalyGuest(enabled) end
end

function fields.command(args, state, phase)
    if args[2] == "native-vortex-apply-sensors" then return project_placed_sensors(args, state, phase) end
    if args[2] == "native-angler-apply-colors" then return lamps.apply_colors(args, state, phase, active) end
    if args[2] == "native-observer-apply-opacity" then return observers.apply_opacity(args, state, phase, active) end
    local electrode = electrodes.command(args, state, phase, world, active)
    if electrode ~= nil then return electrode end
    local fire_view = drum_fire.command(args, state, phase)
    if fire_view ~= nil then return fire_view end
    local canister = canisters.command(args, state, phase)
    if canister ~= nil then return canister end
    local explosive = fixed.command(args, state, phase)
    if explosive ~= nil then return explosive end
    local destructible = destructibles.command(args, state, phase)
    if destructible ~= nil then return destructible end
    local acid = acid_pools.command(args, state, phase)
    if acid ~= nil then return acid end
    local effect = effects.command(args, state, phase, animate_vortex)
    if effect ~= nil then return effect end
    if args[2] == "native-anomaly-trigger-fixture" and args[3] == "restore-wandering-probe" then
        local guest = #state.guests == 1 and state.guests[1].pawn
        if #args ~= 3 or not host_fixture or phase ~= "hosting" or not wandering_probe
            or not same(wandering_probe.world, state.world) or not in_world(guest, state.world)
            or not same(wandering_probe.guest, guest) or not guest:HasAuthority() or guest:IsLocallyControlled() then
            error("Wandering teleport restore probe unavailable", 0)
        end
        if not guest:K2_TeleportTo(wandering_probe.origin, guest:K2_GetActorRotation()) then
            error("The wandering teleport probe could not restore the guest", 0)
        end
        guest:ForceNetUpdate()
        wandering_probe = nil
        return "The test guest returned to the party"
    end
    local glass_contact = type(args[3]) == "string" and args[3]:match("^glass%-thread%-contact|([0-9.,%-]+)$")
    local glass_point = glass_contact or type(args[3]) == "string" and args[3]:match("^glass%-thread|([0-9.,%-]+)$")
    if headless and #args == 3 and glass_point
        and (args[2] == "native-placed-anomaly-guest-fixture" and phase == "connected" and not glass_contact
            or args[2] == "native-anomaly-trigger-fixture" and phase == "hosting" and host_fixture) then
        for _, entry in pairs(glass_live) do
            local actor = entry.actor
            if in_world(actor, state.world)
                and (phase == "hosting" and actor:HasAuthority() or phase == "connected" and replica(actor)) then
                local point = actor:K2_GetActorLocation()
                if ("%.0f,%.0f,%.0f"):format(point.X, point.Y, point.Z) == glass_point then
                    if glass_contact then
                        local guest = #state.guests == 1 and state.guests[1].pawn
                        if not in_world(guest, world) or not guest:HasAuthority() or guest:IsLocallyControlled()
                            or not guest:K2_TeleportTo(point, guest:K2_GetActorRotation()) then
                            error("Unable to stage a stock guest overlap with the Glass Thread", 0)
                        end
                        guest:ForceNetUpdate()
                        return "The stock guest entered the Glass Thread overlap"
                    end
                    actor:Break()
                    return "Invoked the stock Glass Thread break"
                end
            end
        end
        error("The stock Glass Thread is unavailable", 0)
    end
    if args[2] == "native-anomaly-trigger-fixture" or args[2] == "native-placed-anomaly-guest-fixture"
        or args[2] == "native-electrode-retire-fixture" then
        local guest_probe = args[2] == "native-placed-anomaly-guest-fixture"
        local trap = fixtures[args[3]]
        local kind = trap and args[3]
        if not trap then
            for _, entry in pairs(active) do
                if entry.placed == args[3] then trap, kind = entry.actor, entry.kind; break end
            end
        end
        if kind == "gravity" and guest_probe and #args == 3 and headless and phase == "connected"
            and in_world(trap, state.world) and not trap:GetActorEnableCollision() and not trap:IsActorTickEnabled() then
            trap:ReceiveBeginPlay()
            if trap:GetActorEnableCollision() or trap:IsActorTickEnabled() then
                error("The guest Gravity Well resumed simulation", 0)
            end
            return "Stock placed Gravity Well initialization invoked"
        end
        local trigger = in_world(trap, state.world) and (kind == "vortex" and vortex_sensor(trap)
            or kind == "wandering-teleport" and trap.Capsule or trap.Sphere)
        if #args ~= 3 or not headless or not valid(trigger) then
            error("Trap trigger probe unavailable", 0)
        end
        if guest_probe then
            local pawn = state.local_pawn and state.local_pawn.pawn
            if phase ~= "connected" or not in_world(pawn, state.world) or not pawn:IsLocallyControlled()
                or not valid(pawn.CapsuleComponent) or trap:GetActorEnableCollision() ~= false then
                error("Placed trap guest overlap probe unavailable", 0)
            end
            local callback = kind == "electrode" and electrode_overlap
                or kind == "wandering-teleport" and wandering_overlap or overlap
            trap[callback](trap, trigger, pawn, pawn.CapsuleComponent, 0, false, {})
            if kind == "electrode" then
                trap:ReceiveBeginPlay()
                trap:Burst(2)
            end
            return "Stock placed trap guest overlap invoked"
        end
        if args[2] == "native-electrode-retire-fixture" then
            if not host_fixture or phase ~= "hosting" or kind ~= "electrode" or not trap:HasAuthority() then
                error("Electrode removal probe unavailable", 0)
            end
            trap:K2_DestroyActor()
            return "The stock Electrode was destroyed on the host"
        end
        local guest = #state.guests == 1 and state.guests[1].pawn
        if not host_fixture or phase ~= "hosting" or not in_world(guest, state.world)
            or not guest:HasAuthority() or guest:IsLocallyControlled() then error("Trap trigger probe unavailable", 0) end
        if kind == "wandering-teleport" then
            if #trap.Waypoints < 1 or not trap:HasAuthority() then error("Stock teleport waypoints unavailable", 0) end
            local original = guest:K2_GetActorLocation()
            wandering_probe = {world = state.world, guest = guest, origin = {X = original.X, Y = original.Y, Z = original.Z}}
            trap[wandering_overlap](trap, trigger, guest, guest.CapsuleComponent, 0, false, {})
            return "Stock wandering teleport overlap invoked on the host"
        end
        local center = trigger:K2_GetComponentLocation()
        if not guest:K2_TeleportTo({X = center.X, Y = center.Y, Z = center.Z + 45}, guest:K2_GetActorRotation()) then
            error("The stock guest could not enter the trap trigger", 0)
        end
        return "The stock guest entered the trap trigger"
    end
    if args[2] ~= "native-anomaly-field-fixture" then return end
    local path = classes[args[3]]
    if #args ~= 3 or not path or args[3] == "lingering-teleport" or args[3] == "glass-thread" or state.map ~= crossroads or not valid(state.world) or not headless
        or (phase ~= "connected" and (phase ~= "hosting" or not host_fixture)) then
        error("The stock trap probe requires a muted Crossroads session", 0)
    end
    local class = StaticFindObject("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C")
    local kind = StaticFindObject(path)
    if not valid(kind) then LoadAsset(path); kind = StaticFindObject(path) end
    local anchor = StaticFindObject(crossroads .. ".CrossRoads_P:PersistentLevel.AnomalyField_BP_C_"
        .. (args[3] == "burner" and "9" or "6"))
    if not valid(class) or not valid(kind) or not in_world(anchor, state.world) or not same(anchor:GetClass(), class) then
        error("The Crossroads field template is unavailable", 0)
    end
    local field = state.world:SpawnActor(class, anchor:K2_GetActorLocation(), {Pitch = 0, Yaw = 0, Roll = 0})
    if not in_world(field, state.world) or field.ForceSpawn then error("The stock probe field did not initialize", 0) end
    field.Anomalies = {kind}
    field.Min_Spawn, field.Max_Spawn, field.SpawnRadius = 1, 1, 500
    if phase == "connected" then
        -- Exercise the stock Blueprint call through the same ForceSpawn branch used by placed fields.
        field.ForceSpawn = true
        field:ReceiveBeginPlay()
    else
        fixture_kind = args[3]
        local accepted, failure = pcall(function() field:SpawnField() end)
        fixture_kind = nil
        if not accepted then field:K2_DestroyActor(); error(failure, 0) end
    end
    field:K2_DestroyActor()
    return "Stock anomaly field invoked"
end

function fields.update_destructibles(current_world)
    destructibles.update(current_world)
end

function fields.rows(state, phase, row)
    if phase ~= "hosting" and phase ~= "connected" or not valid(state.world) then
        recovery = nil
        lamps.clear()
        observers.clear_visuals()
        drum_fire.clear()
        destructibles.clear()
        acid_pools.clear()
        return {}
    end
    if not same(world, state.world) or world_map ~= state.map then
        active, fires, fixtures, world, world_map = {}, {}, {}, state.world, state.map
        glass_live, glass_work = {}, {}
        recovery, wandering_retry = nil, {}
        effects.clear()
        electrodes.clear()
        lamps.clear()
        observers.clear_visuals()
        -- Initial replicated actors can finish BeginPlay while the new driver is still pending.
        if state.map == rook then
            recovery = {world = world, phase = phase, index = 1, rook = true,
                names = {"GravityAnomaly_BP_C", "VortexTrap_BP_C"}}
        elseif phase == "connected" and state.map == crossroads then
            local names = {"GravityAnomaly_BP_C", "Pyrogeist_BP_C", "Lingering_Teleport_BP1_C"}
            if shadows.enabled then names[#names + 1] = "ShadowSphere_BP_C" end
            if lamps.enabled then names[#names + 1] = "AnglerLamp_BP_C" end
            if observers.enabled then names[#names + 1] = "Observers_BP_C" end
            recovery = {world = world, phase = phase, index = 1, names = names, pyrogeist = true}
        else
            recovery = {world = world, phase = phase, index = 1, names = {"GravityAnomaly_BP_C"}}
        end
    end
    if phase == "hosting" and type(SilverfishTakeNativeLingeringTeleport) == "function" then
        while true do
            local path, source = SilverfishTakeNativeLingeringTeleport(world:GetAddress())
            if not path then break end
            local actor = StaticFindObject(path)
            if in_world(actor, world) and actor:HasAuthority() then
                pending[actor:GetAddress()] = {actor = actor, kind = "lingering-teleport", travel_source = source}
            end
        end
    end
    for address, entry in pairs(pending) do
        local actor = entry.actor
        if in_world(actor, world) then
            if phase == "connected" then
                if replica(actor) then
                    actor:SetActorEnableCollision(false)
                    if disables_replica_tick(entry.kind) then actor:SetActorTickEnabled(false) end
                end
                if entry.kind == "glass-thread" then
                    if replica(actor) then glass_live[address] = entry end
                elseif actor:GetActorEnableCollision() == false then
                    active[address] = entry
                end
            elseif entry.placed or entry.travel_source or actor:ActorHasTag(FName("Anomaly")) then
                -- SpawnField applies its stock tag after finishing the spawned actor's BeginPlay.
                actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 5)
                actor:SetReplicates(true)
                actor:SetReplicateMovement(true)
                if actor.bReplicates and actor.bReplicateMovement then
                    if entry.kind == "vortex" then
                        local sensor = vortex_sensor(actor)
                        if sensor then sensor:SetIsReplicated(true) end
                    elseif entry.kind == "pyrogeist" then
                        local sphere = actor.Sphere
                        if valid(sphere) and same(sphere:GetOwner(), actor) then sphere:SetIsReplicated(true) end
                    end
                    actor:ForceNetUpdate()
                    active[address] = entry
                end
            end
        end
        pending[address] = nil
    end
    pyrogeist.apply(world, phase, active, replica)
    shadows.project(active, state, phase)
    lamps.project(active, state, phase)
    observers.target(active, state, phase)
    observers.publish_rewards(state, phase)
    for address, fire in pairs(pending_fires) do
        if in_world(fire, world) then
            if phase == "hosting" then
                fire.NetUpdateFrequency = math.min(fire.NetUpdateFrequency, 5)
                fire:SetReplicates(true)
                fire:SetReplicateMovement(true)
                fire:ForceNetUpdate()
            end
            if phase == "hosting" and fire.bReplicates or phase == "connected" and not fire:GetActorEnableCollision() then
                fires[address] = fire
            end
        end
        pending_fires[address] = nil
    end
    local rows, live = {}, 0
    for address, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() and valid(actor.RootComponent) then
            if phase == "hosting" and entry.kind == "starburst" then effects.observe_charge(entry) end
            if phase == "hosting" and (entry.kind == "wandering-teleport" or entry.kind == "lingering-teleport")
                and not wandering_hooks[entry.kind] then
                bind_wandering_effect(entry.kind)
            end
            if phase == "connected" and entry.placed and actor:GetActorEnableCollision() then
                actor:SetActorEnableCollision(false)
            end
            live = live + 1
            local position = actor:K2_GetActorLocation()
            local collision = actor:GetActorEnableCollision() and 1 or 0
            rows[#rows + 1] = row("native-anomaly-position", entry.kind, position.X, position.Y, position.Z, collision)
            if entry.placed then
                rows[#rows + 1] = row("native-placed-anomaly", entry.placed, entry.kind, position.X, position.Y, position.Z,
                    collision, actor:GetLocalRole())
            end
            if entry.kind == "vortex" then
                local sensor, mesh = vortex_sensor(actor), actor.Sphere1
                if sensor and valid(mesh) and same(mesh:GetOwner(), actor) then
                    local relative, center, visual = sensor.RelativeLocation, sensor:K2_GetComponentLocation(), mesh:K2_GetComponentLocation()
                    rows[#rows + 1] = row("native-vortex-sensor", entry.placed or "-", position.X, position.Y, position.Z,
                        relative.X, relative.Y, relative.Z, center.X, center.Y, center.Z, visual.X, visual.Y, visual.Z)
                end
            elseif entry.kind == "electrode" then
                rows[#rows + 1] = electrodes.observe(entry, phase, row, position)
            elseif entry.kind == "angler-lamp" then
                rows[#rows + 1] = lamps.color_row(actor, row)
            elseif entry.kind == "observer" then
                rows[#rows + 1] = observers.opacity_row(actor, phase, row, position)
            end
        else
            if phase == "hosting" and entry.kind == "electrode" then electrodes.retire(entry) end
            active[address] = nil
        end
    end
    if headless then
        if phase == "hosting" and glass_work.batches then
            rows[#rows + 1] = row("native-glass-thread-work", glass_work.batches, glass_work.peak_batch,
                glass_work.peak_us, glass_work.deferred or 0, next(glass_pending) and 1 or 0)
        end
        for _, entry in pairs(glass_live) do
            local actor = entry.actor
            if in_world(actor, world) and valid(actor.RootComponent) then
                local position = actor:K2_GetActorLocation()
                rows[#rows + 1] = row("native-anomaly-position", "glass-thread", position.X, position.Y, position.Z,
                    actor:GetActorEnableCollision() and 1 or 0)
                for _, mesh in ipairs(glass_meshes(actor)) do
                    local root = same(mesh, actor.StaticMesh)
                    -- Compare branches in actor space so root movement quantization does not amplify at the ends.
                    local location = root and mesh:K2_GetComponentLocation() or mesh.RelativeLocation
                    local rotation = root and mesh:K2_GetComponentRotation() or mesh.RelativeRotation
                    local scale = mesh.RelativeScale3D
                    rows[#rows + 1] = row("native-glass-thread-mesh", position.X, position.Y, position.Z, root and 1 or 0,
                        location.X, location.Y, location.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, scale.X, scale.Y, scale.Z)
                end
            end
        end
    end
    local skipped, blocked, blocked_canisters, blocked_fixed = 0, 0, 0, 0
    if phase == "connected" and type(SilverfishReadNativeAnomalySkips) == "function" then
        skipped, blocked, blocked_canisters, blocked_fixed = SilverfishReadNativeAnomalySkips(world:GetAddress())
    end
    rows[#rows + 1] = row("native-anomaly-field", live, skipped, blocked or 0)
    for address, fire in pairs(fires) do
        if valid(fire) and not fire:IsActorBeingDestroyed() then
            local position, particles = fire:K2_GetActorLocation(), fire.NI_FireMassive
            rows[#rows + 1] = row("native-burner-jet", position.X, position.Y, position.Z,
                fire:GetActorEnableCollision() and 1 or 0, valid(particles) and particles:IsActive() and 1 or 0)
        else
            fires[address] = nil
        end
    end
    if phase == "hosting" then
        effects.observe_prop_breaks(world)
        for _, cue in ipairs(effects.rows(row)) do rows[#rows + 1] = cue end
        for _, electrode in ipairs(electrodes.retired_rows(row)) do rows[#rows + 1] = electrode end
    else
        rows[#rows + 1] = row("native-canister-blocked", blocked_canisters or 0)
        rows[#rows + 1] = row("native-fixed-explosive-blocked", blocked_fixed or 0)
        effects.retire_charges()
    end
    for _, canister in ipairs(canisters.rows(state, phase, row)) do rows[#rows + 1] = canister end
    for _, view in ipairs(drum_fire.rows(state, phase, row)) do rows[#rows + 1] = view end
    for _, item in ipairs(fixed.rows(state, phase, row)) do rows[#rows + 1] = item end
    rows[#rows + 1] = destructibles.rows(state, phase, row)
    rows[#rows + 1] = acid_pools.rows(state, phase, row)
    return rows
end

return fields
