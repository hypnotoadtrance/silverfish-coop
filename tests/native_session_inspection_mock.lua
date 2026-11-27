-- Run from the repository root with the pinned Lua CLI.
local path = "mod/SilverfishCoop/Scripts/main.lua"
local script = assert(io.open(path, "r"))
local source = script:read("*a")
script:close()
local begin_at = assert(source:find("local function controller_in_world(", 1, true))
local end_at = assert(source:find("function status_publication.session_collection(", begin_at, true))

local address = 0
local function object(fields)
    address = address + 1
    fields.address = address
    fields.IsValid = function(self) return not self.invalid end
    fields.GetAddress = function(self) return self.address end
    return fields
end

local other_world = object({})
local world = object({})
local driver = object({ConnectionTimeout = 110, ClientConnections = {}})
world.NetDriver = driver

local save = object({PlayerHealth = 300})
local function pawn(name)
    return object({
        name = "Player_FP_C /Game/Test.Map:PersistentLevel." .. name,
        Save = save,
        MaxHealth = 350,
        K2_GetActorLocation = function() return {X = 10, Y = 20, Z = 30} end,
        GetFullName = function(self) return self.name end,
        GetWorld = function() return world end,
        IsActorBeingDestroyed = function() return false end,
    })
end

local reads = {}
local function controller(name, actor, is_local)
    local counts = {world = 0, destroyed = 0, local_player = 0, name = 0}
    reads[name] = counts
    return object({
        name = "PlayerControler_FP_C /Game/Test.Map:PersistentLevel." .. name,
        world = world,
        Pawn = actor,
        GetFullName = function(self)
            counts.name = counts.name + 1
            return self.name
        end,
        GetWorld = function(self)
            counts.world = counts.world + 1
            return self.world
        end,
        IsActorBeingDestroyed = function(self)
            counts.destroyed = counts.destroyed + 1
            return self.destroyed == true
        end,
        IsLocalController = function()
            counts.local_player = counts.local_player + 1
            return is_local
        end,
    })
end

local host_actor, zulu_actor, alpha_actor = pawn("Host"), pawn("Zulu"), pawn("Alpha")
local host = controller("HostController", host_actor, true)
local zulu = controller("ZuluController", zulu_actor, false)
local alpha = controller("AlphaController", alpha_actor, false)
local listed = {host, zulu, alpha}
local get_controller = host
local statics = object({GetPlayerController = function() return get_controller end})
local lookup_calls = 0
local env = setmetatable({
    role = "host",
    expected_map = "/Game/Test",
    connection_timeout_seconds = 110,
    status_publication = {
        gameplay_statics = statics,
    },
    vitals = {active = function() return false end},
    current_world = function() return world end,
    map_package = function() return "/Game/Test" end,
    finite = function(value) return type(value) == "number" and math.abs(value) < math.huge end,
    valid = function(value) return value ~= nil and value:IsValid() end,
    same_object = function(first, second) return first == second end,
    FindAllOf = function(class)
        assert(class == "PlayerControler_FP_C")
        lookup_calls = lookup_calls + 1
        return listed
    end,
}, {__index = _G})
local inspect = assert(load(source:sub(begin_at, end_at - 1) .. "\nreturn inspect", "@" .. path, "t", env))()
env.status_publication.find_player_pawns = function()
    return {host_actor, zulu_actor, alpha_actor}
end

driver.ClientConnections = {object({PlayerController = zulu}), object({PlayerController = alpha}), object({})}
local state = inspect()
assert(lookup_calls == 0 and state.world == world and state.clients == 3 and state.listening and not state.server)
assert(state.map == "/Game/Test" and state.connection_timeout == 110)
assert(state.controller == host and state.local_pawn == state.pawns[1] and #state.pawns == 3)
assert(state.pawns[1].pawn == host_actor and state.pawns[2].pawn == zulu_actor and state.pawns[3].pawn == alpha_actor)
assert(state.pawns[2].controller == zulu.name and state.pawns[2].controller_object == zulu)
assert(state.pawns[2].kind == "guest" and state.pawns[2].x == 10 and state.pawns[2].y == 20 and state.pawns[2].z == 30)
assert(state.pawns[2].health == nil and state.pawns[2].save == nil and state.pawns[2].max_health == 350)
assert(state.guests[1] == state.pawns[3] and state.guests[1].slot == "guest")
assert(state.guests[2] == state.pawns[2] and state.guests[2].slot == "guest2")
for _, calls in pairs(reads) do
    assert(calls.world == 1 and calls.destroyed == 1 and calls.local_player == 1 and calls.name == 1)
end

alpha.world = other_world
listed = {zulu, alpha, host}
state = inspect()
assert(lookup_calls == 1 and #state.pawns == 2 and state.local_pawn == state.pawns[2])
assert(state.pawns[1].pawn == zulu_actor and state.pawns[2].pawn == host_actor)
assert(#state.guests == 1 and state.guests[1].pawn == zulu_actor)
alpha.world = world

get_controller = nil
state = inspect()
assert(lookup_calls == 2 and #state.pawns == 3 and state.pawns[1].pawn == zulu_actor)
assert(state.pawns[2].pawn == alpha_actor and state.local_pawn == state.pawns[3])
get_controller = host

env.role = "client"
driver.ServerConnection = object({})
state = inspect()
assert(lookup_calls == 2 and state.server and state.controller == host and state.local_pawn == state.pawns[1])
assert(#state.pawns == 3 and state.pawns[2].pawn == zulu_actor and state.pawns[3].pawn == alpha_actor)
assert(state.pawns[2].controller == "" and state.pawns[2].controller_object == nil)
assert(state.guests[1].pawn == alpha_actor and state.guests[2].pawn == zulu_actor)
