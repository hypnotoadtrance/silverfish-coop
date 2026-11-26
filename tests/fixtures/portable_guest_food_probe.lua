local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-guest-food-"
local map = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local camp = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.CampFire_02_BP_C_1"
local meal_path = "/Game/Classes/Items/Item_MRE.Item_MRE_C"
local weta_path = "/Game/Classes/Items/Item_LiveWeta.Item_LiveWeta_C"
local frog_path = "/Game/Classes/Items/Item_LiveFrog.Item_LiveFrog_C"
local mail_paths = {
    "/Game/Classes/Items/Package_Mail.Package_Mail_C",
    "/Game/Classes/Items/Package_Mail_Small.Package_Mail_Small_C",
}
local health_effects = "/Game/Classes/Items/HealthItem_Master_BP.HealthItem_Master_BP_C:ApplyItemEffects"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local stock_cases = {"Leftovers", "HoneyEgg", "DirtyBandage", "Gauze", "MedSalve", "MedKit_Large", "StimPills",
    "Antibiotics", "BrownMushroom"}
local last, weta_fixture

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
    local stack = controller.MenuStack
    assert(stack:GetArrayNum() == 0, "the guest already has a stock menu open")
    local menu = asset("/Script/UMG.Default__WidgetBlueprintLibrary"):Create(controller,
        asset("/Game/Classes/GUI/Menu_Cook_Widget.Menu_Cook_Widget_C"), controller)
    assert(valid(menu) and same(menu:GetOwningPlayer(), controller), "could not create the stock Cook menu")
    menu.FireRef = fire
    controller:AddMenu(menu)
    local before = cooking_stats(save)
    menu:BndEvt__Menu_Cook_Widget_Button_Eat_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature()
    local proof = before .. ";" .. cooking_stats(save) .. ";" .. tostring(fire.Food_AmmountReady)
    if stack:GetArrayNum() == 1 and same(stack[1], menu) then controller:CloseMenu({}) end
    assert(stack:GetArrayNum() == 0, "Eat All fixture left the stock Cook menu open")
    return proof
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
    if action == "host-guest-view-pitch" then
        return tostring((remote:GetBaseAimRotation().Pitch + 180) % 360 - 180)
    end
    if action == "host-read" then
        return ("%.6f,%.6f,%.6f,%.6f"):format(save.PlayerFood, save.PlayerRegen, detached.PlayerFood, detached.PlayerRegen)
    end
    if action == "host-medical-vitals" then return medical_vitals(save) .. ";" .. medical_vitals(detached) end
    if action == "host-personal-flags" then
        local function report(current)
            return ("%d,%d"):format(stock_flag(current, "Player_Fatigue"), stock_flag(current, "Player_Intox"))
        end
        return report(save) .. ";" .. report(detached)
    end
    if action == "host-cooking-state" then
        return cooking_stats(save) .. ";" .. cooking_stats(detached) .. ";" .. tostring(asset(camp).Food_AmmountReady)
    end
    if action == "seed-cooking-state" then
        detached.PlayerFood = 55
        detached.Flags:Add(FName("Player_Fatigue"), 50)
        return "seeded"
    end
    if action == "seed-personal-compatibility" then
        local weta = asset(weta_path)
        local unsupported = stock_item(remote, weta, 1)
        local spare = stock_item(remote, weta, 1)
        local known = stock_item(remote, asset(meal_path), 1)
        detached.PlayerHealth, detached.PlayerRegen, detached.PlayerStim = 100, 110, 0
        detached.PlayerFood = 40
        detached.Flags:Add(FName("Player_Fatigue"), 35)
        detached.Flags:Add(FName("Player_Intox"), 25)
        remote.Intoxication = 25
        return unsupported .. ";" .. spare .. ";" .. known
    end
    if action == "seed-frogs" then
        local frog = asset(frog_path)
        local clicked = stock_item(remote, frog, 1)
        local dragged = stock_item(remote, frog, 1)
        return clicked .. ";" .. dragged
    end
    if action == "seed-blue-mushroom" then
        return stock_item(remote, asset("/Game/Classes/Items/Item_BlueMushroom.Item_BlueMushroom_C"), 1)
    end
    if action == "seed-mail" then
        return stock_item(remote, asset(mail_paths[1]), 1) .. ";" .. stock_item(remote, asset(mail_paths[2]), 1)
    end
    if action == "seed-red-mushroom" then
        detached.PlayerHealth, detached.PlayerRegen = 200, 200
        detached.Flags:Add(FName("Player_Intox"), 10)
        remote.Intoxication = 10
        return stock_item(remote, asset("/Game/Classes/Items/Item_RedMushroom.Item_RedMushroom_C"), 2)
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
    if action == "sample-mail" then
        local types, seen, props = {}, {}, {}
        for _, path in ipairs(mail_paths) do
            local package = asset(path):GetCDO()
            package.Junk:ForEach(function(_, class)
                class = class:get()
                if valid(class) then types[class:GetFullName():match("[^%.]+$")] = true end
            end)
        end
        for name in pairs(types) do
            for _, actor in ipairs(FindAllOf(name) or {}) do
                if valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), world)
                    and not seen[actor:GetAddress()] and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                    seen[actor:GetAddress()] = true
                    props[#props + 1] = ("%s,%s,%s"):format(name, actor:GetFullName(), tostring(actor:HasAuthority()))
                end
            end
        end
        table.sort(props)
        return #props > 0 and table.concat(props, ";") or "-"
    end
    if action == "guest-read" or action == "guest-tick" then
        assert(not pawn:HasAuthority(), "the guest meal readback requires a joined player")
        return guest_read(pawn, save, action == "guest-tick")
    end
    if action == "guest-look-down" or action == "guest-look-up" or action == "guest-look-level" then
        assert(not pawn:HasAuthority(), "the food pose fixture needs an enrolled guest")
        local pitch = action == "guest-look-down" and -55 or action == "guest-look-up" and 55 or 0
        local controller = pawn.Controller
        controller:SetControlRotation({Pitch = pitch, Yaw = controller:GetControlRotation().Yaw, Roll = 0})
        local facing = controller:GetControlRotation()
        return tostring((facing.Pitch + 180) % 360 - 180)
    end
    if action == "guest-medical-vitals" then
        assert(not pawn:HasAuthority(), "the health readback needs an enrolled guest")
        return medical_vitals(save)
    end
    if action == "guest-use-ready" then
        assert(not pawn:HasAuthority(), "the stock item timer readback needs an enrolled guest")
        return pawn.UsingItem == false and pawn.IsWeaponBlocked == false and "ready" or "busy"
    end
    if action == "guest-personal-vitals" then
        assert(not pawn:HasAuthority(), "the personal stat readback needs an enrolled guest")
        return ("%.6f,%.6f,%d,%d,%d"):format(save.PlayerFood, save.PlayerRegen, stock_flag(save, "Player_Fatigue"),
            stock_flag(save, "Player_Intox"), pawn.Intoxication)
    end
    if action == "modded-weta" or action == "restore-weta" then
        assert(not pawn:HasAuthority(), "the modded recipe fixture needs the isolated guest")
        if action == "modded-weta" then
            assert(not weta_fixture, "the recipe fixture was already active")
            local class = asset(weta_path)
            local fixture = {uses = 0}
            -- Simulate a mod that adds two personal effects after the inherited stock use.
            fixture.pre, fixture.post = RegisterHook(health_effects, function(context)
                local item = context:get()
                local scratch = pawn.Save
                if not valid(item) or not same(item:GetClass(), class) or not same(item.Player_Ref, pawn)
                    or not valid(scratch) or same(scratch, save) or not same(scratch, world.OwningGameInstance.SaveSlot_Ref) then return end
                scratch.Flags:Add(FName("Player_Fatigue"), stock_flag(scratch, "Player_Fatigue") + 5)
                scratch.Flags:Add(FName("Player_Intox"), stock_flag(scratch, "Player_Intox") - 2)
                fixture.uses = fixture.uses + 1
            end)
            weta_fixture = fixture
            return "patched"
        end
        assert(weta_fixture, "the recipe fixture was not active")
        local result = "restored:" .. weta_fixture.uses
        UnregisterHook(health_effects, weta_fixture.pre, weta_fixture.post)
        weta_fixture = nil
        return result
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
