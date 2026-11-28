local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-npc-override-"
local command_limit = 4096
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local outside_campaign = "/Game/PolygonHorrorAsylum/Maps/Overview.Overview"
local class_path = "/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C"
local damage_key = "Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5"
local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
local last, authority_drone, authority_turret
local shooters = {}
local melee_aim, melee_stage
local melee_prop
local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
local color = {R=0, G=0, B=0, A=0}
local pipe_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"
local heavy_pipe_path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_HeavyPipe.MeleeWep_HeavyPipe_C"
local attack_range = "Range_7_1ABB464F45C04D4C4BDA07A41F83FF24"
local attack_radius = "Radius_9_4CAB95D84DE379A3C49DFD9CE1A93643"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function corpse_marker(marker, actor, role)
    if not valid(marker) or not same(marker:GetOwner(), actor) or (role and marker:GetLocalRole() ~= role) then
        return false
    end
    -- Corpse replication carries a non-zero ID in yaw and roll. Trader references use neutral rotation.
    local angle = marker:K2_GetActorRotation()
    return math.abs(angle.Yaw) > .5 or math.abs(angle.Roll) > .5
end

local function heading(from, target)
    local x, y, z = target.X - from.X, target.Y - from.Y, target.Z - from.Z
    return {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
end

local corpse_trace, carry_target, carry_approach, carry_retreat

local function holds_corpse(pawn, actor)
    return same(pawn.HeldObject, actor) and same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Mesh)
end

local function corpse_carry(action, world, pawn, actor, host)
    local mesh = actor.Mesh
    local marker = corpse_trace and corpse_trace.marker
    if not valid(marker) then
        for _, candidate in ipairs(FindAllOf("TargetPoint") or {}) do
            if corpse_marker(candidate, actor, host and 3 or 1) then
                assert(not marker, "unexpected second corpse marker")
                marker = candidate
            end
        end
    end
    assert(valid(marker) and not actor.Alive, "the test needs a bound stock corpse")
    local function rotation_byte(angle) return math.floor(angle * 256 / 360 + .5) % 256 end
    local rotation = marker:K2_GetActorRotation()
    local number = (rotation_byte(rotation.Roll) % 128) * 256 + rotation_byte(rotation.Yaw)
    local code = rotation_byte(rotation.Pitch)
    local index = (code <= 60 and code or code >= 196 and code - 135 or 0) - 1
    assert(number > 0 and index >= 0 and index < mesh:GetNumBones(), "the corpse marker has no skeletal anchor")
    local anchor = mesh:GetBoneName(index)
    if action == "corpse-grab" or action == "corpse-focus" then
        assert((action == "corpse-grab") == host and not valid(pawn.HeldObject), "wrong seat for the stock grip check")
        local target = carry_approach
        if not host then
            local point = mesh:GetSocketLocation(anchor)
            target = {X=point.X+100, Y=point.Y, Z=point.Z+pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()-10}
        end
        assert(pawn:K2_SetActorLocation(target, false, {}, true), "could not reach the carry lane")
        local eye = pawn.Camera:K2_GetComponentLocation()
        for index = 0, mesh:GetNumBones() - 1 do
            local bone = mesh:GetBoneName(index)
            if mesh:IsSimulatingPhysics(bone) then
                local angle = heading(eye, mesh:GetSocketLocation(bone))
                pawn.Controller:SetControlRotation(angle)
                pawn:K2_SetActorRotation({Pitch=0, Yaw=angle.Yaw, Roll=0}, true)
                pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
                pawn:LookTrace()
                if same(pawn.ActorBeingLookedAt, actor) and mesh:IsSimulatingPhysics(pawn["Hit Bone Name"]) then
                    if not host then return "focused" end
                    pawn:Grab_Release_Object(false, false, false)
                    if holds_corpse(pawn, actor) then return "grabbed" end
                    error("the stock grab function did not hold the traced humanoid corpse")
                end
            end
        end
        error("the host stock LookTrace and PhysicsHandle did not grab the corpse")
    elseif action == "corpse-arm" then
        assert(not corpse_trace and (holds_corpse(pawn, actor) == true) == host, "wrong local grip at trace start")
        local clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local watch = {actor=actor, marker=marker, clock=clock, start=clock:GetRealTimeSeconds(world), rows={}}
        corpse_trace = watch
        LoopInGameThreadWithDelay(25, function()
            if corpse_trace ~= watch or #watch.rows >= 240 or not valid(actor) or actor:IsActorBeingDestroyed()
                or not same(actor:GetWorld(), world) then return true end
            local now = clock:GetRealTimeSeconds(world)
            local point, target = mesh:GetSocketLocation(anchor), marker:K2_GetActorLocation()
            local phase = rotation_byte(marker:K2_GetActorRotation().Roll) >= 128
            local angle = mesh:GetSocketRotation(anchor)
            local angular = mesh:GetPhysicsAngularVelocityInDegrees(anchor)
            local speed = math.sqrt(angular.X^2 + angular.Y^2 + angular.Z^2)
            watch.rows[#watch.rows + 1] = ("%.3f %.2f %.2f %.2f %.2f %.2f %.2f %d %d %d %d %.2f %.2f %.2f %.2f %d"):format(
                now-watch.start, point.X, point.Y, point.Z, target.X, target.Y, target.Z, phase and 1 or 0,
                mesh:GetCollisionEnabled(), holds_corpse(pawn, actor) and 1 or 0,
                mesh:IsSimulatingPhysics(anchor) and 1 or 0, angle.Pitch, angle.Yaw, angle.Roll, speed,
                mesh:GetCollisionResponseToChannel(0))
            if watch.sweep then
                local step = math.min(1, (now-watch.sweep.start)/1.2)
                local start, finish = watch.sweep.origin, carry_retreat
                pawn:K2_SetActorLocation({X=start.X+(finish.X-start.X)*step, Y=start.Y+(finish.Y-start.Y)*step,
                    Z=start.Z+(finish.Z-start.Z)*step}, false, {}, true)
                if step == 1 then watch.sweep = nil end
            end
        end)
        return "armed"
    end
    local watch = corpse_trace
    assert(watch and same(watch.actor, actor), "no matching corpse trace")
    if action == "corpse-sweep" then
        assert(host and holds_corpse(pawn, actor), "the host must still grip the corpse")
        watch.sweep = {start=watch.clock:GetRealTimeSeconds(world), origin=pawn:K2_GetActorLocation()}
        return "sweeping"
    elseif action == "corpse-mark" then
        if watch.sweep then return "sweeping" end
        watch.mark = #watch.rows
        return "marked"
    elseif action == "corpse-drop" then
        assert(host and holds_corpse(pawn, actor), "the host released the corpse before the stock drop")
        pawn:Grab_Release_Object(false, true, false)
        assert(not holds_corpse(pawn, actor), "the host's stock drop did not release the corpse")
        return "dropped"
    elseif action == "corpse-read" then
        corpse_trace = nil
        return ("%d %d|%s|end"):format(number, watch.mark, table.concat(watch.rows, "|"))
    end
    error("unknown corpse probe action")
end

local function authoritative_guest(world, name)
    local guest
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and not candidate:IsActorBeingDestroyed() and candidate:HasAuthority()
            and not candidate:IsLocallyControlled() and same(candidate:GetWorld(), world)
            and (not name or candidate:GetFullName() == name) then
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

local function seed_pipe(world, path, owner)
    local guest = owner or authoritative_guest(world)
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    local factory = StaticFindObject(factory_path)
    assert(valid(class) and valid(factory), "stock melee factory unavailable")
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
    error("the melee fixture needs an empty utility slot")
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
    local attack = pipe.ComboAttacks[1]
    local range, radius = attack[attack_range], attack[attack_radius]
    local ignored = {guest}
    local endpoint = step(eye, range)
    if trace:LineTraceSingleForObjects(pipe, eye, endpoint, {0,4}, true, ignored, 0, line, true, color, color, 0) then
        endpoint = (line.OutHit or line).ImpactPoint
        if (endpoint.X-eye.X)^2 + (endpoint.Y-eye.Y)^2 + (endpoint.Z-eye.Z)^2 <= radius^2 then return false, "near surface" end
    end
    local blocking = trace:SphereTraceMulti(pipe, eye, step(endpoint, -radius), radius, 6, false, ignored, 0, sphere, true, color, color, 0)
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

local function stage_melee(world, attempt, prop, owner)
    local guest = owner or authoritative_guest(world)
    local target = prop or melee_target(world, true)
    local eye = guest.Camera:K2_GetComponentLocation()
    assert(valid(target) and target:HasAuthority() and (prop or target.Alive and target.bReplicates),
        "stock melee target unavailable")
    if not melee_stage or not same(melee_stage.world, world) or not same(melee_stage.target, target) then
        melee_stage = {world=world, target=target, yaw=guest:K2_GetActorRotation().Yaw}
    end
    local bearings, ranges = {0, 45, 315, 90, 270, 180}, {160, 140, 195, 125}
    local blockers = {}
    for index = 1, #ranges do
        local range = ranges[(index + attempt - 2) % #ranges + 1]
        for offset = 1, #bearings do
            local angle = math.rad(melee_stage.yaw + bearings[(offset + attempt - 2) % #bearings + 1])
            local center = {X=eye.X+range*math.cos(angle), Y=eye.Y+range*math.sin(angle), Z=eye.Z-24}
            if target:K2_SetActorLocation(center, false, {}, true) then
                local clear, status = melee_lane(guest, target, heading(eye, center))
                if clear then
                    target.Health = prop and 250 or 7
                    target:ForceNetUpdate()
                    return ("%.7f,%.7f,%.7f;%s"):format(center.X, center.Y, center.Z, target:GetFullName())
                end
                blockers[status] = true
            end
        end
    end
    local failures = {}
    for status in pairs(blockers) do failures[#failures+1] = status end
    error("no clear stock melee contact lane: " .. table.concat(failures, ", "))
end

local function aim_melee(world, pawn, center)
    local active = {world=world, pawn=pawn, expires=os.clock()+3}
    melee_aim = active
    local function aim()
        if melee_aim ~= active or os.clock() >= active.expires then return false end
        local viewport = FindFirstOf("GameViewportClient")
        if not valid(viewport) or not same(viewport.World, active.world) or not valid(active.pawn)
            or active.pawn:IsActorBeingDestroyed() or not same(active.pawn:GetWorld(), active.world) then return false end
        local camera, controller = active.pawn.Camera, active.pawn.Controller
        if not valid(camera) or not valid(controller) or not same(camera:GetOwner(), active.pawn)
            or not same(controller.Pawn, active.pawn) or not same(controller:GetWorld(), active.world) then return false end
        local target = center
        if type(center) == "function" then target = center() end
        if not target then return false end
        local angle = heading(camera:K2_GetComponentLocation(), target)
        controller:SetControlRotation(angle)
        camera:K2_SetWorldRotation(angle, false, {}, true)
        return true
    end
    if not aim() then melee_aim = nil; return false end
    LoopInGameThreadWithDelay(15, function()
        if not aim() then
            if melee_aim == active then melee_aim = nil end
            return true
        end
    end)
    return true
end

local shooter_paths = {
    state = "/Game/Classes/NPCs/NPC_State_Conscript.NPC_State_Conscript_C",
    bandit = "/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C",
    drone = class_path,
    turret = "/Game/Classes/NPCs/Turret_MechGun_01_BP.Turret_MechGun_01_BP_C",
}

local function create_stock_npc(pawn, world, kind, point, yaw)
    local path = assert(shooter_paths[kind], "unknown NPC firing fixture")
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    assert(valid(class), "stock NPC class unavailable")
    local half = math.rad(yaw) / 2
    local transform = {Translation=point, Rotation={X=0,Y=0,Z=math.sin(half),W=math.cos(half)}, Scale3D={X=1,Y=1,Z=1}}
    local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
    assert(valid(actor) and same(actor:GetClass(), class) and same(actor:GetWorld(), world), "stock shooter spawn unavailable")
    assert(same(actor, statics:FinishSpawningActor(actor, transform, 0)) and actor:HasAuthority() and actor.Alive,
        "stock shooter did not start on the host")
    return actor, path
end

local function spawn_carry_target(world, pawn)
    assert(not valid(carry_target), "only one carry target is allowed")
    local origin = pawn:K2_GetActorLocation()
    local point, approach, retreat
    local radius = pawn.CapsuleComponent:GetScaledCapsuleRadius()
    local height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    for bearing = 0, 315, 45 do
        local angle = math.rad(bearing)
        local function floor(distance)
            local x, y = origin.X+distance*math.cos(angle), origin.Y+distance*math.sin(angle)
            local hit = {}
            if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=origin.Z+200}, {X=x,Y=y,Z=origin.Z-320},
                0, false, {pawn}, 0, hit, true, color, color, 0) then
                hit = hit.OutHit or hit
                local component = hit.Component and hit.Component:Get()
                if hit.ImpactNormal.Z > .9 and valid(component) and component:GetCollisionObjectType() == 0 then
                    return {X=x,Y=y,Z=hit.ImpactPoint.Z+height+5}
                end
            end
        end
        point, approach, retreat = floor(520), floor(420), floor(150)
        if point and approach and retreat and math.abs(retreat.Z-point.Z)<30
            and not trace:CapsuleTraceSingle(pawn, retreat, point, radius, height-10, 0, false,
                {pawn}, 0, {}, true, color, color, 0) then break end
        point = nil
    end
    assert(point, "no clear stock corpse test position")
    carry_approach, carry_retreat = approach, retreat
    local actor = create_stock_npc(pawn, world, "bandit", {X=origin.X+570, Y=origin.Y+180, Z=origin.Z+120}, 0)
    assert(actor:K2_SetActorLocation(point, false, {}, true), "could not place the stock bandit on the carry lane")
    actor:SetActorTickEnabled(false)
    local controller = actor.Controller
    if valid(controller) then controller:SetActorTickEnabled(false) end
    actor.bAlwaysRelevant = true
    actor:SetReplicates(true)
    actor:SetReplicateMovement(true)
    actor:ForceNetUpdate()
    carry_target = actor
    return "spawned"
end

local function spawn_stock_npc(pawn, world, kind)
    local origin = pawn:K2_GetActorLocation()
    local x = kind == "state" and 170 or 570
    local actor, path = create_stock_npc(pawn, world, kind, {X=origin.X+x,Y=origin.Y+180,Z=origin.Z+120}, 0)
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

local rivalry, rivalry_hook, rivalry_baseline
local rivalry_classes = {state = "NPC_State_Conscript_C", bandit = "NPC_Bandit_C"}

local function watch_rivalry(world)
    rivalry_baseline = {}
    for _, class in pairs(rivalry_classes) do
        for _, actor in ipairs(FindAllOf(class) or {}) do
            if valid(actor) and same(actor:GetWorld(), world) then rivalry_baseline[actor:GetAddress()] = true end
        end
    end
    return "watching"
end

local function rivalry_lane(pawn, guest)
    local center = pawn:K2_GetActorLocation()
    local other = guest:K2_GetActorLocation()
    local ignored = {pawn, guest}
    local function stood(x, y)
        if (x-other.X)^2 + (y-other.Y)^2 < 1750^2 then return end
        local hit = {}
        local found = trace:LineTraceSingle(pawn, {X=x,Y=y,Z=center.Z+300}, {X=x,Y=y,Z=center.Z-370},
            0, false, ignored, 0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        local surface = found and hit.Component and hit.Component:Get()
        if valid(surface) and surface:GetCollisionObjectType() == 0 and hit.ImpactNormal.Z > .8 then
            return {X=x,Y=y,Z=hit.ImpactPoint.Z+100}
        end
    end
    for step = 0, 11 do
        local angle = step * math.pi / 6
        local x, y = center.X + math.cos(angle)*2150, center.Y + math.sin(angle)*2150
        local dx, dy = -math.sin(angle)*425, math.cos(angle)*425
        local first, second = stood(x+dx,y+dy), stood(x-dx,y-dy)
        if first and second and math.abs(first.Z-second.Z) < 80
            and trace:LineTraceSingle(pawn, first, second, 0, true, ignored, 0, {}, true, color, color, 0) == false then
            return first, second
        end
    end
    error("no separate firing lane for the two NPC factions")
end

local function begin_rivalry(pawn, world)
    local guest = authoritative_guest(world)
    for _, actor in pairs(shooters) do if valid(actor) then actor:K2_DestroyActor() end end
    shooters = {}
    local first, second = rivalry_lane(pawn, guest)
    local state = create_stock_npc(pawn, world, "state", first, heading(first, second).Yaw)
    local bandit = create_stock_npc(pawn, world, "bandit", second, heading(second, first).Yaw)
    assert(state.Max_Health > 1 and bandit.Max_Health > 1 and state.Faction ~= bandit.Faction,
        "expected two stock opposing factions")
    rivalry = {state = state, bandit = bandit, hits = {0, 0}, deaths = {}, targets = {}}
    if not rivalry_hook then
        RegisterHook("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage",
            function(context, _, _, instigator)
                if not rivalry then return end
                local victim, source = context:get(), instigator:get()
                local index = same(victim, rivalry.state) and same(source, rivalry.bandit) and 1
                    or same(victim, rivalry.bandit) and same(source, rivalry.state) and 2
                if index then
                    rivalry.hits[index] = rivalry.hits[index] + 1
                    rivalry.targets[index] = rivalry.targets[index] or same(source.CombatTarget, victim)
                    rivalry.deaths[index] = rivalry.deaths[index] or victim.Alive == false
                end
            end)
        rivalry_hook = true
    end
    for _, actor in ipairs({state, bandit}) do
        actor:ForceNetUpdate()
    end
    return ("%.2f,%.2f;%d,%d"):format(state.Max_Health, bandit.Max_Health, state.Faction, bandit.Faction)
end

local function start_rivalry_ai()
    assert(rivalry, "the isolated stock rivalry was not spawned")
    -- Preserve the actor's own initialization if its startup delay elapsed
    -- while the guest waited for replication.
    for _, actor in ipairs({rivalry.state, rivalry.bandit}) do
        if not actor.Initialized then actor:InitializeAI() end
        assert(actor.Initialized and (actor.Alive == false or valid(actor.Controller)), "stock rivalry AI did not initialize")
    end
    return "initialized"
end

local function rivalry_member(world, host, kind)
    if host then return rivalry and rivalry[kind] end
    local found
    if not rivalry_baseline then return end
    local class = rivalry_classes[kind]
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not rivalry_baseline[actor:GetAddress()]
            and actor:GetLocalRole() == 1 and not actor:IsActorBeingDestroyed() then
            if found then return end
            found = actor
        end
    end
    return found
end

local function observe_rivalry(world, host)
    local first, second = rivalry_member(world, host, "state"), rivalry_member(world, host, "bandit")
    if not valid(first) or not valid(second) then return "waiting" end
    local function sample(actor, target, index)
        local role = host and 3 or 1
        if not actor.bReplicates or actor:GetLocalRole() ~= role then return "waiting" end
        local markers = 0
        if not actor.Alive then
            for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
                if corpse_marker(marker, actor, role) then markers = markers + 1 end
            end
        end
        local weapon = actor.Weapon_Ranged
        local path = valid(weapon) and weapon:GetClass():GetFullName():match(" (%S+)$") or "-"
        return ("%s,%s,%d,%.2f,%s,%d,%s,%s,%s,%s"):format(tostring(actor.Alive),
            tostring(actor.Mesh:IsAnySimulatingPhysics()), markers, actor.Health,
            tostring(same(actor.CombatTarget, target) or (host and rivalry.targets[3-index] == true)),
            host and rivalry.hits[index] or 0, tostring(host and rivalry.deaths[index] == true),
            path, tostring(actor.Initialized), tostring(valid(actor.Controller) == true))
    end
    return sample(first, second, 1) .. ";" .. sample(second, first, 2)
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
        if corpse_marker(marker, actor, host and 3 or 1) then
            markers = markers + 1
        end
    end
    local shooter = host and same(actor.LastDamagedMe, shooters.state) and "state" or "-"
    return ("%s;%s;%d;%s;%s"):format(tostring(actor.Alive), tostring(mesh:IsAnySimulatingPhysics()),
        markers, shooter, actor:GetFullName())
end

local turret_class = shooter_paths.turret

local shot_turret, fired_count, fire_hook
local turret_effects, turret_effect_hooks

local function arm_turret_effects(turret)
    local target = turret:K2_GetActorLocation()
    turret_effects = {turret = turret, target = {X=target.X, Y=target.Y, Z=target.Z},
        blasts = 0, sounds = 0, smoke = 0, flames = 0, loops = 0}
    if turret_effect_hooks then return "armed" end
    local function at_turret(point)
        local target = turret_effects and turret_effects.target
        return target and (point.X-target.X)^2 + (point.Y-target.Y)^2 + (point.Z-target.Z)^2 < 10^2
    end
    RegisterHook("/Script/Niagara.NiagaraFunctionLibrary:SpawnSystemAtLocation", function(_, _, asset, position)
        if not turret_effects then return end
        local particle = asset:get()
        if valid(particle) and particle:GetFName():ToString() == "FX_Blast_FragGrenade_Converted"
            and at_turret(position:get()) then turret_effects.blasts = turret_effects.blasts + 1 end
    end)
    RegisterHook("/Script/Niagara.NiagaraFunctionLibrary:SpawnSystemAttached", function(_, asset, parent)
        if not turret_effects then return end
        local turret, particle, mount = turret_effects.turret, asset:get(), parent:get()
        if not valid(turret) or not valid(particle) then return end
        local name = particle:GetFName():ToString()
        if name == "NI_SmokeStack_Large" and same(mount, turret.Turret_Gun) then
            turret_effects.smoke = turret_effects.smoke + 1
        elseif name == "NI_CampFire" and same(mount, turret.RootComponent) then
            turret_effects.flames = turret_effects.flames + 1
        end
    end)
    RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAttached", function(_, sound, parent)
        if not turret_effects then return end
        local turret, cue = turret_effects.turret, sound:get()
        if valid(turret) and valid(cue) and cue:GetFName():ToString() == "Fireplace_Low_02_Loop"
            and same(parent:get(), turret.RootComponent) then turret_effects.loops = turret_effects.loops + 1 end
    end)
    RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAtLocation",
        function(_, _, sound, position, _, _, _, _, attenuation)
            if not turret_effects then return end
            local cue, radius = sound:get(), attenuation:get()
            if valid(cue) and cue:GetFName():ToString() == "MS_Blast_Pop"
                and valid(radius) and radius:GetFName():ToString() == "Blast_Attenuation"
                and at_turret(position:get()) then turret_effects.sounds = turret_effects.sounds + 1 end
        end)
    turret_effect_hooks = true
    return "armed"
end

local function turret_fire_field(world, turret)
    for _, fire in ipairs(FindAllOf("Fire_Camp_BP_C") or {}) do
        if valid(fire) and same(fire:GetWorld(), world) and same(fire:GetAttachParentActor(), turret) then return fire end
    end
end

local function read_turret_effects(world, turret)
    local state = assert(turret_effects, "the turret effect probe is not armed")
    assert(same(state.turret, turret), "the turret effect probe is bound to a different actor")
    local fire, lights, pitch = turret_fire_field(world, turret), 0, "-"
    for _, light in ipairs(FindAllOf("PointLightComponent") or {}) do
        if valid(light) and same(light:GetOwner(), turret) and same(light:GetAttachParent(), turret.RootComponent)
            and light.Intensity == 5000 and light.AttenuationRadius == 2000 and light.SourceRadius == 30 then
            lights = lights + 1
        end
    end
    for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
        if corpse_marker(marker, turret) then
            pitch = ("%.2f"):format(marker:K2_GetActorRotation().Pitch)
        end
    end
    local class = StaticFindObject("/Game/Classes/Actors/ExplosionActor_BP.ExplosionActor_BP_C")
    local defaults = valid(class) and class:GetCDO()
    return ("%d,%d,%d,%d;%s;%s;%s;%d,%d,%d"):format(state.blasts, state.sounds, state.smoke, valid(fire) and 1 or 0,
        pitch, tostring(valid(defaults) and defaults["PFX Scale"]), tostring(valid(defaults) and defaults["SFX Vol"]),
        state.flames, state.loops, lights)
end

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

local function turret_visual_state(turret)
    local gun = turret.Turret_Gun
    assert(valid(gun) and same(gun:GetOwner(), turret), "stock turret gun unavailable")
    local material = gun:GetMaterial(0)
    local burned = valid(material) and material:GetFName():ToString() == "Mat_PolygonMilitary_VehBurnt_01"
    return ("%s,%s,%s,%s"):format(tostring(turret.Alive), tostring(turret.Health <= 0), tostring(burned == true),
        tostring(turret:ActorHasTag(FName("Dead", EFindName.FNAME_Find))))
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
    if action == "turret-arm-effects" then
        return arm_turret_effects(turret)
    elseif action == "turret-read-effects" then
        return read_turret_effects(world, turret)
    elseif action == "turret-extinguish" then
        local fire = turret_fire_field(world, turret)
        assert(host and valid(fire) and fire:HasAuthority(), "the stock turret fire is unavailable on the host")
        fire:FizzleOut()
        return "extinguished"
    elseif action == "turret-fire" or action == "turret-retarget" then
        return turret_fire(pawn, world, turret, host, action == "turret-retarget")
    elseif action == "turret-kill" then
        assert(host and turret.Alive and turret.Health > 0, "only the host may destroy the live turret")
        local total, lethal = {}, {}
        turret:DealDamage({[damage_key]=2000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
        -- The stock turret reports remaining health in the TotalDamage out slot.
        assert(lethal.IsLethal == true and turret.Alive == false and turret.Health <= 0,
            ("stock turret stayed alive at %.2f HP, lethal=%s"):format(turret.Health, tostring(lethal.IsLethal)))
    end
    return turret_visual_state(turret)
end

local function dock_worker(world, name)
    for _, actor in ipairs(FindAllOf("NPC_Survivor_Worker_Light_C") or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
            and actor:GetFName():ToString() == name then return actor end
    end
end

local population_fixture = {}
local population_weapons = {}

local function population_weapon(actor)
    if not valid(actor) or actor:IsActorBeingDestroyed() or actor.Alive ~= true then return end
    local weapon = actor.Weapon_Ranged
    local root = valid(weapon) and weapon.RootComponent
    local mount = valid(root) and root:GetAttachParent()
    if valid(weapon) and same(weapon:GetOwner(), actor) and same(weapon.Instigator, actor)
        and valid(mount) and same(mount:GetOwner(), actor) and same(mount:GetOuter(), actor)
        and weapon:HasAuthority() and weapon.bReplicates == false and weapon.Item_RespawnMode == 0 then return weapon end
end

local function track_population_weapon(kind, actor, weapon)
    local tracked = population_weapons[kind]
    if tracked and same(tracked.npc, actor) then
        tracked.current = weapon
    else
        population_weapons[kind] = {npc=actor, original=weapon, current=weapon, name=weapon:GetFullName()}
    end
end

local function dock_burst_footing(pawn, origin, shift)
    local points = {}
    local height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local radius = pawn.CapsuleComponent:GetScaledCapsuleRadius()
    for row = 0, 5 do
        for column = 0, 7 do
            local x, y = origin.X + 125 * (column + 1), origin.Y + 200 + shift + row * 125
            local hit = {}
            if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=origin.Z+200}, {X=x,Y=y,Z=origin.Z-320},
                0, false, {pawn}, 0, hit, true, color, color, 0) then
                hit = hit.OutHit or hit
                local component = hit.Component and hit.Component:Get()
                local floor = hit.ImpactPoint
                if floor and hit.ImpactNormal.Z > .9 and valid(component) and component:GetCollisionObjectType() == 0 then
                    local point = {X=x,Y=y,Z=floor.Z+height+5}
                    if not trace:CapsuleTraceSingle(pawn, {X=x,Y=y,Z=point.Z+8}, point, radius, height, 0, false,
                        {pawn}, 0, {}, true, color, color, 0) then
                        points[#points + 1] = point
                        if #points == 10 then return points end
                    end
                end
            end
        end
    end
    error("no clear stock footing for ten Dock NPCs")
end

local function dock_spawn_population(pawn, world, host, burst)
    assert(not next(population_fixture), "clean stock NPC population fixture required")
    local origin = pawn:K2_GetActorLocation()
    local points = {}
    local footing = burst and dock_burst_footing(pawn, origin, host and 0 or 100)
    for index = 1, burst and 10 or 2 do
        local kind = index % 2 == 1 and "bandit" or "state"
        local point = footing and footing[index] or {X=origin.X + 125 * (1 + (index - 1) % 5),
            Y=origin.Y + 200 + 125 * math.floor((index - 1) / 5), Z=origin.Z}
        local actor = create_stock_npc(pawn, world, kind, point, 0)
        actor.CharacterMovement:DisableMovement()
        actor:SetActorTickEnabled(false)
        if valid(actor.Controller) then actor.Controller:SetActorTickEnabled(false) end
        if host then
            actor:SetReplicates(false)
            actor:SetReplicateMovement(false)
            assert(actor.bReplicates == false, "fixture must start with an unpublished host NPC")
        end
        population_fixture[index] = actor
        if burst then
            -- Separate the queue stress from the live faction fight and weapon cases.
            actor.Initialized = true
            actor:SetActorEnableCollision(false)
        else
            if not valid(actor.Weapon_Ranged) then actor:Equip_RangedWeapon() end
            track_population_weapon(kind, actor, assert(population_weapon(actor), "stock NPC did not equip a local firearm"))
        end
        local at = actor:K2_GetActorLocation()
        points[#points + 1] = ("%s,%.3f,%.3f,%.3f"):format(kind, at.X, at.Y, at.Z)
    end
    return table.concat(points, ";")
end

local function dock_population_fixture_state()
    local rows = {}
    for index, actor in ipairs(population_fixture) do
        local state = "invalid"
        if valid(actor) then
            local point = actor:K2_GetActorLocation()
            state = ("%s,role=%d,alive=%s,destroying=%s,position=(%.1f,%.1f,%.1f)"):format(actor:GetFName():ToString(),
                actor:GetLocalRole(), tostring(actor.Alive), tostring(actor:IsActorBeingDestroyed()), point.X, point.Y, point.Z)
        end
        rows[#rows + 1] = index .. "=" .. state
    end
    return table.concat(rows, ";")
end

local function population_weapons_at(world, points)
    local armed = {}
    for point in points:gmatch("[^;]+") do
        local kind, x, y, z = point:match("^(%w+),([^,]+),([^,]+),([^,]+)$")
        local class = assert(shooter_paths[kind]):match("%.([^%.]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        for _, actor in ipairs(FindAllOf(class) or {}) do
            if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
                local at = actor:K2_GetActorLocation()
                if (at.X-x)^2 + (at.Y-y)^2 + (at.Z-z)^2 < 40^2 and actor:GetLocalRole() == 1 then
                    local weapon = population_weapon(actor)
                    if weapon then
                        track_population_weapon(kind, actor, weapon)
                        armed[#armed + 1] = kind
                    end
                end
            end
        end
    end
    return table.concat(armed, ",")
end

local function population_weapon_in_world(weapon, world)
    if not valid(weapon) or not same(weapon:GetWorld(), world) or weapon:IsActorBeingDestroyed() then return false end
    local actors = {}
    statics:GetAllActorsOfClass(world, weapon:GetClass(), actors)
    for _, parameter in ipairs(actors.OutActors or actors) do
        local actor = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
            and parameter:get() or parameter
        if same(actor, weapon) then return true end
    end
    return false
end

local function tracked_population_weapons(world, equipped)
    local remaining = {}
    for kind, tracked in pairs(population_weapons) do
        if equipped then tracked.current = population_weapon(tracked.npc) end
        if (equipped and population_weapon_in_world(tracked.current, world))
            or (not equipped and (population_weapon_in_world(tracked.original, world)
                or population_weapon_in_world(tracked.current, world))) then
            remaining[#remaining + 1] = kind
        end
    end
    table.sort(remaining)
    return #remaining > 0 and table.concat(remaining, ",") or "empty"
end

local function population_weapon_details(world)
    local records = {}
    for kind, tracked in pairs(population_weapons) do
        local npc, weapon = tracked.npc, tracked.original
        local current = valid(npc) and npc.Weapon_Ranged
        local root = valid(weapon) and weapon.RootComponent
        local owner, mount = valid(weapon) and weapon:GetOwner(), valid(root) and root:GetAttachParent()
        records[#records + 1] = table.concat({
            kind, "original=" .. tracked.name, "valid=" .. tostring(valid(weapon) == true),
            "in_world=" .. tostring(population_weapon_in_world(weapon, world)),
            "owner=" .. (valid(owner) and owner:GetFullName() or "-"),
            "mount=" .. (valid(mount) and mount:GetFullName() or "-"),
            "current=" .. (valid(current) and current:GetFullName() or "-"),
            "npc=" .. (valid(npc) and npc:GetFullName() or "-"),
            "health=" .. tostring(valid(npc) and npc.Health or "-"),
            "alive=" .. tostring(valid(npc) and npc.Alive == true),
        }, "|")
    end
    table.sort(records)
    return table.concat(records, ";")
end

local function dock_population_at(world, points, include_paths)
    local visible = {}
    local population = {}
    for point in points:gmatch("[^;]+") do
        local kind, x, y, z = point:match("^(%w+),([^,]+),([^,]+),([^,]+)$")
        if not population[kind] then
            local class = StaticFindObject(assert(shooter_paths[kind]))
            local candidates = {}
            if valid(class) then statics:GetAllActorsOfClass(world, class, candidates) end
            population[kind] = {}
            for _, parameter in ipairs(candidates.OutActors or candidates) do
                local actor = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
                    and parameter:get() or parameter
                if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
                    local at = actor:K2_GetActorLocation()
                    -- Copy the location before the next reflected lookup.
                    population[kind][#population[kind] + 1] = {actor=actor, x=at.X, y=at.Y, z=at.Z}
                end
            end
        end
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        for _, entry in ipairs(population[kind]) do
            if (entry.x-x)^2 + (entry.y-y)^2 + (entry.z-z)^2 < 40^2 then
                local actor = entry.actor
                visible[#visible + 1] = kind .. "=" .. (include_paths and actor:GetFullName() or actor:GetLocalRole())
            end
        end
    end
    table.sort(visible)
    return #visible > 0 and table.concat(visible, ",") or "empty"
end

local function dock_combat(pawn, world, host, action, name)
    local actor = dock_worker(world, name or "NPC_Survivor_Worker_C_1")
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
        if corpse_marker(marker, actor, host and 3 or 1) then
            markers = markers + 1
        end
    end
    local location = actor:K2_GetActorLocation()
    local eyes = ("%0.7f,%0.7f,%0.7f"):format(location.X, location.Y, location.Z)
    return ("%s;%s;%d;%s;%s"):format(tostring(actor.Alive), tostring(actor.Mesh:IsAnySimulatingPhysics()), markers,
        eyes, actor:GetFullName())
end

local garage_view, garage_door, garage_aim, garage_plank, garage_worker_scale
local garage_shot_hook, garage_plank_hook
local garage_path = dock .. ":PersistentLevel.StaticMeshActor_347"

local function stock_garage(world)
    local actor = StaticFindObject(garage_path)
    local mesh = valid(actor) and actor.StaticMeshComponent
    assert(valid(mesh) and same(actor:GetWorld(), world)
        and mesh.StaticMesh:GetFName():ToString() == "SM_Bld_Door_Garage_02", "the stock garage door is unavailable")
    return actor, mesh
end

local function line_hit(pawn, start, finish, ignored, channel)
    local hit = {}
    if trace:LineTraceSingle(pawn, start, finish, channel or 1, true, ignored, 0, hit, true, color, color, 0) then
        return hit.OutHit or hit
    end
end

local function point_string(point)
    return ("%.3f,%.3f,%.3f"):format(point.X, point.Y, point.Z)
end

local melee_motion, melee_motion_hooks

local function watch_melee_motion(world, pawn)
    melee_motion = {world=world, pawn=pawn, started=trace:GetGameTimeInSeconds(world), events={}}
    if melee_motion_hooks then return end
    local function record(receiver, movement, event, detail)
        local watch = melee_motion
        if not watch or #watch.events >= 16 or not valid(watch.pawn) or watch.pawn:IsActorBeingDestroyed()
            or not same(watch.pawn:GetWorld(), watch.world)
            or not same(receiver, movement and watch.pawn.CharacterMovement or watch.pawn) then return end
        watch.events[#watch.events + 1] = ("%.3f %s %s"):format(
            trace:GetGameTimeInSeconds(watch.world) - watch.started, event, detail and detail() or "")
    end
    RegisterHook("/Script/Engine.Character:LaunchCharacter", function(context, velocity)
        record(context:get(), false, "launch", function() return point_string(velocity:get()) end)
    end)
    RegisterHook("/Script/Engine.CharacterMovementComponent:AddImpulse", function(context, impulse)
        record(context:get(), true, "impulse", function() return point_string(impulse:get()) end)
    end)
    RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:OnJumped", function(context)
        record(context:get(), false, "jump", function() return point_string(context:get():GetVelocity()) end)
    end)
    RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage", function(context, _, _, instigator, force)
        record(context:get(), false, "damage", function()
            local source = instigator:get()
            return (valid(source) and source:GetFullName() or "-") .. " " .. point_string(force:get())
        end)
    end)
    melee_motion_hooks = true
end

-- Arm this only for the staged long shot. The same stock projectile and
-- rounded muzzle packet are still used by the production contact check.
RegisterBeginPlayPreHook(function(context)
    local view = garage_view
    if not view or not view.stock_spread or not valid(view.worker) then return end
    local projectile = context:get()
    if not valid(projectile) or projectile:GetClass():GetFName():ToString() ~= "Projectile_Pistol_Light_C"
        or not same(projectile:GetOwner(), view.shooter) or not same(projectile.Instigator, view.shooter) then return end
    local velocity = projectile.Velocity
    local origin, target = projectile:K2_GetActorLocation(), view.worker:K2_GetActorLocation()
    local dx, dy = target.X-origin.X, target.Y-origin.Y
    local distance = math.sqrt(dx^2 + dy^2)
    if distance < 5000 then return end
    local limit = 63 / distance
    local forward = math.sqrt(1-limit^2)
    local speed = math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2)
    -- Offset the round by 63 cm at the target, toward the outer edge of the
    -- worker's mesh. This is within the 75 cm contact allowance.
    local axis_x, axis_y = dx/distance, dy/distance
    local side = view.stock_spread
    projectile.Velocity = {X=speed*(forward*axis_x-side*limit*axis_y),
        Y=speed*(forward*axis_y+side*limit*axis_x), Z=0}
    view.stock_spread = nil
end)

local function watch_garage_projectiles()
    if garage_shot_hook then return end
    local base = StaticFindObject("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C")
    assert(valid(base), "the stock projectile class was unavailable")
    RegisterBeginPlayPostHook(function(context)
        local view = garage_view
        if not view or not valid(view.shooter) or not garage_door then return end
        local actor = context:get()
        if not valid(actor) or not actor:IsA(base) or not same(actor:GetOwner(), view.shooter)
            or not same(actor.Instigator, view.shooter) then return end
        local excluded = {}
        for index = 1, actor["Actors to Ignore"]:GetArrayNum() do
            local item = actor["Actors to Ignore"][index]
            if valid(item) then excluded[#excluded + 1] = item:GetFName():ToString() end
        end
        view.last_shot = table.concat({point_string(actor:K2_GetActorLocation()), point_string(actor.Velocity),
            table.concat(excluded, ",")}, ";")
    end)
    garage_shot_hook = true
end

local function open_garage_for_test(world)
    local _, mesh = stock_garage(world)
    assert(not garage_door)
    local point = mesh:K2_GetComponentLocation()
    garage_door = {mesh=mesh, point={X=point.X,Y=point.Y,Z=point.Z}}
    mesh:SetMobility(2)
    mesh:K2_SetWorldLocation({X=point.X,Y=point.Y,Z=point.Z+165}, false, {}, true)
    assert(math.abs(mesh:K2_GetComponentLocation().Z - garage_door.point.Z - 165) < 1, "could not raise the staged garage door")
    return "raised"
end

local function restore_garage_fixture()
    if garage_view and valid(garage_view.extra_barrier) then garage_view.extra_barrier:K2_DestroyActor() end
    if garage_view and garage_view.dynamic and valid(garage_view.worker) then
        garage_view.worker:K2_DestroyActor()
    end
    if garage_view and garage_view.long_shot and valid(garage_view.worker) and garage_view.worker.Alive then
        local worker, saved = garage_view.worker, garage_view.long_shot
        worker.Health = saved.health
        worker:K2_SetActorLocation(saved.position, false, {}, true)
        worker:ForceNetUpdate()
    end
    if garage_aim then
        local camera, muzzle = garage_aim.camera, garage_aim.muzzle
        if valid(camera) then camera:K2_SetRelativeLocation(garage_aim.camera_location, false, {}, true) end
        if valid(muzzle) then
            if garage_aim.absolute_location ~= nil then
                muzzle:SetAbsolute(garage_aim.absolute_location, muzzle.bAbsoluteRotation, muzzle.bAbsoluteScale)
            end
            muzzle:K2_SetRelativeLocation(garage_aim.muzzle_location, false, {}, true)
        end
    end
    if garage_door and valid(garage_door.mesh) then
        garage_door.mesh:K2_SetWorldLocation(garage_door.point, false, {}, true)
        garage_door.mesh:SetMobility(0)
    end
    if garage_plank and valid(garage_plank.mesh) then
        local mesh = garage_plank.mesh
        mesh:K2_SetWorldLocation(garage_plank.position, false, {}, true)
        mesh:SetRelativeScale3D(garage_plank.scale)
        mesh:SetMobility(garage_plank.mobility)
    end
    if garage_worker_scale and valid(garage_worker_scale.actor) then
        garage_worker_scale.actor:SetActorScale3D(garage_worker_scale.original)
    end
    garage_view, garage_door, garage_aim, garage_plank, garage_worker_scale = nil, nil, nil, nil, nil
    return "restored"
end

local function stage_long_garage_shot(world)
    assert(garage_view and garage_door and valid(garage_view.worker) and not garage_view.long_shot)
    local _, mesh = stock_garage(world)
    local bounds, box = {}, {}
    trace:GetComponentBounds(mesh, bounds, box, {})
    local center, extent = bounds.Origin or bounds, box.BoxExtent or box
    local origin = mesh:K2_GetComponentLocation()
    local height = 4000
    local blocker = {X=origin.X,Y=origin.Y,Z=origin.Z+height}
    local axis = extent.X < extent.Y and "X" or "Y"
    local low = {X=center.X,Y=center.Y,Z=center.Z+height}
    local target = {X=low.X,Y=low.Y,Z=low.Z}
    low[axis], target[axis] = center[axis] - 310, center[axis] + 5600
    local worker = garage_view.worker
    assert(worker.Alive and worker.Health > 0)
    garage_view.long_shot = {position=worker:K2_GetActorLocation(), health=worker.Health}
    worker.Health = 120
    assert(worker:K2_SetActorLocation(target, false, {}, true), "could not stage a distant stock worker")
    worker:ForceNetUpdate()
    return table.concat({point_string(low), point_string(target), point_string(blocker), tostring(worker.Health)}, ";")
end

local function relocate_garage_barrier(world, destination, clear)
    assert(garage_door)
    if not garage_worker_scale then
        -- Keep the distant contact test independent of the stock pistol's random spread.
        local worker = dock_worker(world, "NPC_Survivor_Worker_C_2")
        assert(valid(worker))
        local scale = worker:GetActorScale3D()
        garage_worker_scale = {actor=worker, original={X=scale.X,Y=scale.Y,Z=scale.Z}}
        worker:SetActorScale3D({X=scale.X*1.8,Y=scale.Y*1.8,Z=scale.Z*1.8})
    end
    local _, mesh = stock_garage(world)
    local point = position(destination)
    if clear then point.Z = point.Z + 1000 end
    mesh:K2_SetWorldLocation(point, false, {}, true)
    assert(math.abs(mesh:K2_GetComponentLocation().Z - point.Z) < 1)
    return clear and "clear" or "blocked"
end

local function stage_second_garage_barrier(world, specification)
    assert(garage_view and not valid(garage_view.extra_barrier))
    local from_text, to_text = specification:match("^([^;]+);([^;]+)$")
    local from, to = position(from_text), position(to_text)
    local stock, source = stock_garage(world)
    local shift = {X=to.X-from.X, Y=to.Y-from.Y, Z=to.Z-from.Z}
    local scale = 85 / math.sqrt(shift.X^2 + shift.Y^2 + shift.Z^2)
    local point = source:K2_GetComponentLocation()
    local origin = {X=point.X+scale*shift.X, Y=point.Y+scale*shift.Y, Z=point.Z+scale*shift.Z}
    local extra = world:SpawnActor(stock:GetClass(), origin, source:K2_GetComponentRotation())
    assert(valid(extra) and extra:HasAuthority())
    garage_view.extra_barrier = extra
    extra:SetReplicates(false)
    local mesh = extra.StaticMeshComponent
    mesh:SetMobility(2)
    mesh:SetStaticMesh(source.StaticMesh)
    mesh:SetWorldScale3D(source.RelativeScale3D)
    mesh:SetCollisionProfileName(source:GetCollisionProfileName(), false)
    mesh:SetCollisionEnabled(3)
    local shooter = garage_view.shooter
    local ignored = {shooter, shooter.EquipedItem}
    local first = line_hit(shooter, from, to, ignored, 2)
    assert(first and same(first.Component:Get(), source), "the original garage door must be the first collision")
    ignored[#ignored + 1] = stock
    local second = line_hit(shooter, from, to, ignored, 2)
    assert(second and same(second.Component:Get(), mesh), "the second garage barrier must be the next collision")
    ignored[#ignored + 1] = extra
    local last = line_hit(shooter, from, to, ignored, 2)
    local component = last and last.Component and last.Component:Get()
    assert(valid(component) and same(component:GetOwner(), garage_view.worker), "the host trace must reach the worker")
    return extra:GetFName():ToString()
end

local function stage_garage_shot(host, world, dynamic)
    local _, mesh = stock_garage(world)
    local worker = dock_worker(world, "NPC_Survivor_Worker_C_2")
    local shooter = authoritative_guest(world)
    assert(valid(worker) and worker:HasAuthority() and worker.Alive and worker.Health > 1)
    local bounds, box = {}, {}
    trace:GetComponentBounds(mesh, bounds, box, {})
    local center, extent = bounds.Origin or bounds, box.BoxExtent or box
    assert(center.X and extent.X and extent.Y and extent.Z)
    local axis = extent.X < extent.Y and "X" or "Y"
    local ignored = {host, shooter, worker, shooter.EquipedItem}
    for _, sign in ipairs({1, -1}) do
        for _, gap in ipairs({25, 45, 65, 15}) do
            local low_z = center.Z - extent.Z - gap
            local near, far = {X=center.X,Y=center.Y,Z=low_z}, {X=center.X,Y=center.Y,Z=low_z}
            near[axis], far[axis] = center[axis] - 310*sign, center[axis] + 250*sign
            local blocked = {X=near.X,Y=near.Y,Z=center.Z - extent.Z + 45}
            local high_target = {X=far.X,Y=far.Y,Z=blocked.Z}
            local obstruction = line_hit(shooter, blocked, high_target, ignored)
            local surface = obstruction and obstruction.Component and obstruction.Component:Get()
            if same(surface, mesh) and not line_hit(shooter, near, far, ignored) then
                local floor = line_hit(shooter, near, {X=near.X,Y=near.Y,Z=near.Z-220}, ignored)
                local at = floor and floor.ImpactPoint
                if at and near.Z - at.Z > 25 then
                    local half = shooter.CapsuleComponent:GetScaledCapsuleHalfHeight()
                    local aim = heading(near, far)
                    if shooter:K2_TeleportTo({X=near.X,Y=near.Y,Z=at.Z+half+2}, {Pitch=0,Yaw=aim.Yaw,Roll=0}) then
                        shooter.CharacterMovement:StopMovementImmediately()
                        shooter:ForceNetUpdate()
                        if dynamic then
                            worker = create_stock_npc(host, world, dynamic, far, aim.Yaw)
                            worker.bAlwaysRelevant = true
                        end
                        local root = worker:K2_GetActorLocation()
                        local point = far
                        if dynamic == "drone" or dynamic == "turret" then
                            local component = dynamic == "drone" and worker.CapsuleComponent or worker.Turret_Gun
                            assert(valid(component), "the stock target has no aim component")
                            local bounds = {}
                            trace:GetComponentBounds(component, bounds, {}, {})
                            local center = bounds.Origin or bounds
                            point = {X=far.X+root.X-center.X, Y=far.Y+root.Y-center.Y, Z=far.Z+root.Z-center.Z}
                        end
                        assert(worker:K2_SetActorLocation(point, false, {}, true), "could not stage the stock NPC beyond the door")
                        if not dynamic or dynamic == "bandit" then
                            worker.CharacterMovement:DisableMovement()
                            if valid(worker.Controller) then worker.Controller:SetActorTickEnabled(false) end
                        end
                        worker:SetActorTickEnabled(false)
                        worker:ForceNetUpdate()
                        garage_view = {shooter=shooter, worker=worker, dynamic=dynamic}
                        watch_garage_projectiles()
                        return table.concat({point_string(near), point_string(far), point_string(blocked),
                            point_string(high_target), tostring(worker.Health)}, ";")
                    end
                end
            end
        end
    end
    error(("the garage has no qualified low firing lane at %s (bounds %s)"):format(point_string(center), point_string(extent)), 0)
end

local function garage_npc(world, point, kind)
    local class = assert(shooter_paths[kind]):match("%.([^%.]+)$")
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and actor.Alive then
            local at = actor:K2_GetActorLocation()
            if not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
                and (at.X-point.X)^2 + (at.Y-point.Y)^2 + (at.Z-point.Z)^2 < 220^2 then return actor end
        end
    end
end

local function set_garage_aim(pawn, world, host, specification)
    local from_text, to_text, kind = specification:match("^([^;]+);([^;]+);(%a+)$")
    if not from_text then from_text, to_text = specification:match("^([^;]+);([^;]+)$") end
    assert(from_text and to_text)
    local from, to = position(from_text), position(to_text)
    local _, mesh = stock_garage(world)
    local shooter = host and authoritative_guest(world) or pawn
    local weapon = shooter.EquipedItem
    local worker = kind and garage_npc(world, to, kind) or dock_worker(world, "NPC_Survivor_Worker_C_2")
    assert(valid(weapon) and valid(weapon.Arrow_Muzzle))
    local aim = heading(from, to)
    assert(not host or (garage_view and same(garage_view.shooter, shooter)))
    if not garage_aim then
        local camera, muzzle = shooter.Camera.RelativeLocation, weapon.Arrow_Muzzle.RelativeLocation
        garage_aim = {camera=shooter.Camera, muzzle=weapon.Arrow_Muzzle,
            camera_location={X=camera.X,Y=camera.Y,Z=camera.Z}, muzzle_location={X=muzzle.X,Y=muzzle.Y,Z=muzzle.Z}}
    end
    shooter.Controller:SetControlRotation(aim)
    shooter.Camera:K2_SetWorldLocation(from, false, {}, true)
    shooter.Camera:K2_SetWorldRotation(aim, false, {}, true)
    weapon.Arrow_Muzzle:K2_SetWorldLocation(from, false, {}, true)
    local endpoint = to
    if kind == "turret" then
        local x, y, z = to.X-from.X, to.Y-from.Y, to.Z-from.Z
        local span = math.sqrt(x*x + y*y + z*z)
        assert(span > 1)
        -- The visual gun bounds can end before stock collision on a turned turret.
        endpoint = {X=to.X+x*100/span, Y=to.Y+y*100/span, Z=to.Z+z*100/span}
    end
    local hit = line_hit(shooter, from, endpoint, {shooter, weapon})
    local component = hit and hit.Component and hit.Component:Get()
    if same(component, mesh) then return "blocked" end
    if valid(component) and same(component:GetOwner(), worker) then return "worker" end
    return valid(component) and component:GetFullName() or "clear"
end

local function fire_garage_turret(pawn, world, specification)
    local from_text, to_text = specification:match("^([^;]+);([^;]+);turret$")
    assert(from_text and pawn:IsLocallyControlled() and not pawn:HasAuthority())
    -- Reapply the selected pose on the same game-thread callback as the stock press.
    -- The shotgun animation can move its muzzle between two separate commands.
    local lane = set_garage_aim(pawn, world, false, specification)
    if lane ~= "worker" then return "sight;" .. lane end
    local weapon = pawn.EquipedItem
    local turret = garage_npc(world, position(to_text), "turret")
    local muzzle, target = weapon.Arrow_Muzzle:K2_GetComponentLocation(), position(to_text)
    local x, y, z = target.X-muzzle.X, target.Y-muzzle.Y, target.Z-muzzle.Z
    local distance = math.sqrt(x*x+y*y+z*z)
    assert(distance > 1)
    local endpoint = {X=target.X+x*100/distance, Y=target.Y+y*100/distance, Z=target.Z+z*100/distance}
    local hit = line_hit(pawn, muzzle, endpoint, {pawn, weapon}, 2)
    local component = hit and hit.Component and hit.Component:Get()
    if not valid(component) or not same(component:GetOwner(), turret) then
        return "muzzle;" .. (valid(component) and component:GetFullName() or "clear")
    end
    local ok, state, sequence, wrappers = SilverfishProbeNativeGuestPrimaryInput(pawn:GetAddress())
    if not ok then return "stock refusal;" .. tostring(state) end
    assert(state == "stock_pressed" and wrappers == 2 and sequence >= 1)
    local captured = table.pack(SilverfishPeekNativeGuestPrimaryIntent(pawn:GetAddress()))
    assert(captured[1] == sequence, "stock turret trigger capture missing")
    return table.concat({"captured", sequence, point_string(muzzle), point_string(hit.ImpactPoint),
        component:GetFullName(), captured[18] or "-"}, ";")
end

local function stage_garage_plank(pawn, world, host, specification)
    local from_text, to_text, state = specification:match("^([^;]+);([^;]+);(%a+)$")
    assert(state == "in" or state == "out")
    local eye, target = position(from_text), position(to_text)
    local actor = StaticFindObject(dock .. ":PersistentLevel.Dest_WoodPlank_BP_C_1")
    local mesh = valid(actor) and actor.StaticMesh
    assert(valid(mesh) and same(actor:GetWorld(), world) and actor.Health > 0)
    if not garage_plank then
        local scale = mesh.RelativeScale3D
        garage_plank = {actor=actor, mesh=mesh, position=mesh:K2_GetComponentLocation(),
            scale={X=scale.X,Y=scale.Y,Z=scale.Z}, mobility=mesh.Mobility, hits=0}
    end
    if host and not garage_plank_hook then
        RegisterHook("/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP." ..
            "Static_DestructableObject_Master_BP_C:DealDamage", function(context, damage, _, instigator, _, breacher)
                if not garage_view or not garage_plank or not same(context:get(), garage_plank.actor)
                    or not same(instigator:get(), garage_view.shooter) then return end
                garage_plank.hits = garage_plank.hits + 1
                garage_plank.impact = table.concat({
                    tostring(damage:get()[damage_key]),
                    tostring(damage:get().ArmorPenitration_4_E48DD35840D3699D8F37E48678EF29E4),
                    tostring(breacher:get()), tostring(garage_plank.actor["Dmg Threshold"]),
                    tostring(garage_plank.actor["Ballistic Dmg"]), tostring(garage_plank.actor.APDmg),
                }, ";")
            end)
        garage_plank_hook = true
    end
    local shooter = host and authoritative_guest(world) or pawn
    local weapon = shooter.EquipedItem
    local view = set_garage_aim(pawn, world, host, from_text .. ";" .. to_text)
    local muzzle = {X=eye.X,Y=eye.Y,Z=eye.Z-76}
    weapon.Arrow_Muzzle:K2_SetWorldLocation(muzzle, false, {}, true)
    -- Move the stock board to where the offset muzzle ray passes below the sight line.
    local center = {X=muzzle.X+(target.X-muzzle.X)*.16,Y=muzzle.Y+(target.Y-muzzle.Y)*.16,
        Z=muzzle.Z+(target.Z-muzzle.Z)*.16}
    local scale = garage_plank.scale
    mesh:SetMobility(2)
    mesh:SetRelativeScale3D({X=scale.X*0.55,Y=scale.Y*0.55,Z=scale.Z*0.55})
    local origin, bounds = mesh:K2_GetComponentLocation(), {}
    trace:GetComponentBounds(mesh, bounds, {}, {})
    local current = bounds.Origin or bounds
    mesh:K2_SetWorldLocation({X=origin.X+center.X-current.X,Y=origin.Y+center.Y-current.Y,
        Z=origin.Z+center.Z-current.Z+(state == "out" and 400 or 0)}, false, {}, true)
    local hit = line_hit(shooter, muzzle, target, {shooter, weapon}, 2)
    local component = hit and hit.Component and hit.Component:Get()
    local first = valid(component) and component:GetOwner():GetFName():ToString() or "clear"
    return table.concat({view, first, point_string(muzzle), tostring(actor.Health)}, ";")
end

local attack_probe
local attack_probe_hooked = false
local dock_attacker = "NPC_Survivor_Worker_C_0"
local montage_probe, montage_probe_hooked
local live_worker = "NPC_Survivor_Worker_C_2"
local live_probe, live_probe_hooked, pursuit_launch_hooked
local weapon_block_pawn, weapon_block_hooked
local live_observation = {limit = 48}

function live_observation.name(actor)
    return valid(actor) and actor:GetFullName():gsub("[|;\t\r\n]", "?"):sub(1, 384) or "-"
end

function live_observation.actor(probe, actor)
    return same(actor, probe.actor) and "worker" or same(actor, probe.host) and "host"
        or same(actor, probe.guest) and "guest" or live_observation.name(actor)
end

function live_observation.health(pawn)
    if valid(pawn) then
        local save = pawn.Save
        if valid(save) then return save.PlayerHealth end
    end
end

function live_observation.sample(probe)
    if not valid(probe.actor) or not same(probe.actor:GetWorld(), probe.world) then return end
    return {time = statics:GetRealTimeSeconds(probe.world) - probe.started,
        host = live_observation.health(probe.host), guest = live_observation.health(probe.guest),
        worker = probe.actor.Health, target = live_observation.actor(probe, probe.actor.CombatTarget),
        last = live_observation.actor(probe, probe.actor.LastDamagedMe)}
end

function live_observation.number(value)
    if type(value) ~= "number" or value ~= value or math.abs(value) == math.huge then return "-" end
    return ("%.6f"):format(value)
end

function live_observation.record(probe, kind, victim, source, damage)
    if not probe.events then return end
    local current = live_observation.sample(probe)
    if not current then return end
    local prior = probe.prior or {}
    probe.prior = current
    if #probe.events >= live_observation.limit then
        probe.dropped = probe.dropped + 1
        return
    end
    local source_role = live_observation.actor(probe, source)
    if source_role == "-" then source_role = "none"
    elseif source_role ~= "worker" and source_role ~= "host" and source_role ~= "guest" then source_role = "other" end
    local ok, kinetic = pcall(function() return damage:get()[damage_key] end)
    local number = live_observation.number
    -- UE4SS Blueprint hooks run after the stock function. The earlier sample is
    -- a separate observation, with its own timestamp, and may include stock regen.
    probe.events[#probe.events + 1] = table.concat({#probe.events + 1, kind, number(current.time), victim,
        source_role, live_observation.name(source), number(ok and kinetic), number(prior[victim]),
        number(current[victim]), number(current.worker), current.target, current.last,
        prior.target or "-", prior.last or "-", number(prior.time)}, ";")
end

local function dock_fight_position(pawn, actor, ignored, distance, shot_lane, contact)
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
                    if not contact or contact() then return pawn:K2_GetActorLocation() end
                end
            end
        end
    end
end

local function dock_melee_approach(host, guest, worker, distance)
    local function contact()
        local eye = guest.Camera:K2_GetComponentLocation()
        return melee_lane(guest, worker, heading(eye, worker:K2_GetActorLocation()))
    end
    for _, range in ipairs({distance, 160, 195}) do
        local close = dock_fight_position(guest, worker, {host, guest, worker}, range, true, contact)
        if close then
            guest.CharacterMovement:StopMovementImmediately()
            guest:ForceNetUpdate()
            return close
        end
    end
end

local function stage_live_worker(pawn, world, distance, keep_host_at_arrival, observe_damage)
    local actor = dock_worker(world, live_worker)
    assert(valid(actor) and actor.Alive and not valid(actor.CombatTarget) and actor.Health > 1
        and actor:HasAuthority() and valid(actor.Controller), "expected an untouched warehouse worker with its stock brain")
    local guest = authoritative_guest(world)
    local original = actor:K2_GetActorLocation()
    local ignored = {pawn, guest, actor}
    if keep_host_at_arrival then
        local arrival = pawn:K2_GetActorLocation()
        local x, y = arrival.X - original.X, arrival.Y - original.Y
        assert(x*x + y*y >= 5000*5000, "the authored Dock arrival is too close to observe this warehouse fight")
    else
        assert(dock_fight_position(pawn, actor, ignored, 1800, false), "no host position away from the warehouse fight")
    end
    local guest_point
    if distance then
        guest_point = dock_melee_approach(pawn, guest, actor, distance)
    else
        guest_point = dock_fight_position(guest, actor, ignored, 350, true)
    end
    assert(guest_point, "no clear guest approach in the warehouse")
    live_probe = {actor = actor, guest = guest, host = pawn, world = world, enemy_health = actor.Health,
        guest_health = guest.Save.PlayerHealth, guest_attacks = 0, host_attacks = 0}
    if observe_damage then
        live_probe.events, live_probe.dropped = {}, 0
        live_probe.started = statics:GetRealTimeSeconds(world)
        live_probe.prior = live_observation.sample(live_probe)
        if not live_observation.worker_hooked then
            RegisterHook("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage",
                function(context, damage, _, instigator)
                    local probe = live_probe
                    if probe and probe.events and same(context:get(), probe.actor) then
                        live_observation.record(probe, "worker_damage", "worker", instigator:get(), damage)
                    end
                end)
            live_observation.worker_hooked = true
        end
    end
    if keep_host_at_arrival then live_probe.launches = {} end
    if keep_host_at_arrival and not pursuit_launch_hooked then
        RegisterHook("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:SpawnProjectile", function(context)
            local probe = live_probe
            local weapon = context:get()
            if not probe or not probe.launches or not valid(probe.guest) or not valid(probe.actor)
                or not same(weapon, probe.guest.EquipedItem) or not valid(weapon.Arrow_Muzzle) then return end
            local origin = weapon.Arrow_Muzzle:K2_GetComponentLocation()
            local rotation = weapon.Arrow_Muzzle:K2_GetComponentRotation()
            local target = heading(origin, probe.actor:K2_GetActorLocation())
            local camera = probe.guest.Camera:K2_GetComponentRotation()
            probe.launches[#probe.launches + 1] = ("%.3f,%.3f;%.3f,%.3f;%.3f,%.3f")
                :format(rotation.Yaw, rotation.Pitch, target.Yaw, target.Pitch, camera.Yaw, camera.Pitch)
        end)
        pursuit_launch_hooked = true
    end
    if not live_probe_hooked then
        RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage", function(context, damage, _, instigator)
            local probe = live_probe
            if not probe then return end
            local victim, source = context:get(), instigator:get()
            local role = same(victim, probe.guest) and "guest" or same(victim, probe.host) and "host" or nil
            if not role or not same(victim:GetWorld(), probe.world) then return end
            if same(source, probe.actor) then
                if role == "guest" then probe.guest_attacks = probe.guest_attacks + 1
                else probe.host_attacks = probe.host_attacks + 1 end
            end
            live_observation.record(probe, "player_damage", role, source, damage)
        end)
        live_probe_hooked = true
    end
    return ("%.7f,%.7f,%.7f;%.7f,%.7f,%.7f"):format(original.X, original.Y, original.Z,
        guest_point.X, guest_point.Y, guest_point.Z)
end

local function sample_live_worker(pursuit)
    local probe = assert(live_probe, "the warehouse fight has not started")
    local actor, guest = probe.actor, probe.guest
    assert(valid(actor) and valid(guest), "the warehouse participant disappeared")
    local target = actor.CombatTarget
    local chosen = same(target, guest) and "guest" or same(target, probe.host) and "host"
        or valid(target) and target:GetFullName() or "-"
    local sample = ("%s;%s;%.3f;%.3f;%s;%d;%d;%.3f;%.3f"):format(
        tostring(actor.Alive), tostring(same(actor.LastDamagedMe, guest)), probe.enemy_health, actor.Health,
        chosen, probe.guest_attacks, probe.host_attacks, probe.guest_health, guest.Save.PlayerHealth)
    if not pursuit then return sample end
    local point, host = actor:K2_GetActorLocation(), probe.host:K2_GetActorLocation()
    local x, y = point.X - host.X, point.Y - host.Y
    return ("%s;%.3f,%.3f,%.3f;%.3f"):format(sample, host.X, host.Y, host.Z, math.sqrt(x*x + y*y))
end

local function live_worker_position(world)
    local actor = dock_worker(world, live_worker)
    if not valid(actor) or not actor.Alive then return "absent" end
    local point = actor:K2_GetActorLocation()
    return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
end

local pursuit_tracker

local function track_pursuit()
    local view = pursuit_tracker
    if not view then return end
    if not valid(view.actor) or not view.actor.Alive or not valid(view.pawn) or view.pawn.Dead
        or not valid(view.camera) or not valid(view.controller) or not same(view.controller.Pawn, view.pawn)
        or not same(view.pawn:GetWorld(), view.world) or not same(view.actor:GetWorld(), view.world)
        or not view.pawn:IsLocallyControlled() or view.pawn:HasAuthority() then
        pursuit_tracker = nil
        return
    end
    view.controller:SetControlRotation(heading(view.camera:K2_GetComponentLocation(), view.actor:K2_GetActorLocation()))
end

local function pursuit_lane(pawn, actor, weapon, camera)
    local eye = camera:K2_GetComponentLocation()
    local forward = camera:GetForwardVector()
    local center = actor:K2_GetActorLocation()
    local range = math.sqrt((center.X-eye.X)^2 + (center.Y-eye.Y)^2 + (center.Z-eye.Z)^2) + 100
    local ahead = {X=eye.X+range*forward.X, Y=eye.Y+range*forward.Y, Z=eye.Z+range*forward.Z}
    local ignored = {pawn, weapon}
    local aim = line_hit(pawn, eye, ahead, ignored, 1)
    local component = aim and aim.Component and aim.Component:Get()
    if not valid(component) or not same(component:GetOwner(), actor) then return false, "worker outside sight" end

    local muzzle = weapon.Arrow_Muzzle:K2_GetComponentLocation()
    local point = aim.ImpactPoint
    local dx, dy, dz = point.X-muzzle.X, point.Y-muzzle.Y, point.Z-muzzle.Z
    local distance = math.sqrt(dx*dx + dy*dy + dz*dz)
    if distance < 1 or dx*forward.X + dy*forward.Y + dz*forward.Z < 1 then return false, "worker behind muzzle" end
    local scale = 1 + 100 / distance
    local endpoint = {X=muzzle.X+scale*dx, Y=muzzle.Y+scale*dy, Z=muzzle.Z+scale*dz}
    local contact = line_hit(pawn, muzzle, endpoint, ignored, 2)
    component = contact and contact.Component and contact.Component:Get()
    if not valid(component) or not same(component:GetOwner(), actor) then return false, "worker outside muzzle line" end
    return true, ""
end

local function pursuit_frame(pawn, world, host, aim)
    local actor = dock_worker(world, live_worker)
    if not valid(actor) or not actor.Alive then return "absent" end
    assert(actor:GetLocalRole() == (host and 3 or 1), "the original worker has the wrong role")
    local shooter = host and authoritative_guest(world) or pawn
    local camera, controller = shooter.Camera, shooter.Controller
    local weapon = shooter.EquipedItem
    assert(valid(weapon) and valid(weapon.Arrow_Muzzle), "the pursuit shooter has no stock muzzle")
    local target = actor:K2_GetActorLocation()
    local desired = heading(camera:K2_GetComponentLocation(), target)
    if aim then
        assert(not host and shooter:IsLocallyControlled(), "only the guest controls aim in the pursuit proof")
        pursuit_tracker = {pawn = shooter, controller = controller, camera = camera, actor = actor, world = world}
        track_pursuit()
    end
    local point = shooter:K2_GetActorLocation()
    local barrel = weapon.Arrow_Muzzle:K2_GetComponentRotation()
    local function vector(value)
        return ("%.3f,%.3f,%.3f"):format(value.X, value.Y, value.Z)
    end
    local function angles(value)
        return ("%.3f,%.3f"):format(value.Yaw, value.Pitch)
    end
    local blockers = {}
    if controller.Loading then blockers[#blockers + 1] = "Loading" end
    for _, field in ipairs({"Dead", "IsWeaponBlocked", "UsingItem", "Reloading", "IsChangingAmmo",
        "Speaking", "HeadUnderWater", "Wep_HeldAside", "OnLadder"}) do
        if shooter[field] ~= false then blockers[#blockers + 1] = field end
    end
    -- The weapon's point-blank input gate does not detect cover farther down range.
    -- Record the actual first blocking component on the stock projectile channel.
    local muzzle = weapon.Arrow_Muzzle:K2_GetComponentLocation()
    local hit = line_hit(shooter, muzzle, target, {shooter, weapon}, 2)
    local component = hit and hit.Component and hit.Component:Get()
    local cover = valid(component) and not same(component:GetOwner(), actor) and component:GetFullName() or ""
    if aim and cover == "" then
        local _, lane = pursuit_lane(shooter, actor, weapon, camera)
        cover = lane
    end
    return table.concat({vector(point), vector(target), angles(desired), angles(controller:GetControlRotation()),
        angles(camera:K2_GetComponentRotation()), angles(barrel), weapon.Ammo, table.concat(blockers, ","), cover}, ";")
end

local function fire_pursuing_worker(pawn, world)
    local actor = dock_worker(world, live_worker)
    if not valid(actor) or not actor.Alive then return "worker unavailable" end
    assert(pawn:IsLocallyControlled() and not pawn:HasAuthority())
    local weapon, camera = pawn.EquipedItem, pawn.Camera
    if not valid(weapon) or not valid(camera) or not valid(weapon.Arrow_Muzzle) then return "weapon unavailable" end
    track_pursuit()
    local clear, reason = pursuit_lane(pawn, actor, weapon, camera)
    if not clear then return reason end

    local ok, status, sequence, wrappers = SilverfishProbeNativeGuestPrimaryInput(pawn:GetAddress())
    if not ok then return "stock refusal;" .. tostring(status) end
    assert(status == "stock_pressed" and wrappers == 2 and sequence >= 1)
    return "captured;" .. sequence
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
        montage_probe = {animator=animator, count=0, clip="-", rate=0, playing=false}
        if not montage_probe_hooked then
            RegisterHook("/Script/Engine.AnimInstance:PlaySlotAnimationAsDynamicMontage",
                function(context, asset, slot, _fade_in, _fade_out, rate)
                    if not montage_probe or not same(context:get(), montage_probe.animator)
                        or slot:get():ToString() ~= "DefaultSlot" then return end
                    local clip = asset:get()
                    montage_probe.count = montage_probe.count + 1
                    montage_probe.clip = clip:GetFullName()
                    montage_probe.rate = rate:get()
                end,
                function(context, result, _, slot)
                    if not montage_probe or not same(context:get(), montage_probe.animator)
                        or slot:get():ToString() ~= "DefaultSlot" then return end
                    -- Sample playback before a later game frame can end the montage.
                    local montage = result:get()
                    montage_probe.playing = valid(montage) and montage_probe.animator:Montage_IsPlaying(montage) == true
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
    return ("%d;%.3f;%s;%s"):format(montage_probe.count, montage_probe.rate,
        tostring(montage_probe.playing), montage_probe.clip)
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

local function check_downing_retarget(actor, pawn, guest)
    local health = guest.Save.PlayerHealth
    actor:AddTargetToList(pawn, {})
    project_downed(guest, "downed")
    local retargeted = same(actor.CombatTarget, pawn)
    actor:Agro_Buffer(guest, true, true, true, true)
    local stayed_on_host = same(actor.CombatTarget, pawn)
    actor.CombatTarget = guest
    local queued = observe_strike(actor)
    actor.CombatTarget = pawn
    project_downed(guest, "standing")
    assert(retargeted and stayed_on_host and queued == 0 and guest.Save.PlayerHealth == health,
        ("downed checks failed: retarget=%s ignore=%s strike=%s"):format(tostring(retargeted), tostring(stayed_on_host), queued))
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

    check_downing_retarget(actor, pawn, guest)
    return ("%.6f;%.6f;%.6f;%s"):format(host_before, before, after, actor:GetFullName())
end

local local_melee_animation, local_melee_notifies, local_melee_hook

local function observe_local_melee(action, pawn)
    local weapon = pawn.EquipedItem
    local mesh = valid(weapon) and weapon.Wep_Skeli
    local animation = valid(mesh) and mesh:GetAnimInstance()
    assert(valid(animation) and same(animation, weapon["As Melee Wep Anim BP"]), "stock first-person melee animator unavailable")
    if action == "melee-local-read" then
        assert(same(animation, local_melee_animation), "first-person melee animator was replaced")
        return tostring(local_melee_notifies)
    end
    local_melee_animation, local_melee_notifies = animation, 0
    if not local_melee_hook then
        RegisterHook("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleeWep_AnimBP.MeleeWep_AnimBP_C:AnimNotify_Hit",
            function(context)
                if same(context:get(), local_melee_animation) then local_melee_notifies = local_melee_notifies + 1 end
            end)
        local_melee_hook = true
    end
    return "watching"
end

local solo_travel
local function solo_combat(world, action, slot)
    assert(valid(world) and not valid(world.NetDriver), "the singleplayer check requires a standalone world")
    local instance = world.OwningGameInstance
    local controller = statics:GetPlayerController(world, 0)
    if action == "solo-load" then
        assert(world:GetFullName() == "World /Game/Classes/Maps/MainMenu_P.MainMenu_P"
            and valid(instance) and slot:match("^%d+%-%d+$") and statics:DoesSaveGameExist(slot, 0),
            "the singleplayer check needs its copied stock save")
        solo_travel = false
        instance:Load_GameState(slot, false)
        return "loading stock save"
    end
    local pawn = statics:GetPlayerPawn(world, 0)
    if not valid(pawn) or not valid(controller) or controller.Loading ~= false then return "loading" end
    local active_slot = instance.CurrentSaveSlot
    if type(active_slot) == "userdata" then active_slot = active_slot:ToString() end
    assert(pawn:IsLocallyControlled() and pawn:HasAuthority() and same(pawn.Save, instance.SaveSlot_Ref)
        and active_slot == slot, "the stock singleplayer save is not active")
    -- The most recent copied save can be in a stock safe zone where even a valid hit
    -- does zero damage. Use a combat map while keeping the same standalone save.
    if world:GetFullName() ~= "World " .. dock then
        if not solo_travel then
            solo_travel = true
            statics:OpenLevel(controller, FName(dock:match("^[^.]+")), true, "")
        end
        return "loading"
    end
    local inventory_input = "/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory"
    local input = StaticFindObject(inventory_input)
    if not valid(input) then LoadAsset(inventory_input); input = StaticFindObject(inventory_input) end
    assert(valid(input) and controller.MenuStack:GetArrayNum() == 0 and pawn.Dead == false)
    controller:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
    local menu = controller.MenuStack[1]
    assert(valid(menu) and menu:GetClass():GetFName():ToString() == "InventoryScreen_C", "stock Tab did not open inventory")
    controller:CloseMenu({})

    local class = StaticFindObject(class_path)
    if not valid(class) then LoadAsset(class_path); class = StaticFindObject(class_path) end
    assert(valid(class), "stock singleplayer drone class unavailable")
    local origin = pawn:K2_GetActorLocation()
    local actor = world:SpawnActor(class, {X=origin.X+180, Y=origin.Y, Z=origin.Z+180}, {Pitch=0, Yaw=0, Roll=0})
    assert(valid(actor) and actor.Alive and actor.Health > 0)
    local total, lethal = {}, {}
    actor:DealDamage({[damage_key]=1000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
    assert(total.TotalDamage > 0 and lethal.IsLethal == true and actor.Alive == false,
        ("Singleplayer drone damage did not resolve through the stock function (damage=%s, lethal=%s, alive=%s, health=%s)")
            :format(tostring(total.TotalDamage), tostring(lethal.IsLethal), tostring(actor.Alive), tostring(actor.Health)))
    return ("stock inventory and lethal drone damage %.3f"):format(total.TotalDamage)
end


local streamed_npc_fixture

RegisterEndPlayPreHook(function(context, reason)
    local watch = streamed_npc_fixture and streamed_npc_fixture.plank
    if not watch then return end
    local actor = context:get()
    if valid(actor) and actor:GetAddress() == watch.address then watch.end_reason = reason:get() end
end)

local function streamed_plank(fixture, level, action)
    local path = level:GetFullName():match("^%S+ (.+)$") .. ".Dest_WoodPlank_BP_C_1"
    local paths = assert(require("world_path").actor_paths(fixture.world, "destructible"),
        "streamed destructible membership unavailable")
    local member = false
    for _, current in ipairs(paths) do
        if current == path then member = true break end
    end
    local watch = fixture.plank
    local hidden, collision = "-", "-"
    if member and (not watch or watch.end_reason == nil) then
        local plank = StaticFindObject(path)
        local placed, remote = SilverfishQualifyNativePlacedDestructible(path, fixture.world:GetAddress())
        assert(valid(plank) and placed and remote == not fixture.host and same(plank:GetOuter(), level),
            "streamed plank scope unavailable")
        watch = watch or {address = plank:GetAddress()}
        assert(watch.address == plank:GetAddress(), "streamed plank instance changed")
        fixture.plank = watch
        hidden, collision = tostring(plank.bHidden), tostring(plank:GetActorEnableCollision())
        if action == "stream-npc-plank-break" then
            assert(hidden == "false" and collision == "true", "streamed plank is already broken")
            local point = plank.StaticMesh:K2_GetComponentLocation()
            plank:Damage_Feedback(true, {Location = point, ImpactPoint = point})
            return "stock plank break requested"
        end
    else
        assert(action == "stream-npc-plank", "the loaded level no longer contains the stock plank")
    end
    return table.concat({path, tostring(member), tostring(watch and watch.end_reason or "-"), hidden, collision}, ";")
end

local function streamed_npc(world, pawn, host, action)
    if action == "stream-npc-load" then
        local loading = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
        local stream = loading:LoadLevelInstance(world, "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P",
            {X=45000,Y=45000,Z=2000}, {Pitch=0,Yaw=0,Roll=0}, {}, "CoopNpcSublevel", nil, false)
        assert(valid(stream), "could not load the NPC test sublevel")
        streamed_npc_fixture = {world=world, stream=stream, host=host}
        return "loading"
    end
    local fixture = streamed_npc_fixture
    assert(fixture and same(fixture.world, world) and valid(fixture.stream))
    if not fixture.stream:IsLevelVisible() then return "waiting" end
    local level = fixture.stream:GetLoadedLevel()
    assert(valid(level) and not same(level, world.PersistentLevel))
    if action == "stream-npc-plank" or action == "stream-npc-plank-break" then
        return streamed_plank(fixture, level, action)
    end
    local actor = fixture.actor
    if not valid(actor) then
        local path = level:GetFullName():match("^%S+ (.+)$")
        actor = StaticFindObject(path .. ".NPC_Survivor_Worker_C_1")
        if not valid(actor) then return "waiting" end
        fixture.actor = actor
    end
    assert(same(actor:GetOuter(), level), "the NPC must belong to the visible streamed sublevel of this campaign")
    if action == "stream-npc-world" then
        local read, owning = pcall(function() return level.OwningWorld end)
        return ("actor-world=%s;level-owning-world=%s"):format(actor:GetWorld():GetFullName(),
            read and valid(owning) and owning:GetFullName() or "-")
    end
    if action == "stream-npc-stage" then
        assert(host and actor.Alive and actor:HasAuthority())
        local reward_path = "/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C"
        local reward = StaticFindObject(reward_path)
        if not valid(reward) then LoadAsset(reward_path) reward = StaticFindObject(reward_path) end
        assert(valid(reward), "could not load the stock worker's test loot")
        -- Keep the stock death spawn and give its ordinary loot roll one certain result.
        actor.LootItemPool:Empty()
        actor.LootItemPool:Add(reward, 1)
        actor.LootChance_Mod = 1
        local point = gun_target(pawn, world)
        assert(actor:K2_SetActorLocation(point, false, {}, true))
        actor.CharacterMovement:DisableMovement()
        actor:SetActorTickEnabled(false)
        actor.bAlwaysRelevant = true
        actor:SetReplicates(true)
        actor:SetReplicateMovement(true)
        actor.Health = 1
        actor:ForceNetUpdate()
        return actor:GetFullName()
    end
    if action == "stream-npc-retarget" then
        assert(host and actor.Alive and actor:HasAuthority())
        local guest = authoritative_guest(world)
        actor.TargetList = {}
        actor.CombatTarget = nil
        actor:AddTargetToList(guest, {})
        local selection = {}
        actor:GetNewCombatTarget(selection)
        assert(selection.NewTargetFound == true and same(actor.CombatTarget, guest),
            "the streamed worker must first target the standing guest")
        check_downing_retarget(actor, pawn, guest)
        actor.TargetList = {}
        actor.CombatTarget = nil
        return "switched to host"
    end
    if action == "stream-npc-status" then
        return ("%d,%s,%.6f,%s"):format(actor:GetLocalRole(), tostring(actor.Alive),
            actor.Health, tostring(actor.Mesh:IsAnySimulatingPhysics()))
    end
    if action == "stream-npc-position" then
        local point = actor:K2_GetActorLocation()
        return ("%.3f,%.3f,%.3f"):format(point.X, point.Y, point.Z)
    end
    if action == "stream-npc-aim" then
        assert(not host and actor:GetLocalRole() == 1 and valid(pawn.EquipedItem))
        local target = actor.CapsuleComponent:K2_GetComponentLocation()
        local eye = pawn.Camera:K2_GetComponentLocation()
        pawn.Controller:SetControlRotation(heading(eye, target))
        return "aimed"
    end
    if action == "stream-npc-contact" then
        assert(host and type(SilverfishReadFixtureShotContact) == "function")
        return SilverfishReadFixtureShotContact()
    end
    if action == "stream-npc-damage" then
        assert(actor.Alive and actor.Health > 0 and actor.bReplicates
            and actor:GetLocalRole() == (host and 3 or 1), "the NPC must be a live authority or enrolled replica")
        local before = actor.Health
        local total, lethal = {}, {}
        actor:DealDamage({[damage_key]=2000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
        if host then
            assert(total.TotalDamage > 0 and lethal.IsLethal and not actor.Alive, "authority damage did not kill the NPC")
            return "killed"
        end
        assert(total.TotalDamage == 0 and lethal.IsLethal == false and actor.Health == before and actor.Alive,
            ("streamed NPC took local guest damage: %s, %s (%.3f to %.3f)"):format(
                tostring(total.TotalDamage), tostring(lethal.IsLethal), before, actor.Health))
        return "damage denied"
    end
    error("unknown streamed NPC probe")
end

local function run(action, actor_path)
    if action == "disarm-weapon-observers" then
        shot_turret, turret_effects = nil, nil
        return "disarmed"
    elseif action == "clear-melee-aim" then
        melee_aim = nil
        return "cleared"
    end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if action == "solo-load" or action == "solo-combat" then return solo_combat(world, action, actor_path) end
    local at_dock = action:match("^dock%-") ~= nil
    if action == "dock-natural-stop" and (not valid(world) or world:GetFullName() ~= "World " .. dock) then
        live_probe = nil
        return "stopped"
    end
    local world_name = valid(world) and world:GetFullName()
    local outside_population = world_name == "World " .. outside_campaign and action:match("^dock%-population%-")
    assert(valid(world) and (world_name == "World " .. (at_dock and dock or map) or outside_population)
        and valid(world.NetDriver), "the test requires the expected world in an isolated co-op pair")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "local stock pawn unavailable")
    local host = pawn:HasAuthority()
    if action:match("^stream%-npc%-") then return streamed_npc(world, pawn, host, action) end
    if action == "melee-local-arm" or action == "melee-local-read" then
        assert(not host, "first-person melee observer requires the guest")
        return observe_local_melee(action, pawn)
    elseif action == "melee-contact-probe-arm" then
        assert(not host and SilverfishArmGuestMeleeContactProbe(pawn:GetAddress()), "the guest melee probe could not arm")
        return "armed"
    elseif action == "melee-contact-probe-target" then
        assert(host, "the target reference uses the host NPC")
        local target = melee_target(world, true)
        local key = valid(target) and require("native_npc_barter_identity").key(target)
        return key and ("npc:%d"):format(tonumber(key:sub(-4), 16)) or "waiting"
    elseif action == "melee-contact-probe-health" then
        assert(host, "the host holds the test NPC health")
        local target = melee_target(world, true)
        assert(valid(target) and target.Alive and target.Max_Health >= 100)
        target.Health = 100
        return "ready"
    elseif action == "melee-contact-probe-read" then
        assert(not host, "the contact probe requires the owning guest")
        local row = {SilverfishReadGuestMeleeContactProbe()}
        for i, value in ipairs(row) do
            row[i] = type(value) == "number" and ("%.6f"):format(value) or value
        end
        return table.concat(row, ";")
    elseif action == "corpse-spawn" then
        assert(host, "only the host can spawn the stock carry target")
        return spawn_carry_target(world, pawn)
    elseif action:match("^corpse%-") then
        local actor = host and carry_target
        if not host then
            for _, candidate in ipairs(FindAllOf("NPC_Bandit_C") or {}) do
                if valid(candidate) and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then
                    assert(not actor, "unexpected second isolated bandit")
                    actor = candidate
                end
            end
        end
        if action == "corpse-combat-reference" then
            if not valid(actor) then return "waiting" end
            return require("native_npc_barter_identity").key(actor) or "waiting"
        end
        if action == "corpse-kill" then
            assert(host and valid(actor) and actor:HasAuthority() and actor.Alive)
            actor.Health = 0
            actor:ResolveDeath()
            assert(not actor.Alive and actor.Mesh:IsAnySimulatingPhysics(), "the bandit's stock death did not create a ragdoll")
            return "killed"
        end
        if action == "corpse-ready" then
            if not valid(actor) or actor.Alive or not actor.Mesh:IsAnySimulatingPhysics() then return "waiting" end
            for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
                if corpse_marker(marker, actor) then return "ready" end
            end
            return "waiting"
        end
        assert(valid(actor) and actor.bReplicates and actor:GetLocalRole() == (host and 3 or 1), "missing replicated bandit")
        return corpse_carry(action, world, pawn, actor, host)
    elseif action == "dock-equip" then
        assert(not host, "the guest must select their stock equipment")
        return select_equipment(pawn, function(row)
            return row[item_class]:GetFName():ToString() == "Wep_RustyPistol_C"
        end)
    elseif action == "dock-population-spawn" or action == "dock-population-burst" then
        return dock_spawn_population(pawn, world, host, action == "dock-population-burst")
    elseif action == "dock-population-at" or action == "dock-population-paths" then
        return dock_population_at(world, actor_path, action == "dock-population-paths")
    elseif action == "dock-population-fixture-state" then
        return dock_population_fixture_state()
    elseif action == "dock-population-observe-view" then
        assert(not host)
        return population_weapons_at(world, actor_path)
    elseif action == "dock-population-weapons" then
        return tracked_population_weapons(world, actor_path == "equipped")
    elseif action == "dock-population-weapon-details" then
        return population_weapon_details(world)
    elseif action == "dock-population-cleanup" then
        for _, actor in pairs(population_fixture) do
            if valid(actor) and not actor:IsActorBeingDestroyed() then actor:K2_DestroyActor() end
        end
        if host then
            for _, tracked in pairs(population_weapons) do
                for _, weapon in pairs({tracked.original, tracked.current}) do
                    if valid(weapon) and not weapon:IsActorBeingDestroyed() then weapon:K2_DestroyActor() end
                end
            end
        end
        population_fixture = {}
        return "cleared"
    elseif action == "dock-block-weapon" or action == "dock-unblock-weapon" then
        assert(not host and pawn.Dead == false and valid(pawn.EquipedItem), "the guest needs a live equipped pawn")
        local blocked = action == "dock-block-weapon"
        if blocked and not weapon_block_hooked then
            RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:LookTrace", function(context)
                local candidate = context:get()
                if same(candidate, weapon_block_pawn) then candidate.IsWeaponBlocked = true end
            end)
            weapon_block_hooked = true
        end
        weapon_block_pawn = blocked and pawn or nil
        pawn:LookTrace()
        return tostring(pawn.IsWeaponBlocked)
    elseif action == "dock-natural-stage" then
        assert(host, "the test stages the stock host NPC")
        return stage_live_worker(pawn, world, nil, false, true)
    elseif action == "dock-pursuit-stage" then
        assert(host, "the host observes the original warehouse worker")
        return stage_live_worker(pawn, world, nil, true)
    elseif action == "dock-melee-stage" then
        assert(host, "the original worker is owned by the host")
        return stage_live_worker(pawn, world, 135)
    elseif action == "dock-melee-equip" then
        assert(not host, "the guest must select their stock melee equipment")
        return select_equipment(pawn, function(row) return guid_of(row) == actor_path end)
    elseif action == "dock-melee-aim" then
        local worker = dock_worker(world, live_worker)
        assert(valid(worker) and worker.Alive and worker:GetLocalRole() == (host and 3 or 1),
            "the original live warehouse worker is unavailable")
        local shooter = host and authoritative_guest(world) or pawn
        assert(aim_melee(world, shooter, function()
            if valid(worker) and not worker:IsActorBeingDestroyed() and same(worker:GetWorld(), world) and worker.Alive then
                return worker:K2_GetActorLocation()
            end
        end), "the stock warehouse aim camera is unavailable")
        local eye, forward = shooter.Camera:K2_GetComponentLocation(), shooter.Camera:GetForwardVector()
        return table.concat({point_string(eye), point_string(forward), point_string(worker:K2_GetActorLocation())}, ";")
    elseif action == "melee-view" then
        local owner = host and authoritative_guest(world) or pawn
        local eye, forward = owner.Camera:K2_GetComponentLocation(), owner.Camera:GetForwardVector()
        local body, velocity = owner:K2_GetActorLocation(), owner:GetVelocity()
        return table.concat({point_string(eye) .. "," .. point_string(forward), point_string(body),
            point_string(velocity), tostring(owner.CharacterMovement.MovementMode)}, ";")
    elseif action == "watch-melee-motion" then
        watch_melee_motion(world, host and authoritative_guest(world) or pawn)
        return "watching"
    elseif action == "read-melee-motion" then
        local watch = melee_motion
        melee_motion = nil
        return watch and table.concat(watch.events, "|") or ""
    elseif action == "melee-contact-view" or action == "dock-melee-contact-view" then
        assert(host, "the authoritative contact runs on the host")
        if type(SilverfishReadNativeGuestMeleeContactGeometry) ~= "function" then return "unavailable" end
        local x, y, z, dx, dy, dz, reach, radius = SilverfishReadNativeGuestMeleeContactGeometry()
        if not x then return "unavailable" end
        return ("%.7f,%.7f,%.7f,%.7f,%.7f,%.7f,%.7f,%.7f"):format(x, y, z, dx, dy, dz, reach, radius)
    elseif action == "melee-contact" or action == "dock-melee-contact" then
        assert(host, "the authoritative contact runs on the host")
        return SilverfishReadNativeGuestMeleeContactStatus()
    elseif action == "dock-melee-approach" then
        assert(host and live_probe and valid(live_probe.actor) and live_probe.actor.Alive,
            "the dock melee retry requires the original live worker")
        local close = assert(dock_melee_approach(pawn, live_probe.guest, live_probe.actor, 135),
            "the live warehouse worker has no reachable melee approach")
        return point_string(close)
    elseif action == "dock-natural-position" then
        return live_worker_position(world)
    elseif action == "dock-pursuit-fire" then
        assert(not host)
        return fire_pursuing_worker(pawn, world)
    elseif action == "dock-pursuit-view" or action == "dock-pursuit-aim" then
        return pursuit_frame(pawn, world, host, action == "dock-pursuit-aim")
    elseif action == "dock-pursuit-launches" then
        local launches = host and live_probe and live_probe.launches
        return launches and #launches > 0 and table.concat(launches, "|") or "absent"
    elseif action == "dock-pursuit-guest-stop" then
        assert(not host, "only the guest owns the stock aim fixture")
        pursuit_tracker = nil
        return "stopped"
    elseif action == "dock-pursuit-stop" then
        assert(host, "only the host owns the combat observer")
        live_probe = nil
        return "stopped"
    elseif action == "dock-natural-impact" then
        assert(host and live_probe and valid(live_probe.actor.Melee_SFX), "the stock worker impact is unavailable")
        return live_probe.actor.Melee_SFX:GetFullName():match(" (%S+)$")
    elseif action == "dock-natural-seat" then
        local point = pawn:K2_GetActorLocation()
        return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
    elseif action == "dock-natural-sample" or action == "dock-pursuit-sample" then
        assert(host, "the host owns the fight")
        return sample_live_worker(action == "dock-pursuit-sample")
    elseif action == "dock-natural-events" then
        local probe = live_probe
        assert(host and probe and probe.events and same(probe.world, world), "no active host stock fight observation")
        local header = "v1;" .. probe.dropped
        return #probe.events == 0 and header or header .. "|" .. table.concat(probe.events, "|")
    elseif action == "dock-natural-lane" then
        local actor = dock_worker(world, live_worker)
        if not valid(actor) or not actor.Alive then return "absent" end
        local shooter = host and authoritative_guest(world) or pawn
        local weapon, camera = shooter.EquipedItem, shooter.Camera
        if not valid(weapon) or not valid(camera) or not valid(weapon.Arrow_Muzzle) then return "weapon unavailable" end
        local clear, reason = pursuit_lane(shooter, actor, weapon, camera)
        return clear and "clear" or reason
    elseif action == "dock-natural-state" then
        return dock_combat(pawn, world, host, action, live_worker)
    elseif action == "dock-natural-retreat" then
        assert(host and live_probe and valid(live_probe.actor) and live_probe.actor.Alive,
            "a living stock attacker is required")
        local probe = live_probe
        local point = assert(dock_fight_position(probe.guest, probe.actor, {pawn, probe.guest, probe.actor}, 350, true),
            "the guest has no clear retreat from the worker")
        probe.guest.CharacterMovement:StopMovementImmediately()
        probe.guest:ForceNetUpdate()
        return ("%.7f,%.7f,%.7f"):format(point.X, point.Y, point.Z)
    elseif action == "dock-natural-arrive" then
        assert(not host, "only the local guest can stage its retreat")
        local point = position(actor_path)
        pawn.CharacterMovement:StopMovementImmediately()
        assert(pawn:K2_TeleportTo(point, pawn.Controller:GetControlRotation()), "guest retreat placement failed")
        return "ready"
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
    elseif action == "dock-garage-stage" or action == "dock-garage-stage-dynamic" then
        assert(host)
        return stage_garage_shot(pawn, world, action == "dock-garage-stage-dynamic" and actor_path or nil)
    elseif action == "dock-garage-open" then
        return open_garage_for_test(world)
    elseif action == "dock-garage-lower-host" then
        assert(host and garage_door and valid(garage_door.mesh))
        garage_door.mesh:K2_SetWorldLocation(garage_door.point, false, {}, true)
        return "lowered"
    elseif action == "dock-garage-second-barrier" then
        assert(host and garage_door)
        return stage_second_garage_barrier(world, actor_path)
    elseif action == "dock-garage-long-stage" then
        assert(host)
        return stage_long_garage_shot(world)
    elseif action == "dock-garage-long-block" or action == "dock-garage-long-clear" then
        return relocate_garage_barrier(world, actor_path, action == "dock-garage-long-clear")
    elseif action == "dock-garage-restore" then
        return restore_garage_fixture()
    elseif action == "dock-garage-aim" then
        return set_garage_aim(pawn, world, host, actor_path)
    elseif action == "dock-garage-turret-fire" then
        assert(not host)
        return fire_garage_turret(pawn, world, actor_path)
    elseif action == "dock-garage-occlude-camera" or action == "dock-garage-clear-camera" then
        assert(not host and garage_door and valid(garage_door.mesh))
        local raised = action == "dock-garage-clear-camera"
        local point = garage_door.point
        garage_door.mesh:K2_SetWorldLocation({X=point.X,Y=point.Y,Z=point.Z+(raised and 165 or 0)}, false, {}, true)
        return raised and "raised" or "lowered"
    elseif action == "dock-garage-offset-muzzle" then
        assert(not host and garage_aim and same(garage_aim.muzzle, pawn.EquipedItem.Arrow_Muzzle))
        local point = position(actor_path)
        local muzzle = garage_aim.muzzle
        -- Hold the fixture's world-space offset until the stock trigger samples it.
        if garage_aim.absolute_location == nil then garage_aim.absolute_location = muzzle.bAbsoluteLocation end
        muzzle:SetAbsolute(true, muzzle.bAbsoluteRotation, muzzle.bAbsoluteScale)
        muzzle:K2_SetWorldLocation(point, false, {}, true)
        return point_string(muzzle:K2_GetComponentLocation())
    elseif action == "dock-garage-dynamic-name" then
        local point, kind = actor_path:match("^([^;]+);(%a+)$")
        local actor = garage_npc(world, position(point), kind)
        if not valid(actor) then return "absent" end
        local key = require("native_npc_barter_identity").key(actor)
        return actor:GetFName():ToString() .. ";" .. (key and tostring(tonumber(key:sub(-4), 16)) or "-")
    elseif action == "dock-garage-dynamic-death" then
        assert(actor_path:match("^NPC_[%w_]+_%d+$") or actor_path:match("^Turret_[%w_]+_%d+$"))
        local actor = StaticFindObject(dock .. ":PersistentLevel." .. actor_path)
        if not valid(actor) or actor:IsActorBeingDestroyed() then return "removed" end
        return actor.Alive and "alive" or "dead"
    elseif action:match("^dock%-garage%-turret%-") then
        assert(actor_path:match("^Turret_[%w_]+_%d+$"))
        local actor = StaticFindObject(dock .. ":PersistentLevel." .. actor_path)
        assert(valid(actor) and same(actor:GetClass(), StaticFindObject(turret_class)) and same(actor:GetWorld(), world))
        if action == "dock-garage-turret-arm" then return arm_turret_effects(actor) end
        if action == "dock-garage-turret-effects" then return read_turret_effects(world, actor) end
        if action == "dock-garage-turret-aimpoint" then
            local bounds = {}
            trace:GetComponentBounds(actor.Turret_Gun, bounds, {}, {})
            return point_string(bounds.Origin or bounds)
        end
        assert(action == "dock-garage-turret-view")
        return turret_visual_state(actor)
    elseif action == "dock-garage-plank" then
        return stage_garage_plank(pawn, world, host, actor_path)
    elseif action == "dock-garage-plank-health" then
        assert(host and garage_plank)
        return valid(garage_plank.mesh) and tostring(garage_plank.mesh:GetOwner().Health) or "removed"
    elseif action == "dock-garage-plank-impact" then
        assert(host and garage_plank)
        return ("%d;%s"):format(garage_plank.hits, garage_plank.impact or "-")
    elseif action == "dock-garage-health" then
        assert(host and garage_view)
        if garage_view.dynamic and not valid(garage_view.worker) then return "0" end
        assert(valid(garage_view.worker))
        return ("%.6f"):format(garage_view.worker.Health)
    elseif action == "dock-garage-shot-witness" then
        return garage_view and garage_view.last_shot or "none"
    elseif action == "dock-garage-arm-spread" then
        assert(host and garage_view and garage_view.long_shot)
        garage_view.stock_spread = -1
        return "armed"
    elseif action == "dock-garage-contact-resolution" then
        assert(host and type(SilverfishReadFixtureShotContact) == "function")
        return SilverfishReadFixtureShotContact()
    elseif action == "dock-garage-projectile-trace" then
        local from_text, to_text = actor_path:match("^([^;]+);([^;]+)$")
        local from, to = position(from_text), position(to_text)
        local delta = {X=to.X-from.X, Y=to.Y-from.Y, Z=to.Z-from.Z}
        local span = math.sqrt(delta.X^2 + delta.Y^2 + delta.Z^2)
        local end_point = {X=to.X+100*delta.X/span, Y=to.Y+100*delta.Y/span, Z=to.Z+100*delta.Z/span}
        local shooter = host and authoritative_guest(world) or pawn
        local hit = line_hit(shooter, from, end_point, {shooter, shooter.EquipedItem}, 2)
        local component = hit and hit.Component and hit.Component:Get()
        return valid(component) and component:GetOwner():GetFName():ToString() or "clear"
    elseif action == "dock-garage-blockers" then
        local shooter = host and authoritative_guest(world) or pawn
        local blockers = {}
        if shooter.Controller.Loading == true then blockers[#blockers + 1] = "Loading" end
        for _, field in ipairs({"Dead", "IsWeaponBlocked", "UsingItem", "Reloading", "IsChangingAmmo",
            "Speaking", "HeadUnderWater", "Wep_HeldAside", "OnLadder"}) do
            if shooter[field] ~= false then blockers[#blockers + 1] = field end
        end
        return #blockers > 0 and table.concat(blockers, ",") or "ready"
    elseif action == "dock-arm-swing" or action == "dock-play-swing" or action == "dock-read-swing" then
        return dock_swing(world, host, action)
    elseif action == "dock-disarm-swing" then
        montage_probe = nil
        return "disarmed"
    elseif action == "dock-strike" then
        return dock_strike(pawn, world, host)
    elseif action == "dock-stage" or action == "dock-worker" then
        assert(actor_path == "" or actor_path == live_worker, "expected the original warehouse loot worker")
        return dock_combat(pawn, world, host, action, actor_path ~= "" and actor_path or nil)
    end
    if action == "fire-npc" then
        assert(host, "NPC shots must start on the host")
        return fire_stock_npc(pawn, world, actor_path)
    elseif action == "npc-kill" then
        assert(host, "NPC kills must start on the host")
        return npc_kill(pawn)
    elseif action == "npc-state" then
        return npc_combat_state(world, host)
    elseif action == "rivalry-start" then
        assert(host, "only the host spawns the stock rivalry")
        return begin_rivalry(pawn, world)
    elseif action == "rivalry-watch" then
        assert(not host, "only the guest takes the replica baseline")
        return watch_rivalry(world)
    elseif action == "rivalry-initialize" then
        assert(host, "only the host initializes the stock rivalry")
        return start_rivalry_ai()
    elseif action == "rivalry-state" then
        return observe_rivalry(world, host)
    elseif action == "rivalry-cleanup" then
        assert(host, "only the host clears the test NPCs")
        if rivalry then
            for _, actor in ipairs({rivalry.state, rivalry.bandit}) do if valid(actor) then actor:K2_DestroyActor() end end
        end
        rivalry = nil
        return "cleared"
    elseif action == "multi-melee-equip" then
        assert(host, "only the host can seed the fixture loadout")
        return seed_pipe(world, pipe_path, authoritative_guest(world, actor_path))
    elseif action == "multi-melee-stage" then
        assert(host, "only the host can stage the fixture target")
        local first, second = actor_path:match("^(.-)|(.+)$")
        assert(first and second)
        local guest, other = authoritative_guest(world, first), authoritative_guest(world, second)
        assert(not same(guest, other))
        local target = spawn_stock_npc(pawn, world, "state")
        target.Melee_SweepRange = 0
        stage_melee(world, 1, nil, guest)
        target.Max_Health = 300
        target.Health = 300
        local center = target:K2_GetActorLocation()
        local initial = guest:K2_GetActorLocation()
        local angle = math.atan(initial.Y - center.Y, initial.X - center.X)
        local ready
        for _, step in ipairs({100, -100, 150, -150, 65, -65}) do
            local radians = angle + math.rad(step)
            local candidate = {X=center.X+165*math.cos(radians), Y=center.Y+165*math.sin(radians), Z=initial.Z}
            if other:K2_TeleportTo(candidate, heading(candidate, center)) then
                local eye = other.Camera:K2_GetComponentLocation()
                if melee_lane(other, target, heading(eye, center)) and math.sqrt(
                    (candidate.X-initial.X)^2 + (candidate.Y-initial.Y)^2) >= 110 then
                    ready = true
                    break
                end
            end
        end
        assert(ready, "second guest has no stock melee lane to the target")
        guest:ForceNetUpdate()
        other:ForceNetUpdate()
        return table.concat({point_string(center), point_string(guest:K2_GetActorLocation()),
            point_string(other:K2_GetActorLocation())}, ";")
    elseif action == "multi-melee-target" then
        local center = position(actor_path)
        local target = melee_target(world, host, center)
        if not valid(target) then return "waiting" end
        local key = require("native_npc_barter_identity").key(target)
        return key and ("npc:%d;%.3f"):format(tonumber(key:sub(-4), 16), target.Health) or "waiting"
    elseif action == "seed-melee-edges" then
        assert(host, "only the host can grant the fixture item")
        return seed_pipe(world, pipe_path)
    elseif action == "seed-pipe" then
        assert(host, "only the host can grant the fixture item")
        assert(actor_path == "" or actor_path == "heavy", "unknown melee fixture item")
        local target = spawn_stock_npc(pawn, world, "state")
        -- This target measures incoming hits. Stock ResolveMelee can still run from an animation
        -- notify with the actor tick disabled. Keep its normal collision and damage receiver.
        assert(target.Melee_SweepRange > 0, "the staged NPC needs the stock melee range field")
        target.Melee_SweepRange = 0
        assert(target.Melee_SweepRange == 0, "the staged NPC still has an offensive melee range")
        return seed_pipe(world, actor_path == "heavy" and heavy_pipe_path or pipe_path)
    elseif action == "clear-melee-target" then
        local actor = melee_target(world, true)
        assert(host and valid(actor) and same(actor:GetWorld(), world) and not actor.Alive)
        actor:K2_DestroyActor()
        shooters.state = nil
        melee_stage = nil
        return "cleared"
    elseif action == "select-pipe" then
        assert(not host, "the guest must pick their test equipment")
        return select_equipment(pawn, function(row) return guid_of(row) == actor_path end)
    elseif action == "melee-grip" then
        local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        if not host then
            local weapon = pawn.EquipedItem
            local rig, mesh = weapon.Wep_Skeli, weapon.SM_Wep_Melee_Machete_01
            assert(valid(mesh) and valid(mesh.StaticMesh) and same(mesh:GetAttachParent(), rig)
                and mesh:GetAttachSocketName():ToString() == "Melee_Socket")
            local bone = FName("Wep_Root", EFindName.FNAME_Find)
            assert(rig:GetBoneIndex(bone) >= 0)
            local anchor = math_api:InverseTransformLocation(mesh:K2_GetComponentToWorld(), rig:GetSocketLocation(bone))
            return ("%s;%.3f,%.3f,%.3f"):format(mesh.StaticMesh:GetFullName(), anchor.X, anchor.Y, anchor.Z)
        end
        local mesh_name, grip = actor_path:match("^(StaticMesh /Game/[^;]+);(.+)$")
        assert(mesh_name and grip)
        local remote = authoritative_guest(world)
        local class = StaticFindObject("/Script/Engine.StaticMeshComponent")
        local matches, error_cm = 0, nil
        for _, raw in ipairs(remote:K2_GetComponentsByClass(class) or {}) do
            local ok, value = pcall(function() return raw:get() end)
            local copy = ok and value or raw
            local mesh = valid(copy) and copy.StaticMesh
            if valid(mesh) and mesh:GetFullName() == mesh_name and same(copy:GetOwner(), remote)
                and copy:IsVisible() and not copy.bHiddenInGame then
                local socket = copy:GetAttachSocketName()
                local body = copy:GetAttachParent()
                assert(valid(body) and socket:ToString() == "Weapon_Socket01" and body:DoesSocketExist(socket))
                local actual = math_api:TransformLocation(copy:K2_GetComponentToWorld(), position(grip))
                local hand = body:GetSocketLocation(socket)
                error_cm = math.sqrt((actual.X - hand.X)^2 + (actual.Y - hand.Y)^2 + (actual.Z - hand.Z)^2)
                matches = matches + 1
            end
        end
        assert(matches < 2, "duplicate projected melee mesh")
        return error_cm and ("%.3f"):format(error_cm) or "waiting"
    elseif action == "stage-melee" then
        assert(host, "only the host can stage the stock target")
        local attempt = math.tointeger(actor_path) or 1
        assert(attempt >= 1 and attempt <= 4, "invalid stock melee fixture bearing")
        return stage_melee(world, attempt)
    elseif action == "stage-melee-prop" then
        assert(host and not valid(melee_prop), "only one host melee prop is allowed")
        local path = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_PlasticBarrel.Prop_PlasticBarrel_C"
        local class = StaticFindObject(path)
        if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
        assert(valid(class), "stock plastic barrel class unavailable")
        local at = pawn:K2_GetActorLocation()
        local transform = {Translation={X=at.X, Y=at.Y, Z=at.Z+150}, Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
        local prop = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
        assert(valid(prop) and same(statics:FinishSpawningActor(prop, transform, 0), prop), "stock prop spawn failed")
        melee_prop = prop
        assert(valid(prop.PropMesh) and prop.PropMesh:IsSimulatingPhysics(FName("None")), "stock prop physics unavailable")
        prop.PropMesh:SetEnableGravity(false)
        return stage_melee(world, 1, prop)
    elseif action == "clear-melee-prop" then
        assert(host and valid(melee_prop) and same(melee_prop:GetWorld(), world), "stock melee fixture prop is unavailable")
        melee_prop:K2_DestroyActor()
        melee_prop = nil
        return "cleared"
    elseif action == "melee-prop-state" then
        assert(host and valid(melee_prop), "stock melee prop disappeared")
        local velocity = melee_prop.PropMesh:GetPhysicsLinearVelocity(FName("None"))
        local point = melee_prop.PropMesh:K2_GetComponentLocation()
        return ("%.3f;%.3f;%.3f;%.3f;%.3f"):format(melee_prop.Health,
            math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2), point.X, point.Y, point.Z)
    elseif action == "face-away-melee" then
        assert(host, "the host must keep its own view clear of the test target")
        local owner, target = authoritative_guest(world), position(actor_path)
        local eye = owner.Camera:K2_GetComponentLocation()
        local away = {X=2*eye.X-target.X, Y=2*eye.Y-target.Y, Z=eye.Z}
        assert(aim_melee(world, owner, away), "the host copy of the guest camera is unavailable")
        return "ready"
    elseif action == "face-melee" then
        assert(not host, "the guest must drive the test swing")
        assert(aim_melee(world, pawn, position(actor_path)), "the guest stock camera is not ready")
        return "ready"
    elseif action == "align-melee" then
        assert(host, "the test needs the host's replica camera")
        local guest = authoritative_guest(world)
        assert(aim_melee(world, guest, position(actor_path)), "the authoritative guest stock camera is not ready")
        local clear, status = melee_lane(guest, valid(melee_prop) and melee_prop or melee_target(world, true),
            guest.Camera:K2_GetComponentRotation())
        return clear and "true" or status
    elseif action == "melee-state" then
        local target = melee_target(world, host, position(actor_path))
        if not valid(target) then return "absent" end
        return ("%s;%s;%.3f;%s"):format(tostring(target.Alive), tostring(target.Mesh:IsAnySimulatingPhysics()),
            target.Health, target:GetFullName())
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
            if corpse_marker(marker, actor, host and 3 or 1) then
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
    track_pursuit()
    if live_probe and live_probe.events then
        local prior = live_observation.sample(live_probe)
        if prior then live_probe.prior = prior end
    end
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(command_limit + 1)
    request:close()
    local ticket, action, actor_path = (data or ""):match("^(%x+)\t([a-z%-]+)\t?([^\r\n]*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = false, "portable NPC probe command exceeds its size limit"
    if #data <= command_limit then ok, response = pcall(run, action, actor_path) end
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
