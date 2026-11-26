local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local exit_path = observatory .. ":PersistentLevel.Level_Exit_BP_C_1"
local last, copy

local function valid(object) return object and object:IsValid() end

local function run(world, action)
    local stock = StaticFindObject(exit_path)
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    assert(valid(stock) and valid(pawn) and pawn:HasAuthority() and pawn:IsLocallyControlled())
    if action == "spawn" then
        assert(not valid(copy) and not stock.Box:IsOverlappingActor(pawn))
        local point = stock:K2_GetActorLocation()
        copy = world:SpawnActor(stock:GetClass(), point, stock:K2_GetActorRotation())
        assert(valid(copy) and copy:HasAuthority() and valid(copy.Box))
        copy.NextLevel = stock.NextLevel
        copy["Entrance Tag"] = "Obsv_Etr"
        copy.Travel_TimeStep = 17
        copy.Box:SetBoxExtent(stock.Box:GetUnscaledBoxExtent(), false)
        copy.Box:SetGenerateOverlapEvents(true)
        stock.Box:SetGenerateOverlapEvents(false)
        return copy:GetFullName()
    end
    assert(action == "cross" and valid(copy) and not copy.Box:IsOverlappingActor(pawn))
    local point = copy.Box:K2_GetComponentLocation()
    local moved = pawn:K2_SetActorLocation({X = point.X, Y = point.Y, Z = point.Z}, false, {}, false)
    assert(moved and copy.Box:IsOverlappingActor(pawn), "The local pawn did not enter the test exit")
    return "entered"
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
