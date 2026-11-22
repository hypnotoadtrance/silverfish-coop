local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("stock%-door%-two%-sides%-%x+") then return end

local area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local expected = "Door_Wood_04_BP_C " .. area .. ":PersistentLevel.Door_Wood_04_BP_C_1719"
local previous

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function endpoints(actor)
    local panel, knob = actor.DoorMesh, actor.Box_Knob
    assert(valid(panel) and valid(knob) and same(knob:GetAttachParent(), panel))
    local hinge, center = panel:K2_GetComponentLocation(), knob:K2_GetComponentLocation()
    local width = math.sqrt((center.X - hinge.X)^2 + (center.Y - hinge.Y)^2)
    assert(width > 40 and width < 200, "unexpected leaf length")
    local x, y = (center.X - hinge.X) / width, (center.Y - hinge.Y) / width
    return knob, center, {X = x, Y = y}, {X = y, Y = -x}
end

local function run(world, pawn, mode, who, side, reach)
    local actor = StaticFindObject(expected:match("^%S+ (.+)$"))
    assert(valid(actor) and actor:GetFullName() == expected and same(actor:GetWorld(), world))
    local knob, center, along, normal = endpoints(actor)
    local driver = world.NetDriver
    if mode == "stage" then
        assert(not valid(driver.ServerConnection) and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        local client = who == "guest" and driver.ClientConnections[1]
        local target = who == "host" and pawn or valid(client) and client.PlayerController.Pawn
        assert(valid(target) and (side == 1 or side == -1) and reach >= 75 and reach <= 180)
        target:K2_SetActorLocation({X = center.X + normal.X * side * reach - along.X * 24,
            Y = center.Y + normal.Y * side * reach - along.Y * 24, Z = center.Z}, false, {}, true)
        target:ForceNetUpdate()
        return "staged"
    end
    assert(mode == "press" and ((who == "guest") == (valid(driver.ServerConnection) == true)))
    local position = pawn:K2_GetActorLocation()
    local face = (position.X - center.X) * normal.X + (position.Y - center.Y) * normal.Y
    assert(face * side >= 40 and face * side <= 205, ("wrong leaf side %.1f"):format(face))
    local _, _, reason = require("native_loot_crate_input").stock_focus(pawn, knob)
    assert(reason == "stock_started_consumed" or reason == "stock_started", tostring(reason))
    return ("%s|%.3f"):format(reason, face)
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. area or not valid(world.NetDriver) then return end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = api:GetPlayerPawn(world, 0)
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    local input = io.open(box .. "/door-sides-probe", "rb")
    if not input then return end
    local request = input:read("*l")
    input:close()
    if not request or request == previous then return end
    previous = request
    local mode, who, side, reach = request:match("^(%a+)|(%a+)|(%-?%d+)|(%d+)|")
    local ok, result = pcall(run, world, pawn, mode, who, tonumber(side), tonumber(reach))
    local reply = io.open(box .. "/door-sides-reply", "wb")
    if reply then reply:write(ok and "ok|" or "error|", tostring(result)); reply:close() end
end)
