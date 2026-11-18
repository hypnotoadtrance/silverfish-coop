local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if not bridge or bridge == "" then return end

local main_menu = "/Game/Classes/Maps/MainMenu_P"
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
        x = location.X,
        y = location.Y,
        z = location.Z,
        health = health,
        save = save,
    }
end

local function inspect()
    local world = current_world()
    local state = { map = "", clients = 0, server = false, pawns = {} }
    if not valid(world) then return state end

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
        if valid(details.save) and save_counts[details.save:GetAddress()] > 1 then
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
    return type(map) == "string" and #map <= 160
        and map:match("^/Game/Classes/Maps/[%w_/]+$") and map ~= main_menu
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
    if restore_health and (not valid(match.save) or health > match.pawn.MaxHealth) then
        error("Independent health for this pawn is unavailable or out of range", 0)
    end

    local dx, dy, dz = x - match.x, y - match.y, z - match.z
    if dx * dx + dy * dy + dz * dz > restore_position_tolerance * restore_position_tolerance then
        local moved = match.pawn:K2_TeleportTo({ X = x, Y = y, Z = z }, match.pawn:K2_GetActorRotation())
        if not moved then
            error("No valid teleport location near the saved position", 0)
        end
    end
    if restore_health then match.save.PlayerHealth = health end
    match.pawn:ForceNetUpdate()
    return "pawn restored"
end

local function run_command(fields, state, current_phase)
    local command = fields[2]
    local argument = fields[3]

    if command == "host" then
        if #fields ~= 3 or not supported_map(argument) then
            error("Expected a packaged game map", 0)
        end
        if role ~= "idle" then error("Leave the current session before hosting", 0) end
        open_level(state.controller, argument, "listen")
        role = "host"
        expected_map = argument
        return "listen travel requested"
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
                details.x, details.y, details.z, details.controller)
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

local function tick()
    local ok, state = pcall(inspect)
    if not ok then
        write_status({ map = "", clients = 0, server = false, pawns = {} }, "opening", state)
        return
    end
    local current_phase = phase(state)
    local fields = read_command()
    if fields then
        local accepted, message = pcall(run_command, fields, state, current_phase)
        acknowledged_id = fields[1]
        acknowledged_result = accepted and "accepted" or "error"
        acknowledged_message = message
        current_phase = phase(state)
    end
    write_status(state, current_phase)
end

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
