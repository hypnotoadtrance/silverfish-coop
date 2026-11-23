-- Private, muted Ruin census and opt-in stock reinforcement proof.
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_RUIN_NPC_PROBE") ~= "1"
    or not bridge or not bridge:match("[/\\]campaign%-npc%-ruin%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then
    return
end

local world_name = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"
local level_path = world_name .. ":PersistentLevel."
local classes = {"NPC_Zomb_01_C", "NPC_WildBoar_C", "NPC_FeralDog_C", "NPC_Survivor_FlowerLady_C",
    "NPC_MinorStag_C", "NPC_Doe_C", "NPC_Survivor_Worker_C", "NPC_Homunculus_C"}
local marker_name = "AI_Spawner_Squad_BP_C " .. level_path .. "AI_Spawner_Squad_BP_C_8"
local living_count = "/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C:Calc_LivingNPCs"
local marker_check = "/Game/Classes/ScriptingActors/AI_Spawner_Squad_BP.AI_Spawner_Squad_BP_C:SpawnIsValid"
local stock_squads = {
    host = {"WildLife/Stag_WtDoe.Stag_WtDoe", "NPC_MinorStag", "NPC_Doe"},
    client = {"Monsters/Surv_Workers.Surv_Workers", "NPC_Survivor_Worker"},
}
local last, refresh_used

local function valid(value) return value and value:IsValid() end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function object(value)
    if type(value) == "userdata" and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then
        return value:get()
    end
    return value
end

local function active_ruin()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. world_name and valid(world.NetDriver), "world")
    local director = StaticFindObject(level_path .. "DynaimicSpawnControler_BP_C_0")
    assert(valid(director) and valid(director.AmbientSquadPool) and director.AmbientSquadPool:GetFullName() ==
        "AmbientSquadPoolData_C /Game/Classes/Libraries/DataAssets/AmbientSpawnPools/FloodPlainsSquadPool.FloodPlainsSquadPool",
        "director")
    local viewer = valid(world.NetDriver.ServerConnection) and "client" or "host"
    return world, director, viewer
end

local function census()
    local world, _, viewer = active_ruin()
    local rows = {viewer}
    for _, class in ipairs(classes) do
        local expected = "/Game/Classes/NPCs/" .. class:sub(1, -3) .. "." .. class
        local prefix = class .. " " .. level_path
        for _, actor in ipairs(FindAllOf(class) or {}) do
            local name = actor:GetFullName()
            if name:sub(1, #prefix) == prefix and actor:GetClass():GetFullName() == "BlueprintGeneratedClass " .. expected
                and actor:GetWorld():GetAddress() == world:GetAddress() and not actor:IsActorBeingDestroyed() then
                local point = actor:K2_GetActorLocation()
                rows[#rows + 1] = ("%s\t%s\t%d\t%s\t%s\t%.2f\t%.2f\t%.2f"):format(class, name,
                    actor:GetLocalRole(), tostring(actor.bReplicates), tostring(actor.Alive), point.X, point.Y, point.Z)
                assert(#rows <= 129, "census limit")
            end
        end
    end
    return table.concat(rows, "\n")
end

local function stock_population(director)
    local count = StaticFindObject(living_count)
    assert(valid(count) and count:GetFullName() == "Function " .. living_count
        and same(count:GetOuter(), director:GetClass()) and count:GetFunctionFlags() & 0x440 == 0,
        "exact stock population function")
    local parameters = 0
    count:ForEachProperty(function(property)
        if property:GetFName():ToString() == "NPCs" then
            assert(property:GetClass():GetFName():ToString() == "IntProperty" and property:GetOffset_Internal() == 0,
                "stock population output schema")
            parameters = parameters + 1
        end
    end)
    assert(parameters == 1, "expected one exact stock population output")
    local output = {}
    director:Calc_LivingNPCs(output)
    local population, cap = math.tointeger(output.NPCs), math.tointeger(director.MaxNPCs)
    assert(population and population >= 0 and population < 128 and cap and cap > 0 and cap <= 128,
        "stock population and cap readback")
    return population, cap
end

local function stock_refresh()
    assert(os.getenv("SILVERFISH_COOP_RUIN_NPC_REFRESH_PROBE") == "1" and not refresh_used, "refresh scope")
    refresh_used = true
    local world, director, viewer = active_ruin()
    local driver = world.NetDriver
    assert(same(driver.World, world) and (viewer == "client" or driver.ClientConnections:GetArrayNum() == 1),
        "exact loopback seat")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and same(pawn:GetWorld(), world) and pawn:IsLocallyControlled()
        and pawn:HasAuthority() == (viewer == "host"), "local pawn")

    local stock = stock_squads[viewer]
    local asset_name = "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/" .. stock[1]
    local maps = {director.AmbientSquadPool.DayTimeSpawns, director.AmbientSquadPool.NightTimeSpawns}
    local saved_weights, found = {{}, {}}, 0
    for index, map in ipairs(maps) do
        map:ForEach(function(key, value)
            local data, weight = key:get(), value:get()
            assert(valid(data) and type(weight) == "number" and weight == weight and weight > 0 and weight < 100,
                "stock pool weight")
            local selected = data:GetFullName() == asset_name
            if selected then found = found + 1 end
            saved_weights[index][#saved_weights[index] + 1] = {data = data, weight = weight, selected = selected}
        end)
        assert(#saved_weights[index] == (index == 1 and 8 or 2), "stock day and night key counts")
    end
    assert(found == 1 and math.abs(director.AmbientSquadPool.TimeOfDayInfluence - .9944) < .00001,
        "selected asset must already belong to the authored blended pool")

    local originals, marker = {}, nil
    local marker_array = director.Spawns_Reinforcments
    assert(marker_array:GetArrayNum() == 4, "expected four authored reinforcement markers")
    for index = 1, marker_array:GetArrayNum() do
        local candidate = object(marker_array[index])
        assert(valid(candidate) and same(candidate:GetWorld(), world) and candidate.Usage == 3, "stock marker")
        originals[index] = candidate
        if candidate:GetFullName() == marker_name then
            assert(not marker, "duplicate selected stock marker")
            marker = candidate
        end
    end
    assert(valid(marker) and same(marker.Arrow_Leader:GetOwner(), marker), "exact reinforcement marker")
    local point, arrival = marker.Arrow_Leader:K2_GetComponentLocation(), pawn:K2_GetActorLocation()
    local distance = math.sqrt((point.X-arrival.X)^2 + (point.Y-arrival.Y)^2 + (point.Z-arrival.Z)^2)
    assert(distance > 3200 and distance < 9500, "party is not at the stock Market door")
    local classes_loaded = {}
    for index = 2, #stock do
        local name = stock[index]
        local class = StaticFindObject("/Game/Classes/NPCs/" .. name .. "." .. name .. "_C")
        classes_loaded[#classes_loaded + 1] = name .. "=" .. tostring(valid(class))
    end

    local geometry, stock_attempt, phase, hook_failure, maps_changed, markers_changed
    local population, stock_cap, applied_cap, active_population, count_calls, count_pre, count_post
    local cap_changed
    local pre, post = RegisterHook(marker_check, function(context, radius, eligible)
        if not phase or not same(context:get(), marker) then return end
        if radius:get() ~= true or type(eligible:get()) ~= "boolean" then
            hook_failure = "stock geometry output schema"
            return
        end
        if phase == "inspect" then
            geometry = eligible:get()
        else
            stock_attempt = eligible:get()
            if geometry == false and stock_attempt == false then eligible:set(true) end
        end
    end)
    local ok, failure = pcall(function()
        phase = "inspect"
        marker:SpawnIsValid(true, {})
        phase = nil
        assert(type(geometry) == "boolean" and not hook_failure, hook_failure or "missing stock geometry readback")
        population, stock_cap = stock_population(director)
        applied_cap, count_calls = math.max(stock_cap, population + 1), 0
        count_pre, count_post = RegisterHook(living_count, function(context, result)
            if phase ~= "refresh" or not same(context:get(), director) then return end
            count_calls = count_calls + 1
            active_population = math.tointeger(result:get())
            if count_calls ~= 1 or not active_population or active_population < 0 or active_population >= 128 then
                hook_failure = "stock population during refresh unqualified"
            end
        end)

        -- Keep the sole selected key in its own authored pool. A zero-weight key can win a zero roll.
        maps_changed = true
        for index, map in ipairs(maps) do
            map:Empty()
            for _, row in ipairs(saved_weights[index]) do
                if row.selected then map:Add(row.data, row.weight) end
            end
        end
        markers_changed = true
        marker_array:Empty()
        marker_array[1] = marker
        assert(marker_array:GetArrayNum() == 1 and same(object(marker_array[1]), marker), "single stock marker")
        if applied_cap ~= stock_cap then
            cap_changed = true
            director.MaxNPCs = applied_cap
            assert(director.MaxNPCs == applied_cap, "temporary stock population cap")
        end
        phase = "refresh"
        director:RefreshEnemies()
        phase = nil
        assert(not hook_failure, hook_failure)
    end)
    phase = nil

    local cap_ok, cap_error = pcall(function()
        if cap_changed then
            director.MaxNPCs = stock_cap
            assert(director.MaxNPCs == stock_cap, "stock population cap restore verification")
        end
    end)
    local weights_ok, weights_error = pcall(function()
        if not maps_changed then return end
        for index, map in ipairs(maps) do
            map:Empty()
            for _, row in ipairs(saved_weights[index]) do map:Add(row.data, row.weight) end
            assert(#map == #saved_weights[index], "stock pool size restore verification")
            for _, row in ipairs(saved_weights[index]) do
                local current = map:Find(row.data)
                assert(current and current:get() == row.weight, "stock pool restore verification")
            end
        end
    end)
    local markers_ok, markers_error = pcall(function()
        if not markers_changed then return end
        marker_array:Empty()
        for index, saved in ipairs(originals) do marker_array[index] = saved end
        assert(marker_array:GetArrayNum() == #originals, "stock marker array restore verification")
        for index, saved in ipairs(originals) do
            assert(same(object(marker_array[index]), saved), "stock marker order restore verification")
        end
    end)
    local hook_ok, hook_error = pcall(UnregisterHook, marker_check, pre, post)
    local count_ok, count_error = true, nil
    if count_pre then count_ok, count_error = pcall(UnregisterHook, living_count, count_pre, count_post) end
    assert(cap_ok, cap_error)
    assert(weights_ok, weights_error)
    assert(markers_ok, markers_error)
    assert(hook_ok, hook_error)
    assert(count_ok, count_error)
    assert(ok, failure)
    return table.concat({viewer, "refresh", asset_name, marker_name, tostring(geometry),
        tostring(stock_attempt), tostring(geometry == false and stock_attempt == false),
        table.concat(classes_loaded, ","), ("%.2f"):format(distance), tostring(#originals), "restored",
        population, stock_cap, applied_cap, tostring(active_population), count_calls}, "\t")
end

local function stock_corpse(npc, world, viewer)
    local mesh = npc.Mesh
    assert(valid(mesh) and same(mesh:GetOwner(), npc), "exact stock NPC mesh unavailable")
    local class = StaticFindObject("/Script/Engine.TargetPoint")
    local marker
    for _, candidate in ipairs(FindAllOf("TargetPoint") or {}) do
        if valid(candidate) and same(candidate:GetClass(), class) and same(candidate:GetWorld(), world)
            and same(candidate:GetOwner(), npc) and candidate:GetLocalRole() == npc:GetLocalRole()
            and not candidate:IsActorBeingDestroyed() then
            assert(not marker, "multiple corpse markers own the exact stock NPC")
            marker = candidate
        end
    end
    local number = 0
    if marker then
        local rotation = marker:K2_GetActorRotation()
        local function byte(angle) return math.floor(angle / (360 / 256) + .5) % 256 end
        number = byte(rotation.Roll) * 256 + byte(rotation.Yaw)
    end
    return ("%s\t%s\t%s\t%s\t%d"):format(viewer, npc:GetFullName(), tostring(npc.Alive),
        tostring(mesh:IsAnySimulatingPhysics() and mesh:IsVisible()), number)
end

local function damage_probe(operation, name)
    local world, _, viewer = active_ruin()
    local kind, path
    if name then kind, path = name:match("^(NPC_[%w_]+) (.+)$") end
    assert(kind and path:sub(1, #level_path) == level_path, "expected an enrolled ambient NPC name")
    local base = StaticFindObject("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C")
    local npc
    for _, candidate in ipairs(FindAllOf(kind) or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and candidate:GetFullName() == name then
            assert(not npc, "duplicate enrolled ambient NPC")
            npc = candidate
        end
    end
    assert(valid(base) and valid(npc) and npc:IsA(base), "exact stock ambient NPC unavailable")
    assert(npc.bReplicates and npc:GetLocalRole() == (viewer == "client" and 1 or 3),
        "expected a replicated ambient NPC in this process")
    if operation == "corpse" then return stock_corpse(npc, world, viewer) end
    assert(npc.Alive, "expected a living ambient NPC")
    local before = npc.Health
    if operation == "damage" or operation == "kill" then
        assert((operation == "damage") == (viewer == "client")
            and os.getenv("SILVERFISH_COOP_RUIN_NPC_REFRESH_PROBE") == "1", "damage requires the isolated test seat")
        local pawn = StaticFindObject("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
        assert(valid(pawn) and same(pawn:GetWorld(), world) and pawn:IsLocallyControlled()
            and pawn:HasAuthority() == (viewer == "host"), "exact local test pawn required")
        local total, lethal = {}, {}
        npc:DealDamage({Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5 = 200}, {}, pawn,
            {X=0, Y=0, Z=0}, false, total, lethal)
        if operation == "kill" then
            assert(total.TotalDamage > 0 and lethal.IsLethal and npc.Health <= 0,
                "stock host damage did not deal a lethal hit")
            if npc.Alive then npc:ResolveDeath() end
            return stock_corpse(npc, world, viewer)
        end
        assert(npc.Alive and npc.Health == before and total.TotalDamage == 0 and lethal.IsLethal == false,
            ("guest damage ran on %s at %.2f HP, damage=%s, lethal=%s"):format(
                name, npc.Health, tostring(total.TotalDamage), tostring(lethal.IsLethal)))
        npc:ResolveDeath()
        assert(npc.Alive and npc.Health == before and not npc.Mesh:IsAnySimulatingPhysics(),
            "guest death ran on the authoritative replica")
    end
    return ("%s\t%s\t%.6f"):format(viewer, name, npc.Health)
end

LoopInGameThreadWithDelay(150, function()
    local file = io.open(bridge .. "/ruin-npc-command.tsv", "rb")
    if not file then return false end
    local request = file:read(560) or ""
    file:close()
    local ticket, operation, actor = request:match("^(%x+)\t([a-z]+)\t([^\r\n]+)\n$")
    if not ticket then ticket, operation = request:match("^(%x+)\t([a-z]+)\n$") end
    if not ticket then ticket, operation = request:match("^(%x+)\n$"), "census" end
    if not ticket or #ticket ~= 32 or ticket == last then
        return false
    end
    last = ticket
    local ok, result = pcall(function()
        if operation == "census" then return census() end
        if operation == "refresh" then return stock_refresh() end
        assert(operation == "damage" or operation == "health" or operation == "kill" or operation == "corpse",
            "unknown private NPC action")
        return damage_probe(operation, actor)
    end)
    local output = assert(io.open(bridge .. "/ruin-npc-reply.tsv", "wb"))
    output:write(ticket, ok and "\n" or "\nerror\t", result, "\nend\n")
    output:close()
    return false
end)
