local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world = map .. ".Observitory_P"
local level = world .. ":PersistentLevel."
local root, story = "Cast_Root", "Cast_Origin"
local profile = {
    id = "castaway", title = "Castaway", map = map, world = world, root = root,
    npc = level .. "NPC_Castaway_C_1", npc_kind = "NPC_Castaway_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Castaway.NPC_Castaway_C",
    component = "Sphere_Interact", component_class = "/Script/Engine.SphereComponent",
    controller = level .. "Dialogue_Controler_BP_C_3", receiver = level .. "GlobalPing_ScriptingReciver_BP_C_3",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Observatory.Dialogue_Observatory",
    shop = level .. "Shop_BP_C_3", barter = level .. "Barter_BP_C_1568", data = "Castaway_TradeData",
    row_order = {root, story},
    rows = {[root] = {lines = 1, choices = {1, 2, 3}, next = {[1] = false, [2] = false, [3] = story}, count = 3},
        [story] = {lines = 3, choices = {1}, next = {[1] = root}, count = 1, continuation = true}},
    effects = {[root] = {[2] = {ping = "Trade_Cast"}}},
}
local grants = {}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, path) return valid(object) and same(object:GetClass(), StaticFindObject(path)) end

function profile.counter(scene)
    local npc = StaticFindObject(profile.npc)
    local shop = StaticFindObject(profile.shop)
    local receiver = StaticFindObject(level .. "GlobalPing_ScriptingReciver_BP_C_4")
    if not exact(npc, profile.npc_class) or npc.Alive ~= true
        or not exact(shop, "/Game/Classes/Actors/Shop_BP.Shop_BP_C")
        or not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C")
        or not same(shop.MerchantData, StaticFindObject("/Game/Classes/Libraries/DataAssets/Merchants/"
            .. profile.data .. "." .. profile.data))
        or receiver.Ping_ID:ToString() ~= "Trade_Cast" or #receiver.ActorsToPower ~= 1
        or not same(receiver.ActorsToPower[1], shop) then return end
    for _, actor in ipairs({npc, shop, receiver}) do
        if not same(actor:GetWorld(), scene) or actor:IsActorBeingDestroyed() then return end
    end
    local sphere = npc.Sphere_Interact
    if not exact(sphere, profile.component_class) or not same(sphere:GetOwner(), npc) then return end
    local mode = sphere:GetCollisionEnabled()
    if mode ~= 1 and mode ~= 3 then return end
    return npc, shop
end

function profile.grant(state, pawn, connection)
    local npc, shop = profile.counter(state.world)
    local stock = state.world.OwningGameInstance.SaveSlot_Ref
    if not npc or not npc:HasAuthority() or not shop:HasAuthority() or not valid(stock) or stock.BarterOnly ~= false then
        error("native_dialogue_castaway_cash_shop_unavailable", 0)
    end
    local now = os.time()
    for name, grant in pairs(grants) do
        if grant.expires < now or not same(grant.world, state.world) then grants[name] = nil end
    end
    grants[pawn:GetFullName()] = {world = state.world, pawn = pawn, save = pawn.Save, connection = connection,
        shop = shop, expires = now + 30}
end

function profile.authorized_counter(state, pawn, actor)
    local grant = grants[pawn:GetFullName()]
    local controller = pawn.Controller
    if not grant or grant.expires < os.time() or not same(grant.world, state.world) or not same(grant.pawn, pawn)
        or not same(grant.save, pawn.Save) or not same(grant.shop, actor) or not valid(controller)
        or not same(controller.Player, grant.connection) or not same(grant.connection.Driver, state.world.NetDriver) then return end
    local npc, shop = profile.counter(state.world)
    if same(shop, actor) then return npc end
end

return profile
