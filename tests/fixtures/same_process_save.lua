-- Test-only UE4SS entry for an isolated stock title.
local root = os.getenv("SILVERFISH_SAME_PROCESS_SAVE_PROOF")
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local grouped = root == nil
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then return end
if grouped then
    if os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
        or os.getenv("SILVERFISH_COOP_INSTALLED_TITLE") ~= "1" or not mailbox
        or not mailbox:match("[\\/]portable%-title%-%x+[\\/].+[\\/]game%-bridge[\\/]%x+$") then return end
elseif not root:match("[\\/]same%-save%-%x+$") then return end
local save_scope = require("native_save_scope")

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
local profile = root and root .. "/game-user/Saved/SaveGames"
local scope_id, other_id = string.rep("a", 32), string.rep("b", 32)
local coop_profile = profile and profile .. "/SilverfishCoop/" .. scope_id
local other_profile = profile and profile .. "/SilverfishCoop/" .. other_id
local mode, ran, active = "solo", false, false
local routes = {solo = {}, coop = {}, other = {}}
local config_candidate, stable_samples = nil, 0
local pending, pending_other, original_state
local native_routes = {select = 0, release = 0, wrong_release = 0}
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
    for _, side in ipairs({"solo", "coop", "other"}) do
        for _, name in ipairs({"save", "load", "exists", "delete", "find", "async_load", "async_save"}) do
            assert(stream:write(side, "\t", name, "\t", tostring(routes[side][name] or 0), "\n"))
        end
    end
    if grouped then
        for _, name in ipairs({"select", "release", "wrong_release"}) do
            assert(stream:write("native\t", name, "\t", tostring(native_routes[name]), "\n"))
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

local function observe_slot(name, parameter, user)
    if not active or user:get() ~= 0 then return end
    local actual = text(parameter:get())
    local scope = mode == "coop" and scope_id or mode == "other" and other_id
    if actual == slot or scope and type(actual) == "string"
        and actual:gsub("\\\\", "/") == "SilverfishCoop/" .. scope .. "/" .. slot then count(name) end
end

RegisterHook("/Script/Engine.GameplayStatics:SaveGameToSlot", function(_, _, parameter, user)
    observe_slot("save", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:LoadGameFromSlot", function(_, parameter, user)
    observe_slot("load", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:DoesSaveGameExist", function(_, parameter, user)
    observe_slot("exists", parameter, user)
end)
RegisterHook("/Script/Engine.GameplayStatics:DeleteGameInSlot", function(_, parameter, user)
    observe_slot("delete", parameter, user)
end)
RegisterHook("/Script/Engine.AsyncActionHandleSaveGame:AsyncLoadGameFromSlot", function(_, context, parameter, user)
    if not pending or mode ~= "coop" then return end
    local actor = context:get()
    local world = valid(actor) and actor:GetWorld()
    if valid(world) and world:GetFullName() == loading_world then observe_slot("async_load", parameter, user) end
end)
RegisterHook("/Script/Engine.AsyncActionHandleSaveGame:AsyncSaveGameToSlot", function(_, _, _, parameter, user)
    observe_slot("async_save", parameter, user)
end)
RegisterHook("/Script/BlueprintFileUtils.BlueprintFileUtilsBPLibrary:FindFiles", function(_, directory, _, extension)
    if not active or text(extension:get()) ~= ".sav" then return end
    local actual = text(directory:get())
    if type(actual) ~= "string" then return end
    actual = normalized(actual)
    local selected_profile = mode == "coop" and coop_profile or mode == "other" and other_profile or profile
    if actual == normalized(profile) or actual == normalized(selected_profile) then count("find") end
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

local function assert_native_scope(id)
    if grouped then assert_equal(SilverfishSelectedNativeSaveScope(), id, "native selected save scope") end
end

local function select_scope(id)
    save_scope.select(id)
    assert_native_scope(id)
    native_routes.select = native_routes.select + 1
end

local function release_scope(id)
    save_scope.release(id)
    assert_native_scope(nil)
    native_routes.release = native_routes.release + 1
end

local function prepare(controller, instance, statics, save_class, stock_save)
    local config = instance.GamePlay_Config
    require_that(valid(config) and config:GetClass():GetFullName() == config_path, "stock settings object unavailable")
    original_state = {instance = instance, stock_save = stock_save, slot_name = text(instance.CurrentSaveSlot),
                      autosave = instance.SaveAfterTransition, save_exists = instance.SaveFileExists}
    if grouped then
        assert_native_scope(nil)
        require_that(not statics:DoesSaveGameExist(slot, 0), "the temporary solo slot already exists")
        original_state.verified_new_slot = true
        for field in pairs(preferences) do preferences[field] = config[field] end
    else
        for field, value in pairs(preferences) do config[field] = value end
        require_that(statics:SaveGameToSlot(config, "Config", 0), "the stock settings slot did not save")
    end
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

    select_scope(scope_id)
    mode = "coop"
    recent(instance, nil)
    canary.Level = "Observitory_P"
    canary.PlayerHealth = 202
    instance:Save_GameState(slot, true)
    local coop_bytes = snapshot_save("coop-before-delete", coop_profile .. "/" .. slot .. ".sav")
    require_that(solo_bytes ~= coop_bytes, "solo and co-op saves contain identical data")
    recent(instance, 202)
    check_preferences(statics:LoadGameFromSlot("Config", 0))
    local escaped = grouped and slot .. "_Escape" or "ScopeEscape"
    require_that(not statics:SaveGameToSlot(canary, "../../" .. escaped, 0), "an invalid save slot was accepted")
    local wrong_release = pcall(save_scope.release, other_id)
    require_that(not wrong_release, "another scope released the active campaign")
    assert_native_scope(scope_id)
    native_routes.wrong_release = native_routes.wrong_release + 1
    release_scope(scope_id)
    select_scope(other_id)
    mode = "other"
    recent(instance, nil)
    canary.Level = "DeadBridge_P"
    canary.PlayerHealth = 303
    local async = StaticFindObject("/Script/Engine.Default__AsyncActionHandleSaveGame")
    local request = valid(async) and async:AsyncSaveGameToSlot(controller, canary, slot, 0)
    require_that(valid(request), "stock save action was unavailable")
    request:Activate()
    pending_other = {instance = instance, solo_bytes = solo_bytes}
end

local function finish_other(statics, world)
    local file = io.open(other_profile .. "/" .. slot .. ".sav", "rb")
    if not file then return end
    local data = file:read("*a")
    file:close()
    if #data <= 16 then return end
    local instance = pending_other.instance
    local controller = statics:GetPlayerController(world, 0)
    recent(instance, 303)
    snapshot_save("other-after-async-save", other_profile .. "/" .. slot .. ".sav")
    release_scope(other_id)
    select_scope(scope_id)
    mode = "coop"
    recent(instance, 202)
    prove_manage_saves(controller)
    pending, pending_other = pending_other, nil
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
    local controller = statics:GetPlayerController(world, 0)
    require_that(valid(controller), "the loaded campaign controller was unavailable")
    pending.returning = true
    statics:OpenLevel(controller, FName("/Game/Classes/Maps/MainMenu_P"), true, "")
end

local function verify_returned(statics)
    local instance = pending.instance
    release_scope(scope_id)
    mode = "solo"
    recent(instance, 119)
    require_that(pending.solo_bytes == slurp(profile .. "/" .. slot .. ".sav"), "co-op altered solo save bytes")
    check_preferences(instance.GamePlay_Config)
    check_preferences(statics:LoadGameFromSlot("Config", 0))
    snapshot_save("config-after", profile .. "/Config.sav")
end

local function restore_original(statics)
    if not original_state then return end
    local previous = original_state
    local instance = previous.instance
    if grouped then
        local selected = save_scope.selected()
        if selected == scope_id or selected == other_id then release_scope(selected) end
        assert_native_scope(nil)
        if previous.verified_new_slot and statics:DoesSaveGameExist(slot, 0) then
            require_that(statics:DeleteGameInSlot(slot, 0), "temporary solo save could not be removed")
        end
    end
    instance.CurrentSaveSlot = previous.slot_name
    if grouped and not valid(previous.stock_save) and previous.save_exists then
        local recent_save = {}
        instance:Find_MostRecentSaveFile(recent_save, recent_save)
        instance.SaveSlot_Ref = valid(recent_save.Save) and recent_save.Save or nil
    else
        instance.SaveSlot_Ref = valid(previous.stock_save) and previous.stock_save or nil
    end
    instance.SaveAfterTransition = previous.autosave
    instance.SaveFileExists = previous.save_exists
end

local function finish(statics, ok, failure)
    active, mode = false, "solo"
    local cleaned, cleanup_failure = pcall(restore_original, statics)
    if not cleaned then failure = (failure and tostring(failure) .. " / " or "") .. tostring(cleanup_failure) end
    pending, pending_other = nil, nil
    report(ok and cleaned and "verified" or "failed", failure or "stock save menus, asynchronous save and load shared one process")
    return true
end

local function tick()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) then return end
    if pending then
        if pending.returning then
            local title = world:GetFullName() == stock_world and FindFirstOf(title_class)
            local statics = StaticFindObject(statics_path)
            if not valid(title) or not title:IsInViewport() or not title:IsVisible() or not valid(statics) then return end
            return finish(statics, pcall(verify_returned, statics))
        end
        if world:GetFullName() ~= destination_world then return end
        local statics = StaticFindObject(statics_path)
        if not valid(statics) then return end
        local ok, failure = pcall(verify_loaded, statics, world)
        if not ok then return finish(statics, false, failure) end
        return
    end
    if pending_other then
        local statics = StaticFindObject(statics_path)
        if not valid(statics) or world:GetFullName() ~= stock_world then return end
        local ok, failure = pcall(finish_other, statics, world)
        if not ok then return finish(statics, false, failure) end
        return
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
    if not ok then return finish(statics, false, failure) end
end

if grouped then
    function SilverfishStartSameProcessSaveProof(request, first, second, expected_profile)
        local function token(value)
            return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") and value:find("[1-9a-f]")
        end
        require_that(root == nil and token(request) and token(first) and token(second) and first ~= second
            and type(expected_profile) == "string" and expected_profile:match("^%a:[/\\\\]"),
            "invalid private native save proof request")
        require_that(type(SilverfishSelectedNativeSaveScope) == "function"
            and type(SilverfishSelectNativeSaveScope) == "function" and type(SilverfishReleaseNativeSaveScope) == "function"
            and not save_scope.selected(), "native save routing is unavailable")
        assert_native_scope(nil)
        root, profile, scope_id, other_id = mailbox .. "/same-save-" .. request, expected_profile, first, second
        coop_profile, other_profile = profile .. "/SilverfishCoop/" .. first, profile .. "/SilverfishCoop/" .. second
        slot = "SFSaveCanary_" .. request
        LoopInGameThreadWithDelay(75, tick)
        return "armed native stock save proof"
    end
else
    LoopInGameThreadWithDelay(75, tick)
end
