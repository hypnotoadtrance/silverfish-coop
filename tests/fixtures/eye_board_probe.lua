local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local shopkeep = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
    or not bridge or not bridge:find("eye%-board%-%x+") then return {sync = function() end} end

local class_path = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
local factory_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local amount_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local previous, leveled, seeded

local function valid(a) return a and a:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function identity(item)
    local id = item[guid_field]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function prepare(state, host, operation)
    local world = state.world
    local instance, driver = world.OwningGameInstance, world.NetDriver
    local connections = driver.ClientConnections
    assert(host:HasAuthority() and same(host.Save, instance.SaveSlot_Ref) and connections:GetArrayNum() == 1)
    if operation == "xp" then
        assert(not leveled and #host.Save.ActiveQuests == 0)
        local before, after = {}, {}
        instance:Check_SaveFlag(FName("Player_Xp"), before, before)
        assert((before.Found and before.Value or 0) == 0)
        instance:GivePlayer_XP(5)
        instance:Check_SaveFlag(FName("Player_Xp"), after, after)
        assert(after.Value == 5)
        leveled = true
        return "5"
    end

    assert(operation == "eye" and not seeded)
    local remote = connections[1].PlayerController.Pawn
    assert(valid(remote) and not same(remote, host) and same(remote:GetWorld(), world) and remote:HasAuthority())
    local owner, save = require("guest_save").exact_item_context(state, "hosting", remote:GetFullName())
    assert(same(owner, remote) and same(save, remote.Save) and not same(save, instance.SaveSlot_Ref))
    local item = StaticFindObject(class_path)
    if not valid(item) then LoadAsset(class_path); item = StaticFindObject(class_path) end
    local factory = StaticFindObject(factory_path)
    assert(valid(factory) and valid(item))
    local rows, out = save.PlayerBackpack, {}
    for index = 1, rows:GetArrayNum() do assert(not same(rows[index][class_field], item)) end
    factory:MakeItemStack(item, 1, remote, out)
    local stock = out.Item or out
    local guid = identity(stock)
    assert(same(stock[class_field], item) and stock[amount_field] == 1 and guid:find("[^0]"))
    local index = rows:GetArrayNum() + 1
    rows[index] = stock
    assert(rows:GetArrayNum() == index and identity(rows[index]) == guid and same(rows[index][class_field], item))
    seeded = true
    return guid
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
