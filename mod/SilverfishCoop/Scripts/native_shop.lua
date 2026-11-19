local shop = {}
local guest_save = require("guest_save")
local pickup = require("native_pickup")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map_path = "/Game/Classes/Maps/PlaytestMap_01"
local bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local stock_index = 0
local range = 250
local shops = { Shop_BP_C_0 = true, Shop_BP_C_1 = false }
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_weight = "Weight_12_11AB9ED547504E78A849A692A36DC938"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local item_never_drop = "NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local reservations = {}
local last

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_guid(value)
    if type(value) ~= "string" or #value ~= 32 or value:find("[^0-9A-F]") or not value:find("[^0]") then
        error("Expected an uppercase nonzero native shop purchase GUID", 0)
    end
    return value
end

local function guid_string(guid)
    if not guid then return nil end
    local a, b, c, d = math.tointeger(guid.A), math.tointeger(guid.B), math.tointeger(guid.C), math.tointeger(guid.D)
    if not a or not b or not c or not d or (a == 0 and b == 0 and c == 0 and d == 0) then return nil end
    return string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
end

local function finite(value, maximum)
    return type(value) == "number" and value == value and value >= 0 and value <= maximum
end

local function merchant(world, id)
    if shops[id] == nil then error("Unknown staged native merchant", 0) end
    local name = "Shop_BP_C " .. map_path .. ".PlaytestMap_01:PersistentLevel." .. id
    local match
    for _, candidate in ipairs(FindAllOf("Shop_BP_C") or {}) do
        if valid(candidate) and candidate:GetFullName() == name and same(candidate:GetWorld(), world)
            and candidate:HasAuthority() and not candidate:IsActorBeingDestroyed() then
            if match then error("The stock merchant identity is ambiguous", 0) end
            match = candidate
        end
    end
    if not match or match.IsCheatShop ~= shops[id] then error("The qualified stock merchant is unavailable", 0) end
    return match
end

local function near(pawn, actor)
    local here, there = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()
    local x, y, z = here.X - there.X, here.Y - there.Y, here.Z - there.Z
    return x * x + y * y + z * z <= range * range
end

local function bandage(item)
    local class = item and item[item_class]
    return valid(class) and class:GetFullName():match("^%S+ (%S+)$") == bandage_path
end

local function stock_offer(actor)
    local stock = actor.StockInventory
    local count = stock and math.tointeger(stock:GetArrayNum())
    if not count or count < 1 or count > 1024 then error("Merchant infinite stock is unavailable", 0) end
    local match
    for index = 1, count do
        local item = stock[index]
        if bandage(item) then
            if match then error("The Bandage offer is ambiguous", 0) end
            match = item
        end
    end
    local source = match and match[item_guid]
    local identity = guid_string(source)
    if not identity then error("The merchant has no usable infinite Bandage offer", 0) end
    return { A = source.A, B = source.B, C = source.C, D = source.D }, identity, count
end

local function make_unit(actor, source, identity, count)
    local unit, remainder, entire, success = {}, {}, {}, {}
    -- Stock inventory zero calls MakeItemStack with the class defaults and a
    -- fresh GUID. This stock path keeps its source unlimited.
    actor:SplitStack(source, 1, stock_index, unit, remainder, entire, success)
    if success.Success ~= true or entire.TookEntireStack ~= false or not bandage(unit)
        or not bandage(remainder) or guid_string(remainder[item_guid]) ~= identity
        or actor.StockInventory:GetArrayNum() ~= count then
        error("Could not verify the merchant's native infinite-stock unit", 0)
    end
    local ammo, weight, value, never_drop = unit[item_ammo], unit[item_weight], unit[item_value], unit[item_never_drop]
    if ammo ~= 1 or not finite(weight, 1000000) or not finite(value, 1000000000)
        or not math.tointeger(value) or type(never_drop) ~= "boolean" then
        error("The live Bandage payload is incompatible with independent native inventory", 0)
    end
    return { ammo = ammo, weight = weight, value = value, never_drop = never_drop }
end

local function quoted_price(actor, pawn, stock, value)
    if actor.IsCheatShop then return 0 end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local owner = valid(statics) and statics:GetGameInstance(pawn) or nil
    if not valid(owner) or not same(owner.SaveSlot_Ref, stock) or not finite(actor.BaseMarkup, 1000) then
        error("The authoritative stock merchant pricing state is unavailable", 0)
    end
    local active, instances = {}, {}
    owner:FindPassiveEffect(FName("Haggler"), true, active, instances)
    if type(active.EffectActive) ~= "boolean" or not finite(instances.Instances, 64)
        or not math.tointeger(instances.Instances) then
        error("The stock merchant's Haggler markup could not be read", 0)
    end
    -- ShopPanel.CalcItemMarkup applies a category factor of one to Bandage.
    local price = math.ceil(value * (actor.BaseMarkup * (0.7 * instances.Instances)))
    if not finite(price, 1000000000) then error("The native purchase price is out of range", 0) end
    return price
end

local function prepare(state, pawn, stock, id, guid)
    local name = pawn:GetFullName()
    local reservation = reservations[name]
    if reservation and reservation.state == "prepared" then
        if reservation.guid == guid and reservation.shop == id then
            last = reservation
            return "native purchase already prepared"
        end
        error("This guest already has a prepared native purchase", 0)
    end
    if reservation and reservation.guid == guid then error("This native purchase already has an outcome", 0) end
    local actor = merchant(state.world, id)
    if pawn.Dead ~= false or not (pawn.Save.PlayerHealth > 0) or not near(pawn, actor) then
        error("The standing guest must be within 2.5 meters of the merchant", 0)
    end
    local source, offer, count = stock_offer(actor)
    local item = make_unit(actor, source, offer, count)
    local prepared = { state = "prepared", world = state.world, save = pawn.Save, pawn = name, owner = pawn,
        shop = id, guid = guid, offer = offer, item = item, price = quoted_price(actor, pawn, stock, item.value) }
    reservations[name], last = prepared, prepared
    return "native Bandage purchase prepared"
end

local function settle(action, pawn, stock, guid)
    local reservation = reservations[pawn:GetFullName()]
    if not reservation or reservation.guid ~= guid then
        error("The exact native purchase reservation is unavailable", 0)
    end
    if action == "native-shop-abort" then
        if reservation.state == "committed" then error("The native purchase is already committed", 0) end
        reservation.state = "aborted"
        last = reservation
        return "native purchase reservation released"
    end
    if reservation.state == "committed" then
        last = reservation
        return "native purchase already committed"
    end
    if reservation.state ~= "prepared" then error("The native purchase is not reserved", 0) end
    local item = reservation.item
    local ok, result = SilverfishAppendNativePickup(stock:GetAddress(), pawn:GetAddress(), pawn.Save:GetAddress(),
        guid, item.ammo, item.weight, item.value, item.never_drop)
    if not ok then error("Native guest inventory rejected the purchase (" .. tostring(result) .. ")", 0) end
    reservation.state = "committed"
    last = reservation
    return "native Bandage purchase placed in the guest inventory"
end

local function remove_stale(state, phase)
    for name, reservation in pairs(reservations) do
        local owner = reservation.owner
        if phase ~= "hosting" or not same(reservation.world, state.world) or not guest_save.matches(owner)
            or not same(reservation.save, owner.Save) then
            reservations[name] = nil
            if last == reservation then last = nil end
        end
    end
end

function shop.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-shop-prepare" and action ~= "native-shop-commit" and action ~= "native-shop-abort" then return end
    if not enabled or type(SilverfishAppendNativePickup) ~= "function" then
        error("The staged native merchant is unavailable", 0)
    end
    if state.map ~= map_path then error("Native purchases require the active playtest merchant map", 0) end
    local pawn, stock = pickup.enrolled(state, phase, fields[3])
    remove_stale(state, phase)
    local name = pawn:GetFullName()
    local reservation = reservations[name]
    if reservation and not same(reservation.owner, pawn) then
        error("The native purchase reservation belongs to a different pawn", 0)
    end
    if action == "native-shop-prepare" and #fields == 5 then
        return prepare(state, pawn, stock, fields[4], exact_guid(fields[5]))
    end
    if action ~= "native-shop-prepare" and #fields == 4 then
        return settle(action, pawn, stock, exact_guid(fields[4]))
    end
    error("Invalid native merchant arguments", 0)
end

function shop.rows(state, phase, status_row)
    if not enabled then return {} end
    remove_stale(state, phase)
    local reservation = last
    if not reservation then return {} end
    local item = reservation.item
    return { status_row("native-shop-purchase", reservation.state, reservation.guid, reservation.shop,
        reservation.pawn, reservation.offer, reservation.price, bandage_path, item.ammo,
        string.format("%.17g", item.weight), item.value, item.never_drop and 1 or 0) }
end

return shop
