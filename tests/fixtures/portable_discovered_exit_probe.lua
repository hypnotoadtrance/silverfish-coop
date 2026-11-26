local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local exit_path = observatory .. ":PersistentLevel.Level_Exit_BP_C_1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local streamed_entrance = "Mod-Stream-Arrival"
local last, copy, approach, outside, stream
local arrival_pending, arrival_stream

local function valid(object) return object and object:IsValid() end

local function playtest_stream(world, name)
    local loader = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
    assert(valid(loader))
    return loader:LoadLevelInstance(world, "/Game/Classes/Maps/PlaytestMap_01",
        {X = 45000, Y = 45000, Z = 2000}, {Pitch = 0, Yaw = 0, Roll = 0}, {}, name, nil, false)
end

local function prepare_arrival(world)
    if not valid(arrival_stream) then
        arrival_stream = playtest_stream(world, "CoopStreamArrivalProbe")
        assert(valid(arrival_stream), "Could not load the fixture's destination sublevel")
        return
    end
    if not arrival_stream:IsLevelVisible() then return end
    local level = arrival_stream:GetLoadedLevel()
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local stock = StaticFindObject(crossroads .. ":PersistentLevel.LevelExit_Spawn_BP_C_2")
    local exit_class = StaticFindObject("/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C")
    assert(valid(level) and valid(stock) and valid(exit_class))
    local found, owner = {}, nil
    api:GetAllActorsOfClass(world, exit_class, found)
    for _, actor in ipairs(found.OutActors or found) do
        if type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        if valid(actor) and actor:GetOuter():GetAddress() == level:GetAddress() then
            owner = actor
            break
        end
    end
    assert(valid(owner), "Could not find an actor owned by the fixture's destination sublevel")
    local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    local origin, rotation = stock:K2_GetActorLocation(), stock:K2_GetActorRotation()
    local transform = math_api:MakeTransform(origin, rotation, {X = 1, Y = 1, Z = 1})
    local marker = api:BeginDeferredActorSpawnFromClass(world, stock:GetClass(), transform, 1, owner, 0)
    assert(valid(marker) and marker:GetOuter():GetAddress() == level:GetAddress(), "The spawned marker is outside the streamed sublevel")
    marker.EntranceTag = streamed_entrance
    api:FinishSpawningActor(marker, transform, 0)
    local tag = marker.EntranceTag
    if type(tag) ~= "string" and tag then tag = tag:ToString() end
    assert(valid(marker.Arrow) and tag == streamed_entrance)
    arrival_pending = nil
end

local function run(world, action)
    local stock = StaticFindObject(exit_path)
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    assert(valid(stock) and valid(pawn) and pawn:HasAuthority() and pawn:IsLocallyControlled())
    if action == "load" then
        stream = playtest_stream(world, "CoopStreamExitProbe")
        return valid(stream) and "loading" or "failed"
    end
    if action == "spawn" or action == "spawnstream" then
        assert(not valid(copy) and not stock.Box:IsOverlappingActor(pawn))
        if action == "spawnstream" then
            if not valid(stream) or not stream:IsLevelVisible() then return "waiting" end
            local streamed = stream:GetLoadedLevel()
            assert(valid(streamed) and streamed:GetAddress() ~= world.PersistentLevel:GetAddress())
            local found = {}
            api:GetAllActorsOfClass(world, stock:GetClass(), found)
            for _, actor in ipairs(found.OutActors or found) do
                if type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                if valid(actor) and actor:GetOuter():GetAddress() == streamed:GetAddress()
                    and actor:GetFullName():match(":PersistentLevel%.Level_Exit_BP_C_0$") then
                    copy = actor
                    break
                end
            end
            assert(valid(copy), "No stock exit was found in the loaded streamed level")
        end
        local player_point, stock_point = pawn:K2_GetActorLocation(), stock.Box:K2_GetComponentLocation()
        local dx, dy = player_point.X - stock_point.X, player_point.Y - stock_point.Y
        local distance = math.sqrt(dx * dx + dy * dy)
        assert(distance > 100, "The stock approach does not identify an outward lane")
        local point = {X = player_point.X + 1250 * dx / distance, Y = player_point.Y + 1250 * dy / distance, Z = player_point.Z + 80}
        if action == "spawn" then
            copy = world:SpawnActor(stock:GetClass(), point, stock:K2_GetActorRotation())
        else
            assert(copy:K2_SetActorLocation(point, false, {}, false))
            copy:K2_SetActorRotation(stock:K2_GetActorRotation(), false)
        end
        assert(valid(copy) and copy:HasAuthority() and valid(copy.Box))
        copy.NextLevel = stock.NextLevel
        copy["Entrance Tag"] = action == "spawnstream" and streamed_entrance or "Obsv_Etr"
        copy.Travel_TimeStep = action == "spawnstream" and 19 or 17
        copy.Box:SetBoxExtent({X = 160, Y = 160, Z = 180}, false)
        copy.Box:SetGenerateOverlapEvents(true)
        local center = copy.Box:K2_GetComponentLocation()
        outside = {X = center.X + 800 * dx / distance, Y = center.Y + 800 * dy / distance, Z = center.Z}
        approach = {X = center.X + 400 * dx / distance, Y = center.Y + 400 * dy / distance, Z = center.Z}
        assert(pawn:K2_SetActorLocation(outside, false, {}, false))
        return copy:GetFullName()
    end
    assert(valid(copy) and approach and not copy.Box:IsOverlappingActor(pawn))
    assert(action == "cross" or action == "alone" or action == "regroup")
    local point = copy.Box:K2_GetComponentLocation()
    local connection = world.NetDriver.ClientConnections[1]
    local guest = connection.PlayerController.Pawn
    assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
    local normal = copy:GetActorForwardVector()
    local separation = action == "regroup" and 600 or 2700
    assert(guest:K2_SetActorLocation({X = point.X + separation * normal.X, Y = point.Y + separation * normal.Y, Z = point.Z},
        false, {}, false), "The remote pawn could not be positioned for the party-radius check")
    if action == "regroup" then
        assert(pawn:K2_SetActorLocation(approach, false, {}, false))
        return "near"
    end
    if action == "alone" then assert(pawn:K2_SetActorLocation(approach, false, {}, false)) end
    local hit = {}
    local moved = pawn:K2_SetActorLocation({X = point.X, Y = point.Y, Z = point.Z}, true, hit, false)
    if action == "cross" then
        assert(moved and copy.Box:IsOverlappingActor(pawn), "The local pawn did not enter the test exit")
        if valid(stream) and stream:IsLevelVisible() then arrival_pending = true end
        return "entered"
    end
    hit = hit.SweepHitResult or hit.OutSweepHitResult or hit
    local component = hit.Component and hit.Component:Get()
    assert(hit.bBlockingHit and valid(component) and component:GetOwner():GetFullName():match("^TriggerBox "),
        "The runtime-discovered wall did not block the out-of-range sweep")
    local observed = component:K2_GetComponentLocation()
    assert(math.abs(observed.X - point.X) < 1 and math.abs(observed.Y - point.Y) < 1 and math.abs(observed.Z - point.Z) < 1,
        "The blocking shape belongs to a different exit")
    assert(not copy.Box:IsOverlappingActor(pawn))
    return "wall"
end

LoopInGameThreadWithDelay(50, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    if not valid(driver) or valid(driver.ServerConnection) then return end
    if arrival_pending and world:GetFullName() == "World " .. crossroads then
        local ok, problem = pcall(prepare_arrival, world)
        if not ok then
            print("Portable streamed arrival fixture: " .. tostring(problem))
            arrival_pending = nil
        end
    end
    if #driver.ClientConnections ~= 1 or world:GetFullName() ~= "World " .. observatory then return end
    local file = io.open(bridge .. "/portable-discovered-exit-command.tsv", "rb")
    if not file then return end
    local input = file:read("*l")
    file:close()
    if not input or input == last then return end
    local id, action = input:match("^([a-f0-9]+)\t(%a+)$")
    if not id or #id ~= 32 then return end
    last = input
    local ok, result = pcall(run, world, action)
    file = assert(io.open(bridge .. "/portable-discovered-exit-reply.tsv", "wb"))
    file:write(id, "\t", ok and "ok" or "error", "\t", tostring(result):gsub("[%c]", " "), "\n")
    file:close()
end)
