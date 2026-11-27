local view = {}
local interaction_lease = require("guest_interaction_lease")
local shopkeep = require("native_dialogue_shopkeep")
local castaway = require("native_dialogue_castaway")
local focus_fixture = require("native_shop_focus_fixture")
local stock = require("native_shop_stock")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_SHOP_VIEW") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local embedded = os.getenv("SILVERFISH_COOP_EMBEDDED_ENGINE") == "1"
local profiles = {}
local castaway_shop = {map = castaway.map, world = "World " .. castaway.world,
    actor_name = "Shop_BP_C " .. castaway.shop, reception = true}
local function register_merchant(map, level, actor, data, barter)
    local world = map .. "." .. level
    local path = world .. ":PersistentLevel." .. actor
    profiles[map] = {
        map = map, world = "World " .. world, actor_object_path = path, actor_name = "Shop_BP_C " .. path,
        data_path = "/Game/Classes/Libraries/DataAssets/Merchants/" .. data .. "." .. data,
        barter_path = barter and world .. ":PersistentLevel." .. barter or nil,
    }
end

register_merchant("/Game/Classes/Maps/Maps_OverWorld/Observitory_P",
    "Observitory_P", "Shop_BP_C_2", "Observitory_ShopData")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
    "Shop_ScavBar_P", "Shop_BP_C_2", "ScavBar_ShopData")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
    "Shop_BlackMarket_01", "Shop_BP_C_1", "BlacMarket_ShopData")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01",
    "Shop_Scientist_01", "Shop_BP_C_2", "Science_ShopData", "Barter_BP_C_107")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P",
    "Shop_Gunsmith_P", "Shop_BP_C_1", "AmmoShopData", "Barter_BP_C_1")
register_merchant(shopkeep.map, "Shop_ShopKeep_P", "Shop_BP_C_1", "StrangeJunkShopData", "Barter_BP_C_1")
profiles[shopkeep.map].reception = shopkeep.target
local smuggler = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
register_merchant(smuggler, "Shop_Smuggler_P", "Shop_BP_C_1", "SmugglerShopData", "Barter_BP_C_2")
profiles[smuggler].prove_focus = true

local actor_path = "/Game/Classes/Actors/Shop_BP.Shop_BP_C"
local max_offers, max_money, max_sequence = 128, 2147483647, 9007199254740991
local events_path, heartbeat_path, mailbox, is_downed, binding, session
local event_sequence = 0
local lease_through = 0

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function integer(value, maximum)
    if type(value) ~= "string" or #value > 16 or not value:match("^%d+$") then return nil end
    local number = math.tointeger(tonumber(value))
    if number and number >= 0 and number <= maximum then return number end
end

local function renew_lease(current)
    local sent = interaction_lease.timestamp(heartbeat_path, interaction_lease.shop, current.profile.map,
        current.name, os.time(), true)
    lease_through = sent and sent + 2 or 0
end

local function context(state, phase, castaway_handoff)
    local host = phase == "hosting"
    local trading_castaway = castaway_handoff or session and session.state ~= "closed"
        and session.context.profile == castaway_shop
    local profile = trading_castaway and castaway_shop or profiles[state.map]
    if not enabled or not events_path or (not host and phase ~= "connected") or not profile
        or not state.listening or state.server ~= (not host) or not valid(state.world)
        or state.world:GetFullName() ~= profile.world then return nil end
    local detail = state.local_pawn
    local pawn, controller = detail and detail.pawn, state.controller
    if not valid(pawn) or not valid(controller) or not pawn:IsLocallyControlled()
        or pawn:HasAuthority() ~= host or not controller:IsLocalController()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return nil end
    return {host = host, profile = profile, world = state.world, pawn = pawn, controller = controller, name = detail.name}
end

local function same_context(first, second)
    return first and second and first.host == second.host and first.name == second.name and first.profile == second.profile
        and same(first.world, second.world) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller)
end

local function qualified_actor(current)
    local profile = current.profile
    if profile == castaway_shop then
        local _, shop = castaway.counter(current.world)
        return shop
    end
    local actor = StaticFindObject(profile.actor_object_path)
    if not exact(actor, actor_path) or actor:GetFullName() ~= profile.actor_name or not same(actor:GetWorld(), current.world)
        or actor:IsActorBeingDestroyed() or not same(actor.MerchantData, StaticFindObject(profile.data_path))
        or not exact(actor.Box, "/Script/Engine.BoxComponent") or not same(actor.Box:GetOwner(), actor) then return nil end
    if profile.barter_path then
        local barter = StaticFindObject(profile.barter_path)
        local collision = actor.Box:GetCollisionEnabled()
        if not exact(barter, "/Game/Classes/Actors/Barter_BP.Barter_BP_C")
            or not same(actor.BarterReplacement, barter) or not same(barter:GetWorld(), current.world)
            or not same(barter.MerchantData, actor.MerchantData)
            or not profile.reception and (collision ~= 1 and collision ~= 3) then return nil end
    end
    return actor
end

local function standing(current)
    return not current.host and current.controller.Loading == false
        and type(is_downed) == "function" and is_downed(current.pawn) == false and os.time() <= lease_through
end

local function can_open(current)
    local menu = current.controller.MenuStack
    return standing(current) and menu and menu:GetArrayNum() == 0
end

local function can_use(current)
    if session and session.screen then return standing(current) and stock.present(session) end
    return can_open(current)
end

local function in_reach(current)
    local actor = qualified_actor(current)
    if not actor then return false end
    local component = actor.Box
    if current.profile == castaway_shop then
        local npc = castaway.counter(current.world)
        if not npc then return false end
        component = npc.Sphere_Interact
    elseif current.profile.reception then
        actor = StaticFindObject(current.profile.reception)
        if not exact(actor, shopkeep.target_class) or not same(actor:GetWorld(), current.world)
            or actor:IsActorBeingDestroyed() or not exact(actor.Box, shopkeep.component_class)
            or not same(actor.Box:GetOwner(), actor) then return false end
        local collision = actor.Box:GetCollisionEnabled()
        if collision ~= 1 and collision ~= 3 then return false end
        component = actor.Box
    end
    local here, target = current.pawn:K2_GetActorLocation(), component:K2_GetComponentLocation()
    local radius = current.pawn.InteractionRange
    if not here or not target or type(radius) ~= "number" or radius <= 0 or radius > 1000 then return false end
    return (here.X - target.X) ^ 2 + (here.Y - target.Y) ^ 2 + (here.Z - target.Z) ^ 2 < radius ^ 2
end

function view.clear()
    if session and session.screen then stock.close(session) end
    if binding and type(SilverfishClearNativeShopViewScope) == "function" then SilverfishClearNativeShopViewScope() end
    binding, session = nil, nil
    lease_through = 0
end

local function emit(kind, first, second)
    if event_sequence >= max_sequence then return end
    local profile = session.context.profile
    local next_event = event_sequence + 1
    local line = table.concat({"shop-view", next_event, session.sequence, kind, profile.map,
        session.context.name, profile.actor_name, first or "-", second or "-"}, "\t") .. "\n"
    if embedded then
        if type(SilverfishEnqueueLocalBridgeInput) ~= "function" or not SilverfishEnqueueLocalBridgeInput(line) then return end
    else
        local file = io.open(events_path, "ab")
        if not file then return end
        event_sequence = next_event
        local wrote = file:write(line)
        local closed = file:close()
        if not wrote or not closed then return end
    end
    event_sequence = next_event
    return next_event
end

local function request(kind, waiting, first, second)
    local event = emit(kind, first, second)
    session.state = event and waiting or "error"
    session.pending_event = event
end

local function active()
    return session and session.state ~= "closed" and session.state ~= "error"
end

local function close()
    if session and session.screen then stock.close(session) end
    if not session or session.state == "closed" then return end
    session.state = "closed"
    session.pending_event = nil
    emit("close")
end

local function read_press(current)
    local sequence = SilverfishTakeNativeShopViewPress(current.pawn:GetAddress())
    if type(sequence) ~= "number" or not math.tointeger(sequence) or sequence < 1 then return false end
    if not can_open(current) or not in_reach(current) or not stock.available() then return false end
    if active() or session and session.state == "error" then
        close()
    else
        session = {context = current, sequence = sequence, total = 0}
        request("open", "pending")
    end
    return true
end

function view.reception_pending(state, phase, sequence, castaway_handoff)
    local current = context(state, phase, castaway_handoff)
    if not current or not current.profile.reception or not same_context(context(state, phase), binding) or not can_open(current)
        or not in_reach(current) or not stock.available() or not math.tointeger(sequence) or sequence < 1 or sequence > max_sequence then return false end
    if active() then close() end
    binding = current
    session = {context = current, sequence = sequence, total = 0, expires = os.time() + 29}
    request("open", "pending")
    return session.state == "pending"
end

function view.reception_open(pawn)
    return active() and session.context.profile.reception and same(session.context.pawn, pawn)
end

local function stock_action()
    if not session or not session.screen or session.state ~= "ready" or not stock.present(session, true) then return end
    local guid, sell, value, quantity = stock.take(session)
    if not guid then return end
    local quote = session.stock_offers[guid]
    if math.type(quantity) ~= "integer" or quantity < 0
        or (quantity > 0 and (sell or not quote or not quote.markup)) then
        if not SilverfishSetNativeShopScreen(session.screen:GetAddress(), true) then close() end
        return
    end
    local chosen = quantity > 0
    local price = (sell or chosen) and value or quote and quote.price
    if math.type(price) ~= "integer" or price < 0 or price > max_money then
        close()
        return
    end
    if not sell and price > session.wallet then
        if not SilverfishSetNativeShopScreen(session.screen:GetAddress(), true) then close() end
        return
    end
    session.stock_selection = {guid = guid, price = price}
    request(sell and "sell" or "buy", sell and "selling" or "buying", guid,
        chosen and (tostring(price) .. "/" .. tostring(quantity)) or price)
end

function view.sync(state, phase)
    local current = context(state, phase)
    if current and current.profile.reception then
        if current.host then view.clear(); return end
        if not same_context(current, binding) then view.clear() end
        renew_lease(current)
        binding = current
        if active() and (not can_use(current) or not in_reach(current) or os.time() > session.expires) then close() end
        stock_action()
        return
    end
    if not current or type(SilverfishSetNativeShopViewScope) ~= "function"
        or type(SilverfishTakeNativeShopViewPress) ~= "function" then
        view.clear()
        return
    end
    if not same_context(current, binding) then view.clear() end
    if not current.host then renew_lease(current) end
    local accepting = can_open(current)
    if not SilverfishSetNativeShopViewScope(current.pawn:GetAddress(), current.host, accepting == true) then
        view.clear()
        return
    end
    binding = current
    if current.host then return end
    if session and session.state ~= "closed" and (not can_use(current) or not in_reach(current)) then close() end
    stock_action()
    read_press(current)
end

local function fixture(current, action)
    if not headless or not can_use(current) or not in_reach(current) then error("The staged merchant view fixture is unavailable", 0) end
    if action == "open" then
        if current.profile.reception then error("Use the exact ShopKeep dialogue response to open this view", 0) end
        local actor = qualified_actor(current)
        local probe = SilverfishProbeNativeInteractInput
        if not actor or type(probe) ~= "function" then error("The exact staged stock merchant input is unavailable", 0) end
        local invoked, ok, reason
        if current.profile.prove_focus then
            invoked, ok, reason = pcall(focus_fixture.press, current.pawn, actor.Box, probe)
        else
            local original = current.pawn.InteractionTarget_Component
            current.pawn.InteractionTarget_Component = actor.Box
            invoked, ok, reason = pcall(probe, current.pawn:GetAddress(), true)
            current.pawn.InteractionTarget_Component = valid(original) and original or nil
        end
        if not invoked and current.profile.prove_focus then error(ok, 0) end
        if not ok and current.profile.prove_focus then error(reason, 0) end
        if not invoked or not ok or reason ~= "stock_started_consumed" or current.controller.MenuStack:GetArrayNum() ~= 0
            or not read_press(current) then error("The stock merchant key did not publish one isolated shop press", 0) end
    elseif session and session.screen and session.state == "ready" then
        if action == "back" then
            close()
        else
            local source, guid = action:match("^stock:(%l+):([0-9A-F]+)$")
            local gesture, amount
            if not source then gesture, source, guid = action:match("^stock%-(%l+):(%l+):([0-9A-F]+)$") end
            if not source then
                source, guid, amount = action:match("^stock%-amount:(%l+):([0-9A-F]+):(%d+)$")
                amount = math.tointeger(tonumber(amount))
            end
            if not source or #guid ~= 32 then error("The stock merchant fixture needs an exact card", 0) end
            if gesture and gesture ~= "open" and gesture ~= "cancel" then error("Invalid merchant popup gesture", 0) end
            stock.move_fixture(session, source, guid, gesture, amount)
            if gesture then return "stock merchant quantity popup " .. gesture end
            stock_action()
            if session.state ~= "buying" and session.state ~= "selling" then
                error("The stock merchant did not publish one trade", 0)
            end
        end
    else
        error("The staged merchant input is unavailable", 0)
    end
    return "qualified stock merchant input observed"
end

function view.command(fields, state, phase)
    local operation = fields[2]
    local stock_render = operation == "native-shop-view-render-stock"
    local stock_refresh = operation == "native-shop-view-refresh-stock"
    local unavailable = operation == "native-shop-view-unavailable"
    if not stock_render and not stock_refresh and not unavailable
        and operation ~= "native-shop-view-fixture" then return end
    local current = context(state, phase)
    if not current or not same_context(current, binding) or not can_use(current) or not in_reach(current) then
        error("The exact standing merchant guest is unavailable", 0)
    end
    if operation == "native-shop-view-fixture" and #fields == 3 then return fixture(current, fields[3]) end
    local sequence = integer(fields[3], max_sequence)
    if stock_refresh then
        if #fields ~= 4 or not same_context(current, session and session.context) or sequence ~= session.sequence
            or fields[4] ~= current.profile.map or not session.screen or session.state ~= "ready" then
            error("The stock shop refresh does not match the active screen", 0)
        end
        if not stock.idle(session) then return "stock shop refresh deferred" end
        if not SilverfishSetNativeShopScreen(session.screen:GetAddress(), false) then
            return "stock shop refresh deferred"
        end
        request("open", "pending")
        if session.state ~= "pending" then error("The stock shop refresh could not queue a request", 0) end
        return "stock shop refresh queued"
    end
    local event = integer(fields[4], max_sequence)
    if not same_context(current, session and session.context) or sequence ~= session.sequence or fields[5] ~= current.profile.map
        or (session.state ~= "pending" and session.state ~= "buying" and session.state ~= "selling")
        or not event or event ~= session.pending_event then
        error("The authenticated merchant view has expired", 0)
    end
    if unavailable and #fields == 5 then
        close()
        return "stock shop closed after rejected trade"
    end
    if stock_render then
        local balance, total, nonce = integer(fields[7], max_money), integer(fields[8], max_offers), fields[9]
        if not stock.available() or #fields ~= 9 or fields[6] ~= current.profile.actor_name or not balance or not total
            or #nonce ~= 32 or not nonce:match("^[0-9a-f]+$") then error("The native merchant catalog is unavailable", 0) end
        local ok, rows = pcall(stock.render, session, qualified_actor(current), state, phase, balance,
            mailbox .. "/native-shop-stock-" .. nonce .. ".tsv", total)
        if not ok then close(); error(rows, 0) end
        session.wallet, session.total, session.stock_offers, session.state = balance, total, rows, "ready"
        session.stock_selection, session.render_event = nil, event
        session.pending_event = nil
        return "verified stock shop screen"
    end
    error("The authenticated merchant reply is invalid", 0)
end

function view.rows(state, phase, status_row)
    local current = session and session.context
    if not current or phase ~= "connected" or state.map ~= current.profile.map
        or not same(state.world, current.world) then return {} end
    local offer = session.stock_selection
    return {status_row("native-shop-view", session.state, session.sequence, current.profile.map, current.name, current.profile.actor_name,
        session.wallet or "-", session.total, session.screen and session.total or 0, 0,
        offer and offer.guid or "-", offer and offer.price or "-", "stock"),
        status_row("native-shop-view-request", session.sequence, current.profile.map, current.name, current.profile.actor_name,
            session.pending_event or 0, session.render_event or 0)}
end

function view.start(bridge, downed)
    if not enabled or not bridge or bridge == "" then return end
    events_path, heartbeat_path = bridge .. "/native-shop-view-events", bridge .. "/native-shop-view-heartbeat"
    mailbox = bridge
    is_downed = downed
end

return view
