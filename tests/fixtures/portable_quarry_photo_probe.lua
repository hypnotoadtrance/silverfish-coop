local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-quarry-photo-"
local quarry = "/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P.Quarry_01_P"
local stone_maze = "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"
local quarry_quest, stone_quest = "Photo_BrnzQuarry", "Photo_SM_Anom"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local seen

local function valid(object) return object and object:IsValid() end
local function same(a,b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function guid(row)
    local id = row[item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function sight(world)
    local in_quarry = world:GetFullName() == "World " .. quarry
    local actor = StaticFindObject(in_quarry and quarry .. ":PersistentLevel.StaticMeshActor_1147"
        or stone_maze .. ":PersistentLevel.SpinningBall_Anomaly_C_1")
    local target = valid(actor) and actor:GetComponentByClass(
        StaticFindObject("/Game/Classes/Actors/ActorAssets/CameraTarget_BP.CameraTarget_BP_C"))
    assert(valid(target) and same(actor:GetWorld(), world)
        and target.TrackedQuest.RowName:ToString() == (in_quarry and quarry_quest or stone_quest))
    return target
end

local function point_for(target)
    assert(target.Targets:GetArrayNum() == 1 and valid(target.Targets[1]), "stock photo primitive unavailable")
    return target.Targets[1]:K2_GetComponentLocation()
end

local function run(action, argument)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and valid(world.NetDriver))
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled())
    local quest = world:GetFullName() == "World " .. quarry and quarry_quest or stone_quest
    local save, key = pawn.Save, FName(quest, EFindName.FNAME_Find)
    if action == "binding" then
        local target = sight(world)
        return target.Targets:GetArrayNum() == 1 and valid(target.Targets[1]) and "bound" or "unbound"
    end
    if action == "journal" then
        return save.ActiveQuests:Contains(key) and tostring(save.ActiveQuests:Find(key):get()[step]) or "-"
    end
    if action == "equip" then
        assert(not pawn:HasAuthority())
        local slots = save.PlayerEquipment
        for index = 1, slots:GetArrayNum() do
            local row = slots[index]
            if valid(row[item_class]) and guid(row) == argument then
                pawn:Equip_NextItem(index - 1, false)
                assert(valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), row[item_class]))
                return "equipped"
            end
        end
        return "waiting"
    end
    if action == "seed" or action == "position" then
        local instance, connections = world.OwningGameInstance, world.NetDriver.ClientConnections
        assert(pawn:HasAuthority() and same(save, instance.SaveSlot_Ref) and connections:GetArrayNum() == 1)
        local guest = connections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled() and not same(guest.Save, save))
        if action == "seed" then
            assert(world:GetFullName():find("Observitory_P", 1, true))
            for _, id in ipairs({stone_quest, quarry_quest}) do
                local row = FName(id, EFindName.FNAME_Find)
                assert(not save.ActiveQuests:Contains(row))
                instance:AddQuest_ByName(row)
                assert(save.ActiveQuests:Find(row):get()[step] == 1)
            end
            if not valid(StaticFindObject(camera_path)) then LoadAsset(camera_path) end
            local class = StaticFindObject(camera_path)
            local inventory = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
            local result = {}
            inventory:MakeDefaultItem(class, guest, result)
            local row = result.Item or result
            assert(same(row[item_class], class))
            local items = guest.Save.PlayerBackpack
            items[items:GetArrayNum() + 1] = row
            return guid(row)
        end
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local center = point_for(sight(world))
        local color = {R=0,G=0,B=0,A=0}
        local height = guest.CapsuleComponent:GetScaledCapsuleHalfHeight()
        for _, distance in ipairs({800, 1300, 2000, 2800, 4200}) do
            for index = 0, 15 do
                local angle = math.pi * index / 8
                local x, y = center.X + distance * math.cos(angle), center.Y + distance * math.sin(angle)
                local ground, hit = {}, {}
                if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=center.Z+600}, {X=x,Y=y,Z=center.Z-4000}, 0, false,
                    {pawn,guest}, 0, ground, true, color, color, 0) then
                    local floor = (ground.OutHit or ground).ImpactPoint
                    local point = {X=x,Y=y,Z=floor.Z+height+4}
                    local eye = {X=x,Y=y,Z=point.Z+60}
                    local blocked = trace:LineTraceSingle(pawn, eye, center, 0, true, {pawn,guest},
                        0, hit, true, color, color, 0)
                    if not blocked and guest:K2_TeleportTo(point, {Pitch=0,Yaw=0,Roll=0}) then
                        guest:ForceNetUpdate()
                        return ("%.3f,%.3f,%.3f"):format(point.X,point.Y,point.Z)
                    end
                end
            end
        end
        error("A clear stock floor and line of sight to the photo target are required")
    end
    assert((action == "aim" or action == "miss") and not pawn:HasAuthority())
    local center = point_for(sight(world))
    local camera = pawn.EquipedItem
    assert(valid(camera) and valid(camera.SceneCaptureComponent2D))
    local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    local from = camera.SceneCaptureComponent2D:K2_GetComponentLocation()
    local aim = math_api:FindLookAtRotation(from, center)
    local rotation = {Pitch = aim.Pitch, Yaw = aim.Yaw, Roll = 0}
    if action == "miss" then rotation.Yaw = (rotation.Yaw + 180) % 360 end
    pawn.Controller:SetControlRotation(rotation)
    return action
end

LoopInGameThreadWithDelay(30, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(128)
    file:close()
    if not request or seen == request then return end
    seen = request
    local ticket, action, arg = request:match("^(%x+)\t([a-z]+)\t?(%x*)$")
    if not ticket or #ticket ~= 32 then return end
    local ok, result = pcall(run, action, arg)
    local reply = io.open(prefix .. "reply.tsv", "wb")
    if reply then
        local message = tostring(result):gsub("[\r\n\t]", " ")
        reply:write(ticket, ok and "\tok\t" or "\terror\t", message)
        reply:close()
    end
end)
