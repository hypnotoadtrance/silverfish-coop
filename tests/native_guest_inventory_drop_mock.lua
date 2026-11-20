package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" or name == "SILVERFISH_COOP_NATIVE_INVENTORY_VIEW"
        or name == "SILVERFISH_COOP_HEADLESS" then return "1" end
end
io.open = function() return { write = function() return true end, close = function() return true end } end
LoopInGameThreadWithDelay = function() end
FText = function(message) return { ToString = function() return message end } end
package.loaded.native_guest_armor = { is_body_path = function() return false end, is_helmet_path = function() return false end }

local objects, next_address = {}, 0
local function object(path, type_path)
    next_address = next_address + 1
    local address = next_address
    local value = { IsValid = function() return true end, GetAddress = function() return address end,
        GetClass = function() return objects[type_path] end }
    if path then objects[path] = value end
    return value
end
function StaticFindObject(path) return objects[path] end
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local display_class = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local text_class = "/Script/UMG.TextBlock"
local leaf = arg[1] == "crossroads" and "CrossRoads_P" or "Observitory_P"
local map = "/Game/Classes/Maps/Maps_OverWorld/" .. leaf
local pawn_name = "Player_FP_C " .. map .. "." .. leaf .. ":PersistentLevel.Player_FP_C_7"
for _, path in ipairs({pawn_class, controller_class, display_class, text_class,
    "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"}) do object(path) end
local world, pawn, controller = object(), object(nil, pawn_class), object(nil, controller_class)
pawn.GetWorld, controller.GetWorld = function() return world end, function() return world end
pawn.GetFullName = function() return pawn_name end
pawn.HasAuthority = function() return false end
pawn.IsLocallyControlled, controller.IsLocalController = function() return true end, function() return true end
pawn.Controller, controller.Pawn, controller.Loading = controller, pawn, false
local fixture = {map = map, world = world, listening = true, server = true, controller = controller,
    local_pawn = {pawn = pawn, controller_object = controller, name = pawn_name}}

local note, widget = object(nil, text_class), object(nil, display_class)
local text, opened
note.SetText = function(_, value) text = value end
note.GetText = function() return text end
widget.TextBlock_Notes = note
widget.GetOwningPlayer = function() return controller end
widget.SetVisibility = function() end
widget.SetDesiredSizeInViewport = function() end
widget.SetPositionInViewport = function() end
widget.AddToPlayerScreen, widget.IsInViewport = function() return true end, function() return true end
widget.RemoveFromParent = function() end
local factory = object("/Script/UMG.Default__WidgetBlueprintLibrary")
factory.Create = function() return widget end
SilverfishSetNativeInventoryViewScope = function() return true end
SilverfishTakeNativeInventoryViewRequest = function()
    if opened then return false end
    opened = true
    return true, 5, true
end
SilverfishNativeInventoryViewInput = function(address, serial) return address == pawn:GetAddress() and serial == 5 end

local view = require("native_inventory_view")
view.start("scratch", function() return false end)
view.sync(fixture, "connected")
local guid, item = ("A"):rep(32), "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local row = table.concat({guid, "backpack", "0", "9", item, "split,drop"}, "|")
view.command({"id", "native-inventory-view-render", "5", map, "0", "1", row}, fixture, "connected")
assert(text:ToString():find("Delete prepares") and text:ToString():find("Home splits"))
local function key(action)
    return view.command({"id", "native-inventory-view-key-fixture", "5", map, action}, fixture, "connected")
end
local function stage(action)
    for _, args in ipairs(view.rows(fixture, "connected", table.pack)) do
        if args[1] == ("native-guest-inventory-" .. action .. "-view") then return args[7] end
    end
end
key("drop")
assert(stage("drop") == "confirming" and text:ToString():find("Drop all 9 rounds"))
key("back")
assert(stage("drop") == nil)
key("confirm")
assert(stage("split") == "confirming")
key("drop")
assert(stage("split") == nil and stage("drop") == "confirming")
key("confirm")
assert(stage("drop") == "dropping")
assert(not pcall(view.command, {"id", "native-inventory-view-drop-result", "5", map, ("B"):rep(32), "dropped"},
    fixture, "connected"))
view.command({"id", "native-inventory-view-drop-result", "5", map, guid, "dropped"}, fixture, "connected")
assert(stage("drop") == "dropped" and text:ToString():find("Ammo dropped"))
