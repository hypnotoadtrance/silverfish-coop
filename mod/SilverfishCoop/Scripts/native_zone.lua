local zone = {}
local wall = require("zone_wall")
local routes = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local event_path
local heartbeat_path
local armed
local fixture
local sequence = 0
local requests = 0
local guest_denied = 0
local too_far = 0
local decision = ""
local door_decision = ""
local door_denied = 0
local door_requests = 0
local status = "inactive"
local failure = ""

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function placed_actor(class, name, world)
    if not name or not valid(world) then return end
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and actor:GetFullName():match(":PersistentLevel%.([^%.]+)$") == name
            and same(actor:GetWorld(), world) then
            if match then return end
            match = actor
        end
    end
    return match
end

local function find_fixture(local_pawn, route)
    local world = local_pawn:GetWorld()
    if valid(fixture) and same(fixture:GetWorld(), world) then return fixture end
    fixture = placed_actor("Level_Exit_BP_C", route.fixture, world)
    return fixture
end

local function heartbeat_fresh()
    local file = io.open(heartbeat_path, "rb")
    if not file then return false end
    local text = file:read(32)
    file:close()
    local timestamp = text and text:match("^(%d+)%s*$")
    if not timestamp or #timestamp > 12 then return false end
    local age = os.time() - tonumber(timestamp)
    return age >= 0 and age <= 2
end

local function clear()
    wall.clear()
    if armed and type(SilverfishClearNativeZoneScope) == "function" then
        pcall(SilverfishClearNativeZoneScope)
    end
    armed = nil
    fixture = nil
end

local function wait_for_scope(detail)
    if armed and type(SilverfishPauseNativeZoneScope) == "function" then
        SilverfishPauseNativeZoneScope()
    end
    status, failure = "arming", detail
end

local function emit(map, route)
    if not route or not route.destination then return false end
    local next_sequence = sequence + 1
    local file = io.open(event_path, "ab")
    if not file then return false end
    local tag = route.entrance and "\t" .. route.entrance or ""
    local written = file:write("request\t", tostring(next_sequence), "\t", map, "\t", route.destination, tag, "\n")
    local closed = file:close()
    if not written or not closed then return false end
    sequence = next_sequence
    requests = requests + 1
    return true
end

local function handle_event(map, hosting)
    local value = SilverfishTakeNativeZoneEvent()
    if value == nil then return true end
    decision = value
    if value == "guest_denied" then
        guest_denied = guest_denied + 1
    elseif value == "too_far" then
        too_far = too_far + 1
    elseif value == "request" or value == "door_request" then
        local route = routes[map]
        if value == "door_request" then route = route.door end
        if not hosting or not heartbeat_fresh() or not emit(map, route) then
            SilverfishReleaseNativeZoneRequest()
            decision = "request_unavailable"
            return false
        end
    end
    return true
end

local function handle_door_event()
    if type(SilverfishTakeNativeDoorEvent) ~= "function" then return end
    local value = SilverfishTakeNativeDoorEvent()
    if not value then return end
    door_decision = value
    if value == "guest_denied" then door_denied = door_denied + 1 end
    if value == "travel_requested" then door_requests = door_requests + 1 end
end

function zone.start(bridge)
    if not enabled then return end
    event_path = bridge .. "/native-zone-events"
    heartbeat_path = bridge .. "/native-zone-heartbeat"
end

function zone.sync(state, phase)
    if not enabled or not event_path then return end
    local host = phase == "hosting"
    local route = routes[state.map]
    if not route or (not host and phase ~= "connected") then
        clear()
        status, failure = "inactive", ""
        return
    end

    if type(SilverfishSetNativeZoneScope) ~= "function" or type(SilverfishTakeNativeZoneEvent) ~= "function" then
        wait_for_scope("Waiting for native zone hooks")
        return
    end
    local local_pawn = state.local_pawn and state.local_pawn.pawn
    local remote
    if host then
        for _, details in ipairs(state.pawns) do
            if details.kind == "guest" and valid(details.pawn) then
                if remote then
                    wait_for_scope("Multiple guest pawns in a two-player lab")
                    return
                end
                remote = details.pawn
            end
        end
    end
    if not valid(local_pawn) or (host and state.clients ~= (remote and 1 or 0)) then
        wait_for_scope("Waiting for the native player roster")
        return
    end
    local exit = find_fixture(local_pawn, route)
    if route.fixture and not valid(exit) then
        wall.clear()
        if SilverfishSetNativeZoneScope(local_pawn:GetAddress(), remote and remote:GetAddress() or 0, 0, host, false) then
            armed = state.map
        end
        wait_for_scope("Waiting for the placed " .. route.fixture)
        return
    end

    local accepting = host and heartbeat_fresh()
    wall.sync(state, phase, exit, accepting)
    local refreshed = SilverfishSetNativeZoneScope(local_pawn:GetAddress(), remote and remote:GetAddress() or 0,
        valid(exit) and exit:GetAddress() or 0, host, accepting)
    if not refreshed then
        wait_for_scope("Native zone hooks declined the scope")
        return
    end
    armed = state.map
    handle_door_event()
    if not handle_event(state.map, host) then
        status, failure = "error", "The native zone request could not be written"
        return
    end
    status, failure = "ready", ""
end

function zone.position_arrival(state, phase, tag)
    local route = routes[state.map]
    local is_door = route and route.door and route.door.entrance == tag
    if not enabled or phase ~= "hosting" or not route or (not is_door and (not route.arrival or route.entrance ~= tag)) then
        error("An authored host arrival for the current zone is required", 0)
    end
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    if not valid(pawn) or not pawn:HasAuthority() or not valid(controller) or not controller:IsLocalController()
        or not same(controller.Pawn, pawn) then
        error("The local authoritative arrival pawn is unavailable", 0)
    end

    local world = pawn:GetWorld()
    local match
    for _, marker in ipairs(FindAllOf("LevelExit_Spawn_BP_C") or {}) do
        if valid(marker) and same(marker:GetWorld(), world) then
            local marker_tag = marker.EntranceTag
            if marker_tag and marker_tag:ToString() == tag then
                if match then error("The destination contains multiple matching arrival tags", 0) end
                match = marker
            end
        end
    end
    local name = route.arrival
    local qualified = valid(match) and name and match:GetFullName():match(":PersistentLevel%.([^%.]+)$") == name
    if is_door then
        local door = placed_actor("Dungeon_TransitionDoor_BP_C", route.door.fixture, world)
        local component = valid(door) and door.LevelExit_Spawn_BP
        qualified = valid(component) and same(component.ChildActor, match)
        name = route.door.fixture .. ".LevelExit_Spawn_BP"
    end
    if not qualified or not valid(match.Arrow) then
        error("The authored arrival marker is unavailable", 0)
    end

    local target = match.Arrow:K2_GetComponentLocation()
    local rotation = match.Arrow:K2_GetComponentRotation()
    if not pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z },
        { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }) then
        error("The authored Arrival Arrow has no safe placement", 0)
    end
    controller:SetControlRotation({ Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll })
    pawn:ForceNetUpdate()
    return "native arrival " .. tag .. " at " .. name
end

local function door_command(fields, state, phase, command)
    local route = routes[state.map]
    local slot = fields[3]
    if not enabled or phase ~= "hosting" or armed ~= state.map or status ~= "ready" or not route or not route.door
        or (slot ~= "local" and slot ~= "guest") then
        error("A qualified authored transition door and authoritative pawn are required", 0)
    end
    local pawn
    for _, details in ipairs(state.pawns) do
        if details.kind == slot and valid(details.pawn) and details.pawn:HasAuthority() then
            if pawn then error("More than one pawn occupies the requested slot", 0) end
            pawn = details.pawn
        end
    end
    local door = valid(pawn) and placed_actor("Dungeon_TransitionDoor_BP_C", route.door.fixture, pawn:GetWorld())
    if not valid(door) or not valid(door.Box) then error("The authored transition door is unavailable", 0) end

    if command == "native-door-probe" then
        if #fields ~= 3 then error("Expected native-door-probe local|guest", 0) end
        door:SendInteractionToActor(pawn, door.Box)
        handle_door_event()
        return "native door " .. door_decision
    end

    local position = fields[4]
    if #fields ~= 4 or (position ~= "near" and position ~= "far") then
        error("Expected native-door-position local|guest near|far", 0)
    end
    local origin = door:K2_GetActorLocation()
    local direction = door:GetActorForwardVector()
    local rotation = pawn:K2_GetActorRotation()
    local distance = position == "near" and 180 or 2250
    for _, sign in ipairs({-1, 1}) do
        local destination = { X = origin.X + sign * distance * direction.X,
            Y = origin.Y + sign * distance * direction.Y, Z = origin.Z + 100 }
        if pawn:K2_TeleportTo(destination, rotation) then
            pawn:ForceNetUpdate()
            return "positioned " .. slot .. " " .. position .. " the authored door"
        end
    end
    error("No safe pawn location near the authored door", 0)
end

function zone.command(fields, state, phase)
    local name = fields[2]
    if name == "native-exit-wall-probe" then return wall.command(fields, state, phase) end
    if name == "native-door-position" or name == "native-door-probe" then
        return door_command(fields, state, phase, name)
    end
    if name == "native-exit-arrival" then
        if #fields ~= 3 then error("Expected native-exit-arrival <tag>", 0) end
        return zone.position_arrival(state, phase, fields[3])
    end
    if name ~= "native-exit-position" and name ~= "native-exit-release" then return end
    if not enabled or phase ~= "hosting" or not routes[state.map] or armed ~= state.map or status ~= "ready" then
        error("An active staged native zone host is required", 0)
    end
    if name == "native-exit-release" then
        if #fields ~= 2 then error("Expected native-exit-release", 0) end
        SilverfishReleaseNativeZoneRequest()
        return "native exit request released"
    end
    local slot, position = fields[3], fields[4]
    if #fields ~= 4 or (slot ~= "local" and slot ~= "guest")
        or (position ~= "near" and position ~= "inside" and position ~= "far") then
        error("Expected native-exit-position local|guest near|inside|far", 0)
    end
    local pawn
    for _, details in ipairs(state.pawns) do
        if details.kind == slot and valid(details.pawn) and details.pawn:HasAuthority() then
            if pawn then error("More than one pawn occupies the requested slot", 0) end
            pawn = details.pawn
        end
    end
    if not valid(pawn) or not valid(fixture) or not valid(fixture.Box)
        or not same(pawn:GetWorld(), fixture:GetWorld()) then
        error("The authoritative pawn or exit geometry is unavailable", 0)
    end

    local center = fixture.Box:K2_GetComponentLocation()
    local offsets = position == "inside" and {{0, 0}} or position == "far" and
        {{2250, 0}, {-2250, 0}, {0, 2250}, {0, -2250}} or {{-350, 0}, {350, 0}, {0, -350}, {0, 350}}
    if position == "near" and state.map == "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P" then
        offsets[1], offsets[2] = offsets[2], offsets[1]
    end
    local rotation = pawn:K2_GetActorRotation()
    for _, offset in ipairs(offsets) do
        local destination = {X = center.X + offset[1], Y = center.Y + offset[2], Z = center.Z}
        if pawn:K2_TeleportTo(destination, rotation) then
            pawn:ForceNetUpdate()
            return "positioned " .. slot .. " " .. position .. " the native exit"
        end
    end
    error("No safe pawn location near the staged exit box", 0)
end

function zone.rows(_, _, status_row)
    if not enabled then return {} end
    local rows = wall.rows(status_row)
    rows[#rows + 1] = status_row("native-zone", status, decision, requests, guest_denied, too_far, failure)
    rows[#rows + 1] = status_row("native-door", door_decision, door_denied, door_requests)
    return rows
end

return zone
