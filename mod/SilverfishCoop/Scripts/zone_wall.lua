local wall = {}
local zones = require("zone_maps")
local regroup = require("zone_regroup")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE_WALLS") == "1"
local expanded = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local named_fixtures = expanded and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local authored_routes = enabled and expanded and require("authored_wall_routes") or {}
local trigger_class = "/Script/Engine.TriggerBox"
local exit_class = "Level_Exit_BP_C"
local door_class = "Dungeon_TransitionDoor_BP_C"
local no_collision, query_only = 0, 1
local world_static, pawn_channel = 0, 2
local ignore, block = 0, 2
local range_squared = 1500 * 1500
local approach_range, retreat_margin = 480, 60
local records = {}
local status, failure = "inactive", ""
local observation
local primary_key
local retry_at = {}
local scanned_world
local scanned_actors = {}
local scan_after = 0
local approach_scope, open_exit
local guest_walls
local guest_check_interval = 0.5

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function distance_squared(first, second)
    local dx, dy, dz = first.X - second.X, first.Y - second.Y, first.Z - second.Z
    return dx * dx + dy * dy + dz * dz
end

local function capsule(pawn)
    if not valid(pawn) or not valid(pawn.CapsuleComponent) then return end
    local component = pawn.CapsuleComponent
    if component:GetCollisionObjectType() == pawn_channel and component:GetCollisionResponseToChannel(world_static) == block then
        return component
    end
end

local function set_ignore(component, actor, should_ignore)
    if valid(component) and valid(actor) then
        component:IgnoreActorWhenMoving(actor, should_ignore)
    end
end

local function clear_record(index)
    local record = records[index]
    if not record then return end
    pcall(set_ignore, record.host_capsule, record.actor, false)
    if valid(record.actor) then pcall(function() record.actor:K2_DestroyActor() end) end
    if open_exit == record then open_exit = nil end
    records[index] = nil
    retry_at[index] = nil
    if observation and observation.index == index then observation = nil end
end

function wall.start(bridge)
    if enabled then regroup.start(bridge) end
end

function wall.clear()
    for key in pairs(records) do clear_record(key) end
    retry_at = {}
    observation = nil
    primary_key, approach_scope, open_exit = nil, nil, nil
    scanned_world, scanned_actors, scan_after = nil, {}, 0
    guest_walls = nil
    status, failure = "inactive", ""
    regroup.clear()
end

local function same_rotation(first, second)
    for _, key in ipairs({ "Pitch", "Yaw", "Roll" }) do
        local delta = (first[key] - second[key] + 180) % 360 - 180
        if not finite(delta) or math.abs(delta) > 0.1 then return false end
    end
    return true
end

local function create(world, fixture)
    local original = fixture.Box
    local class = StaticFindObject(trigger_class)
    if not valid(original) or not same(original:GetOwner(), fixture) or not valid(class) then
        error("Expected the authored actor's Box and Engine.TriggerBox", 0)
    end

    local center = original:K2_GetComponentLocation()
    local rotation = original:K2_GetComponentRotation()
    local extent = original:GetScaledBoxExtent()
    if not finite(extent.X) or not finite(extent.Y) or not finite(extent.Z) or extent.X < 1 or extent.Y < 1 or extent.Z < 1
        or extent.X > 10000 or extent.Y > 10000 or extent.Z > 10000
        or not finite(center.X) or not finite(center.Y) or not finite(center.Z)
        or not finite(rotation.Pitch) or not finite(rotation.Yaw) or not finite(rotation.Roll) then
        error("The authored exit Box has invalid geometry", 0)
    end
    local actor = world:SpawnActor(class, { X = center.X, Y = center.Y, Z = center.Z },
        { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll })
    if not valid(actor) then error("The local exit blocker could not spawn", 0) end

    local ok, result = pcall(function()
        local component = actor.CollisionComponent
        if not valid(component) or not same(actor.RootComponent, component) or actor.bReplicates ~= false
            or not same(actor:GetWorld(), world) then
            error("Expected a local TriggerBox with its own root shape", 0)
        end
        actor:SetActorEnableCollision(false)
        actor:SetActorHiddenInGame(true)
        component:SetCollisionEnabled(no_collision)
        component:SetGenerateOverlapEvents(false)
        component:SetBoxExtent({ X = extent.X, Y = extent.Y, Z = extent.Z }, false)
        component:SetCollisionObjectType(world_static)
        component:SetCollisionResponseToAllChannels(ignore)
        component:SetCollisionResponseToChannel(pawn_channel, block)
        component:SetCollisionEnabled(query_only)
        actor:SetActorEnableCollision(true)

        local cloned = component:GetScaledBoxExtent()
        if component:GetCollisionEnabled() ~= query_only or component:GetCollisionObjectType() ~= world_static
            or component:GetCollisionResponseToChannel(pawn_channel) ~= block or distance_squared(cloned, extent) > 0.01
            or distance_squared(component:K2_GetComponentLocation(), center) > 0.01
            or not same_rotation(component:K2_GetComponentRotation(), rotation) then
            error("The staged wall's collision geometry could not be verified", 0)
        end
        return { actor = actor, component = component, fixture = fixture, original = original, world = world,
            center = center, extent = {extent.X, extent.Y, extent.Z},
            axes = {original:GetForwardVector(), original:GetRightVector(), original:GetUpVector()} }
    end)
    if not ok then
        pcall(function() actor:K2_DestroyActor() end)
        error(result, 0)
    end
    return result
end

local function host_can_pass(state, local_pawn, world, accepting)
    if not accepting or not local_pawn:HasAuthority() or type(state.clients) ~= "number"
        or state.clients < 0 or state.clients > 4 then return false end
    local count, positions, blockers = 0, {}, {}
    local host_position = local_pawn:K2_GetActorLocation()
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" then
            local remote = details.pawn
            if not valid(remote) or same(remote, local_pawn) or not same(remote:GetWorld(), world)
                or not remote:HasAuthority() or remote:IsActorBeingDestroyed() then return false end
            local address = remote:GetAddress()
            if positions[address] then return false end
            positions[address] = true
            count = count + 1
            local squared = distance_squared(host_position, remote:K2_GetActorLocation())
            if not finite(squared) then return false end
            if squared > range_squared then
                blockers[#blockers + 1] = {pawn = details.name, slot = details.slot, meters = math.sqrt(squared) / 100}
            end
        end
    end
    if count ~= state.clients then return false end
    return #blockers == 0, blockers
end

local function sync_shape(index, world, fixture, desired)
    local record = records[index]
    if not valid(fixture) then
        clear_record(index)
        return false
    end
    if record and (not same(record.fixture, fixture) or not same(record.world, world) or not valid(record.actor)
        or not same(record.original, fixture.Box)) then
        clear_record(index)
        record = nil
    end
    local ok, ready = pcall(function()
        if not record then
            if os.time() < (retry_at[index] or 0) then return false end
            retry_at[index] = os.time() + 2
            record = create(world, fixture)
            records[index] = record
        end
        if (record.host_capsule or desired) and not same(record.host_capsule, desired) then
            set_ignore(record.host_capsule, record.actor, false)
            set_ignore(desired, record.actor, true)
            record.host_capsule = desired
        end
        return true
    end)
    if not ok then
        clear_record(index)
        return false, tostring(ready)
    end
    return ready
end

local function in_world(actor, world)
    return valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
end

function wall.placed_actor(world, class, name)
    if not name or not valid(world) then return end
    local world_path = world:GetFullName():match("^World (.+)$")
    if not world_path then return end
    local path = world_path .. ":PersistentLevel." .. name
    local actor = StaticFindObject(path)
    if in_world(actor, world) and actor:GetFullName() == class .. " " .. path then return actor end
end

local function nearby_exit(location, range, managed)
    local name, nearest = nil, range ^ 2
    for key, record in pairs(records) do
        if not managed or record.managed then
            local center = record.center
            local x, y, z = location.X - center.X, location.Y - center.Y, location.Z - center.Z
            local squared = 0
            for index, length in ipairs(record.extent) do
                local axis = record.axes[index]
                local outside = math.max(0, math.abs(x * axis.X + y * axis.Y + z * axis.Z) - length)
                squared = squared + outside ^ 2
            end
            if squared < nearest or (squared == nearest and name and key < name) then
                name, nearest = key, squared
            end
        end
    end
    return name, nearest
end

local function close_approach()
    if open_exit then
        set_ignore(open_exit.host_capsule, open_exit.actor, false)
        open_exit.host_capsule, open_exit.nearest = nil, nil
        open_exit = nil
    end
end

function wall.update(get_world)
    local scope = approach_scope
    if not scope then return end
    if not valid(scope.pawn) or not same(scope.pawn:GetWorld(), scope.world) then
        close_approach()
        approach_scope = nil
        return
    end
    local key, squared = nearby_exit(scope.pawn:K2_GetActorLocation(), approach_range, true)
    local selected = key and records[key]
    if not selected and not open_exit then return end
    if not same(scope.world, get_world()) or not same(scope.capsule, scope.pawn.CapsuleComponent) then
        close_approach()
        approach_scope = nil
        SilverfishSetNativeExitApproach("")
        return
    end
    if open_exit and selected ~= open_exit then
        close_approach()
        SilverfishSetNativeExitApproach("")
    elseif open_exit and math.sqrt(squared) > open_exit.nearest + retreat_margin then
        close_approach()
        SilverfishSetNativeExitApproach("")
        return
    end
    if selected and in_world(selected.fixture, scope.world) and SilverfishSetNativeExitApproach(key) then
        if not open_exit then
            set_ignore(scope.capsule, selected.actor, true)
            selected.host_capsule, open_exit = scope.capsule, selected
        end
        selected.nearest = math.min(selected.nearest or approach_range, math.sqrt(squared))
    else
        close_approach()
    end
end

local function entrance_name(actor)
    local name = actor["Entrance Tag"]
    if type(name) ~= "string" and name then name = name:ToString() end
    return name
end

local function qualified_source(fixture, world, plan)
    if not in_world(fixture, world) then return nil, "placed actor not observed" end
    local entrance = entrance_name(fixture)
    if entrance ~= plan.entrance then return nil, "stock tag " .. tostring(entrance) .. " differs" end
    if plan.kind == "door" then
        local component = fixture.Level_Exit_BP
        local child = valid(component) and component.ChildActor
        if not in_world(child, world) or not child:GetFullName():match("^Level_Exit_BP_C ")
            or not same(component:GetOwner(), fixture) or not same(child:GetParentComponent(), component)
            or not same(fixture["As Level Exit BP"], child) or entrance_name(child) ~= plan.entrance then
            return nil, "stock door travel child not qualified"
        end
    end
    return fixture
end

local function scan_authored(world, plans)
    if not same(scanned_world, world) then
        scanned_world, scanned_actors, scan_after = world, {}, 0
    end
    local now = os.time()
    if now < scan_after then return end
    for _, plan in ipairs(plans) do
        if not in_world(scanned_actors[plan.actor], world) then
            local class = plan.kind == "door" and door_class or exit_class
            scanned_actors[plan.actor] = wall.placed_actor(world, class, plan.actor)
        end
    end
    scan_after = now + 1
end

local function wall_sources(world, map, route, fixture, secondary)
    local sources = {}
    if route.fixture then
        sources[route.fixture] = { fixture = fixture, tag = route.entrance, approach = route }
    end
    if route.secondary then
        sources[route.secondary.fixture] = { fixture = secondary, tag = route.secondary.entrance,
            approach = route.secondary }
    end
    local plans = authored_routes[map]
    if plans then
        scan_authored(world, plans)
        for _, plan in ipairs(plans) do
            local source = sources[plan.actor] or {}
            source.fixture, source.problem = qualified_source(scanned_actors[plan.actor], world, plan)
            source.tag, source.kind = plan.entrance, plan.kind
            sources[plan.actor] = source
        end
    end
    return sources
end

function wall.sync(state, phase, fixture, accepting, secondary)
    if not enabled then return end
    local hosting = phase == "hosting"
    local local_pawn = state.local_pawn and state.local_pawn.pawn
    local route = zones[state.map]
    if (not hosting and phase ~= "connected") or not route or not valid(local_pawn)
        or (not route.fixture and not authored_routes[state.map]) then
        wall.clear()
        return
    end
    local world = local_pawn:GetWorld()
    local local_capsule = capsule(local_pawn)
    if not same(world, state.world) or not local_capsule then
        wall.clear()
        status, failure = "arming", "Waiting for a compatible pawn capsule and exit Box"
        return
    end
    if not hosting and guest_walls and guest_walls.map == state.map and os.clock() < guest_walls.expires
        and same(guest_walls.world, world) and same(guest_walls.pawn, local_pawn)
        and same(guest_walls.capsule, local_capsule) then
        local intact = true
        for _, record in pairs(records) do
            if not valid(record.actor) or not valid(record.component) or not valid(record.fixture) then
                intact = false
                break
            end
        end
        if intact then return end
    end
    guest_walls = nil
    local permitted, blockers
    if hosting then permitted, blockers = host_can_pass(state, local_pawn, world, accepting) end
    local desired = permitted and local_capsule or nil
    local manages_overlap = hosting and type(SilverfishSetNativeExitApproach) == "function"
    approach_scope = manages_overlap and accepting and {pawn = local_pawn, capsule = local_capsule, world = world} or nil
    if not approach_scope then close_approach() end
    local sources = wall_sources(world, state.map, route, fixture, secondary)
    for key in pairs(records) do
        if not sources[key] then clear_record(key) end
    end
    primary_key = route.fixture or (route.door and route.door.fixture)
    local first_ready, diagnostics, had_error = nil, {}, false
    for key, source in pairs(sources) do
        local source_actor = in_world(source.fixture, world) and source.fixture or nil
        local managed = manages_overlap and source.kind == "overlap"
        local keep = records[key] and records[key].host_capsule
        local target
        if managed then
            if approach_scope then target = keep end
        else
            target = desired
        end
        local ready, problem = sync_shape(key, world, source_actor, target)
        if not ready then
            had_error = had_error or problem ~= nil
            diagnostics[#diagnostics + 1] = key .. " (" .. (problem or source.problem or "waiting for wall geometry") .. ")"
        else
            local record = records[key]
            record.tag, record.approach, record.kind, record.managed = source.tag, source.approach, source.kind, managed
            if not first_ready or key < first_ready then first_ready = key end
        end
    end
    primary_key = primary_key or first_ready
    status = primary_key and records[primary_key] and "ready" or had_error and "error" or "arming"
    table.sort(diagnostics)
    failure = table.concat(diagnostics, ", ")
    if not hosting and status == "ready" and failure == "" then
        guest_walls = {map = state.map, world = world, pawn = local_pawn, capsule = local_capsule,
            expires = os.clock() + guest_check_interval}
    end
    local near = blockers and #blockers > 0 and nearby_exit(local_pawn:K2_GetActorLocation(), 450)
    if near and not valid(records[near].host_capsule) then
        regroup.show(state, near, blockers)
    else
        regroup.clear()
    end
end

local function nexus_probe_target(pawn, capsule_component, box, start, center)
    local extent, up = box:GetScaledBoxExtent(), box:GetUpVector()
    local radius = capsule_component:GetScaledCapsuleRadius()
    if not finite(radius) or radius < 10 or radius > 100 or not finite(up.Z) or up.Z < .99 then
        error("The stock Gold Nexus sweep has unqualified capsule or box geometry", 0)
    end
    local tangent, length = box:GetForwardVector(), extent.X
    if extent.Y > extent.X then tangent, length = box:GetRightVector(), extent.Y end
    local lane = (start.X - center.X) * tangent.X + (start.Y - center.Y) * tangent.Y
    local limit = length - radius - 5
    local z = math.min(start.Z + 35, center.Z + extent.Z - 10)
    if not finite(lane) or math.abs(lane) > limit or not finite(z) or z - start.Z < 20 then
        error("Position the pawn on a qualified Gold Nexus approach lane before the swept wall probe", 0)
    end
    local raised = {X = start.X, Y = start.Y, Z = z}
    local goal = {X = center.X + lane * tangent.X, Y = center.Y + lane * tangent.Y, Z = z}
    local rotation = pawn:K2_GetActorRotation()
    if not pawn:K2_TeleportTo(raised, rotation) then error("The Gold Nexus sweep could not clear its stock floor", 0) end
    if distance_squared(pawn:K2_GetActorLocation(), raised) > 2^2 or box:IsOverlappingActor(pawn) then
        if not pawn:K2_TeleportTo(start, rotation) then error("The Gold Nexus sweep could not restore the pawn", 0) end
        error("The Gold Nexus sweep could not verify clearance on its original lane", 0)
    end
    return goal
end

function wall.command(fields, state, phase)
    if fields[2] ~= "native-exit-wall-probe" then return end
    local selector = fields[3]
    local exact = type(selector) == "string" and #selector <= 512 and selector:match("^Player_FP_C /Game/[^%c]+$")
    local tag, actor = fields[4], fields[5]
    local index = not tag and primary_key
    local plan
    if actor then
        if not named_fixtures or not tag or tag == "" or #actor > 80
            or (not actor:match("^Level_Exit_BP_C_%d+$") and not actor:match("^Dungeon_TransitionDoor_BP_C_%d+$")) then
            error("Choose an exact stock wall actor with its entrance tag in a headless fixture", 0)
        end
        for _, candidate in ipairs(authored_routes[state.map] or {}) do
            if candidate.actor == actor and candidate.entrance == tag then
                if plan then error("More than one wall matches the requested actor and entrance", 0) end
                plan = candidate
            end
        end
        if not plan then error("The exact authored wall and entrance pair is unavailable", 0) end
        index = plan.actor
    elseif tag then
        local planned = 0
        for _, route in ipairs(authored_routes[state.map] or {}) do
            if route.entrance == tag then planned = planned + 1 end
        end
        if planned > 1 then error("More than one wall matches the requested entrance", 0) end
        for key, candidate in pairs(records) do
            if candidate.tag == tag then
                if index then error("More than one wall matches the requested entrance", 0) end
                index = key
            end
        end
    end
    local record = index and records[index]
    if not enabled or phase ~= "hosting" or not record or not same(record.world, state.world)
        or not valid(record.actor) or not valid(record.component) or #fields > 5
        or type(selector) ~= "string"
        or (not exact and selector ~= "local" and selector ~= "guest" and not selector:match("^guest[2-4]$")) then
        error("Expected a local slot or exact guest pawn with an optional authored entrance tag", 0)
    end
    if plan then
        local world_name = state.map .. "." .. state.map:match("[^/]+$")
        local class = plan.kind == "door" and door_class or exit_class
        local source = qualified_source(record.fixture, state.world, plan)
        if state.server ~= false or state.listening ~= true or state.world:GetFullName() ~= "World " .. world_name
            or not source or not source:HasAuthority()
            or source:GetFullName() ~= class .. " " .. world_name .. ":PersistentLevel." .. plan.actor
            or record.tag ~= plan.entrance or record.kind ~= plan.kind or not same(record.original, source.Box)
            or not same(record.original:GetOwner(), source)
            or not in_world(record.actor, state.world) or not same(record.component:GetOwner(), record.actor)
            or not same(record.actor.RootComponent, record.component) or record.actor:GetActorEnableCollision() ~= true
            or record.component:GetCollisionEnabled() ~= query_only
            or record.component:GetCollisionObjectType() ~= world_static
            or record.component:GetCollisionResponseToChannel(pawn_channel) ~= block then
            error("The selected stock wall geometry is stale or unavailable", 0)
        end
        local box, shape = record.original, record.component
        local offset = distance_squared(box:K2_GetComponentLocation(), shape:K2_GetComponentLocation())
        local extents = distance_squared(box:GetScaledBoxExtent(), shape:GetScaledBoxExtent())
        if not finite(offset) or offset > 0.01 or not finite(extents) or extents > 0.01
            or not same_rotation(box:K2_GetComponentRotation(), shape:K2_GetComponentRotation()) then
            error("The selected stock wall geometry is stale or unavailable", 0)
        end
    end
    local pawn, slot
    for _, details in ipairs(state.pawns) do
        local current = details.pawn
        local alias = details.slot or details.kind
        if in_world(current, record.world) and current:HasAuthority()
            and ((not exact and alias == selector)
                or (exact and details.kind == "guest" and details.name == selector and current:GetFullName() == selector)) then
            if pawn then error("More than one pawn occupies the requested slot", 0) end
            pawn, slot = current, alias
        end
    end
    local pawn_capsule = capsule(pawn)
    if not valid(pawn) or not same(pawn:GetWorld(), record.world) or not pawn_capsule then
        error("The authoritative pawn capsule is unavailable", 0)
    end
    local center = record.component:K2_GetComponentLocation()
    local start = pawn:K2_GetActorLocation()
    local approach = record.approach
    -- Tall authored boxes are entered at terrain height, well below their center.
    local nexus_probe = plan and plan.kind == "overlap" and state.map == "/Game/Classes/Maps/Dreams/GoldNexus_P"
    if not nexus_probe and ((approach and approach.probe_arrival) or ((plan or not approach) and record.kind)) then
        local extent = record.component:GetScaledBoxExtent()
        if approach and approach.probe_arrival then
            local forward = record.fixture:GetActorForwardVector()
            local normal_distance = (start.X - center.X) * forward.X + (start.Y - center.Y) * forward.Y
            center.X, center.Y = start.X - normal_distance * forward.X, start.Y - normal_distance * forward.Y
        end
        local clearance = math.max(0, extent.Z - 100)
        center.Z = math.max(center.Z - clearance, math.min(start.Z, center.Z + clearance))
    end
    local from = distance_squared(start, center)
    if finite(from) and from > 1250^2 and plan and plan.kind == "overlap" and not nexus_probe
        and not (approach and approach.probe_arrival) then
        -- Very wide exits can have a safe lane far from the box center.
        local extent = record.component:GetScaledBoxExtent()
        local normal, tangent = record.component:GetForwardVector(), record.component:GetRightVector()
        local width, length = extent.X, extent.Y
        if extent.Y < extent.X then normal, tangent, width, length = tangent, normal, length, width end
        local dx, dy = start.X - center.X, start.Y - center.Y
        local outward, lateral = dx * normal.X + dy * normal.Y, dx * tangent.X + dy * tangent.Y
        local radius = pawn_capsule:GetScaledCapsuleRadius()
        if finite(outward) and finite(lateral) and finite(radius) and radius >= 10 and radius <= 100
            and math.abs(outward) >= width and math.abs(outward) <= width + 425
            and math.abs(lateral) <= length + radius then
            local depth = math.max(0, width - radius - 45) * (outward < 0 and -1 or 1)
            local lane = math.max(0, length - radius - 35)
            local side = math.max(-lane, math.min(lateral, lane))
            center.X = center.X + depth * normal.X + side * tangent.X
            center.Y = center.Y + depth * normal.Y + side * tangent.Y
            from = distance_squared(start, center)
        end
    end
    local overlapping = record.fixture.Box:IsOverlappingActor(pawn)
    if not finite(from) or from > 1250^2 or overlapping then
        error(string.format("Position the pawn near the exit before the swept wall probe (distance %.1f, overlapping %s)",
            finite(from) and math.sqrt(from) or -1, tostring(overlapping)), 0)
    end
    if nexus_probe then
        local inward = record.original:GetRightVector()
        local depth = (start.X - center.X) * inward.X + (start.Y - center.Y) * inward.Y
        if not finite(depth) or depth < -150 or depth >= -20 then
            error("Position the pawn in the selected Gold Nexus crypt's outward pocket before the swept wall probe", 0)
        end
        center = nexus_probe_target(pawn, pawn_capsule, record.original, start, center)
        start = pawn:K2_GetActorLocation()
    end
    local hit = {}
    pawn:K2_SetActorLocation({ X = center.X, Y = center.Y, Z = center.Z }, true, hit, false)
    pawn:ForceNetUpdate()
    hit = hit.SweepHitResult or hit.OutSweepHitResult or hit
    local component = hit.Component and hit.Component:Get()
    local outcome = hit.bBlockingHit and (same(component, record.component) and "wall" or "other") or "clear"
    local remaining = distance_squared(pawn:K2_GetActorLocation(), center)
    observation = { index = index, slot = slot, outcome = outcome,
        remaining = finite(remaining) and math.sqrt(remaining) or -1 }
    if outcome == "other" then
        local name = valid(component) and component:GetFullName() or "unknown component"
        local owner = valid(component) and component:GetOwner()
        return string.format("native exit sweep other%s (%s on %s, start %.1f %.1f %.1f, target %.1f %.1f %.1f, hit fraction %.3f, penetration depth %.3f)",
            plan and " at stock " .. plan.actor or "", name, valid(owner) and owner:GetFullName() or "unknown actor",
            start.X, start.Y, start.Z, center.X, center.Y, center.Z, hit.Time or -1, hit.PenetrationDepth or -1)
    end
    return "native exit sweep " .. outcome .. (plan and " at stock " .. plan.actor or "")
end

function wall.rows(status_row)
    if not enabled then return {} end
    local primary = records[primary_key]
    local host_open = open_exit ~= nil or primary and valid(primary.host_capsule)
    local rows = {status_row("native-zone-wall", status, host_open and 1 or 0,
        observation and observation.slot or "-", observation and observation.outcome or "-",
        observation and observation.remaining or "-", failure)}
    return regroup.rows(rows, status_row)
end

return wall
