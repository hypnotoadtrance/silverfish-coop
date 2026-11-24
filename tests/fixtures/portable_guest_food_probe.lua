local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-guest-food-"
local map = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local camp = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.CampFire_02_BP_C_1"
local meal_path = "/Game/Classes/Items/Item_MRE.Item_MRE_C"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local stock_cases = {"Leftovers", "HoneyEgg", "DirtyBandage", "Gauze", "MedSalve", "MedKit_Large", "StimPills",
    "Antibiotics", "BrownMushroom"}
local last

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), "stock asset unavailable: " .. path)
    return object
end

local function stock_flag(save, name)
    local key = FName(name)
    return save.Flags:Contains(key) and save.Flags:Find(key):get() or 0
end

local function medical_vitals(save)
    return ("%.6f,%.6f,%.6f,%d,%d"):format(save.PlayerHealth, save.PlayerRegen, save.PlayerStim,
        stock_flag(save, "Sickness"), stock_flag(save, "Use_AntiBio"))
end

local function cooking_stats(save)
    return ("%.6f,%d,%d"):format(save.PlayerFood, stock_flag(save, "Player_Fatigue"), stock_flag(save, "Player_Intox"))
end

local function eat_all(pawn, save, empty)
    local fire = asset(camp)
    assert(not pawn:HasAuthority() and same(fire:GetWorld(), pawn:GetWorld()), "the cook must be the connected guest")
    assert(fire.Food_AmmountReady == 0, "the guest's cooking pot was already prepared")
    if not empty then
        fire:AddFood(40)
        -- Start with a ready portion so the fixture does not wait for the stock cooking timer.
        fire.Food_AmmountReady = 20
    end
    local controller = pawn.Controller
    local menu = asset("/Script/UMG.Default__WidgetBlueprintLibrary"):Create(controller,
        asset("/Game/Classes/GUI/Menu_Cook_Widget.Menu_Cook_Widget_C"), controller)
    assert(valid(menu) and same(menu:GetOwningPlayer(), controller), "could not create the stock Cook menu")
    menu.FireRef = fire
    controller:AddMenu(menu)
    local before = cooking_stats(save)
    menu:BndEvt__Menu_Cook_Widget_Button_Eat_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    return before .. ";" .. cooking_stats(save) .. ";" .. tostring(fire.Food_AmmountReady)
end

local function stock_item(remote, class, quantity)
    local out = {}
    asset(factory):MakeItemStack(class, quantity, remote, out)
    local row = out.Item or out
    assert(same(row[item_class], class) and row[ammo] == quantity, "stock item constructor returned a different stack")
    local pack = remote.Save.PlayerBackpack
    pack[pack:GetArrayNum() + 1] = row
    local id = row[item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function guest_read(pawn, save, tick)
    local stack = pawn.Controller.MenuStack
    local screen = stack[stack:GetArrayNum()]
    if screen and type(screen.type) == "function" and screen:type() == "RemoteUnrealParam" then screen = screen:get() end
    assert(valid(screen) and screen:IsVisible() and same(screen:GetOwningPlayer(), pawn.Controller)
        and same(screen:GetClass(), asset("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")), "stock Backpack unavailable")
    local info = screen.PlayerInfoBox
    assert(valid(info) and same(info["As Player FP"], pawn) and valid(info.TextBlock_Hunger), "stock hunger widget is not bound to the guest")
    -- The offscreen NullRHI viewport does not tick this stock widget after construction.
    if tick then info:Tick(info:GetCachedGeometry(), 0) end
    return ("%.6f,%.6f,%s"):format(save.PlayerFood, save.PlayerRegen, info.TextBlock_Hunger:GetText():ToString())
end

local function host_read(pawn, save, world, action)
    local remote
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and candidate:HasAuthority() and not candidate:IsLocallyControlled()
            and not candidate:IsActorBeingDestroyed() and same(candidate:GetWorld(), world) then
            assert(not remote, "only one enrolled guest is supported by this fixture")
            remote = candidate
        end
    end
    local detached = remote and remote.Save
    assert(valid(detached) and not same(detached, save), "detached authoritative guest save unavailable")
    if action == "host-read" then
        return ("%.6f,%.6f,%.6f,%.6f"):format(save.PlayerFood, save.PlayerRegen, detached.PlayerFood, detached.PlayerRegen)
    end
    if action == "host-medical-vitals" then return medical_vitals(save) .. ";" .. medical_vitals(detached) end
    if action == "host-cooking-state" then
        return cooking_stats(save) .. ";" .. cooking_stats(detached) .. ";" .. tostring(asset(camp).Food_AmmountReady)
    end
    if action == "seed-cooking-state" then
        detached.PlayerFood = 55
        detached.Flags:Add(FName("Player_Fatigue"), 50)
        return "seeded"
    end
    if action == "seed-personal-compatibility" then
        local weta = asset("/Game/Classes/Items/Item_LiveWeta.Item_LiveWeta_C")
        local unsupported = stock_item(remote, weta, 1)
        local spare = stock_item(remote, weta, 1)
        local known = stock_item(remote, asset(meal_path), 1)
        detached.PlayerHealth, detached.PlayerRegen, detached.PlayerStim = 100, 110, 0
        detached.PlayerFood = 40
        return unsupported .. ";" .. spare .. ";" .. known
    end
    if action == "seed-use-cases" then
        local ids = {}
        for index, name in ipairs(stock_cases) do
            local path = ("/Game/Classes/Items/Item_%s.Item_%s_C"):format(name, name)
            ids[index] = stock_item(remote, asset(path), name == "Gauze" and 2 or 1)
        end
        detached.PlayerHealth, detached.PlayerRegen, detached.PlayerStim = 100, 110, 0
        detached.Flags:Add(FName("Sickness"), 35)
        detached.Flags:Add(FName("Use_AntiBio"), 4)
        return table.concat(ids, ",")
    end
    assert(action == "seed", "unknown host food fixture action")
    local food, out = asset(meal_path), {}
    local pack = detached.PlayerBackpack
    for index = 1, pack:GetArrayNum() do
        assert(not same(pack[index][item_class], food), "unexpected preexisting meal")
    end
    asset(factory):ItemActorToStruct(food:GetCDO(), remote, out)
    local row = out.Item or out
    assert(same(row[item_class], food), "stock meal converter returned a different class")
    row[ammo] = 1
    pack[pack:GetArrayNum() + 1] = row
    detached.PlayerFood, detached.PlayerRegen, detached.PlayerHealth = 38, 230, 220
    local id = row[item_guid]
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function run(action)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == map, "stock Observatory unavailable")
    local pawn = asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    local save = valid(pawn) and pawn.Save
    assert(valid(save) and pawn:IsLocallyControlled() and same(save, world.OwningGameInstance.SaveSlot_Ref), "bound local co-op pawn unavailable")
    if action == "guest-read" or action == "guest-tick" then
        assert(not pawn:HasAuthority(), "the guest meal readback requires a joined player")
        return guest_read(pawn, save, action == "guest-tick")
    end
    if action == "guest-medical-vitals" then
        assert(not pawn:HasAuthority(), "the health readback needs an enrolled guest")
        return medical_vitals(save)
    end
    if action == "guest-personal-vitals" then
        assert(not pawn:HasAuthority(), "the personal stat readback needs an enrolled guest")
        return ("%.6f,%.6f"):format(save.PlayerFood, save.PlayerRegen)
    end
    if action == "cook" or action == "cook-empty" then return eat_all(pawn, save, action == "cook-empty") end
    if action == "guest-cooking-state" then return cooking_stats(save) end
    assert(pawn:HasAuthority(), "the meal fixture must seed the detached save on the host")
    return host_read(pawn, save, world, action)
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(96)
    request:close()
    if not data then return end
    local ticket, action = data:match("^(%x+)\t([a-z%-]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
