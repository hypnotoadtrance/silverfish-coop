local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not bridge then return end
local matched = bridge:match("[/\\]clients[/\\]placed%-prop%-carry%-trace%-matched%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
if not matched and not bridge:match("[/\\]clients[/\\]placed%-prop%-carry%-trace%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local typewriter = map .. ":PersistentLevel.Prop_TypeWriter_C_3"
local request, response = bridge .. "/prop-grip-trace-request", bridge .. "/prop-grip-trace-reply.tsv"
local stationary_seconds, turn_seconds = .65, 1.0
local lift_angle = {Pitch = -30, Yaw = -35.4415, Roll = 0}
local matched_turn = -70
local sweep_radius = 16
local lift_output = bridge .. "/prop-grip-lift.tsv"
local color = {R = 0, G = 0, B = 0, A = 0}
local trace, finished
local next_request = 0

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function clear_turn(pawn, actor, body, grip, camera, audit)
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local center, pivot = body:K2_GetComponentLocation(), camera:K2_GetComponentLocation()
    local target, facing = {}, {}
    grip:GetTargetLocationAndRotation(target, facing)
    local x, y = target.X - pivot.X, target.Y - pivot.Y
    local ignored = {pawn, actor}
    for _, degrees in ipairs(matched and {matched_turn} or {70, -70}) do
        local previous, clear = center, true
        for step = 1, 4 do
            local angle = math.rad(degrees * step / 4)
            local sine, cosine = math.sin(angle), math.cos(angle)
            local next_point = {X = center.X + x * cosine - y * sine - x,
                Y = center.Y + x * sine + y * cosine - y, Z = center.Z}
            local hit = {}
            if api:SphereTraceSingle(pawn, previous, next_point, sweep_radius, 0, false, ignored, 0, hit,
                true, color, color, 0) ~= false then
                if audit then
                    hit = hit.OutHit or hit
                    local at, normal = hit.ImpactPoint, hit.ImpactNormal
                    local values = {"blocked_arc", degrees, step, previous.X, previous.Y, previous.Z,
                        next_point.X, next_point.Y, next_point.Z}
                    if at and normal then
                        for _, value in ipairs({at.X, at.Y, at.Z, normal.X, normal.Y, normal.Z}) do
                            values[#values + 1] = value
                        end
                    end
                    audit[#audit + 1] = table.concat(values, "\t")
                end
                clear = false
                break
            end
            previous = next_point
        end
        if clear then return degrees end
    end
    error("the stock Typewriter has no clear fixture turn", 0)
end

local function start(now)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. map, "the stock Crossroads world is unavailable")
    local driver = world.NetDriver
    assert(valid(driver) and valid(driver.ServerConnection), "the trace requires a connected guest")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn, actor = statics:GetPlayerPawn(world, 0), StaticFindObject(typewriter)
    assert(valid(pawn) and pawn:IsLocallyControlled() and not pawn:HasAuthority() and pawn.Dead == false
        and valid(actor) and not actor:HasAuthority() and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed(),
        "the exact Typewriter replica or guest is unavailable")
    local grip, body, controller, camera = pawn.PhysicsHandle, actor.PropMesh, pawn.Controller, pawn.Camera
    assert(same(body, actor.RootComponent) and same(body:GetOwner(), actor) and valid(grip) and same(grip:GetOwner(), pawn)
        and valid(controller) and valid(camera) and same(camera:GetOwner(), pawn)
        and same(pawn.HeldObject, actor) and same(grip:GetGrabbedComponent(), body), "the stock Typewriter grip is unavailable")
    local current = {pawn = pawn, actor = actor, grip = grip, body = body, controller = controller, camera = camera,
        angle = controller:GetControlRotation(), connection = driver.ServerConnection, world = world, driver = driver,
        start = now, last = -1, rows = {}}
    if matched then
        current.lift_start = now
        current.audit = {}
    else
        current.turn_degrees = clear_turn(pawn, actor, body, grip, camera)
    end
    return current
end

local function exact_grip(current)
    local pawn, actor, grip, body = current.pawn, current.actor, current.grip, current.body
    assert(valid(pawn) and pawn.Dead == false and same(pawn.HeldObject, actor)
        and same(pawn.PhysicsHandle, grip) and same(grip:GetGrabbedComponent(), body)
        and same(actor.RootComponent, body) and same(current.world.NetDriver, current.driver)
        and same(current.driver.ServerConnection, current.connection) and same(pawn:GetWorld(), current.world)
        and same(pawn.Controller, current.controller) and same(pawn.Camera, current.camera), "the exact grip changed during trace")
end

local function verify_lift_floor(current, point)
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local bottom = {X = point.X, Y = point.Y, Z = point.Z - 125}
    local result = {}
    assert(valid(api) and api:LineTraceSingle(current.pawn, point, bottom, 0, false, {current.pawn, current.actor},
        0, result, true, color, color, 0) == true, "matched held Typewriter has no stock floor below it")
    local hit = result.OutHit or result
    local component = hit.Component and hit.Component:Get()
    local floor, normal = hit.ImpactPoint, hit.ImpactNormal
    assert(floor and normal and normal.Z >= .99 and valid(component) and component.Mobility == 0
        and valid(component:GetOwner()) and same(component:GetOwner():GetWorld(), current.world),
        "matched held Typewriter is not above level stock floor")
    local clearance = point.Z - floor.Z
    current.audit[#current.audit + 1] = table.concat({"support", floor.X, floor.Y, floor.Z,
        normal.X, normal.Y, normal.Z, clearance}, "\t")
    assert(clearance >= 2 * sweep_radius, "matched held Typewriter lacks clearance above the stock floor")
end

local function lift(current, now)
    exact_grip(current)
    local body = current.body
    local point = body:K2_GetComponentLocation()
    local velocity = body:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
    local target, facing = {}, {}
    current.grip:GetTargetLocationAndRotation(target, facing)
    local elapsed = now - current.lift_start
    local values = {elapsed, point.X, point.Y, point.Z, target.X, target.Y, target.Z, velocity.X, velocity.Y, velocity.Z}
    for index = 1, 10 do
        local value = values[index]
        assert(type(value) == "number" and value == value and math.abs(value) < 1e8, "invalid matched stock lift reading")
    end
    local row = table.concat(values, "\t")
    if #current.audit == 0 then
        current.audit[1] = "start\t" .. row
        current.lift_target_z = target.Z
        current.controller:SetControlRotation(lift_angle)
        current.camera:K2_SetWorldRotation(lift_angle, false, {}, true)
        return false
    end
    local offset = (point.X - target.X)^2 + (point.Y - target.Y)^2 + (point.Z - target.Z)^2
    -- The first post-grab body sample is transient. Qualify the raised stock target and actual floor clearance.
    if target.Z >= current.lift_target_z + 20 and offset <= 65^2
        and velocity.X^2 + velocity.Y^2 + velocity.Z^2 <= 12^2 then
        current.lift_quiet = current.lift_quiet or now
        if now - current.lift_quiet >= .15 then
            current.audit[#current.audit + 1] = "settled\t" .. row
            verify_lift_floor(current, point)
            current.angle = current.controller:GetControlRotation()
            current.turn_degrees = clear_turn(current.pawn, current.actor, body, current.grip, current.camera, current.audit)
            current.lift_start, current.start = nil, now
            return true
        end
    else
        current.lift_quiet = nil
    end
    if elapsed >= 2.5 then
        current.audit[#current.audit + 1] = "timeout\t" .. row
        error("the stock Typewriter did not reach the matched raised hold", 0)
    end
    return false
end

local function sample(current, elapsed, progress)
    exact_grip(current)
    local pawn, grip, body = current.pawn, current.grip, current.body
    if progress > 0 then
        local yaw = (current.angle.Yaw + current.turn_degrees * progress + 180) % 360 - 180
        current.controller:SetControlRotation({Pitch = current.angle.Pitch, Yaw = yaw, Roll = current.angle.Roll})
        pawn:K2_SetActorRotation({Pitch = 0, Yaw = yaw, Roll = 0}, true)
        current.camera:K2_SetWorldRotation({Pitch = current.angle.Pitch, Yaw = yaw, Roll = current.angle.Roll}, false, {}, true)
    end
    local point, rotation = body:K2_GetComponentLocation(), body:K2_GetComponentRotation()
    local velocity = body:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
    local target, facing = {}, {}
    grip:GetTargetLocationAndRotation(target, facing)
    local row = {elapsed, progress, point.X, point.Y, point.Z, rotation.Pitch, rotation.Yaw, rotation.Roll,
        velocity.X, velocity.Y, velocity.Z, target.X, target.Y, target.Z, facing.Pitch, facing.Yaw, facing.Roll}
    for index = 1, 17 do
        local value = row[index]
        assert(type(value) == "number" and value == value and math.abs(value) < 1e8, "invalid grip sample")
    end
    assert(#current.rows < 96, "the bounded grip trace is full")
    current.rows[#current.rows + 1] = table.concat(row, "\t")
end

local function finish(ok, result)
    finished = true
    if trace and trace.audit then
        local audit = assert(io.open(lift_output, "wb"))
        audit:write(table.concat(trace.audit, "\n"), "\n")
        audit:close()
    end
    local output = assert(io.open(response, "wb"))
    if ok then
        output:write("ok\t", typewriter, "\t", #result, "\t", trace.turn_degrees, "\n",
            table.concat(result, "\n"), "\n")
    else
        output:write("error\t", tostring(result):gsub("[\t\r\n]", " "), "\n")
    end
    output:close()
    trace = nil
end

LoopInGameThreadWithDelay(25, function()
    if finished then return end
    local now = os.clock()
    if not trace then
        if now < next_request then return end
        next_request = now + .10
        local input = io.open(request, "rb")
        if not input then return end
        local command = input:read(16)
        input:close()
        if command ~= "start\n" then return end
        local ok, result = pcall(start, now)
        if not ok then finish(false, result); return end
        trace = result
    end
    if trace.lift_start then
        local ok, ready = pcall(lift, trace, now)
        if not ok then finish(false, ready); return end
        if not ready then return end
    end
    local elapsed = now - trace.start
    if elapsed - trace.last < .015 then return end
    local progress = math.max(0, math.min(1, (elapsed - stationary_seconds) / turn_seconds))
    local ok, error_message = pcall(sample, trace, elapsed, progress)
    if not ok then finish(false, error_message); return end
    trace.last = elapsed
    if progress == 1 then finish(true, trace.rows) end
end)
