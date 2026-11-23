local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local level = area .. ".Observitory_P:PersistentLevel."
if not bridge or not bridge:find("simmi%-jobs%-%x+") or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
    or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") ~= "1"
    then return {sync = function() end} end

local profiles = require("native_jammer_profiles")
local simmi = profiles.by_tag["native-crossroads-jammer"].speaker
local response_class = "/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C"
local dialogue_class = "/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C"
local log_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local next_field = "NextConvo_8_59FA977C44DA6739371CD9992F385F4F"
local pings_field = "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local last, work, deadline, introduced, clicked_work

local function valid(o) return o and o:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(o, class) return valid(o) and same(o:GetClass(), StaticFindObject(class)) end
local function name(value) return type(value) == "userdata" and value:ToString() or value end
local function top(controller)
    local stack = controller.MenuStack
    local n = stack:GetArrayNum()
    return n > 0 and stack[n] or nil
end
local function square(a, b)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2
end

local function approach(state, phase, spot)
    assert(phase == "hosting" and state.server == false, "The host must place stock authority pawns")
    local world, host = state.world, state.local_pawn.pawn
    local connections = world.NetDriver.ClientConnections
    assert(connections:GetArrayNum() == 1 and host:HasAuthority(), "Expected exactly one enrolled guest")
    local guest = connections[1].PlayerController.Pawn
    local exact_guest = require("guest_save").exact_item_context(state, phase, guest:GetFullName())
    assert(same(exact_guest, guest), "The guest has no bound campaign save")
    local pawn = spot == "host" and host or guest
    local actor = spot == "board" and StaticFindObject(level .. "BountyBoard_BP_C_2") or StaticFindObject(simmi.path)
    assert(valid(actor) and actor:HasAuthority() and same(actor:GetWorld(), world), "Stock approach target absent")
    local origin = actor:K2_GetActorLocation()
    local center = spot == "board" and actor.Box:K2_GetComponentLocation() or origin
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local capsule, reach = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight(), pawn.InteractionRange
    local color, ignore = {R = 0, G = 0, B = 0, A = 0}, {host, guest, actor}
    local offsets = spot == "host" and {{0, 125}, {125, 0}, {0, -125}, {-125, 0}} or
        {{0, -125}, {-125, 0}, {0, 125}, {125, 0}}
    for _, offset in ipairs(offsets) do
        local x, y = center.X + offset[1], center.Y + offset[2]
        local out = {}
        local found = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = center.Z + 250},
            {X = x, Y = y, Z = center.Z - 380}, 0, false, ignore, 0, out, true, color, color, 0)
        local hit = out.OutHit or out
        if found == true and hit.ImpactNormal.Z >= 0.7 then
            local target = {X = x, Y = y, Z = hit.ImpactPoint.Z + capsule + 2}
            if square(target, center) <= reach^2 and square(target, origin) <= reach^2
                and pawn:K2_TeleportTo(target, pawn:K2_GetActorRotation()) then
                local actual = pawn:K2_GetActorLocation()
                if square(actual, target) <= 625 then
                    pawn:ForceNetUpdate()
                    return ("%.2f,%.2f,%.2f"):format(actual.X, actual.Y, actual.Z)
                end
            end
        end
    end
    error("No reachable stock floor near " .. spot, 0)
end

local function start_conversation(state)
    local pawn, controller = state.local_pawn.pawn, state.controller
    local actor = StaticFindObject(simmi.path)
    assert(exact(actor, simmi.class) and same(actor:GetWorld(), state.world) and actor.Alive == true)
    local sphere = actor.Sphere_Interact
    assert(valid(sphere) and same(sphere:GetOwner(), actor) and not top(controller))
    assert(square(pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()) <= pawn.InteractionRange^2)
    -- Enter the real stock F callback on Simmi. The dialogue response fires the stock board's GlobalPing.
    actor:SendInteractionToActor(pawn, sphere)
end

local function advance_conversation(controller)
    local menu = top(controller)
    if exact(menu, log_class) and menu.BountyBoard == true
        and same(menu.BountyBoard_Ref, StaticFindObject(level .. "BountyBoard_BP_C_0")) then
        return tostring(menu:GetAddress())
    end
    if not exact(menu, dialogue_class) then return end
    local list = menu.VerticalBox_PlayerResponces
    local introduction
    for i = 0, math.min(list:GetChildrenCount(), 8) - 1 do
        local entry = list:GetChildAt(i)
        if exact(entry, response_class) and same(entry.ParentWidget_Ref, menu) then
            local pings, next_row = entry.Entry[pings_field], entry.Entry[next_field].RowName
            if not clicked_work and pings:GetArrayNum() == 1 and name(pings[1]) == "Simmi_Contracts" then
                clicked_work = true
                entry:BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature()
                return
            elseif not introduced and pings:GetArrayNum() == 0 and name(next_row) == "Simmi_Root" then
                introduction = entry
            end
        end
    end
    if introduction then
        introduced = true
        introduction:BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature()
    end
end

local function read_menu(controller)
    local menu = top(controller)
    assert(exact(menu, log_class) and menu.BountyBoard == true and valid(menu.BountyBoard_Ref), "Expected stock board menu")
    local list = menu.VerticalBox_ContractsList
    local count, ids = list:GetChildrenCount(), {}
    assert(count >= 0 and count <= 128)
    for i = 0, count - 1 do
        local row = list:GetChildAt(i)
        assert(same(row.QuestLog_MenuRef, menu))
        ids[#ids + 1] = name(row.Quest_IDTag)
    end
    table.sort(ids)
    return ("%s;%s;%s"):format(menu.BountyBoard_Ref:GetFullName(), menu:GetAddress(), table.concat(ids, ","))
end

local function journal(state)
    local save = state.local_pawn.pawn.Save
    assert(valid(save) and same(save, state.world.OwningGameInstance.SaveSlot_Ref))
    local wire = require("native_wire_quest")
    local values = {}
    for _, quest in ipairs({"Jmr_CrossRoads", "Jmr_Lake"}) do
        local row = wire.stock_row(save.ActiveQuests, FName(quest, EFindName.FNAME_Find))
        values[#values + 1] = row and tostring(row[step_field]) or "-"
    end
    return table.concat(values, ",")
end

local function sync(state, phase)
    if state.map ~= area or (phase ~= "hosting" and phase ~= "connected") or not state.local_pawn then return end
    local file = io.open(bridge .. "/simmi-jobs-probe", "rb")
    if not file then return end
    local request = file:read("*l")
    file:close()
    if not request or request == last then return end
    local operation, token = request:match("^(%a+)|([0-9a-f]+)$")
    if not token or #token ~= 32 then return end
    local ok, result = pcall(function()
        if operation == "host" or operation == "guest" or operation == "board" then return approach(state, phase, operation) end
        if operation == "read" then return read_menu(state.controller) end
        if operation == "close" then
            local board = StaticFindObject(level .. "BountyBoard_BP_C_2")
            local menu = top(state.controller)
            assert(exact(menu, log_class) and same(menu.BountyBoard_Ref, board))
            state.controller:CloseMenu({})
            assert(not same(top(state.controller), menu))
            return "closed"
        end
        if operation == "journal" then return journal(state) end
        assert(operation == "talk", "Unsupported Simmi fixture operation")
        if work ~= token then
            start_conversation(state)
            work, deadline, introduced, clicked_work = token, os.time() + 16, false, false
        end
        assert(os.time() < deadline, "Simmi's stock work response did not open the board")
        return advance_conversation(state.controller)
    end)
    if not ok or result then
        last = request
        local reply = assert(io.open(bridge .. "/simmi-jobs-reply", "wb"))
        reply:write(token, "|", ok and "ok|" or "error|", tostring(result))
        reply:close()
    end
end

return {sync = sync}

