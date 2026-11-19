local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if not bridge or bridge == "" then return end

local guest_save = require("guest_save")
local loot = require("loot")
local native_bed = require("native_bed")
local native_blue = require("native_blue")
local native_death = require("native_death")
local native_dream = require("native_dream")
local native_matrix = require("native_matrix")
local native_quest_catalog = require("native_quest_catalog")
local native_broker_quest = require("native_broker_quest")
local native_authored_arrival = require("native_authored_arrival")
local native_green = require("native_green")
local native_green_cube = require("native_cubes").new("green")
local native_hazard = require("native_hazard")
local native_host_save = require("native_host_save")
local native_world_flush = require("native_world_flush")
local native_rest = require("native_rest")
local native_black_stone = require("native_black_stone")
local native_world_view = require("native_world_view")
local native_inventory = require("native_inventory")
local native_wipe_inventory = require("native_wipe_inventory")
local native_loot_actions = require("native_loot_actions")
local native_pickup = require("native_pickup")
local native_red = require("native_cubes").new("red")
local native_purple = require("native_cubes").new("purple")
local purple_arrival = require("native_purple_arrival")
local native_roster = require("native_roster")
local native_shop = require("native_shop")
local native_stardust = require("native_stardust")
local native_transport = require("native_transport")
local native_world = require("native_world")
local native_zone = require("native_zone")
local players = require("players")
local vitals = require("vitals")
local wallet = require("wallet")
local weapon_inventory = require("weapon_inventory")
local native_guest_weapon = require("native_guest_weapon")
local native_guest_npc = require("native_guest_npc")
local native_guest_reload_input = require("native_guest_reload_input")
local zone_maps = require("zone_maps")

local main_menu = "/Game/Classes/Maps/MainMenu_P"
local gold_nexus = "/Game/Classes/Maps/Dreams/GoldNexus_P"
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local expanded_routes = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local stock_save_probes = {
    ["native-death-probe"] = true,
    ["native-damage-probe"] = true,
    ["native-inventory-probe"] = true,
    ["vitals-probe"] = true,
    ["vitals-reset"] = true,
}
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local command_path = bridge .. "/command.tsv"
local status_path = bridge .. "/status.tsv"
local connection_timeout_seconds = 95
local restore_position_tolerance = 0.25

local role = "idle"
local expected_map
local leaving = false
local was_connected = false
local seen_commands = {}
local acknowledged_id = ""
local acknowledged_result = ""
local acknowledged_message = ""
local sequence = 0
local tick_queued = false
local last_blue_route, last_blue_arrival, last_dream_route, last_dream_arrival, last_dream_effects
local last_quest_catalog, last_purple_arrival, last_broker_quest, last_broker_position
local last_world_flush
local last_rest
local host_world_view, host_world_view_check, host_world_view_world
local world_view_enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local last_guest_xp
local last_authored_arrival
local last_green_options, last_green_arrival

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same_object(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function text_field(value)
    return (tostring(value or ""):gsub("[%c]", " "))
end

local function status_row(...)
    local fields = { ... }
    for index, value in ipairs(fields) do
        fields[index] = text_field(value)
    end
    return table.concat(fields, "\t")
end

local function map_package(world)
    return world:GetFullName():match("^%S+%s+(/[^%.:]+)%.") or ""
end

local function current_world()
    local viewport = FindFirstOf("GameViewportClient")
    if valid(viewport) and valid(viewport.World) then
        return viewport.World
    end

    for _, controller in ipairs(FindAllOf("PlayerControler_FP_C") or {}) do
        if valid(controller) and controller:IsLocalController() then
            return controller:GetWorld()
        end
    end
end

local function controller_in_world(controller, world)
    return valid(controller) and same_object(controller:GetWorld(), world)
end

local function get_pawn_health(pawn)
    local save = pawn.Save
    if valid(save) and finite(save.PlayerHealth) then
        return save.PlayerHealth, save
    end
end

local function describe_pawn(pawn, local_player, controller)
    local location = pawn:K2_GetActorLocation()
    local health, save = get_pawn_health(pawn)
    return {
        pawn = pawn,
        name = pawn:GetFullName(),
        kind = local_player and "local" or "guest",
        controller = valid(controller) and controller:GetFullName() or "",
        controller_object = controller,
        x = location.X,
        y = location.Y,
        z = location.Z,
        health = health,
        max_health = pawn.MaxHealth,
        save = save,
    }
end

local function inspect()
    local world = current_world()
    local state = { map = "", clients = 0, server = false, pawns = {}, guests = {} }
    if not valid(world) then return state end

    state.world = world
    state.map = map_package(world)
    local driver = world.NetDriver
    if valid(driver) then
        state.listening = true
        state.clients = #driver.ClientConnections
        state.server = valid(driver.ServerConnection)
        local own_session = (role == "host" and state.map == expected_map and not state.server)
            or (role == "client" and state.server)
        if own_session and not leaving and driver.ConnectionTimeout ~= connection_timeout_seconds then
            driver.ConnectionTimeout = connection_timeout_seconds
        end
    end

    local known_pawns = {}
    for _, controller in ipairs(FindAllOf("PlayerControler_FP_C") or {}) do
        if controller_in_world(controller, world) then
            local is_local = controller:IsLocalController()
            local pawn = controller.Pawn
            if is_local then state.controller = controller end
            if valid(pawn) and pawn:GetFullName():match("^Player_FP_C ") then
                local details = describe_pawn(pawn, is_local, controller)
                state.pawns[#state.pawns + 1] = details
                if is_local then state.local_pawn = details end
                known_pawns[pawn:GetAddress()] = true
            end
        end
    end

    -- Unreal only replicates our own controller to a client. Report the other
    -- player's visible pawn independently of whether its controller exists here.
    if role == "client" and state.server then
        for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
            if valid(pawn) and same_object(pawn:GetWorld(), world)
                and not known_pawns[pawn:GetAddress()]
                and not pawn:GetFullName():find("Default__", 1, true) then
                state.pawns[#state.pawns + 1] = describe_pawn(pawn, false, nil)
            end
        end
    end

    -- The stock game can put multiple pawns on one SaveGame_BP object.
    -- A shared field cannot be presented as independent character health.
    local save_counts = {}
    for _, details in ipairs(state.pawns) do
        if valid(details.save) then
            local address = details.save:GetAddress()
            save_counts[address] = (save_counts[address] or 0) + 1
        end
    end
    for _, details in ipairs(state.pawns) do
        if vitals.active() or (valid(details.save) and save_counts[details.save:GetAddress()] > 1) then
            details.health = nil
            details.save = nil
        end
        if details.kind == "guest" then state.guests[#state.guests + 1] = details end
    end
    table.sort(state.guests, function(first, second) return first.name < second.name end)
    for index, guest in ipairs(state.guests) do
        guest.slot = index == 1 and "guest" or "guest" .. tostring(index)
    end

    return state
end

local function phase(state)
    if leaving then
        if state.controller and state.map == main_menu and not state.listening then
            role = "idle"
            leaving = false
            was_connected = false
        else
            return "leaving"
        end
    end

    if role == "host" then
        if state.map == expected_map and state.listening and not state.server and state.local_pawn then
            return "hosting"
        end
        return "opening"
    end
    if role == "client" then
        if state.server and state.local_pawn and state.map ~= main_menu then
            was_connected = true
            return "connected"
        end
        return was_connected and "disconnected" or "joining"
    end
    return valid(state.controller) and "ready" or "opening"
end

local function open_level(context, level, options)
    if not valid(context) then
        error("No current local player controller", 0)
    end

    local statics = StaticFindObject(gameplay_statics_path)
    if not valid(statics) then
        error("GameplayStatics is unavailable", 0)
    end

    statics:OpenLevel(context, FName(level), true, options)
end

local function supported_map(map)
    local route = zone_maps[map]
    if not route then return false end
    if route.expanded then return authored_routes and expanded_routes end
    return route.entrance == nil or authored_routes
end

local function loopback_endpoint(endpoint)
    local port = endpoint and endpoint:match("^127%.0%.0%.1:(%d%d?%d?%d?%d?)$")
    return port ~= nil and tonumber(port) >= 1 and tonumber(port) <= 65535
end

local function restore_pawn(fields, state, current_phase)
    local exact = fields[2] == "restore-exact"
    local slot = exact and "guest" or fields[3]
    local offset = exact and 2 or 0
    local restore_health = fields[4 + offset] ~= "-"
    local health = tonumber(fields[4 + offset])
    local x, y, z = tonumber(fields[5 + offset]), tonumber(fields[6 + offset]), tonumber(fields[7 + offset])
    local respawning = #fields == 8 + offset and fields[8 + offset] == "native-wipe"
    if role ~= "host" or current_phase ~= "hosting" then
        error("Pawn restoration requires the active listen host", 0)
    end
    if (not respawning and #fields ~= 7 + offset) or (slot ~= "local" and slot ~= "guest")
        or (restore_health and (not finite(health) or health < 0))
        or not finite(x) or not finite(y) or not finite(z)
        or math.abs(x) > 100000000 or math.abs(y) > 100000000 or math.abs(z) > 100000000 then
        error("Invalid restore arguments", 0)
    end

    local match
    for _, details in ipairs(state.pawns) do
        if details.kind == slot and (not exact or (details.name == fields[3] and details.controller == fields[4])) then
            if match then error("Multiple pawns occupy the requested slot", 0) end
            match = details
        end
    end
    if not match or not match.pawn:HasAuthority() then
        error("The requested authoritative pawn is unavailable", 0)
    end
    if exact then
        local driver = state.world.NetDriver
        local controller = match.controller_object
        local connection = valid(controller) and controller.Player or nil
        local membership = 0
        if valid(driver) and valid(connection) and connection:GetFullName() == fields[5]
            and same_object(connection.Driver, driver) and same_object(connection.PlayerController, controller)
            and same_object(controller.Pawn, match.pawn) and not controller:IsLocalController() then
            local connections = driver.ClientConnections
            for index = 1, #connections do
                if same_object(connections[index], connection) then membership = membership + 1 end
            end
        end
        if membership ~= 1 then error("The restored pawn's exact native connection is unavailable", 0) end
    end
    if respawning and (not authored_routes or not restore_health or health ~= match.pawn.MaxHealth)
        or restore_health and (not same_object(match.save, match.pawn.Save) or health > match.pawn.MaxHealth) then
        error("Independent health for this pawn is unavailable or out of range", 0)
    end

    local dx, dy, dz = x - match.x, y - match.y, z - match.z
    if dx * dx + dy * dy + dz * dz > restore_position_tolerance * restore_position_tolerance then
        local moved = match.pawn:K2_TeleportTo({ X = x, Y = y, Z = z }, match.pawn:K2_GetActorRotation())
        if not moved then
            error("No valid teleport location near the saved position", 0)
        end
    end
    if restore_health then
        match.save.PlayerHealth = health
        if respawning then
            match.save.PlayerRegen = health
            if match.save.PlayerHealth ~= health or match.save.PlayerRegen ~= health then
                error("The stock game-over health and regeneration were not verified", 0)
            end
        end
        if health == match.pawn.MaxHealth then
            if slot == "guest" then guest_save.restored(match.pawn) end
            native_death.rearm(match.slot or slot)
        end
    end
    match.pawn:ForceNetUpdate()
    return "pawn restored"
end

local function run_command(fields, state, current_phase)
    local command = fields[2]
    local argument = fields[3]

    if guest_save.active() and stock_save_probes[command] then
        error("Clear the managed native guest save before running a stock-save probe", 0)
    end
    if command == "native-guest-clear" or command == "travel" or command == "leave" then
        native_hazard.clear("co-op session is changing")
    end
    if command == "travel" or command == "leave" then native_rest.clear_hook() end
    if command == "join" or command == "leave" then native_world_view.clear() end
    if command == "host" then
        local carry, dream_kind
        local load_save = #fields >= 5 and #fields <= 7 and fields[4] == "native-save"
        if load_save then
            for index = 6, #fields do
                local option = fields[index]
                local value = option:match("^eather=(.+)$")
                if value and not carry then
                    carry = value
                elseif native_dream.supports(argument) and not dream_kind then
                    dream_kind = option
                else
                    error("Unknown co-op save restore option", 0)
                end
            end
        end
        if (not load_save and #fields ~= 3 and #fields ~= 4) or not supported_map(argument) then
            error("Expected a packaged game map", 0)
        end
        if role ~= "idle" then error("Leave the current session before hosting", 0) end
        if load_save then
            native_host_save.prepare(state, current_phase, fields[5], carry)
            native_world.prepare_loaded_seed(state, current_phase, argument)
        else
            native_world.prepare_seed(state, current_phase, argument, fields[4])
        end
        if dream_kind then native_dream.prepare_duration(state.controller, dream_kind) end
        if expanded_routes then native_authored_arrival.prepare_time(state, current_phase, 0) end
        open_level(state.controller, argument, "listen")
        role = "host"
        expected_map = argument
        return "listen travel requested"
    elseif command == "travel" then
        local blue = #fields == 5 and fields[4] == "blue" and native_bed.profile(argument)
        local destination_route = zone_maps[argument]
        local red = #fields == 5 and fields[4] == "red" and destination_route
            and destination_route.other_arrivals and destination_route.other_arrivals.Red_Tes
        local green = #fields == 5 and fields[4] == "green" and native_green.supports(argument)
        local purple = #fields == 5 and fields[4] == "purple" and purple_arrival.supports(argument)
        local matrix = #fields == 5 and fields[4] == "matrix" and not native_dream.supports(argument)
        local authored = expanded_routes and #fields == 5 and fields[4] == "authored"
            and not native_dream.supports(argument)
        local dream_departure = native_dream.supports(argument) and #fields == 5
            and (fields[4] == "stardust" or fields[4] == "fruiting-stardust")
        local dream_return = native_dream.supports(state.map) and #fields == 4 and fields[4] == "stardust-return"
            and native_bed.profile(argument)
        if not supported_map(argument) or argument == state.map
            or (native_dream.supports(argument) and not dream_departure)
            or (native_dream.supports(state.map) and not (dream_return or dream_departure))
            or (not (blue or red or green or purple or matrix or authored or dream_departure or dream_return)
                and #fields ~= (argument == gold_nexus and 4 or 3)) then
            error("Expected a different qualified co-op map", 0)
        end
        if role ~= "host" or current_phase ~= "hosting" then
            error("Zone travel requires the active listen host", 0)
        end
        if argument == gold_nexus and not matrix
            and not native_transport.authorize_gold_travel(fields[4], state, current_phase) then
            error("Gold Nexus travel requires the exact host inventory debit", 0)
        end
        if blue and not native_blue.authorize_travel(fields[5], state, current_phase) then
            error("Blue Cube travel requires the exact host inventory debit", 0)
        end
        if red and not native_red.authorize_travel(fields[5], state, current_phase) then
            error("Red Cube travel requires the exact host inventory debit", 0)
        end
        if green and not native_green_cube.authorize_travel(fields[5], state, current_phase) then
            error("Green Cube travel requires the exact host inventory debit", 0)
        end
        if purple and not native_purple.authorize_travel(fields[5], state, current_phase) then
            error("Purple Cube travel requires the exact host inventory debit", 0)
        end
        if matrix and not native_matrix.authorize_travel(fields[5], state, current_phase) then
            error("Regression Matrix travel requires the exact host Eather debit", 0)
        end
        if dream_departure then
            if not native_stardust.authorize_travel(fields[4], fields[5], state, current_phase) then
                error("Dream travel requires the recorded Stardust debit", 0)
            end
            native_dream.prepare_duration(state.controller, fields[4])
        end
        guest_save.clear()
        vitals.reset()
        native_world.prepare_travel_seed(state, current_phase, argument)
        if expanded_routes then
            native_authored_arrival.prepare_time(state, current_phase, authored and tonumber(fields[5]) or 0)
        end
        open_level(state.controller, argument, "listen")
        expected_map = argument
        return "zone travel requested"
    elseif command == "native-blue-route" or command == "native-blue-rest-fixture" or command == "native-blue-rest-restore" then
        last_blue_route = nil
        local host = native_transport.scoped_host(state, current_phase)
        local restoring = command == "native-blue-rest-restore"
        if not authored_routes or #fields ~= (restoring and 5 or 3) or not host or host.name ~= argument then
            error("Blue Cube routing requires the exact authoritative host pawn", 0)
        end
        if command == "native-blue-rest-fixture" then
            if os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then error("Fixture host required", 0) end
            last_blue_route = native_bed.select_fixture_rest(state, current_phase)
        elseif restoring then
            last_blue_route = native_bed.restore(state, current_phase, fields[4], fields[5])
        else
            last_blue_route = native_bed.source(state, current_phase)
        end
        last_blue_route.pawn = host.name
        return "qualified stock rest route"
    elseif command == "native-wipe-fallback" then
        last_blue_arrival = nil
        if #fields ~= 2 or not authored_routes or role ~= "host" then
            error("Default stock game-over arrival requires the active host", 0)
        end
        last_blue_arrival = native_bed.wipe_fallback(state, current_phase)
        return "default stock game-over arrival verified"
    elseif command == "native-blue-arrival" then
        last_blue_arrival = nil
        if not authored_routes or (#fields ~= 4 and #fields ~= 5) or argument ~= state.map
            or not native_bed.profile(argument) then
            error("Blue Cube arrival requires the qualified current world", 0)
        end
        if #fields == 5 and not native_blue.authorize_travel(fields[5], state, current_phase) then
            error("Blue Cube movement requires the exact host inventory debit", 0)
        end
        local fallback = fields[4] == "-"
        local itinerary = {destination = argument, bed_id = fallback and "" or fields[4], fallback = fallback}
        last_blue_arrival = native_bed.arrival(state, current_phase, itinerary)
        return "qualified Blue Cube arrival"
    elseif command == "native-red-arrival" then
        if #fields ~= 4 or argument ~= state.map or not native_red.authorize_travel(fields[4], state, current_phase) then
            error("Same-map Red Cube arrival requires the exact host inventory debit", 0)
        end
        return native_zone.position_arrival(state, current_phase, "Red_Tes")
    elseif command == "native-green-options" then
        last_green_options = nil
        local host = native_transport.scoped_host(state, current_phase)
        if #fields ~= 3 or not host or host.name ~= argument then
            error("Green Cube eligibility requires the exact authoritative host pawn", 0)
        end
        local destinations, reputation = native_green.source(state, current_phase)
        local ranks = {}
        for _, faction in ipairs({ 2, 8, 4, 5 }) do
            ranks[#ranks + 1] = tostring(faction) .. ":" .. tostring(reputation[faction].relationship)
        end
        last_green_options = { map = state.map, pawn = host.name, destinations = table.concat(destinations, "|"),
            ranks = table.concat(ranks, "|") }
        return "stock Green Cube eligibility verified"
    elseif command == "native-green-arrival" then
        last_green_arrival = nil
        if (#fields ~= 3 and #fields ~= 4) or argument ~= state.map or not native_green.supports(argument) then
            error("Green Cube arrival requires a qualified current map", 0)
        end
        if #fields == 4 and not native_green_cube.authorize_travel(fields[4], state, current_phase) then
            error("Same-map Green Cube arrival requires the exact host inventory debit", 0)
        end
        last_green_arrival = native_green.arrival(state, current_phase)
        return "qualified stock Green Cube arrival"
    elseif command == "native-authored-arrival" then
        if not expanded_routes or #fields ~= 5 or role ~= "host" or current_phase ~= "hosting" then
            error("Expected a qualified authored route arrival", 0)
        end
        last_authored_arrival = native_authored_arrival.place(state, current_phase, argument, fields[4], fields[5])
        return "stock authored arrival verified"
    elseif command == "native-purple-arrival" then
        last_purple_arrival = nil
        if (#fields ~= 3 and #fields ~= 4) or argument ~= state.map or not purple_arrival.supports(argument) then
            error("Purple Cube arrival requires a qualified current map", 0)
        end
        if #fields == 4 and not native_purple.authorize_travel(fields[4], state, current_phase) then
            error("Same-map Purple Cube arrival requires the exact host inventory debit", 0)
        end
        last_purple_arrival = purple_arrival.arrival(state, current_phase)
        return "qualified stock Purple Cube arrival"
    elseif command == "native-dream-route" then
        last_dream_route = nil
        local host = native_transport.scoped_host(state, current_phase)
        if #fields ~= 4 or not host or host.name ~= argument then
            error("Stardust routing requires the exact authoritative host pawn", 0)
        end
        last_dream_route = native_dream.source(state, current_phase, fields[4])
        last_dream_route.pawn = host.name
        return "qualified stock Stardust route"
    elseif command == "native-dream-effects" then
        last_dream_effects = nil
        local host = native_transport.scoped_host(state, current_phase)
        if #fields ~= 6 or not host or host.name ~= argument then
            error("Stardust effects require the exact authoritative host pawn", 0)
        end
        local tarot = fields[6] == "-" and "" or fields[6]
        last_dream_effects = native_dream.apply_effects(state, current_phase, fields[4], fields[5], tarot)
        last_dream_effects.pawn = host.name
        return "stock Stardust effects verified"
    elseif command == "native-dream-tarot-fixture" then
        if #fields ~= 4 then error("Expected a stock Tarot flag and points", 0) end
        return native_dream.tarot_fixture(state, current_phase, argument, tonumber(fields[4]))
    elseif command == "native-dream-arrival" then
        last_dream_arrival = nil
        if #fields ~= 3 or argument ~= state.map or not native_dream.supports(argument) then
            error("Expected a qualified current dream arrival", 0)
        end
        last_dream_arrival = native_dream.arrival(state, current_phase)
        return "qualified stock dream arrival"
    elseif command == "native-dream-place-guest" then
        if #fields ~= 3 then error("Expected the exact enrolled Slate guest", 0) end
        return native_dream.place_descending_guest(state, current_phase, argument)
    elseif command == "native-dream-power-fixture" then
        if #fields ~= 2 then error("Expected the isolated dream power fixture", 0) end
        return native_dream.power_fixture(state, current_phase)
    elseif command == "native-quest-catalog" then
        if #fields ~= 2 or role ~= "host" or current_phase ~= "hosting" then
            error("The native quest catalog requires the active listen host", 0)
        end
        last_quest_catalog = { world = state.world, rows = native_quest_catalog.rows(state, current_phase, status_row) }
        return "stock host quest catalog read"
    elseif command == "native-broker-position-fixture" then
        if #fields ~= 4 or role ~= "host" or current_phase ~= "hosting" then
            error("The Broker fixture requires an exact authoritative pawn", 0)
        end
        last_broker_position = native_broker_quest.fixture_position(state, current_phase, argument, fields[4])
        return "stock Broker interaction position verified"
    elseif command == "native-broker-inspect" or command == "native-broker-issue"
        or command == "native-broker-issue-fixture" or command == "native-broker-turn-in"
        or command == "native-broker-grant-manifest" then
        local actions = {
            ["native-broker-inspect"] = { count = 3, invoke = native_broker_quest.inspect },
            ["native-broker-issue"] = { count = 4, invoke = native_broker_quest.issue },
            ["native-broker-issue-fixture"] = { count = 4, invoke = native_broker_quest.issue_fixture },
            ["native-broker-turn-in"] = { count = 5, invoke = native_broker_quest.turn_in },
            ["native-broker-grant-manifest"] = { count = 3, invoke = native_broker_quest.fixture_grant_manifest },
        }
        local action = actions[command]
        if #fields ~= action.count or role ~= "host" or current_phase ~= "hosting" then
            error("Stock Broker actions require the exact authoritative interactor", 0)
        end
        local result = action.invoke(state, current_phase, argument, fields[4], fields[5])
        last_broker_quest = { world = state.world, operation = command, target = argument, result = result }
        return "stock Broker quest state verified"
    elseif command == "native-host-save-capture" then
        if #fields ~= 3 or role ~= "host" then error("Expected an isolated host save generation", 0) end
        return native_host_save.capture(state, current_phase, argument)
    elseif command == "native-guest-quest-xp" then
        if #fields ~= 4 or role ~= "host" or current_phase ~= "hosting" or type(guest_save.quest_xp) ~= "function" then
            error("Quest XP requires the exact enrolled native guest", 0)
        end
        last_guest_xp = guest_save.quest_xp(state, current_phase, argument, tonumber(fields[4]))
        return "native guest quest XP verified"
    elseif command == "native-world-flush" then
        if #fields ~= 2 or role ~= "host" or current_phase ~= "hosting" then
            error("Native world checkpoint flush requires an active listen host", 0)
        end
        last_world_flush = native_world_flush.flush(state, current_phase)
        return "stock world checkpoint flush inspected"
    elseif command == "native-rest-inspect" or command == "native-rest" then
        local apply = command == "native-rest"
        if #fields ~= (apply and 5 or 4) or role ~= "host" or current_phase ~= "hosting" then
            error("Stock rest requires the exact active host and bed", 0)
        end
        if apply then
            last_rest = native_rest.apply(state, current_phase, argument, fields[4], tonumber(fields[5]))
        else
            last_rest = native_rest.inspect(state, current_phase, argument, fields[4])
        end
        return "qualified stock rest inspected"
    elseif command == "native-rest-ui-fixture" then
        if #fields ~= 4 or role ~= "host" or current_phase ~= "hosting" then
            error("Stock sleep confirmation requires the exact host", 0)
        end
        return native_rest.ui_fixture(state, current_phase, argument, tonumber(fields[4]))
    elseif command == "native-rest-guest-interaction-fixture" then
        if #fields ~= 3 or (current_phase ~= "hosting" and current_phase ~= "connected") then
            error("Stock rest denial requires the exact enrolled guest", 0)
        end
        return native_rest.guest_fixture(state, current_phase, argument)
    elseif command == "native-world-view-apply" then
        if not world_view_enabled or (#fields ~= 18 and #fields ~= 23)
            or current_phase ~= "connected" or role ~= "client" then
            error("Authoritative sun projection requires the current connected client", 0)
        end
        native_world_view.apply(state, current_phase, {
            epoch = fields[3], sequence = tonumber(fields[4]), map = fields[5], actor = fields[6],
            game_seconds = tonumber(fields[7]), pitch = tonumber(fields[8]), yaw = tonumber(fields[9]),
            roll = tonumber(fields[10]), sun_x = tonumber(fields[11]), sun_y = tonumber(fields[12]),
            sun_z = tonumber(fields[13]), sun_visible = fields[14] == "1", sun_intensity = tonumber(fields[15]),
            sky_r = tonumber(fields[16]), sky_g = tonumber(fields[17]), sky_b = tonumber(fields[18]),
            fog_density = tonumber(fields[19]), fog_falloff = tonumber(fields[20]),
            cloud_bottom_km = tonumber(fields[21]), cloud_height_km = tonumber(fields[22]),
            cloud_density_offset = tonumber(fields[23]),
        })
        return "authenticated host sun and sky projected"
    elseif command == "join" then
        if #fields ~= 3 or not loopback_endpoint(argument) then
            error("Expected the local relay endpoint 127.0.0.1:port", 0)
        end
        if role == "host" or state.server or leaving then
            error("Leave the current session before joining", 0)
        end
        open_level(state.controller, argument, "")
        role = "client"
        was_connected = false
        return "client travel requested"
    elseif command == "leave" then
        if #fields > 3 or (argument and argument ~= main_menu) then
            error("Expected the packaged title-screen map", 0)
        end
        guest_save.clear()
        vitals.reset()
        if state.map ~= main_menu or state.listening then
            open_level(state.controller, main_menu, "")
            leaving = true
        else
            role = "idle"
        end
        return "leaving session"
    elseif command == "restore" or command == "restore-exact" then
        return restore_pawn(fields, state, current_phase)
    end
    local projection_slot
    if (command == "player-state" or command == "player-state-exact") and #fields == 4
        and (fields[4] == "downed" or fields[4] == "standing") then
        if command == "player-state" then
            projection_slot = argument
            if argument == "guest" then guest_save.project(state, current_phase, fields[4]) end
        else
            for _, details in ipairs(state.pawns) do
                if details.name == argument then
                    projection_slot = details.slot or details.kind
                    if details.kind == "guest" and current_phase == "hosting" then
                        guest_save.project(state, current_phase, fields[4], details.name)
                    end
                    break
                end
            end
        end
        if projection_slot == "local" then native_death.project_host(state, current_phase, fields[4]) end
    end
    local result = players.command(fields, state, current_phase)
    if result and projection_slot and fields[4] == "standing" then
        native_death.rearm(projection_slot)
    end
    result = result
        or guest_save.command(fields, state, current_phase)
        or native_death.command(fields, state, current_phase)
        or native_hazard.command(fields, state, current_phase)
        or native_inventory.command(fields, state, current_phase)
        or native_wipe_inventory.command(fields, state, current_phase)
        or native_pickup.command(fields, state, current_phase)
        or native_shop.command(fields, state, current_phase)
        or weapon_inventory.command(fields, state, current_phase)
        or native_guest_weapon.command(fields, state, current_phase)
        or native_guest_npc.command(fields, state, current_phase)
        or native_guest_reload_input.command(fields, state, current_phase)
        or native_loot_actions.command(fields, state, current_phase)
        or native_black_stone.command(fields, state, current_phase)
        or native_zone.command(fields, state, current_phase)
        or native_transport.command(fields, state, current_phase)
        or native_blue.command(fields, state, current_phase)
        or native_red.command(fields, state, current_phase)
        or native_green_cube.command(fields, state, current_phase)
        or native_purple.command(fields, state, current_phase)
        or native_stardust.command(fields, state, current_phase)
        or native_matrix.command(fields, state, current_phase)
        or loot.command(fields, state, current_phase)
        or vitals.command(fields, state, current_phase)
        or wallet.command(fields, state, current_phase)
        or native_world.command(fields, state, current_phase)
    if result then return result end
    error("Unknown command", 0)
end

local function read_command()
    local file = io.open(command_path, "rb")
    if not file then return end
    local content = file:read(32769)
    file:close()
    if not content or #content > 32768 then return end
    local line = content:gsub("[\r\n]+$", "")
    if line == "" or line:find("[\r\n]") then return end
    local fields = {}
    for field in (line .. "\t"):gmatch("([^\t]*)\t") do
        fields[#fields + 1] = field
    end
    local id = fields[1]
    if not id or #id > 80 or not id:match("^[%w_.%-]+$") or seen_commands[id] then
        return
    end
    seen_commands[id] = true
    return fields
end

local function world_view_row(mode, view)
    return status_row("native-world-view", mode, view.map, view.actor, view.epoch or "-", view.sequence,
        view.game_seconds, view.pitch, view.yaw, view.roll, view.sun_x, view.sun_y, view.sun_z,
        view.sun_visible and 1 or 0, view.sun_intensity, view.sky_r or "-", view.sky_g or "-", view.sky_b or "-",
        view.clock_text or "-", view.save_unchanged and 1 or 0, view.sky_verified and 1 or 0,
        view.fog_density or "-", view.fog_falloff or "-", view.cloud_bottom_km or "-", view.cloud_height_km or "-",
        view.cloud_density_offset or "-", view.weather_verified and 1 or 0)
end

local function write_status(state, current_phase, failure)
    sequence = sequence + 1
    local rows = {
        status_row("schema", 1),
        status_row("seq", sequence),
        status_row("ack", acknowledged_id, acknowledged_result, acknowledged_message),
        status_row("session", role, current_phase, state.map, state.clients, state.server and 1 or 0),
    }
    if world_view_enabled and current_phase == "hosting" then
        if not same_object(state.world, host_world_view_world) or host_world_view_check ~= os.time() then
            local checked, view = pcall(native_world_view.observe, state, current_phase)
            host_world_view_world, host_world_view_check = state.world, os.time()
            host_world_view = checked and view or nil
            if host_world_view then host_world_view.sequence = sequence end
        end
        if host_world_view then rows[#rows + 1] = world_view_row("sample", host_world_view) end
    elseif world_view_enabled and current_phase == "connected" then
        host_world_view = nil
        local seen = native_world_view.status(state, current_phase)
        if seen then rows[#rows + 1] = world_view_row("applied", seen) end
    end
    for _, details in ipairs(state.pawns) do
        if finite(details.x) and finite(details.y) and finite(details.z) then
            rows[#rows + 1] = status_row("pawn", details.slot or details.kind, details.name, details.health or "-",
                details.x, details.y, details.z, details.controller, details.max_health or "-")
        end
    end
    local guest_save_row = guest_save.row(status_row)
    if guest_save_row then rows[#rows + 1] = guest_save_row end
    local guest_frame = guest_save.frame_row(status_row)
    if guest_frame then rows[#rows + 1] = guest_frame end
    if type(guest_save.rows) == "function" then
        for _, row in ipairs(guest_save.rows(status_row)) do rows[#rows + 1] = row end
    end
    if type(guest_save.quest_xp) == "function" then
        rows[#rows + 1] = status_row("native-guest-xp-capability", 1)
    end
    if last_guest_xp and last_guest_xp.map == state.map then
        rows[#rows + 1] = status_row("native-guest-xp", last_guest_xp.map, last_guest_xp.pawn,
            last_guest_xp.before, last_guest_xp.after)
    end
    if not failure then
        for _, row in ipairs(players.rows(status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(loot.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        local vitals_ok, vitals_row = pcall(vitals.row, state, current_phase, status_row)
        if vitals_ok and vitals_row then rows[#rows + 1] = vitals_row end
        for _, row in ipairs(wallet.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_inventory.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_wipe_inventory.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_pickup.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_shop.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(weapon_inventory.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_guest_weapon.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_guest_npc.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_guest_reload_input.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_loot_actions.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_black_stone.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_zone.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_transport.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_blue.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_red.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_green_cube.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_purple.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        if last_green_options and last_green_options.map == state.map then
            local options = last_green_options
            rows[#rows + 1] = status_row("native-green-options", options.map, options.pawn, options.destinations, options.ranks)
        end
        if last_green_arrival and last_green_arrival.destination == state.map then
            local arrival = last_green_arrival
            rows[#rows + 1] = status_row("native-green-arrival", arrival.destination, arrival.actor, arrival.placement,
                arrival.x, arrival.y, arrival.z)
        end
        if last_purple_arrival and last_purple_arrival.destination == state.map then
            local arrival = last_purple_arrival
            rows[#rows + 1] = status_row("native-purple-arrival", arrival.destination, arrival.actor, arrival.placement,
                arrival.x, arrival.y, arrival.z)
        end
        if last_authored_arrival and last_authored_arrival.destination == state.map then
            local proof = last_authored_arrival
            rows[#rows + 1] = status_row("native-authored-arrival", proof.destination, proof.actor, proof.placement,
                proof.x, proof.y, proof.z)
        end
        for _, row in ipairs(native_roster.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_stardust.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_dream.rows(status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_matrix.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        if last_quest_catalog and same_object(last_quest_catalog.world, state.world) then
            for _, row in ipairs(last_quest_catalog.rows) do rows[#rows + 1] = row end
        end
        if last_broker_quest and same_object(last_broker_quest.world, state.world) then
            local result = last_broker_quest.result
            rows[#rows + 1] = status_row("native-broker", last_broker_quest.operation, state.map, last_broker_quest.target,
                result.quest_id, result.state, result.step, result.final_step, result.cash, result.xp,
                result.manifest_guid, result.native_cash, result.native_xp, result.changed and 1 or 0,
                result.consumed_guid or "-")
        end
        if last_broker_position and last_broker_position.map == state.map then
            local proof = last_broker_position
            rows[#rows + 1] = status_row("native-broker-position", proof.map, proof.pawn, proof.target, proof.point,
                proof.x, proof.y, proof.z)
        end
        for _, row in ipairs(native_host_save.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        if last_world_flush and last_world_flush.map == state.map then
            local proof = last_world_flush
            rows[#rows + 1] = status_row("native-world-flush", proof.map, proof.state, proof.antfarm, proof.explosives,
                proof.reason or "")
        end
        for _, row in ipairs(native_rest.hook_rows(status_row)) do rows[#rows + 1] = row end
        if last_rest and last_rest.map == state.map then
            local rest = last_rest
            rows[#rows + 1] = status_row("native-rest", rest.map, rest.pawn, rest.actor, rest.bed, rest.level,
                rest.eligible and 1 or 0, rest.reason or "", rest.points, rest.distance, rest.range,
                rest.insomnia and 1 or 0, rest.x, rest.y, rest.z, rest.hours or "-", rest.before or "-",
                rest.after or "-", rest.ping or "")
        end
        if last_dream_route and last_dream_route.source == state.map then
            local route = last_dream_route
            rows[#rows + 1] = status_row("native-dream-route", route.source, route.destination, route.pawn,
                route.arrival, route.visit_seconds, route.startup_seconds, route.fade_seconds,
                route.return_plan.destination, route.return_plan.bed_id, route.kind, route.tarot)
        end
        if last_dream_effects and last_dream_effects.source == state.map then
            local effects = last_dream_effects
            rows[#rows + 1] = status_row("native-dream-effects", effects.source, effects.pawn, effects.kind,
                effects.destination, effects.tarot, effects.before, effects.after, effects.event or "")
        end
        if last_dream_arrival and last_dream_arrival.destination == state.map then
            local arrival = last_dream_arrival
            rows[#rows + 1] = status_row("native-dream-arrival", arrival.destination, arrival.actor,
                arrival.x, arrival.y, arrival.z)
        end
        if last_blue_route and last_blue_route.source == state.map then
            local route = last_blue_route
            rows[#rows + 1] = status_row("native-blue-route", route.source, route.destination, route.bed_id, route.pawn)
        end
        if last_blue_arrival and last_blue_arrival.destination == state.map then
            local arrival = last_blue_arrival
            rows[#rows + 1] = status_row("native-blue-arrival", arrival.destination, arrival.bed_id, arrival.actor,
                arrival.placement, arrival.x, arrival.y, arrival.z, arrival.points, arrival.min_safe_rep)
        end
        for _, row in ipairs(native_death.rows(state, current_phase, status_row)) do
            rows[#rows + 1] = row
        end
        for _, row in ipairs(native_hazard.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        local native_ok, native_rows = pcall(native_world.rows, state, current_phase, status_row)
        if native_ok then
            for _, row in ipairs(native_rows) do rows[#rows + 1] = row end
        end
    end
    if failure then rows[#rows + 1] = status_row("diagnostic", failure) end

    local temporary = status_path .. ".tmp"
    local file = io.open(temporary, "wb")
    if not file then return end
    local written = file:write(table.concat(rows, "\n"), "\n")
    local closed = file:close()
    if not written or not closed then return end

    -- Lua's Windows rename cannot replace an existing destination. Readers
    -- may briefly see no status file, and never see partially written rows.
    if not os.rename(temporary, status_path) then
        os.remove(status_path)
        os.rename(temporary, status_path)
    end
end

local function observe_session()
    local ok, state, current_phase = pcall(function()
        local observed = inspect()
        local observed_phase = phase(observed)
        native_hazard.sync(observed, observed_phase)
        if guest_save.sync(observed, observed_phase) then
            observed = inspect()
            observed_phase = phase(observed)
        end
        return observed, observed_phase
    end)
    if not ok then
        local diagnostic = tostring(state)
        local cleared, failure = pcall(function()
            native_hazard.clear("game bridge observation failed")
            guest_save.clear()
        end)
        if not cleared then diagnostic = diagnostic .. " / " .. tostring(failure) end
        write_status({ map = "", clients = 0, server = false, pawns = {} }, "opening", diagnostic)
        return
    end
    return state, current_phase
end

local function tick()
    local state, current_phase = observe_session()
    if not state then return end
    native_world_view.sync(state, current_phase)
    native_death.sync(state, current_phase)
    native_zone.sync(state, current_phase)
    native_rest.sync_hook(state, current_phase)
    native_black_stone.sync(state, current_phase)
    native_transport.sync(state, current_phase)
    native_blue.sync(state, current_phase)
    native_red.sync(state, current_phase)
    native_green_cube.sync(state, current_phase)
    native_purple.sync(state, current_phase)
    native_stardust.sync(state, current_phase)
    native_matrix.sync(state, current_phase)
    if native_dream.sync(state, current_phase) then
        state, current_phase = observe_session()
        if not state then return end
    end
    local fields = read_command()
    if fields then
        last_quest_catalog = nil
        last_broker_quest = nil
        last_broker_position = nil
        last_world_flush = nil
        last_rest = nil
        last_guest_xp = nil
        native_guest_weapon.reset()
        native_loot_actions.reset()
        native_inventory.reset()
        native_wipe_inventory.reset()
        last_authored_arrival = nil
        local was_guest_bound = guest_save.active()
        local accepted, message = pcall(run_command, fields, state, current_phase)
        acknowledged_id = fields[1]
        acknowledged_result = accepted and "accepted" or "error"
        acknowledged_message = message
        if was_guest_bound or guest_save.active() or fields[2] == "native-exit-arrival"
            or fields[2] == "native-blue-arrival" or fields[2] == "native-wipe-fallback"
            or fields[2] == "native-red-arrival" or fields[2] == "native-green-arrival"
            or fields[2] == "native-purple-arrival" or fields[2] == "native-dream-arrival"
            or fields[2] == "native-dream-place-guest" or fields[2] == "native-matrix-place"
            or fields[2] == "native-broker-position-fixture" or fields[2] == "native-authored-arrival"
            or fields[2] == "native-loot-approach" or fields[2] == "native-dream-stone-position"
            or fields[2] == "restore" or fields[2] == "restore-exact" then
            state, current_phase = observe_session()
            if not state then return end
        else
            current_phase = phase(state)
        end
        -- Ambient damage results are drained by the ordinary tick above.
        if fields[2] ~= "native-guest-damage" and fields[2] ~= "native-host-damage" then
            native_death.sync(state, current_phase)
        end
    end
    local projected, failure = pcall(function()
        vitals.sync(state, current_phase)
        players.sync(state, current_phase)
    end)
    write_status(state, current_phase, not projected and failure or nil)
end

players.start(bridge)
native_death.start(bridge)
guest_save.start(bridge, native_death.clear_scope)
native_zone.start(bridge)
LoopAsync(250, function()
    if not tick_queued then
        tick_queued = true
        ExecuteInGameThread(function()
            local ok, failure = pcall(tick)
            tick_queued = false
            if not ok then print("[SilverfishCoop] " .. tostring(failure) .. "\n") end
        end)
    end
    return false
end)
