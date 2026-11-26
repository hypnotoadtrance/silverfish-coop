local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local exit_path = observatory .. ":PersistentLevel.Level_Exit_BP_C_1"
local last, copy, approach, outside

local function valid(object) return object and object:IsValid() end

local function run(world, action)
    local stock = StaticFindObject(exit_path)
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    assert(valid(stock) and valid(pawn) and pawn:HasAuthority() and pawn:IsLocallyControlled())
    if action == "spawn" then
        assert(not valid(copy) and not stock.Box:IsOverlappingActor(pawn))
        local player_point, stock_point = pawn:K2_GetActorLocation(), stock.Box:K2_GetComponentLocation()
        local dx, dy = player_point.X - stock_point.X, player_point.Y - stock_point.Y
        local distance = math.sqrt(dx * dx + dy * dy)
        assert(distance > 100, "The stock approach does not identify an outward lane")
        local point = {X = player_point.X + 1250 * dx / distance, Y = player_point.Y + 1250 * dy / distance, Z = player_point.Z + 80}
        copy = world:SpawnActor(stock:GetClass(), point, stock:K2_GetActorRotation())
        assert(valid(copy) and copy:HasAuthority() and valid(copy.Box))
        copy.NextLevel = stock.NextLevel
        copy["Entrance Tag"] = "Obsv_Etr"
        copy.Travel_TimeStep = 17
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
    local separation = action == "alone" and 2700 or 600
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
    if not valid(driver) or valid(driver.ServerConnection) or #driver.ClientConnections ~= 1
        or world:GetFullName() ~= "World " .. observatory then return end
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
