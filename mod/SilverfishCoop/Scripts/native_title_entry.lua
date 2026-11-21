local title = {}

local map = "/Game/Classes/Maps/MainMenu_P"
local world_name = "World " .. map .. ".MainMenu_P"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local mode_path = "/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C"
local widget_path = "/Game/Classes/GUI/MainMenu_Widget.MainMenu_Widget_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local viewport_path = "/Script/Engine.GameViewportClient"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local inspect_save_directory = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
    or os.getenv("SILVERFISH_COOP_INSPECT_TITLE_PROFILE") == "1"

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), StaticFindObject(class))
end

local function observe(state, phase)
    if state.map ~= map or (phase ~= "opening" and phase ~= "ready") then return end
    local report = {map = map, state = "waiting", world = state.world}
    local function wait_for(reason)
        report.reason = reason
        return report
    end

    local world, controller = state.world, state.controller
    if state.listening or state.server ~= false or state.clients ~= 0 or state.local_pawn then
        return wait_for("standalone_title_required")
    end
    if not valid(world) or world:GetFullName() ~= world_name or valid(world.NetDriver) then
        return wait_for("stock_world_unavailable")
    end
    if not exact(controller, controller_path) or not controller:IsLocalController()
        or not controller:HasAuthority() or not same(controller:GetWorld(), world) then
        return wait_for("stock_controller_unavailable")
    end
    report.controller = controller

    local statics = StaticFindObject(statics_path)
    local mode = valid(statics) and statics:GetGameMode(controller)
    if not exact(mode, mode_path) or not same(mode:GetWorld(), world) then
        return wait_for("stock_menu_mode_unavailable")
    end
    local instance = statics:GetGameInstance(controller)
    if not exact(instance, instance_path) then return wait_for("stock_game_instance_unavailable") end

    local viewport = FindFirstOf("GameViewportClient")
    if not exact(viewport, viewport_path) or not same(viewport.World, world)
        or not same(viewport:GetOuter(), instance:GetOuter()) then
        return wait_for("stock_viewport_unavailable")
    end
    report.viewport = viewport

    local stack = controller.MenuStack
    local count = stack and math.tointeger(stack:GetArrayNum())
    if not count or count < 0 or count > 32 then return wait_for("stock_menu_stack_unqualified") end
    report.count = count
    local class = StaticFindObject(widget_path)
    if not valid(class) then return wait_for("stock_menu_class_unavailable") end

    -- Stock ActiveMenu reads the last element of this same array.
    local menu, index
    for position = 1, count do
        local candidate = stack[position]
        if valid(candidate) and same(candidate:GetClass(), class) then
            if menu then return wait_for("stock_menu_ambiguous") end
            menu, index = candidate, position
        end
    end
    if not menu then return wait_for("stock_menu_not_created") end
    if not same(menu:GetOwningPlayer(), controller) or not same(menu:GetOuter(), instance) then
        return wait_for("stock_menu_owner_unqualified")
    end
    report.menu, report.top = menu, index == count
    report.mounted, report.visible = menu:IsInViewport(), menu:IsVisible()
    if type(report.mounted) ~= "boolean" or type(report.visible) ~= "boolean" then
        return wait_for("stock_menu_visibility_unqualified")
    end
    if not report.top then return wait_for("stock_menu_below_dialog") end
    if not report.mounted then return wait_for("stock_menu_not_mounted") end
    if not report.visible then return wait_for("stock_menu_hidden") end
    if phase ~= "ready" then return wait_for("stock_title_opening") end

    report.state = "ready"
    return report
end

function title.probe(state, phase)
    local report = observe(state, phase)
    if report and report.state == "ready" then return report end
end

function title.refresh_singleplayer(context)
    local instance = context.world.OwningGameInstance
    if require("native_save_scope").selected() or not exact(instance, instance_path) then
        error("title_stock_save_scope_unavailable", 0)
    end
    local recent = {}
    instance:Find_MostRecentSaveFile(recent, recent)
    if type(recent.SaveFileFound) ~= "boolean" or instance.SaveFileExists ~= recent.SaveFileFound
        or recent.SaveFileFound and not exact(recent.Save, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") then
        error("title_stock_save_refresh_failed", 0)
    end
    -- The stock title normally does this on BeginPlay, which can run while
    -- the previous co-op save scope is still selected.
    instance.SaveSlot_Ref = recent.SaveFileFound and recent.Save or nil
    instance.CurrentSaveSlot = ""
end

local function name(object)
    return object and object:GetFullName() or "-"
end

local function bit(value)
    if type(value) ~= "boolean" then return "-" end
    return value and 1 or 0
end

local function string_value(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function saved_directory()
    local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(system) then return end
    local path = string_value(system:GetProjectSavedDirectory())
    if type(path) ~= "string" or path == "" or path:find("%c") then return end
    if not path:match("^%a:[/\\]") and not path:match("^[/\\][/\\]") then
        local paths = StaticFindObject("/Script/Engine.Default__BlueprintPathsLibrary")
        if not valid(paths) then return end
        path = string_value(paths:ConvertRelativePathToFull(path, ""))
    end
    if type(path) == "string" and #path <= 4096 and not path:find("%c")
        and (path:match("^%a:[/\\]") or path:match("^[/\\][/\\]")) then return path end
end

function title.rows(state, phase, status_row, idle_sequence)
    local report = observe(state, phase)
    if not report then return {} end
    local rows = {status_row("native-title-entry", report.map, report.state, name(report.controller), name(report.menu),
        name(report.viewport), report.count or "-", bit(report.top), bit(report.mounted), bit(report.visible),
        report.reason or "-")}
    if inspect_save_directory and idle_sequence and report.state == "ready" then
        local ok, path = pcall(saved_directory)
        if ok and path then rows[#rows + 1] = status_row("embedded-engine-startup", map, idle_sequence, path) end
    end
    return rows
end

return title
