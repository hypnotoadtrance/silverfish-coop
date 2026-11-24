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
        local function near(candidate, coordinates)
            local x, y, z = coordinates:match("^([^,]+),([^,]+),([^,]+)$")
            local point = candidate:K2_GetActorLocation()
            return (point.X-tonumber(x))^2 + (point.Y-tonumber(y))^2 + (point.Z-tonumber(z))^2 < 40^2
        end
        local function floor_under(point, ignored)
            local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            local color, hit = {R=0,G=0,B=0,A=0}, {}
            if trace:LineTraceSingle(pawn, point, {X=point.X,Y=point.Y,Z=point.Z-300},
                0, true, ignored, 0, hit, true, color, color, 0) then
                local result = hit.OutHit or hit
                if result.ImpactNormal.Z > .9 then return result.ImpactPoint end
            end
        end
        local function clear_stock_floor(origin)
            for step = 0, 7 do
                local angle = step * math.pi / 4
                local floor = floor_under({X=origin.X+600*math.cos(angle),Y=origin.Y+600*math.sin(angle),Z=origin.Z+60}, {pawn})
                if floor then
                    local clear = true
                    for _, guest in ipairs(state.guests) do
                        local remote = guest.pawn:K2_GetActorLocation()
                        if (remote.X-floor.X)^2 + (remote.Y-floor.Y)^2 < 400^2 then clear = false end
                    end
                    if clear then return {X=floor.X,Y=floor.Y,Z=floor.Z+24} end
                end
            end
        end
        if mode == "wood-pieces" then
            local x, y, z = args[4]:match("^([^,]+),([^,]+),([^,]+)$")
            local rows = {}
            for _, class in ipairs({"Prop_ScrapWood_C", "Prop_ScrapWood_Post_C"}) do
                for _, part in ipairs(FindAllOf(class) or {}) do
                    if current(part) then
                        local point = part:K2_GetActorLocation()
                        if (point.X-tonumber(x))^2 + (point.Y-tonumber(y))^2 + (point.Z-tonumber(z))^2 <= 400^2 then
                            local mesh = part.PropMesh.StaticMesh
                            rows[#rows + 1] = ("%s,%s,%s,%d,%d,%.3f,%.3f,%.3f"):format(part:GetFName():ToString(),
                                class, valid(mesh) and mesh:GetFName():ToString() or "-", part:GetLocalRole(),
                                part.Random and 1 or 0, point.X, point.Y, point.Z)
                        end
                    end
                end
            end
            return table.concat(rows, ";")
        end
        if mode == "wood-nudge" or mode == "wood-remove" then
            assert(phase == "hosting" and args[4]:match("^[%w_]+$"))
            local part = StaticFindObject(dock .. ".Tut_Dock_P:PersistentLevel." .. args[4])
            local class = valid(part) and part:GetClass():GetFName():ToString()
            assert(current(part) and part:HasAuthority() and (class == "Prop_ScrapWood_C" or class == "Prop_ScrapWood_Post_C"))
            local mount = part:GetParentComponent()
            local wrapper = valid(mount) and mount:GetOwner()
            assert(current(wrapper) and wrapper:GetClass():GetFName():ToString():match("Gib_Wood_01_BP[1]?_C$"))
            if mode == "wood-remove" then
                wrapper:K2_DestroyActor()
                return "removed"
            end
            local point, angles = part:K2_GetActorLocation(), part:K2_GetActorRotation()
            assert(part.PropMesh:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)))
            assert(part:K2_SetActorLocationAndRotation({X=point.X+110,Y=point.Y+80,Z=point.Z+45}, angles, false, {}, true))
            part:ForceNetUpdate()
            return "moved"
        end
        if mode == "pool-near" or mode == "pool-remove" then
            local found
            for _, candidate in ipairs(FindAllOf("AcidPool_BP_C") or {}) do
                if current(candidate) and near(candidate, assert(args[4])) then
                    assert(not found, "two stock acid pools appeared at the same impact")
                    found = candidate
                end
            end
            if not found then return "absent" end
            if mode == "pool-remove" then
                assert(phase == "hosting" and found:HasAuthority() and found.FizzledOut)
                found:K2_DestroyActor()
                return "removed"
            end
            local shape = StaticFindObject("/Script/Engine.SphereComponent")
            local point = found:K2_GetActorLocation()
            local status = found.FizzledOut and "extinguished" or found.FizzleOut_Mat:IsPlaying() and "fizzling" or "active"
            return ("%s;%.3f,%.3f,%.3f;%.3f;%.3f;%d;%s;%s;%s"):format(status,
                point.X, point.Y, point.Z, found.SizeScaler, found.BurnTime, #found:K2_GetComponentsByClass(shape),
                tostring(found.DotVolume.DotActive), tostring(found:GetActorEnableCollision()), tostring(found.bHidden))
        end
        if mode == "spawn-prop" or mode == "locate-prop" then
            local stem = assert(args[4]:match("^(Prop_[%w_]+)$"))
            local base = "/Game/Assets/StaticMeshes/Mesh_Props/" .. stem .. "." .. stem .. "_C"
            local location = pawn:K2_GetActorLocation()
            if mode == "spawn-prop" and phase == "hosting" then
                local class = StaticFindObject(base)
                if not valid(class) then LoadAsset(base); class = StaticFindObject(base) end
                assert(valid(class), "could not load the stock " .. stem .. " class")
                local point = {X=location.X+105, Y=location.Y+60, Z=location.Z+90}
                local simulate = args[5] == "floor-physics"
                if stem == "Prop_AcidBottle" or simulate then
                    point = assert(clear_stock_floor(location), "no stock floor far enough from both pawns")
                end
                local candidate = state.world:SpawnActor(class, point, {Pitch=0,Yaw=0,Roll=0})
                assert(current(candidate), "could not spawn " .. stem .. " in the warehouse")
                assert(candidate:HasAuthority() and candidate.DestroyIfBroken and valid(candidate.PropMesh),
                    "stock " .. stem .. " has no authoritative breakable mesh")
                -- Authored and crate props are enrolled by the corresponding gameplay path.
                candidate:SetReplicates(true)
                candidate:SetReplicateMovement(true)
                if not simulate then candidate.PropMesh:SetSimulatePhysics(false) end
                candidate:ForceNetUpdate()
                point = candidate:K2_GetActorLocation()
                return ("%s;%.3f,%.3f,%.3f"):format(candidate:GetFullName():match("^%S+ (.+)$"), point.X, point.Y, point.Z)
            end
            assert(phase == "connected")
            local found
            for _, candidate in ipairs(FindAllOf(stem .. "_C") or {}) do
                if current(candidate) and candidate:GetLocalRole() == 1 and not candidate.Broken and near(candidate, args[5]) then
                    assert(not found, "ambiguous replicated stock prop")
                    found = candidate
                end
            end
            return found and found:GetFullName():match("^%S+ (.+)$") or "waiting"
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
            if current(actor) and not actor.Broken and valid(actor.PropMesh) then
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
            local function ground(x, y, z)
                local hit = {}
                if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=z+75}, {X=x,Y=y,Z=z-200},
                    0, true, {pawn, remote, actor}, 0, hit, true, color, color, 0) then
                    local floor = hit.OutHit or hit
                    if floor.ImpactPoint and floor.ImpactNormal.Z > .9 then return floor.ImpactPoint end
                end
            end
            local host_position = pawn:K2_GetActorLocation()
            local lane
            -- The authored shelf can pin the bottle against its lower mesh. Keep the same
            -- stock actor and test its normal pickup and throw on clear warehouse floor.
            for step = 0, 7 do
                local angle = step * math.pi / 4
                local dx, dy = math.cos(angle), math.sin(angle)
                local near_floor = ground(host_position.X + 180*dx, host_position.Y + 180*dy, host_position.Z)
                local far_floor = ground(host_position.X + 305*dx, host_position.Y + 305*dy, host_position.Z)
                if near_floor and far_floor and math.abs(near_floor.Z - far_floor.Z) < 5 then
                    local from = {X=near_floor.X, Y=near_floor.Y, Z=near_floor.Z+60}
                    local to = {X=far_floor.X, Y=far_floor.Y, Z=far_floor.Z+60}
                    if not trace:LineTraceSingle(pawn, from, to, 0, true, {pawn, remote, actor}, 0, {}, true, color, color, 0) then
                        lane = far_floor
                        break
                    end
                end
            end
            assert(lane and actor:K2_SetActorLocation({X=lane.X,Y=lane.Y,Z=lane.Z+24}, false, {}, true),
                "could not place the stock bottle on clear warehouse floor")
            actor:ForceNetUpdate()
            point = actor.PropMesh:K2_GetComponentLocation()
            local original, facing = remote:K2_GetActorLocation(), remote.Controller:GetControlRotation()
            for step = 0, 7 do
                local angle = step * math.pi / 4
                local x, y = point.X + 125 * math.cos(angle), point.Y + 125 * math.sin(angle)
                local floor = ground(x, y, point.Z)
                if floor then
                    local target = {X=x,Y=y,Z=floor.Z + remote.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3}
                    if remote:K2_TeleportTo(target, facing) then
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
        if (mode == "impact" or mode == "floor-impact") and phase == "hosting" and actor:HasAuthority() then
            if mode == "floor-impact" then
                point = assert(floor_under(point, {pawn, actor}), "could not locate warehouse floor")
            end
            local total, lethal = {}, {}
            local before = actor.Health
            actor:DealDamage({BluntForce_6_6923B3964740DE281BD773BCCAAC2772 = 800},
                {Location = point, ImpactPoint = point}, pawn, {X = 0, Y = 0, Z = 0}, true, total, lethal)
            if current(actor) and not actor.Broken then
                error(("Stock prop survived the staged impact: %s health=%s->%s damage=%s lethal=%s"):format(path,
                    tostring(before), tostring(actor.Health), tostring(total.TotalDamage), tostring(lethal.IsLethal)))
            end
            return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
        end
        error("invalid stock bottle fixture")
    end
