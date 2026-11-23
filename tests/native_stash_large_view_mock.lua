-- Optional mock of the stock stash renderer with 511 distinct rows (>32 KiB).
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local objects, next_address = {}, 0
local function object(fields)
    fields = fields or {}
    next_address = next_address + 1
    local address = next_address
    objects[address] = fields
    fields.IsValid = function() return true end
    fields.GetAddress = function() return address end
    return fields
end

local function array()
    return {GetArrayNum = function(self) return #self end}
end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local path = map .. ".Observitory_P:PersistentLevel.Stash_Crate_BP_C_0"
local item_class_path = "/Game/Classes/Items/Test.Test_C"
local site = "00000001000000000000000000000000"
local origin = {X = 0, Y = 0, Z = 0}
local world, stash_base, item_base = object(), object(), object()
local controller = object({Loading = false, MenuStack = array(), IsLocalController = function() return true end})
local pawn = object({Dead = false, InteractionRange = 100,
    GetWorld = function() return world end, K2_GetActorLocation = function() return origin end,
    IsLocallyControlled = function() return true end, HasAuthority = function() return false end})
controller.Pawn = pawn
function controller:AddMenu(screen) self.MenuStack[1] = screen end
function controller:CloseMenu() self.MenuStack[1] = nil end

local stash = object({Stash_ID = {A = 1, B = 0, C = 0, D = 0},
    K2_GetActorLocation = function() return origin end, GetWorld = function() return world end,
    IsA = function(_, base) return base == stash_base end, IsActorBeingDestroyed = function() return false end,
    GetFullName = function() return "Stash_Crate_BP_C " .. path end})
local cdo = object({IsA = function(_, base) return base == item_base end})
local item_class = object({GetCDO = function() return cdo end})
local save = object()
package.loaded.native_save_scope = {bind_guest = function() return save end}

local stock_cards = array()
local screen = object({StashPanel = {Cards = stock_cards}, BackpackPanel = {FullRefresh = function() end}})
local stock_refreshes = 0
function screen.StashPanel:FullRefresh()
    stock_refreshes = stock_refreshes + 1
    for index, item in ipairs(screen.preview.PlayerBackpack) do stock_cards[index] = object({Item = item}) end
end

local exports = {
    [path] = stash,
    [item_class_path] = item_class,
    ["/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C"] = stash_base,
    ["/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"] = item_base,
    ["/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"] = object(),
    ["/Game/Classes/GUI/New/StashScreen.StashScreen_C"] = object(),
    ["/Script/Engine.Default__GameplayStatics"] = object({CreateSaveGameObject = function()
        return object({PlayerBackpack = array()})
    end}),
    ["/Script/UMG.Default__WidgetBlueprintLibrary"] = object({Create = function() return screen end}),
}
function StaticFindObject(name) return exports[name] end
function LoadAsset() error("unexpected asset load") end
function SilverfishSetNativeStashScreen() return true end
function SilverfishClearNativeStashScreen() end
function SilverfishTakeNativeStashMove() end
function SilverfishReplaceNativeStashPreview(address, preview)
    assert(objects[address] == screen)
    screen.preview = assert(objects[preview])
    return true
end

local pressed
function SilverfishTakeNativeStashPress()
    if pressed then
        pressed = false
        return true, path
    end
end

local state = {listening = true, server = true, world = world, map = map, controller = controller,
    local_pawn = {name = "Guest", pawn = pawn}}
local page, count = {}, 511
for index = 1, count do
    page[index] = string.format("%032X\t%s\t%d\t0.25\t15\t0\n", index, item_class_path, index)
end
local body = table.concat(page)
assert(#body > 32768)

local stash_view = require("native_stash_view")
stash_view.start("unavailable-test-mailbox", function() return false end)
local original_open = io.open
io.open = function(...) error("unexpected stash disk access") end
local row = function(...) return {...} end
local function open()
    pressed = true
    stash_view.sync(state, "connected")
    local pending = stash_view.rows(state, "connected", row)[1]
    assert(pending[2] == "pending")
    return {"ticket", stash_view.memory_render, tostring(pending[3]), map, site, tostring(count)}
end

local small = open()
assert(stash_view.memory_body_limit() == 20000)
local ok, issue = pcall(stash_view.command, small, state, "connected", body)
assert(not ok and issue == "native_stash_view_invalid_page" and stock_refreshes == 0)

function SilverfishLocalBridgeStashBodyLimit() return 2500000 end
assert(stash_view.memory_body_limit() == 2500000)
local fields = open()
local receipt = stash_view.command(fields, state, "connected", body)
assert(receipt == "private stash view updated in memory" and stock_refreshes == 1 and #stock_cards == count)
for index, card in ipairs(stock_cards) do
    assert(card.Item["GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"].D == index)
    assert(card.Item["Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"] == index)
end

local function expired(fields)
    local accepted, failure = pcall(stash_view.command, fields, state, "connected", body)
    assert(not accepted and failure == "native_stash_view_request_expired" and stock_refreshes == 1)
end
expired(fields)
local close = {"ticket", "native-stash-view-close"}
stash_view.command(close, state, "connected")
local recreated = open()
assert(recreated[3] ~= fields[3])
expired(fields)
stash_view.sync(state, "disconnected")
expired(recreated)
assert(stash_view.rows(state, "connected", row)[1] == nil)
io.open = original_open
print(("Verified scoped in-memory stash rendering and stale receipt refusal (%d bytes, %d rows)"):format(#body, count))
