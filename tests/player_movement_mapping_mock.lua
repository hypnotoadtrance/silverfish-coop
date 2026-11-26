package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function upvalue(fn, name)
    for index = 1, math.huge do
        local field, value = debug.getupvalue(fn, index)
        if field == name then return value end
        if not field then error("Missing " .. name) end
    end
end

local players = require("players")
local swap = upvalue(upvalue(players.sync, "sync_local_input"), "switch_mapping_gate")
local stock, reduced = {}, {}
local attached = {[stock] = true}
local input = {AppliedInputContexts = {Contains = function(_, context) return attached[context] end}}
local function action(address)
    return {GetAddress = function() return address end, IsValid = function() return true end}
end
local forward, fire, look, inventory = action(1), action(2), action(3), action(4)

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
