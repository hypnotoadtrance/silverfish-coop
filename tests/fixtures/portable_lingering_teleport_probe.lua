local lingering_test, lingering_ticket, lingering_replica_path
local lingering_mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local lingering_class = "Lingering_Teleport_BP1_C"
local travel_spawner = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Artifact_Spawner_BP_C_29"

local function lingering_fixture(action, point, state, phase)
    assert(state.map == crossroads and same(world, state.world), "stock Travel probe requires Crossroads")
    local host = phase == "hosting"
    local pawn = host and #state.guests == 1 and state.guests[1].pawn
        or not host and state.local_pawn and state.local_pawn.pawn
    assert(in_world(pawn, world), "stock Travel probe needs the enrolled guest")
    local function coords(point)
        return ("%.6f,%.6f,%.6f"):format(point.X, point.Y, point.Z)
    end
    local function local_teleporters()
        local count = 0
        for _, actor in ipairs(FindAllOf(lingering_class) or {}) do
            if in_world(actor, world) and not replica(actor) then count = count + 1 end
        end
        return count
    end
    if action == "prepare" or action == "guest-spawn-attempt" then
        assert(host == (action == "prepare"), "wrong seat for stock Travel spawning")
        if not host then lingering_replica_path = nil end
        local before = local_teleporters()
        if host and before > 0 then return "natural" end
        local source = StaticFindObject(travel_spawner)
        assert(in_world(source, world) and source.Pool.RowName:ToString() == "Travel" and source.Center_Amomally,
            "authored Crossroads Travel spawner unavailable")
        source:SpawnAnomalies()
        if host then
            assert(local_teleporters() == before + 1, "The authored Travel spawner did not produce a stock Lingering teleporter")
            return "stock-spawner"
        end
        assert(before == 0 and local_teleporters() == 0, "An independent Lingering teleporter spawned on the guest")
        return "guest-spawn-suppressed"
    end
    local function nearest()
        local x, y, z = point:match("^([%d.%-]+),([%d.%-]+),([%d.%-]+)$")
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
        assert(local_teleporters() == 0, "An independent Lingering teleporter spawned on the guest")
        local actor
        if action == "guest-overlap" then
            actor = lingering_replica_path and StaticFindObject(lingering_replica_path)
            local entry = in_world(actor, world) and active[actor:GetAddress()]
            assert(entry and entry.kind == "lingering-teleport" and same(entry.actor, actor) and replica(actor),
                "The stock teleporter matched during motion is no longer a guest replica")
        else
            assert(action == "inspect")
            actor = nearest()
            if valid(actor) then lingering_replica_path = actor:GetFullName():match("^%S+ (.+)$") end
        end
        if not valid(actor) then return "waiting" end
        assert(not actor:GetActorEnableCollision() and not actor:IsActorTickEnabled(), "Lingering replica is active")
        if action == "guest-overlap" then
            actor[callback](actor, actor.Capsule, pawn, pawn.CapsuleComponent, 0, false, {})
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
        -- Leave room for the stock actor to wander during the motion sample.
        local staging_radius = 2200
        for step = 0, 7 do
            local angle = step * math.pi / 4
            local x, y = at.X + staging_radius * math.cos(angle), at.Y + staging_radius * math.sin(angle)
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

local function poll_lingering_fixture(state, phase)
    if not headless or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" or not lingering_mailbox
        or not lingering_mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
        or state.map ~= crossroads or phase ~= "hosting" and phase ~= "connected" then return end
    local prefix = lingering_mailbox .. "/portable-lingering-"
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local content = request:read(200)
    request:close()
    if not content then return end
    local ticket, action, point = content:match("^(%x+)\t([a-z%-]+)\t([^\r\n\t]*)$")
    if not ticket or #ticket ~= 32 or ticket == lingering_ticket then return end
    lingering_ticket = ticket
    local ok, response = pcall(lingering_fixture, action, point, state, phase)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    output:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(response):gsub("[\r\n\t]", " ")))
    output:close()
end
