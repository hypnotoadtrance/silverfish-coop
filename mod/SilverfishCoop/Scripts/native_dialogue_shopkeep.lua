local wire = require("native_wire_quest")

local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
local world = map .. ".Shop_ShopKeep_P"
local level = world .. ":PersistentLevel."
local first, root, ping = "ShopKeep_FirstMeet", "ShopKeep_Default", "ShopKeep_Shop"
local profile = {
    id = "shopkeep", title = "ShopKeep", map = map, world = world, root = root, first = first,
    first_flag = "Meet_ShopKeep",
    npc = level .. "NPC_ShopKeep_C_1", npc_kind = "NPC_ShopKeep_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_ShopKeep.NPC_ShopKeep_C",
    target = level .. "Generic_Interact_BP_C_1", target_kind = "Generic_Interact_BP_C",
    target_class = "/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C",
    component = "Box", component_class = "/Script/Engine.BoxComponent",
    controller = level .. "Dialogue_Controler_BP_C_1",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_ShopKeep_DT.Dialogue_ShopKeep_DT",
    actor = level .. "Shop_BP_C_1",
    row_order = {first, root},
    rows = {
        [first] = {lines = 6, count = 2, choices = {1}, next = {[1] = root}},
        [root] = {lines = 1, count = 5, choices = {2}, next = {[2] = root}},
    },
    effects = {[first] = {[1] = {ping = ping}}, [root] = {[2] = {ping = ping}}},
    fixture_offsets = {{0, 115}, {-70, 105}, {70, 105}, {-115, 0}, {115, 0}},
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local cash_scopes = {}

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("native_dialogue_shopkeep_" .. reason, 0) end
local function named(value) return type(value) == "userdata" and value:ToString() or value end

local function links(actor, field, expected)
    local values = actor[field]
    return values and values:GetArrayNum() == 1 and same(values[1], expected)
end

local function counter(state, phase)
    if not enabled or phase ~= "hosting" or state.map ~= map or state.server ~= false or state.listening ~= true
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. world then return nil, "reception_world_unavailable" end
    local scene = state.world
    local target, npc = StaticFindObject(profile.target), StaticFindObject(profile.npc)
    local actor = StaticFindObject(profile.actor)
    local dialogue = StaticFindObject(profile.controller)
    local branch = StaticFindObject(level .. "BasicBranch_BP_C_1")
    local receiver = StaticFindObject(level .. "GlobalPing_ScriptingReciver_BP_C_1")
    if not exact(target, profile.target_class) or not exact(npc, profile.npc_class) or npc.Alive ~= true
        or not exact(actor, "/Game/Classes/Actors/Shop_BP.Shop_BP_C")
        or not exact(dialogue, "/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C")
        or not exact(branch, "/Game/Classes/ScriptingActors/BasicBranch_BP.BasicBranch_BP_C")
        or not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C") then
        return nil, "reception_actors_unavailable"
    end
    if named(receiver.Ping_ID) ~= ping or not links(receiver, "ActorsToPower", actor) or not links(target, "Actors_ToPower", branch) then
        return nil, "reception_signal_unqualified"
    end
    if not same(actor.MerchantData, StaticFindObject("/Game/Classes/Libraries/DataAssets/Merchants/StrangeJunkShopData.StrangeJunkShopData"))
        or not exact(target.Box, profile.component_class) or not same(target.Box:GetOwner(), target) then
        return nil, "reception_shop_or_box_unqualified"
    end
    for _, object in ipairs({target, npc, actor, dialogue, branch, receiver}) do
        if not same(object:GetWorld(), scene) or object:IsActorBeingDestroyed() or not object:HasAuthority()
            or not same(object:GetOuter(), scene.PersistentLevel) then return nil, "reception_actor_world_unqualified" end
    end
    local collision = target.Box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return nil, "reception_collision_unavailable" end
    return target, actor
end

local function read(context)
    if not same(context.instance.SaveSlot_Ref, context.stock) or not same(context.host.Save, context.stock) then
        refuse("canonical_save_unavailable")
    end
    if wire.stock_flag(context.instance, "Gaurd_Shopkeep_Mad") == 1 then refuse("reception_closed") end
    return {meet = wire.stock_flag(context.instance, profile.first_flag),
        cash = wire.stock_flag(context.instance, "Player_Cash"), xp = wire.stock_flag(context.instance, "Player_Xp")}
end

function profile.progress(state)
    local reception, reason = counter(state, "hosting")
    if not reception then refuse(reason) end
    local host = state.local_pawn and state.local_pawn.pawn
    local controller = state.controller
    local instance = state.world.OwningGameInstance
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")
        or not exact(stock, "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C") or not valid(host) or not valid(controller)
        or not host:HasAuthority() or not controller:IsLocalController() or not same(host.Controller, controller)
        or not same(controller.Pawn, host) or not same(host:GetWorld(), state.world)
        or not same(instance["As Player FP"], host) or not same(host.Save, stock) then refuse("canonical_save_unavailable") end
    local context = {host = host, instance = instance, stock = stock}
    return read(context), context
end

function profile.open(context, before)
    local key = FName(profile.first_flag, EFindName.FNAME_Find)
    if key:GetComparisonIndex() == 0 or key:ToString() ~= profile.first_flag or not math.tointeger(before.meet)
        or before.meet < 0 or before.meet >= 2147483647 then refuse("meet_flag_unqualified") end
    local checked, after = pcall(function()
        context.instance:Add_PointTo_SaveFlag(key, 1, true, false)
        return read(context)
    end)
    if not checked or after.meet ~= before.meet + 1 or after.cash ~= before.cash or after.xp ~= before.xp then
        error("mutation_unverified_native_dialogue_shopkeep_meet_flag", 0)
    end
    return after, before.meet == 0 and "opened-first" or "opened-repeat"
end

function profile.grant_cash(state, phase, pawn, connection, context)
    local reception, actor = counter(state, phase)
    if not reception then refuse(actor) end
    if context.stock.BarterOnly ~= false then refuse("stock_cash_counter_unavailable") end
    local key = pawn:GetFullName()
    for name, saved in pairs(cash_scopes) do
        if not valid(saved.pawn) or os.time() > saved.expires or not same(saved.world, state.world) then cash_scopes[name] = nil end
    end
    cash_scopes[key] = {world = state.world, controller = pawn.Controller, pawn = pawn, connection = connection,
        stock = context.stock, reception = reception, actor = actor, expires = os.time() + 30}
end

function profile.authorized_counter(state, phase, pawn, stock)
    local key = pawn:GetFullName()
    local grant = cash_scopes[key]
    if not grant then return end
    local reception, actor = counter(state, phase)
    local controller = pawn.Controller
    if not reception or not valid(controller) or not same(controller, grant.controller)
        or not same(controller.Pawn, pawn) or not same(controller.Player, grant.connection)
        or not same(grant.connection.Driver, state.world.NetDriver)
        or not same(reception, grant.reception) or not same(actor, grant.actor) or not same(state.world, grant.world)
        or not same(stock, grant.stock) or not same(pawn, grant.pawn) or stock.BarterOnly ~= false or os.time() > grant.expires
        or wire.stock_flag(state.world.OwningGameInstance, "Gaurd_Shopkeep_Mad") == 1 then
        cash_scopes[key] = nil
        return
    end
    return reception
end

function profile.progress_row(name, state, effect, status_row)
    return status_row("native-dialogue-progress", map, name, state.meet, state.cash, state.xp, effect)
end

return profile
