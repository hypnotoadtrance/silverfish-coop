local root = os.getenv("SF_LISTEN_PROOF_DIRECTORY")
if os.getenv("SF_LISTEN_NATIVE_PROBE") ~= "1" or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
    or not root or not root:match("[\\/]same%-listen%-%x+$") then return end

local title_map = "/Game/Classes/Maps/MainMenu_P"
local playtest_map = "/Game/Classes/Maps/PlaytestMap_01"
local title_world = "World " .. title_map .. ".MainMenu_P"
local playtest_world = "World " .. playtest_map .. ".PlaytestMap_01"
local ports = {tonumber(os.getenv("SF_LISTEN_PORT_1")), tonumber(os.getenv("SF_LISTEN_PORT_2"))}
local guest = os.getenv("SF_LISTEN_GUEST") == "1"
local phase, round = "returning", 1
local observed_peer = false

local function valid(value) return value and value:IsValid() end

local function emit(event, ...)
    local file = assert(io.open(root .. "/progress.tsv", "ab"))
    assert(file:write(table.concat({event, round, ...}, "\t"), "\n"))
    file:close()
end

local function requested()
    local file = io.open(root .. "/command.txt", "rb")
    if not file then return end
    local value = file:read(24)
    file:close()
    return value
end

LoopInGameThreadWithDelay(75, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(statics) then return end
    local controller = statics:GetPlayerController(world, 0)
    if not valid(controller) then return end
    local name = world:GetFullName()
    local command = requested()
    local driver = world.NetDriver
    local pawn = controller.Pawn
    if phase == "returning" and name == title_world and not valid(world.NetDriver) then
        local menu = FindFirstOf("MainMenu_Widget_C")
        if not valid(menu) or not menu:IsInViewport() or not menu:IsVisible() then return end
        phase = "title"
        emit("title")
        if round == 3 then return true end
    elseif phase == "title" and name == title_world and guest and command then
        local port = tonumber(command:match("^join" .. round .. " ([0-9]+)\n$"))
        if not port or port < 1 or port > 65535 then return end
        phase = "joining"
        statics:OpenLevel(controller, FName("127.0.0.1:" .. port), true, "")
    elseif phase == "title" and name == title_world and not guest and command == "host" .. round .. "\n" then
        if type(SilverfishArmTitleListenProbe) ~= "function" or SilverfishArmTitleListenProbe(ports[round]) ~= true then
            emit("arm_refused")
            return true
        end
        phase = "opening"
        statics:OpenLevel(controller, FName(playtest_map), true, "listen")
    elseif phase == "opening" and name == playtest_world and valid(world.NetDriver) then
        local maps, sockets, restored = SilverfishReadTitleListenProbe()
        phase = "hosting"
        emit("host", maps or 0, sockets or 0, restored and 1 or 0)
    elseif phase == "joining" and name == playtest_world and valid(driver) and valid(driver.ServerConnection)
        and valid(pawn) and pawn:IsLocallyControlled() then
        phase = "connected"
        emit("joined", string.format("%x", pawn:GetAddress()), string.format("%x", driver.ServerConnection:GetAddress()))
    elseif phase == "connected" and command == "sample" .. round .. "\n" then
        if not valid(driver) or not valid(driver.ServerConnection) or not valid(pawn) or not pawn:IsLocallyControlled() then return end
        phase = "sampled"
        emit("sampled", string.format("%x", pawn:GetAddress()), string.format("%x", driver.ServerConnection:GetAddress()))
    elseif phase == "hosting" and observed_peer and command == "sample" .. round .. "\n" then
        local connection = valid(driver) and #driver.ClientConnections == 1 and driver.ClientConnections[1]
        if not valid(connection) then return end
        phase = "sampled"
        emit("sampled_peer", string.format("%x", connection:GetAddress()))
    elseif (phase == "hosting" or phase == "connected" or phase == "sampled") and name == playtest_world
        and command == "leave" .. round .. "\n" then
        phase, round = "returning", round + 1
        observed_peer = false
        statics:OpenLevel(controller, FName(title_map), true, "")
    elseif phase == "hosting" and not observed_peer and valid(driver) and #driver.ClientConnections == 1 then
        local connection = driver.ClientConnections[1]
        if not valid(connection) then return end
        observed_peer = true
        emit("peer", string.format("%x", connection:GetAddress()))
    end
end)
