-- Invoke from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local environment = {
    SILVERFISH_COOP_NATIVE_DEATH = "cpp",
    SILVERFISH_COOP_NATIVE_EVENTS = "1",
    SILVERFISH_COOP_NATIVE_ZONE = "1",
}
local restore_kind, projected, damage_pending = "restored", false, false
local embedded = arg[1] == "embedded"
local fifo_available, fifo_attempts = not embedded, 0
if embedded then environment.SILVERFISH_COOP_EMBEDDED_ENGINE = "1" end
local world, controller, pawn, guest
local regenerated_health = 1.3552319555
local stock_health, damage_events, replications = regenerated_health, 0, 0
local fail_readback = false
local token = 1

os.getenv = function(key) return environment[key] end
io.open = function(path, mode)
    assert(not embedded, "Embedded death receipts must stay in memory")
    if path:find("native-events-heartbeat", 1, true) and mode == "rb" then
        return { read = function() return tostring(os.time()) end, close = function() return true end }
    end
    assert(path == "mock/death.tsv" and mode == "ab")
    return {
        write = function(_, action, slot)
            assert(action == "downed\t" and slot == "local")
            damage_events = damage_events + 1
            return true
        end,
        close = function() return true end,
    }
end

function SilverfishReadLocalNativeEventsHeartbeat()
    if embedded then return true, os.time() end
end

function SilverfishEnqueueLocalBridgeInput(row)
    assert(embedded and row == "downed\tlocal\t1\tcanonical host pawn\n")
    fifo_attempts = fifo_attempts + 1
    if not fifo_available then return false end
    damage_events = damage_events + 1
    return true
end

local function object(address, name, data)
    data = data or {}
    data.IsValid = function() return true end
    data.GetAddress = function() return address end
    data.GetFullName = function() return name end
    data.HasAuthority = function() return true end
    data.GetWorld = function() return world end
    return data
end

world = object(1, "stock world")
local canonical = setmetatable(object(2, "canonical stock save"), {
    __index = function(_, key)
        if key == "PlayerHealth" then return stock_health end
    end,
    __newindex = function(table, key, value)
        if key == "PlayerHealth" then
            stock_health = fail_readback and value == 25 and 12 or value
        else
            rawset(table, key, value)
        end
    end,
})
local detached = object(3, "detached stock save", { PlayerHealth = 250 })
pawn = object(4, "canonical host pawn", {
    Save = canonical, Dead = false, MaxHealth = 250,
    ForceNetUpdate = function() replications = replications + 1 end,
})
guest = object(5, "detached guest pawn", { Save = detached, Dead = false })
controller = object(6, "stock host controller", { Pawn = pawn, IsLocalController = function() return true end })
pawn.Controller = controller
local instance = object(7, "stock game instance", { SaveSlot_Ref = canonical })
local statics = object(8, "stock statics", {
    GetGameInstance = function(_, candidate) assert(candidate == controller); return instance end,
})

function StaticFindObject(path)
    assert(path == "/Script/Engine.Default__GameplayStatics")
    return statics
end

function SilverfishNativeDeathReady() return true end
function SilverfishClearNativeDeathScope() end
function SilverfishSetNativeDeathScope(host, remote)
    assert(host == pawn:GetAddress() and remote == guest:GetAddress())
    token = token + 1
    return true, token
end
function SilverfishTakeScopedNativeDeathHit(address, expected_token)
    assert(expected_token == token)
    if address == pawn:GetAddress() and damage_pending then
        damage_pending = false
        return true
    end
    return false
end

function SilverfishHasScopedNativeDeathHit(expected_token)
    assert(expected_token == token)
    return damage_pending
end

package.loaded.zone_maps = { [map] = { entrance = "ShortRoad" } }
package.loaded.vitals = {}
package.loaded.guest_save = { matches = function(candidate) return candidate == guest end }
package.loaded.players = { is_downed = function(candidate) return projected and candidate == pawn end }
package.loaded.native_host_save = {
    restored_save = function(state, phase, expected)
        assert(state.local_pawn.pawn == pawn and phase == "hosting" and expected == pawn)
        if restore_kind == "restored" then return canonical end
    end,
}

local native = require("native_death")
local local_pawn = { name = pawn:GetFullName(), pawn = pawn, kind = "local" }
local guest_pawn = { name = guest:GetFullName(), pawn = guest, kind = "guest" }
local state = {
    map = map, world = world, controller = controller, listening = true, server = false,
    clients = 0, local_pawn = local_pawn, pawns = { local_pawn },
}

local function assert_refused(code)
    local ok, message = pcall(native.project_host, state, "hosting", "standing")
    assert(not ok and message == code
        and stock_health == regenerated_health and replications == 0 and damage_events == 0, message)
end

native.start("mock")
native.sync(state, "hosting")
restore_kind = "captured"
projected = true
assert_refused("native_restored_host_revive_scope_unavailable")
state.clients, state.pawns = 1, { local_pawn, guest_pawn }
native.sync(state, "hosting")

projected = false
native.project_host(state, "hosting", "standing")
assert(stock_health == regenerated_health and replications == 0)
projected = true

instance.SaveSlot_Ref = detached
assert_refused("native_restored_host_revive_scope_unavailable")
instance.SaveSlot_Ref = canonical
fail_readback = true
assert_refused("mutation_unverified_native_host_revive")
fail_readback = false

native.project_host(state, "hosting", "standing")
assert(stock_health == 25 and detached.PlayerHealth == 250 and replications == 1 and damage_events == 0)
native.rearm("local")
native.project_host(state, "hosting", "standing")
assert(stock_health == 25 and replications == 1 and damage_events == 0)

stock_health, damage_pending = 0, true
native.sync(state, "hosting")
if embedded then
    assert(fifo_attempts == 1 and damage_events == 0 and native.has_pair(pawn, guest))
    fifo_available = true
    native.sync(state, "hosting")
    native.sync(state, "hosting")
    assert(fifo_attempts == 2)
end
native.project_host(state, "hosting", "standing")
assert(stock_health == 25 and detached.PlayerHealth == 250 and replications == 2 and damage_events == 1)
if embedded then
    print("native restored host revive and in-memory backpressure proof passed")
    return
end
local host_heartbeat = require("native_event_heartbeat")
local file_reads, file_stamp = 0, "1234\n"
os.time = function() return 1234 end
io.open = function(path, mode)
    assert(path == "mock/native-events-heartbeat" and mode == "rb")
    file_reads = file_reads + 1
    return { read = function(_, size) assert(size == 32); return file_stamp end,
        close = function() end }
end
local heartbeat_path = "mock/native-events-heartbeat"
assert(host_heartbeat.fresh(heartbeat_path))
file_stamp = "0000000001234\n"
assert(host_heartbeat.fresh(heartbeat_path) and not host_heartbeat.fresh(heartbeat_path, 12))
file_stamp = "bad input"
assert(not host_heartbeat.fresh(heartbeat_path))

environment.SILVERFISH_COOP_EMBEDDED_ENGINE = "1"
package.loaded.native_event_heartbeat = nil
local local_heartbeat = require("native_event_heartbeat")
local attached, local_stamp = true, 1232
SilverfishReadLocalNativeEventsHeartbeat = function() return attached, local_stamp end
file_stamp = "1234\n"
local reads_before_local = file_reads
assert(local_heartbeat.fresh(heartbeat_path, 12))
local_stamp = 1231
assert(not local_heartbeat.fresh(heartbeat_path, 12))
local_stamp = 1235
assert(not local_heartbeat.fresh(heartbeat_path, 12))
local_stamp = nil
assert(not local_heartbeat.fresh(heartbeat_path, 12))
attached, local_stamp = false, 1234
assert(not local_heartbeat.fresh(heartbeat_path, 12))
SilverfishReadLocalNativeEventsHeartbeat = nil
assert(not local_heartbeat.fresh(heartbeat_path, 12))
assert(reads_before_local == file_reads)
print("native restored host revive and heartbeat proof passed")
