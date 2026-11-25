package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local clock, input_tick, blocked = 10, nil, true
local native_queue, published, previewed = {}, {}, {}
local original_getenv = os.getenv
function os.getenv(name)
    if name == "SILVERFISH_COOP_EMBEDDED_ENGINE" then return "1" end
    return original_getenv(name)
end

function LoopInGameThreadWithDelay(delay, callback)
    if delay == 16 then input_tick = callback end
end

function SilverfishSetNativeInteractScope() return true end
function SilverfishClearNativeInteractScope() end
function SilverfishTakeNativeInteractPress(address)
    local component = table.remove(native_queue, 1)
    if component then return 1, address, component end
end
function SilverfishEnqueueLocalBridgeInput(line)
    if blocked then return false end
    published[#published + 1] = line
    return true
end

local function no_action() end
package.loaded.native_downed_ai = {sync = no_action}
package.loaded.native_bag_projection = {focused_target = no_action}
package.loaded.native_guest_drop_projection = {take_press = no_action}
package.loaded.native_authored_pickup = {take_press = no_action, expire_previews = no_action}
package.loaded.native_enemy_shell_view = {focused_target = no_action}
package.loaded.native_ladder = {focused_target = no_action, release_target = no_action, sync = no_action}
package.loaded.native_stock_switch = {focused_target = no_action}
package.loaded.native_critters = {focused_target = no_action}
package.loaded.native_stock_door = {
    focused_target = function(_, component) return component end,
    preview_input = function(_, target) previewed[#previewed + 1] = target end,
}

function StaticFindObject()
    return {GetRealTimeSeconds = function() return clock end}
end
function io.open() error("resident interaction must use the memory bridge") end

local players = require("players")
players.start("private")

local world = {IsValid = function() return true end, GetAddress = function() return 1 end}
local pawn = {
    Dead = false, IsValid = function() return true end,
    IsActorBeingDestroyed = function() return false end, IsLocallyControlled = function() return true end,
    GetFullName = function() return "Player_FP_C guest" end,
    GetAddress = function() return 2 end, GetWorld = function() return world end,
}
local game = {world = world, pawns = {{pawn = pawn, name = pawn:GetFullName(), slot = "local"}}}

native_queue = {"door-a", "door-b"}
players.sync(game, "connected")
assert(#published == 0 and table.concat(previewed, ",") == "door-a,door-b")
native_queue = {"door-c"}
input_tick()
assert(#native_queue == 1 and #previewed == 2, "stop taking new receipts until earlier input is delivered")

blocked = false
input_tick()
assert(table.concat(published) == "interact\t1\tdoor-a\ninteract\t2\tdoor-b\ninteract\t3\tdoor-c\n")
input_tick()
assert(#published == 3 and #previewed == 3, "every press and preview happens once")

blocked = true
native_queue = {"stale-after-transition"}
input_tick()
players.sync(game, "title")
blocked = false
players.sync(game, "connected")
assert(#published == 3, "leaving the session discards the previous input")

blocked = true
native_queue = {"expired"}
input_tick()
clock = clock + 2
blocked = false
input_tick()
assert(#published == 3, "do not replay old inputs after a sustained stall")
