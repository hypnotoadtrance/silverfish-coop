local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local lane = mailbox and mailbox:match("[/\\]portable%-title%-%x+[/\\]([a-z]+)[/\\]")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or (lane ~= "first" and lane ~= "second")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local path = map .. ":PersistentLevel.Prop_Lantern_01_C_1"
local toolbox_path = map .. ":PersistentLevel.Prop_ToolBox01_C_7"
local stalker_path = map .. ":PersistentLevel.NPC_LoneStalker_C_0"
local command, reply = mailbox .. "/portable-lantern-command.tsv", mailbox .. "/portable-lantern-reply.tsv"
local color = {R = 0, G = 0, B = 0, A = 0}
local last, watch, dynamic, placed_stream, placed_scope

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function current_world_is(address)
    local viewport = FindFirstOf("GameViewportClient")
    local current = valid(viewport) and viewport.World
    return valid(current) and current:GetAddress() == address
end

local function holds(pawn, lantern, root)
    local grip = pawn.PhysicsHandle
    return same(pawn.HeldObject, lantern) == true and valid(grip) == true and same(grip:GetGrabbedComponent(), root) == true
end

local function sweep_lantern(world, driver, pawn, lantern, root, trace)
    local controller, grip = pawn.Controller, pawn.PhysicsHandle
    local hosting = trace ~= nil
    local connected = valid(driver.ServerConnection) == true
    assert(connected ~= hosting and pawn:IsLocallyControlled() and pawn:HasAuthority() == hosting
        and valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn)
        and valid(grip) and same(grip:GetGrabbedComponent(), root) and same(pawn.HeldObject, lantern),
        "unqualified local lantern grip")
    local rotation = controller:GetControlRotation()
    local start_yaw, pitch, roll = rotation.Yaw, rotation.Pitch, rotation.Roll
    local began = os.clock()
    local world_address = world:GetAddress()
    local sweep, finished
    local function stop()
        finished = true
        CancelDelayedAction(sweep)
    end
    sweep = LoopInGameThreadWithDelay(25, function()
        if finished then return end
        if not current_world_is(world_address) or not same(world.NetDriver, driver)
            or not same(controller.Pawn, pawn) or pawn.Dead ~= false or not same(pawn.HeldObject, lantern) then
            stop()
            return
        end
        local fraction = math.min((os.clock() - began) / 1.2, 1)
        controller:SetControlRotation({Pitch = pitch, Yaw = start_yaw + 65 * fraction, Roll = roll})
        if fraction == 1 then
            if trace then trace.swept = true end
            stop()
        end
    end)
    return "sweeping"
end

local function place_lantern(pawn, lantern)
    assert(not valid(pawn.HeldObject), "the carrier is not empty-handed")
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local origin, eye = pawn:K2_GetActorLocation(), pawn.Camera:K2_GetComponentLocation()
    for _, heading in ipairs({180, 270, 90, 0}) do
        local bearing = math.rad(heading)
        local target = {X = origin.X + 110 * math.cos(bearing), Y = origin.Y + 110 * math.sin(bearing), Z = origin.Z - 25}
        if api:LineTraceSingle(pawn, eye, target, 1, true, {pawn, lantern}, 0, {}, true, color, color, 0) == false
            and lantern:K2_SetActorLocation(target, false, {}, true) then
            lantern:ForceNetUpdate()
            return origin
        end
    end
    error("no clear lantern approach")
end

local function host_grab(pawn, lantern, root)
    local eye, target = pawn.Camera:K2_GetComponentLocation(), root:K2_GetComponentLocation()
    local x, y, z = target.X - eye.X, target.Y - eye.Y, target.Z - eye.Z
    local rotation = {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
    pawn.Controller:SetControlRotation(rotation)
    pawn:K2_SetActorRotation({Pitch = 0, Yaw = rotation.Yaw, Roll = 0}, false)
    pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
    pawn:LookTrace()
    assert(same(pawn.ActorBeingLookedAt, lantern), "stock LookTrace did not select the exact lantern")
    pawn:Grab_Release_Object(false, false, true)
    assert(holds(pawn, lantern, root), "the host's stock grip did not grab the exact lantern")
    return "grabbed"
end

local function arm_trace(world, driver, pawn, lantern, root, role)
    assert(not watch and holds(pawn, lantern, root) == (role == 3), "wrong local lantern grip at trace start")
    local current = {world = world, pawn = pawn, actor = lantern, root = root, role = role, started = os.clock(), rows = {}}
    local world_address = world:GetAddress()
    watch = current
    current.timer = LoopInGameThreadWithDelay(25, function()
        if not current.timer then return end
        if watch ~= current or #current.rows >= 240 or not current_world_is(world_address) then
            CancelDelayedAction(current.timer)
            current.timer = nil
            return
        end
        local ok, issue = pcall(function()
            assert(same(world.NetDriver, driver) and same(pawn:GetWorld(), world) and pawn.Dead == false
                and same(lantern:GetWorld(), world) and not lantern:IsActorBeingDestroyed()
                and same(lantern.RootComponent, root) and lantern:GetLocalRole() == role and lantern.bReplicateMovement == true,
                "lantern trace lost its scoped stock actor")
            local point, rotation = root:K2_GetComponentLocation(), root:K2_GetComponentRotation()
            local visible = lantern.bHidden == false and root:IsVisible() and not root.bHiddenInGame and not root.bOnlyOwnerSee
            current.rows[#current.rows + 1] = string.format("%.3f %.3f %.3f %.3f %.3f %.3f %.3f %d %d",
                os.clock() - current.started, point.X, point.Y, point.Z, rotation.Pitch, rotation.Yaw, rotation.Roll,
                visible and 1 or 0, holds(pawn, lantern, root) and 1 or 0)
        end)
        if not ok then
            current.error = issue
            CancelDelayedAction(current.timer)
            current.timer = nil
        end
    end)
    return "armed"
end

local impact_watch, impact_hooked
local prop_hit = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C:" ..
    "BndEvt__PhysicsProp_Master_BP_PropMesh_K2Node_ComponentBoundEvent_0_ComponentHitSignature__DelegateSignature"
local pawn_hit = "/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage"
local npc_hit = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage"

local function other_player(world, pawn)
    local other
    for _, player in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(player) and same(player:GetWorld(), world) and not same(player, pawn)
            and not player:IsActorBeingDestroyed() and player.Dead == false then
            assert(not other, "expected exactly one other player")
            other = player
        end
    end
    assert(valid(other), "the other stock player could not be found")
    return other
end

local function position_text(point)
    return ("%.3f %.3f %.3f"):format(point.X, point.Y, point.Z)
end

local function impact_lane(pawn, guest, lantern)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local start = pawn:K2_GetActorLocation()
    local half = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local radius = pawn.CapsuleComponent:GetScaledCapsuleRadius()
    local ignored = {pawn, guest, lantern}
    for _, heading in ipairs({0, 90, 180, 270, 45, 135, 225, 315}) do
        local angle = math.rad(heading)
        local x, y = start.X + math.cos(angle) * 315, start.Y + math.sin(angle) * 315
        local hit = {}
        if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=start.Z+100}, {X=x,Y=y,Z=start.Z-half-85}, 0,
            true, ignored, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            local finish = {X=x,Y=y,Z=hit.ImpactPoint.Z+half+3}
            if hit.ImpactNormal.Z > .9 and math.abs(finish.Z-start.Z) < 15
                and not trace:CapsuleTraceSingle(pawn, start, finish, radius, half-8, 0, false, ignored, 0, {},
                    true, color, color, 0)
                and guest:K2_TeleportTo(finish, {Pitch=0,Yaw=heading+180,Roll=0}) then
                guest:ForceNetUpdate()
                return position_text(guest:K2_GetActorLocation())
            end
        end
    end
    error("expected a clear flat stock prop impact lane")
end

local function impact_probe(action, world, pawn, lantern, root)
    local other = other_player(world, pawn)
    local hosting = lane == "first"
    local npc
    if action:match("npc$") then
        npc = StaticFindObject(stalker_path)
        assert(valid(npc) and npc.Alive == true and same(npc:GetWorld(), world), "expected the original Observatory stalker")
        action = action:sub(1, -4)
    end
    local function health(target)
        if same(target, npc) then return target.Health end
        return valid(target.Save) and target.Save.PlayerHealth or "-"
    end
    if action == "impactlaneguest" and hosting and npc then return impact_lane(npc, other, lantern) end
    if action == "impactlane" and hosting then return impact_lane(pawn, other, lantern) end
    if (action == "impactplacehost" or action == "impactplaceguest") and hosting then
        local source = action == "impactplacehost" and pawn or other
        local target = npc or (same(source, pawn) and other or pawn)
        assert(not valid(source.HeldObject), "the impact thrower was not empty-handed")
        local start, finish = source:K2_GetActorLocation(), target:K2_GetActorLocation()
        local dx, dy = finish.X - start.X, finish.Y - start.Y
        local distance = math.sqrt(dx*dx + dy*dy)
        assert(distance > 300 and distance < 330, "the throwers left the test lane")
        local point = {X = start.X + 110*dx/distance, Y = start.Y + 110*dy/distance, Z = start.Z - 25}
        assert(lantern:K2_SetActorLocation(point, false, {}, true), "the lantern could not enter the test lane")
        local bone = FName("None", EFindName.FNAME_Find)
        root:SetPhysicsLinearVelocity({X=0,Y=0,Z=0}, false, bone)
        root:SetPhysicsAngularVelocityInDegrees({X=0,Y=0,Z=0}, false, bone)
        lantern:ForceNetUpdate()
        return position_text(point)
    end
    if action == "impactposition" then return position_text(root:K2_GetComponentLocation()) end
    if action == "impactgrab" and hosting then return host_grab(pawn, lantern, root) end
    if (action == "impactaim" or action == "impactthrow" and hosting) and holds(pawn, lantern, root) then
        local eye, target = pawn.Camera:K2_GetComponentLocation(), (npc or other):K2_GetActorLocation()
        local x, y, z = target.X-eye.X, target.Y-eye.Y, target.Z+12-eye.Z
        local rotation = {Pitch=math.deg(math.atan(z, math.sqrt(x*x+y*y))),Yaw=math.deg(math.atan(y,x)),Roll=0}
        pawn.Controller:SetControlRotation(rotation)
        pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
        if action == "impactaim" then return "aimed" end
        local launch = root:K2_GetComponentLocation()
        local aim = pawn.Camera:GetForwardVector()
        local toolbox = lantern:GetClass():GetFName():ToString() == "Prop_ToolBox01_C"
        if toolbox then
            -- Stock sleep can clear the instigator during a grip. Exercise that state on every run.
            lantern.Instigator = nil
            assert(not valid(lantern.Instigator), "could not stage the missing thrower")
        end
        pawn:Grab_Release_Object(true, false, false)
        assert(not holds(pawn, lantern, root), "stock throw did not release the prop")
        if toolbox then assert(same(lantern.Instigator, pawn), "stock toolbox throw lost its host attribution") end
        return table.concat({position_text(launch), position_text(aim),
            position_text(root:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find)))}, ";")
    end
    if action == "impactarmguest" or action == "impactarmhost" then
        local target = npc or ((action == "impactarmguest") == hosting and other or pawn)
        local thrower = npc and (hosting and other or pawn) or (same(target, pawn) and other or pawn)
        local body = target.CapsuleComponent
        impact_watch = {lantern=lantern, target=target, hp=health(target), hits={},
            thrower=thrower, damage_calls=0, attributed=0,
            position=body:K2_GetComponentLocation(),
            response=root:GetCollisionResponseToChannel(body:GetCollisionObjectType()),
            target_response=body:GetCollisionResponseToChannel(root:GetCollisionObjectType())}
        if not impact_hooked then
            assert(valid(StaticFindObject(prop_hit)), "stock prop impact event unavailable")
            RegisterHook(prop_hit, function(ctx, _, hit_actor, _, impulse)
                local sample = impact_watch
                if not sample or not same(ctx:get(), sample.lantern) or not same(hit_actor:get(), sample.target) then return end
                local value = impulse:get()
                sample.hits[#sample.hits+1] = math.sqrt(value.X^2 + value.Y^2 + value.Z^2)
            end)
            local function observe_damage(ctx, _, _, instigator)
                local sample = impact_watch
                if not sample or not same(ctx:get(), sample.target) then return end
                sample.damage_calls = sample.damage_calls + 1
                if same(instigator:get(), sample.thrower) then sample.attributed = sample.attributed + 1 end
            end
            RegisterHook(pawn_hit, observe_damage)
            RegisterHook(npc_hit, observe_damage)
            impact_hooked = true
        end
        return "armed"
    end
    if action == "impactread" then
        local sample = impact_watch
        assert(sample and same(sample.lantern, lantern), "expected an armed prop impact observation")
        impact_watch = nil
        local point = root:K2_GetComponentLocation()
        local body = sample.target.CapsuleComponent
        return table.concat({sample.hp, health(sample.target), position_text(sample.position),
            position_text(point), sample.response, sample.target_response,
            root:GetCollisionResponseToChannel(body:GetCollisionObjectType()), table.concat(sample.hits, " "),
            root:GetMass(), sample.damage_calls, sample.attributed}, ";")
    end
    error("unknown stock prop impact probe action")
end

local function observer_probe(action, world, driver, pawn, lantern, root)
    local hosting = lane == "first"
    local role = hosting and 3 or 1
    local controller = pawn.Controller
    local connected = valid(driver.ServerConnection) == true
    assert(connected ~= hosting and (not hosting or #driver.ClientConnections == 1)
        and pawn:IsLocallyControlled() and pawn:HasAuthority() == hosting and lantern:GetLocalRole() == role
        and valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn),
        "unqualified stock lantern host or passive observer")
    if action == "hostignite" and hosting then
        assert(not holds(pawn, lantern, root))
        lantern:SetOnFire(5, 0.2, pawn)
        assert(lantern.Lit == true, "the stock fire function did not light the lantern")
        return "lit"
    end
    if action == "hostgrab" and hosting then
        place_lantern(pawn, lantern)
        return host_grab(pawn, lantern, root)
    end
    if action == "tracearm" then return arm_trace(world, driver, pawn, lantern, root, role) end
    assert(watch and same(watch.world, world) and same(watch.pawn, pawn)
        and same(watch.actor, lantern) and same(watch.root, root) and watch.role == role,
        "no matching stock lantern trace is armed")
    assert(not watch.error, watch.error)
    if action == "hostsweep" and hosting then return sweep_lantern(world, driver, pawn, lantern, root, watch) end
    if action == "tracemark" and not watch.mark then
        if hosting and not watch.swept then return "sweeping" end
        assert(holds(pawn, lantern, root) == hosting, "the stock grip changed before the sweep finished")
        watch.mark = #watch.rows
        return "marked"
    end
    assert(watch.mark and watch.mark > 0, "the stock lantern sweep has no end marker")
    if action == "hostdrop" and hosting then
        assert(holds(pawn, lantern, root), "the host released the lantern before stock drop")
        pawn:Grab_Release_Object(false, true, false)
        assert(not holds(pawn, lantern, root), "the host's stock drop did not release the lantern")
        return "dropped"
    end
    if action == "traceread" then
        local result = string.format("%d %d %.2f|%s|end", role, watch.mark, lantern.NetUpdateFrequency,
            table.concat(watch.rows, "|"))
        if watch.timer then
            CancelDelayedAction(watch.timer)
            watch.timer = nil
        end
        watch = nil
        return result
    end
    error("unknown stock lantern trace action")
end

local function dynamic_probe(action, world, pawn, source_prop)
    assert(pawn:IsLocallyControlled() and pawn:HasAuthority() == (lane == "first"), "wrong dynamic prop seat")
    local class = assert(StaticFindObject(toolbox_path)):GetClass()
    if action == "dynamicspawnhost" or action == "dynamicspawnguest" then
        assert(lane == "first" and not valid(dynamic), "dynamic prop already spawned")
        local other = other_player(world, pawn)
        local first = action == "dynamicspawnhost" and pawn or other
        local last = same(first, pawn) and other or pawn
        local a, b = first:K2_GetActorLocation(), last:K2_GetActorLocation()
        local distance = math.sqrt((b.X-a.X)^2+(b.Y-a.Y)^2)
        assert(distance > 250, "dynamic prop lane is too short")
        local x, y = a.X + (b.X-a.X)*120/distance, a.Y + (b.Y-a.Y)*120/distance
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local hit = {}
        assert(trace:LineTraceSingle(first, {X=x,Y=y,Z=a.Z+20}, {X=x,Y=y,Z=a.Z-170}, 0,
            true, {pawn, other, source_prop}, 0, hit, true, color, color, 0), "no floor for the dynamic prop")
        hit = hit.OutHit or hit
        local point = {X=x,Y=y,Z=hit.ImpactPoint.Z+12}
        dynamic = world:SpawnActor(class, point, {Pitch=0,Yaw=0,Roll=0})
        assert(valid(dynamic) and not dynamic:HasAnyFlags(EObjectFlags.RF_WasLoaded)
            and dynamic.PropMesh:IsSimulatingPhysics(FName("None")), "dynamic toolbox was not simulated")
        dynamic:SetReplicateMovement(false)
        dynamic:SetReplicates(true)
        dynamic:ForceNetUpdate()
        assert(dynamic.bReplicates and not dynamic.bReplicateMovement, "the new prop already publishes motion")
        return position_text(point)
    end
    local actor = dynamic
    if lane == "second" then
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local found = {}
        api:GetAllActorsOfClass(world, class, found)
        for _, candidate in ipairs(found.OutActors or found) do
            if type(candidate.type) == "function" and candidate:type() == "RemoteUnrealParam" then candidate = candidate:get() end
            if valid(candidate) and not candidate:HasAuthority() and not candidate:IsActorBeingDestroyed()
                and not candidate:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                assert(not actor, "ambiguous dynamic toolbox")
                actor = candidate
            end
        end
    end
    if action == "dynamicstate" and (not valid(actor) or actor:IsActorBeingDestroyed()) then return "absent" end
    assert(valid(actor) and same(actor:GetWorld(), world))
    local root = actor.PropMesh
    if action == "dynamicstate" then
        return string.format("%s;%d;%d;%s", actor:GetFullName(), actor.bReplicateMovement and 1 or 0,
            holds(pawn, actor, root) and 1 or 0, position_text(actor:K2_GetActorLocation()))
    end
    if action == "dynamicgrab" then return host_grab(pawn, actor, root) end
    if action == "dynamicsweep" then
        return sweep_lantern(world, world.NetDriver, pawn, actor, root, lane == "first" and {} or nil)
    end
    if action == "dynamicdrop" then
        assert(holds(pawn, actor, root))
        pawn:Grab_Release_Object(false, true, false)
        return "dropped"
    end
    assert(action == "dynamicremove" and lane == "first" and actor:HasAuthority() and not valid(pawn.HeldObject))
    actor:K2_DestroyActor()
    dynamic = nil
    return "removed"
end

local function streamed_probe(action, world, pawn, source_prop)
    assert(pawn:IsLocallyControlled() and pawn:HasAuthority() == (lane == "first"), "wrong streamed prop seat")
    if action == "streamload" then
        local loading = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
        placed_stream = loading:LoadLevelInstance(world, "/Game/Classes/Maps/Maps_OverWorld/Observitory_P",
            {X = 65000, Y = 65000, Z = 2500}, {Pitch = 0, Yaw = 0, Roll = 0}, {}, "CoopPhysicsSublevel", nil, false)
        assert(valid(placed_stream))
        placed_scope = world
        return "loading"
    end
    assert(valid(placed_stream) and same(placed_scope, world))
    if action == "streamunload" then
        placed_stream:SetShouldBeVisible(false)
        placed_stream:SetShouldBeLoaded(false)
        return "unloading"
    end
    if action == "streamhide" or action == "streamshow" then
        placed_stream:SetShouldBeVisible(action == "streamshow")
        return "visibility changed"
    end
    if not placed_stream:IsLevelVisible() then return "absent" end
    local level = placed_stream:GetLoadedLevel()
    local level_path = level:GetFullName():match("^%S+ (.+)$")
    if action == "streamloot" then
        local path = level_path .. ".Eqp_Bolt_C_1"
        local item = StaticFindObject(path)
        local visible = valid(item) and same(item:GetOuter(), level) and item.bHidden == false and item:GetActorEnableCollision()
        local original = StaticFindObject(map .. ":PersistentLevel.Eqp_Bolt_C_1")
        local untouched = valid(original) and original.bHidden == false and original:GetActorEnableCollision()
        return table.concat({path, tostring(visible), tostring(untouched)}, ";")
    end
    local actor = StaticFindObject(level_path .. ".Prop_ToolBox01_C_7")
    assert(valid(actor) and same(actor:GetOuter(), level), "the sublevel did not contain the stock toolbox")
    local root = actor.PropMesh
    if action == "streamstate" then
        return table.concat({actor:GetFullName(), tostring(actor:GetLocalRole()),
            tostring(actor.bReplicateMovement), tostring(holds(pawn, actor, root)),
            tostring(actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)), position_text(actor:K2_GetActorLocation())}, ";")
    end
    if action == "streamplacehost" or action == "streamplaceguest" then
        assert(lane == "first" and actor:HasAuthority() and actor.bReplicateMovement == true
            and root:IsSimulatingPhysics(FName("None")), "the placed prop is missing authoritative motion")
        local other = other_player(world, pawn)
        local thrower = action == "streamplacehost" and pawn or other
        local opposite = same(thrower, pawn) and other or pawn
        local from, to = thrower:K2_GetActorLocation(), opposite:K2_GetActorLocation()
        local dx, dy = to.X-from.X, to.Y-from.Y
        local distance = math.sqrt(dx*dx+dy*dy)
        assert(distance > 250, "the streamed carry lane is too short")
        local x, y = from.X + 110*dx/distance, from.Y + 110*dy/distance
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local hit = {}
        assert(trace:LineTraceSingle(thrower, {X=x,Y=y,Z=from.Z+20}, {X=x,Y=y,Z=from.Z-170}, 0,
            true, {pawn, other, actor}, 0, hit, true, color, color, 0), "no floor for the streamed prop")
        hit = hit.OutHit or hit
        local point = {X=x,Y=y,Z=hit.ImpactPoint.Z+12}
        assert(actor:K2_SetActorLocation(point, false, {}, true))
        local zero, bone = {X=0,Y=0,Z=0}, FName("None")
        root:SetPhysicsLinearVelocity(zero, false, bone)
        root:SetPhysicsAngularVelocityInDegrees(zero, false, bone)
        actor:ForceNetUpdate()
        return position_text(point)
    end
    if action == "streamlane" then
        assert(lane == "first")
        return impact_lane(pawn, other_player(world, pawn), source_prop)
    end
    if action == "streamgrab" then return host_grab(pawn, actor, root) end
    if action == "streamsweep" then
        return sweep_lantern(world, world.NetDriver, pawn, actor, root, lane == "first" and {} or nil)
    end
    assert(action == "streamdrop" and holds(pawn, actor, root))
    pawn:Grab_Release_Object(false, true, false)
    return "dropped"
end

local function verify(action, pawn_name)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    local toolbox = action:match("^heavyimpact")
    if toolbox then action = action:sub(6) end
    local lantern = StaticFindObject(toolbox and toolbox_path or path)
    local root = valid(lantern) and lantern.PropMesh
    local mesh = StaticFindObject("/Script/Engine.StaticMeshComponent")
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver)
        and valid(lantern) and same(lantern:GetWorld(), world) and same(lantern:GetOuter(), world.PersistentLevel)
        and lantern:HasAnyFlags(EObjectFlags.RF_WasLoaded)
        and not lantern:IsActorBeingDestroyed() and lantern.bReplicateMovement == true
        and same(root, lantern.RootComponent) and valid(mesh) and root:IsA(mesh),
        "unqualified authored Observatory physics prop")
    local pawn_path = pawn_name:match("^Player_FP_C (.+)$")
    local pawn = pawn_path and StaticFindObject(pawn_path)
    assert(valid(pawn) and pawn:GetFullName() == pawn_name and same(pawn:GetWorld(), world) and pawn.Dead == false,
        "unqualified Observatory pawn")
    if action:match("^dynamic") then return dynamic_probe(action, world, pawn, lantern) end
    if action:match("^stream") then return streamed_probe(action, world, pawn, lantern) end
    if action:match("^impact") then
        assert(pawn:IsLocallyControlled() and pawn:HasAuthority() == (lane == "first"), "wrong impact seat")
        return impact_probe(action, world, pawn, lantern, root)
    end
    if action:match("^host") or action:match("^trace") then
        return observer_probe(action, world, driver, pawn, lantern, root)
    end
    if lane == "second" then
        assert(action == "sweep" and not lantern:HasAuthority(), "unexpected guest lantern probe action")
        return sweep_lantern(world, driver, pawn, lantern, root)
    end
    assert(not valid(driver.ServerConnection) and #driver.ClientConnections == 1
        and lantern:HasAuthority() and pawn:HasAuthority() and not pawn:IsLocallyControlled(),
        "unqualified authority guest")
    if action == "approach" then
        local origin = place_lantern(pawn, lantern)
        return string.format("%.3f %.3f %.3f", origin.X, origin.Y, origin.Z)
    end
    assert(action == "sample", "unexpected lantern probe action")
    local point = root:K2_GetComponentLocation()
    return string.format("%d %.3f %.3f %.3f", holds(pawn, lantern, root) and 1 or 0, point.X, point.Y, point.Z)
end

LoopInGameThreadWithDelay(50, function()
    local input = io.open(command, "rb")
    if not input then return end
    local request = input:read(513)
    input:close()
    if not request or #request > 512 then return end
    local ticket, action, pawn = request:match("^(%x+)\t([a-z]+)\t([^\r\n]+)$")
    if not ticket or #ticket ~= 32 or last == ticket then return end
    last = ticket
    local ok, result = pcall(verify, action, pawn)
    local output = io.open(reply, "wb")
    if output then
        output:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(result):gsub("[\r\n\t]", " ")))
        output:close()
    end
end)
