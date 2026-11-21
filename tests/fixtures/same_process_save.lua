-- Test-only UE4SS entry for an isolated stock title.
local root = os.getenv("SILVERFISH_SAME_PROCESS_SAVE_PROOF")
if not root or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not root:match("[\\/]same%-save%-%x+$") then return end

local statics_path = "/Script/Engine.Default__GameplayStatics"
local system_path = "/Script/Engine.Default__KismetSystemLibrary"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local config_path = "BlueprintGeneratedClass /Game/Classes/Global/Settings_Config_BP.Settings_Config_BP_C"
local stock_world = "World /Game/Classes/Maps/MainMenu_P.MainMenu_P"
local title_class = "MainMenu_Widget_C"
local loading_package = "/Game/Classes/Maps/UtilityMaps/LoadingArea_P"
local loading_world = "World " .. loading_package .. ".LoadingArea_P"
local destination_world = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local manage_path = "/Game/Classes/GUI/ManageSaves_Widget.ManageSaves_Widget_C"
local slot = "SameProcessCanary"
local profile = root .. "/game-user/Saved/SaveGames"
local branch = "SameProcessCoop"
local coop_profile = profile .. "/" .. branch
local mode, ran, active = "solo", false, false
local routes = {solo = {}, coop = {}}
local config_candidate, stable_samples = nil, 0
local pending
local preferences = {FOV = 88, MouseSensitivity = 0.75, VolumeMaster = 0.375, VolumetricFog = false}

local function text(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function normalized(path)
    return (path:gsub("\\", "/"):gsub("//+", "/"):gsub("/+$", ""):lower())
end

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function require_that(test, failure)
    if not test then error(failure, 0) end
end

local function report(state, message)
    local path = root .. "/proof.tsv"
    local stream = assert(io.open(path .. ".tmp", "wb"))
    assert(stream:write(state, "\t", tostring(message):gsub("[\r\n\t]", " "), "\n"))
    for _, side in ipairs({"solo", "coop"}) do
        for _, name in ipairs({"save", "load", "exists", "delete", "find", "async_load"}) do
            assert(stream:write(side, "\t", name, "\t", tostring(routes[side][name] or 0), "\n"))
        end
    end
    stream:close()
    assert(os.rename(path .. ".tmp", path))
end

local function assert_equal(actual, expected, failure)
    require_that(actual == expected, failure .. " actual=" .. tostring(actual))
end

local function count(name)
    routes[mode][name] = (routes[mode][name] or 0) + 1
end

local function route_slot(name, parameter, user)
    if not active or text(parameter:get()) ~= slot or user:get() ~= 0 then return end
    count(name)
    if mode == "coop" then parameter:set(branch .. "/" .. slot) end
end

RegisterHook("/Script/Engine.GameplayStatics:SaveGameToSlot", function(_, _, parameter, user)
    route_slot("save", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:LoadGameFromSlot", function(_, parameter, user)
    route_slot("load", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:DoesSaveGameExist", function(_, parameter, user)
    route_slot("exists", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:DeleteGameInSlot", function(_, parameter, user)
    route_slot("delete", parameter, user)
end)
RegisterHook("/Script/Engine.AsyncActionHandleSaveGame:AsyncLoadGameFromSlot", function(_, context, parameter, user)
    if not pending or mode ~= "coop" then return end
    local actor = context:get()
    local world = valid(actor) and actor:GetWorld()
    if valid(world) and world:GetFullName() == loading_world then route_slot("async_load", parameter, user) end
end)
RegisterHook("/Script/BlueprintFileUtils.BlueprintFileUtilsBPLibrary:FindFiles", function(_, directory, _, extension)
    if not active or text(extension:get()) ~= ".sav" or normalized(text(directory:get())) ~= normalized(profile) then return end
    count("find")
    if mode == "coop" then directory:set(coop_profile .. "/") end
end)

local function slurp(path)
    local stream, detail = io.open(path, "rb")
    require_that(stream, tostring(detail))
    local data = stream:read("*a")
    stream:close()
    return data
end

local function snapshot_save(name, path)
    local data = slurp(path)
    require_that(#data > 16 and data:sub(1, 4) == "GVAS", "invalid save bytes for " .. name)
    local packet = assert(io.open(root .. "/" .. name .. ".sav", "wb"))
    assert(packet:write(data))
    packet:close()
    return data
end

local function recent(instance, expected)
    local output = {}
    instance:Find_MostRecentSaveFile(output, output)
    assert_equal(output.SaveFileFound, expected ~= nil, mode .. " recent-save lookup")
    if expected ~= nil then
        require_that(valid(output.Save), mode .. " missing recent-save object")
        assert_equal(output.Save.PlayerHealth, expected, mode .. " recent-save data")
    end
end

local function check_preferences(config)
    require_that(valid(config) and config:GetClass():GetFullName() == config_path, "stock settings object unavailable")
    for field, expected in pairs(preferences) do
        assert_equal(config[field], expected, "the mode switch changed " .. field)
    end
end

local function prove_manage_saves(controller)
    local widget_class = StaticFindObject(manage_path)
    if not valid(widget_class) then
        LoadAsset(manage_path)
        widget_class = StaticFindObject(manage_path)
    end
    local library = StaticFindObject(widgets_path)
    require_that(valid(widget_class) and valid(library), "stock Manage Saves widget unavailable")
    local screen = library:Create(controller, widget_class, controller)
    require_that(valid(screen) and screen:GetClass():GetAddress() == widget_class:GetAddress(), "wrong Manage Saves widget")
    local observed = routes[mode].find
    screen:Setup()
    if routes[mode].find == observed then screen:AddToViewport(0) end
    require_that(routes[mode].find > observed, "stock Manage Saves did not enumerate saves")
    screen:RemoveFromParent()
end

local function prepare(controller, instance, statics, save_class, stock_save)
    local config = instance.GamePlay_Config
    require_that(valid(config) and config:GetClass():GetFullName() == config_path, "stock settings object unavailable")
    for field, value in pairs(preferences) do config[field] = value end
    require_that(statics:SaveGameToSlot(config, "Config", 0), "the stock settings slot did not save")
    check_preferences(statics:LoadGameFromSlot("Config", 0))
    snapshot_save("config-before", profile .. "/Config.sav")
    local canary = statics:CreateSaveGameObject(save_class)
    require_that(valid(canary) and (not valid(stock_save) or canary:GetAddress() ~= stock_save:GetAddress()),
        "stock canary save unavailable")
    active = true
    instance.SaveSlot_Ref = canary
    canary.Level = "CrossRoads_P"
    canary.PlayerHealth = 119
    instance:Save_GameState(slot, true)
    local solo_bytes = snapshot_save("solo-before", profile .. "/" .. slot .. ".sav")
    recent(instance, 119)

    mode = "coop"
    recent(instance, nil)
    canary.Level = "Observitory_P"
    canary.PlayerHealth = 202
    instance:Save_GameState(slot, true)
    local coop_bytes = snapshot_save("coop-before-delete", coop_profile .. "/" .. slot .. ".sav")
    require_that(solo_bytes ~= coop_bytes, "solo and co-op saves contain identical data")
    recent(instance, 202)
    prove_manage_saves(controller)
    pending = {instance = instance, stock_save = stock_save, slot_name = text(instance.CurrentSaveSlot), solo_bytes = solo_bytes}
    instance.CurrentSaveSlot = slot
    statics:OpenLevel(controller, FName(loading_package), true, "")
end

local function verify_loaded(statics, world)
    local instance = statics:GetGameInstance(world)
    require_that(valid(instance) and instance:GetAddress() == pending.instance:GetAddress(),
        "stock GameInstance changed during loading")
    require_that(valid(instance.SaveSlot_Ref), "the stock loading map did not adopt a save")
    assert_equal(routes.coop.async_load, 1, "stock asynchronous load count")
    assert_equal(text(instance.SaveSlot_Ref.Level), "Observitory_P", "stock asynchronous save level")
    assert_equal(instance.SaveSlot_Ref.PlayerHealth, 202, "stock asynchronous save health")
    instance:Clear_SaveSlot(slot)
    recent(instance, nil)

    mode = "solo"
    recent(instance, 119)
    require_that(pending.solo_bytes == slurp(profile .. "/" .. slot .. ".sav"), "co-op altered solo save bytes")
    check_preferences(instance.GamePlay_Config)
    check_preferences(statics:LoadGameFromSlot("Config", 0))
    snapshot_save("config-after", profile .. "/Config.sav")
    instance.CurrentSaveSlot = pending.slot_name
    instance.SaveSlot_Ref = pending.stock_save
end

LoopInGameThreadWithDelay(75, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) then return end
    if pending then
        if world:GetFullName() ~= destination_world then return end
        local statics = StaticFindObject(statics_path)
        if not valid(statics) then return end
        local ok, failure = pcall(verify_loaded, statics, world)
        active, mode, pending = false, "solo", nil
        report(ok and "verified" or "failed", failure or "stock save menus, asynchronous load and local settings shared one process")
        return true
    end
    if ran then return true end
    if world:GetFullName() ~= stock_world then return end
    local title = FindFirstOf(title_class)
    if not valid(title) or not title:IsInViewport() or not title:IsVisible() then return end
    local statics, system = StaticFindObject(statics_path), StaticFindObject(system_path)
    if not valid(statics) or not valid(system) then return end
    local controller = statics:GetPlayerController(world, 0)
    if not valid(controller) then return end
    local instance = statics:GetGameInstance(controller)
    local stock_save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) then return end
    local config = io.open(profile .. "/Config.sav", "rb")
    if not config then return end
    local contents = config:read("*a")
    config:close()
    if contents == config_candidate then
        stable_samples = stable_samples + 1
    else
        config_candidate, stable_samples = contents, 0
    end
    -- The stock title can write Config again after initially creating the slot.
    if stable_samples < 10 then return end
    ran = true
    local ok, failure = pcall(function()
        local save_class = StaticFindObject(save_path)
        if not valid(save_class) then LoadAsset(save_path); save_class = StaticFindObject(save_path) end
        require_that(instance:GetClass():GetFullName() == "BlueprintGeneratedClass " .. instance_path
            and valid(save_class), "unexpected stock GameInstance or SaveGame class")
        local actual = normalized(text(system:GetProjectSavedDirectory()) .. "/SaveGames")
        require_that(actual == normalized(profile), "unexpected stock saved directory: " .. actual)
        prepare(controller, instance, statics, save_class, stock_save)
    end)
    if not ok then
        active, mode, pending = false, "solo", nil
        instance.SaveSlot_Ref = stock_save
        report("failed", failure)
        return true
    end
end)
