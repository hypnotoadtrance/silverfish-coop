-- Invoke from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local environment = {
    SILVERFISH_COOP_NATIVE_DEATH = "cpp",
    SILVERFISH_COOP_NATIVE_EVENTS = "1",
    SILVERFISH_COOP_NATIVE_ZONE = "1",
}
local restore_kind, projected, damage_pending = "restored", false, false
local world, controller, pawn, guest
local stock_health, damage_events, replications = 0, 0, 0
local fail_readback = false
local token = 1

os.getenv = function(key) return environment[key] end
io.open = function(path, mode)
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

package.loaded.zone_maps = { [map] = { entrance = "ShortRoad" } }
package.loaded.vitals = {}
package.loaded.guest_save = { matches = function(candidate) return candidate == guest end }
package.loaded.players = { is_downed = function(candidate) return projected and candidate == pawn end }
package.loaded.native_host_save = {
    rows = function(state, phase, status_row)
        assert(state.local_pawn.pawn == pawn and phase == "hosting")
        return { status_row("native-host-save", restore_kind, "SFCoop_exact_pending_slot", map, pawn:GetFullName()) }
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
    assert(not ok and message == code and stock_health == 0 and replications == 0 and damage_events == 0, message)
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
assert(stock_health == 0 and replications == 0)
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
native.project_host(state, "hosting", "standing")
assert(stock_health == 25 and detached.PlayerHealth == 250 and replications == 2 and damage_events == 1)
print("native restored host revive proof passed")
