local view = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_SHOP_VIEW") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local profiles = {}
local function register_merchant(map, level, actor, data, title)
    local world = map .. "." .. level
    local path = world .. ":PersistentLevel." .. actor
    profiles[map] = {
        map = map, world = "World " .. world, actor_object_path = path, actor_name = "Shop_BP_C " .. path,
        data_path = "/Game/Classes/Libraries/DataAssets/Merchants/" .. data .. "." .. data, title = title,
    }
end

register_merchant("/Game/Classes/Maps/Maps_OverWorld/Observitory_P",
    "Observitory_P", "Shop_BP_C_2", "Observitory_ShopData", "Observatory merchant")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P",
    "Shop_ScavBar_P", "Shop_BP_C_2", "ScavBar_ShopData", "Scav Bar merchant")
register_merchant("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01",
    "Shop_BlackMarket_01", "Shop_BP_C_1", "BlacMarket_ShopData", "Black Market merchant")

local actor_path = "/Game/Classes/Actors/Shop_BP.Shop_BP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local keys = {up = "Up", down = "Down", enter = "Enter", back = "BackSpace"}
local page_size, max_offers, max_money = 8, 128, 2147483647
local events_path, heartbeat_path, is_downed, binding, session, widget
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

local function renew_lease()
    local file = io.open(heartbeat_path, "rb")
    if not file then
        lease_through = 0
        return
    end
    local value = file:read(32)
    file:close()
    local timestamp = value and value:match("^(%d+)%s*$")
    local sent = timestamp and #timestamp <= 12 and tonumber(timestamp)
    if sent and sent <= os.time() then lease_through = sent + 2 else lease_through = 0 end
end

local function context(state, phase)
    local host = phase == "hosting"
    local profile = profiles[state.map]
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
    local actor = StaticFindObject(profile.actor_object_path)
    if not exact(actor, actor_path) or actor:GetFullName() ~= profile.actor_name or not same(actor:GetWorld(), current.world)
        or actor:IsActorBeingDestroyed() or not same(actor.MerchantData, StaticFindObject(profile.data_path))
        or not exact(actor.Box, "/Script/Engine.BoxComponent") or not same(actor.Box:GetOwner(), actor) then return nil end
    return actor
end

local function can_open(current)
    local menu = current.controller.MenuStack
    return not current.host and current.controller.Loading == false and menu and menu:GetArrayNum() == 0
        and type(is_downed) == "function" and is_downed(current.pawn) == false and os.time() <= lease_through
end

local function in_reach(current)
    local actor = qualified_actor(current)
    if not actor then return false end
    local here, target = current.pawn:K2_GetActorLocation(), actor.Box:K2_GetComponentLocation()
    local radius = current.pawn.InteractionRange
    if not here or not target or type(radius) ~= "number" or radius <= 0 or radius > 1000 then return false end
    return (here.X - target.X) ^ 2 + (here.Y - target.Y) ^ 2 + (here.Z - target.Z) ^ 2 < radius ^ 2
end

local function remove_widget()
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

local function emit(kind, offer)
    local profile = session.context.profile
    local file = io.open(events_path, "ab")
    if not file then return false end
    event_sequence = event_sequence + 1
    local wrote = file:write(table.concat({"shop-view", event_sequence, session.sequence, kind, profile.map,
        session.context.name, profile.actor_name, offer and offer.guid or "-", offer and offer.price or "-"}, "\t"), "\n")
    local closed = file:close()
    return wrote and closed
end

local function active()
    return session and session.state ~= "closed" and session.state ~= "error"
end

local function close()
    remove_widget()
    if not session or session.state == "closed" then return end
    session.state = "closed"
    emit("close")
end

local function read_press(current)
    local sequence = SilverfishTakeNativeShopViewPress(current.pawn:GetAddress())
    if type(sequence) ~= "number" or not math.tointeger(sequence) or sequence < 1 then return false end
    if not can_open(current) or not in_reach(current) then return false end
    if active() or session and session.state == "error" then
        close()
    else
        session = {context = current, sequence = sequence, state = "pending", total = 0, offers = {}, selection = 0}
        held = {}
        if not emit("open") then session.state = "error" end
    end
    return true
end

function view.sync(state, phase)
    local current = context(state, phase)
    if not current or type(SilverfishSetNativeShopViewScope) ~= "function"
        or type(SilverfishTakeNativeShopViewPress) ~= "function" then
        view.clear()
        return
    end
    if not same_context(current, binding) then view.clear() end
    if not current.host then renew_lease() end
    local accepting = can_open(current)
    if not SilverfishSetNativeShopViewScope(current.pawn:GetAddress(), current.host, accepting == true) then
        view.clear()
        return
    end
    binding = current
    if current.host then return end
    if session and session.state ~= "closed" and (not accepting or not in_reach(current)) then close() end
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
        lines[#lines + 1] = string.format("Showing %d of %d offers", #session.offers, session.total)
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
    elseif session.state == "error" then
        lines[#lines + 1] = "Merchant unavailable. Backspace or interact to close."
    else
        if offer and offer.price > session.wallet then lines[#lines + 1] = "This offer exceeds party cash." end
        lines[#lines + 1] = "Up / Down selects. Enter buys. Backspace closes."
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
                session.state = emit("buy", offer) and "buying" or "error"
            else
                session.state = "confirming"
            end
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
    for _, action in ipairs({"back", "up", "down", "enter"}) do
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

local function offers(fields, total)
    if #fields - 7 ~= math.min(page_size, total) then return nil end
    local list, seen = {}, {}
    for index = 8, #fields do
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
    if not headless or not can_open(current) or not in_reach(current) then error("The staged merchant view fixture is unavailable", 0) end
    if action == "open" then
        local actor = qualified_actor(current)
        local probe = SilverfishProbeNativeInteractInput
        if not actor or type(probe) ~= "function" then error("The exact staged stock merchant input is unavailable", 0) end
        local original = current.pawn.InteractionTarget_Component
        current.pawn.InteractionTarget_Component = actor.Box
        local invoked, ok, reason = pcall(probe, current.pawn:GetAddress(), true)
        current.pawn.InteractionTarget_Component = valid(original) and original or nil
        if not invoked or not ok or reason ~= "stock_started_consumed" or current.controller.MenuStack:GetArrayNum() ~= 0
            or not read_press(current) then error("The stock merchant key did not publish one isolated shop press", 0) end
    elseif keys[action] and session and valid(widget) then
        choose(action)
    else
        error("The staged merchant input is unavailable", 0)
    end
    return "qualified stock merchant input observed"
end

function view.command(fields, state, phase)
    local operation = fields[2]
    if operation ~= "native-shop-view-render" and operation ~= "native-shop-view-unavailable"
        and operation ~= "native-shop-view-fixture" then return end
    local current = context(state, phase)
    if not current or not same_context(current, binding) or not can_open(current) or not in_reach(current) then
        error("The exact standing merchant guest is unavailable", 0)
    end
    if operation == "native-shop-view-fixture" and #fields == 3 then return fixture(current, fields[3]) end
    local sequence = integer(fields[3], 9007199254740991)
    if not same_context(current, session and session.context) or sequence ~= session.sequence or fields[4] ~= current.profile.map
        or (session.state ~= "pending" and session.state ~= "buying") then
        error("The authenticated merchant view has expired", 0)
    end
    if operation == "native-shop-view-unavailable" and #fields == 4 then
        session.state, session.offers, session.total, session.selection = "error", {}, 0, 0
        session.wallet = nil
    else
        local balance, total = integer(fields[6], max_money), integer(fields[7], max_offers)
        local catalog = total and offers(fields, total)
        if operation ~= "native-shop-view-render" or fields[5] ~= current.profile.actor_name or not balance or not catalog then
            error("The authenticated merchant catalog is unavailable", 0)
        end
        session.wallet, session.total, session.offers = balance, total, catalog
        session.state, session.selection = "ready", #catalog > 0 and 1 or 0
        held = {}
    end
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
    local offer = session.offers[session.selection]
    return {status_row("native-shop-view", session.state, session.sequence, current.profile.map, current.name, current.profile.actor_name,
        session.wallet or "-", session.total, #session.offers, session.selection, offer and offer.guid or "-", offer and offer.price or "-")}
end

function view.start(bridge, downed)
    if not enabled or not bridge or bridge == "" then return end
    events_path, heartbeat_path = bridge .. "/native-shop-view-events", bridge .. "/native-shop-view-heartbeat"
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
