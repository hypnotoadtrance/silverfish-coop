local pause = {}

local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_PAUSE_FIXTURES") == "1"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local pause_class = "/Game/Classes/GUI/Menu_Pause_Widget.Menu_Pause_Widget_C"
local confirmation_class = "/Game/Classes/GUI/New/ConfirmationScreen.ConfirmationScreen_C"
local set_paused = "/Script/Engine.GameplayStatics:SetGamePaused"
local prompt_press = "BndEvt__Menu_Pause_Widget_Button_Continue_1_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature"
local save_press = "BndEvt__Menu_Pause_Widget_Button_SaveAndQuit_K2Node_ComponentBoundEvent_5_Pressed__DelegateSignature"
local confirm_press = "BndEvt__ConfirmationScreen_Button_Confirm_K2Node_ComponentBoundEvent_2_Pressed__DelegateSignature"
local cancel_press = "BndEvt__ConfirmationScreen_Button_Cancel_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature"
local fixture_command = "native-coop-pause-fixture"
local fixture_actions = {open = true, running = true, prompt = true, cancel = true, confirm = true, save_and_quit = true}
local steam_host_path, steam_request_path, steam_invite
local fixture_command_path, fixture_reply_path, fixture_previous, fixture_pending
local fixture_seen, fixture_count = {}, 0
local generation, fixture_menu, fixture_confirmation, fixture_status, fixture_diagnostic
local active_scene

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function token(value) return value and #value == 32 and value:match("^[0-9a-f]+$") and value:find("[^0]") end

local function pointer(object)
    return valid(object) and math.tointeger(object:GetAddress()) or nil
end

local function context(state, phase)
    local role = phase == "hosting" and "host" or phase == "connected" and "guest" or nil
    if not role or state.listening ~= true or state.server ~= (role == "guest") then return end
    local world, controller = state.world, state.controller
    local pawn = state.local_pawn and state.local_pawn.pawn
    local leaf = type(state.map) == "string" and state.map:match("^/Game/Classes/Maps/.+")
        and state.map:match("/([^/]+)$")
    if not valid(world) or not leaf or world:GetFullName() ~= "World " .. state.map .. "." .. leaf
        or not exact(controller, controller_class) or not exact(pawn, pawn_class)
        or controller.Loading ~= false or not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or controller:HasAuthority() ~= (role == "host") or pawn:HasAuthority() ~= (role == "host")
        or controller:IsActorBeingDestroyed() or pawn:IsActorBeingDestroyed()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(controller:GetWorld(), world) or not same(pawn:GetWorld(), world) then return end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world)
        or (valid(driver.ServerConnection) and true or false) ~= (role == "guest") then return end
    local world_id, controller_id, pawn_id = pointer(world), pointer(controller), pointer(pawn)
    if not world_id or not controller_id or not pawn_id then return end
    return {world = world, driver = driver, controller = controller, pawn = pawn, role = role,
        world_id = world_id, controller_id = controller_id, pawn_id = pawn_id}
end

local function keep_world_running(_, world_context, paused)
    local scene = active_scene
    if not scene or paused:get() ~= true or not valid(scene.world) or not valid(scene.controller)
        or not same(scene.world.NetDriver, scene.driver) or not same(scene.controller.Pawn, scene.pawn)
        or not same(scene.controller:GetWorld(), scene.world) then return end
    local menu = world_context:get()
    if exact(menu, pause_class) and same(menu:GetOwningPlayer(), scene.controller)
        and same(menu:GetOuter(), scene.world.OwningGameInstance) then
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if valid(statics) and statics:IsGamePaused(scene.world) == false then paused:set(false) end
    end
end

local function read(path, limit)
    local file = io.open(path, "rb")
    if not file then return end
    local data = file:read(limit + 1)
    file:close()
    if data and #data <= limit then return data end
end

local function exists(path)
    local file = io.open(path, "rb")
    if not file then return false end
    file:close()
    return true
end

local function publish(path, record, replace)
    if not replace and exists(path) then return false end
    local temporary = path .. ".new"
    local file = io.open(temporary, "wb")
    if not file then return false end
    local written = file:write(record)
    local closed = file:close()
    if written and closed then
        if replace then os.remove(path) end
        if (replace or not exists(path)) and os.rename(temporary, path) then return true end
    end
    os.remove(temporary)
    return false
end

local function clear_invite()
    if steam_invite then
        steam_invite.controls.remove(steam_invite.button)
        steam_invite = nil
    end
end

function pause.clear()
    active_scene = nil
    if embedded and type(SilverfishBindStockPauseExit) == "function" then SilverfishBindStockPauseExit() end
    clear_invite()
    fixture_menu, fixture_confirmation, fixture_status, fixture_diagnostic = nil, nil, nil, nil
end

local function qualified(scene)
    if not scene or not embedded or type(SilverfishBindStockPauseExit) ~= "function" then return end
    local current, press = SilverfishBindStockPauseExit(scene.role == "host" and 1 or 2,
        scene.world_id, scene.controller_id, scene.pawn_id)
    if not current then return end
    if generation ~= current then
        generation = current
        fixture_menu, fixture_confirmation, fixture_status, fixture_diagnostic = nil, nil, nil, nil
    end
    scene.generation, scene.press = current, press
    return scene
end

local function owned_pause(value, scene)
    return exact(value, pause_class) and same(value:GetOwningPlayer(), scene.controller)
        and same(value:GetOuter(), scene.world.OwningGameInstance) and value:IsInViewport()
end

local steam_views = {
    ready = {label = "Invite Steam Friends", hint = "Open the Steam friends list."},
    friends = {label = "Steam friends can join",
        hint = "Friends can use Join Game in Steam. Select here to retry the invite."},
    opening = {label = "Opening Steam…", hint = "Waiting for Steam."},
    retry = {label = "Retry Steam Lobby", hint = "Steam could not create the lobby. Select here to retry."},
    overlay = {label = "Steam invites unavailable", hint = "Check the Steam overlay, then select here to retry."},
}

local function can_invite(status)
    return status and status ~= "opening"
end

local function steam_status(scene)
    local data = read(steam_host_path, 80)
    if not data then return end
    local generation, status = data:match("^1\t([0-9a-f]+)\t([a-z_]+)\n$")
    if generation == scene.generation and steam_views[status] then return status end
end

local function top_pause(scene)
    local stack = scene.controller.MenuStack
    local menu = stack and stack:GetArrayNum() == 1 and stack[1]
    if owned_pause(menu, scene) and menu:IsVisible() then return menu end
end

local function sync_steam_invite(scene)
    local menu = scene.role == "host" and scene.press == 0 and top_pause(scene)
    local status = menu and steam_status(scene)
    if not status then clear_invite(); return end
    if steam_invite and (not same(steam_invite.menu, menu) or steam_invite.generation ~= scene.generation) then
        clear_invite()
    end
    if not steam_invite then
        local anchor = menu.Button_SaveAndQuit
        local parent = valid(anchor) and anchor:GetParent()
        if not exact(parent, "/Script/UMG.VerticalBox") then return end
        local controls = require("native_menu_controls")
        local record = {controls = controls, menu = menu, generation = scene.generation}
        steam_invite = record
        local added, button = pcall(controls.create, scene, parent, steam_views[status].label, function()
            local latest = qualified(active_scene)
            if steam_invite ~= record or not latest or latest.press ~= 0 or latest.generation ~= record.generation
                or not can_invite(steam_status(latest)) or not same(top_pause(latest), menu) then return end
            publish(steam_request_path, "1\t" .. record.generation .. "\tinvite\n", false)
        end)
        if added then record.button = button end
    end
    if steam_invite.button and steam_invite.status ~= status then
        local widget = steam_invite.button.widget
        local view = steam_views[status]
        widget:UpdateText(FText(view.label))
        widget:SetToolTipText(FText(view.hint))
        widget:SetIsEnabled(can_invite(status))
        steam_invite.status = status
    end
end

local function fixture_reply(nonce, ok, message)
    local bounded = tostring(message):gsub("[^ -~]", "?"):sub(1, 512)
    return table.concat({"1", nonce, ok and "ok" or "error", bounded}, "\t") .. "\n"
end

local function service_fixture(state, phase)
    if not fixture_command_path then return end
    if fixture_pending then
        if not publish(fixture_reply_path, fixture_pending, true) then return end
        fixture_pending = nil
    end
    local data = read(fixture_command_path, 128)
    if data == fixture_previous then return end
    fixture_previous = data
    if not data then return end
    local nonce = data:match("^1\t([0-9a-f]+)\t")
    if not token(nonce) then return end
    local action = data:match("^1\t[0-9a-f]+\t([a-z_]+)\n$")
    local ok, message = false, "native_coop_pause_fixture_duplicate_nonce"
    if not fixture_seen[nonce] then
        if fixture_count >= 128 then
            message = "native_coop_pause_fixture_nonce_limit"
        else
            fixture_seen[nonce], fixture_count = true, fixture_count + 1
            if fixture_actions[action] then
                ok, message = pcall(pause.command, {nonce, fixture_command, action}, state, phase)
                if type(message) ~= "string" then ok, message = false, "native_coop_pause_fixture_unverified_reply" end
            else
                message = "native_coop_pause_fixture_arguments"
            end
        end
    end
    fixture_pending = fixture_reply(nonce, ok, message)
    if publish(fixture_reply_path, fixture_pending, true) then fixture_pending = nil end
end

function pause.sync(state, phase)
    local scene = context(state, phase)
    local managed = qualified(scene)
    if managed then
        sync_steam_invite(managed)
    elseif embedded then
        pause.clear()
    end
    active_scene = scene
    service_fixture(state, phase)
end

local function reject(reason) error("native_coop_pause_fixture_" .. reason, 0) end

local function class_label(class)
    local label = valid(class) and class:GetFullName():match("([^./: ]+)$") or "-"
    return label:sub(1, 48):gsub("[^%w_%-]", "?")
end

local function confirmation(stack, scene)
    local count = stack and stack:GetArrayNum()
    local anchored = count == 2 and same(stack[1], fixture_menu)
    local top = count and count > 0 and stack[count]
    local class = valid(top) and top:GetClass()
    local owner = valid(top) and top:GetOwningPlayer()
    local outer = valid(top) and top:GetOuter()
    local instance = scene.world.OwningGameInstance
    local owned = same(owner, scene.controller) and "local" or valid(owner) and "other" or "unbound"
    local abode = same(outer, instance) and "instance" or same(outer, scene.world) and "world" or valid(outer) and "other" or "unbound"
    local mounted, visible = valid(top) and top:IsInViewport() == true, valid(top) and top:IsVisible() == true
    local dialog = exact(top, confirmation_class)
    local owner_qualified = owned == "local" or owned == "unbound" and (abode == "instance" or abode == "world")
    local same_world = false
    if anchored and dialog and owner_qualified then
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        same_world = valid(statics) and same(statics:GetGameInstance(top), instance)
            and same(statics:GetPlayerController(top, 0), scene.controller)
    end
    local outer_class = abode == "other" and class_label(outer:GetClass()) or "-"
    fixture_diagnostic = table.concat({"count=" .. tostring(count or "-"), "pause=" .. tostring(anchored and true or false),
        "class=" .. class_label(class), "owner=" .. owned, "outer=" .. abode, "outer_class=" .. outer_class,
        "world=" .. tostring(same_world and true or false),
        "viewport=" .. tostring(mounted and true or false), "visible=" .. tostring(visible and true or false)}, ":")
    if same_world and mounted and visible then return top end
end

function pause.command(fields, state, phase)
    if fields[2] ~= fixture_command then return end
    if not embedded or not fixtures then reject("unavailable") end
    local action = fields[3]
    if #fields ~= 3 or not fixture_actions[action] then reject("arguments") end
    local scene = qualified(context(state, phase))
    if not scene or scene.press ~= 0 then reject("scope_unavailable") end
    local stack = scene.controller.MenuStack
    local count = stack and stack:GetArrayNum()
    if action == "open" then
        fixture_confirmation, fixture_diagnostic = nil, nil
        if count ~= 0 then reject("stock_menu_open") end
        local class = StaticFindObject(pause_class)
        if not valid(class) then LoadAsset(pause_class); class = StaticFindObject(pause_class) end
        if not valid(class) then reject("stock_pause_class") end
        local created = {}
        scene.controller:OpenMenu(class, created)
        local menus = scene.controller.MenuStack
        if not menus or menus:GetArrayNum() ~= 1 or not owned_pause(menus[1], scene)
            or not menus[1]:IsVisible() or not same(menus[1], created.CreatedMenu) then reject("stock_pause_open_unverified") end
        fixture_menu = menus[1]
    elseif not count or not owned_pause(fixture_menu, scene) or not same(stack[1], fixture_menu) then
        reject("exact_stock_pause_unavailable")
    elseif action == "running" then
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if count ~= 1 or not fixture_menu:IsVisible() or not valid(statics) then reject("stock_pause_covered") end
        if statics:IsGamePaused(scene.world) ~= false then reject("world_paused") end
    elseif action == "prompt" then
        if count ~= 1 or not fixture_menu:IsVisible() then reject("stock_pause_covered") end
        fixture_menu[prompt_press](fixture_menu)
        fixture_confirmation = confirmation(scene.controller.MenuStack, scene)
        if not fixture_confirmation then reject("stock_confirmation_unverified:" .. fixture_diagnostic) end
    elseif action == "save_and_quit" then
        if count ~= 1 or not fixture_menu:IsVisible() or not valid(fixture_menu.Button_SaveAndQuit)
            or not fixture_menu.Button_SaveAndQuit:IsVisible()
            or fixture_menu:Get_Button_SaveAndQuit_bIsEnabled() ~= true then reject("stock_save_and_quit_unavailable") end
        fixture_menu[save_press](fixture_menu)
    else
        local confirm = confirmation(stack, scene)
        if not same(confirm, fixture_confirmation) or confirm.Dismissed ~= false then
            reject("stock_confirmation_unavailable:" .. fixture_diagnostic)
        end
        local press = action == "confirm" and confirm_press or cancel_press
        confirm[press](confirm)
        fixture_confirmation = nil
    end
    fixture_status = action
    return "exact stock co-op pause " .. action
end

function pause.rows(state, phase, row)
    local scene = active_scene
    local role = phase == "hosting" and "host" or phase == "connected" and "guest"
    if not scene or not scene.generation or scene.role ~= role or not same(scene.world, state.world)
        or not same(scene.controller, state.controller) or not same(scene.pawn, state.local_pawn and state.local_pawn.pawn) then
        return {}
    end
    local lines = {row("native-coop-pause", scene.role, scene.generation, scene.press ~= 0 and "requested" or "armed", scene.press)}
    if fixture_status then lines[#lines + 1] = row("native-coop-pause-fixture", bound.role, fixture_status) end
    if fixture_diagnostic then lines[#lines + 1] = row("native-coop-pause-fixture-diagnostic", bound.role, fixture_diagnostic) end
    return lines
end

function pause.start(bridge)
    RegisterHook(set_paused, keep_world_running)
    if not embedded then return end
    steam_host_path = bridge .. "/steam-coop-host.tsv"
    steam_request_path = bridge .. "/steam-coop-invite-request.tsv"
    if fixtures then
        fixture_command_path = bridge .. "/native-coop-pause-fixture-command.tsv"
        fixture_reply_path = bridge .. "/native-coop-pause-fixture-reply.tsv"
        os.remove(fixture_command_path)
        os.remove(fixture_reply_path)
    end
end

return pause
