local probe = {}
local moving

local function valid(value) return value and value:IsValid() end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function probe.grip(pawn, identity)
    assert(not moving and identity:match("^[a-f0-9]+$") and #identity == 32)
    for slot = 1, 64 do
        local shown, _, id, name = SilverfishReadNativeGuestDropProjection(slot)
        if shown and id == identity then
            local actor = StaticFindObject(name:match("^%S+ (.+)$"))
            assert(valid(actor) and actor:GetFullName() == name and same(actor:GetWorld(), pawn:GetWorld())
                and same(actor.Box, actor.RootComponent) and not valid(pawn.HeldObject))
            local camera = pawn.Camera
            local angle = camera.RelativeRotation
            local old_angle = {Pitch = angle.Pitch, Yaw = angle.Yaw, Roll = angle.Roll}
            local old_actor, old_distance = pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance
            local old_part = pawn.InteractionTarget_Component
            local at, eye = actor:K2_GetActorLocation(), camera:K2_GetComponentLocation()
            local dx, dy, dz = at.X - eye.X, at.Y - eye.Y, at.Z - eye.Z
            local horizontal = math.sqrt(dx * dx + dy * dy)
            local distance = math.sqrt(horizontal * horizontal + dz * dz)
            assert(distance < pawn.InteractionRange, ("fixture distance %.2f"):format(distance))
            local grabbed, error_detail = pcall(function()
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(dz, horizontal)),
                    Yaw = math.deg(math.atan(dy, dx)), Roll = 0}, false, {}, true)
                pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, distance
                pawn.InteractionTarget_Component = actor.Box
                pawn:Grab_Release_Object(false, false, true)
                assert(same(pawn.HeldObject, actor) and same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box),
                    "stock grip did not attach to the shared item")
                assert(actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)),
                    "stock carry input did not activate the projected item")
            end)
            camera:K2_SetRelativeRotation(old_angle, false, {}, true)
            pawn.ActorBeingLookedAt = valid(old_actor) and old_actor or nil
            pawn.ActorLookedAt_Distance = old_distance
            pawn.InteractionTarget_Component = valid(old_part) and old_part or nil
            if not grabbed then
                if same(pawn.HeldObject, actor) then pawn:Grab_Release_Object(false, true, false) end
                error(error_detail)
            end
            moving = {pawn = pawn, actor = actor}
            return tostring(actor:GetAddress())
        end
    end
    error("shared stock drop identity not found")
end

function probe.release(pawn, throwing)
    assert(moving and same(moving.pawn, pawn) and valid(moving.actor))
    local actor = moving.actor
    assert(same(pawn.HeldObject, actor) and actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)))
    local position = actor:K2_GetActorLocation()
    pawn:Grab_Release_Object(throwing, not throwing, false)
    assert(not same(pawn.HeldObject, actor) and not same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box),
        "stock drop did not release the shared item")
    moving = nil
    return ("%.4f,%.4f,%.4f"):format(position.X, position.Y, position.Z)
end

function probe.pose(world, value)
    local actor = StaticFindObject(value)
    assert(valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed())
    local position = actor:K2_GetActorLocation()
    return ("%d,%.4f,%.4f,%.4f"):format(actor:GetAddress(), position.X, position.Y, position.Z)
end

function probe.listen()
    local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
    if not bridge or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then return end
    local previous
    LoopInGameThreadWithDelay(100, function()
        local viewport = FindFirstOf("GameViewportClient")
        local world = valid(viewport) and viewport.World
        if not valid(world) or not valid(world.NetDriver) then return end
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local pawn = api:GetPlayerPawn(world, 0)
        if not valid(pawn) or not pawn:IsLocallyControlled() then return end
        local input = io.open(bridge .. "/world-loot-carry-probe", "rb")
        if not input then return end
        local request = input:read("*l")
        input:close()
        if not request or request == previous then return end
        previous = request
        local mode, value = request:match("^(%a+)|([^|]*)|%x+\r?$")
        local ok, result = pcall(function()
            if mode == "grip" then return probe.grip(pawn, value) end
            if mode == "throw" or mode == "drop" then return probe.release(pawn, mode == "throw") end
            if mode == "pose" then return probe.pose(world, value) end
            error("Unsupported carry probe request")
        end)
        local output = io.open(bridge .. "/world-loot-carry-reply", "wb")
        if output then output:write(ok and "ok|" or "error|", tostring(result)); output:close() end
    end)
end

return probe
