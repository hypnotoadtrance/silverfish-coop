local barter = {}
local guest_save = require("guest_save")
local wire = require("native_wire_quest")

local map = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world = map .. ".Cistern_P"
local level = world .. ":PersistentLevel."
local npc_path = level .. "NPC_SewerTrader_C_1"
local trigger_path = level .. "Trigger_Volume_BP_C_1"
local post_path = level .. "TradePost_BP_C_1"
local receiver_path = level .. "GlobalPing_ScriptingReciver_BP_C_1"
local inventory_path = "/Game/Classes/Libraries/DataTables/TradeInventories_DT.TradeInventories_DT"
local trade_offers = "TradeOffers_6_AAB7177E4D8899D984DE8F8674A7990F"
local offer_label = "ButtonText_7_E1D013B8416183BE9BA1889087DF95D3"
local lifetime = 90
local grants, report = {}, nil

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, class) return valid(value) and same(value:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("sewer_trade_refused_" .. reason, 0) end

local function scene(state, phase)
    if phase ~= "hosting" or state.map ~= map or state.server ~= false or state.listening ~= true
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. world then return end
    local npc, trigger = StaticFindObject(npc_path), StaticFindObject(trigger_path)
    local post, receiver = StaticFindObject(post_path), StaticFindObject(receiver_path)
    local ping = valid(receiver) and receiver.Ping_ID
    if type(ping) == "userdata" then ping = ping:ToString() end
    if not exact(npc, "/Game/Classes/NPCs/NPC_Characters/NPC_SewerTrader.NPC_SewerTrader_C") or npc.Alive ~= true
        or not exact(trigger, "/Game/Classes/ScriptingActors/Trigger_Volume_BP.Trigger_Volume_BP_C")
        or not exact(trigger.Box, "/Script/Engine.BoxComponent") or not same(trigger.Box:GetOwner(), trigger)
        or not exact(post, "/Game/Classes/Actors/TradePost_BP.TradePost_BP_C")
        or not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C")
        or ping ~= "Trade_Sewer" or not receiver.ActorsToPower
        or receiver.ActorsToPower:GetArrayNum() ~= 1 or not same(receiver.ActorsToPower[1], post) then return end
    for _, actor in ipairs({npc, trigger, post, receiver}) do
        if not same(actor:GetWorld(), state.world) or not same(actor:GetOuter(), state.world.PersistentLevel)
            or actor:IsActorBeingDestroyed() or not actor:HasAuthority() then return end
    end
    return trigger.Box, post
end

local function prune(state)
    local now = os.time()
    for name, lease in pairs(grants) do
        if now >= lease.expires or not same(lease.world, state.world) or not valid(lease.pawn) or not valid(lease.controller)
            or not same(lease.controller.Pawn, lease.pawn) or not same(lease.controller.Player, lease.connection) then
            grants[name] = nil
        end
    end
end

function barter.grant(state, phase, pawn, connection, stock)
    local box, post = scene(state, phase)
    local instance = box and state.world.OwningGameInstance
    local controller = valid(pawn) and pawn.Controller
    if not valid(pawn) or not valid(instance) or not same(instance.SaveSlot_Ref, stock) or same(pawn.Save, stock)
        or not valid(controller) or not same(controller.Pawn, pawn) or not same(controller.Player, connection)
        or not same(connection.Driver, state.world.NetDriver) or pawn.Dead ~= false
        or not same(pawn:GetWorld(), state.world) or not pawn:HasAuthority() or not box:IsOverlappingActor(pawn) then refuse("grant_scope") end
    prune(state)
    grants[pawn:GetFullName()] = {world = state.world, pawn = pawn, controller = controller, connection = connection,
        stock = stock, post = post, expires = os.time() + lifetime}
end

local function authorized(state, phase, pawn, stock)
    prune(state)
    local lease = grants[pawn:GetFullName()]
    local box, post = scene(state, phase)
    return lease and box and same(lease.pawn, pawn) and same(lease.controller, pawn.Controller)
        and same(lease.connection.Driver, state.world.NetDriver) and same(lease.stock, stock)
        and same(lease.post, post) and box:IsOverlappingActor(pawn) and post
end

local function fields_from_line(line)
    local parts = {}
    for part in line:gmatch("[^|]+") do parts[#parts + 1] = part end
    return parts
end

local function offers_with_labels(encoded)
    local table_asset = StaticFindObject(inventory_path)
    if not valid(table_asset) then refuse("offer_labels") end
    local stock_row = table_asset:FindRow("SewerThing")
    local stock = stock_row and stock_row[trade_offers]
    if not stock or stock:GetArrayNum() ~= 26 then refuse("offer_labels") end
    local result = {}
    for line in encoded:gmatch("[^\n]+") do
        local parts = fields_from_line(line)
        local index = math.tointeger(tonumber(parts[1]))
        if index ~= #result + 1 or (#parts ~= 7 and #parts ~= 9 and #parts ~= 11) then refuse("offer_labels") end
        local label = stock[index][offer_label]
        if type(label) == "userdata" then label = label:ToString() end
        if type(label) ~= "string" or #label == 0 or #label > 500 then refuse("offer_labels") end
        parts[#parts + 1] = label
        result[index] = parts
    end
    if #result ~= 26 then refuse("offer_labels") end
    return result
end

function barter.command(fields, state, phase, players)
    local action = fields[2]
    local browse = action == "native-sewer-offers"
    local fixture = action == "native-sewer-mushroom-fixture"
    if not browse and not fixture and action ~= "native-sewer-trade" then return end
    report = nil
    local name = fields[3]
    local ordinal = browse and 0 or fixture and -1 or math.tointeger(tonumber(fields[4]))
    if type(SilverfishNativeSewerTrade) ~= "function" or not players or #fields ~= ((browse or fixture) and 3 or 4)
        or not name or #name > 512 or not ordinal or not browse and not fixture
        and (ordinal < 1 or ordinal > 26 or tostring(ordinal) ~= fields[4]) then
        refuse("arguments")
    end
    if fixture and (os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1") then
        refuse("fixture_unavailable")
    end
    local ok, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not ok or not valid(pawn) or not valid(save) or pawn:GetFullName() ~= name or pawn.Dead ~= false
        or not players.is_standing_guest(pawn, name) then refuse("guest_scope") end
    local instance = state.world.OwningGameInstance
    local stock = valid(instance) and instance.SaveSlot_Ref
    local post = valid(stock) and not same(save, stock) and authorized(state, phase, pawn, stock)
    if not post then refuse("dialogue_expired") end
    local before = wire.stock_flag(instance, "Player_Cash")
    if not math.tointeger(before) then refuse("wallet_scope") end
    local verified, outcome, encoded, reward, item, quantity = SilverfishNativeSewerTrade(
        pawn:GetAddress(), save:GetAddress(), stock:GetAddress(), ordinal)
    if verified ~= true then error(tostring(outcome), 0) end
    local after = wire.stock_flag(instance, "Player_Cash")
    if before ~= after or not same(instance.SaveSlot_Ref, stock) or not same(pawn.Save, save) then
        error("mutation_unverified_sewer_trade_scope", 0)
    end
    if browse then
        if outcome ~= "offers" or type(encoded) ~= "string" then refuse("stock_offers") end
        report = {world = state.world, name = name, pawn = pawn, post = post, offers = offers_with_labels(encoded)}
        return "exact guest Sewer Thing offers verified"
    end
    if outcome ~= (fixture and "seeded" or "exchanged") or type(encoded) ~= "string" or not reward or not item
        or not math.tointeger(quantity) then
        error("mutation_unverified_sewer_trade_receipt", 0)
    end
    if fixture then
        if encoded ~= "" or quantity ~= 1 then error("mutation_unverified_sewer_trade_fixture", 0) end
        report = {world = state.world, name = name, pawn = pawn, post = post, fixture = true,
            reward = reward, item = item, quantity = quantity}
        return "exact guest mushroom fixture verified"
    end
    local spent = {}
    for line in encoded:gmatch("[^\n]+") do spent[#spent + 1] = fields_from_line(line) end
    if #spent == 0 or #spent > 32 then error("mutation_unverified_sewer_trade_debits", 0) end
    report = {world = state.world, name = name, pawn = pawn, post = post, spent = spent, ordinal = ordinal,
        reward = reward, item = item, quantity = quantity, cash = before}
    return "exact guest Sewer Thing exchange verified"
end

function barter.rows(state, phase, row)
    prune(state)
    local current = report
    if not current or phase ~= "hosting" or state.map ~= map or not same(current.world, state.world)
        or not valid(current.pawn) or not valid(current.post) then return {} end
    if current.offers then
        local result = {row("native-sewer-offers", map, current.name, current.post:GetFullName(), #current.offers)}
        for _, offer in ipairs(current.offers) do result[#result + 1] = row("native-sewer-offer", table.unpack(offer)) end
        return result
    end
    if current.fixture then
        return {row("native-sewer-mushroom-fixture", map, current.name, current.post:GetFullName(),
            current.reward, current.item, current.quantity)}
    end
    local result = {row("native-sewer-trade", map, current.name, current.post:GetFullName(), current.ordinal,
        current.reward, current.item, current.quantity, current.cash, current.cash, #current.spent)}
    for _, debit in ipairs(current.spent) do result[#result + 1] = row("native-sewer-debit", table.unpack(debit)) end
    return result
end

return barter
