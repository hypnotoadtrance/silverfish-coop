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
    actor_name = "Shop_BP_C " .. castaway.shop, title = "Data Hoard", reception = true}
local function register_merchant(map, level, actor, data, title, barter)
    local world = map .. "." .. level
    local path = world .. ":PersistentLevel." .. actor
    profiles[map] = {
        map = map, world = "World " .. world, actor_object_path = path, actor_name = "Shop_BP_C " .. path,
        data_path = "/Game/Classes/Libraries/DataAssets/Merchants/" .. data .. "." .. data, title = title,
        barter_path = barter and world .. ":PersistentLevel." .. barter or nil,
    }
end

register_merchant("/Game/Classes/Maps/Maps_OverWorld/Observitory_P",
    "Observitory_P", "Shop_BP_C_2", "Observitory_ShopData", "Observatory merchant")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
    "Shop_ScavBar_P", "Shop_BP_C_2", "ScavBar_ShopData", "Scav Bar merchant")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
    "Shop_BlackMarket_01", "Shop_BP_C_1", "BlacMarket_ShopData", "Black Market merchant")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01",
    "Shop_Scientist_01", "Shop_BP_C_2", "Science_ShopData", "Scientist Shop", "Barter_BP_C_107")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P",
    "Shop_Gunsmith_P", "Shop_BP_C_1", "AmmoShopData", "Gunsmith shop", "Barter_BP_C_1")
register_merchant(shopkeep.map, "Shop_ShopKeep_P", "Shop_BP_C_1", "StrangeJunkShopData", "ShopKeep limited stock", "Barter_BP_C_1")
profiles[shopkeep.map].reception = shopkeep.target
local smuggler = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
register_merchant(smuggler, "Shop_Smuggler_P", "Shop_BP_C_1", "SmugglerShopData", "Smuggler shop", "Barter_BP_C_2")
profiles[smuggler].prove_focus = true

local actor_path = "/Game/Classes/Actors/Shop_BP.Shop_BP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local keys = {up = "Up", down = "Down", enter = "Enter", back = "BackSpace",
    ["page-up"] = "PageUp", ["page-down"] = "PageDown"}
local ordinary_keys = {"back", "up", "down", "enter"}
local paged_keys = {"back", "page-up", "page-down", "up", "down", "enter"}
local page_size, max_offers, max_money, max_sequence = 8, 128, 2147483647, 9007199254740991
local events_path, heartbeat_path, mailbox, is_downed, binding, session, widget
local event_sequence = 0
local lease_through = 0
local held = {}
local input_names = {}

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

local function remove_widget()
    if session and session.screen then stock.close(session) end
    local previous = widget
    widget = nil
    if valid(previous) then previous:RemoveFromParent() end
end

function view.clear()
    remove_widget()
    if binding and type(SilverfishClearNativeShopViewScope) == "function" then SilverfishClearNativeShopViewScope() end
    binding, session, held = nil, nil, {}
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

local function request(kind, waiting, offset, first, second)
    local event = emit(kind, first, second)
    session.state = event and waiting or "error"
    session.pending_event, session.wanted_offset = event, event and offset or nil
end

local function active()
    return session and session.state ~= "closed" and session.state ~= "error"
end

local function close()
    remove_widget()
    if not session or session.state == "closed" then return end
    session.state = "closed"
    session.pending_event, session.wanted_offset = nil, nil
    emit("close")
end

local function read_press(current)
    local sequence = SilverfishTakeNativeShopViewPress(current.pawn:GetAddress())
    if type(sequence) ~= "number" or not math.tointeger(sequence) or sequence < 1 then return false end
    if not can_open(current) or not in_reach(current) then return false end
    if active() or session and session.state == "error" then
        close()
    else
        session = {context = current, sequence = sequence, offset = 0, total = 0, offers = {}, selection = 0,
            stock = stock.available()}
        held = {}
        request("open", "pending", 0)
    end
    return true
end

function view.reception_pending(state, phase, sequence, castaway_handoff)
    local current = context(state, phase, castaway_handoff)
    if not current or not current.profile.reception or not same_context(context(state, phase), binding) or not can_open(current)
        or not in_reach(current) or not math.tointeger(sequence) or sequence < 1 or sequence > max_sequence then return false end
    if active() then close() end
    binding = current
    session = {context = current, sequence = sequence, offset = 0, total = 0, offers = {}, selection = 0,
        stock = stock.available(), expires = os.time() + 29}
    held = {}
    request("open", "pending", 0)
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
    request(sell and "sell" or "buy", sell and "selling" or "buying", 0, guid,
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

local function show()
    local current = session.context
    if not valid(widget) then
        local class = StaticFindObject(widget_path)
        if not valid(class) then
            LoadAsset(widget_path)
            class = StaticFindObject(widget_path)
        end
        local library = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(library) then error("The stock merchant text widget is unavailable", 0) end
        local created = library:Create(current.pawn, class, current.controller)
        if not exact(created, widget_path) or not same(created:GetOwningPlayer(), current.controller)
            or not exact(created.TextBlock_Notes, "/Script/UMG.TextBlock") then
            error("The exact local stock merchant widget did not qualify", 0)
        end
        widget = created
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(31) ~= true or not widget:IsInViewport() then
            error("The stock merchant text widget could not be shown", 0)
        end
        widget:SetDesiredSizeInViewport({X = 580, Y = 420})
        widget:SetPositionInViewport({X = 42, Y = 138}, false)
    end
    local lines = {current.profile.title}
    if session.wallet then lines[#lines + 1] = "Party cash  " .. session.wallet end
    if #session.offers > 0 then
        lines[#lines + 1] = session.paging and string.format("Showing %d-%d of %d offers",
            session.offset + 1, session.offset + #session.offers, session.total)
            or string.format("Showing %d of %d offers", #session.offers, session.total)
        for index, offer in ipairs(session.offers) do
            lines[#lines + 1] = string.format("%s %s  x%d  %d", index == session.selection and ">" or " ",
                offer.name, offer.quantity, offer.price)
        end
    elseif session.state == "ready" then
        lines[#lines + 1] = "No finite stock available"
    end
    local offer = session.offers[session.selection]
    if session.state == "confirming" and offer then
        lines[#lines + 1] = string.format("Buy %d x %s for %d? Enter confirms. Backspace cancels.",
            offer.quantity, offer.name, offer.price)
    elseif session.state == "buying" then
        lines[#lines + 1] = "Purchase pending"
    elseif session.state == "paging" then
        lines[#lines + 1] = "Checking the next stock page"
    elseif session.state == "error" then
        lines[#lines + 1] = "Merchant unavailable. Backspace or interact to close."
    else
        if offer and offer.price > session.wallet then lines[#lines + 1] = "This offer exceeds party cash." end
        lines[#lines + 1] = "Up / Down selects. Enter buys. Backspace closes."
        if session.paging and session.total > page_size then lines[#lines + 1] = "Page Up / Page Down changes page." end
    end
    local text = table.concat(lines, "\n")
    widget.TextBlock_Notes:SetText(FText(text))
    if widget.TextBlock_Notes:GetText():ToString() ~= text then error("The stock merchant text did not verify", 0) end
end

local function choose(action)
    if not session or session.state == "closed" or session.state == "pending" then return end
    if action == "back" then
        if session.state == "confirming" then session.state = "ready" elseif session.state ~= "buying" then close() end
    elseif session.state == "ready" or session.state == "confirming" then
        local offer = session.offers[session.selection]
        if action == "enter" and offer and offer.price <= session.wallet then
            if session.state == "confirming" then
                request("buy", "buying", session.offset, offer.guid, offer.price)
            else
                session.state = "confirming"
            end
        elseif session.paging and (action == "page-up" or action == "page-down") then
            local offset = session.offset + (action == "page-up" and -page_size or page_size)
            if offset >= 0 and offset < session.total then request("page", "paging", offset, session.offset, offset) end
        elseif (action == "up" or action == "down") and offer then
            local direction = action == "up" and -1 or 1
            session.selection = math.min(#session.offers, math.max(1, session.selection + direction))
            session.state = "ready"
        end
    end
    if session.state ~= "closed" then show() end
end

local function poll_keys()
    if not session or not valid(widget) or session.state == "closed" or not can_open(session.context) then return end
    local changed
    for _, action in ipairs(session.paging and paged_keys or ordinary_keys) do
        if not input_names[action] then
            local name = FName(keys[action], EFindName.FNAME_Find)
            if name:GetComparisonIndex() == 0 then error("The stock merchant input key did not resolve", 0) end
            input_names[action] = {KeyName = name}
        end
        local pressed = session.context.controller:IsInputKeyDown(input_names[action])
        if type(pressed) ~= "boolean" then error("The stock merchant key state could not verify", 0) end
        if pressed and held[action] == false and not changed then changed = action end
        held[action] = pressed
    end
    if changed then choose(changed) end
end

local function offers(fields, first, offset, total)
    if #fields - first + 1 ~= math.min(page_size, total - offset) then return nil end
    local list, seen = {}, {}
    for index = first, #fields do
        local guid, path, quantity, price = fields[index]:match("^([0-9A-F]+)|(/Game/[%w_./%-]+)|(%d+)|(%d+)$")
        local name = path and #path <= 512 and path:match("%.([%w_%-]+)_C$")
        quantity, price = integer(quantity, max_money), integer(price, max_money)
        if not guid or #guid ~= 32 or not guid:find("[^0]") or seen[guid]
            or not name or not quantity or quantity < 1 or not price then return nil end
        seen[guid] = true
        list[#list + 1] = {guid = guid, path = path, name = name:gsub("_", " "), quantity = quantity, price = price}
    end
    return list
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
    elseif keys[action] and session and valid(widget) and (session.paging or (action ~= "page-up" and action ~= "page-down")) then
        choose(action)
    else
        error("The staged merchant input is unavailable", 0)
    end
    return "qualified stock merchant input observed"
end

function view.command(fields, state, phase)
    local operation = fields[2]
    local page_render = operation == "native-shop-view-render-page"
    local stock_render = operation == "native-shop-view-render-stock"
    local stock_refresh = operation == "native-shop-view-refresh-stock"
    local unavailable = operation == "native-shop-view-unavailable"
    if operation ~= "native-shop-view-render" and not page_render and not stock_render and not stock_refresh and not unavailable
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
        request("open", "pending", 0)
        if session.state ~= "pending" then error("The stock shop refresh could not queue a request", 0) end
        return "stock shop refresh queued"
    end
    local render_with_event = page_render or stock_render
    local tokened = render_with_event or (unavailable and #fields == 5)
    local event = tokened and integer(fields[render_with_event and 4 or 5], max_sequence)
    local map = fields[render_with_event and 5 or 4]
    if not same_context(current, session and session.context) or sequence ~= session.sequence or map ~= current.profile.map
        or (session.state ~= "pending" and session.state ~= "buying" and session.state ~= "selling" and session.state ~= "paging")
        or (tokened and (not event or event ~= session.pending_event)) or (session.paging and not tokened) then
        error("The authenticated merchant view has expired", 0)
    end
    if stock_render then
        local balance, total, nonce = integer(fields[7], max_money), integer(fields[8], max_offers), fields[9]
        if not session.stock or #fields ~= 9 or fields[6] ~= current.profile.actor_name or not balance or not total
            or #nonce ~= 32 or not nonce:match("^[0-9a-f]+$") then error("The native merchant catalog is unavailable", 0) end
        local ok, rows = pcall(stock.render, session, qualified_actor(current), state, phase, balance,
            mailbox .. "/native-shop-stock-" .. nonce .. ".tsv", total)
        if not ok then close(); error(rows, 0) end
        session.wallet, session.total, session.stock_offers, session.state = balance, total, rows, "ready"
        session.stock_selection, session.render_event = nil, event
        session.pending_event, session.wanted_offset = nil, nil
        return "verified stock shop screen"
    end
    if unavailable and (#fields == 4 or #fields == 5) then
        if session.stock then close(); return "stock shop closed after rejected trade" end
        session.state, session.offers, session.total, session.selection, session.offset = "error", {}, 0, 0, 0
        session.wallet, session.render_event = nil, nil
    else
        local first = page_render and 10 or 8
        local balance, total = integer(fields[first - 2], max_money), integer(fields[first - 1], max_offers)
        local offset = 0
        if page_render then
            offset = integer(fields[7], max_offers)
            local last = total and (math.max(0, total - 1) // page_size) * page_size
            if not offset or not last or offset ~= math.min(session.wanted_offset, last) then
                error("The authenticated merchant page does not match the requested offset", 0)
            end
        end
        local catalog = total and offers(fields, first, offset, total)
        if (not page_render and operation ~= "native-shop-view-render")
            or fields[page_render and 6 or 5] ~= current.profile.actor_name or not balance or not catalog then
            error("The authenticated merchant catalog is unavailable", 0)
        end
        session.wallet, session.total, session.offers = balance, total, catalog
        session.state, session.selection = "ready", #catalog > 0 and 1 or 0
        session.paging, session.offset, session.render_event = page_render, offset, event or nil
        held = {}
    end
    session.pending_event, session.wanted_offset = nil, nil
    local ok, failure = pcall(show)
    if not ok then
        remove_widget()
        session.state = "error"
        error(failure, 0)
    end
    return "verified local merchant view"
end

function view.rows(state, phase, status_row)
    local current = session and session.context
    if not current or phase ~= "connected" or state.map ~= current.profile.map
        or not same(state.world, current.world) then return {} end
    local offer = session.stock_selection or session.offers[session.selection]
    local visible = session.stock and session.screen and session.total or #session.offers
    return {status_row("native-shop-view", session.state, session.sequence, current.profile.map, current.name, current.profile.actor_name,
        session.wallet or "-", session.total, visible, session.selection, offer and offer.guid or "-", offer and offer.price or "-",
        session.stock and "stock" or "list"),
        status_row("native-shop-view-page", session.sequence, current.profile.map, current.name, current.profile.actor_name,
            session.offset, session.pending_event or 0, session.wanted_offset or session.offset, session.render_event or 0)}
end

function view.start(bridge, downed)
    if not enabled or not bridge or bridge == "" then return end
    events_path, heartbeat_path = bridge .. "/native-shop-view-events", bridge .. "/native-shop-view-heartbeat"
    mailbox = bridge
    is_downed = downed
    LoopInGameThreadWithDelay(16, function()
        local ok, failure = pcall(poll_keys)
        if not ok then
            if session then session.state = "error" end
            remove_widget()
            print("[SilverfishCoop] Merchant input unavailable: " .. tostring(failure) .. "\n")
        end
    end)
end

return view
