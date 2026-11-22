local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("flare%-proof%-%x+") then return end

local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local profiles = {
    red = {"Eqp_Flare", "Flare_Projectile_BP"},
    blue = {"Eqp_Flare_Blue", "Blue_Flare_Projectile_BP"},
    yellow = {"Eqp_Flare_Yellow", "Yellow_Flare_Projectile_BP"},
}
local equipment = "/Game/Classes/Equipment/"
local projectiles = equipment .. "Equipment_Assets/Equimpents_Projectiles/"
local item_path, shot_path
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local previous

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local fixture_projectiles = {}
for _, profile in pairs(profiles) do
    local name = profile[2]
    fixture_projectiles[projectiles .. name .. "." .. name .. "_C"] = true
end
RegisterBeginPlayPreHook(function(context)
    local actor = context:get()
    if not valid(actor) then return end
    local class = actor:GetClass()
    if not valid(class) or not fixture_projectiles[class:GetFullName():match("^%S+ (.+)$")] then return end
    local world = actor:GetWorld()
    if not valid(world) or world:GetFullName() ~= "World " .. area or not valid(world.NetDriver) then return end
    local marker = io.open(box .. "/flare-short-fuse", "rb")
    if not marker then return end
    marker:close()
    actor.FuseTime = 15
end)

local function run(world, pawn, guest, command, arg)
    if command == "color" then
        local profile = assert(profiles[arg])
        item_path = equipment .. profile[1] .. "." .. profile[1] .. "_C"
        shot_path = projectiles .. profile[2] .. "." .. profile[2] .. "_C"
        return arg
    end
    assert(item_path and shot_path)
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
    if command == "tossstart" or command == "tossrelease" then
        assert(guest and valid(pawn.EquipedItem) and same(pawn.EquipedItem:GetClass(), StaticFindObject(item_path)))
        pawn.EquipedItem:Use_Item_Secondary(command == "tossstart")
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
            local light, flame = actor.PointLight, actor.NI_FireSmall
            local color = valid(light) and light:GetLightColor() or {R = 0, G = 0, B = 0}
            rows[#rows + 1] = ("%d,%.2f,%.2f,%.2f,%d,%.3f,%.5f,%.5f,%.5f,%d,%.2f,%.2f"):format(
                actor:GetLocalRole(), point.X, point.Y, point.Z, actor.Lit and 1 or 0,
                valid(light) and light.Intensity or 0, color.R, color.G, color.B,
                valid(flame) and flame:IsActive() and 1 or 0, actor.FuseTime or 0, actor:GetLifeSpan())
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
