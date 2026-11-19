local wire = require("native_wire_quest")

local map = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local world = map .. ".Borough"
local level = world .. ":PersistentLevel."
local first, questions, root, ping = "Momma_Intro", "Questions_Mamma", "Momma_Root", "Trade_Mamma"
local profile = {
    id = "mama", title = "Mama", map = map, world = world, root = root, first = first, first_flag = "Met_Mamma",
    npc = level .. "NPC_Mama_C_1", npc_kind = "NPC_Mama_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Mama.NPC_Mama_C",
    component = "Sphere_Interact", component_class = "/Script/Engine.SphereComponent",
    controller = level .. "Dialogue_Controler_BP_C_1",
    receiver = level .. "GlobalPing_ScriptingReciver_BP_C_1", talk_ping = "Talk_Mamma",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Borough_DT.Dialogue_Borough_DT",
    post = level .. "TradePost_BP_C_1080",
    row_order = {first, questions, root},
    rows = {
        [first] = {lines = 2, count = 3, choices = {3}, next = {[3] = questions}},
        [questions] = {lines = 1, count = 2, choices = {1}, next = {[1] = root}},
        [root] = {lines = 1, count = 5, choices = {1, 3, 5}, next = {[1] = false, [3] = questions, [5] = false}},
    },
    effects = {[root] = {[5] = {ping = ping}}},
    fixture_offsets = {{92, -65}, {-90, 65}, {100, 70}, {-105, -70}, {0, 115}, {0, -115}},
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local grants = {}

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("native_dialogue_mama_" .. reason, 0) end

function profile.clear() grants = {} end

local function scene(state, phase)
    if not enabled or phase ~= "hosting" or state.map ~= map or state.server ~= false or state.listening ~= true
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. world then return end
    local npc = StaticFindObject(profile.npc)
    local post = StaticFindObject(profile.post)
    local receiver = StaticFindObject(level .. "GlobalPing_ScriptingReciver_BP_C_3")
    if not exact(npc, profile.npc_class) or npc.Alive ~= true
        or not exact(npc.Sphere_Interact, profile.component_class) or not same(npc.Sphere_Interact:GetOwner(), npc)
        or not exact(post, "/Game/Classes/Actors/TradePost_BP.TradePost_BP_C")
        or not exact(post.Box, "/Script/Engine.BoxComponent") or not same(post.Box:GetOwner(), post)
        or not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C")
        or not receiver.Ping_ID or receiver.Ping_ID:ToString() ~= ping or not receiver.ActorsToPower
        or receiver.ActorsToPower:GetArrayNum() ~= 1
        or not same(receiver.ActorsToPower[1], post) then return end
    for _, object in ipairs({npc, post, receiver}) do
        if not same(object:GetWorld(), state.world) or not same(object:GetOuter(), state.world.PersistentLevel)
            or object:IsActorBeingDestroyed() or not object:HasAuthority() then return end
    end
    return npc, post
end

local function read(context)
    if not same(context.instance.SaveSlot_Ref, context.stock) or not same(context.host.Save, context.stock) then
        refuse("canonical_save_unavailable")
    end
    return {meet = wire.stock_flag(context.instance, profile.first_flag),
        cash = wire.stock_flag(context.instance, "Player_Cash"), xp = wire.stock_flag(context.instance, "Player_Xp")}
end

function profile.progress(state)
    if not scene(state, "hosting") then refuse("trade_post_unavailable") end
    local host, controller = state.local_pawn and state.local_pawn.pawn, state.controller
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
        or before.meet < 0 or before.meet > 2147483647 then refuse("meet_flag_unqualified") end
    local checked, after = pcall(function()
        context.instance:Add_PointTo_SaveFlag(key, 1, true, false)
        return read(context)
    end)
    if not checked or after.meet ~= math.max(before.meet, 1) or after.cash ~= before.cash or after.xp ~= before.xp then
        error("mutation_unverified_native_dialogue_mama_meet_flag", 0)
    end
    return after, before.meet == 0 and "opened-first" or "opened-repeat"
end

function profile.grant(state, phase, pawn, connection, context)
    local npc, post = scene(state, phase)
    if not npc then refuse("trade_post_unavailable") end
    local now = os.time()
    for name, saved in pairs(grants) do
        if now >= saved.expires or not valid(saved.pawn) or not same(saved.world, state.world) then grants[name] = nil end
    end
    grants[pawn:GetFullName()] = {pawn = pawn, world = state.world, controller = pawn.Controller,
        connection = connection, stock = context.stock, npc = npc, post = post, expires = now + 30}
end

function profile.authorized_trade(state, phase, pawn)
    local key = pawn:GetFullName()
    local grant = grants[key]
    if not grant then return end
    local npc, post = scene(state, phase)
    local controller = pawn.Controller
    local instance = state.world.OwningGameInstance
    if not npc or not valid(controller) or not same(controller, grant.controller) or not same(controller.Pawn, pawn)
        or not same(grant.pawn, pawn) or not same(grant.world, state.world) or not same(grant.npc, npc) or not same(grant.post, post)
        or not same(controller.Player, grant.connection) or not same(grant.connection.Driver, state.world.NetDriver)
        or not valid(instance) or not same(instance.SaveSlot_Ref, grant.stock) or os.time() >= grant.expires then
        grants[key] = nil
        return
    end
    return npc, post, grant.stock
end

function profile.progress_row(name, state, effect, status_row)
    return status_row("native-dialogue-progress", map, name, state.meet, state.cash, state.xp, effect)
end

return profile
