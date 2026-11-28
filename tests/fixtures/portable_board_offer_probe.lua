local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = bridge .. "/portable-board-offer-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local dry_dock = "/Game/Classes/Maps/Maps_Dungeons/DryDock_P.DryDock_P"
local board_path = map .. ":PersistentLevel.BountyBoard_BP_C_2"
local menu_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local descriptions = "Descriptions_ByStep_20_CE78450B4708786A5D7D71BFE2D3DE0B"
local items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local objective_items = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local consumes_items = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333"
local chained = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9"
local track = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774"
local ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F"
local photo_key = "Photo_BrnzQuarry"
local dynamic_key, local_only_key = "CoopProbe.Supply-Run", "CoopProbe.Local-Only"
local custom_followup, custom_flag = "CoopProbe.Second-Run", "CoopProbe.Supply-Delivered"
local completion_flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F"
local oversized_key, unsupported_key = "CoopProbe.Large-Journal-Entry", "CoopProbe.Reserved:Name"
local reward_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_units = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local tracked = {
    Sab_Fuel_01 = {flag = "Kill_ForestTank", threshold = 7, progress = 6},
    Sab_Factory = {flag = "PB_Factory_Kill", threshold = 5, progress = 4},
}
local followup_count, followup_hook = 0, false
local last
local streamed

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function distance(a, b) return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 end
local function text(value) return type(value) == "userdata" and value:ToString() or value end
local function set_name(value) value = text(value); return value ~= "" and value ~= "None" end

local function stock_menu(pawn, board)
    local stack = pawn.Controller.MenuStack
    local count = stack:GetArrayNum()
    local panel = count > 0 and stack[count]
    if panel and type(panel.type) == "function" and panel:type() == "RemoteUnrealParam" then panel = panel:get() end
    assert(valid(panel) and same(panel:GetClass(), StaticFindObject(menu_class)) and same(panel.BountyBoard_Ref, board),
        "The requested stock Observatory board is not open")
    return panel
end

local function approach(pawn, other, board)
    local wall = board.Box:K2_GetComponentLocation()
    local origin = board:K2_GetActorLocation()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local color = {R=0,G=0,B=0,A=0}
    for _, offset in ipairs({{0,-125},{-125,0},{0,125},{125,0}}) do
        local x,y = wall.X+offset[1],wall.Y+offset[2]
        local result = {}
        if trace:LineTraceSingle(pawn,{X=x,Y=y,Z=wall.Z+250},{X=x,Y=y,Z=wall.Z-380},0,false,
            {pawn,other,board},0,result,true,color,color,0) then
            local hit = result.OutHit or result
            local point = {X=x,Y=y,Z=hit.ImpactPoint.Z+height+2}
            if hit.ImpactNormal.Z >= .7 and distance(point, wall) <= pawn.InteractionRange^2
                and distance(point, origin) <= pawn.InteractionRange^2
                and pawn:K2_TeleportTo(point, pawn:K2_GetActorRotation()) then
                pawn:ForceNetUpdate()
                local at = pawn:K2_GetActorLocation()
                return ("%.4f,%.4f,%.4f"):format(at.X,at.Y,at.Z)
            end
        end
    end
    error("No reachable stock floor near the physical Observatory board")
end

local function run(action, quest)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world), "The stock world is unavailable")
    local world_path = world:GetFullName():match("^World (.+)$")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    if action == "clear-dry-dock-npcs" then
        assert(world_path == dry_dock and valid(pawn) and pawn:IsLocallyControlled() and pawn:HasAuthority())
        local class = StaticFindObject("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C")
        local actors = {}
        statics:GetAllActorsOfClass(world, class, actors)
        local count = 0
        for _, parameter in ipairs(actors.OutActors or actors) do
            local actor = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
                and parameter:get() or parameter
            if valid(actor) and actor.Alive and actor:HasAuthority() and same(actor:GetWorld(), world) then
                -- This disposable campaign measures the sabotage prop, while the
                -- separate combat trial exercises stock attacks and retaliation.
                actor:K2_DestroyActor()
                count = count + 1
            end
        end
        -- Destroyed actors can remain valid Lua handles until garbage collection.
        -- Read back the level roster to verify that no living stock actor remains.
        local remaining = {}
        statics:GetAllActorsOfClass(world, class, remaining)
        for _, parameter in ipairs(remaining.OutActors or remaining) do
            local actor = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
                and parameter:get() or parameter
            assert(not valid(actor) or not actor.Alive or not same(actor:GetWorld(), world),
                "A stock NPC remained in the firing test level")
        end
        return tostring(count)
    end
    assert(world_path == map, "The fixture requires the stock Observatory")
    local board = StaticFindObject(board_path)
    assert(valid(pawn) and pawn:IsLocallyControlled() and valid(board) and same(board:GetWorld(), world)
        and same(board.Box:GetOwner(), board), "The local stock board is unavailable")
    if action == "stream-load" then
        local loading = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
        local level = loading:LoadLevelInstance(world, "/Game/Classes/Maps/Maps_OverWorld/Observitory_P",
            {X = 65000, Y = 65000, Z = 2500}, {Pitch = 0, Yaw = 0, Roll = 0}, {}, "CoopBoardSublevel", nil, false)
        assert(valid(level), "Could not stream the stock board fixture")
        streamed = {world = world, level = level}
        return "loading"
    elseif action == "stream-hide" or action == "stream-show" or action == "stream-unload" then
        assert(streamed and same(streamed.world, world) and valid(streamed.level))
        streamed.level:SetShouldBeVisible(action == "stream-show")
        if action == "stream-unload" then streamed.level:SetShouldBeLoaded(false) end
        return action
    elseif action == "stream-board" then
        assert(streamed and same(streamed.world, world) and valid(streamed.level))
        if not streamed.level:IsLevelVisible() then return "waiting" end
        local level = streamed.level:GetLoadedLevel()
        local path = level:GetFullName():match("^%S+ (.+)$") .. ".BountyBoard_BP_C_2"
        local selected = StaticFindObject(path)
        assert(valid(selected) and same(selected:GetOuter(), level), "The sublevel is missing the stock board")
        local stock = selected["As Game Instance FP"]
        if not same(stock, world.OwningGameInstance) then return "initializing" end
        assert(selected:K2_SetActorLocationAndRotation(board:K2_GetActorLocation(), board:K2_GetActorRotation(),
            false, {}, true), "Could not stage the loaded board near the guest")
        streamed.board = selected
        return "stream:" .. selected:GetFullName()
    elseif action == "stream-visible" then
        return tostring(valid(streamed.level) and streamed.level:IsLevelVisible())
    elseif action == "stream-stock-open" then
        assert(streamed and same(streamed.world, world) and valid(streamed.board)
            and valid(streamed.level) and streamed.level:IsLevelVisible()
            and pawn:HasAuthority() == false and pawn.Controller.MenuStack:GetArrayNum() == 0,
            "The streamed board and guest menu were not ready")
        streamed.board:Send_PowerSignal(true, pawn)
        streamed.stock_menu = stock_menu(pawn, streamed.board)
        assert(streamed.stock_menu.BountyBoard == true, "Stock power did not open a bounty board")
        return "stock power opened board"
    elseif action == "stream-stock-adopted" then
        assert(streamed and valid(streamed.board) and same(stock_menu(pawn, streamed.board), streamed.stock_menu),
            "Host options did not retain the original stock board widget")
        return "original stock widget retained"
    elseif action == "stream-modded-offers" then
        assert(streamed and same(streamed.world, world) and valid(streamed.board))
        local quest_table = StaticFindObject("/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT")
        local seed = assert(quest_table:FindRow(photo_key))
        local dynamic, local_only, followup = FName(dynamic_key), FName(local_only_key), FName(custom_followup)
        local reward = StaticFindObject(reward_path)
        if not valid(reward) then LoadAsset(reward_path); reward = StaticFindObject(reward_path) end
        assert(valid(reward))
        assert(not quest_table:FindRow(dynamic_key) and not quest_table:FindRow(local_only_key)
            and not quest_table:FindRow(custom_followup))
        if pawn:HasAuthority() then
            local active = pawn.Save.ActiveQuests
            for index = 1, 132 do
                local completed = FName(("CoopProbe.Archived-%03d"):format(index))
                active:Add(completed, seed)
                active:Find(completed):get()[step] = -10
            end
            active:Add(followup, seed)
            active:Find(followup):get()[step] = 2
            local oversized = FName(oversized_key)
            active:Add(oversized, seed)
            local lines = active:Find(oversized):get()[descriptions]
            for index = 1, 3 do lines[index] = FText(("x"):rep(4090)) end
            active:Add(FName(unsupported_key), seed)
            assert(active:Contains(FName(unsupported_key)) and #lines[3]:ToString() == 4090)
        end
        RegisterHook("/Game/Classes/Actors/BountyBoard_BP.BountyBoard_BP_C:Configure_Stock", function(raw)
            local actor = raw:get()
            if not streamed or not same(actor, streamed.board) then return end
            local save = world.OwningGameInstance.SaveSlot_Ref
            local contracts = actor.Contracts
            if not save.ActiveQuests:Contains(dynamic) and not contracts:Contains(dynamic) then
                contracts:Add(dynamic, seed)
                local row = contracts:Find(dynamic):get()
                row[cash] = 137
                row[items][1] = reward
                row[chained][1] = followup
                row[completion_flags]:Add(FName(custom_flag), 1)
            end
            if not pawn:HasAuthority() and not contracts:Contains(local_only) then
                contracts:Add(local_only, seed)
            end
        end)
        return "off-table offers armed"
    elseif action == "active-quest-count" then
        assert(pawn:HasAuthority())
        return tostring(#pawn.Save.ActiveQuests)
    elseif action == "complete-modded-objective" then
        local save = world.OwningGameInstance.SaveSlot_Ref
        assert(pawn:HasAuthority() and same(pawn.Save, save))
        local row = save.ActiveQuests:Find(FName(dynamic_key, EFindName.FNAME_Find)):get()
        assert(row[step] == 1 and row[cash] == 137)
        row[step] = row[final]
        return tostring(row[step])
    elseif action == "modded-completion-flag" then
        local key = FName(custom_flag, EFindName.FNAME_Find)
        local flags = world.OwningGameInstance.SaveSlot_Ref.Flags
        return flags:Contains(key) and tostring(flags:Find(key):get()) or "-"
    elseif action == "reward-units" then
        assert(valid(pawn.Save) and same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref))
        local pack, total = pawn.Save.PlayerBackpack, 0
        for index = 1, pack:GetArrayNum() do
            local row = pack[index]
            local class = row[item_class]
            if valid(class) and class:GetFullName() == "BlueprintGeneratedClass " .. reward_path then
                total = total + row[item_units]
            end
        end
        return tostring(total)
    end
    if action == "approach" then
        local connections = world.NetDriver.ClientConnections
        assert(pawn:HasAuthority() and connections:GetArrayNum() == 1)
        local guest = connections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled() and valid(guest.Save)
            and not same(guest.Save, pawn.Save), "An enrolled stock guest is required")
        return approach(guest, pawn, board)
    end
    if action == "qualify-tracked" or action == "track-progress" then
        local instance = world.OwningGameInstance
        assert(pawn:HasAuthority() and same(pawn.Save, instance.SaveSlot_Ref))
        if action == "qualify-tracked" then
            instance:Add_PointTo_SaveFlag(FName("Player_Xp"), 10, true, true)
            return "10"
        end
        local expected = assert(tracked[quest], "Unknown tracked stock contract")
        local found
        pawn.Save.ActiveQuests:ForEach(function(key, row)
            if key:get():ToString() == quest then found = row:get() end
        end)
        assert(found and found[step] == 1 and found[final] == expected.threshold
            and text(found[track]) == expected.flag, "The unrefreshed stock quest is unavailable")
        local flag = found[track]
        instance:Add_PointTo_SaveFlag(flag, expected.progress, true, true)
        assert(pawn.Save.Flags:Contains(flag) and pawn.Save.Flags:Find(flag):get() == expected.progress)
        return tostring(expected.progress)
    end
    if action == "stage-turnin-followup" then
        local instance = world.OwningGameInstance
        assert(pawn:HasAuthority() and same(pawn.Save, instance.SaveSlot_Ref))
        assert(quest == "Asn_Panzer_01")
        local key = FName(quest, EFindName.FNAME_Find)
        local active = pawn.Save.ActiveQuests
        assert(not active:Contains(key), "This stock contract must start absent")
        instance:AddQuest_ByName(key)
        local row = active:Find(key):get()
        assert(row and row[step] == 1 and row[final] >= 1 and row[objective_items]:GetArrayNum() == 0
            and row[consumes_items] == false and text(row[ping]) == "Coup_HuntPlayer")
        row[step] = row[final]
        assert(active:Find(key):get()[step] == row[final], "Could not stage the completed objective")
        if not followup_hook then
            RegisterHook("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.FL_CodeLib01_C:GlobalPing",
                function(_, key_param)
                    if text(key_param:get()) == "Coup_HuntPlayer" then followup_count = followup_count + 1 end
                end)
            followup_hook = true
        end
        return tostring(row[final])
    end
    if action == "add-photo-contract" or action == "complete-photo-objective" or action == "photo-consumption-field" then
        local instance = world.OwningGameInstance
        assert(pawn:HasAuthority() and same(pawn.Save, instance.SaveSlot_Ref))
        local save, key = pawn.Save, FName(photo_key, EFindName.FNAME_Find)
        if action == "add-photo-contract" then
            assert(not save.ActiveQuests:Contains(key))
            instance:AddQuest_ByName(key)
        end
        local row = save.ActiveQuests:Find(key):get()
        assert(row and row[objective_items]:GetArrayNum() == 0 and not set_name(row[track]))
        if action == "photo-consumption-field" then return tostring(row[consumes_items]) end
        assert(row[step] == 1 and row[consumes_items] == true)
        if action == "complete-photo-objective" then
            row[step] = row[final]
            assert(save.ActiveQuests:Find(key):get()[step] == row[final])
        end
        return tostring(row[step])
    end
    if action == "turnin-followups" then
        assert(pawn:HasAuthority())
        return tostring(followup_count)
    end
    if action == "catalog" then
        assert(pawn:HasAuthority(), "Only the host can read the stock catalog")
        board:Configure_Stock()
        local offered, extended = {}, {}
        board.Contracts:ForEach(function(raw_key, raw_row)
            local id, row = raw_key:get():ToString(), raw_row:get()
            if type(row[step]) == "number" and row[step] >= 1 and row[step] <= row[final] then
                offered[#offered+1] = id
                if row[items]:GetArrayNum() > 0 or row[chained]:GetArrayNum() > 0
                    or set_name(row[track]) or set_name(row[ping]) then
                    extended[#extended+1] = id
                end
            end
        end)
        table.sort(offered)
        table.sort(extended)
        return table.concat(offered,",") .. ";" .. table.concat(extended,",")
    end
    if action == "menu" or action == "stream-menu" then
        local expected = action == "stream-menu" and streamed.board or board
        local list = stock_menu(pawn, expected).VerticalBox_ContractsList
        local visible = {}
        for index=0,list:GetChildrenCount()-1 do
            visible[#visible+1] = list:GetChildAt(index).Quest_IDTag:ToString()
        end
        table.sort(visible)
        return table.concat(visible,",")
    end
    assert(action == "journal" and quest and quest:match("^[A-Za-z0-9_.:%-]+$"), "Unknown stock board operation")
    local result = "-"
    assert(valid(pawn.Save) and same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref), "The local stock journal is not bound")
    pawn.Save.ActiveQuests:ForEach(function(key, row)
        if key:get():ToString() == quest then result = tostring(row:get()[step]) end
    end)
    return result
end

LoopInGameThreadWithDelay(50, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(180)
    file:close()
    local ticket, action, quest = (request or ""):match("^(%x+)\t([a-z%-]+)\t?([A-Za-z0-9_.:%-]*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, quest)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\t\r\n]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
