local lingering_test

local function lingering_fixture(args, state, phase)
    if args[2] ~= "fixture-lingering-teleport" or not headless
        or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then return end
    assert(state.map == crossroads and same(world, state.world), "stock Travel probe requires Crossroads")
    local action = args[3]
    local host = phase == "hosting"
    local pawn = host and #state.guests == 1 and state.guests[1].pawn
        or not host and state.local_pawn and state.local_pawn.pawn
    assert(in_world(pawn, world), "stock Travel probe needs the enrolled guest")
    local function coords(point)
        return ("%.6f,%.6f,%.6f"):format(point.X, point.Y, point.Z)
    end
    local function nearest()
        local x, y, z = args[4]:match("^([%d.%-]+),([%d.%-]+),([%d.%-]+)$")
        assert(x and y and z, "missing authoritative teleporter point")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local best, distance = nil, 160^2
        for _, row in pairs(active) do
            if row.kind == "lingering-teleport" and in_world(row.actor, world) and replica(row.actor) then
                local at = row.actor:K2_GetActorLocation()
                local squared = (at.X-x)^2 + (at.Y-y)^2 + (at.Z-z)^2
                if squared < distance then best, distance = row.actor, squared end
            end
        end
        return best
    end
    local callback = "BndEvt__Wandering_Teleport_BP_Capsule_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"
    if not host then
        for _, actor in ipairs(FindAllOf("Lingering_Teleport_BP1_C") or {}) do
            assert(not in_world(actor, world) or replica(actor), "An independent Lingering teleporter spawned on the guest")
        end
        local actor = nearest()
        if not valid(actor) then return "waiting" end
        assert(not actor:GetActorEnableCollision() and not actor:IsActorTickEnabled(), "Lingering replica is active")
        if action == "guest-overlap" then
            actor[callback](actor, actor.Capsule, pawn, pawn.CapsuleComponent, 0, false, {})
        else
            assert(action == "inspect")
        end
        return coords(actor:K2_GetActorLocation())
    end
    assert(pawn:HasAuthority() and not pawn:IsLocallyControlled(), "wrong stock Travel test pawn")
    if action == "stage" then
        local selected
        for _, row in pairs(active) do
            if row.travel_source and in_world(row.actor, world) and row.actor:GetActorEnableCollision()
                and #row.actor.Waypoints > 0 then
                local source = StaticFindObject(row.travel_source)
                assert(in_world(source, world) and source.Pool.RowName:ToString() == "Travel" and source.Center_Amomally,
                    "stock Lingering teleporter has no Travel spawner")
                selected = row
                break
            end
        end
        if not selected then return "waiting" end
        local actor = selected.actor
        local at = actor:K2_GetActorLocation()
        local original, rotation = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local color = {R=0, G=0, B=0, A=0}
        for step = 0, 7 do
            local angle = step * math.pi / 4
            local x, y = at.X + 950*math.cos(angle), at.Y + 950*math.sin(angle)
            local floor = {}
            if trace:LineTraceSingle(pawn, {X=x, Y=y, Z=at.Z+300}, {X=x, Y=y, Z=at.Z-600},
                0, false, {pawn, actor}, 0, floor, true, color, color, 0) then
                local hit = floor.OutHit or floor
                local point = {X=x, Y=y, Z=hit.ImpactPoint.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() + 3}
                if hit.ImpactNormal.Z >= .7 and pawn:K2_TeleportTo(point, rotation) then
                    pawn:ForceNetUpdate()
                    lingering_test = {world=world, pawn=pawn, actor=actor,
                        origin={X=original.X, Y=original.Y, Z=original.Z}, angle=rotation}
                    return selected.travel_source .. ";" .. coords(pawn:K2_GetActorLocation())
                end
            end
        end
        error("could not place the guest near a stock Travel spawner")
    end
    assert(lingering_test and same(lingering_test.world, world) and same(lingering_test.pawn, pawn), "no active stock Travel probe")
    if action == "restore" then
        assert(pawn:K2_TeleportTo(lingering_test.origin, lingering_test.angle), "could not restore the test guest")
        pawn:ForceNetUpdate()
        lingering_test = nil
        return "restored"
    end
    local actor = lingering_test.actor
    assert(in_world(actor, world) and actor:HasAuthority() and actor.bReplicateMovement)
    if action == "host-overlap" then
        actor[callback](actor, actor.Capsule, pawn, pawn.CapsuleComponent, 0, false, {})
        pawn:ForceNetUpdate()
        return coords(pawn:K2_GetActorLocation())
    end
    assert(action == "inspect")
    return coords(actor:K2_GetActorLocation())
end
