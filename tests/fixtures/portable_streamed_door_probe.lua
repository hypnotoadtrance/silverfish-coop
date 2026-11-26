local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = bridge .. "/portable-streamed-door-"
local world_name = "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local sublevel_package = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
local level, owner, last
local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function run(action)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == world_name and valid(world.NetDriver), "Crossroads is required")
    if action == "load" then
        assert(not valid(level) or not same(owner, world))
        local api = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
        level = api:LoadLevelInstance(world, sublevel_package, {X = 50000, Y = 50000, Z = 8000},
            {Pitch = 0, Yaw = 0, Roll = 0}, {}, "CoopDoorSublevel", nil, false)
        assert(valid(level), "could not load the stock dock")
        owner = world
        return "loading"
    end
    assert(same(world, owner) and valid(level), "the dock is unavailable")
    if action == "show" or action == "hide" or action == "unload" then
        level:SetShouldBeVisible(action == "show")
        if action == "unload" then level:SetShouldBeLoaded(false) end
        return action
    end
    assert(action == "identify")
    if not level:IsLevelVisible() then return "waiting" end
    local outer = level:GetLoadedLevel()
    local path = outer:GetFullName():match("^%S+ (.+)$") .. ".Door_Bars_01_BP_C_1"
    local door = StaticFindObject(path)
    assert(valid(door) and same(door:GetOuter(), outer) and same(door.Box_Knob:GetOwner(), door),
        "the loaded dock lacks the stock hinged gate")
    return "stream:" .. door:GetFullName()
end

LoopInGameThreadWithDelay(50, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(64)
    file:close()
    local ticket, action = (request or ""):match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, result = pcall(run, action)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(result):gsub("[\t\r\n]", " ")))
    reply:close()
end)
