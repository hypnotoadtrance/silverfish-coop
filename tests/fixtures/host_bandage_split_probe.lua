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
        local shown, epoch, id, name = SilverfishReadNativeGuestDropProjection(slot)
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
            local facing = actor:K2_GetActorRotation()
            local origin = {X = at.X, Y = at.Y, Z = at.Z}
            local rotation = {Pitch = facing.Pitch, Yaw = facing.Yaw, Roll = facing.Roll}
            local dx, dy, dz = at.X - eye.X, at.Y - eye.Y, at.Z - eye.Z
            local horizontal = math.sqrt(dx * dx + dy * dy)
            local distance = math.sqrt(horizontal * horizontal + dz * dz)
            assert(distance < pawn.InteractionRange, ("fixture distance %.2f"):format(distance))
            local ok, detail = SilverfishSetNativeWorldLootPhysics(slot, epoch, id, true)
            assert(ok and detail == "stock_world_loot_physics_active"
                and actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)), detail)
            local grabbed, error_detail = pcall(function()
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(dz, horizontal)),
                    Yaw = math.deg(math.atan(dy, dx)), Roll = 0}, false, {}, true)
                pawn.ActorBeingLookedAt, pawn.ActorLookedAt_Distance = actor, distance
                pawn.InteractionTarget_Component = actor.Box
                pawn:Grab_Release_Object(false, false, true)
                assert(same(pawn.HeldObject, actor) and same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box),
                    "stock grip did not attach to the shared item")
            end)
            camera:K2_SetRelativeRotation(old_angle, false, {}, true)
            pawn.ActorBeingLookedAt = valid(old_actor) and old_actor or nil
            pawn.ActorLookedAt_Distance = old_distance
            pawn.InteractionTarget_Component = valid(old_part) and old_part or nil
            if not grabbed then
                if same(pawn.HeldObject, actor) then pawn:Grab_Release_Object(false, true, false) end
                SilverfishSetNativeWorldLootPhysics(slot, epoch, id, false)
                error(error_detail)
            end
            moving = {pawn = pawn, actor = actor, position = origin, rotation = rotation, slot = slot, epoch = epoch, id = id}
            return tostring(actor:GetAddress())
        end
    end
    error("shared stock drop identity not found")
end

local function release(pawn, mode)
    assert(moving and same(moving.pawn, pawn) and valid(moving.actor))
    local actor = moving.actor
    if mode == "drop" then
        assert(same(pawn.HeldObject, actor))
        local height = actor:K2_GetActorLocation().Z
        local simulating = actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find))
        pawn:Grab_Release_Object(false, true, false)
        local holding = same(pawn.HeldObject, actor)
        local attached = same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box)
        local released = actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find))
        assert(not holding and not attached and released,
            ("release held=%s attached=%s simulating=%s to %s collision=%s Physics=%s"):format(
                tostring(holding), tostring(attached), tostring(simulating), tostring(released),
                tostring(actor.Box:GetCollisionEnabled()), tostring(actor.Physics)))
        return ("%.3f"):format(height)
    elseif mode == "sample" then
        assert(actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)))
        return ("%.3f"):format(actor:K2_GetActorLocation().Z)
    end
    assert(mode == "reset" and not valid(pawn.HeldObject))
    assert(actor:K2_SetActorLocationAndRotation(moving.position, moving.rotation, false, {}, true))
    local ok, detail = SilverfishSetNativeWorldLootPhysics(moving.slot, moving.epoch, moving.id, false)
    assert(ok and not actor.Box:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)), detail)
    moving = nil
    return tostring(actor:GetAddress())
end

local function witness(world, pawn, mode, value)
    if mode == "grip" then return grip(pawn, value) end
    if mode == "drop" or mode == "sample" or mode == "reset" then return release(pawn, mode) end
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
