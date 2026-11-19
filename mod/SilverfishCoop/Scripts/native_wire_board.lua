local board_view = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local wire, computer, photo = "Clt_Wire", "Clt_ComputerBoard", "Photo_DB_Turrets"
local report, depot = "Clt_CoupReport_01", "DepotDocs"
local function board_profile(area, level, suffix, id, quests, display_global)
    local world = area .. "." .. level
    local path = world .. ":PersistentLevel.BountyBoard_BP_C_" .. suffix
    return {map = area, world = "World " .. world, path = path, name = "BountyBoard_BP_C " .. path,
        id = id, quests = quests, display_global = display_global == true}
end
local profiles = {
    [borough] = board_profile(borough, "Borough", "3", 5, {wire, computer, photo, report}),
    [scientist] = board_profile(scientist, "Shop_Scientist_01", "2", 4, {computer}),
    [scav_bar] = board_profile(scav_bar, "Shop_ScavBar_P", "2", 10, {depot}, true),
}
local titles = {[wire] = "Copper Stop", [computer] = "Find a Computer Board", [photo] = "Dead Bridge Turret Photos",
    [report] = "Northern Passage", [depot] = "Depot Documents"}
local board_class = "/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local box_class = "/Script/Engine.BoxComponent"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local max_sequence = 2 ^ 53
local prompt = {
    focused = "Interact to view the contract.",
    loading = "Checking the party's contract...",
    starting = "Accepting the party contract...",
    redeeming = "Turning in the party contract...",
    completed = "This party has completed the contract.",
    unavailable = "The board could not verify this action.\nInteract to check the contract again.",
    not_offered = "This contract is currently unavailable on this board.\nInteract to check again.",
    elsewhere = "Deliver one Computer Board to the Scientist bounty board.",
}
local contract_prompt = {
    [wire] = {
        offer = "Bring Copper Wire to this board.\nInteract again to accept the contract.",
        missing = "Bring one Copper Wire to this board.\nInteract to check the contract again.",
        ready = "Turn in one Copper Wire to finish the contract.\nInteract again to turn it in.",
    },
    [computer] = {
        offer = "Deliver a Computer Board to the Scientist bounty board.\nInteract again to accept the contract.",
        missing = "Bring one Computer Board in your backpack.\nInteract to check the contract again.",
        ready = "Turn in your Computer Board for 800 shared cash.\nInteract again to turn it in.",
    },
    [photo] = {
        offer = "Use a Camera to photograph two turrets in Dead Bridge.\nInteract again to accept the contract.",
        missing = "Photograph the two turrets in Dead Bridge.\nInteract to check the contract again.",
        ready = "The photos are ready for review.\nInteract again to turn them in.",
    },
    [report] = {
        offer = "Recover the report at Dead End.\nInteract again to accept the contract.",
        missing = "Bring the Dead End report in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the report for 1,200 shared cash.\nInteract again to turn it in.",
    },
    [depot] = {
        offer = "Recover the documents from the depot office.\nInteract again to accept the contract.",
        missing = "Bring the depot documents in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the documents for 1,300 shared cash.\nInteract again to turn them in.",
    },
}
local rendered_states = {
    view = { offer = true, missing = true, ready = true, completed = true, unavailable = true, not_offered = true, elsewhere = true },
    start = { offer = true, missing = true, ready = true, completed = true, unavailable = true, not_offered = true, elsewhere = true },
    turnin = { missing = true, ready = true, completed = true, unavailable = true, elsewhere = true },
}

local events, heartbeat, is_downed
local binding, session, widget, presented_text
local selection, pending_move, navigation = 1, 0, false
local last_sequence = 0

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(value, path)
    return valid(value) and same(value:GetClass(), StaticFindObject(path))
        and not value:GetFullName():find("Default__", 1, true)
end

local function current_context(state, phase)
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local world, profile = state.world, profiles[state.map]
    if not enabled or not events or not (host or client) or not profile or state.listening ~= true
        or not valid(world) or world:GetFullName() ~= profile.world then return end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) then return end
    local server = driver.ServerConnection
    if host and valid(server) or not host and (not valid(server) or not same(server.Driver, driver)) then return end

    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() or pawn:HasAuthority() ~= host or not pawn:IsLocallyControlled()
        or pawn:IsActorBeingDestroyed() or not controller:IsLocalController() or controller:IsActorBeingDestroyed()
        or not same(detail.controller_object, controller) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world) then return end

    local board = StaticFindObject(profile.path)
    if not exact(board, board_class) or board:GetFullName() ~= profile.name or not same(board:GetWorld(), world)
        or board:IsActorBeingDestroyed() or board.TurnIn_ID ~= profile.id or board.BountyBoard_RedeemableHere ~= true
        or board.AcceptAllGlobalContracts ~= true or board.Display_GlobalContracts ~= profile.display_global
        or board.ShowAllContracts ~= false then return end
    local box = board.Box
    if not exact(box, box_class) or not same(box:GetOwner(), board) then return end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return end
    return { phase = phase, host = host, world = world, driver = driver, name = detail.name, pawn = pawn,
        controller = controller, board = board, box = box, profile = profile }
end

local function same_context(first, second)
    return first and second and first.phase == second.phase and first.name == second.name
        and same(first.world, second.world) and same(first.driver, second.driver) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller) and same(first.board, second.board) and same(first.box, second.box)
end

local function fresh(now)
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local raw = file:read(32)
    file:close()
    local value = raw and raw:match("^(%d+)%s*$")
    local stamp = value and #value <= 12 and tonumber(value)
    return stamp and stamp <= now and now <= stamp + 2 or false
end

local function distance_squared(first, second)
    if not first or not second then return math.huge end
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function can_use(current, now)
    local pawn, controller = current.pawn, current.controller
    local stack, range = controller.MenuStack, pawn.InteractionRange
    if controller.Loading ~= false or not stack or stack:GetArrayNum() ~= 0
        or pawn.Dead ~= false or type(is_downed) ~= "function" or is_downed(pawn) ~= false
        or type(range) ~= "number" or not (range > 0 and range <= 1000) or not fresh(now) then return false end
    local location = pawn:K2_GetActorLocation()
    return distance_squared(location, current.board:K2_GetActorLocation()) <= range^2
        and distance_squared(location, current.box:K2_GetComponentLocation()) <= range^2
end

local function focused(current)
    return same(current.pawn.InteractionTarget_Component, current.box)
end

local function hide()
    if valid(widget) then widget:RemoveFromParent() end
    widget, presented_text = nil, nil
end

function board_view.clear()
    hide()
    if binding and type(SilverfishClearNativeWireBoardScope) == "function" then SilverfishClearNativeWireBoardScope() end
    binding, session = nil, nil
    selection, pending_move = 1, 0
end

local function show(current, text)
    if not valid(widget) then
        local class = StaticFindObject(widget_path)
        if not valid(class) then
            LoadAsset(widget_path)
            class = StaticFindObject(widget_path)
        end
        local library = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(library) then error("wire_board_widget_class_unavailable", 0) end
        local created = library:Create(current.pawn, class, current.controller)
        if not exact(created, widget_path) or not same(created:GetOwningPlayer(), current.controller)
            or not exact(created.TextBlock_Notes, "/Script/UMG.TextBlock") then error("wire_board_widget_unqualified", 0) end
        widget = created
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(31) ~= true or not widget:IsInViewport() then error("wire_board_widget_unavailable", 0) end
        widget:SetDesiredSizeInViewport({X = 610, Y = 280})
        widget:SetPositionInViewport({X = 42, Y = 138}, false)
    end
    widget.TextBlock_Notes:SetText(FText(text))
    if widget.TextBlock_Notes:GetText():ToString() ~= text then error("wire_board_widget_readback_unverified", 0) end
end

local function present(current, status)
    if headless then return true end
    local quests = current.profile.quests
    local quest = quests[selection]
    local lines = {titles[quest]}
    if navigation and #quests > 1 then
        lines = {"Bounty Board"}
        for index, item in ipairs(quests) do
            lines[#lines + 1] = (index == selection and "> " or "   ") .. titles[item]
        end
        lines[#lines + 1] = "Up / Down to select"
    end
    lines[#lines + 1] = ""
    lines[#lines + 1] = contract_prompt[quest][status] or prompt[status]
    local text = table.concat(lines, "\n")
    if presented_text == text and valid(widget) then return true end
    local ok = pcall(show, current, text)
    if ok then presented_text = text else hide() end
    return ok
end

local function emit(current, sequence, kind, now, fixture)
    local open_sequence = kind == "view" and sequence or session.open
    local profile = current.profile
    local quest = profile.quests[selection]
    local file = io.open(events, "ab")
    local success = false
    if file then
        local written = file:write(table.concat({"wire-board", sequence, open_sequence, kind, profile.map, current.name,
            profile.name, quest}, "\t"), "\n")
        local closed = file:close()
        success = not not (written and closed)
    end
    local waiting = kind == "view" and "loading" or kind == "start" and "starting" or "redeeming"
    session = { open = open_sequence, sequence = sequence, kind = kind, quest = quest, fixture = fixture, expires = now + 30,
        state = success and waiting or "unavailable", pending = success }
    return success
end

local function drain(current, allow, fixture, now)
    local sequence = math.tointeger((SilverfishTakeNativeWireBoardPress(current.pawn:GetAddress())))
    if not sequence or sequence <= last_sequence or sequence >= max_sequence then return false end
    last_sequence = sequence
    if not allow or (not fixture and not focused(current)) or session and session.pending then return false end
    local kind = "view"
    if session and session.expires > now then
        if session.state == "offer" then kind = "start" end
        if session.state == "ready" then kind = "turnin" end
        if kind ~= "view" and (session.quest ~= current.profile.quests[selection]
            or session.fixture ~= fixture or not present(current, session.state)) then return false end
    end
    return emit(current, sequence, kind, now, fixture)
end

local function arm(current, allow)
    if type(SilverfishSetNativeWireBoardScope) ~= "function" or type(SilverfishTakeNativeWireBoardPress) ~= "function" then
        return false, "native_wire_board_backend_unavailable"
    end
    return SilverfishSetNativeWireBoardScope(current.pawn:GetAddress(), current.host, allow)
end

function board_view.sync(state, phase)
    local current = current_context(state, phase)
    if not current then board_view.clear(); return end
    local now = os.time()
    if not same_context(binding, current) then
        board_view.clear()
        if not fresh(now) then return end
        binding = current
    end
    local allow = can_use(current, now)
    local armed, reason = arm(current, allow)
    if armed ~= true then
        board_view.clear()
        binding = current
        binding.status = type(reason) == "string" and reason:match("^native_wire_board_[a-z_]+$") or "scope_rejected"
        return
    end
    local facing = focused(current)
    if session and (session.expires <= now or not allow or (not facing and not session.fixture)) then session = nil end
    if pending_move ~= 0 then
        if allow and facing and (not session or not session.pending) then
            local selected = ((selection - 1 + pending_move) % #current.profile.quests) + 1
            if selection ~= selected then selection, session = selected, nil end
        end
        pending_move = 0
    end
    drain(current, allow, false, now)
    if session then
        present(current, session.state)
    elseif allow and facing then
        present(current, "focused")
    else
        hide()
    end
    binding.status = allow and (facing and "focused" or "armed") or "waiting"
end

function board_view.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-wire-board-render" and action ~= "native-wire-board-fixture" then return end
    local current, now = current_context(state, phase), os.time()
    if not same_context(current, binding) or not can_use(current, now) then error("wire_board_scope_unavailable", 0) end
    if action == "native-wire-board-fixture" then
        local quests, selected = current.profile.quests, nil
        if #fields == 2 then
            selected = 1
        elseif #fields == 3 then
            for index, quest in ipairs(quests) do
                if quest == fields[3] then selected = index end
            end
        end
        if not selected or not headless or (current.host and not host_fixtures) or not arm(current, true)
            or type(SilverfishProbeNativeInteractInput) ~= "function" then error("wire_board_fixture_unavailable", 0) end
        if selected ~= selection then selection, session = selected, nil end
        local pawn, controller = current.pawn, current.controller
        local original, cursor = pawn.InteractionTarget_Component, controller.bShowMouseCursor
        pawn.InteractionTarget_Component = current.box
        local invoked, ok, reason = pcall(SilverfishProbeNativeInteractInput, pawn:GetAddress(), true)
        pawn.InteractionTarget_Component = valid(original) and original or nil
        if not invoked or ok ~= true or reason ~= "stock_started_consumed" or controller.MenuStack:GetArrayNum() ~= 0
            or controller.bShowMouseCursor ~= cursor or not drain(current, true, true, now) then
            error("wire_board_stock_press_unverified", 0)
        end
        return quests[selected] == wire and "exact stock Copper Stop board press verified"
            or "exact stock bounty board press verified"
    end

    local sequence = fields[3] and fields[3]:match("^%d+$") and math.tointeger(tonumber(fields[3]))
    local next_state = fields[6]
    if #fields ~= 6 or not session or not session.pending or session.sequence ~= sequence or session.expires <= now
        or fields[4] ~= current.profile.map or fields[5] ~= current.name
        or session.quest ~= current.profile.quests[selection] or not rendered_states[session.kind][next_state]
        or not (session.fixture or focused(current)) then error("wire_board_render_expired_or_unqualified", 0) end
    session.state, session.expires, session.pending = next_state, now + 30, false
    present(current, next_state)
    return session.quest == wire and "authenticated Copper Stop board state rendered"
        or "authenticated bounty board state rendered"
end

function board_view.rows(state, phase, status_row)
    if not binding or state.map ~= binding.profile.map or phase ~= binding.phase or not same(state.world, binding.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, binding.pawn) then return {} end
    local profile = binding.profile
    return {status_row("native-wire-board", profile.map, binding.name, profile.name, profile.quests[selection], session and session.state or binding.status,
        session and session.sequence or 0, session and session.open or 0)}
end

function board_view.start(bridge, downed)
    if not enabled or type(bridge) ~= "string" or bridge == "" or type(downed) ~= "function" then return end
    events, heartbeat = bridge .. "/native-wire-board-events", bridge .. "/native-wire-board-heartbeat"
    is_downed = downed
    if not headless and not navigation and type(RegisterKeyBind) == "function" and Key and Key.UP_ARROW and Key.DOWN_ARROW then
        RegisterKeyBind(Key.UP_ARROW, function() if binding then pending_move = pending_move - 1 end end)
        RegisterKeyBind(Key.DOWN_ARROW, function() if binding then pending_move = pending_move + 1 end end)
        navigation = true
    end
end

return board_view
