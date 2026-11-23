local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or not mailbox:match("finite%-shop%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local focus = require("native_shop_focus_fixture")
local world_path = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local shop_path = world_path .. ":PersistentLevel.Shop_BP_C_2"
local screen_path = "/Game/Classes/GUI/New/ShopScreen.ShopScreen_C"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_type = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local ammo_path = "BlueprintGeneratedClass /Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local last

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function identity(item)
    local id = item and item[item_guid]
    return id and ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
end

local function balance(pawn)
    local flags, name = pawn.Save.Flags, FName("Player_Cash")
    assert(flags:Contains(name), "fixture host wallet is unavailable")
    return flags:Find(name):get()
end

local function fund(pawn, amount)
    local flags, name = pawn.Save.Flags, FName("Player_Cash")
    if flags:Contains(name) then flags:Remove(name) end
    flags:Add(name, amount)
    assert(balance(pawn) == amount, "fixture host wallet did not verify")
end

local function ammunition(pawn)
    local rows, total = pawn.Save.PlayerBackpack, 0
    for index = 1, rows:GetArrayNum() do
        local row = rows[index]
        local class = row[item_type]
        if valid(class) and class:GetFullName() == ammo_path then total = total + row[item_ammo] end
    end
    return total
end

local function position_buyer(pawn, merchant)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local box = merchant.Box
    assert(valid(trace) and valid(box) and same(box:GetOwner(), merchant), "fixture stock merchant geometry unavailable")
    local anchor = box:K2_GetComponentLocation()
    local half = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local color = {R = 0, G = 0, B = 0, A = 0}
    for _, offset in ipairs({{0,-110}, {110,0}, {0,110}, {-110,0}, {70,70}, {-70,-70}, {140,70}, {-140,-70}}) do
        local x, y = anchor.X + offset[1], anchor.Y + offset[2]
        local hit = {}
        if trace:LineTraceSingle(pawn, {X=x,Y=y,Z=anchor.Z+250}, {X=x,Y=y,Z=anchor.Z-380}, 0, false,
            {pawn, merchant}, 0, hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            if hit.ImpactPoint and hit.ImpactNormal.Z >= .7 then
                local candidate = {X=x,Y=y,Z=hit.ImpactPoint.Z+half+2}
                local dz = candidate.Z - anchor.Z
                if offset[1]^2 + offset[2]^2 + dz^2 < (pawn.InteractionRange-10)^2
                    and focus.has_sight(pawn, box, candidate)
                    and pawn:K2_TeleportTo(candidate, pawn:K2_GetActorRotation()) then return end
            end
        end
    end
    error("fixture found no safe stock floor in merchant range")
end

local function verify(action, guid)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    local merchant = StaticFindObject(shop_path)
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = valid(world) and valid(api) and api:GetPlayerPawn(world, 0)
    local controller = valid(pawn) and pawn.Controller
    local host = valid(driver) and not valid(driver.ServerConnection)
    assert(valid(world) and world:GetFullName() == "World " .. world_path and valid(driver)
        and valid(merchant) and same(merchant:GetWorld(), world)
        and valid(pawn) and pawn:IsLocallyControlled() and pawn:HasAuthority() == host
        and valid(controller) and same(controller.Pawn, pawn), "fixture merchant scope changed")
    if action == "prepare" then
        local path = guid:match("^Player_FP_C (.+)$")
        local remote = path and StaticFindObject(path)
        assert(host and #driver.ClientConnections == 1 and valid(remote) and remote:GetFullName() == guid
            and remote:HasAuthority() and not remote:IsLocallyControlled() and same(remote:GetWorld(), world)
            and valid(remote.Save) and not same(remote.Save, pawn.Save), "fixture exact enrolled guest unavailable")
        position_buyer(remote, merchant)
        remote:ForceNetUpdate()
        return merchant:GetFullName()
    elseif action == "fund" then
        local amount = tonumber(guid)
        assert(host and amount and amount >= 0 and amount <= 100000 and math.tointeger(amount), "invalid fixture cash")
        fund(pawn, amount)
        return tostring(amount)
    end
    assert(#guid == 32 and guid:match("^[0-9A-F]+$"), "fixture invalid buyback item identity")
    local stack = controller.MenuStack
    local count = stack:GetArrayNum()
    local screen = count > 0 and stack[count]
    local class = StaticFindObject(screen_path)
    if action == "open" then
        assert(host and merchant:HasAuthority() and #driver.ClientConnections == 1 and count == 0,
            "fixture expected the listen host near the merchant with no menu")
        position_buyer(pawn, merchant)
        local accepted, reason = focus.press(pawn, merchant.Box, function()
            local input = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
            local interact = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact")
            assert(valid(input) and valid(interact) and not pawn.Holding_Interact, "stock host Interact is unavailable")
            pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(input:MakeInputActionValueOfType(1, 0, 0, 0), 0, 0, interact)
            pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(input:MakeInputActionValueOfType(0, 0, 0, 0), .05, .05, interact)
            return true
        end)
        assert(accepted, reason)
        screen, class = stack[stack:GetArrayNum()], StaticFindObject(screen_path)
    elseif action == "buy" or action == "host-read" then
        assert(host and merchant:HasAuthority(), "fixture expected the host's existing shop")
    else
        assert(action == "read" and not host, "fixture expected the guest's existing shop")
    end
    assert(valid(screen) and same(screen:GetClass(), class) and same(screen:GetOwningPlayer(), controller)
        and same(screen.Shop, merchant) and same(screen.ShopPanel.Target, merchant), "fixture exact stock shop unavailable")
    local cards, manager = screen.ShopPanel.Cards_BuyBack, screen.ItemManager
    for index = 1, cards:GetArrayNum() do
        local card = cards[index]
        if valid(card) and identity(card.Item) == guid then
            assert(valid(card.Item[item_type]) and card.Item[item_type]:GetFullName() == ammo_path,
                "fixture merchant card type changed")
            local quantity = card.Item[item_ammo]
            if action == "buy" then
                assert(quantity > 1, "fixture needs a finite stack of more than one round")
                local cash_before, rounds_before = balance(pawn), ammunition(pawn)
                local displayed = card.Value:GetText():ToString()
                manager:OpenContextMenu(card)
                assert(same(manager.ItemContextMenu["Item Card"], card), "stock context menu did not select the buyback card")
                manager:ActionSplitStack()
                local popup = manager.SplitStackPopup
                assert(manager.IsSplittingStack == true and valid(popup) and same(popup.ItemCard, card),
                    "stock buyback popup did not open")
                popup:SetCount(1)
                assert(popup.Count == 1, "stock buyback popup did not select one round")
                manager:FinalSplitStack(1)
                if manager.HoldingItem then
                    local held = manager.HeldItem
                    assert(held[item_ammo] == 1 and same(held[item_type], card.Item[item_type])
                        and same(screen.BackpackPanel.Target, pawn:GetWorld().OwningGameInstance),
                        "stock host purchase cursor did not hold the selected round")
                    manager:InsertItem(screen.BackpackPanel, -1)
                    assert(manager.HoldingItem == false, "stock host purchase kept the round on its cursor")
                end
                return ("%s,%d,%d,%d,%d,%d,%s"):format(screen:GetFullName(), quantity, cash_before,
                    balance(pawn), rounds_before, ammunition(pawn), displayed)
            end
            return ("%s,%d"):format(screen:GetFullName(), quantity)
        end
    end
    error("fixture could not find the exact stock buyback card")
end

LoopInGameThreadWithDelay(40, function()
    local input = io.open(mailbox .. "/stock-host-shop-command.tsv", "rb")
    if not input then return end
    local line = input:read(256)
    input:close()
    if not line then return end
    local token, action, guid = line:match("^(%x+)\t([a-z%-]+)\t([^\r\n]+)$")
    if not token or #token ~= 32 or token == last then return end
    last = token
    local ok, value = pcall(verify, action, guid)
    local output = assert(io.open(mailbox .. "/stock-host-shop-reply.tsv", "wb"))
    output:write(token, "\t", ok and "ok" or "error", "\t", (tostring(value):gsub("[\r\n\t]", " ")))
    output:close()
end)
