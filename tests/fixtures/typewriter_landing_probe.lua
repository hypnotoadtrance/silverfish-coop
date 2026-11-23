local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not bridge
    or not bridge:match("[/\\]placed%-prop%-carry%-trace%-matched%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local typewriter = map .. ":PersistentLevel.Prop_TypeWriter_C_3"
-- Use the grounded Crossroads lane from the hazard fixture. Keep the existing grab offset.
local stage_host = {X = 14270, Y = -200}
local stage_guest = {X = 13850, Y = -200}
local stage_prop = {X = 13952.70, Y = -275.25}
local lane_height = 132
local start_facing = {Pitch = 0, Yaw = -35.4415, Roll = 0}
local release_facing = {Pitch = 48, Yaw = 34.5585, Roll = 0}
local drop_facing = {Pitch = -30, Yaw = start_facing.Yaw, Roll = 0}
local zero = {X = 0, Y = 0, Z = 0}
local color = {R = 0, G = 0, B = 0, A = 0}
local release_path = "/Script/Engine.PhysicsHandleComponent:ReleaseComponent"
local request, reply = bridge .. "/typewriter-landing-command.tsv", bridge .. "/typewriter-landing-reply.tsv"
local trace_path = bridge .. "/typewriter-landing-trace.tsv"
local watch, last_request

local function valid(value) return value and value:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function unwrap(value)
    if type(value) == "userdata" and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then
        return value:get()
    end
    return value
end

local function context()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. map, "matched landing requires stock Crossroads")
    local driver = world.NetDriver
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local local_pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(driver) and valid(local_pawn) and local_pawn:IsLocallyControlled(), "matched landing has no local co-op seat")
    local hosting = local_pawn:HasAuthority() and not valid(driver.ServerConnection)
    local pawn = local_pawn
    if hosting then
        assert(os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and #driver.ClientConnections == 1,
            "matched landing requires one private host peer")
        local connection = unwrap(driver.ClientConnections[1])
        pawn = valid(connection) and valid(connection.PlayerController) and connection.PlayerController.Pawn
    else
        assert(valid(driver.ServerConnection) and not local_pawn:HasAuthority(), "matched landing has no live guest route")
    end
    local actor = StaticFindObject(typewriter)
    local body = valid(actor) and actor.PropMesh
    assert(valid(pawn) and pawn:HasAuthority() == hosting and pawn.Dead == false and same(pawn:GetWorld(), world)
        and valid(actor) and actor:HasAuthority() == hosting and same(actor:GetWorld(), world)
        and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) and not actor:IsActorBeingDestroyed()
        and same(body, actor.RootComponent) and same(body:GetOwner(), actor), "matched landing stock identity changed")
    local grip = pawn.PhysicsHandle
    assert(valid(grip) and same(grip:GetOwner(), pawn), "matched landing stock handle changed")
    return {hosting = hosting, world = world, driver = driver, local_pawn = local_pawn,
        pawn = pawn, actor = actor, body = body, grip = grip}
end

local function holding(scope)
    return same(scope.pawn.HeldObject, scope.actor) and same(scope.grip:GetGrabbedComponent(), scope.body)
end

local function distance_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function hit_description(hit)
    local point, normal = hit.ImpactPoint, hit.ImpactNormal
    local component = hit.Component and hit.Component:Get()
    return (valid(component) and component:GetFullName() or "unknown component")
        .. (point and normal and (" at (%.1f,%.1f,%.1f), normal (%.3f,%.3f,%.3f)"):format(
            point.X, point.Y, point.Z, normal.X, normal.Y, normal.Z) or "")
end

local function level_floor(scope, hit, label)
    local point, normal = hit.ImpactPoint, hit.ImpactNormal
    local component = hit.Component and hit.Component:Get()
    local owner = valid(component) and component:GetOwner()
    assert(point and normal and normal.Z >= .99 and valid(owner) and same(owner:GetWorld(), scope.world)
        and component.Mobility == 0, label .. ": " .. hit_description(hit))
    return point, normal
end

local function trace_floor(scope, api, x, y, top, bottom, label)
    local result = {}
    assert(api:LineTraceSingle(scope.pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom},
        0, false, {scope.actor, scope.pawn, scope.local_pawn}, 0, result, true, color, color, 0) == true,
        label .. (": no floor in stock trace at (%.1f,%.1f)"):format(x, y))
    return level_floor(scope, result.OutHit or result, label)
end

local function pawn_start(scope, api, pawn, location)
    local ground = trace_floor(scope, api, location.X, location.Y, lane_height + 350, lane_height - 350,
        "matched pawn staging floor")
    local capsule = pawn.CapsuleComponent
    local half_height, radius = capsule:GetScaledCapsuleHalfHeight(), capsule:GetScaledCapsuleRadius()
    local point = {X = ground.X, Y = ground.Y, Z = ground.Z + half_height + 8}
    local above = {X = point.X, Y = point.Y, Z = point.Z + 2}
    local hit = {}
    assert(api:CapsuleTraceSingle(pawn, point, above, radius, half_height, 0, false,
        {scope.actor, scope.local_pawn, scope.pawn}, 0, hit, true, color, color, 0) == false,
        "matched pawn staging capsule blocked: " .. hit_description(hit.OutHit or hit))
    return point
end

local function stage(scope)
    assert(scope.hosting and not watch and not valid(scope.pawn.HeldObject) and not valid(scope.local_pawn.HeldObject),
        "matched landing must be staged on the empty-handed host before pickup")
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    assert(valid(api), "the matched stock staging trace is unavailable")
    local host_start = pawn_start(scope, api, scope.local_pawn, stage_host)
    local guest_start = pawn_start(scope, api, scope.pawn, stage_guest)
    local ground = trace_floor(scope, api, stage_prop.X, stage_prop.Y, lane_height + 350, lane_height - 350,
        "matched Typewriter staging floor")
    local drop_start = {X = ground.X, Y = ground.Y, Z = ground.Z + 48}
    local hit = {}
    assert(api:SphereTraceSingle(scope.pawn, drop_start, {X = drop_start.X, Y = drop_start.Y, Z = drop_start.Z + 2},
        16, 0, false, {scope.actor, scope.local_pawn, scope.pawn}, 0, hit, true, color, color, 0) == false,
        "matched Typewriter staging body blocked: " .. hit_description(hit.OutHit or hit))
    assert(scope.local_pawn:K2_TeleportTo(host_start, start_facing) and scope.pawn:K2_TeleportTo(guest_start, start_facing),
        "stock matched pawn placement failed")
    local bone = FName("None", EFindName.FNAME_Find)
    assert(scope.body:IsSimulatingPhysics(bone) and
        scope.actor:K2_SetActorLocationAndRotation(drop_start, start_facing, false, {}, true),
        "stock matched Typewriter placement failed")
    scope.body:SetPhysicsLinearVelocity(zero, false, bone)
    scope.body:SetPhysicsAngularVelocityInDegrees(zero, false, bone)
    scope.body:WakeRigidBody(bone)
    scope.local_pawn:ForceNetUpdate()
    scope.pawn:ForceNetUpdate()
    scope.actor:ForceNetUpdate()
    local host = scope.local_pawn:K2_GetActorLocation()
    local player, prop = scope.pawn:K2_GetActorLocation(), scope.body:K2_GetComponentLocation()
    assert(distance_squared(host, host_start) <= 2^2 and distance_squared(player, guest_start) <= 2^2
        and distance_squared(prop, drop_start) <= 2^2,
        "the stock staging call adjusted a fixed origin")
    return {player.X, player.Y, player.Z, prop.X, prop.Y, prop.Z, host.X, host.Y, host.Z, ground.Z}
end

local function start_sample(scope, drop)
    assert(not watch and not valid(scope.pawn.HeldObject), "matched staging must be observed before the grab")
    local player, prop = scope.pawn:K2_GetActorLocation(), scope.body:K2_GetComponentLocation()
    local rotation = scope.body:K2_GetComponentRotation()
    local velocity = scope.body:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
    local speed = math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2)
    local sample = {player.X, player.Y, player.Z, prop.X, prop.Y, prop.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, speed}
    if drop and (prop.X - stage_prop.X)^2 + (prop.Y - stage_prop.Y)^2 <= 8^2 then
        local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        if valid(api) then
            local ok, ground, normal = pcall(trace_floor, scope, api, prop.X, prop.Y, prop.Z + 4, prop.Z - 125,
                "matched drop staging support")
            if ok then
                table.insert(sample, ground.Z)
                table.insert(sample, normal.X)
                table.insert(sample, normal.Y)
                table.insert(sample, normal.Z)
            end
        end
    end
    return sample
end

local function held_sample(scope)
    local point, rotation = scope.body:K2_GetComponentLocation(), scope.body:K2_GetComponentRotation()
    local target, facing = {}, {}
    scope.grip:GetTargetLocationAndRotation(target, facing)
    local bone = FName("None", EFindName.FNAME_Find)
    local velocity = scope.body:GetPhysicsLinearVelocity(bone)
    local spin = scope.body:GetPhysicsAngularVelocityInDegrees(bone)
    return {point.X, point.Y, point.Z, rotation.Pitch, rotation.Yaw, rotation.Roll,
        target.X, target.Y, target.Z, facing.Pitch, facing.Yaw, facing.Roll,
        math.sqrt(velocity.X^2 + velocity.Y^2 + velocity.Z^2), math.sqrt(spin.X^2 + spin.Y^2 + spin.Z^2)}
end

local function sample(scope, now)
    local body = scope.body
    local bone = FName("None", EFindName.FNAME_Find)
    local point, rotation = body:K2_GetComponentLocation(), body:K2_GetComponentRotation()
    local velocity, spin = body:GetPhysicsLinearVelocity(bone), body:GetPhysicsAngularVelocityInDegrees(bone)
    local awake = body:IsAnyRigidBodyAwake() and 1 or 0
    local record = {now - scope.release, point.X, point.Y, point.Z, rotation.Pitch, rotation.Yaw, rotation.Roll,
        velocity.X, velocity.Y, velocity.Z, spin.X, spin.Y, spin.Z, awake, holding(scope) and 1 or 0}
    for index = 1, 15 do
        local value = record[index]
        assert(type(value) == "number" and value == value and math.abs(value) < 1e8, "invalid matched body sample")
    end
    scope.rows[#scope.rows + 1] = table.concat(record, "\t")
end

local function floor(scope)
    local center = scope.body:K2_GetComponentLocation()
    local bottom = {X = center.X, Y = center.Y, Z = center.Z - 90}
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local result = {}
    if api:LineTraceSingle(scope.pawn, center, bottom, 0, false, {scope.actor, scope.pawn, scope.local_pawn}, 0,
        result, true, color, color, 0) then
        local hit = result.OutHit or result
        if hit.ImpactNormal and hit.ImpactPoint then
            local point, normal = hit.ImpactPoint, hit.ImpactNormal
            return table.concat({point.X, point.Y, point.Z, normal.X, normal.Y, normal.Z}, "\t")
        end
    end
    return "-"
end

local function finish(err)
    local output = assert(io.open(trace_path, "wb"))
    if err then
        output:write("error\t", tostring(err):gsub("[\t\r\n]", " "), "\n")
    else
        local floor_ok, normal = pcall(floor, watch)
        output:write("ok\t", typewriter, "\t", watch.hosting and "host" or "guest", "\t", #watch.rows, "\n",
            floor_ok and normal or "-", "\n", table.concat(watch.rows, "\n"), "\n")
    end
    output:close()
    watch = nil
end

RegisterHook(release_path, function(param)
    local scope = watch
    if not scope or scope.release or not same(param:get(), scope.grip) or not holding(scope) then return end
    scope.release = os.clock()
    local ok, err = pcall(sample, scope, scope.release)
    if not ok then finish(err) end
end)

local function verify_fixed_throw_corridor(scope)
    local pawn, body, actor = scope.pawn, scope.body, scope.actor
    local center, eye = body:K2_GetComponentLocation(), pawn.Camera:K2_GetComponentLocation()
    local arrow = pawn.Prop_Arrow:K2_GetComponentLocation()
    local velocity = body:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
    local mass, limit, power = body:GetMass(), pawn.MaxHoldWeight, pawn.ThrowVelocity
    local gravity = pawn.CharacterMovement:GetGravityZ()
    assert(mass > 0 and limit > 1 and power > 0 and gravity < 0, "invalid matched stock throw inputs")

    -- Stock release aims from Prop_Arrow toward the camera's point 1000 cm forward.
    local pitch, yaw = math.rad(release_facing.Pitch), math.rad(release_facing.Yaw)
    local x = eye.X - arrow.X + 1000 * math.cos(pitch) * math.cos(yaw)
    local y = eye.Y - arrow.Y + 1000 * math.cos(pitch) * math.sin(yaw)
    local z = eye.Z - arrow.Z + 1000 * math.sin(pitch)
    local distance = math.sqrt(x^2 + y^2 + z^2)
    assert(distance > 1, "invalid matched stock throw axis")
    local speed = power * (1 - .7 * math.max(0, math.min(1, (mass - 1) / (limit - 1))))
    local vx, vy, vz = velocity.X + x * speed / distance, velocity.Y + y * speed / distance,
        velocity.Z + z * speed / distance
    local length = math.sqrt(vx^2 + vy^2 + vz^2)
    if length > speed * 1.5 then
        local scale = speed * 1.5 / length
        vx, vy, vz = vx * scale, vy * scale, vz * scale
    end
    local horizontal = math.sqrt(vx^2 + vy^2)
    assert(horizontal >= 125, "matched stock throw has insufficient projected travel")
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    assert(valid(trace), "the matched stock throw trace is unavailable")
    local previous, ignored = center, {pawn, actor}
    local step_seconds = math.min(.05, 25 / horizontal)
    for step = 1, 128 do
        local seconds = step * step_seconds
        local next_point = {X = center.X + vx * seconds, Y = center.Y + vy * seconds,
            Z = center.Z + vz * seconds + .5 * gravity * seconds^2}
        local result = {}
        local blocked = trace:SphereTraceSingle(pawn, previous, next_point, 16, 0, false, ignored, 0, result,
            true, color, color, 0)
        assert(blocked == true or blocked == false, "matched stock throw trace did not return a result")
        if blocked then
            local hit = result.OutHit or result
            assert(vz + gravity * seconds < 0 and hit.ImpactPoint
                and (hit.ImpactPoint.X - center.X)^2 + (hit.ImpactPoint.Y - center.Y)^2 >= 100^2,
                "matched fixed throw corridor has an early obstruction: " .. hit_description(hit))
            local at, normal = level_floor(scope, hit, "matched fixed throw contact is not level stock floor")
            for _, offset in ipairs({{35, 0}, {-35, 0}, {0, 35}, {0, -35}}) do
                local ground = trace_floor(scope, trace, at.X + offset[1], at.Y + offset[2],
                    at.Z + 25, at.Z - 25, "matched fixed throw landing footprint")
                assert(math.abs(ground.Z - at.Z) <= 5, "matched fixed throw landing footprint crosses a step")
            end
            return {at.X, at.Y, at.Z, normal.X, normal.Y, normal.Z}
        end
        previous = next_point
    end
    error("matched fixed throw did not reach a bounded stock landing", 0)
end

local function operate(action)
    local scope = context()
    if action == "stage" then return stage(scope) end
    if action == "ready" or action == "readydrop" then return start_sample(scope, action == "readydrop") end
    assert(holding(scope) and scope.body:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)),
        "matched landing requires the stock exact grip")
    if action == "hold" then return held_sample(scope) end
    if action == "dropaim" then
        assert(not scope.hosting and not watch, "only the guest may aim for a matched drop before arming")
        scope.pawn.Controller:SetControlRotation(drop_facing)
        scope.pawn.Camera:K2_SetWorldRotation(drop_facing, false, {}, true)
        local applied = scope.pawn.Camera:K2_GetComponentRotation()
        return {applied.Pitch, applied.Yaw}
    end
    if action == "arm" or action == "armdrop" then
        assert(not watch, "matched landing already armed")
        scope.rows = {}
        scope.drop = action == "armdrop"
        watch = scope
        return {}
    end
    if action == "drop" then
        assert(not scope.hosting and watch and watch.drop and same(watch.pawn, scope.pawn) and not watch.release,
            "matched landing may drop only from the armed guest")
        local aim = scope.pawn.Camera:K2_GetComponentRotation()
        assert(math.abs(aim.Pitch - drop_facing.Pitch) < .05 and math.abs(aim.Yaw - drop_facing.Yaw) < .05,
            "the stock drop aim changed while settling")
        local point = scope.body:K2_GetComponentLocation()
        local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        assert(valid(api), "the matched drop floor trace is unavailable")
        local ground = trace_floor(scope, api, point.X, point.Y, point.Z, point.Z - 125,
            "matched drop floor")
        assert(point.Z - ground.Z >= 32, "matched drop body is too close to the stock floor")
        local near_floor = {X = point.X, Y = point.Y, Z = ground.Z + 32}
        local hit = {}
        assert(api:SphereTraceSingle(scope.pawn, point, near_floor, 16, 0, false,
            {scope.pawn, scope.actor}, 0, hit, true, color, color, 0) == false,
            "matched drop corridor is blocked: " .. hit_description(hit.OutHit or hit))
        local before = held_sample(scope)
        scope.pawn:Grab_Release_Object(false, true, false)
        assert(not holding(scope) and watch and watch.release, "fixed stock drop did not release the exact grip")
        return before
    end
    assert(action == "throw" and not scope.hosting and watch and same(watch.pawn, scope.pawn) and not watch.release,
        "matched landing may throw only from the armed guest")
    scope.pawn.Controller:SetControlRotation(release_facing)
    scope.pawn.Camera:K2_SetWorldRotation(release_facing, false, {}, true)
    local applied = scope.pawn.Camera:K2_GetComponentRotation()
    assert(math.abs(applied.Pitch - release_facing.Pitch) < .05 and math.abs(applied.Yaw - release_facing.Yaw) < .05,
        "the stock camera did not take the matched throw aim")
    local landing = verify_fixed_throw_corridor(scope)
    scope.pawn:Grab_Release_Object(true, false, false)
    assert(not holding(scope) and watch and watch.release, "fixed stock throw did not release the exact grip")
    return {applied.Pitch, applied.Yaw, table.unpack(landing)}
end

LoopInGameThreadWithDelay(25, function()
    local now = os.clock()
    if watch and watch.release then
        local scope = watch
        local elapsed = now - scope.release
        local sample_interval = scope.drop and elapsed <= .5 and .018 or .045
        if elapsed >= 10 then
            finish()
        elseif elapsed - (scope.last or 0) >= sample_interval then
            if not valid(scope.actor) or not same(scope.actor.RootComponent, scope.body)
                or not same(scope.world.NetDriver, scope.driver) or not same(scope.actor:GetWorld(), scope.world) then
                finish("stock body or world was replaced during landing")
            else
                local ok, err = pcall(sample, scope, now)
                if ok then scope.last = elapsed else finish(err) end
            end
        end
        return
    end
    local input = io.open(request, "rb")
    if not input then return end
    local raw = input:read(64) or ""
    input:close()
    local id, action = raw:match("^(%x+)\t([a-z]+)\n$")
    if not id or #id ~= 32 or id == last_request then return end
    last_request = id
    local ok, result = pcall(operate, action)
    local output = assert(io.open(reply, "wb"))
    output:write(id, "\t", ok and "ok\t" .. table.concat(result, "\t")
        or "error\t" .. tostring(result):gsub("[\t\r\n]", " "), "\n")
    output:close()
end)
