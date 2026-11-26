local board_view = {}
local stock = require("native_wire_quest")
local content_paths = require("content_paths")
local board_world = require("world_path")
local world_leaf = board_world.leaf

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local input_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local shopkeep = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local molasses, keycard = "Clt_Molasis", "Clct_SavloKey"
local wire, computer, photo = "Clt_Wire", "Clt_ComputerBoard", "Photo_DB_Turrets"
local report, depot, brandy = "Clt_CoupReport_01", "DepotDocs", "Clt_Brandy"
local stolen_bag = "Clt_StolenBag"
local dead_drop, metro_drop = "DeadDrop_CrossRoads", "DeadDrop_Metro"
local missing_contact = "Note_Farmhouse"
local black_box = "Clct_Radio_Scar"
local pocket_watch = "Clt_Watch"
local ring = "Clct_EvilEye"
local pump_room = "Clt_EatherPumpTape"
local lake_jammer = "Jmr_Lake"
local crossroads_jammer = "Jmr_CrossRoads"
local flood_jammer = "Jmr_FloodPlains"
local lenny = "Asn_Lenny_01"
local spratt = "Asn_Spratt"
local readings = "Clt_Tape_StoneMaze_01"
local prototype = "Clt_DrillHead"
local flood_control = "Sub_Valve"
local function board_profile(area, level, suffix, id, quests, display_global)
    local world = area .. "." .. level
    local path = world .. ":PersistentLevel.BountyBoard_BP_C_" .. suffix
    return {map = area, world = "World " .. world, path = path, name = "BountyBoard_BP_C " .. path,
        id = id, quests = quests, display_global = display_global == true}
end
local profiles = {
    [shopkeep] = board_profile(shopkeep, "Shop_ShopKeep_P", "1", 2, {molasses}),
    [observatory] = board_profile(observatory, "Observitory_P", "0", 3, {keycard, dead_drop, black_box,
        crossroads_jammer, lake_jammer, flood_jammer}, true),
    [borough] = board_profile(borough, "Borough", "3", 5, {wire, computer, photo, report, flood_control, flood_jammer}),
    [scientist] = board_profile(scientist, "Shop_Scientist_01", "2", 4, {computer, readings, prototype}),
    [scav_bar] = board_profile(scav_bar, "Shop_ScavBar_P", "2", 10, {depot, pocket_watch, ring}, true),
    [black_market] = board_profile(black_market, "Shop_BlackMarket_01", "0", 1, {brandy, pump_room, lake_jammer, lenny,
        stolen_bag, dead_drop, metro_drop, black_box, missing_contact, spratt}, true),
}
local titles = {[molasses] = "Get Molasses", [keycard] = "Hall Pass", [wire] = "Copper Stop", [computer] = "Find a Computer Board", [photo] = "Dead Bridge Turret Photos",
    [report] = "Northern Passage", [depot] = "Depot Documents", [brandy] = "Fine Vintage", [readings] = "Stone Maze Readings",
    [pocket_watch] = "Pocket Watch", [ring] = "Savlo's Ring", [pump_room] = "Pump Room", [lake_jammer] = "Signal Jammer: Black Lake",
    [lenny] = "Lenny's House", [flood_control] = "Flood Control", [stolen_bag] = "Stolen Goods",
    [dead_drop] = "CrossRoads Dead Drop", [metro_drop] = "Metro Dead Drop",
    [black_box] = "Black Box", [missing_contact] = "Missing Contact",
    [spratt] = "Kill Spratt", [prototype] = "Prototype Drill", [crossroads_jammer] = "Signal Jammer: Crossroads",
    [flood_jammer] = "Signal Jammer: Flood Plains"}
local board_class = "/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local quest_log_path = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local quest_details_path = "/Game/Classes/GUI/SubWidgets/Quest_DetailsWindow_Widget.Quest_DetailsWindow_Widget_C"
local quest_turnin_path = "/Game/Classes/GUI/SubWidgets/Quest_TurnIn_Widget.Quest_TurnIn_Widget_C"
local quest_listed_path = "/Game/Classes/GUI/SubWidgets/Quest_Listed_Widget.Quest_Listed_Widget_C"
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
    [prototype] = {
        missing = "Bring the Drill Head recovered in the Scientist Tunnel.\nInteract to check the contract again.",
        ready = "Turn in the Drill Head for 10,000 shared cash and a Hazard Suit.\nInteract again to turn it in.",
    },
    [missing_contact] = {
        offer = "Find the Broker's contact in CrossRoads.\nInteract again to accept.",
        missing = "Bring the sealed package recovered in CrossRoads.\nInteract to check the contract again.",
        ready = "Turn in the sealed package for 450 shared cash.\nInteract again to turn it in.",
    },
    [black_box] = {
        offer = "Recover the flight recorder in the Gorge.\nInteract again to accept the contract.",
        elsewhere = "Deliver the recorder to Simmi's Observatory bounty board.",
        missing = "Bring the flight recorder in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the recorder for 12,000 shared cash.\nInteract again to turn it in.",
    },
    [dead_drop] = {
        offer = "Collect the sealed bag in CrossRoads.\nInteract again to accept the contract.",
        elsewhere = "Deliver the sealed bag to the Black Market bounty board.",
        missing = "Bring the sealed bag in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the bag for 300 shared cash.\nInteract again to turn it in.",
    },
    [metro_drop] = {
        offer = "Collect the sealed bag in Metropol.\nInteract again to accept the contract.",
        missing = "Bring the sealed bag in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the bag for 600 shared cash.\nInteract again to turn it in.",
    },
    [stolen_bag] = {
        offer = "Recover the Broker's bag from the Forest.\nInteract again to accept the contract.",
        missing = "Bring the Broker's unopened bag in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the bag for 1,200 shared cash.\nInteract again to turn it in.",
    },
    [flood_control] = {
        offer = "Close the Subway flow-control valve.\nInteract again to accept the contract.",
        missing = "Close the flow-control valve in the Subway.\nInteract to check the contract again.",
        ready = "The valve is closed.\nInteract again to turn in the contract.",
    },
    [molasses] = {
        offer = "Find a can of molasses for Shop Keep.\nInteract again to accept the contract.",
        missing = "Bring a can of molasses in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the molasses for 30 shared cash.\nInteract again to turn it in.",
    },
    [keycard] = {
        offer = "Retrieve the Savlo keycard for Simmi.\nInteract again to accept the contract.",
        missing = "Bring the Savlo keycard in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the keycard for 50,000 shared cash.\nInteract again to turn it in.",
    },
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
    [brandy] = {
        offer = "Find the sealed Paladin Brandy in the City.\nInteract again to accept the contract.",
        missing = "Bring the sealed bottle in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the bottle for 900 shared cash.\nInteract again to turn it in.",
    },
    [pocket_watch] = {
        offer = "Find the pocket watch in the Crossroads farm cabin.\nInteract again to accept the contract.",
        missing = "Bring the pocket watch in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the pocket watch for 1,000 shared cash.\nInteract again to turn it in.",
    },
    [ring] = {
        offer = "Find Savlo's ring in the manor near Trinsdal.\nInteract again to accept the contract.",
        missing = "Bring Savlo's ring in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the ring for 20,000 shared cash.\nInteract again to turn it in.",
    },
    [pump_room] = {
        offer = "Download the pump data in the Utility Tunnels.\nInteract again to accept the contract.",
        missing = "Bring the Eather Pump Data tape in your backpack.\nInteract to check the contract again.",
        ready = "Turn in the tape for 3,200 shared cash.\nInteract again to turn it in.",
    },
    [lake_jammer] = {
        offer = "Disable the signal jammer at Black Lake.\nInteract again to accept the contract.",
        missing = "Disable the signal jammer at Black Lake.\nInteract to check the contract again.",
        ready = "Turn in the contract for 400 shared cash.\nInteract again to turn it in.",
    },
    [crossroads_jammer] = {
        offer = "Disable the signal jammer at Crossroads.\nInteract again to accept the contract.",
        missing = "Disable the signal jammer at Crossroads.\nInteract to check the contract again.",
        ready = "Turn in the contract for 200 shared cash.\nInteract again to turn it in.",
    },
    [flood_jammer] = {
        offer = "Disable the signal jammer at Flood Plains.\nInteract again to accept the contract.",
        missing = "Disable the signal jammer at Flood Plains.\nInteract to check the contract again.",
        ready = "Turn in the contract for 350 shared cash.\nInteract again to turn it in.",
    },
    [lenny] = {
        offer = "Find Lenny in the Rail Yard.\nInteract again to accept the contract.",
        missing = "Take out Lenny in the Rail Yard.\nInteract to check the contract again.",
        ready = "Turn in the contract for 2,500 shared cash.\nInteract again to turn it in.",
    },
    [spratt] = {
        offer = "Find Spratt in the Cistern.\nInteract again to accept the contract.",
        missing = "Take out Spratt in the Cistern.\nInteract to check the contract again.",
        ready = "Turn in the contract for 5,000 shared cash.\nInteract again to turn it in.",
    },
    [readings] = {
        not_offered = "Speak to Dr. Mosby to start this contract.",
        missing = "Bring the Ground Saturation Data tape from Stone Maze.\nInteract to check the contract again.",
        ready = "Turn in your Ground Saturation Data tape for 7,500 shared cash.\nInteract again to turn it in.",
    },
}
local rendered_states = {
    view = { offer = true, missing = true, ready = true, completed = true, unavailable = true, not_offered = true, elsewhere = true },
    start = { offer = true, missing = true, ready = true, completed = true, unavailable = true, not_offered = true, elsewhere = true },
    turnin = { missing = true, ready = true, completed = true, unavailable = true, elsewhere = true },
}

local events, heartbeat, is_downed
local binding, session, stock_session, stock_pending, stock_projection, widget, presented_text
local input_armed = false
local selection = 1
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

local function local_context(state, phase)
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local world, area = state.world, state.map
    local leaf = world_leaf(area)
    local world_path = leaf and area .. "." .. leaf
    if not enabled or not events or not (host or client) or not world_path or state.listening ~= true
        or not valid(world) or world:GetFullName() ~= "World " .. world_path then return end
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

    return {phase = phase, host = host, world = world, driver = driver, name = detail.name, pawn = pawn,
        controller = controller, area = area, world_path = world_path}
end

local function selected_context(seat, path, actor)
    if not seat or type(path) ~= "string" or #path > 490 then return end
    local package, export = board_world.level_actor_package(path)
    if not package then return end
    local board = actor or StaticFindObject(path)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(seat.pawn)
    if not exact(instance, instance_class) then return end
    local box, level = stock.qualified_stock_board(seat.world, instance, board)
    if not box or (seat.host and not board:HasAuthority()) then return end
    local name = board:GetFullName()
    if name:match("^[A-Za-z0-9_%-]+_C (.+)$") ~= path then return end
    local streamed = not same(level, seat.world.PersistentLevel)
    if streamed then
        if package == seat.area then return end
        name = "stream:" .. name
    elseif package ~= seat.area or export ~= world_leaf(seat.area) then
        return
    end
    local authored = profiles[seat.area]
    local profile = authored and authored.path == path and board.TurnIn_ID == authored.id
        and board.BountyBoard_RedeemableHere == true and board.AcceptAllGlobalContracts == true
        and board.Display_GlobalContracts == authored.display_global and authored
        or {map = seat.area, path = path, name = name, quests = {}}
    local current = {}
    for key, value in pairs(seat) do current[key] = value end
    current.board, current.box, current.profile = board, box, profile
    return current
end

local function same_seat(first, second)
    return first and second and first.phase == second.phase and first.name == second.name
        and same(first.world, second.world) and same(first.driver, second.driver) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller)
end

local function same_context(first, second)
    return same_seat(first, second) and same(first.board, second.board) and same(first.box, second.box)
end

local function bound_context(seat)
    if same_seat(seat, binding) then return selected_context(seat, binding.profile.path, binding.board) end
end

local function fixture_context(seat, name)
    if not seat or not headless or not (input_fixtures or not seat.host or host_fixtures) then return end
    local profile = profiles[seat.area]
    if name == nil then return selected_context(seat, profile and profile.path) end
    if type(name) ~= "string" then return end
    local raw_name = name:sub(1, 7) == "stream:" and name:sub(8) or name
    local path = raw_name:match("^[A-Za-z0-9_%-]+_C (.+)$")
    local current = selected_context(seat, path)
    if current and current.profile.name == name then return current end
end

local function fresh()
    if type(SilverfishReadLocalStockBoardHeartbeat) == "function" then
        local attached, timestamp = SilverfishReadLocalStockBoardHeartbeat()
        if attached then
            -- The resident worker can publish a new second during a stock menu callback.
            local now = os.time()
            return timestamp ~= nil and timestamp <= now and now <= timestamp + 2
        end
    end
    if embedded then return false end
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local raw = file:read(32)
    file:close()
    local value = raw and raw:match("^(%d+)%s*$")
    local stamp = value and #value <= 12 and tonumber(value)
    local now = os.time()
    return stamp and stamp <= now and now <= stamp + 2 or false
end

local function distance_squared(first, second)
    if not first or not second then return math.huge end
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function can_use(current, pending_readings)
    local pawn, controller = current.pawn, current.controller
    local stack, range = controller.MenuStack, pawn.InteractionRange
    if controller.Loading ~= false or not stack or stack:GetArrayNum() ~= 0
        or pawn.Dead ~= false or type(is_downed) ~= "function" or is_downed(pawn) ~= false
        or type(range) ~= "number" or not (range > 0 and range <= 1000) or not (pending_readings or fresh()) then return false end
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

local function select_board(current)
    hide()
    binding, session, stock_session, stock_pending, stock_projection = current, nil, nil, nil, nil
    selection = 1
end

function board_view.clear()
    select_board(nil)
    if input_armed and type(SilverfishClearNativeWireBoardScope) == "function" then SilverfishClearNativeWireBoardScope() end
    input_armed = false
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
    lines[#lines + 1] = ""
    lines[#lines + 1] = contract_prompt[quest][status] or prompt[status]
    local text = table.concat(lines, "\n")
    if presented_text == text and valid(widget) then return true end
    local ok = pcall(show, current, text)
    if ok then presented_text = text else hide() end
    return ok
end

local function write_event(current, sequence, open_sequence, kind, quest)
    local profile = current.profile
    local record = table.concat({"wire-board", sequence, open_sequence, kind, profile.map, current.name,
        profile.name, quest}, "\t") .. "\n"
    if embedded then
        return type(SilverfishEnqueueLocalBridgeInput) == "function" and SilverfishEnqueueLocalBridgeInput(record) == true
    end
    local file = io.open(events, "ab")
    if not file then return false end
    local written = file:write(record)
    local closed = file:close()
    return not not (written and closed)
end

local function emit(current, sequence, kind, now, fixture)
    local open_sequence = kind == "view" and sequence or session.open
    local quest = current.profile.quests[selection]
    local success = write_event(current, sequence, open_sequence, kind, quest)
    local waiting = kind == "view" and "loading" or kind == "start" and "starting" or "redeeming"
    session = { open = open_sequence, sequence = sequence, kind = kind, quest = quest, fixture = fixture, expires = now + 30,
        state = success and waiting or "unavailable", pending = success }
    return success
end

local function bound_stock_menu(current)
    local stack = current.controller.MenuStack
    local count = stack and stack:GetArrayNum()
    local menu = count and count > 0 and stack[count]
    if exact(menu, quest_log_path) and menu.BountyBoard == true and same(menu.BountyBoard_Ref, current.board) then return menu end
end

local function current_stock_menu(current)
    if not current or not fresh() or current.controller.Loading ~= false or current.pawn.Dead ~= false
        or type(is_downed) ~= "function" or is_downed(current.pawn) ~= false then return end
    return bound_stock_menu(current)
end

local function supported_stock_action(current, quest)
    local map = current.profile.map
    if current.profile ~= profiles[map] then return false end
    return quest == lake_jammer and (map == black_market or map == observatory)
        or quest == crossroads_jammer and map == observatory
        or quest == flood_jammer and (map == borough or map == observatory)
end

local function queue_stock_view(current, sequence, now, fixture, menu)
    if stock_pending or not write_event(current, sequence, sequence, "stock-view", "-") then return false end
    stock_pending = {sequence = sequence, expires = now + 12, fixture = fixture, menu = menu}
    session = nil
    hide()
    return true
end

local function stock_ids(raw)
    if raw == "-" then return {} end
    if type(raw) ~= "string" or #raw > 8192 then error("wire_board_catalog_invalid", 0) end
    local ids, seen = {}, {}
    for id in (raw .. ","):gmatch("([^,]*),") do
        if not content_paths.quest_id(id) or seen[id:lower()] or #ids >= 128 then
            error("wire_board_catalog_invalid", 0)
        end
        ids[#ids + 1], seen[id:lower()] = id, true
    end
    return ids
end

local function read_menu_rows(menu, list, class, allowed, expected)
    if not valid(list) or list:GetChildrenCount() ~= expected then return false end
    local seen = {}
    for index = 0, expected - 1 do
        local row = list:GetChildAt(index)
        if not exact(row, class) or not same(row.QuestLog_MenuRef, menu) then return false end
        local id = stock.stock_quest_id(row.Quest_IDTag:ToString())
        if not allowed[id] or seen[id] then return false end
        seen[id] = true
    end
    return true
end

local function show_stock_menu(current, sequence, offers_arg, ready_arg, existing_menu)
    local offered, redeemable = stock_ids(offers_arg), stock_ids(ready_arg)
    local offers, ready_names, ready = {}, {}, {}
    local table_data = stock.stock_quest_table()
    local missing = {}
    for _, id in ipairs(redeemable) do
        local key = stock.stock_quest_key(id)
        if key then
            ready_names[#ready_names + 1], ready[id] = key, true
        else
            missing[#missing + 1] = id
        end
    end
    if not existing_menu then current.board:Send_PowerSignal(true, current.pawn) end
    local menu = current_stock_menu(current)
    if not menu or existing_menu and not same(menu, existing_menu) then error("wire_board_stock_menu_missing", 0) end
    local pawn_address, menu_address = current.pawn:GetAddress(), menu:GetAddress()
    local started = SilverfishRenderNativeStockBoard(pawn_address, menu_address, true)
    local drawn, reason
    if started == true then
        drawn, reason = pcall(function()
            local contracts, additions, removals = current.board.Contracts, {}, {}
            if #contracts > 128 then error("wire_board_stock_offers_extent", 0) end
            local offer_count = 0
            for _, id in ipairs(offered) do
                if ready[id] then error("wire_board_catalog_invalid", 0) end
                local key = stock.stock_quest_key(id)
                -- Keep matching live rows in place. A mod can supply them as
                -- part of the board's stock interaction, outside the base table.
                if key and contracts:Contains(key) then
                    offers[id], offer_count = true, offer_count + 1
                else
                    local row = key and table_data:FindRow(key:ToString())
                    if row then
                        additions[#additions + 1] = {key = key, row = row}
                        offers[id], offer_count = true, offer_count + 1
                    else
                        missing[#missing + 1] = id
                    end
                end
            end
            contracts:ForEach(function(raw_key)
                local key = raw_key:get()
                if not offers[stock.stock_quest_id(key:ToString())] then removals[#removals + 1] = key end
            end)
            for _, key in ipairs(removals) do contracts:Remove(key) end
            for _, entry in ipairs(additions) do contracts:Add(entry.key, entry.row) end
            if #contracts ~= offer_count then error("wire_board_stock_offers_unverified", 0) end
            if #missing > 0 then
                print("[SilverfishCoop] Board quests missing from local game data: " .. table.concat(missing, ", "))
            end
            current.board["Validated Quests"] = ready_names

            -- Stock Add_Contracts also calls FillInCompletedQuests. During this repaint
            -- the native guard keeps the authority's redemption set in place.
            menu:Add_Contracts()
            if not read_menu_rows(menu, menu.VerticalBox_ContractsList, quest_listed_path, offers, offer_count)
                or not read_menu_rows(menu, menu.VerticalBox_CompletedList, quest_turnin_path, ready, #ready_names) then
                error("wire_board_stock_rows_unverified", 0)
            end
        end)
    end
    local finished, validations = SilverfishRenderNativeStockBoard(pawn_address, menu_address, false)
    if not drawn or finished ~= true or validations ~= 1 or not same(current_stock_menu(current), menu) then
        if same(current_stock_menu(current), menu) then current.controller:CloseMenu({}) end
        error(reason or "wire_board_stock_menu_unverified", 0)
    end
    stock_projection = {menu = menu, sequence = sequence, offers = offers, ready = ready}
end

local function drain_stock(current, now)
    local menu = current_stock_menu(current)
    if stock_session and (stock_session.expires <= now or not same(menu, stock_session.menu)) then stock_session = nil end
    if not menu or type(SilverfishTakeNativeStockBoardAction) ~= "function" then return end
    local sequence, kind, quest = SilverfishTakeNativeStockBoardAction(current.pawn:GetAddress())
    quest = stock.stock_quest_id(quest)
    sequence = math.tointeger(sequence)
    if not sequence or sequence <= last_sequence or sequence >= max_sequence then return end
    last_sequence = sequence
    if stock_session or not (kind == "stock-start" or kind == "stock-turnin") then return end
    local open_sequence = sequence
    if stock_projection and same(stock_projection.menu, menu) then
        local allowed = kind == "stock-start" and stock_projection.offers or stock_projection.ready
        if not allowed[quest] then return end
        open_sequence = stock_projection.sequence
    elseif not supported_stock_action(current, quest) then
        return
    end
    if write_event(current, sequence, open_sequence, kind, quest) then
        stock_session = {sequence = sequence, open = open_sequence, quest = quest, menu = menu, expires = now + 30,
            state = kind == "stock-start" and "starting" or "redeeming"}
    end
end

local function stock_fixture(current, fields, now)
    local quest, verb = fields[3], fields[4]
    local menu = current_stock_menu(current)
    local projected = stock_projection and same(menu, stock_projection.menu)
    local allowed = projected and (verb == "start" and stock_projection.offers or stock_projection.ready)
    if not headless or not (input_fixtures or current.host and host_fixtures) or #fields ~= 4
        or (verb ~= "start" and verb ~= "turnin")
        or projected and not allowed[quest]
        or not projected and (not supported_stock_action(current, quest) or not can_use(current)) then
        error("wire_board_stock_fixture_scope", 0)
    end
    if not projected then
        current.board:Send_PowerSignal(true, current.pawn)
        menu = current_stock_menu(current)
    end
    if not menu then error("wire_board_stock_fixture_menu_missing", 0) end
    if verb == "start" then
        local id = stock.stock_quest_key(quest)
        local offer = id and stock.stock_row(current.board.Contracts, id)
        if not offer then error("wire_board_stock_fixture_offer_missing", 0) end
        menu:ShowDetails_Quest(offer, id)
        local details = menu.VerticalBox_Details:GetChildAt(0)
        if not exact(details, quest_details_path) or not same(details.QuestLog_MenuRef, menu)
            or stock.stock_quest_id(details.Quest_ID:ToString()) ~= quest then
            error("wire_board_stock_fixture_details_missing", 0)
        end
        details:BndEvt__Quest_DetailsWindow_Widget_Button_Clear_Accept_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    else
        if not projected then menu:FillInCompletedQuests() end
        local list = menu.VerticalBox_CompletedList
        local count = list:GetChildrenCount()
        local selected
        for index = 0, math.min(count, 64) - 1 do
            local entry = list:GetChildAt(index)
            if exact(entry, quest_turnin_path) and same(entry.QuestLog_MenuRef, menu)
                and stock.stock_quest_id(entry.Quest_IDTag:ToString()) == quest then selected = entry; break end
        end
        if not selected then error("wire_board_stock_fixture_redemption_missing", 0) end
        selected:BndEvt__Quest_TurnIn_Widget_Button_K2Node_ComponentBoundEvent_2_OnButtonReleasedEvent__DelegateSignature()
    end
    drain_stock(current, now)
    if not stock_session or stock_session.quest ~= quest then error("wire_board_stock_fixture_action_unconsumed", 0) end
    return "verified stock bounty button intercepted"
end

local function take_press(seat)
    local raw, path, already_open = SilverfishTakeNativeWireBoardPress(seat.pawn:GetAddress())
    local sequence = math.tointeger(raw)
    if not sequence or sequence <= last_sequence or sequence >= max_sequence then return end
    last_sequence = sequence
    local current = selected_context(seat, path)
    if already_open then
        local menu = current and current_stock_menu(current)
        if not menu then return end
        return sequence, current, menu
    end
    return sequence, current
end

local function drain_fixture(current, sequence, now)
    local kind = "view"
    if session and session.expires > now then
        if session.pending then return false end
        if session.state == "offer" then kind = "start" end
        if session.state == "ready" then kind = "turnin" end
        if kind ~= "view" and (session.quest ~= current.profile.quests[selection]
            or not session.fixture or not present(current, session.state)) then return false end
    end
    return emit(current, sequence, kind, now, true)
end

local function arm(seat, allow)
    if type(SilverfishSetNativeWireBoardScope) ~= "function" or type(SilverfishTakeNativeWireBoardPress) ~= "function" then return end
    input_armed = SilverfishSetNativeWireBoardScope(seat.pawn:GetAddress(), seat.host, allow) == true
    return input_armed
end

function board_view.sync(state, phase)
    local seat = local_context(state, phase)
    if not seat then board_view.clear(); return end
    local now = os.time()
    local current = bound_context(seat)
    if binding and not current and same_seat(seat, binding)
        and binding.profile.name:sub(1, 7) == "stream:" and bound_stock_menu(binding) then
        seat.controller:CloseMenu({})
    end
    if not fresh() then
        if current and session and session.quest == readings and session.pending
            and session.expires > now and can_use(current, true) then return end
        board_view.clear()
        return
    end
    if binding and not current then board_view.clear() end
    if stock_pending and (stock_pending.expires <= now
        or stock_pending.menu and not same(current_stock_menu(current), stock_pending.menu)) then stock_pending = nil end
    if stock_projection and not same(current_stock_menu(current), stock_projection.menu) then stock_projection = nil end

    if input_armed and not stock_pending and not stock_projection then
        local sequence, pressed, menu = take_press(seat)
        if pressed and (menu or can_use(pressed, true) and focused(pressed)) then
            if not same_context(current, pressed) then select_board(pressed) end
            current = pressed
            queue_stock_view(current, sequence, now, false, menu)
        end
    end
    local stack = seat.controller.MenuStack
    local allow = not stock_pending and not stock_projection and stack and stack:GetArrayNum() == 0
        and seat.controller.Loading == false and seat.pawn.Dead == false and is_downed(seat.pawn) == false
    if not arm(seat, allow == true) then board_view.clear(); return end
    if not current and headless then
        current = fixture_context(seat)
        if current then select_board(current) end
    end
    if not current then return end
    local usable = can_use(current, true)
    if session and (session.expires <= now or not usable) then session = nil end
    drain_stock(current, now)
    if session then present(current, session.state) else hide() end
    binding.status = usable and (focused(current) and "focused" or "armed") or "waiting"
end

local function probe_press(current)
    local pawn, controller = current.pawn, current.controller
    local original, cursor = pawn.InteractionTarget_Component, controller.bShowMouseCursor
    pawn.InteractionTarget_Component = current.box
    local armed = arm(current, true)
    local invoked, ok, reason
    if armed then invoked, ok, reason = pcall(SilverfishProbeNativeInteractInput, pawn:GetAddress(), true) end
    pawn.InteractionTarget_Component = valid(original) and original or nil
    if not armed or not invoked or ok ~= true or reason ~= "stock_started_consumed"
        or controller.MenuStack:GetArrayNum() ~= 0 or controller.bShowMouseCursor ~= cursor then return end
    local sequence, pressed, menu = take_press(current)
    if not menu and same_context(current, pressed) then return sequence end
end

function board_view.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-wire-board-render" and action ~= "native-wire-board-fixture"
        and action ~= "native-wire-board-stock-result" and action ~= "native-wire-board-stock-fixture"
        and action ~= "native-wire-board-stock-open" and action ~= "native-wire-board-stock-f-fixture" then return end
    local seat, now = local_context(state, phase), os.time()
    local current = bound_context(seat)
    if action == "native-wire-board-stock-open" then
        local sequence = fields[3] and fields[3]:match("^%d+$") and math.tointeger(tonumber(fields[3]))
        if #fields ~= 7 or not same_context(current, binding) or not stock_pending or sequence ~= stock_pending.sequence
            or stock_pending.expires <= now or fields[4] ~= current.profile.map or fields[5] ~= current.name
            or type(SilverfishRenderNativeStockBoard) ~= "function" then error("wire_board_stock_open_stale", 0) end
        local menu = stock_pending.menu
        if menu and not same(current_stock_menu(current), menu)
            or not menu and (not can_use(current) or not stock_pending.fixture and not focused(current)) then
            error("wire_board_stock_open_stale", 0)
        end
        stock_pending = nil
        show_stock_menu(current, sequence, fields[6], fields[7], menu)
        return "verified the host options in the native bounty board menu"
    end
    if action == "native-wire-board-stock-f-fixture" then
        local chosen = fixture_context(seat, fields[3])
        if (#fields ~= 2 and #fields ~= 3) or not chosen or not (input_fixtures or chosen.host and host_fixtures)
            or not can_use(chosen) or type(SilverfishProbeNativeInteractInput) ~= "function" then
            error("wire_board_stock_f_fixture_scope", 0)
        end
        if not same_context(current, chosen) then select_board(chosen) end
        local sequence = probe_press(chosen)
        if not sequence then error("wire_board_stock_f_fixture_receipt", 0) end
        if not queue_stock_view(chosen, sequence, now, true) then error("wire_board_stock_f_fixture_unverified", 0) end
        return "verified stock bounty board Interact press"
    end
    if action == "native-wire-board-stock-fixture" then
        if not same_context(current, binding) then error("wire_board_stock_fixture_scope", 0) end
        return stock_fixture(current, fields, now)
    end
    if action == "native-wire-board-stock-result" then
        local sequence = fields[3] and fields[3]:match("^%d+$") and math.tointeger(tonumber(fields[3]))
        local result = fields[7]
        if #fields ~= 7 or not same_context(current, binding) or not stock_session or stock_session.sequence ~= sequence
            or fields[4] ~= current.profile.map or fields[5] ~= current.name or fields[6] ~= stock_session.quest
            or not (result == "accepted" or result == "completed" or result == "unavailable")
            or not same(stock_session.menu, current_stock_menu(current)) then error("wire_board_stock_result_stale", 0) end
        stock_session = nil
        if result ~= "unavailable" then current.controller:CloseMenu({}) end
        return "verified stock bounty action result " .. result
    end
    local readings_pending = action == "native-wire-board-render" and session and session.quest == readings and session.pending
    if not same_context(current, binding) or not can_use(current, readings_pending) then error("wire_board_scope_unavailable", 0) end
    if action == "native-wire-board-fixture" then
        local quests, selected = current.profile.quests, nil
        if #fields == 2 then
            selected = 1
        elseif #fields == 3 then
            for index, quest in ipairs(quests) do
                if quest == fields[3] then selected = index end
            end
        end
        if not selected or not quests[selected] or not headless or (current.host and not host_fixtures)
            or type(SilverfishProbeNativeInteractInput) ~= "function" then error("wire_board_fixture_unavailable", 0) end
        if selected ~= selection then selection, session = selected, nil end
        local sequence = probe_press(current)
        if not sequence or not drain_fixture(current, sequence, now) then
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

function board_view.stock_row(status_row)
    local profile = binding.profile
    if stock_session then
        return status_row("native-wire-board-stock", profile.map, binding.name, profile.name,
            stock_session.quest, stock_session.state, stock_session.sequence, stock_session.open)
    elseif stock_pending then
        return status_row("native-wire-board-stock", profile.map, binding.name, profile.name,
            "-", "loading", stock_pending.sequence, stock_pending.sequence)
    elseif stock_projection then
        return status_row("native-wire-board-stock", profile.map, binding.name, profile.name,
            "-", "visible", stock_projection.sequence, stock_projection.sequence)
    end
end

function board_view.open_reply(fields, state, phase, status_row)
    local message = board_view.command(fields, state, phase)
    return message, board_view.stock_row(status_row)
end

function board_view.rows(state, phase, status_row)
    if not binding or state.map ~= binding.profile.map or phase ~= binding.phase or not same(state.world, binding.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, binding.pawn) then return {} end
    local profile = binding.profile
    local rows = {}
    if profile.quests[selection] then
        rows[1] = status_row("native-wire-board", profile.map, binding.name, profile.name, profile.quests[selection],
            session and session.state or binding.status or "waiting", session and session.sequence or 0, session and session.open or 0)
    end
    local stock_row = board_view.stock_row(status_row)
    if stock_row then rows[#rows + 1] = stock_row end
    return rows
end

function board_view.start(bridge, downed)
    if not enabled or type(bridge) ~= "string" or bridge == "" or type(downed) ~= "function" then return end
    events, heartbeat = bridge .. "/native-wire-board-events", bridge .. "/native-wire-board-heartbeat"
    is_downed = downed
end

return board_view
