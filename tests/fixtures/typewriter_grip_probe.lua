local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not bridge
    or not bridge:match("[/\\]clients[/\\]placed%-prop%-carry%-trace%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local typewriter = map .. ":PersistentLevel.Prop_TypeWriter_C_3"
local request, response = bridge .. "/prop-grip-trace-request", bridge .. "/prop-grip-trace-reply.tsv"
local stationary_seconds, turn_seconds = .65, 1.0
local trace, finished
local next_request = 0

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
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
        and valid(controller) and valid(camera) and same(camera:GetOwner(), pawn), "the stock Typewriter grip is unavailable")
    return {pawn = pawn, actor = actor, grip = grip, body = body, controller = controller, camera = camera,
        angle = controller:GetControlRotation(), connection = driver.ServerConnection, world = world, driver = driver,
        start = now, last = -1, rows = {}}
end

local function sample(current, elapsed, progress)
    local pawn, actor, grip, body = current.pawn, current.actor, current.grip, current.body
    assert(valid(pawn) and pawn.Dead == false and same(pawn.HeldObject, actor)
        and same(pawn.PhysicsHandle, grip) and same(grip:GetGrabbedComponent(), body)
        and same(actor.RootComponent, body) and same(current.world.NetDriver, current.driver)
        and same(current.driver.ServerConnection, current.connection) and same(pawn:GetWorld(), current.world)
        and same(pawn.Controller, current.controller) and same(pawn.Camera, current.camera), "the exact grip changed during trace")
    if progress > 0 then
        local yaw = (current.angle.Yaw + 70 * progress + 180) % 360 - 180
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
    local output = assert(io.open(response, "wb"))
    if ok then
        output:write("ok\t", typewriter, "\t", #result, "\n", table.concat(result, "\n"), "\n")
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
    local elapsed = now - trace.start
    if elapsed - trace.last < .015 then return end
    local progress = math.max(0, math.min(1, (elapsed - stationary_seconds) / turn_seconds))
    local ok, error_message = pcall(sample, trace, elapsed, progress)
    if not ok then finish(false, error_message); return end
    trace.last = elapsed
    if progress == 1 then finish(true, trace.rows) end
end)
