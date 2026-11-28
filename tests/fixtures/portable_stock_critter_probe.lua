    if args[2] == "fixture-stock-critter" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local mode, number = args[3], math.tointeger(args[4])
        local host = seat == "hosting"
        local pawn = assert(state.local_pawn.pawn)
        local mounted = state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
        assert((host or seat == "connected") and (mounted or state.map == "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"))
        if mode == "find" and host then
            local closest, range
            local origin = pawn:K2_GetActorLocation()
            local expected = mounted and args[4]
            assert(not mounted or expected and expected:find("CoopNpcSublevel", 1, true)
                and expected:match(":PersistentLevel%.Critter_Frog_BP_C_1$"))
            for _, entry in pairs(entries) do
                local actor = entry.actor
                if exact_critter(actor) and actor:GetClass():GetFName():ToString() == "Critter_Frog_BP_C"
                    and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded)
                    and (not expected or actor:GetFullName() == "Critter_Frog_BP_C " .. expected) then
                    local point = actor:K2_GetActorLocation()
                    local dist = (point.X-origin.X)^2 + (point.Y-origin.Y)^2 + (point.Z-origin.Z)^2
                    if not range or dist < range then closest, range = entry, dist end
                end
            end
            if mounted and not closest then return "waiting" end
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
        if mode == "native-view" and not host then
            local native = assert(SilverfishUpdateNativeCritterMesh)
            local address, scope = actor:GetAddress(), world:GetAddress()
            local path, level = assert(entry.marker_path), assert(entry.level_address)
            local component, expected = actor.StaticMesh, actor.Mesh_Normal
            local original = component.StaticMesh
            assert(not same(actor.Mesh_Hopping, expected), "the native critter fixture needs distinct frog meshes")
            if actor.CharacterMovement:IsFalling() or not same(original, expected) then return "waiting" end
            assert(native(scope + 1, address, path, level) == nil
                and native(scope, address + 1, path, level) == nil
                and native(scope, address, path .. "_missing", level) == nil
                and native(scope, address, path, level + 1) == nil, "the native critter view accepted a mismatched scope")
            assert(component:SetStaticMesh(actor.Mesh_Hopping), "the critter fixture could not stage the opposite mesh")
            local running = collectgarbage("isrunning")
            local ok, result = pcall(function()
                local handled, changed, native_used = update_view(entry, address, scope, native)
                assert(handled and changed and native_used and same(component.StaticMesh, expected),
                    "the native critter view did not restore the stock idle mesh")
                local stamp = SilverfishBridgeProfileStamp or function() return os.clock() * 1000000 end
                local function measure(reader)
                    local heap, started = collectgarbage("count"), stamp()
                    for _ = 1, 32 do
                        local kept, wrote, used = update_view(entry, address, scope, reader)
                        assert(kept and not wrote and (used == true) == (reader ~= nil))
                    end
                    return stamp() - started, collectgarbage("count") - heap
                end
                collectgarbage("stop")
                local native_one, native_heap_one = measure(native)
                local stock_one, stock_heap_one = measure(nil)
                local stock_two, stock_heap_two = measure(nil)
                local native_two, native_heap_two = measure(native)
                return ("native critter idle verified;64,%.3f,%.3f,%.3f,%.3f"):format(
                    (native_one + native_two) / 1000, (stock_one + stock_two) / 1000,
                    native_heap_one + native_heap_two, stock_heap_one + stock_heap_two)
            end)
            if running then collectgarbage("restart") end
            if not same(component.StaticMesh, original) then component:SetStaticMesh(original) end
            if not ok then error(result, 0) end
            return result
        end
        local point = actor:K2_GetActorLocation()
        local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        local function focus(player, target, remote)
            local aim = math_api:FindLookAtRotation(player.Camera:K2_GetComponentLocation(), target)
            if remote then player.Controller:SetControlRotation(aim) end
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
            local function ground_at(player, x, y, z)
                local hit = {}
                if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=z+120}, {X=x,Y=y,Z=z-300}, 0, true,
                    {actor, pawn, remote}, 0, hit, true, color, color, 0) then
                    local floor = (hit.OutHit or hit).ImpactPoint
                    if floor then return {X=x,Y=y,Z=floor.Z+player.CapsuleComponent:GetScaledCapsuleHalfHeight()+3} end
                end
            end
            if mounted and not entry.fixture_mounted_staged then
                -- Keep the Dock actor in its streaming level and place it on nearby stock Observatory floor.
                local origin = remote:K2_GetActorLocation()
                actor.CharacterMovement:DisableMovement()
                for index = 0, 7 do
                    local angle = index * math.pi / 4
                    local floor = ground_at(actor, origin.X + 180 * math.cos(angle),
                        origin.Y + 180 * math.sin(angle), origin.Z)
                    if floor and actor:K2_SetActorLocation(floor, false, {}, true) then
                        entry.fixture_mounted_staged = true
                        point = actor:K2_GetActorLocation()
                        actor:ForceNetUpdate()
                        break
                    end
                end
                assert(entry.fixture_mounted_staged, "no stock floor for the mounted Dock frog")
            end
            for step = entry.fixture_next_stance or 0, 11 do
                local angle = step * math.pi / 6
                local x, y = point.X + 125 * math.cos(angle), point.Y + 125 * math.sin(angle)
                local target = ground_at(remote, x, y, point.Z)
                local host_target = ground_at(pawn, 2 * x - point.X, 2 * y - point.Y, point.Z)
                if target and host_target and remote:K2_TeleportTo(target, remote:K2_GetActorRotation())
                    and pawn:K2_TeleportTo(host_target, pawn:K2_GetActorRotation()) and focus(remote, point, true) then
                    entry.fixture_next_stance = step + 1
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
            local camera = pawn.Camera
            local original = camera.RelativeRotation
            local restore = {Pitch = original.Pitch, Yaw = original.Yaw, Roll = original.Roll}
            local points = {point}
            for _, component in pairs({actor.CapsuleComponent, actor.StaticMesh}) do
                if valid(component) and same(component:GetOwner(), actor) then
                    points[#points + 1] = component:K2_GetComponentLocation()
                end
            end
            for _, height in ipairs({8, 16, -6}) do
                points[#points + 1] = {X = point.X, Y = point.Y, Z = point.Z + height}
            end
            local completed, result = pcall(function()
                for _, target_point in ipairs(points) do
                    if focus(pawn, target_point) then
                        local path = pawn.InteractionTarget_Component:GetFullName():match("^%S+ (.+)$")
                        assert(critters.focused_target(pawn, path) == "stock-critter:" .. number,
                            "stock Interact resolved the wrong frog")
                        local ok, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                        assert(ok, tostring(reason))
                        return "stock Interact dispatched"
                    end
                end
                local hit, eye = pawn.InteractionTarget_Component, camera:K2_GetComponentLocation()
                return ("stock Interact focus unavailable:can=%s,hit=%s,eye=(%.1f,%.1f,%.1f),frog=(%.1f,%.1f,%.1f)")
                    :format(tostring(pawn.CanInteract), valid(hit) and hit:GetFullName() or "-", eye.X, eye.Y, eye.Z,
                        point.X, point.Y, point.Z)
            end)
            camera:K2_SetRelativeRotation(restore, false, {}, true)
            pawn:LookTrace()
            if not completed then error(result, 0) end
            return result
        end
        error("stock critter probe unavailable", 0)
    end
