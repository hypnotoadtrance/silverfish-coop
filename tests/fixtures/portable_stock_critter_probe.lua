    if args[2] == "fixture-stock-critter" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local mode, number = args[3], math.tointeger(args[4])
        local host = seat == "hosting"
        local pawn = assert(state.local_pawn.pawn)
        assert((host or seat == "connected") and state.map == "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P")
        if mode == "find" and host then
            local closest, range
            local origin = pawn:K2_GetActorLocation()
            for _, entry in pairs(entries) do
                local actor = entry.actor
                if exact_critter(actor) and actor:GetClass():GetFName():ToString() == "Critter_Frog_BP_C"
                    and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                    local point = actor:K2_GetActorLocation()
                    local dist = (point.X-origin.X)^2 + (point.Y-origin.Y)^2 + (point.Z-origin.Z)^2
                    if not range or dist < range then closest, range = entry, dist end
                end
            end
            assert(closest, "no enrolled stock Crossroads frog")
            return ("%d;%s"):format(closest.number, closest.actor:GetFullName())
        end
        local entry
        if host then entry = numbered[number]
        else
            for _, candidate in pairs(entries) do
                if candidate.number == number then entry = candidate break end
            end
        end
        local actor = entry and entry.actor
        if mode == "view" then
            if not exact_critter(actor) then return "absent" end
            local mesh = actor.StaticMesh.StaticMesh
            local point = actor:K2_GetActorLocation()
            return ("%d,%s,%s,%f,%f,%f"):format(actor:GetLocalRole(), tostring(actor.CharacterMovement:IsFalling()),
                same(mesh, actor.Mesh_Hopping) and "hop" or same(mesh, actor.Mesh_Normal) and "idle" or "unknown",
                point.X, point.Y, point.Z)
        end
        assert(exact_critter(actor), "the selected frog is not available")
        local point = actor:K2_GetActorLocation()
        local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        local function focus(player)
            local aim = math_api:FindLookAtRotation(player.Camera:K2_GetComponentLocation(), point)
            player.Controller:SetControlRotation(aim)
            player.Camera:K2_SetWorldRotation(aim, false, {}, true)
            player:LookTrace()
            local component = player.InteractionTarget_Component
            return valid(component) and same(component:GetOwner(), actor) and player.CanInteract
        end
        if mode == "stage" and host then
            local remote = assert(state.guests[1]).pawn
            assert(valid(remote))
            local controller = actor.Controller
            if valid(controller) then controller:StopMovement() controller:SetActorTickEnabled(false) end
            local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            local color = {R=0,G=0,B=0,A=0}
            local function ground_at(player, x, y)
                local hit = {}
                if trace:LineTraceSingle(player, {X=x,Y=y,Z=point.Z+120}, {X=x,Y=y,Z=point.Z-300}, 0, true,
                    {actor, pawn, remote}, 0, hit, true, color, color, 0) then
                    local floor = (hit.OutHit or hit).ImpactPoint
                    if floor then return {X=x,Y=y,Z=floor.Z+player.CapsuleComponent:GetScaledCapsuleHalfHeight()+3} end
                end
            end
            for step = 0, 11 do
                local angle = step * math.pi / 6
                local x, y = point.X + 125 * math.cos(angle), point.Y + 125 * math.sin(angle)
                local target = ground_at(remote, x, y)
                local host_target = ground_at(pawn, 2 * x - point.X, 2 * y - point.Y)
                if target and host_target and remote:K2_TeleportTo(target, remote:K2_GetActorRotation())
                    and pawn:K2_TeleportTo(host_target, pawn:K2_GetActorRotation()) and focus(remote) then
                    return ("%f,%f,%f"):format(target.X,target.Y,target.Z)
                end
            end
            error("no clear stock interaction focus on the frog", 0)
        end
        if mode == "hop" and host then
            entry.fixture_origin = actor:K2_GetActorLocation()
            actor.CharacterMovement:StopMovementImmediately()
            actor.StaticMesh:SetStaticMesh(actor.Mesh_Hopping)
            actor:LaunchCharacter({X=0,Y=0,Z=625}, false, true)
            actor:ForceNetUpdate()
            return "stock character hop dispatched"
        end
        if mode == "settle" and host then
            -- Restore the stock point before the separate catch check. Seed-dependent wildlife can
            -- land outside Interact focus on this terrain after its vertical impulse.
            assert(actor:K2_TeleportTo(entry.fixture_origin, actor:K2_GetActorRotation()))
            actor.CharacterMovement:StopMovementImmediately()
            actor.CharacterMovement:SetMovementMode(1, 0)
            actor.StaticMesh:SetStaticMesh(actor.Mesh_Normal)
            actor:ForceNetUpdate()
            return "stock placement restored"
        end
        if mode == "press" and not host then
            assert(focus(pawn), "stock Interact did not focus the frog")
            local target = critters.focused_target(pawn, pawn.InteractionTarget_Component:GetFullName():match("^%S+ (.+)$"))
            assert(target == "stock-critter:" .. number, "stock Interact resolved the wrong frog")
            local ok, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            assert(ok, tostring(reason))
            return "stock Interact dispatched"
        end
        error("stock critter probe unavailable", 0)
    end
