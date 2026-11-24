-- Accelerate the stock delay on one private host patch and observe its real grow graph.
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not bridge then return end
local standalone = os.getenv("SILVERFISH_COOP_MUSHROOM_REGROWTH_PROBE") == "1"
    and bridge:match("[/\\]mushroom%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$")
local portable = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
    and bridge:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
if not standalone and not portable then return end

local world_name = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local patch_prefix = world_name .. ":PersistentLevel.MushroomPatch_BP_C_"
local patch_class = "/Game/Assets/PCG_Brushes/MushroomPatch_BP.MushroomPatch_BP_C"
local grow_days = 1 / 86400
local last

local function valid(object) return object and object:IsValid() end
local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function child_count(patch)
    local count = 0
    patch.SpawnedShrooms:ForEach(function(key)
        local child = key:get()
        if valid(child) and not child:IsActorBeingDestroyed() then count = count + 1 end
    end)
    return count
end

local function grow(path)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. world_name and valid(world.NetDriver)
        and not valid(world.NetDriver.ServerConnection), "private listening host world")
    local patch = StaticFindObject(path)
    local instance = world.OwningGameInstance
    assert(valid(patch) and same(patch:GetClass(), StaticFindObject(patch_class)) and patch:HasAuthority()
        and not patch:IsActorBeingDestroyed() and same(patch:GetWorld(), world)
        and same(patch:GetOuter(), world.PersistentLevel) and valid(instance)
        and same(patch.Save, instance.SaveSlot_Ref), "canonical stock mushroom patch")
    local field = "DaysToGrow_Min/Max"
    local minimum, maximum = patch[field].X, patch[field].Y
    assert(type(minimum) == "number" and type(maximum) == "number" and minimum > grow_days
        and maximum >= minimum, "stock grow window")
    local before = child_count(patch)
    local ok, error_message = pcall(function()
        patch[field] = {X = grow_days, Y = grow_days}
        patch:GrowMushrooms()
    end)
    patch[field] = {X = minimum, Y = maximum}
    assert(ok, error_message)
    local after = child_count(patch)
    assert(after > before, ("stock graph did not grow a new mushroom (%d, %d)"):format(before, after))
    patch:SavePatch()
    return ("grown\t%d\t%d\t%s\t%s"):format(before, after, minimum, maximum)
end

LoopInGameThreadWithDelay(150, function()
    local file = io.open(bridge .. "/mushroom-regrow-command.tsv", "rb")
    if not file then return false end
    local request = file:read(320)
    file:close()
    local ticket, path = (request or ""):match("^(%x+)\t(/Game/[%w_./:]+)\n$")
    if not ticket or #ticket ~= 32 or ticket == last or path:sub(1, #patch_prefix) ~= patch_prefix
        or not path:sub(#patch_prefix + 1):match("^%d+$") then return false end
    last = ticket
    local ok, result = pcall(grow, path)
    local output = assert(io.open(bridge .. "/mushroom-regrow-reply.tsv", "wb"))
    output:write(ticket, ok and "\n" or "\nerror\t", result, "\nend\n")
    output:close()
    return false
end)
