local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = bridge .. "/portable-board-offer-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local board_path = map .. ":PersistentLevel.BountyBoard_BP_C_2"
local menu_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local chained = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9"
local track = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774"
local ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F"
local tracked = {
    Sab_Fuel_01 = {flag = "Kill_ForestTank", threshold = 7, progress = 8},
    Sab_Factory = {flag = "PB_Factory_Kill", threshold = 5, progress = 5},
}
local last

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function distance(a, b) return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 end
local function text(value) return type(value) == "userdata" and value:ToString() or value end
local function set_name(value) value = text(value); return value ~= "" and value ~= "None" end

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
    assert(valid(world) and world:GetFullName() == "World " .. map, "The fixture requires the stock Observatory")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    local board = StaticFindObject(board_path)
    assert(valid(pawn) and pawn:IsLocallyControlled() and valid(board) and same(board:GetWorld(), world)
        and same(board.Box:GetOwner(), board), "The local stock board is unavailable")
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
    if action == "menu" then
        local stack = pawn.Controller.MenuStack
        local panel = stack[stack:GetArrayNum()]
        if panel and type(panel.type) == "function" and panel:type() == "RemoteUnrealParam" then panel = panel:get() end
        assert(valid(panel) and same(panel:GetClass(), StaticFindObject(menu_class)) and same(panel.BountyBoard_Ref, board),
            "The stock Observatory board is not open")
        local list = panel.VerticalBox_ContractsList
        local visible = {}
        for index=0,list:GetChildrenCount()-1 do
            visible[#visible+1] = list:GetChildAt(index).Quest_IDTag:ToString()
        end
        table.sort(visible)
        return table.concat(visible,",")
    end
    assert(action == "journal" and quest and quest:match("^[A-Za-z0-9_]+$"), "Unknown stock board operation")
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
    local ticket, action, quest = (request or ""):match("^(%x+)\t([a-z%-]+)\t?([A-Za-z0-9_]*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, quest)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\t\r\n]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
