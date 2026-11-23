package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local token, session, epoch = string.rep("a", 32), string.rep("b", 32), string.rep("c", 32)
local map, name = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P", "Player_FP_C ExactGuest"
local function object(properties)
    properties.IsValid = function() return true end
    properties.GetAddress = function(self) return self end
    return properties
end

local guest_save = {PlayerHealth = 155, PlayerFood = 54.6, PlayerRegen = 250}
local solo_save = {PlayerHealth = 250, PlayerFood = 175, PlayerRegen = 250}
local pawn = object({MaxHealth = 250, Dead = false, GetFullName = function() return name end})
local class = object({})
local refreshes = 0
local info = object({UpdateStats = function() refreshes = refreshes + 1 end})
local widget = object({PlayerInfoBox = info, GetClass = function() return class end})
local state = {map = map, local_pawn = {pawn = pawn}, controller = {MenuStack = {
    widget, GetArrayNum = function() return 1 end,
}}}
local guest_view = object(guest_save)

package.loaded.guest_save = {}
package.loaded.native_guest_creator = {}
package.loaded.native_avatar_character_view = {}
package.loaded.native_world = {}
package.loaded.native_save_scope = {
    selected = function() return token end,
    bind_guest = function(actual, phase)
        assert(actual == state and phase == "connected")
        return guest_view, pawn, token
    end,
}
StaticFindObject = function(path)
    assert(path == "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")
    return class
end
SilverfishReadNativeClientDamageScope = function(address, active_session, active_epoch)
    return address == pawn and active_session == session and active_epoch == epoch
end
io.open = function() error("Live stock vitals tried to open a file") end
local ui = require("native_guest_ui")
local function project(hp, food, regen, current_epoch, phase)
    return ui.command({"request", "native-guest-health-project", map, session, current_epoch or epoch, token, name,
        tostring(hp), "250", food, regen}, state, phase or "connected")
end

assert(project(125, "84.6", "210.25") == "stock co-op guest health projected")
assert(guest_view.PlayerHealth == 125 and guest_view.PlayerFood == 84.6 and guest_view.PlayerRegen == 210.25)
assert(refreshes == 1)
assert(project(120, "-", "200.75") == "stock co-op guest health projected")
assert(guest_view.PlayerHealth == 120 and guest_view.PlayerFood == 84.6 and guest_view.PlayerRegen == 200.75)
assert(refreshes == 1)
assert(project(0, "0", "200.75") == "stock co-op guest health projected")
assert(guest_view.PlayerHealth == 0 and guest_view.PlayerFood == 0 and guest_view.PlayerRegen == 200.75)
assert(refreshes == 2)
assert(project(25) == "stock co-op guest health projected")
assert(guest_view.PlayerHealth == 25 and guest_view.PlayerFood == 0 and guest_view.PlayerRegen == 200.75)
assert(not pcall(project, 75, "201", "190"))
assert(not pcall(project, 75, "120", "251"))
assert(not pcall(project, 75, "120", "190", string.rep("d", 32)))
assert(not pcall(project, 75, "120", "190", epoch, "hosting"))
assert(guest_view.PlayerHealth == 25 and guest_view.PlayerFood == 0 and guest_view.PlayerRegen == 200.75)
assert(solo_save.PlayerHealth == 250 and solo_save.PlayerFood == 175 and solo_save.PlayerRegen == 250)
assert(refreshes == 2)
print("guest stock vitals update only the qualified isolated in-memory view")
