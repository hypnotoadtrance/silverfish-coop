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
        if mode == "contact-read" and phase == "hosting" then
            local watch = assert(bottle_contact_watch)
            assert(watch.path == path)
            bottle_contact_watch = nil
            local parts = watch.contacts
            if watch.held_hits > 0 then
                parts[#parts + 1] = ("held hits=%d peak impulse=%.1f"):format(watch.held_hits, watch.held_impulse)
            end
            if current(actor) then
                local mesh = actor.PropMesh
                local location = mesh:K2_GetComponentLocation()
                parts[#parts + 1] = ("rest=(%.1f,%.1f,%.1f) health=%s awake=%s"):format(
                    location.X, location.Y, location.Z, tostring(actor.Health), tostring(mesh:IsAnyRigidBodyAwake()))
            end
            return table.concat(parts, ";")
        end
        if mode == "inspect" then
            if not current(actor) or actor:GetLocalRole() == 0 then return "absent" end
            return ("%d,%s"):format(actor:GetLocalRole(), tostring(actor.Broken))
        end
        assert(current(actor) and not actor.Broken and valid(actor.PropMesh))
        local point = actor.PropMesh:K2_GetComponentLocation()
        if mode == "height" and phase == "hosting" then return tostring(point.Z) end
        if mode == "guest-grip" and phase == "connected" then
            local eye, aim = pawn.Camera:K2_GetComponentLocation(), pawn.Camera:GetForwardVector()
            local target, rotation = {}, {}
            pawn.PhysicsHandle:GetTargetLocationAndRotation(target, rotation)
            return ("rootZ=%.1f eyeZ=%.1f aimZ=%.3f targetZ=%.1f held=%s"):format(point.Z, eye.Z, aim.Z, target.Z,
                tostring(valid(pawn.HeldObject) and pawn.HeldObject:GetAddress() == actor:GetAddress()))
        end
        if mode == "contact-watch" and phase == "hosting" and actor:HasAuthority() then
            bottle_contact_watch = {path = path, address = actor:GetAddress(), contacts = {}, held_hits = 0, held_impulse = 0}
            if not bottle_contact_hook then
                bottle_contact_hook = RegisterHook("/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C:" ..
                    "BndEvt__PhysicsProp_Master_BP_PropMesh_K2Node_ComponentBoundEvent_0_ComponentHitSignature__DelegateSignature",
                    function(context, _, other, _, impulse)
                        local watch = bottle_contact_watch
                        local current_actor = watch and context:get()
                        if not watch or not valid(current_actor) or current_actor:GetAddress() ~= watch.address then return end
                        local value, surface = impulse:get(), other:get()
                        local strength = math.sqrt(value.X^2 + value.Y^2 + value.Z^2)
                        if not current(current_actor) then
                            watch.contacts[#watch.contacts + 1] = ("retired on impulse=%.1f"):format(strength)
                            return
                        end
                        if current_actor.BeingHeld then
                            watch.held_hits = watch.held_hits + 1
                            watch.held_impulse = math.max(watch.held_impulse, strength)
                            return
                        end
                        if #watch.contacts >= 8 then return end
                        local position = current_actor.PropMesh:K2_GetComponentLocation()
                        local speed = current_actor.PropMesh:GetPhysicsLinearVelocity(FName("None", EFindName.FNAME_Find))
                        watch.contacts[#watch.contacts + 1] = ("%s impulse=%.1f vz=%.1f at=(%.1f,%.1f,%.1f) health=%s held=%s"):format(
                            valid(surface) and surface:GetFName():ToString() or "none", strength,
                            speed.Z, position.X, position.Y, position.Z, tostring(current_actor.Health), tostring(current_actor.BeingHeld))
                    end)
            end
            return "armed"
        end
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
        if (mode == "lift" or mode == "throw-down") and phase == "connected" and valid(pawn.HeldObject)
            and pawn.HeldObject:GetAddress() == actor:GetAddress() then
            local aim = pawn.Controller:GetControlRotation()
            aim = {Pitch = mode == "lift" and 35 or -67, Yaw = aim.Yaw, Roll = 0}
            pawn.Controller:SetControlRotation(aim)
            pawn.Camera:K2_SetWorldRotation(aim, false, {}, true)
            if mode == "lift" then return "guest lifted the bottle" end
            pawn:Grab_Release_Object(true, false, false)
            assert(not valid(pawn.HeldObject), "stock throw did not release the bottle")
            return "guest threw at the floor"
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
