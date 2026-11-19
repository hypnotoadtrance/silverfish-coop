local shop = {}
local pickup = require("native_pickup")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local merchants = {
    ["/Game/Classes/Maps/Maps_OverWorld/Observitory_P"] = {
        level = "Observitory_P", actor = "Shop_BP_C_2", data = "Observitory_ShopData",
    },
    ["/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"] = {
        level = "Shop_ScavBar_P", actor = "Shop_BP_C_2", data = "ScavBar_ShopData",
    },
    ["/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"] = {
        level = "Shop_BlackMarket_01", actor = "Shop_BP_C_1", data = "BlacMarket_ShopData",
    },
    ["/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"] = {
        level = "Shop_Scientist_01", actor = "Shop_BP_C_2", data = "Science_ShopData", barter = "Barter_BP_C_107",
    },
}
local merchant_data_root = "/Game/Classes/Libraries/DataAssets/Merchants/"
local shop_class = "/Game/Classes/Actors/Shop_BP.Shop_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local panel_path = "/Game/Classes/GUI/New/Components/ShopPanel.ShopPanel_C"
local widget_library = "/Script/UMG.Default__WidgetBlueprintLibrary"
local max_money, max_offers = 2147483647, 128
local actions = {
    ["native-finite-shop-list"] = {native = "SilverfishReadNativeFiniteShop", fields = 3, result = "listed"},
    ["native-finite-shop-buy"] = {native = "SilverfishBuyNativeFiniteShop", fields = 7, result = "purchased"},
    ["native-finite-shop-sale-quote"] = {native = "SilverfishQuoteNativeFiniteShopSale", fields = 4, result = "sale-quote"},
    ["native-finite-shop-sell"] = {native = "SilverfishSellNativeFiniteShop", fields = 7, result = "sold"},
}
local last

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < 1e8
end

local function vector(value)
    return value and finite(value.X) and finite(value.Y) and finite(value.Z)
end

local function distance_squared(first, second)
    return (first.X - second.X) ^ 2 + (first.Y - second.Y) ^ 2 + (first.Z - second.Z) ^ 2
end

local function refused(reason)
    error("finite_shop_refused_" .. reason, 0)
end

local function integer(value, minimum, maximum)
    if type(value) == "string" and #value <= 10 and value:match("^%d+$") then
        value = tonumber(value)
    end
    return type(value) == "number" and math.tointeger(value) and value >= minimum and value <= maximum and value or nil
end

local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]") and value or nil
end

local function canonical_host(state, detail, pawn_name)
    local pawn, controller = detail.pawn, state.controller
    if not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or not same(detail.controller_object, controller) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), state.world)
        or not same(controller:GetWorld(), state.world) or not pawn:HasAuthority() or not controller:HasAuthority()
        or not pawn:IsLocallyControlled() or not controller:IsLocalController() or controller.Loading ~= false
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed() or pawn.Dead ~= false
        or players.is_downed(pawn) ~= false or pawn:GetFullName() ~= pawn_name then
        refused("exact_standing_host_unavailable")
    end
    local driver, instance = state.world.NetDriver, state.world.OwningGameInstance
    if not valid(driver) or not same(driver.World, state.world) or valid(driver.ServerConnection)
        or not exact(instance, instance_class) or not same(instance["As Player FP"], pawn)
        or not same(pawn["As Game Instance FP"], instance)
        or not exact(instance.SaveSlot_Ref, save_class) or not same(pawn.Save, instance.SaveSlot_Ref)
        or not finite(pawn.Save.PlayerHealth) or pawn.Save.PlayerHealth <= 0 then
        refused("exact_host_canonical_save_unavailable")
    end
    return pawn, instance.SaveSlot_Ref
end

local function merchant(state, phase, pawn_name, allow_host)
    local profile = merchants[state.map]
    if not enabled or not profile or phase ~= "hosting" or not state.listening or state.server ~= false
        or not valid(state.world) then refused("exact_listen_world_unavailable") end
    local world = state.map .. "." .. profile.level
    if state.world:GetFullName() ~= "World " .. world then refused("exact_listen_world_unavailable") end
    local target_name = "Shop_BP_C " .. world .. ":PersistentLevel." .. profile.actor
    local merchant_data = merchant_data_root .. profile.data .. "." .. profile.data
    local pawn, stock
    local detail = state.local_pawn
    if detail and detail.name == pawn_name then
        if not allow_host or not profile.barter then refused("scientist_host_camera_only") end
        pawn, stock = canonical_host(state, detail, pawn_name)
    else
        local accepted
        accepted, pawn, stock = pcall(pickup.enrolled, state, phase, pawn_name)
        if not accepted or not valid(pawn) or not valid(stock) or same(pawn.Save, stock) or pawn.Dead ~= false
            or not finite(pawn.Save.PlayerHealth) or pawn.Save.PlayerHealth <= 0 then
            refused("exact_standing_guest_unavailable")
        end
    end
    local actor
    for _, candidate in ipairs(FindAllOf("Shop_BP_C") or {}) do
        if exact(candidate, shop_class) and candidate:GetFullName() == target_name
            and same(candidate:GetWorld(), state.world) and candidate:HasAuthority() and not candidate:IsActorBeingDestroyed() then
            if actor then refused("exact_merchant_ambiguous") end
            actor = candidate
        end
    end
    local instance = actor and actor["As Game Instance FP"]
    if not actor or actor.IsCheatShop ~= false or stock.BarterOnly ~= false
        or not same(actor.MerchantData, StaticFindObject(merchant_data)) or not valid(instance)
        or not same(instance, state.world.OwningGameInstance) or not same(instance.SaveSlot_Ref, stock)
        or not exact(actor.Box, "/Script/Engine.BoxComponent") or not same(actor.Box:GetOwner(), actor) then
        refused("exact_stock_merchant_unavailable")
    end
    if profile.barter then
        local replacement = StaticFindObject(world .. ":PersistentLevel." .. profile.barter)
        local collision = actor.Box:GetCollisionEnabled()
        if not exact(replacement, "/Game/Classes/Actors/Barter_BP.Barter_BP_C")
            or not same(replacement:GetWorld(), state.world) or not same(replacement, actor.BarterReplacement)
            or not same(replacement.MerchantData, actor.MerchantData) or (collision ~= 1 and collision ~= 3) then
            refused("scientist_cash_counter_unavailable")
        end
    end
    return {pawn = pawn, stock = stock, actor = actor, name = target_name, instance = instance}
end

local function quote_panel(context)
    local class = StaticFindObject(panel_path)
    if not valid(class) then
        LoadAsset(panel_path)
        class = StaticFindObject(panel_path)
    end
    local widgets = StaticFindObject(widget_library)
    local controller = context.pawn.Controller
    if not valid(class) or not valid(widgets) or not valid(controller) then refused("exact_stock_quote_panel_unavailable") end
    -- The server renders no stock UI for this guest. The panel is never
    -- configured with inventory widgets, added to a menu, or shown.
    local panel = widgets:Create(context.pawn, class, nil)
    if not exact(panel, panel_path) or panel:IsInViewport() then refused("exact_stock_quote_panel_unavailable") end
    panel.Target, panel.GameInstanceFP = context.actor, context.instance
    if not same(panel.Target, context.actor) or not same(panel.GameInstanceFP, context.instance) then
        refused("exact_stock_quote_panel_binding_failed")
    end
    return panel
end

local function offer_rows(encoded)
    if type(encoded) ~= "string" or #encoded > max_offers * 640 then return nil end
    local rows, seen = {}, {}
    for line in encoded:gmatch("[^\n]+") do
        local key, class, quantity, value, price = line:match("^([0-9A-F]+)\t(/Game/[%w_./%-]+)\t(%d+)\t(%d+)\t(%d+)$")
        if not guid(key) or seen[key] or #class > 512 or not integer(quantity, 1, max_money)
            or not integer(value, 0, max_money) or not integer(price, 0, max_money)
            or #rows >= max_offers then return nil end
        seen[key] = true
        rows[#rows + 1] = {key, class, quantity, value, price}
    end
    if #rows == 0 and encoded ~= "" then return nil end
    return rows
end

local offsets = { {0, -110}, {110, 0}, {0, 110}, {-110, 0}, {70, 70}, {-70, -70}, {140, 70}, {-140, -70} }
local trace_color = {R = 0, G = 0, B = 0, A = 0}

function shop.fixture_position(state, phase, pawn_name)
    if not fixtures then refused("fixture_unavailable") end
    local context = merchant(state, phase, pawn_name, true)
    local pawn, actor = context.pawn, context.actor
    local anchor = actor.Box:K2_GetComponentLocation()
    local range = pawn.InteractionRange
    local capsule = pawn.CapsuleComponent
    local half_height = valid(capsule) and capsule:GetScaledCapsuleHalfHeight()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not vector(anchor) or not finite(range) or range <= 0 or not finite(half_height) or half_height <= 0
        or not valid(trace) then refused("fixture_merchant_geometry_unavailable") end
    local rotation = pawn:K2_GetActorRotation()
    if not rotation or not finite(rotation.Pitch) or not finite(rotation.Yaw) or not finite(rotation.Roll) then
        refused("fixture_merchant_rotation_unavailable")
    end
    local aim = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
    local ignored = { actor, pawn }
    if not same(pawn, state.local_pawn.pawn) then ignored[#ignored + 1] = state.local_pawn.pawn end
    for _, offset in ipairs(offsets) do
        local x, y = anchor.X + offset[1], anchor.Y + offset[2]
        for _, height in ipairs({150, 250, 400}) do
            local hit = {}
            local start, finish = {X = x, Y = y, Z = anchor.Z + height}, {X = x, Y = y, Z = anchor.Z - 380}
            local found = trace:LineTraceSingle(pawn, start, finish, 0, false,
                ignored, 0, hit, true, trace_color, trace_color, 0)
            hit = hit.OutHit or hit
            if found and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= 0.7
                and hit.ImpactPoint.Z < start.Z - 1 and hit.ImpactPoint.Z >= finish.Z then
                local location = {X = x, Y = y, Z = hit.ImpactPoint.Z + half_height + 2}
                if distance_squared(location, anchor) < (range - 10) ^ 2 and pawn:K2_TeleportTo(location, aim) then
                    local actual = pawn:K2_GetActorLocation()
                    if vector(actual) and distance_squared(actual, location) < 625
                        and distance_squared(actual, anchor) < range ^ 2 then
                        pawn:ForceNetUpdate()
                        return "exact stock merchant approach verified"
                    end
                end
            end
        end
    end
    refused("fixture_safe_merchant_approach_unavailable")
end

function shop.command(fields, state, phase)
    local operation = fields[2]
    if operation == "native-finite-shop-approach" then
        if #fields ~= 3 then refused("fixture_exact_participant_required") end
        return shop.fixture_position(state, phase, fields[3])
    end
    local action = actions[operation]
    if not action then return end
    last = nil
    local mutates = action.fields == 7
    if #fields ~= action.fields then refused("exact_offer_arguments_required") end
    local native = _G[action.native]
    if type(native) ~= "function" then refused("native_merchant_unavailable") end
    local prepared, context, panel = pcall(function()
        local allow_host = operation == "native-finite-shop-list" or operation == "native-finite-shop-buy"
        local exact_merchant = merchant(state, phase, fields[3], allow_host)
        return exact_merchant, quote_panel(exact_merchant)
    end)
    if not prepared then
        local reason = tostring(context)
        if reason:match("^finite_shop_refused_") then error(reason, 0) end
        refused("stock_preflight_failed " .. reason:gsub("[%c]", " "):sub(1, 256))
    end
    local args = {context.pawn:GetAddress(), context.pawn.Save:GetAddress(), context.stock:GetAddress(),
        context.actor:GetAddress(), panel:GetAddress()}
    if action.fields > 3 then
        args[6] = guid(fields[4])
        if not args[6] then refused("exact_offer_guid_required") end
    end
    if mutates then
        args[7], args[8], args[9] = integer(fields[5], 1, max_money), integer(fields[6], 0, max_money),
            integer(fields[7], 0, max_money)
        if not args[7] or not args[8] or not args[9] then refused("exact_price_and_guid_required") end
    end
    local succeeded, status, data, before, after, cash_before, cash_after = native(table.unpack(args))
    if not succeeded then
        if type(status) ~= "string" or status:match("^mutation_unverified_") then
            error(tostring(status), 0)
        end
        refused(status)
    end
    local rows = offer_rows(data)
    if status ~= action.result or not rows or (action.fields > 3 and #rows ~= 1)
        or not integer(before, 0, max_money) or not integer(after, 0, max_money)
        or not integer(cash_before, 0, max_money) or not integer(cash_after, 0, max_money) then
        error(mutates and "mutation_unverified_shop_offer_result" or "finite_shop_invalid_offer_result", 0)
    end
    last = {world = state.world, map = state.map, actor = context.name, status = status, pawn = fields[3],
        before = before, after = after,
        cash_before = cash_before, cash_after = cash_after, rows = rows}
    return "exact stock finite merchant verified"
end

function shop.rows(state, phase, status_row)
    if not last or phase ~= "hosting" or state.map ~= last.map or not same(last.world, state.world) then return {} end
    local rows = { status_row("native-finite-shop", last.status, last.map, last.pawn, last.actor, last.before,
        last.after, last.cash_before, last.cash_after, #last.rows) }
    for _, row in ipairs(last.rows) do rows[#rows + 1] = status_row("native-finite-shop-item", table.unpack(row)) end
    return rows
end

return shop
