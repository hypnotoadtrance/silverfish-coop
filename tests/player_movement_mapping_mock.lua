package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function upvalue(fn, name)
    for index = 1, math.huge do
        local field, value = debug.getupvalue(fn, index)
        if field == name then return value end
        if not field then error("Missing " .. name) end
    end
end

local players = require("players")
local sync_input = upvalue(players.sync, "sync_local_input")
local swap = upvalue(sync_input, "switch_mapping_gate")
local read_mapping = upvalue(sync_input, "read_local_mapping")
local stock, reduced = {}, {}
local attached = {[stock] = true}
local input = {AppliedInputContexts = {Contains = function(_, context) return attached[context] end}}
local function action(address, name)
    return {GetAddress = function() return address end, IsValid = function() return true end,
        GetFName = function() return {ToString = function() return name end} end}
end
local forward, fire, look, inventory = action(1, "IA_MoveForward"), action(2, "IA_Use_Primary"),
    action(3, "IA_Look"), action(4, "IA_Inventory")

local function row(binding, key, ignored)
    return {Action = binding, Key = {KeyName = {ToString = function() return key end}}, bShouldBeIgnored = ignored}
end

local calls = 0
local function rebuild()
    calls = calls + 1
    input.EnhancedActionMappings = {row(forward, "W", true), row(forward, "Up", true), row(look, "MouseX", false)}
    if attached[stock] then
        input.EnhancedActionMappings[#input.EnhancedActionMappings + 1] = row(fire, "LeftMouseButton", true)
        input.EnhancedActionMappings[#input.EnhancedActionMappings + 1] = row(inventory, "Tab", true)
        input.EnhancedActionMappings[#input.EnhancedActionMappings + 1] = row(forward, "Gamepad_Up", true)
    end
end

local function check_options(options)
    assert(options.bIgnoreAllPressedKeysUntilRelease and options.bForceImmediately)
end

local gate = {
    player_input = input,
    priority = 3,
    allowed = {[1] = forward, [3] = look},
    subsystem = {
        AddMappingContext = function(_, context, priority, options)
            assert(priority == 3)
            check_options(options)
            attached[context] = true
            rebuild()
        end,
        RemoveMappingContext = function(_, context, options)
            check_options(options)
            attached[context] = nil
            rebuild()
        end,
    },
}
input.EnhancedActionMappings = {row(forward, "W", false), row(forward, "Up", true), row(look, "MouseX", false)}
swap(gate, stock, reduced)
assert(attached[reduced] and not attached[stock])
local down = input.EnhancedActionMappings
assert(not down[1].bShouldBeIgnored and down[2].bShouldBeIgnored and not down[3].bShouldBeIgnored)
swap(gate, stock, reduced)
assert(calls == 2, "a steady downed pawn must not rebuild its mappings")
swap(gate, reduced, stock)
assert(attached[stock] and not attached[reduced])
local up = input.EnhancedActionMappings
assert(not up[1].bShouldBeIgnored and up[2].bShouldBeIgnored and not up[3].bShouldBeIgnored)
assert(up[4].bShouldBeIgnored and up[5].bShouldBeIgnored and up[6].bShouldBeIgnored)

stock.IsValid, input.IsValid, input.GetAddress = function() return true end, function() return true end, function() return 10 end
local controller, pawn = action(11), action(12)
controller.PlayerInput = input
function StaticFindObject() return stock end
local clock, native_calls = 10, 0
local original_clock = os.clock
os.clock = function() return clock end
local mask, supported = 255, true
function SilverfishReadNativeStandingActionMappings(pawn_address, controller_address)
    assert(pawn_address == 12 and controller_address == 11)
    native_calls = native_calls + 1
    if supported then return #input.EnhancedActionMappings, mask end
end
local observed, verified = read_mapping(controller, pawn, nil, false)
assert(verified and observed.stock and observed.flags.move and observed.flags.reload and not observed.active)
assert(native_calls == 1)
assert(read_mapping(controller, pawn, nil, false) == observed and native_calls == 1, "keep the standing refresh interval")
clock, mask = 12, 255 - 64
local remapped, still_verified = read_mapping(controller, pawn, nil, false)
assert(native_calls == 2 and not still_verified and not remapped.flags.reload, "detect same-size changes after refresh")
supported = false
local fallback, fallback_verified = read_mapping(controller, pawn, nil, false)
assert(native_calls == 3 and not fallback_verified and fallback.active[1] and fallback.flags.inventory)

gate.clone, gate.blocked = reduced, {[2] = fire, [4] = inventory}
attached[stock], attached[reduced] = nil, true
input.EnhancedActionMappings = {row(forward, "W", false), row(look, "MouseX", false)}
local downed, downed_verified = read_mapping(controller, pawn, gate, true)
assert(native_calls == 3 and downed_verified and downed.reduced and downed.active[1] and downed.active[3])
input.EnhancedActionMappings[1] = row(action(99, "IA_MoveForward"), "W", false)
local different_membership, exact = read_mapping(controller, pawn, gate, true)
assert(not exact and different_membership.flags.move and not different_membership.active[1],
    "action names cannot replace exact downed membership")
read_mapping(controller, pawn, gate, false)
assert(native_calls == 3, "restoring mappings must use the exact Lua path")
os.clock = original_clock
