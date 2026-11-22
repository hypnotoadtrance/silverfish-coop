local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("flare%-proof%-%x+") then return end

local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local item_path = "/Game/Classes/Equipment/Eqp_Flare.Eqp_Flare_C"
local shot_path = "/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/Flare_Projectile_BP.Flare_Projectile_BP_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local previous

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function run(world, pawn, guest, command, arg)
    if command == "equip" or command == "slot" then
        local slot = assert(math.tointeger(tonumber(arg)))
        assert(guest and slot >= 0 and slot < 8)
        local row = pawn.Save.PlayerEquipment[slot + 1]
        local contains_flare = row and same(row[item_class], StaticFindObject(item_path)) and row[item_ammo] == 2
        if command == "slot" then return contains_flare and "ready" or "waiting" end
        assert(contains_flare)
        pawn:Equip_NextItem(slot, false)
        assert(valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), StaticFindObject(item_path)))
        return pawn.EquipedItem.Ammo
    end
    if command == "press" or command == "release" then
        assert(guest and valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), StaticFindObject(item_path)))
        pawn.EquipedItem:Use_Item_Primary(command == "press", 1.0)
        return command
    end
    if command == "ammo" then
        assert(guest)
        local actor = pawn.EquipedItem
        return valid(actor) and same(actor:GetClass(), StaticFindObject(item_path)) and actor.Ammo or "gone"
    end
    assert(command == "world")
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local klass = StaticFindObject(shot_path)
    local report = {}
    if valid(klass) then api:GetAllActorsOfClass(world, klass, report) end
    local rows = {}
    for _, actor in ipairs(report.OutActors or report) do
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        if valid(actor) and same(actor:GetWorld(), world) then
            local point = actor:K2_GetActorLocation()
            rows[#rows + 1] = ("%d,%.2f,%.2f,%.2f"):format(actor:GetLocalRole(), point.X, point.Y, point.Z)
        end
    end
    return table.concat(rows, "|")
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. area or not valid(world.NetDriver) then return end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    local input = io.open(box .. "/flare-probe", "rb")
    if not input then return end
    local request = input:read("*l")
    input:close()
    if not request or request == previous then return end
    previous = request
    local command, arg = request:match("^(%a+)|([^|]*)")
    local ok, value = pcall(run, world, pawn, valid(world.NetDriver.ServerConnection) == true, command, arg)
    local result = io.open(box .. "/flare-reply", "wb")
    if result then result:write(ok and "ok|" or "error|", tostring(value)); result:close() end
end)
