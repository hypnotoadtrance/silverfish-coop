package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" or name == "SILVERFISH_COOP_NATIVE_GUEST_SAVE"
        or name == "SILVERFISH_COOP_NATIVE_EXPANDED_ZONES" then return "1" end
    return getenv(name)
end

local profiles = require("native_tape_profiles")
local spec = profiles.drill
local blank_path = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"
local station_path = "/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local inventory_field = "TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F"
local give_field = "Items_Give_20_B4929C76420B737D08B6E7A56384E8EC"
local receive_field = "Items_Recive_22_BF8DA3D74F10EFDF07693DB9957DFFCD"
local assets = {}

local function object(name, class)
    local result = {}
    function result:IsValid() return true end
    function result:GetFullName() return name end
    function result:GetClass() return class end
    function result:GetAddress() return self end
    function result:HasAuthority() return true end
    function result:IsActorBeingDestroyed() return false end
    return result
end

function StaticFindObject(path)
    if not assets[path] then assets[path] = object(path) end
    return assets[path]
end

local function array(items)
    function items:GetArrayNum() return #self end
    return items
end

local function item_map(class, amount)
    local map = {class}
    function map:Contains(key) return key == class end
    function map:Find(key)
        assert(key == class)
        return {get = function() return amount or 1 end}
    end
    return map
end

local blank, product = StaticFindObject(blank_path), StaticFindObject(spec.product)
local other = StaticFindObject(profiles.stone.product)
local e206 = StaticFindObject("/Game/Classes/Items/DataTapes/DataTape_E206_01.DataTape_E206_01_C")
local function offer(output, amount)
    return {[give_field] = item_map(blank), [receive_field] = item_map(output, amount),
        RemoveAfterTrade_1_B26933FA4BBC62F03E1EE481B5414BA2 = false,
        ChanceShown_4_18C5CA7A480470CDF14CAD8FBF228FF7 = 1,
        Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F = 0,
        Cash_Recive_12_FE560CBF4176840FDE3480B7FFB4C4F2 = 0,
        Hide_IfCantTrade_24_4698C1544884DAA7621280A142208174 = false}
end

local function guid(value) return string.format("%08X", value) .. string.rep("0", 24) end
local function item(class, value) return {[class_field] = class, [guid_field] = {A = value, B = 0, C = 0, D = 0}} end
local world = object("World " .. spec.world)
world.NetDriver = object("stock NetDriver")
world.NetDriver.World = world
local station = object(spec.station, StaticFindObject(station_path))
local box = object("stock Box", StaticFindObject("/Script/Engine.BoxComponent"))
station.Box = box
function station:GetWorld() return world end
function station:K2_GetActorLocation() return spec.anchor end
function box:GetOwner() return station end
function box:K2_GetComponentLocation() return spec.anchor end
assets[spec.path] = station

local function pawn(name, blank_id)
    local actor = object(name)
    actor.Dead, actor.InteractionRange = false, 250
    actor.Save = object("Stock Save", StaticFindObject(save_path))
    actor.Save.PlayerBackpack, actor.Save.PlayerEquipment = array({item(blank, blank_id)}), array({})
    function actor:GetWorld() return world end
    function actor:K2_GetActorLocation() return spec.anchor end
    return actor
end

local host, guest = pawn("Player_FP_C Host", 4), pawn("Player_FP_C Guest", 1)
local instance = object("Canonical GameInstance")
instance["As Player FP"] = host
local canonical = {host = host, save = host.Save, instance = instance}
local progress = {prototype = -10, step = 2, Mosby_DrillTest = 2, cash = 715, xp = 11}
package.loaded.native_dialogue_mosby = {map = spec.map, world = spec.world, progress = function()
    local result = {}
    for key, value in pairs(progress) do result[key] = value end
    return result, canonical
end}
package.loaded.guest_save = {exact_item_context = function(_, _, name)
    if name == guest:GetFullName() then return guest, guest.Save end
end}
package.loaded.players = {is_downed = function() return false end, is_standing_guest = function() return true end}
package.loaded.native_wire_quest = {}
package.loaded.zone_maps = {[spec.map] = {expanded = true}}

local config_count, conversion_count = 0, 0
local stock = array({offer(other), offer(product), offer(e206)})
station.Tapes_DataBase = array({other, e206, product})
function station:Configure_Stock()
    config_count = config_count + 1
    self.ShopInv = {[inventory_field] = stock}
end

function SilverfishExchangeDrillTestResultsTape(actor, target, original, old, assigned)
    conversion_count = conversion_count + 1
    assert((actor == host or actor == guest) and target == actor.Save and original == host.Save)
    assert(old == guid(actor == host and 4 or 1))
    assert(target.PlayerBackpack[1][class_field] == blank)
    target.PlayerBackpack[1] = item(product, assert(tonumber(assigned:sub(1, 8), 16)))
    return true, "exchanged", assigned
end

local tape = require("native_mosby_tape")
local state = {map = spec.map, world = world, listening = 7777}
local function command(action, actor, blank_guid, filled)
    return tape.command({"TEST", spec.prefix .. "-" .. action, actor:GetFullName(), blank_guid, filled}, state, "hosting")
end
local function row()
    return tape.rows(state, "hosting", table.pack)[1]
end
local function refuses(reason, actor)
    local ok, err = pcall(command, "preflight", actor, guid(1))
    assert(not ok and err == "native_mosby_tape_" .. reason, tostring(err))
    assert(row() == nil)
end

progress.Mosby_DrillTest = 0
refuses("stock_drill_test_not_completed", host)
assert(config_count == 0)
progress.Mosby_DrillTest, progress.prototype = 1, 2
refuses("stock_drill_test_not_completed", guest)
progress.prototype = -10
assert(command("preflight", guest, guid(1)))
assert(row().n == 16 and row()[5] == spec.station and row()[7] == guid(1) and row()[9] == -10 and row()[16] == 1)
assert(command("convert", guest, guid(1), guid(2)))
assert(row()[8] == guid(2) and row()[10] == 715 and row()[11] == 11)
assert(guest.Save.PlayerBackpack[1][class_field] == product and host.Save.PlayerBackpack[1][class_field] == blank)

stock = array({offer(product), offer(product), offer(e206)})
refuses("station_offer_ambiguous", host)
stock = array({offer(other), offer(product, 2), offer(e206)})
refuses("station_offer_item_changed", host)
stock = array({offer(other), offer(product), offer(e206)})
stock[2].Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F = 1
refuses("station_offer_changed:Cash_Give_10_7D278BDA423E1A8C582C23B5C1A91B6F", host)
stock[2] = offer(product)
station.Tapes_DataBase = array({product, product, e206})
refuses("station_database_changed", host)
station.Tapes_DataBase = array({other, e206, product})
progress.Mosby_DrillTest = 3
assert(command("convert", host, guid(4), guid(3)))
assert(row()[4] == host:GetFullName() and row()[8] == guid(3) and row()[16] == 3)
assert(host.Save.PlayerBackpack[1][class_field] == product and guest.Save.PlayerBackpack[1][guid_field].A == 2)
assert(conversion_count == 2)
print("Scientist Drill Test Results exact multi-offer, canonical host and detached guest mocks passed")
