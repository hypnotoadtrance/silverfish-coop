local transport = require("native_transport")
local wire = require("native_wire_quest")

local map = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local world = map .. ".Cistern_P"
local level = world .. ":PersistentLevel."
local first, root, ping = "FirstMeet", "Greet", "Trade_Sewer"
local profile = {
    id = "sewer", title = "Sewer Thing", map = map, world = world, root = root, first = first,
    first_flag = "Meet_SewerThing", volume = true,
    npc = level .. "NPC_SewerTrader_C_1", npc_kind = "NPC_SewerTrader_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_SewerTrader.NPC_SewerTrader_C",
    target = level .. "Trigger_Volume_BP_C_1", target_kind = "Trigger_Volume_BP_C",
    target_class = "/Game/Classes/ScriptingActors/Trigger_Volume_BP.Trigger_Volume_BP_C",
    component = "Box", component_class = "/Script/Engine.BoxComponent",
    controller = level .. "Dialogue_Controler_BP_C_1",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_SewerThing_DT.Dialogue_SewerThing_DT",
    row_order = {first, root},
    rows = {
        [first] = {lines = 3, count = 2, choices = {1, 2}, next = {[1] = false, [2] = false}},
        [root] = {lines = 1, count = 2, choices = {1, 2}, next = {[1] = false, [2] = false}},
    },
    effects = {[first] = {[1] = {ping = ping}}, [root] = {[1] = {ping = ping}}},
}

local function same(a, b)
    return a and a:IsValid() and b and b:IsValid() and a:GetAddress() == b:GetAddress()
end

local function read(context)
    if not same(context.instance.SaveSlot_Ref, context.stock) or not same(context.host.Save, context.stock) then
        error("native_dialogue_sewer_save_unavailable", 0)
    end
    return {meet = wire.stock_flag(context.instance, profile.first_flag),
        cash = wire.stock_flag(context.instance, "Player_Cash"), xp = wire.stock_flag(context.instance, "Player_Xp")}
end

function profile.progress(state)
    local host, fresh = transport.scoped_host(state, "hosting")
    local instance = state.world.OwningGameInstance
    local stock = instance and instance:IsValid() and instance.SaveSlot_Ref
    if not fresh or not host or not stock or not stock:IsValid() then error("native_dialogue_sewer_save_unavailable", 0) end
    local context = {host = host.pawn, instance = instance, stock = stock}
    return read(context), context
end

function profile.open(context, before)
    local flag = FName(profile.first_flag, EFindName.FNAME_Find)
    if flag:GetComparisonIndex() == 0 or flag:ToString() ~= profile.first_flag or not math.tointeger(before.meet)
        or before.meet < 0 or before.meet > 2147483647 then error("native_dialogue_sewer_meet_flag_unqualified", 0) end
    if before.meet > 0 then return before, "opened-repeat" end
    local checked, after = pcall(function()
        context.instance:Add_PointTo_SaveFlag(flag, 1, true, false)
        return read(context)
    end)
    if not checked or after.meet ~= 1 or after.cash ~= before.cash or after.xp ~= before.xp then
        error("mutation_unverified_native_dialogue_sewer_meet_flag", 0)
    end
    return after, "opened-first"
end

function profile.grant(state, phase, pawn, connection, context)
    local barter = require("native_sewer_barter")
    barter.grant(state, phase, pawn, connection, context.stock)
end

function profile.progress_row(name, state, effect, status_row)
    return status_row("native-dialogue-progress", map, name, state.meet, state.cash, state.xp, effect)
end

local function top_menu(state)
    local stack = state.controller and state.controller.MenuStack
    local count = stack and stack:GetArrayNum()
    if count and count > 0 and count <= 8 then return stack[count] end
end

local function exact(object, class)
    return object and object:IsValid() and same(object:GetClass(), StaticFindObject(class))
end

local dialogue_class = "/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C"
local trade_class = "/Game/Classes/GUI/Menu_Trade_Widget.Menu_Trade_Widget_C"

function profile.host_rows(state, phase, row)
    if phase ~= "hosting" or state.map ~= map or state.server ~= false or state.listening ~= true then return {} end
    local menu = top_menu(state)
    if exact(menu, dialogue_class) and menu["Convo Pointer"] and same(menu["Convo Pointer"].DataTable, StaticFindObject(profile.source)) then
        return {row("native-dialogue-sewer-host-menu", map, "dialogue")}
    end
    if exact(menu, trade_class) and same(menu.Shop, StaticFindObject(level .. "TradePost_BP_C_1")) then
        return {row("native-dialogue-sewer-host-menu", map, "trade")}
    end
    return {}
end

function profile.host_fixture(fields, state, phase)
    if #fields ~= 3 or phase ~= "hosting" or state.map ~= map or state.server ~= false
        or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then
        error("native_dialogue_sewer_fixture_unavailable", 0)
    end
    local host, ready = transport.scoped_host(state, phase)
    local pawn, trigger = host and host.pawn, StaticFindObject(profile.target)
    local box = exact(trigger, profile.target_class) and trigger.Box
    local controller = StaticFindObject(profile.controller)
    if not ready or not pawn or pawn.Dead ~= false or not exact(box, profile.component_class) or not same(box:GetOwner(), trigger)
        or not same(trigger:GetWorld(), state.world) or not same(trigger:GetOuter(), state.world.PersistentLevel)
        or not trigger.ActorsToPower or trigger.ActorsToPower:GetArrayNum() ~= 1
        or not same(trigger.ActorsToPower[1], controller) then error("native_dialogue_sewer_fixture_scope", 0) end
    if fields[3] == "enter" then
        local center = box:K2_GetComponentLocation()
        if box:IsOverlappingActor(pawn) or not pawn:K2_TeleportTo({X = center.X, Y = center.Y - 20, Z = center.Z + 45},
            pawn:K2_GetActorRotation()) or box:IsOverlappingActor(pawn) ~= true then error("native_dialogue_sewer_fixture_overlap", 0) end
        pawn:ForceNetUpdate()
        return "host entered the original Sewer Thing trigger"
    end
    local menu = top_menu(state)
    if fields[3] == "trade" and exact(menu, trade_class) and same(menu.Shop, StaticFindObject(level .. "TradePost_BP_C_1")) then
        return "original Sewer Thing trade is open"
    end
    if fields[3] ~= "trade" or not exact(menu, dialogue_class) or not same(menu:GetOwningPlayer(), state.controller)
        or not menu["Convo Pointer"] or not same(menu["Convo Pointer"].DataTable, StaticFindObject(profile.source)) then
        error("native_dialogue_sewer_fixture_menu", 0)
    end
    local responses = menu.VerticalBox_PlayerResponces
    if not exact(responses, "/Script/UMG.VerticalBox") then error("native_dialogue_sewer_fixture_responses", 0) end
    if responses:GetChildrenCount() == 0 then
        menu:Enter_AdvanceText()
        return "advanced the original Sewer Thing dialogue"
    end
    for index = 0, math.min(responses:GetChildrenCount(), 2) - 1 do
        local button = responses:GetChildAt(index)
        if exact(button, "/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C")
            and same(button.ParentWidget_Ref, menu) then
            local entry = button.Entry
            local pings = entry and entry.GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD
            if pings and pings:GetArrayNum() == 1 and pings[1]:ToString() == ping then
                button:BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature()
                return "selected the original Sewer Thing trade response"
            end
        end
    end
    error("native_dialogue_sewer_fixture_trade_response", 0)
end

return profile
