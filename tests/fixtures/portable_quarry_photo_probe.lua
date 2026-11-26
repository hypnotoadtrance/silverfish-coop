local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-quarry-photo-"
local quarry = "/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P.Quarry_01_P"
local southern_road = "/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P.SouthEast_Road_P"
local quarry_quest, tower_quest = "Photo_BrnzQuarry", "Photo_SB_Tower"
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
        or southern_road .. ":PersistentLevel.StaticMeshActor_3458")
    local target = valid(actor) and actor:GetComponentByClass(
        StaticFindObject("/Game/Classes/Actors/ActorAssets/CameraTarget_BP.CameraTarget_BP_C"))
    assert(valid(target) and same(actor:GetWorld(), world)
        and target.TrackedQuest.RowName:ToString() == (in_quarry and quarry_quest or tower_quest))
    return target
end

local function point_for(target)
    if target.TrackedQuest.RowName:ToString() == quarry_quest then
        -- The camera view is sampled independently from the guest. The host owns the binding.
        return target:GetOwner().StaticMeshComponent:K2_GetComponentLocation()
    end
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
    local quest = world:GetFullName() == "World " .. quarry and quarry_quest or tower_quest
    local save, key = pawn.Save, FName(quest, EFindName.FNAME_Find)
    if action == "binding" then
        local target = sight(world)
        local primitive = target.Targets:GetArrayNum() == 1 and target.Targets[1]
        if not primitive or not valid(primitive) then
            local actor = target:GetOwner()
            local mesh = actor.StaticMeshComponent
            return ("unbound target=%s slots=%d weight=%.2f mesh=%s material=%s"):format(target:GetFullName(),
                target.Targets:GetArrayNum(), target.SumOfWeights, mesh.StaticMesh:GetFullName(), mesh:GetMaterial(0):GetFullName())
        end
        if quest == quarry_quest then assert(same(primitive, target:GetOwner().StaticMeshComponent)) end
        return "bound"
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
            for _, id in ipairs({tower_quest, quarry_quest}) do
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
        local function standing_point(x, y, height)
            local ground = {}
            if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=center.Z+600}, {X=x,Y=y,Z=center.Z-4000}, 0, false,
                {pawn,guest}, 0, ground, true, color, color, 0) then
                local floor = (ground.OutHit or ground).ImpactPoint
                return {X=x,Y=y,Z=floor.Z+height+4}
            end
        end
        local guest_height = guest.CapsuleComponent:GetScaledCapsuleHalfHeight()
        local host_height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
        local floors, visible = 0, 0
        for _, distance in ipairs({800, 1300, 2000, 2800, 4200}) do
            for index = 0, 15 do
                local angle = math.pi * index / 8
                local cosine, sine = math.cos(angle), math.sin(angle)
                local x, y = center.X + distance * cosine, center.Y + distance * sine
                local point = standing_point(x, y, guest_height)
                if point then
                    floors = floors + 1
                    local eye = {X=x,Y=y,Z=point.Z+60}
                    local blocked = trace:LineTraceSingle(pawn, eye, center, 0, true, {pawn,guest},
                        0, {}, true, color, color, 0)
                    if not blocked then
                        visible = visible + 1
                        if guest:K2_TeleportTo(point, {Pitch=0,Yaw=0,Roll=0}) then
                            for _, offset in ipairs({{-210*sine, 210*cosine}, {210*sine, -210*cosine}, {240*cosine, 240*sine}}) do
                                local observer = standing_point(x + offset[1], y + offset[2], host_height)
                                if observer and pawn:K2_TeleportTo(observer, {Pitch=0,Yaw=0,Roll=0}) then
                                    pawn:ForceNetUpdate()
                                    guest:ForceNetUpdate()
                                    return ("%.3f,%.3f,%.3f"):format(point.X,point.Y,point.Z)
                                end
                            end
                        end
                    end
                end
            end
        end
        error(("No clear position at %.1f,%.1f,%.1f, floors=%d, visible=%d"):format(center.X, center.Y, center.Z, floors, visible))
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
    pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
    camera.SceneCaptureComponent2D:K2_SetWorldRotation(rotation, false, {}, true)
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
