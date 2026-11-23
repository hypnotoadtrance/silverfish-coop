local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("weather%-proof%-%x+") then return end

local function valid(object)
    return object and object:IsValid()
end
local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end
local function find(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path) object = StaticFindObject(path) end
    return object
end
local function spool(name, value)
    local target = assert(io.open(box .. "/" .. name, "ab"))
    target:write(value, "\n")
    target:close()
end

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local fog_class = "/Game/Classes/Weather/Storms/TheFog_BP.TheFog_BP_C"
local fog_cue = "/Game/Assets/Sounds/CUEs/Alarm_Cues/StormSiren_03_Cue.StormSiren_03_Cue"
local rain, installed, previous, fog_demo
local fog_walkers = os.getenv("SILVERFISH_COOP_FOG_WALKERS_PROBE") == "1"
local spawn_call = "/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnSquadFromData"
local marker_class = "BlueprintGeneratedClass /Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C"
local walker_class = "BlueprintGeneratedClass /Game/Classes/NPCs/NPC_FogWalker.NPC_FogWalker_C"
local walkers_asset = "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/FogWalkers.FogWalkers"
local fog_watch, fog_armed, fog_marker

local function live_in(actor, world)
    return valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
end

local function walker_row(actor, world)
    if not live_in(actor, world) or actor:GetClass():GetFullName() ~= walker_class then return end
    local point = actor:K2_GetActorLocation()
    return ("%s|%d|%s|%s|%s|%.3f|%.3f|%.3f|%d|%s|%s|%s|%s|%s|%.3f|%.3f|%.3f"):format(
        actor:GetFullName(), actor:GetLocalRole(), tostring(actor:HasAuthority()), tostring(actor.bReplicates),
        tostring(actor.Alive), point.X, point.Y, point.Z, actor:GetRemoteRole(), tostring(actor.NetDormancy),
        tostring(actor.bReplicateMovement), tostring(actor.bAlwaysRelevant), tostring(actor.bOnlyRelevantToOwner),
        tostring(actor.bNetUseOwnerRelevancy), actor.NetCullDistanceSquared, actor.NetUpdateFrequency,
        actor.MinNetUpdateFrequency)
end

local function observe_fog_call(world)
    if fog_watch then return end
    assert(valid(StaticFindObject(spawn_call)), "The Crossroads stock squad callback is unavailable")
    fog_watch = true
    RegisterHook(spawn_call, function(context, data, _, count)
        local marker, squad = context:get(), data:get()
        if not valid(squad) or squad:GetFullName() ~= walkers_asset or not live_in(marker, world)
            or marker:GetClass():GetFullName() ~= marker_class then return end
        local total = type(count) == "number" and count or count and count:get()
        local exact = fog_armed and fog_armed[marker:GetFullName()] == true
        if exact then fog_marker = marker end
        spool("fog-walkers-calls", ("%s|%s|%s|%s|%s"):format(marker:GetFullName(), squad:GetFullName(),
            tostring(total), tostring(marker:HasAuthority()), tostring(exact == true)))
        local seen = {}
        local function observe(actor)
            local data = walker_row(actor, world)
            if data and not seen[data] then
                seen[data] = true
                spool("fog-walkers-members", data)
            end
        end
        observe(marker.SquadLeader_ref)
        local members = marker.SquadMembers_ref
        for index = 1, math.min(members and members:GetArrayNum() or 0, 16) do observe(members[index]) end
    end)
end

local function farther_markers(world, pawn)
    local origin, farthest, selected = pawn:K2_GetActorLocation(), -1, {}
    for _, actor in ipairs(FindAllOf("AI_Spawner_Squad_BP_C") or {}) do
        if live_in(actor, world) and actor:GetClass():GetFullName() == marker_class then
            local point = actor:K2_GetActorLocation()
            local distance = (point.X - origin.X)^2 + (point.Y - origin.Y)^2 + (point.Z - origin.Z)^2
            if distance > farthest then farthest, selected = distance, {} end
            if distance == farthest then selected[actor:GetFullName()] = true end
        end
    end
    assert(next(selected), "The stock fog has no authored squad marker candidate")
    return selected
end

local function report_fog_roster(command, world, pawn)
    observe_fog_call(world)
    local stock_storms = 0
    for _, actor in ipairs(FindAllOf("TheFog_BP_C") or {}) do
        if live_in(actor, world) and actor:GetClass():GetFullName() == "BlueprintGeneratedClass " .. fog_class then
            stock_storms = stock_storms + 1
        end
    end
    local roster = {}
    for _, actor in ipairs(FindAllOf("NPC_FogWalker_C") or {}) do
        local info = walker_row(actor, world)
        if info then roster[#roster + 1] = info end
        assert(#roster <= 32, "The stock FogWalker diagnostic roster exceeds the fixture bound")
    end
    table.sort(roster)
    for _, info in ipairs(roster) do spool("fog-walkers-roster", command .. "|" .. info) end
    local point = pawn:K2_GetActorLocation()
    return ("%d|%d|%.3f|%.3f|%.3f"):format(stock_storms, #roster, point.X, point.Y, point.Z)
end

local function distance(a, b)
    return math.sqrt((a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2)
end

-- Same bounded world-static floor and capsule check as the stock campaign NPC pair fixture.
local function safe_pair_floor(trace, pawn, anchor, x, y, ignored)
    local capsule = pawn.CapsuleComponent
    local height = valid(capsule) and same(capsule:GetOwner(), pawn) and capsule:GetScaledCapsuleHalfHeight()
    if not height or height < 20 or height > 120 then return end
    local top, bottom = anchor.Z + 420, anchor.Z - 450
    local out, color = {}, {R = 0, G = 0, B = 0, A = 0}
    local hit = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom},
        0, false, ignored, 0, out, true, color, color, 0)
    out = out.OutHit or out
    local component = hit and out.Component and out.Component:Get()
    if valid(component) and component:GetCollisionObjectType() == 0 and out.ImpactNormal.Z >= .75
        and out.ImpactPoint.Z < top - 1 and out.ImpactPoint.Z >= bottom then
        return {X = x, Y = y, Z = out.ImpactPoint.Z + height + 3}
    end
end

local function approach_fog(world, driver, host)
    assert(fog_demo and live_in(fog_demo.storm, world) and live_in(fog_marker, world)
        and fog_marker:HasAuthority(), "The exact stock fog marker is unavailable")
    local connections = driver.ClientConnections
    assert(connections:GetArrayNum() == 1, "The fog fixture requires one authenticated guest")
    local connection = connections[1]
    local controller = valid(connection) and connection.PlayerController
    local guest = valid(controller) and controller.Pawn
    assert(live_in(host, world) and host:IsLocallyControlled() and live_in(guest, world)
        and guest:HasAuthority() and not guest:IsLocallyControlled(), "The exact host/guest pawns are unavailable")
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    assert(valid(trace))
    local anchor = fog_marker:K2_GetActorLocation()
    local host_before, guest_before = host:K2_GetActorLocation(), guest:K2_GetActorLocation()
    local host_facing, guest_facing = host:K2_GetActorRotation(), guest:K2_GetActorRotation()
    local ignored = {host, guest, fog_marker}
    for _, radius in ipairs({2800, 2200, 3400}) do
        for step = 0, 11 do
            local angle = step * math.pi / 6
            local x, y = anchor.X + math.cos(angle) * radius, anchor.Y + math.sin(angle) * radius
            local dx, dy = -math.sin(angle) * 150, math.cos(angle) * 150
            local first = safe_pair_floor(trace, host, anchor, x + dx, y + dy, ignored)
            local second = safe_pair_floor(trace, guest, anchor, x - dx, y - dy, ignored)
            if first and second and math.abs(first.Z - second.Z) <= 120 then
                local moved_host = host:K2_TeleportTo(first, host_facing) == true
                local moved_guest = moved_host and guest:K2_TeleportTo(second, guest_facing) == true
                local host_at, guest_at = host:K2_GetActorLocation(), guest:K2_GetActorLocation()
                if moved_guest and distance(host_at, first) <= 20 and distance(guest_at, second) <= 20 then
                    host:ForceNetUpdate()
                    guest:ForceNetUpdate()
                    return ("%s|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f|%.3f"):format(fog_marker:GetFullName(),
                        anchor.X, anchor.Y, anchor.Z, host_at.X, host_at.Y, host_at.Z, guest_at.X, guest_at.Y, guest_at.Z)
                end
                if moved_host then assert(host:K2_TeleportTo(host_before, host_facing), "Host floor rollback failed") end
                if moved_guest then assert(guest:K2_TeleportTo(guest_before, guest_facing), "Guest floor rollback failed") end
            end
        end
    end
    error("No qualified floor pair within 35 metres of the exact stock fog marker", 0)
end

local function start_fog(world, statics, pawn, walkers)
    assert(not fog_demo and world:GetFullName() == "World " .. crossroads)
    local day = StaticFindObject(crossroads .. ":PersistentLevel.DayCycle_SunControler_BP_C_1")
    local horn = StaticFindObject(crossroads .. ":PersistentLevel.AlarmSiren_BP_C_1")
    assert(valid(day) and same(day:GetWorld(), world) and not valid(day.LocalStorm) and day:HasAuthority())
    assert(valid(horn) and same(horn:GetWorld(), world) and horn.bReplicates == false)
    local class = find(fog_class)
    assert(valid(class))
    local transform = {Translation = {X = 0, Y = 0, Z = 0},
        Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
    local storm = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
    assert(valid(storm) and same(storm:GetClass(), class) and same(storm:GetWorld(), world))
    assert(math.abs(storm.FogDensity - 1.2) < .0001 and storm.FogDensityBlend == 1
        and storm.SirenTime == 44 and valid(storm.Siren) and storm.Siren:GetFullName() == "SoundCue " .. fog_cue)
    storm.FirstActivation = true
    -- The first stock activation uses level 0 for the hunt and level 1 for atmosphere only.
    storm.StormLevel = walkers and 0 or 1
    if walkers then fog_armed = farther_markers(world, pawn) end
    local realtime = day.UpdateInRealTime
    day.UpdateInRealTime = false
    storm = statics:FinishSpawningActor(storm, transform, 0)
    assert(valid(storm))
    day.LocalStorm = storm
    fog_demo = {day = day, storm = storm, realtime = realtime}
    fog_armed = nil
    return tostring(storm.FogDensity) .. "|" .. horn:GetFullName() .. "|" .. tostring(storm.SirenTime)
end

local function clear_fog(world)
    assert(fog_demo and valid(fog_demo.day) and same(fog_demo.day:GetWorld(), world))
    local demo = fog_demo
    assert(valid(demo.storm) and same(demo.day.LocalStorm, demo.storm))
    demo.storm:Start_Stop(false)
    demo.storm:K2_DestroyActor()
    demo.day.LocalStorm = nil
    fog_demo = nil
    demo.day:UpdateFog()
    local density = demo.day.HightFog.Component.FogDensity
    demo.day.UpdateInRealTime = demo.realtime
    return tostring(density)
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    if not valid(driver) then return end
    local guest = valid(driver.ServerConnection)
    if fog_demo and valid(fog_demo.storm) and same(fog_demo.storm:GetWorld(), world) then
        fog_demo.day:UpdateFog()
    end
    if guest and not installed then
        RegisterHook("/Script/Niagara.NiagaraComponent:SetVariableFloat", function(context, parameter, amount)
            local particle = context:get()
            if valid(particle) and valid(particle.Asset) and parameter:get():ToString() == "User.Intensity"
                and particle.Asset:GetFullName() == "NiagaraSystem /Game/Assets/PFX/NI_Rain_01.NI_Rain_01" then
                spool("guest-rain", tostring(amount:get()))
            end
        end)
        if valid(StaticFindObject("/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren")) then
            RegisterHook("/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren", function(context, cue, duration)
                local actor, sound = context:get(), cue:get()
                if valid(actor) and valid(sound) and same(actor:GetWorld(), world) then
                    spool("guest-siren", actor:GetFullName() .. "|" .. sound:GetFullName() .. "|" .. tostring(duration:get()))
                end
            end)
        end
        installed = true
    end
    local source = io.open(box .. "/weather-probe", "rb")
    if not source then return end
    local command = source:read("*l")
    source:close()
    if previous == command then return end
    previous = command
    local ok, result = pcall(function()
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local pawn = statics:GetPlayerPawn(world, 0)
        if fog_walkers and (command == "fog-roster-before" or command == "fog-roster-during") then
            return report_fog_roster(command, world, pawn)
        end
        if guest then
            assert(command == "check" or command == "check-dry")
            local stock, projected = 0, 0
            for _, actor in ipairs(FindAllOf("Rain_BP_C") or {}) do
                if same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then stock = stock + 1 end
            end
            for _, particle in ipairs(FindAllOf("NiagaraComponent") or {}) do
                if valid(particle) and valid(particle.Asset) and same(particle:GetAttachParent(), pawn.RootComponent)
                    and particle.Asset:GetFullName() == "NiagaraSystem /Game/Assets/PFX/NI_Rain_01.NI_Rain_01"
                    and particle:IsActive() then projected = projected + 1 end
            end
            return tostring(stock) .. "|" .. tostring(projected)
        end
        assert(os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        if fog_walkers and command == "fog-roster-approach" then return approach_fog(world, driver, pawn) end
        if command == "the-fog" or command == "the-fog-walkers" and fog_walkers then
            return start_fog(world, statics, pawn, command == "the-fog-walkers")
        end
        if command == "clear-fog" then return clear_fog(world) end
        if command == "dry" then
            assert(valid(rain))
            rain:UpdateIntensity(0, false)
            return tostring(rain.Intensity)
        end
        assert(command == "wet")
        local day = FindFirstOf("DayCycle_SunControler_BP_C")
        assert(valid(day) and same(day:GetWorld(), world))
        day.UpdateInRealTime = false
        rain = day.RainController
        if not valid(rain) then
            local class = find("/Game/Classes/Actors/Rain_BP.Rain_BP_C")
            local transform = {Translation = pawn:K2_GetActorLocation(),
                Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
            local created = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
            assert(valid(created), "stock rain spawn unavailable")
            created.Max_LightningChance, created.LightningMod = 0, 0
            created.Intensity, created.Rain_Mod = 0.55, 1.0
            rain = statics:FinishSpawningActor(created, transform, 0)
            day.RainController = rain
        end
        assert(valid(rain))
        rain.Max_LightningChance, rain.LightningMod = 0, 0
        rain:UpdateIntensity(0.55, false)
        local horn
        for _, candidate in ipairs(FindAllOf("AlarmSiren_BP_C") or {}) do
            if same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then horn = candidate break end
        end
        assert(valid(horn), "no authored storm siren")
        local cue = find("/Game/Assets/Sounds/CUEs/Alarm_Cues/StormSiren_03_Cue.StormSiren_03_Cue")
        assert(valid(cue))
        horn:StormSiren(cue, 15)
        return tostring(rain.Intensity) .. "|" .. horn:GetFullName() .. "|" .. tostring(horn.bReplicates)
    end)
    spool("weather-probe-reply", command .. "|" .. (ok and "ok" or "failed") .. "|" .. tostring(result))
end)
