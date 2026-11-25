local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-wipe-chooser-"
local observatory = "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local paths = {
    pause = "/Game/Classes/GUI/Menu_Pause_Widget.Menu_Pause_Widget_C",
    journal = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C",
}
local bandage = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local extraction = "ExtractionMode_45_FC0B60E8470417127E7367B262395F41"
local slots = "Items_KeptOnDeath_14_9602AB054254974168B43BB0C2924307"
local drops = "Drop_BonePile_43_877E5A2C4F77E5E91CB0ED82EBC3738D"
local lose_slot = "LoseSlot_PerDeath_41_60DB8A134132D666D7FD9E9714F51A77"
local seen

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function load_class(path)
    if not valid(StaticFindObject(path)) then LoadAsset(path) end
    return StaticFindObject(path)
end

local function guest_in_world(world, stock)
    local guest
    for _, actor in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(actor) and actor:HasAuthority() and same(actor:GetWorld(), world) and not actor:IsLocallyControlled()
            and not actor:IsActorBeingDestroyed() and valid(actor.Save) and not same(actor.Save, stock) then
            assert(not guest, "the wipe fixture requires a single guest")
            guest = actor
        end
    end
    assert(guest, "the wipe fixture requires one detached guest")
    return guest
end

local function held_row(pawn)
    local slots, index = pawn.Save.PlayerEquipment, pawn.Save.PlayerEquipIndex
    local selected = index >= 0 and index < slots:GetArrayNum() and slots[index + 1]
    local held = pawn.EquipedItem
    local guid = selected and selected[item_guid]
    local class = selected and selected[item_class]
    local identity = valid(class) and guid and ("%08X%08X%08X%08X"):format(
        guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff) or "-"
    return table.concat({valid(held) and not held:IsActorBeingDestroyed() and held:GetClass():GetFullName() or "-",
        identity, pawn.Aiming and "aiming" or "lowered"}, ",")
end

local function run(action)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == observatory and valid(world.NetDriver))
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    local controller = valid(pawn) and pawn.Controller
    assert(valid(controller) and pawn:IsLocallyControlled() and same(controller.Pawn, pawn) and same(pawn:GetWorld(), world))
    if action == "held" then
        local guest = pawn:HasAuthority() and guest_in_world(world, world.OwningGameInstance.SaveSlot_Ref) or pawn
        return held_row(guest)
    end
    if action == "aim" then
        assert(not pawn:HasAuthority() and valid(world.NetDriver.ServerConnection))
        local held = pawn.EquipedItem
        assert(valid(held) and same(held.User, pawn), "stock wipe aim requires a locally held weapon")
        held:Use_Item_Secondary(true)
        assert(pawn.Aiming)
        return "aiming"
    end
    if action == "configure" then
        local stock = world.OwningGameInstance.SaveSlot_Ref
        assert(pawn:HasAuthority() and same(pawn.Save, stock) and not valid(world.NetDriver.ServerConnection))
        local guest = guest_in_world(world, stock)
        local rules = stock.GameRules
        rules[extraction], rules[slots], rules[drops], rules[lose_slot] = true, 1, true, false
        assert(stock.GameRules[extraction] and stock.GameRules[slots] == 1 and stock.GameRules[drops]
            and not stock.GameRules[lose_slot])
        local library = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
        local class, row = load_class(bandage), {}
        library:MakeDefaultItem(class, guest, row)
        local item = row.Item or row
        assert(same(item[item_class], class))
        local backpack = guest.Save.PlayerBackpack
        backpack[backpack:GetArrayNum() + 1] = item
        local guid = item[item_guid]
        return ("%08X%08X%08X%08X"):format(guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
    end
    local class = paths[action] and load_class(paths[action])
    local menus = controller.MenuStack
    assert(valid(class) and menus:GetArrayNum() == 0 and controller.Loading == false)
    local opened = {}
    controller:OpenMenu(class, opened)
    local menu = menus[1]
    assert(menus:GetArrayNum() == 1 and same(menu, opened.CreatedMenu) and same(menu:GetClass(), class)
        and same(menu:GetOwningPlayer(), controller) and menu:IsInViewport())
    return action
end

LoopInGameThreadWithDelay(30, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(128)
    file:close()
    if not request or request == seen then return end
    seen = request
    local ticket, action = request:match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 then return end
    local ok, result = pcall(run, action)
    local reply = io.open(prefix .. "reply.tsv", "wb")
    if reply then reply:write(ticket, ok and "\tok\t" or "\terror\t", tostring(result)); reply:close() end
end)
