local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local shopkeep = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not bridge
    or not (bridge:find("eye%-board%-%x+") or bridge:find("portable%-title%-%x+"))
    or (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1") then return {sync = function() end} end

local class_path = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local amount_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local previous, leveled, seeded
local discovered_path = shopkeep .. ".Shop_ShopKeep_P:PersistentLevel.BountyBoard_BP_C_2"

local function valid(a) return a and a:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function identity(item)
    local id = item[guid_field]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function approach_discovered(state, guest)
    local world = state.world
    local board = StaticFindObject(discovered_path)
    local stock = require("native_wire_quest")
    local box = stock.qualified_stock_board(world, world.OwningGameInstance, board)
    assert(box and board:GetFullName() == "BountyBoard_BP_C " .. discovered_path and board:HasAuthority())
    assert(board.TurnIn_ID == 0 and board.Display_GlobalContracts == true)
    local anchor, origin = box:K2_GetComponentLocation(), board:K2_GetActorLocation()
    local reach, capsule = guest.InteractionRange, guest.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local ignored, color = {guest, board}, {R = 0, G = 0, B = 0, A = 0}
    local function in_reach(point, center)
        return (point.X - center.X)^2 + (point.Y - center.Y)^2 + (point.Z - center.Z)^2 <= reach^2
    end
    for _, offset in ipairs({{0, 110}, {110, 0}, {0, -110}, {-110, 0}, {85, 85}, {-85, 85}, {85, -85}, {-85, -85}}) do
        local x, y = anchor.X + offset[1], anchor.Y + offset[2]
        local top, bottom = {X = x, Y = y, Z = anchor.Z + 250}, {X = x, Y = y, Z = anchor.Z - 380}
        local out = {}
        local found = trace:LineTraceSingle(guest, top, bottom, 0, false, ignored, 0, out, true, color, color, 0)
        local hit = out.OutHit or out
        if found == true and hit.ImpactNormal.Z >= 0.7 then
            local target = {X = x, Y = y, Z = hit.ImpactPoint.Z + capsule + 2}
            if in_reach(target, anchor) and in_reach(target, origin) and guest:K2_TeleportTo(target, guest:K2_GetActorRotation()) then
                local actual = guest:K2_GetActorLocation()
                if in_reach(actual, anchor) and in_reach(actual, origin) then
                    guest:ForceNetUpdate()
                    return ("%s;%0.4f;%0.4f;%0.4f"):format(board:GetFullName(), actual.X, actual.Y, actual.Z)
                end
            end
        end
    end
    error("The discovered board has no reachable stock collision floor", 0)
end

local function prepare(state, host, operation)
    local world = state.world
    local instance, driver = world.OwningGameInstance, world.NetDriver
    local connections = driver.ClientConnections
    assert(host:HasAuthority() and same(host.Save, instance.SaveSlot_Ref) and connections:GetArrayNum() == 1)
    if operation == "xp" or operation == "globalxp" then
        assert(not leveled and #host.Save.ActiveQuests == 0)
        local before, after = {}, {}
        instance:Check_SaveFlag(FName("Player_Xp"), before, before)
        local original = before.Found and before.Value or 0
        assert(original == 0 or operation == "globalxp" and original == 5)
        if original == 0 then instance:GivePlayer_XP(5) end
        instance:Check_SaveFlag(FName("Player_Xp"), after, after)
        assert(after.Value == 5)
        leveled = true
        return "5"
    end

    local remote = connections[1].PlayerController.Pawn
    assert(valid(remote) and not same(remote, host) and same(remote:GetWorld(), world) and remote:HasAuthority())
    local owner, save = require("guest_save").exact_item_context(state, "hosting", remote:GetFullName())
    assert(same(owner, remote) and same(save, remote.Save) and not same(save, instance.SaveSlot_Ref))
    if operation == "approach" then return approach_discovered(state, remote) end
    assert(operation == "eye" and not seeded)
    local item = StaticFindObject(class_path)
    if not valid(item) then LoadAsset(class_path); item = StaticFindObject(class_path) end
    local factory = StaticFindObject(factory_path)
    assert(valid(factory) and valid(item))
    local defaults = item:GetCDO()
    local count = defaults.Ammo
    assert(defaults.Stacks == true and count == 2, "The native Eye stack recipe changed")
    local rows = save.PlayerBackpack
    for index = 1, rows:GetArrayNum() do assert(not same(rows[index][class_field], item)) end

    local function append_stack(units)
        local out = {}
        factory:MakeItemStack(item, units, remote, out)
        local stock = out.Item or out
        local guid = identity(stock)
        assert(same(stock[class_field], item) and stock[amount_field] == units and guid:find("[^0]"))
        local index = rows:GetArrayNum() + 1
        rows[index] = stock
        assert(rows:GetArrayNum() == index and identity(rows[index]) == guid and same(rows[index][class_field], item))
        return guid
    end

    local guid, spare = append_stack(count), append_stack(1)
    assert(guid ~= spare)
    seeded = true
    return guid .. ";" .. spare
end

local function sync(state, phase)
    if phase ~= "hosting" or state.map ~= shopkeep or state.clients ~= 1 or not state.local_pawn then return end
    local host = state.local_pawn.pawn
    local input = io.open(bridge .. "/eye-board-probe", "rb")
    if not input then return end
    local request = input:read("*l")
    input:close()
    if not request or request == previous then return end
    previous = request
    local operation, nonce = request:gsub("\r$", ""):match("^(%a+)|(%x+)$")
    if not nonce then return end
    local ok, result = pcall(prepare, state, host, operation)
    local output = assert(io.open(bridge .. "/eye-board-reply", "wb"))
    output:write(nonce, "|", ok and "ok|" or "error|", tostring(result))
    output:close()
end

return {sync = sync}
