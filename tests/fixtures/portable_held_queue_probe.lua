local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\]first[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-held-queue-"
local weapon = "/Game/Classes/Weapons/Wep_SMG_01.Wep_SMG_01_C"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
local last

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), "stock asset unavailable: " .. path)
    return object
end

local function seed(name)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P",
        "the held-fire fixture requires the stock Observatory")
    local statics = asset("/Script/Engine.Default__GameplayStatics")
    local host = statics:GetPlayerPawn(world, 0)
    assert(valid(host) and host:IsLocallyControlled() and host:HasAuthority(), "the fixture must run on the authoritative seat")
    local guest
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and pawn:GetFullName() == name and same(pawn:GetWorld(), world)
            and pawn:HasAuthority() and not pawn:IsLocallyControlled() and not pawn:IsActorBeingDestroyed() then
            assert(not guest, "duplicate authoritative guest pawn")
            guest = pawn
        end
    end
    local canonical = world.OwningGameInstance.SaveSlot_Ref
    assert(valid(guest) and same(host.Save, canonical) and valid(guest.Save) and not same(guest.Save, canonical),
        "the exact guest needs a detached save")
    local bag = guest.Save.PlayerBackpack
    local class = asset(weapon)
    for index = 1, bag:GetArrayNum() do
        assert(not same(bag[index][item_class], class), "the guest already owns a fixture SMG")
    end
    local defaults = class:GetCDO()
    local output = {}
    asset(factory):ItemActorToStruct(defaults, guest, output)
    local row = output.Item or output
    row[ammo] = 7
    assert(same(row[item_class], class) and type(defaults.DataStruct) == "userdata", "stock SMG factory row is unavailable")
    local index = bag:GetArrayNum() + 1
    bag[index] = row
    bag[index][payload] = defaults.DataStruct
    local entry = bag[index]
    assert(same(entry[item_class], class) and entry[ammo] == 7, "the detached guest did not receive the stock SMG row")
    local guid = entry[item_guid]
    local encoded = ("%08X%08X%08X%08X"):format(guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
    assert(encoded ~= string.rep("0", 32), "the stock SMG row did not receive its own GUID")
    return encoded
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local input = request:read(650)
    request:close()
    if not input then return end
    local ticket, pawn = input:match("^(%x+)\tseed\t(Player_FP_C [^%c]+)$")
    if not ticket or #ticket ~= 32 or ticket == last or #pawn > 512 then return end
    last = ticket
    local ok, response = pcall(seed, pawn)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(response):gsub("[%c]", " "))
    reply:close()
end)
