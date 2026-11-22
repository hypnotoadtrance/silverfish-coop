local fixture = {}
local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("boar%-proof%-%x+")
    or type(SilverfishReadCampaignNpc) ~= "function" then return end

local world_path = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local director_path = world_path .. ":PersistentLevel.DynaimicSpawnControler_BP_C_1"
local squads = {
    single = {"WildLife/Boar_Single.Boar_Single", "NPC_WildBoar_C"},
    group = {"WildLife/Boar_SmallGroup.Boar_SmallGroup", "NPC_WildBoar_C"},
    husks = {"Monsters/Husks_Scattered.Husks_Scattered", "NPC_Zomb_01_C"},
}
local selection = os.getenv("SILVERFISH_COOP_BOAR_SQUAD") or "single"
local stock = assert(squads[selection])
local data_path = "/Game/Classes/Libraries/DataAssets/Squads/" .. stock[1]
local bootcheck = "/Script/Engine.Default__GameplayStatics"
local previous, refreshes, last = nil, 0, ""
local armed, watched, damage_hook
local test_marker, eligibility_hook

local function valid(value)
    return value and value:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function spool(short_name, value, mode)
    local target = assert(io.open(box .. "/" .. short_name, mode or "ab"))
    target:write(value, "\n")
    target:close()
end

local function exact_npcs(world)
    local actors = {}
    for _, actor in ipairs(FindAllOf(stock[2]) or {}) do
        if same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then actors[#actors + 1] = actor end
    end
    return actors
end

local function refresh_stock(director)
    local pool = director.AmbientSquadPool
    assert(valid(pool) and pool:GetFullName() == "AmbientSquadPoolData_C /Game/Classes/Libraries/DataAssets/AmbientSpawnPools/CrossRoadsSquadPool.CrossRoadsSquadPool")
    local data = StaticFindObject(data_path)
    assert(valid(data))
    local maps = {pool.DayTimeSpawns, pool.NightTimeSpawns}
    local originals = {{}, {}}
    for index, map in ipairs(maps) do
        map:ForEach(function(key, value)
            originals[index][#originals[index] + 1] = {key:get(), value:get()}
        end)
        assert(#originals[index] < 128, "Crossroads pool exceeds the fixture size")
    end
    assert(#originals[1] + #originals[2] > 0, "Crossroads ambient pool is empty")
    local cap = director.MaxNPCs
    local ok, err = pcall(function()
        for _, map in ipairs(maps) do map:Empty() map:Add(data, 1) end
        director.MaxNPCs = cap + 4
        director:RefreshEnemies()
    end)
    director.MaxNPCs = cap
    for index, map in ipairs(maps) do
        map:Empty()
        for _, pair in ipairs(originals[index]) do map:Add(pair[1], pair[2]) end
    end
    assert(ok, err)
end

local function execution(world, director, driver, pawn, host, operation, marker_path)
    if operation == "refresh" then
        assert(host and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        refreshes = refreshes + 1
        assert(refreshes <= 8)
        refresh_stock(director)
        return tostring(refreshes)
    elseif operation == "approach" then
        assert(host and #driver.ClientConnections == 1 and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        local actors = exact_npcs(world)
        local target
        for _, candidate in ipairs(actors) do
            if candidate:HasAuthority() and candidate.bReplicates and candidate.Alive then target = candidate break end
        end
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(target) and valid(guest) and same(guest:GetWorld(), world) and not guest:IsLocallyControlled())
        local location = target:K2_GetActorLocation()
        local pending = {}
        pawn:K2_SetActorLocation({X=location.X - 800, Y=location.Y, Z=location.Z + 25}, false, pending, true)
        guest:K2_SetActorLocation({X=location.X - 800, Y=location.Y + 130, Z=location.Z + 25}, false, pending, true)
        pawn:ForceNetUpdate()
        guest:ForceNetUpdate()
        return target:GetFullName()
    elseif operation == "veto" then
        assert(not host and marker_path and #marker_path < 255)
        local marker = StaticFindObject(marker_path)
        assert(valid(marker) and same(marker:GetWorld(), world)
            and marker:GetClass():GetFullName():match("%.AI_Spawner_Squad_BP_C$"))
        if os.getenv("SILVERFISH_COOP_NATIVE_CROSSROADS_ROSTER") == "1" then
            if not eligibility_hook then
                RegisterHook("/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnIsValid",
                    function(context, _, eligible)
                        if same(context:get(), test_marker) then eligible:set(true) end
                    end)
                eligibility_hook = true
            end
            test_marker = marker
            local ok, err = pcall(refresh_stock, director)
            test_marker = nil
            assert(ok, err)
            return "stock refresh"
        end
        local data = StaticFindObject(data_path)
        assert(valid(data))
        local out = {}
        marker:SpawnSquadFromData(data, {}, out)
        return tostring(out.NpcsCount)
    elseif operation == "arm" or operation == "armlethal" then
        assert(host and #driver.ClientConnections == 1 and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        local target = StaticFindObject(marker_path)
        local shooter = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(target) and target:GetClass():GetFullName():match("%.NPC_WildBoar_C$")
            and same(target:GetWorld(), world) and target:HasAuthority() and target.bReplicates and target.Alive)
        assert(valid(shooter) and same(shooter:GetWorld(), world) and not shooter:IsLocallyControlled())
        if operation == "armlethal" then target.Health = 1 end
        armed = {target = target, shooter = shooter, before = target.Health, world = world}
        if not damage_hook then
            RegisterHook("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage",
                function(context, _, _, instigator)
                    local victim, source = context:get(), instigator:get()
                    if watched and same(victim, watched.target) and same(source, watched.shooter) then
                        spool("boar-damage", ("%s|%s|%.2f|%.2f|%s"):format(victim:GetFullName(),
                            source:GetFullName(), watched.before, victim.Health, tostring(victim.Alive)))
                        watched = nil
                    end
                end)
            damage_hook = true
        end
        return tostring(target.Health)
    elseif operation == "localdamage" then
        assert(not host and os.getenv("SILVERFISH_COOP_NATIVE_CROSSROADS_ROSTER") == "1")
        local actors = exact_npcs(world)
        assert(#actors == 1 and actors[1]:GetLocalRole() == 1 and actors[1].Alive)
        local npc = actors[1]
        local initial, total, lethal = npc.Health, {}, {}
        npc:DealDamage({Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5 = 200}, {}, pawn,
            {X = 0, Y = 0, Z = 0}, false, total, lethal)
        npc:ResolveDeath()
        assert(npc.Alive and npc.Health == initial and not npc.Mesh:IsAnySimulatingPhysics())
        assert(total.TotalDamage == 0 and lethal.IsLethal == false)
        return tostring(initial) .. "|" .. tostring(npc.Health) .. "|alive"
    elseif operation == "death" then
        local deaths = 0
        for _, actor in ipairs(exact_npcs(world)) do
            if not actor.Alive and actor:GetLocalRole() == (host and 3 or 1)
                and valid(actor.Mesh) and actor.Mesh:IsAnySimulatingPhysics() then deaths = deaths + 1 end
        end
        return tostring(deaths)
    elseif operation == "corpseposition" or operation == "corpsemove" then
        local corpse
        for _, actor in ipairs(exact_npcs(world)) do
            if not actor.Alive and actor:GetLocalRole() == (host and 3 or 1)
                and valid(actor.Mesh) and actor.Mesh:IsAnySimulatingPhysics() then
                assert(not corpse, "expected one stock corpse")
                corpse = actor
            end
        end
        assert(corpse, "stock corpse missing")
        local mesh = corpse.Mesh
        if operation == "corpsemove" then
            assert(host)
            mesh:K2_AddWorldOffset({X = 0, Y = 360, Z = 10}, false, {}, true)
        end
        local pelvis = FName("pelvis", EFindName.FNAME_Find)
        local point = mesh:DoesSocketExist(pelvis) and mesh:GetSocketLocation(pelvis) or mesh:K2_GetComponentLocation()
        return ("%.2f|%.2f|%.2f"):format(point.X, point.Y, point.Z)
    elseif operation == "position" then
        local actors = {}
        for _, actor in ipairs(exact_npcs(world)) do
            local point = actor:K2_GetActorLocation()
            actors[#actors+1] = ("%s|%d|%.1f|%.1f|%.1f|%s|%.1f"):format(actor:GetFullName(),
                actor:GetLocalRole(), point.X, point.Y, point.Z, tostring(actor.Alive), actor.Health)
        end
        return table.concat(actors, ";")
    end
    error("unexpected operation")
end

function fixture.before_fire(state, phase, name)
    local shot = armed
    armed = nil
    if not shot then return end
    local host, guest = state.local_pawn, state.guests and state.guests[1]
    if phase ~= "hosting" or not host or not guest or #state.guests ~= 1 or guest.name ~= name
        or not same(state.world, shot.world) or not same(guest.pawn, shot.shooter)
        or not valid(shot.target) or not shot.target.Alive then return nil, "boar_shot_scope_unavailable" end
    local stock_pose = require("native_campaign_npc").boar_test_stock_aim
    local ok, reach, restore = pcall(stock_pose, state, host, guest, shot.target, exact_npcs(state.world))
    if not ok then return nil, tostring(reach) end
    if not reach or not restore then return nil, "boar_shot_lane_unavailable" end
    watched = shot
    return restore
end

LoopInGameThreadWithDelay(240, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. world_path then return end
    local driver = world.NetDriver
    local director = StaticFindObject(director_path)
    if not valid(driver) or not valid(director) then return end
    local pawn = StaticFindObject(bootcheck):GetPlayerPawn(world, 0)
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    local host = not valid(driver.ServerConnection)
    local values = selection == "husks" and table.pack(SilverfishReadCampaignNpc(pawn:GetAddress(), host))
        or table.pack(SilverfishReadCampaignNpc(pawn:GetAddress(), host, "boar"))
    if values.n == 27 then
        local line = {}
        for index = 1, values.n do line[index] = tostring(values[index]):gsub("\n", ";") end
        local output = table.concat(line, "|")
        if output ~= last then spool("boar-census", output) last = output end
    end
    local source = io.open(box .. "/boar-probe", "rb")
    if not source then return end
    local request = source:read("*l")
    source:close()
    if not request or request == previous then return end
    local operation, marker = request:match("^(%w+)|?(.*)$")
    if not operation then return end
    previous = request
    local ok, result = pcall(execution, world, director, driver, pawn, host, operation, marker)
    spool("boar-reply", (ok and "ok|" or "failed|") .. tostring(result), "wb")
end)

return fixture
