local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local world_path = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local level = world_path .. ":PersistentLevel."
local prefix = mailbox .. "/portable-castaway-"
local dialogue = "/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C"
local response = "/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C"
local shop_screen = "/Game/Classes/GUI/New/ShopScreen.ShopScreen_C"
local name_field = "TextContent_2_891360234FC305CA4D09FE9EA69C8936"
local ping_field = "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local last

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end

local function stage(pawn, component, offsets)
    local origin = component:K2_GetComponentLocation()
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local height = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    local color = {R=0, G=0, B=0, A=0}
    for _, offset in ipairs(offsets) do
        local x, y = origin.X + offset[1], origin.Y + offset[2]
        local out = {}
        if trace:LineTraceSingle(pawn, {X=x, Y=y, Z=origin.Z+160}, {X=x, Y=y, Z=origin.Z-210},
            0, false, {pawn, component:GetOwner()}, 0, out, true, color, color, 0) then
            local hit = out.OutHit or out
            local point = {X=x, Y=y, Z=hit.ImpactPoint.Z + height + 2}
            if hit.ImpactNormal.Z >= .7 and math.abs(point.Z - origin.Z) < 100
                and pawn:K2_TeleportTo(point, pawn:K2_GetActorRotation()) then
                pawn:ForceNetUpdate()
                local reached = pawn:K2_GetActorLocation()
                return ("%.3f,%.3f,%.3f"):format(reached.X, reached.Y, reached.Z)
            end
        end
    end
    error("Castaway fixture could not stage the host on stock floor", 0)
end

local function inspect(action)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. world_path, "stock Observatory unavailable")
    local pawn = StaticFindObject("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    local controller = valid(pawn) and pawn.Controller
    assert(valid(controller) and pawn:IsLocallyControlled() and same(controller.Pawn, pawn), "local pawn unavailable")
    local stack = controller.MenuStack
    local menu = #stack == 1 and stack[1]
    if action == "open" then
        local npc = StaticFindObject(level .. "NPC_Castaway_C_1")
        assert(pawn:HasAuthority() and #stack == 0 and valid(npc) and npc.Alive and npc:HasAuthority()
            and same(npc:GetWorld(), world) and same(npc.Sphere_Interact:GetOwner(), npc))
        stage(pawn, npc.Sphere_Interact, {{0, 120}, {120, 0}, {0, -120}, {-120, 0}})
        npc:SendInteractionToActor(pawn, npc.Sphere_Interact)
        return "started"
    elseif action == "choices" then
        if not exact(menu, dialogue) then return "waiting" end
        local labels = {}
        local options = menu.VerticalBox_PlayerResponces
        for i = 0, options:GetChildrenCount() - 1 do
            local card = options:GetChildAt(i)
            if exact(card, response) and same(card.ParentWidget_Ref, menu) then
                labels[#labels + 1] = card.Entry[name_field]:ToString()
            end
        end
        return #labels > 0 and table.concat(labels, "|") or "waiting"
    elseif action == "trade" then
        assert(pawn:HasAuthority() and exact(menu, dialogue))
        local options = menu.VerticalBox_PlayerResponces
        for i = 0, options:GetChildrenCount() - 1 do
            local card = options:GetChildAt(i)
            if exact(card, response) and same(card.ParentWidget_Ref, menu) then
                local pings = card.Entry[ping_field]
                if #pings == 1 and pings[1]:ToString() == "Trade_Cast" then
                    card:BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature()
                    return "pressed"
                end
            end
        end
        error("Castaway's stock Trade_Cast response is unavailable", 0)
    elseif action == "shop" then
        if not exact(menu, shop_screen) or not valid(menu.ShopPanel) or not valid(menu.ShopPanel.Target) then return "waiting" end
        local shop = menu.ShopPanel.Target
        return table.concat({menu:GetClass():GetFullName(), shop:GetFullName(), shop.MerchantData:GetFullName(),
            tostring(pawn.Save.BarterOnly)}, "|")
    elseif action == "fund" then
        assert(pawn:HasAuthority() and #stack == 0)
        world.OwningGameInstance:Give_PlayerCash(2500, false)
        return "funded"
    elseif action == "offer" then
        assert(not pawn:HasAuthority() and exact(menu, shop_screen))
        local groups = {shelf = "Cards_Infinite", limited = "Cards_Limited", buyback = "Cards_BuyBack"}
        local selected
        for _, source in ipairs({"shelf", "limited", "buyback"}) do
            local cards = menu.ShopPanel[groups[source]]
            for index = 1, #cards do
                local card = cards[index]
                local id = card.Item[item_guid]
                local price = tonumber(card.Value:GetText():ToString():match("^%$(%d+)$"))
                if price and price > 0 and (not selected or price < selected.price) then
                    local guid = ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff,
                        id.C & 0xffffffff, id.D & 0xffffffff)
                    selected = {source = source, guid = guid, price = price}
                end
            end
        end
        assert(selected, "No priced stock card was displayed")
        return ("%s;%s;%d"):format(selected.source, selected.guid, selected.price)
    elseif action == "regular" then
        local merchant = StaticFindObject(level .. "Shop_BP_C_2")
        local links = world.NetDriver.ClientConnections
        assert(pawn:HasAuthority() and valid(merchant) and links:GetArrayNum() == 1)
        local guest = links[1].PlayerController.Pawn
        assert(valid(guest) and not guest:IsLocallyControlled() and same(guest:GetWorld(), world))
        return stage(guest, merchant.Box, {{0, -110}, {110, 0}, {-110, 0}})
    elseif action == "near" then
        local merchant = StaticFindObject(level .. "Shop_BP_C_2")
        local from, to = pawn:K2_GetActorLocation(), merchant.Box:K2_GetComponentLocation()
        local distance = (from.X - to.X)^2 + (from.Y - to.Y)^2 + (from.Z - to.Z)^2
        return distance < (pawn.InteractionRange - 10)^2 and "near" or "waiting"
    elseif action == "close" then
        assert(pawn:HasAuthority())
        if exact(menu, shop_screen) or exact(menu, dialogue) then controller:CloseMenu({}) end
        return tostring(#stack)
    end
    error("unknown Castaway fixture command", 0)
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(96)
    request:close()
    local ticket, action = (data or ""):match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, result = pcall(inspect, action)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(result))
    reply:close()
end)
