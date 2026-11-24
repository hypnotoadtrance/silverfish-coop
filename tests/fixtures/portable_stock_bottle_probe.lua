    if args[2] == "fixture-stock-bottle" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        local pawn = assert(state.local_pawn.pawn)
        assert(state.map == dock and (phase == "hosting" or phase == "connected"))
        local mode = args[3]
        local function current(actor)
            return valid(actor) and not actor:IsActorBeingDestroyed() and valid(actor:GetWorld())
                and actor:GetWorld():GetAddress() == state.world:GetAddress()
        end
        if mode == "find" and phase == "hosting" then
            local nearest, range
            local origin = pawn:K2_GetActorLocation()
            for _, actor in ipairs(FindAllOf("Prop_Bottle_Booze01_C") or {}) do
                if current(actor) and actor:HasAuthority() and actor.bReplicates == true and not actor.Broken
                    and actor.DestroyIfBroken and actor.DamageOnImpact and valid(actor.PropMesh) then
                    local point = actor:K2_GetActorLocation()
                    local dist = (origin.X - point.X)^2 + (origin.Y - point.Y)^2 + (origin.Z - point.Z)^2
                    if not range or dist < range then nearest, range = actor, dist end
                end
            end
            assert(nearest, "no replicated stock bottle in Tutorial Dock")
            return nearest:GetFullName():match("^%S+ (.+)$")
        end
        local path = assert(args[4])
        assert(path:sub(1, #dock + 1) == dock .. ".")
        local actor = StaticFindObject(path)
        if mode == "inspect" then
            if not current(actor) or actor:GetLocalRole() == 0 then return "absent" end
            return ("%d,%s"):format(actor:GetLocalRole(), tostring(actor.Broken))
        end
        assert(current(actor) and not actor.Broken and valid(actor.PropMesh))
        local point = actor.PropMesh:K2_GetComponentLocation()
        if mode == "approach" and phase == "hosting" then
            local remote
            for _, guest in ipairs(state.guests) do
                if guest.pawn:GetFullName() == args[5] then remote = guest.pawn break end
            end
            assert(valid(remote) and not remote:IsLocallyControlled())
            local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            local color = {R = 0, G = 0, B = 0, A = 0}
            local original, facing = remote:K2_GetActorLocation(), remote.Controller:GetControlRotation()
            for step = 0, 7 do
                local angle = step * math.pi / 4
                local x, y = point.X + 125 * math.cos(angle), point.Y + 125 * math.sin(angle)
                local hit = {}
                if trace:LineTraceSingle(remote, {X=x,Y=y,Z=point.Z + 75}, {X=x,Y=y,Z=point.Z - 200},
                    0, true, {remote, actor}, 0, hit, true, color, color, 0) then
                    local floor = (hit.OutHit or hit).ImpactPoint
                    local target = floor and {X=x,Y=y,Z=floor.Z + remote.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3}
                    if target and remote:K2_TeleportTo(target, facing) then
                        local aim = math_api:FindLookAtRotation(remote.Camera:K2_GetComponentLocation(), point)
                        remote.Controller:SetControlRotation(aim)
                        remote.Camera:K2_SetWorldRotation(aim, false, {}, true)
                        remote:LookTrace()
                        local focused = remote.ActorBeingLookedAt
                        if valid(focused) and focused:GetAddress() == actor:GetAddress() then
                            local position = remote:K2_GetActorLocation()
                            return ("%f,%f,%f"):format(position.X, position.Y, position.Z)
                        end
                    end
                end
            end
            remote:K2_TeleportTo(original, facing)
            error("could not focus the stock bottle from a nearby placement")
        end
        if mode == "aim-down" and phase == "connected" and valid(pawn.HeldObject)
            and pawn.HeldObject:GetAddress() == actor:GetAddress() then
            local aim = pawn.Controller:GetControlRotation()
            aim = {Pitch = -67, Yaw = aim.Yaw, Roll = 0}
            pawn.Controller:SetControlRotation(aim)
            pawn.Camera:K2_SetWorldRotation(aim, false, {}, true)
            return "guest aimed at the floor"
        end
        if mode == "local-break" and phase == "connected" then
            actor:PropBroken(point, pawn)
            return "guest tried local stock break"
        end
        if mode == "impact" and phase == "hosting" and actor:HasAuthority() then
            local total, lethal = {}, {}
            local before = actor.Health
            actor:DealDamage({BluntForce_6_6923B3964740DE281BD773BCCAAC2772 = 500},
                {Location = point, ImpactPoint = point}, pawn, {X = 0, Y = 0, Z = 0}, true, total, lethal)
            if current(actor) and not actor.Broken then
                error(("Stock bottle survived the staged impact: %s health=%s->%s damage=%s lethal=%s"):format(path,
                    tostring(before), tostring(actor.Health), tostring(total.TotalDamage), tostring(lethal.IsLethal)))
            end
            return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
        end
        error("invalid stock bottle fixture")
    end
