local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local level = area .. ".Observitory_P:PersistentLevel."
local portable = bridge and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
    and bridge:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
local legacy = bridge and bridge:find("simmi%-jobs%-%x+")
    and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" or os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not (portable or legacy) then return {sync = function() end} end

local simmi = {path = level .. "NPC_Simmi_C_1", class = "/Game/Classes/NPCs/NPC_Characters/NPC_Simmi.NPC_Simmi_C"}
local response_class = "/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C"
local dialogue_class = "/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C"
local log_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local details_class = "/Game/Classes/GUI/SubWidgets/Quest_DetailsWindow_Widget.Quest_DetailsWindow_Widget_C"
local turnin_class = "/Game/Classes/GUI/SubWidgets/Quest_TurnIn_Widget.Quest_TurnIn_Widget_C"
local pistol_class = "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local data_field = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
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
    local menu = n > 0 and stack[n]
    return menu and type(menu.type) == "function" and menu:type() == "RemoteUnrealParam" and menu:get() or menu
end
local function square(a, b)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2
end

local function detached_guest(state)
    local world, host = state.world, state.local_pawn.pawn
    local connections = world.NetDriver.ClientConnections
    assert(host:HasAuthority() and connections:GetArrayNum() == 1, "Expected exactly one enrolled guest")
    local guest = connections[1].PlayerController.Pawn
    assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled() and valid(guest.Save)
        and same(guest:GetWorld(), world) and not same(guest.Save, host.Save), "The guest has no detached campaign save")
    return guest
end

local function approach(state, phase, spot)
    assert(phase == "hosting" and state.server == false, "The host must place stock authority pawns")
    local world, host = state.world, state.local_pawn.pawn
    local connections = world.NetDriver.ClientConnections
    assert(connections:GetArrayNum() == 1 and host:HasAuthority(), "Expected exactly one enrolled guest")
    local guest = connections[1].PlayerController.Pawn
    if portable then guest = detached_guest(state)
    else
        local exact_guest = require("guest_save").exact_item_context(state, phase, guest:GetFullName())
        assert(same(exact_guest, guest), "The guest has no bound campaign save")
    end
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

local function read_menu(controller, completed)
    local menu = top(controller)
    assert(exact(menu, log_class) and menu.BountyBoard == true and valid(menu.BountyBoard_Ref), "Expected stock board menu")
    local list = completed and menu.VerticalBox_CompletedList or menu.VerticalBox_ContractsList
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
    local quests = {Jmr_CrossRoads = "-", Jmr_Lake = "-"}
    save.ActiveQuests:ForEach(function(key, row)
        local id = key:get():ToString()
        if quests[id] then quests[id] = tostring(row:get()[step_field]) end
    end)
    return quests.Jmr_CrossRoads .. "," .. quests.Jmr_Lake
end

local function accept_lake_quest(state)
    local id = "Jmr_Lake"
    local menu, board = top(state.controller), StaticFindObject(level .. "BountyBoard_BP_C_0")
    assert(exact(menu, log_class) and same(menu.BountyBoard_Ref, board), "The actual host Simmi jobs menu must be open")
    local quest = FName(id, EFindName.FNAME_Find)
    local offered
    board.Contracts:ForEach(function(key, row)
        if key:get():ToString() == id then offered = row:get() end
    end)
    assert(offered, "The host Simmi jobs menu did not offer the exact quest")
    menu:ShowDetails_Quest(offered, quest)
    local details = menu.VerticalBox_Details:GetChildAt(0)
    assert(exact(details, details_class) and same(details.QuestLog_MenuRef, menu) and name(details.Quest_ID) == id)
    details:BndEvt__Quest_DetailsWindow_Widget_Button_Clear_Accept_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    return "clicked"
end

local function turn_in_crossroads(state)
    local menu, board = top(state.controller), StaticFindObject(level .. "BountyBoard_BP_C_0")
    assert(exact(menu, log_class) and same(menu.BountyBoard_Ref, board), "Expected stock Simmi jobs menu")
    local list = menu.VerticalBox_CompletedList
    for index = 0, math.min(list:GetChildrenCount(), 128) - 1 do
        local row = list:GetChildAt(index)
        if exact(row, turnin_class) and name(row.Quest_IDTag) == "Jmr_CrossRoads" then
            assert(same(row.QuestLog_MenuRef, menu), "Crossroads redemption belongs to another menu")
            row:BndEvt__Quest_TurnIn_Widget_Button_K2Node_ComponentBoundEvent_2_OnButtonReleasedEvent__DelegateSignature()
            return "clicked"
        end
    end
    error("The host Simmi menu has no Crossroads redemption row", 0)
end

local function seed_pistol(state)
    local guest = detached_guest(state)
    local class = StaticFindObject(pistol_class)
    if not valid(class) then LoadAsset(pistol_class); class = StaticFindObject(pistol_class) end
    assert(valid(class))
    local output, defaults = {}, class:GetCDO()
    StaticFindObject(inventory):ItemActorToStruct(defaults, guest, output)
    local row = output.Item or output
    assert(same(row[item_class], class))
    row[ammo_field] = 7
    local pack = guest.Save.PlayerBackpack
    local index = pack:GetArrayNum() + 1
    pack[index] = row
    pack[index][data_field] = defaults.DataStruct
    local id = pack[index][item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function select_pistol(pawn)
    if exact(pawn.EquipedItem, pistol_class) then return "equipped" end
    local slots = pawn.Save.PlayerEquipment
    for index = 1, slots:GetArrayNum() do
        if same(slots[index][item_class], StaticFindObject(pistol_class)) then
            pawn:Equip_NextItem(index - 1, false)
            return exact(pawn.EquipedItem, pistol_class) and "equipped" or "waiting"
        end
    end
    return "waiting"
end

local function aim_jammer(state)
    local leaf = state.map:match("/(CrossRoads_P)$") or state.map:match("/(Lake_01_P)$")
    assert(leaf, "Scoped jammer map required")
    local actor = StaticFindObject(state.map .. "." .. leaf .. ":PersistentLevel.SignalJammer_BP_C_1")
    local pawn = state.local_pawn.pawn
    local weapon, camera = pawn.EquipedItem, pawn.Camera
    if not valid(actor) or not same(actor:GetWorld(), state.world) or not valid(actor.Capsule)
        or not exact(weapon, pistol_class) or not valid(camera) then return "waiting" end
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local eye, center = camera:K2_GetComponentLocation(), actor.Capsule:K2_GetComponentLocation()
    local color, ignored = {R=0, G=0, B=0, A=0}, {pawn, weapon}
    for _, height in ipairs({0, 8, -8, 16, -16, 25}) do
        local dx, dy, dz = center.X-eye.X, center.Y-eye.Y, center.Z+height-eye.Z
        local distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        if distance > 1 and distance < 2000 then
            local scale = (distance + 60) / distance
            local stop = {X=eye.X+dx*scale, Y=eye.Y+dy*scale, Z=eye.Z+dz*scale}
            local out = {}
            if trace:LineTraceSingle(pawn, eye, stop, 0, false, ignored, 0, out, true, color, color, 0) then
                local hit = out.OutHit or out
                local component = hit.Component and hit.Component:Get()
                if valid(component) and same(component:GetOwner(), actor) then
                    local rotation = {Pitch=math.deg(math.atan(dz, math.sqrt(dx*dx + dy*dy))),
                        Yaw=math.deg(math.atan(dy, dx)), Roll=0}
                    state.controller:SetControlRotation(rotation)
                    camera:K2_SetWorldRotation(rotation, false, {}, true)
                    return "the guest crosshair is on the jammer"
                end
            end
        end
    end
    return "waiting"
end

local function sync(state, phase)
    if (phase ~= "hosting" and phase ~= "connected") or not state.local_pawn then return end
    local file = io.open(bridge .. (portable and "/simmi-jobs-command.tsv" or "/simmi-jobs-probe"), "rb")
    if not file then return end
    local request = file:read("*l")
    file:close()
    if not request or request == last then return end
    local operation, token
    if portable then token, operation = request:match("^([0-9a-f]+)\t(%a+)$")
    else operation, token = request:match("^(%a+)|([0-9a-f]+)$") end
    if not token or #token ~= 32 then return end
    local ok, result = pcall(function()
        if operation == "journal" then return journal(state) end
        if portable and phase == "connected" and operation == "selectpistol" then
            return select_pistol(state.local_pawn.pawn)
        end
        if portable and phase == "connected" and operation == "aimjammer" then return aim_jammer(state) end
        assert(state.map == area, "Simmi's stock board is only available in Observatory")
        if portable and phase == "hosting" then
            if operation == "seedpistol" then return seed_pistol(state) end
            if operation == "acceptlake" then return accept_lake_quest(state) end
            if operation == "turnincrossroads" then return turn_in_crossroads(state) end
        end
        if operation == "host" or operation == "guest" or operation == "board" then return approach(state, phase, operation) end
        if operation == "read" then return read_menu(state.controller) end
        if operation == "ready" then return read_menu(state.controller, true) end
        if operation == "closesimmi" then
            local board = StaticFindObject(level .. "BountyBoard_BP_C_0")
            for _ = 1, 4 do
                local menu = top(state.controller)
                if not valid(menu) then return "closed" end
                local details = menu:GetClass():GetFullName():match("%.QuestDetails_Widget_C$")
                local parent = details and menu.QuestLog_MenuRef or menu
                assert(exact(menu, dialogue_class) or exact(parent, log_class) and same(parent.BountyBoard_Ref, board),
                    "Unexpected menu after Simmi acceptance: " .. menu:GetFullName())
                state.controller:CloseMenu({})
                assert(not same(top(state.controller), menu), "Stock CloseMenu left the same widget on top")
            end
            error("Simmi's stock menu stack did not close", 0)
        end
        if operation == "close" then
            local board = StaticFindObject(level .. "BountyBoard_BP_C_2")
            local menu = top(state.controller)
            assert(exact(menu, log_class) and same(menu.BountyBoard_Ref, board))
            state.controller:CloseMenu({})
            assert(not same(top(state.controller), menu))
            return "closed"
        end
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
        local reply = assert(io.open(bridge .. (portable and "/simmi-jobs-reply.tsv" or "/simmi-jobs-reply"), "wb"))
        local separator = portable and "\t" or "|"
        reply:write(token, separator, ok and "ok" or "error", separator, tostring(result))
        reply:close()
    end
end

if portable then
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    LoopInGameThreadWithDelay(50, function()
        local viewport = FindFirstOf("GameViewportClient")
        local world = valid(viewport) and viewport.World
        local driver = valid(world) and world.NetDriver
        if not valid(driver) or not same(driver.World, world) then return end
        local pawn = statics:GetPlayerPawn(world, 0)
        if not valid(pawn) or not pawn:IsLocallyControlled() or not same(pawn:GetWorld(), world) then return end
        local connected = valid(driver.ServerConnection) == true
        if pawn:HasAuthority() == connected then return end
        local map = world:GetFullName():match("^World (.+)%.")
        sync({map = map, world = world, server = connected, local_pawn = {pawn = pawn}, controller = pawn.Controller},
            connected and "connected" or "hosting")
    end)
end

return {sync = sync}
