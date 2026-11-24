local acid_mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local acid_area = "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P"
local acid_id, acid_control = "AcidPool_BP_C_1", "AcidPool_BP_C_9"
local acid_member = "Acid_20_F4C551B34F620FDF72D2F2A64DE15F3E"
local acid_pawn = "/Game/Classes/Player/Player_FP.Player_FP_C"
local acid_damage = "/Game/Classes/Actors/StatusEffect_Actors/DotVolume.DotVolume_C:DamageActor"
local acid_ticket, acid_trial, acid_hook

local function acid_exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function acid_summary(entry)
    local actor = entry.actor
    if entry.ended or not live(actor) then return "gone,0,1,0" end
    return table.concat({entry.retired and "gone" or stock_phase(actor), actor.DotVolume.DotActive and 1 or 0,
        actor.bHidden and 1 or 0, actor:GetActorEnableCollision() and 1 or 0}, ",")
end

local function acid_capture()
    if acid_hook then return end
    RegisterHook(acid_damage, function(context, victim)
        local trial = acid_trial
        if not trial or not same(context:get(), trial.dot) then return end
        local pawn = victim:get()
        if same(pawn, trial.victim) then trial.hits = trial.hits + 1 end
        if same(pawn, trial.host) then trial.host_hits = trial.host_hits + 1 end
    end)
    acid_hook = true
end

local function enter_acid(current, actor, victim, other)
    local shape = StaticFindObject("/Script/Engine.SphereComponent")
    local capsule = victim.CapsuleComponent
    local spheres, height = actor:K2_GetComponentsByClass(shape), capsule:GetScaledCapsuleHalfHeight()
    assert(#spheres > 0 and #spheres <= 32 and height >= 35 and height <= 110
        and same(capsule:GetOwner(), victim), "no qualified stock pool sphere or pawn capsule")
    local attempts = {}
    for index = 1, #spheres do
        local sphere = spheres[index]
        if type(sphere) == "userdata" and type(sphere.type) == "function" and sphere:type() == "RemoteUnrealParam" then
            sphere = sphere:get()
        end
        if acid_exact(sphere, "/Script/Engine.SphereComponent") and same(sphere:GetOwner(), actor)
            and sphere:GetGenerateOverlapEvents() then
            local center, radius = sphere:K2_GetComponentLocation(), sphere:GetScaledSphereRadius()
            if radius >= 10 and radius <= 500 then
                attempts[#attempts + 1] = {sphere = sphere, center = {X = center.X, Y = center.Y, Z = center.Z}, radius = radius}
            end
        end
    end
    for _, trial in ipairs(attempts) do
        local point = trial.center
        for _, rise in ipairs({height - 15, height + 10, 0}) do
            if victim:K2_TeleportTo({X = point.X, Y = point.Y, Z = point.Z + rise}, acid_trial.rotation)
                and trial.sphere:IsOverlappingActor(victim) and actor:IsOverlappingActor(victim)
                and not actor:IsOverlappingActor(other) then
                local isolated = true
                for _, id in ipairs(current.ids) do
                    local other_pool = current.entries[id].actor
                    if id ~= acid_id and live(other_pool) and other_pool:IsOverlappingActor(victim) then isolated = false end
                end
                if isolated then
                    victim:ForceNetUpdate()
                    return ("%.3f,%.3f,%.3f,%.3f"):format(point.X, point.Y, point.Z, trial.radius)
                end
            end
        end
    end
    assert(victim:K2_TeleportTo(acid_trial.origin, acid_trial.rotation), "could not restore the private test pawn")
    error("No isolated stock pool overlap accepted the authoritative guest", 0)
end

local function acid_qualification(state, phase, current)
    local function read(get)
        local ok, result = pcall(get)
        return ok and tostring(result) or "unavailable"
    end
    local native = type(SilverfishNativeAuthoredAcidPool) == "function"
    local actors = FindAllOf("AcidPool_BP_C") or {}
    local rows = {("phase=%s;native=%s;found=%d;accepted=%s;complete=%s"):format(
        phase, tostring(native), #actors, tostring(current and #current.ids), tostring(current and current.complete))}
    local level = state.world.PersistentLevel
    for _, id in ipairs({acid_id, acid_control}) do
        local expected = acid_area .. ".Lab_EastWing_P:PersistentLevel." .. id
        local actor = StaticFindObject(expected)
        local found = valid(actor)
        if not found then
            for index = 1, math.min(64, #actors) do
                local candidate = actors[index]
                if live(candidate) and same(candidate:GetWorld(), state.world) and candidate:GetFName():ToString() == id then
                    actor = candidate
                    break
                end
            end
        end
        local fields = {id, "static=" .. tostring(found), "object=" .. tostring(live(actor))}
        local function record(name, getter)
            fields[#fields + 1] = name .. "=" .. read(getter)
        end
        if live(actor) then
            local full = actor:GetFullName()
            local dot = actor.DotVolume
            record("path", function() return full end)
            record("same_world", function() return same(actor:GetWorld(), state.world) end)
            record("level_outer", function() return same(actor:GetOuter(), level) end)
            record("was_loaded", function() return actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) end)
            record("role", function() return actor:GetLocalRole() end)
            record("authority", function() return actor:HasAuthority() end)
            record("replicates", function() return actor.bReplicates end)
            record("dot", function() return valid(dot) and dot:GetClass():GetFullName() end)
            if valid(dot) then
                record("dot_outer", function() return dot:GetOuter():GetFullName() end)
                record("dot_outer_is_pool", function() return same(dot:GetOuter(), actor) end)
                record("dot_owner_is_pool", function() return same(dot:GetOwner(), actor) end)
                record("dot_active", function() return dot.DotActive end)
            end
            local path = full:match("^AcidPool_BP_C (.+)$")
            if native and path then
                record("native_result", function()
                    local qualified, guest = SilverfishNativeAuthoredAcidPool(path, state.world:GetAddress())
                    return tostring(qualified) .. "/" .. tostring(guest)
                end)
            end
        end
        rows[#rows + 1] = table.concat(fields, ";")
    end
    return table.concat(rows, "|")
end

local function acid_fixture(command, state, phase, current)
    if command == "qualify" then return acid_qualification(state, phase, current) end
    assert(current and current.complete, "the authored pool census is unavailable")
    local entry, sibling = current.entries[acid_id], current.entries[acid_control]
    assert(entry and sibling and entry.path == acid_area .. ".Lab_EastWing_P:PersistentLevel." .. acid_id,
        "the qualified Lab East Wing pool and its control are not present")
    local actor, host = entry.actor, state.local_pawn and state.local_pawn.pawn
    assert(acid_exact(host, acid_pawn) and host:IsLocallyControlled() and same(host:GetWorld(), state.world)
        and host:HasAuthority() ~= current.guest and valid(host.Save), "the private local pawn is not qualified")
    if command == "read" then return acid_summary(entry) .. ";" .. acid_summary(sibling) end
    assert(live(actor), "the selected stock pool has already retired")
    local dot = actor.DotVolume
    assert(acid_exact(dot, "/Game/Classes/Actors/StatusEffect_Actors/DotVolume.DotVolume_C")
        and same(dot:GetOwner(), actor), "the exact pool's stock DoT is unavailable")
    if command == "guest-extinguish" then
        assert(current.guest and dot.DotActive == false and stock_phase(actor) == "a")
        actor:Extinguish(1.0)
        assert(stock_phase(actor) == "a" and dot.DotActive == false, "guest changed the stock pool independently")
        return "blocked"
    end
    local victim = not current.guest and #state.guests == 1 and state.guests[1].pawn
    assert(acid_exact(victim, acid_pawn) and victim:HasAuthority() and not victim:IsLocallyControlled()
        and same(victim:GetWorld(), state.world) and valid(victim.Save) and not same(victim.Save, host.Save),
        "the exact listen host guest and detached save are unavailable")
    if command == "begin" then
        assert(not acid_trial and stock_phase(actor) == "a" and dot.DotActive == true
            and dot.BaseDamage[acid_member] > 0 and not host.Dead and not victim.Dead
            and host.Save.PlayerHealth > 30 and victim.Save.PlayerHealth > 30
            and not actor:IsOverlappingActor(host) and not actor:IsOverlappingActor(victim), "stock acid overlap not ready")
        acid_capture()
        acid_trial = {world = state.world, host = host, victim = victim, dot = dot, save = victim.Save,
            origin = victim:K2_GetActorLocation(), rotation = victim:K2_GetActorRotation(),
            before = victim.Save.PlayerHealth, host_before = host.Save.PlayerHealth,
            hits = 0, host_hits = 0}
        local ok, result = pcall(enter_acid, current, actor, victim, host)
        if not ok then acid_trial = nil; error(result, 0) end
        return result
    end
    if command == "fizzle" then
        assert(not acid_trial and stock_phase(actor) == "a" and not actor:IsOverlappingActor(victim),
            "stock fizzle requested before leaving the damage volume")
        actor:FizzleOut()
        assert(stock_phase(actor) == "f", "stock acid fizzle timeline did not start")
        return "f"
    end
    local trial = acid_trial
    assert(trial and same(trial.world, state.world) and same(trial.victim, victim) and same(trial.save, victim.Save),
        "the exact stock acid damage trial changed")
    if command == "return" then
        assert(victim:K2_TeleportTo(trial.origin, trial.rotation) and not actor:IsOverlappingActor(victim),
            "could not leave the selected stock pool")
        victim:ForceNetUpdate()
        acid_trial = nil
    else
        assert(command == "damage", "invalid pool probe operation")
    end
    return ("%d,%d,%.6f,%.6f,%.6f,%.6f"):format(trial.hits, trial.host_hits, trial.before, victim.Save.PlayerHealth,
        trial.host_before, host.Save.PlayerHealth)
end

local function poll_acid_fixture(state, phase, current)
    if state.map ~= acid_area or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
        or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" or not acid_mailbox
        or not acid_mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then
        return
    end
    local prefix = acid_mailbox .. "/portable-acid-pool"
    local input = io.open(prefix .. "-command.tsv", "rb")
    if not input then return end
    local request = input:read(256)
    input:close()
    local ticket, command
    if request then ticket, command = request:match("^([0-9a-f]+)\t([a-z%-]+)$") end
    if not ticket or #ticket ~= 32 or ticket == acid_ticket then return end
    acid_ticket = ticket
    local ok, response = pcall(acid_fixture, command, state, phase, current)
    local output = assert(io.open(prefix .. "-reply.tsv", "wb"))
    output:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(response):gsub("[\r\n\t]", " ")))
    output:close()
end
