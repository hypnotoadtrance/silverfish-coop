local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box
    or not box:find("drop%-host%-menu%-split_drag_to_floor%-bandage%-%x+") then return end

local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_count = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local previous, seeded
local moving

local function valid(value) return value and value:IsValid() end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function guid(row)
    local id = row[item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function grip(pawn, identity)
    assert(not moving and identity:match("^[a-f0-9]+$") and #identity == 32)
    for slot = 1, 64 do
        local shown, _, id, name = SilverfishReadNativeGuestDropProjection(slot)
        if shown and id == identity then
            local actor = StaticFindObject(name:match("^%S+ (.+)$"))
            assert(valid(actor) and actor:GetFullName() == name and same(actor:GetWorld(), pawn:GetWorld())
                and same(actor.Box, actor.RootComponent) and not valid(pawn.HeldObject))
            local camera = pawn.Camera
            local angle = camera.RelativeRotation
            local old_angle = {Pitch = angle.Pitch, Yaw = angle.Yaw, Roll = angle.Roll}
            local old_actor, old_distance = pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance
            local old_part = pawn.InteractionTarget_Component
            local at, eye = actor:K2_GetActorLocation(), camera:K2_GetComponentLocation()
            local dx, dy, dz = at.X - eye.X, at.Y - eye.Y, at.Z - eye.Z
            local horizontal = math.sqrt(dx * dx + dy * dy)
            local distance = math.sqrt(horizontal * horizontal + dz * dz)
            assert(distance < pawn.InteractionRange, ("fixture distance %.2f"):format(distance))
            local grabbed, error_detail = pcall(function()
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(dz, horizontal)),
                    Yaw = math.deg(math.atan(dy, dx)), Roll = 0}, false, {}, true)
                pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, distance
                pawn.InteractionTarget_Component = actor.Box
                pawn:Grab_Release_Object(false, false, true)
                assert(same(pawn.HeldObject, actor) and same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box),
                    "stock grip did not attach to the shared item")
                assert(actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)),
                    "stock carry input did not activate the projected item")
            end)
            camera:K2_SetRelativeRotation(old_angle, false, {}, true)
            pawn.ActorBeingLookedAt = valid(old_actor) and old_actor or nil
            pawn.ActorLookedAt_Distance = old_distance
            pawn.InteractionTarget_Component = valid(old_part) and old_part or nil
            if not grabbed then
                if same(pawn.HeldObject, actor) then pawn:Grab_Release_Object(false, true, false) end
                error(error_detail)
            end
            moving = {pawn = pawn, actor = actor}
            return tostring(actor:GetAddress())
        end
    end
    error("shared stock drop identity not found")
end

local function release(pawn, throwing)
    assert(moving and same(moving.pawn, pawn) and valid(moving.actor))
    local actor = moving.actor
    assert(same(pawn.HeldObject, actor) and actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)))
    local position = actor:K2_GetActorLocation()
    pawn:Grab_Release_Object(throwing, not throwing, false)
    assert(not same(pawn.HeldObject, actor) and not same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box),
        "stock drop did not release the shared item")
    moving = nil
    return ("%.4f,%.4f,%.4f"):format(position.X, position.Y, position.Z)
end

local function witness(world, pawn, mode, value)
    if mode == "grip" then return grip(pawn, value) end
    if mode == "drop" or mode == "throw" then return release(pawn, mode == "throw") end
    if mode == "pose" then
        local actor = StaticFindObject(value)
        assert(valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed())
        local position = actor:K2_GetActorLocation()
        return ("%d,%.4f,%.4f,%.4f"):format(actor:GetAddress(), position.X, position.Y, position.Z)
    end
    if mode == "actor" then
        local prefix = area .. ":PersistentLevel.Item_Bandage_C_"
        assert(value:sub(1, #prefix) == prefix and value:sub(#prefix + 1):match("^%d+$"))
        local actor = StaticFindObject(value)
        if not valid(actor) or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return "gone" end
        -- A retired UObject can still be returned by name until garbage
        -- collection. Query the level's live actors to distinguish that case.
        local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local found = {}
        api:GetAllActorsOfClass(world, actor:GetClass(), found)
        for _, live_actor in ipairs(found.OutActors or found) do
            if type(live_actor.type) == "function" and live_actor:type() == "RemoteUnrealParam" then
                live_actor = live_actor:get()
            end
            if same(live_actor, actor) then
                return actor:GetActorEnableCollision() and not actor.bHidden and "present" or "hidden"
            end
        end
        return "gone"
    end
    assert(mode == "seed" and not seeded and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
        and not valid(world.NetDriver.ServerConnection) and pawn:HasAuthority())
    local instance = world.OwningGameInstance
    local save = pawn.Save
    assert(valid(instance) and same(save, instance.SaveSlot_Ref))
    local class = StaticFindObject(bandage_path)
    if not valid(class) then LoadAsset(bandage_path); class = StaticFindObject(bandage_path) end
    local factory = StaticFindObject(factory_path)
    assert(valid(factory) and valid(class) and class:GetCDO().Stacks == true)
    local generated = {}
    factory:MakeItemStack(class, 9, pawn, generated)
    local item = generated.Item or generated
    local identity = guid(item)
    assert(same(item[item_class], class) and item[item_count] == 9 and identity:find("[^0]"))
    local result = {}
    instance:AddItem(item, 0, -1, result, result, result)
    for index = 1, save.PlayerBackpack:GetArrayNum() do
        local row = save.PlayerBackpack[index]
        if guid(row) == identity then
            assert(same(row[item_class], class) and row[item_count] == 9)
            seeded = true
            return identity
        end
    end
    error("stock Bandage was not added to the isolated host backpack")
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. area or not valid(world.NetDriver) then return end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    local input = io.open(box .. "/bandage-split-probe", "rb")
    if not input then return end
    local request = input:read("*l")
    input:close()
    if not request or request == previous then return end
    previous = request
    local mode, value = request:match("^(%a+)|([^|]*)")
    local ok, result = pcall(witness, world, pawn, mode, value)
    local output = io.open(box .. "/bandage-split-reply", "wb")
    if output then output:write(ok and "ok|" or "error|", tostring(result)); output:close() end
end)
