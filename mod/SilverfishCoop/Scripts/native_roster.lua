local native_roster = {}

local controller_class = "PlayerControler_FP_C"
local pawn_class = "Player_FP_C"
local connection_class_path = "/Script/Engine.NetConnection"
local max_connections = 16
local last_scope
local next_driver_id = 0
local next_connection_id = 0

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function stock_name(object)
    local name = object:GetFullName()
    if type(name) == "string" and #name > 0 and #name <= 512
        and not name:find("[%c]") and not name:find("Default__", 1, true) then
        return name
    end
end

local function actor_name(actor, class, world)
    if not valid(actor) or not same(actor:GetWorld(), world) or not actor:HasAuthority()
        or actor:IsActorBeingDestroyed() then
        return
    end
    local name = stock_name(actor)
    if name and name:match("^" .. class .. " ") then return name end
end

local function qualified_host(state)
    local world, controller = state.world, state.controller
    local host = state.local_pawn
    if not valid(world) or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map
        or not host or not same(controller, host.controller_object)
        or not actor_name(controller, controller_class, world) or not controller:IsLocalController()
        or not actor_name(host.pawn, pawn_class, world) or not same(controller.Pawn, host.pawn)
        or not same(host.pawn.Controller, controller) then
        return
    end
    return world
end

local function participant(connection, world)
    local controller = connection.PlayerController
    local name = actor_name(controller, controller_class, world)
    if not name or controller:IsLocalController() or not same(controller.Player, connection)
        or not same(connection.OwningActor, controller) then
        return "joining", "-", "-"
    end
    if valid(controller.NetConnection) and not same(controller.NetConnection, connection) then
        return "joining", "-", "-"
    end

    local pawn = controller.Pawn
    local pawn_name = actor_name(pawn, pawn_class, world)
    if not pawn_name or not same(pawn.Controller, controller) then return "joining", name, "-" end
    return "ready", name, pawn_name, pawn
end

local function census(state)
    local world = qualified_host(state)
    if not world then return nil, "host_unavailable" end
    local driver = world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) then
        return nil, "listen_driver_unavailable"
    end
    local driver_name = stock_name(driver)
    if not driver_name then return nil, "listen_driver_identity_unavailable" end
    local connections = driver.ClientConnections
    local count = connections:GetArrayNum()
    if type(count) ~= "number" or not math.tointeger(count) or count < 0 or count > max_connections
        or count ~= state.clients then
        return nil, "connection_count_unqualified"
    end
    local connection_class = count > 0 and StaticFindObject(connection_class_path)
    if count > 0 and not valid(connection_class) then return nil, "connection_class_unavailable" end

    local observed = { world = world:GetAddress(), driver = driver:GetAddress(), driver_name = driver_name, guests = {} }
    local connection_addresses, connection_names, ready_controllers, ready_pawns = {}, {}, {}, {}
    -- UE4SS TArray reads use one-based indices. Stay within the observed length.
    for index = 1, count do
        local connection = connections[index]
        if not valid(connection) or not connection:IsA(connection_class) or not same(connection.Driver, driver) then
            return nil, "connection_membership_unqualified"
        end
        local address, name = connection:GetAddress(), stock_name(connection)
        if not name or connection_addresses[address] or connection_names[name] then
            return nil, "connection_identity_ambiguous"
        end
        connection_addresses[address], connection_names[name] = true, true

        local status, controller, pawn, pawn_object = participant(connection, world)
        if status == "ready" then
            if ready_controllers[controller] or ready_pawns[pawn] then return nil, "participant_identity_ambiguous" end
            ready_controllers[controller], ready_pawns[pawn] = true, true
        end
        observed.guests[#observed.guests + 1] = {
            address = address, name = name, status = status, controller = controller, pawn = pawn,
            pawn_object = pawn_object,
        }
    end
    return observed
end

function native_roster.rows(state, phase, status_row)
    if phase ~= "hosting" or not state.listening or state.server then
        last_scope = nil
        return {}
    end
    local ok, observed, reason = pcall(census, state)
    if not ok then observed, reason = nil, "reflection_unavailable" end
    if not observed then
        return { status_row("native-roster-state", "unavailable", "-", state.clients or 0, reason) }
    end

    local previous
    if last_scope and last_scope.world == observed.world and last_scope.driver == observed.driver
        and last_scope.driver_name == observed.driver_name then
        observed.id, previous = last_scope.id, last_scope.by_address
    else
        next_driver_id = next_driver_id + 1
        observed.id = "d" .. next_driver_id
        previous = {}
    end
    observed.by_address = {}
    local rows = { status_row("native-roster-state", "qualified", observed.id, #observed.guests, "") }
    for _, guest in ipairs(observed.guests) do
        local prior = previous[guest.address]
        local identity
        if prior and prior.name == guest.name then
            identity = prior.id
        else
            next_connection_id = next_connection_id + 1
            identity = "c" .. next_connection_id
        end
        observed.by_address[guest.address] = { id = identity, name = guest.name }
        rows[#rows + 1] = status_row("native-roster", guest.status, identity, guest.name, guest.controller, guest.pawn)
    end
    last_scope = observed
    return rows
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

function native_roster.verify_party_departure(state, phase, fields, stock_crossing)
    if #fields < 8 or #fields > 24 or (#fields - 8) % 4 ~= 0 then return "native_roster_unavailable" end
    if phase ~= "hosting" or state.map ~= fields[4] or not last_scope or last_scope.id ~= fields[6] then
        return "travel_source_changed"
    end
    local host = state.local_pawn
    if not host or host.name ~= fields[7] or host.controller ~= fields[8] then return "travel_source_changed" end
    local observed = census(state)
    if not observed then return "native_roster_unavailable" end
    if observed.world ~= last_scope.world or observed.driver ~= last_scope.driver
        or observed.driver_name ~= last_scope.driver_name then
        return "travel_source_changed"
    end
    if #observed.guests ~= (#fields - 8) / 4 then return "native_roster_unavailable" end
    local indexed = {}
    for _, guest in ipairs(observed.guests) do
        local prior = last_scope.by_address[guest.address]
        if not prior or prior.name ~= guest.name or guest.status ~= "ready" then return "native_roster_unavailable" end
        indexed[prior.id] = guest
    end

    local location = host.pawn:K2_GetActorLocation()
    if not (finite(location.X) and finite(location.Y) and finite(location.Z)) then
        return "game_snapshot_unavailable"
    end
    for index = 9, #fields, 4 do
        local id = fields[index]
        local guest = indexed[id]
        if not guest or guest.name ~= fields[index + 1] or guest.controller ~= fields[index + 2]
            or guest.pawn ~= fields[index + 3] then
            return "native_roster_unavailable"
        end
        indexed[id] = nil
        local other = guest.pawn_object:K2_GetActorLocation()
        local dx, dy, dz = other.X - location.X, other.Y - location.Y, other.Z - location.Z
        local squared_distance = dx * dx + dy * dy + dz * dz
        if not finite(squared_distance) then return "game_snapshot_unavailable" end
        if not stock_crossing and squared_distance > 1500 * 1500 then return "travel_blocked:" .. ((index - 9) // 4 + 1) end
    end
end

return native_roster
