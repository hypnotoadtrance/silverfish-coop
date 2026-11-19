local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if not bridge or bridge == "" then return end

local guest_save = require("guest_save")
local loot = require("loot")
local native_bed = require("native_bed")
local native_blue = require("native_blue")
local native_death = require("native_death")
local native_hazard = require("native_hazard")
local native_inventory = require("native_inventory")
local native_pickup = require("native_pickup")
local native_shop = require("native_shop")
local native_transport = require("native_transport")
local native_world = require("native_world")
local native_zone = require("native_zone")
local players = require("players")
local vitals = require("vitals")
local wallet = require("wallet")
local weapon_inventory = require("weapon_inventory")
local zone_maps = require("zone_maps")

local main_menu = "/Game/Classes/Maps/MainMenu_P"
local gold_nexus = "/Game/Classes/Maps/Dreams/GoldNexus_P"
local blue_destinations = {
    ["/Game/Classes/Maps/Maps_OverWorld/Observitory_P"] = true,
    ["/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"] = true,
}
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
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
local last_blue_route, last_blue_arrival

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
    local state = { map = "", clients = 0, server = false, pawns = {} }
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
    return route ~= nil and (route.entrance == nil or authored_routes)
end

local function loopback_endpoint(endpoint)
    local port = endpoint and endpoint:match("^127%.0%.0%.1:(%d%d?%d?%d?%d?)$")
    return port ~= nil and tonumber(port) >= 1 and tonumber(port) <= 65535
end

local function restore_pawn(fields, state, current_phase)
    local slot = fields[3]
    local restore_health = fields[4] ~= "-"
    local health = tonumber(fields[4])
    local x, y, z = tonumber(fields[5]), tonumber(fields[6]), tonumber(fields[7])
    if role ~= "host" or current_phase ~= "hosting" then
        error("Pawn restoration requires the active listen host", 0)
    end
    if #fields ~= 7 or (slot ~= "local" and slot ~= "guest")
        or (restore_health and (not finite(health) or health < 0))
        or not finite(x) or not finite(y) or not finite(z)
        or math.abs(x) > 100000000 or math.abs(y) > 100000000 or math.abs(z) > 100000000 then
        error("Invalid restore arguments", 0)
    end

    local match
    for _, details in ipairs(state.pawns) do
        if details.kind == slot then
            if match then error("Multiple pawns occupy the requested slot", 0) end
            match = details
        end
    end
    if not match or not match.pawn:HasAuthority() then
        error("The requested authoritative pawn is unavailable", 0)
    end
    if restore_health and (not same_object(match.save, match.pawn.Save) or health > match.pawn.MaxHealth) then
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
        if health == match.pawn.MaxHealth then
            if slot == "guest" then guest_save.restored(match.pawn) end
            native_death.rearm(slot)
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
    if command == "host" then
        if #fields ~= 3 or not supported_map(argument) then
            error("Expected a packaged game map", 0)
        end
        if role ~= "idle" then error("Leave the current session before hosting", 0) end
        open_level(state.controller, argument, "listen")
        role = "host"
        expected_map = argument
        return "listen travel requested"
    elseif command == "travel" then
        local blue = #fields == 5 and fields[4] == "blue" and blue_destinations[argument]
        if not supported_map(argument) or argument == state.map
            or (not blue and #fields ~= (argument == gold_nexus and 4 or 3)) then
            error("Expected a different qualified co-op map", 0)
        end
        if role ~= "host" or current_phase ~= "hosting" then
            error("Zone travel requires the active listen host", 0)
        end
        if argument == gold_nexus and not native_transport.authorize_gold_travel(fields[4], state, current_phase) then
            error("Gold Nexus travel requires the exact host inventory debit", 0)
        end
        if blue and not native_blue.authorize_travel(fields[5], state, current_phase) then
            error("Blue Cube travel requires the exact host inventory debit", 0)
        end
        guest_save.clear()
        vitals.reset()
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
    elseif command == "native-blue-arrival" then
        last_blue_arrival = nil
        if not authored_routes or (#fields ~= 4 and #fields ~= 5) or argument ~= state.map
            or not blue_destinations[argument] then
            error("Blue Cube arrival requires the qualified current world", 0)
        end
        if #fields == 5 and not native_blue.authorize_travel(fields[5], state, current_phase) then
            error("Blue Cube movement requires the exact host inventory debit", 0)
        end
        local fallback = fields[4] == "-"
        local itinerary = {destination = argument, bed_id = fallback and "" or fields[4], fallback = fallback}
        last_blue_arrival = native_bed.arrival(state, current_phase, itinerary)
        return "qualified Blue Cube arrival"
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
    elseif command == "restore" then
        return restore_pawn(fields, state, current_phase)
    end
    if command == "player-state" and #fields == 4 and argument == "guest"
        and (fields[4] == "downed" or fields[4] == "standing") then
        guest_save.project(state, current_phase, fields[4])
    end
    if command == "player-state" and #fields == 4 and argument == "local" then
        native_death.project_host(state, current_phase, fields[4])
    end
    local result = players.command(fields, state, current_phase)
    if result and fields[2] == "player-state" and fields[4] == "standing" then
        native_death.rearm(fields[3])
    end
    result = result
        or guest_save.command(fields, state, current_phase)
        or native_death.command(fields, state, current_phase)
        or native_hazard.command(fields, state, current_phase)
        or native_inventory.command(fields, state, current_phase)
        or native_pickup.command(fields, state, current_phase)
        or native_shop.command(fields, state, current_phase)
        or weapon_inventory.command(fields, state, current_phase)
        or native_zone.command(fields, state, current_phase)
        or native_transport.command(fields, state, current_phase)
        or native_blue.command(fields, state, current_phase)
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
    local content = file:read(4097)
    file:close()
    if not content or #content > 4096 then return end
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

local function write_status(state, current_phase, failure)
    sequence = sequence + 1
    local rows = {
        status_row("schema", 1),
        status_row("seq", sequence),
        status_row("ack", acknowledged_id, acknowledged_result, acknowledged_message),
        status_row("session", role, current_phase, state.map, state.clients, state.server and 1 or 0),
    }
    for _, details in ipairs(state.pawns) do
        if finite(details.x) and finite(details.y) and finite(details.z) then
            rows[#rows + 1] = status_row("pawn", details.kind, details.name, details.health or "-",
                details.x, details.y, details.z, details.controller, details.max_health or "-")
        end
    end
    local guest_save_row = guest_save.row(status_row)
    if guest_save_row then rows[#rows + 1] = guest_save_row end
    if not failure then
        for _, row in ipairs(players.rows(status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(loot.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        local vitals_ok, vitals_row = pcall(vitals.row, state, current_phase, status_row)
        if vitals_ok and vitals_row then rows[#rows + 1] = vitals_row end
        for _, row in ipairs(wallet.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_inventory.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_pickup.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_shop.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(weapon_inventory.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_zone.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_transport.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
        for _, row in ipairs(native_blue.rows(state, current_phase, status_row)) do rows[#rows + 1] = row end
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
    native_death.sync(state, current_phase)
    native_zone.sync(state, current_phase)
    native_transport.sync(state, current_phase)
    native_blue.sync(state, current_phase)
    local fields = read_command()
    if fields then
        local was_guest_bound = guest_save.active()
        local accepted, message = pcall(run_command, fields, state, current_phase)
        acknowledged_id = fields[1]
        acknowledged_result = accepted and "accepted" or "error"
        acknowledged_message = message
        if was_guest_bound or guest_save.active() or fields[2] == "native-exit-arrival"
            or fields[2] == "native-blue-arrival" or fields[2] == "restore" then
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
