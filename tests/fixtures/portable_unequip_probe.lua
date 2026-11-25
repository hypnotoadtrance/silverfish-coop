-- Private proof of the stock context-menu Unequip route in Observatory.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local lane = mailbox and mailbox:match("[/\\]portable%-title%-%x+[/\\]([a-z]+)[/\\]")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or (lane ~= "first" and lane ~= "second")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local path = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local inventory = "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local data_tag = "DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"
local specs = {
    {index = 2, class = "/Game/Classes/Weapons/Wep_Pistol.Wep_Pistol_C"},
    {index = 5, class = "/Game/Classes/Equipment/Eqp_Flashlight.Eqp_Flashlight_C"},
    {index = 6, class = "/Game/Classes/Equipment/Eqp_Compass.Eqp_Compass_C"},
}
local worn_fields = {[9] = "PlayerHelmet", [10] = "PlayerArmor"}
local worn_classes = {
    "/Game/Classes/Armor_Items/Helm_FaceMask.Helm_FaceMask_C",
    "/Game/Classes/Armor_Items/Armor_LabCoat.Armor_LabCoat_C",
}
local melee_class = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"
local stored_rounds, live_rounds = 5, 3
local seed, remote_seed, last

local function valid(o) return o and o:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function asset(name)
    local found = StaticFindObject(name)
    if not valid(found) then LoadAsset(name); found = StaticFindObject(name) end
    assert(valid(found), "fixture stock asset unavailable: " .. name)
    return found
end

local function identity(row)
    if not row or not valid(row[item_class]) then return "-" end
    local g = row[item_guid]
    return string.format("%08X%08X%08X%08X", g.A & 0xffffffff, g.B & 0xffffffff, g.C & 0xffffffff, g.D & 0xffffffff)
end

local function row_state(save, spec, guid)
    local slot = save.PlayerEquipment[spec.index]
    local row, in_slot, in_pack = nil, identity(slot) == guid and 1 or 0, 0
    if in_slot == 1 then row = slot end
    for index = 1, save.PlayerBackpack:GetArrayNum() do
        local item = save.PlayerBackpack[index]
        if identity(item) == guid then row, in_pack = item, in_pack + 1 end
    end
    assert(row and same(row[item_class], asset(spec.class)) and in_slot + in_pack == 1, "fixture GUID absent or duplicated")
    return ("%s,%d,%d,%d,%s"):format(guid, in_slot, in_pack, row[ammo], row[data_tag]:ToString())
end

local function rows(save, ids)
    local fields = {}
    for index, spec in ipairs(specs) do fields[index] = row_state(save, spec, ids[index]) end
    return table.concat(fields, "|")
end

local function item_actor(pawn, spec)
    local item = pawn.EquipedItem
    return same(valid(item) and item:GetClass(), asset(spec.class)) and same(item.User, pawn)
        and same(item:GetOwner(), pawn) and same(item:GetWorld(), pawn:GetWorld()) and not item:IsActorBeingDestroyed()
end

local function retired(actor, world)
    if not valid(actor) or actor:IsActorBeingDestroyed() then return true end
    local live = {}
    asset("/Script/Engine.Default__GameplayStatics"):GetAllActorsOfClass(world, asset(specs[1].class), live)
    for _, value in ipairs(live.OutActors or live) do
        if value and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then value = value:get() end
        if same(value, actor) then return false end
    end
    return true
end

local function make_rows(pawn)
    local save, ids = pawn.Save, {}
    assert(valid(save) and save.PlayerEquipment:GetArrayNum() >= 8, "fixture stock equipment array unavailable")
    for index = 1, save.PlayerEquipment:GetArrayNum() do
        assert(identity(save.PlayerEquipment[index]) == "-", "fixture expected a starting campaign with no equipped items")
    end
    for index, spec in ipairs(specs) do
        local target, out = asset(spec.class), {}
        asset(factory):MakeDefaultItem(target, pawn, out)
        local row = out.Item or out
        assert(same(row[item_class], target), "fixture stock default item creation failed")
        if index == 1 then row[ammo] = stored_rounds end
        save.PlayerEquipment[spec.index] = row
        ids[index] = identity(save.PlayerEquipment[spec.index])
        assert(ids[index] ~= "-" and ids[index] ~= string.rep("0", 32), "fixture stock item identity missing")
    end
    return ids
end

local function select_pistol(pawn, ids)
    local save = pawn.Save
    for index, spec in ipairs(specs) do
        assert(identity(save.PlayerEquipment[spec.index]) == ids[index], "fixture expected stock equipment projection")
    end
    pawn:Equip_NextItem(specs[1].index - 1, false)
    assert(item_actor(pawn, specs[1]) and save.PlayerEquipIndex == specs[1].index - 1,
        "fixture stock pistol selection did not equip")
end

local function widget_at(array, index)
    local value = array[index]
    if type(value) == "userdata" and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then
        return value:get()
    end
    return value
end

local function equipped_card(pawn, index, guid)
    local control, save = pawn.Controller, pawn.Save
    local menu_stack = control.MenuStack
    local count = menu_stack:GetArrayNum()
    assert(count > 0, "fixture expected an open stock inventory")
    local screen = widget_at(menu_stack, count)
    local manager = valid(screen) and screen.ItemManager
    assert(same(valid(screen) and screen:GetClass(), asset(inventory)) and same(screen:GetOwningPlayer(), control)
        and valid(manager) and same(manager.ParentScreen, screen)
        and same(screen.BackpackPanel.Target, pawn:GetWorld().OwningGameInstance),
        "fixture stock inventory widget not open")
    local slot = widget_at(manager.EquipSlots, index)
    local card = valid(slot) and slot.ItemCard
    local worn = worn_fields[index]
    local target = worn and pawn:GetWorld().OwningGameInstance or pawn
    local saved_item = worn and save[worn] or save.PlayerEquipment[index]
    assert(valid(card) and same(slot.ItemManager, manager) and same(slot.Target, target)
        and slot.InventoryIndex == (worn and 1 or 0) and slot.ItemIndex == (worn and index - 9 or index - 1)
        and same(card.InventoryWidget, slot) and same(card.ItemManager, manager)
        and identity(card.Item) == guid and identity(saved_item) == guid, "fixture stock equipment card mismatch")
    return screen, manager, card
end

local function unequip(pawn, index, guid, repeat_count)
    local _, manager, card = equipped_card(pawn, index, guid)
    manager:OpenContextMenu(card)
    local menu = manager.ItemContextMenu
    assert(same(menu["Item Card"], card) and valid(menu.Button_Unequip) and menu.Button_Unequip:IsVisible(),
        "fixture stock Unequip button is not available")
    for _ = 1, repeat_count do
        menu:BndEvt__ItemContextMenu_Button_Unequip_K2Node_ComponentBoundEvent_4_Pressed__DelegateSignature()
    end
    if menu:IsVisible() then manager:CloseContextMenu() end
end

local function drag(pawn, index, guid, cancel, to_floor)
    local screen, manager, card = equipped_card(pawn, index, guid)
    manager.HoveredCard = card
    manager:InitiateItemTake()
    manager:FinalTakeItem()
    assert(manager.HoldingItem == true and identity(manager.HeldItem) == guid, "stock cursor did not hold the exact item")
    if cancel then manager:ReturnItem(manager.HeldItem, {})
    else manager:InsertItem(to_floor and screen.FloorPanel or screen.BackpackPanel, -1) end
    assert(manager.HoldingItem == false, "stock equipment drag retained the cursor item")
end

local function worn_state(pawn)
    local save, fields = pawn.Save, {}
    for index = 9, 10 do fields[#fields + 1] = identity(save[worn_fields[index]]) end
    return table.concat(fields, ",")
end

local function drop_helmet(pawn, guid)
    local _, manager, card = equipped_card(pawn, 9, guid)
    manager:OpenContextMenu(card)
    local menu = manager.ItemContextMenu
    assert(same(menu["Item Card"], card) and valid(menu.Button_Drop) and menu.Button_Drop:IsVisible(),
        "fixture stock Drop button is not available for worn gear")
    menu:BndEvt__ItemContextMenu_Button_Drop_K2Node_ComponentBoundEvent_6_Pressed__DelegateSignature()
end

local function backpack_card(pawn, guid)
    local stack = pawn.Controller.MenuStack
    local screen = widget_at(stack, stack:GetArrayNum())
    local manager = valid(screen) and screen.ItemManager
    assert(valid(manager) and same(manager.ParentScreen, screen), "fixture stock inventory widget not open")
    local cards, card = screen.BackpackPanel.Cards
    for index = 1, cards:GetArrayNum() do
        local candidate = widget_at(cards, index)
        if valid(candidate) and identity(candidate.Item) == guid then card = candidate; break end
    end
    assert(card and same(card.InventoryWidget, screen.BackpackPanel), "fixture backpack card unavailable")
    return manager, card
end

local function drag_from_backpack(pawn, guid, index)
    local manager, card = backpack_card(pawn, guid)
    manager.HoveredCard = card
    manager:InitiateItemTake()
    manager:FinalTakeItem()
    assert(manager.HoldingItem and identity(manager.HeldItem) == guid, "fixture did not pick up the exact backpack item")
    manager:InsertItem(widget_at(manager.EquipSlots, index), 1)
    assert(not manager.HoldingItem, "stock equipment drag did not release the cursor")
end

local function equip_worn(pawn, guid, drag_to_chest)
    if drag_to_chest then
        drag_from_backpack(pawn, guid, 10)
    else
        local manager, card = backpack_card(pawn, guid)
        manager:OpenContextMenu(card)
        local menu = manager.ItemContextMenu
        assert(same(menu["Item Card"], card) and valid(menu.Button_Equip) and menu.Button_Equip:IsVisible(),
            "fixture stock Equip button is not available")
        menu:BndEvt__ItemContextMenu_Button_Equip_K2Node_ComponentBoundEvent_3_Pressed__DelegateSignature()
        if menu:IsVisible() then manager:CloseContextMenu() end
    end
end

local function seed_melee(pawn)
    local class, out = asset(melee_class), {}
    asset(factory):MakeDefaultItem(class, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], class), "fixture melee creation failed")
    local save = pawn.Save
    local index = save.PlayerBackpack:GetArrayNum() + 1
    assert(identity(save.PlayerEquipment[1]) == "-", "fixture expected the stock melee slot empty")
    save.PlayerBackpack[index] = row
    return identity(save.PlayerBackpack[index])
end

local function exercise(pawn, ids)
    local save, pistol = pawn.Save, pawn.EquipedItem
    assert(item_actor(pawn, specs[1]) and save.PlayerEquipment[specs[1].index][ammo] == stored_rounds,
        "fixture pistol or stored round count changed before stock Unequip")
    local before = rows(save, ids)
    unequip(pawn, specs[3].index, ids[3], 1)
    local inactive = rows(save, ids)
    assert(same(pawn.EquipedItem, pistol) and item_actor(pawn, specs[1]), "inactive slot changed the held pistol")
    pistol.Ammo = live_rounds
    local live = pistol.Ammo
    assert(live == live_rounds and save.PlayerEquipment[specs[1].index][ammo] == stored_rounds,
        "fixture live-vs-saved ammunition split was unavailable")
    unequip(pawn, specs[1].index, ids[1], 1)
    local after = rows(save, ids)
    local held = item_actor(pawn, specs[2]) and "fallback" or same(pawn.EquipedItem, pistol) and "pistol" or "other"
    return table.concat({before, inactive, after, tostring(save.PlayerEquipIndex), held,
        tostring(retired(pistol, pawn:GetWorld())), tostring(live)}, ";")
end

local function settled(pawn, capture)
    local held = item_actor(pawn, specs[1]) and "pistol" or item_actor(pawn, specs[2]) and "fallback" or "other"
    local pistol = capture.pistol
    local equipped = pawn.EquipedItem
    return table.concat({rows(pawn.Save, capture.ids), tostring(pawn.Save.PlayerEquipIndex),
        held, tostring(retired(pistol, pawn:GetWorld())), valid(equipped) and tostring(equipped.Ammo) or "-"}, ";")
end

local function run(action, argument)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    local pawn = valid(world) and asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    local control = valid(pawn) and pawn.Controller
    local guest = lane == "second"
    assert(valid(world) and world:GetFullName() == "World " .. path and valid(driver)
        and (valid(driver.ServerConnection) == true) == guest
        and valid(pawn) and pawn:IsLocallyControlled() and pawn:HasAuthority() ~= guest and pawn.Dead == false
        and valid(control) and control:IsLocalController() and same(control.Pawn, pawn)
        and same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref), "fixture exact private player scope unavailable")
    if action == "remote-seed" or action == "remote-ready" or action == "remote-ammo" or action == "remote-read"
        or action == "remote-worn-seed" or action == "remote-melee-seed" or action == "remote-melee-held" then
        assert(not guest and #driver.ClientConnections == 1, "one enrolled guest required")
        local name = argument and argument:match("^Player_FP_C (.+)$")
        local remote = name and StaticFindObject(name)
        assert(valid(remote) and remote:GetFullName() == argument and remote:HasAuthority()
            and not remote:IsLocallyControlled() and same(remote:GetWorld(), world)
            and valid(remote.Save) and not same(remote.Save, pawn.Save), "fixture authoritative detached owner unavailable")
        if action == "remote-seed" then
            assert(not remote_seed, "fixture remote seed already applied")
            remote_seed = {pawn = remote, save = remote.Save, ids = make_rows(remote)}
            return table.concat(remote_seed.ids, ",")
        end
        assert(remote_seed and same(remote_seed.pawn, remote) and same(remote_seed.save, remote.Save),
            "fixture authoritative detached owner changed")
        if action == "remote-melee-seed" then
            assert(not remote_seed.melee, "fixture melee item already seeded")
            remote_seed.melee = seed_melee(remote)
            return remote_seed.melee
        elseif action == "remote-melee-held" then
            assert(remote_seed.melee and identity(remote.Save.PlayerEquipment[1]) == remote_seed.melee
                and remote.Save.PlayerEquipIndex == 0 and item_actor(remote, {class = melee_class}),
                "fixture expected the exact Old Pipe equipped on the host")
            return "held"
        elseif action == "remote-worn-seed" then
            assert(not remote_seed.worn, "fixture worn items already seeded")
            local save, ids = remote.Save, {}
            assert(worn_state(remote) == "-,-", "fixture expected unoccupied worn slots")
            local classes = {worn_classes[1], worn_classes[2], worn_classes[1],
                "/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C"}
            for index, name in ipairs(classes) do
                local class, out = asset(name), {}
                asset(factory):MakeDefaultItem(class, remote, out)
                local row = out.Item or out
                assert(same(row[item_class], class), "fixture stock wearable creation failed")
                local slot = save.PlayerBackpack:GetArrayNum() + 1
                save.PlayerBackpack[slot] = row
                ids[index] = identity(row)
                assert(identity(save.PlayerBackpack[slot]) == ids[index] and ids[index] ~= "-",
                    "fixture failed to seed the detached backpack")
            end
            remote_seed.worn = ids
            return table.concat(ids, ",")
        elseif action == "remote-ready" then
            assert(not remote_seed.pistol and item_actor(remote, specs[1]) and remote.EquipedItem.Ammo == stored_rounds
                and remote.Save.PlayerEquipIndex == specs[1].index - 1, "guest stock selection did not settle on host")
            remote_seed.pistol = remote.EquipedItem
        elseif action == "remote-ammo" then
            assert(same(remote.EquipedItem, remote_seed.pistol) and remote.Save.PlayerEquipment[specs[1].index][ammo] == stored_rounds,
                "host live rounds no longer match the named fixture")
            remote_seed.pistol.Ammo = live_rounds
        end
        return settled(remote, remote_seed)
    elseif action == "host-close-inventory" then
        assert(not guest and control.MenuStack:GetArrayNum() == 1, "fixture expected the host's stock inventory")
        control:CloseMenu({})
        assert(control.MenuStack:GetArrayNum() == 0, "fixture host inventory did not close")
        return "closed"
    elseif action == "host-wear-mask" or action == "host-drop-mask" then
        assert(not guest and #driver.ClientConnections == 1, "fixture expected a co-op host")
        if action == "host-wear-mask" then
            assert(identity(pawn.Save.PlayerHelmet) == "-" and control.MenuStack:GetArrayNum() == 0)
            local input = asset("/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory")
            control:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
            equip_worn(pawn, argument, false)
        else
            drop_helmet(pawn, argument)
        end
        return identity(pawn.Save.PlayerHelmet)
    elseif action == "baseline" then
        assert(not guest and #driver.ClientConnections == 0 and not seed, "fixture needs its private solo host")
        assert(control.MenuStack:GetArrayNum() == 0, "fixture expected closed solo inventory")
        local input = asset("/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory")
        local melee = seed_melee(pawn)
        local initial = pawn.Save.PlayerEquipIndex
        assert(initial == 0 and not valid(pawn.EquipedItem), "fixture expected the initially selected empty melee slot")
        control:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
        drag_from_backpack(pawn, melee, 1)
        assert(identity(pawn.Save.PlayerEquipment[1]) == melee, "vanilla did not accept the Old Pipe in the first slot")
        assert(item_actor(pawn, {class = melee_class}), "vanilla did not draw the newly equipped melee weapon")
        unequip(pawn, 1, melee, 1)
        assert(identity(pawn.Save.PlayerEquipment[1]) == "-", "vanilla did not clear the melee slot")
        control:CloseMenu({})
        seed = {pawn = pawn, ids = make_rows(pawn)}
        select_pistol(pawn, seed.ids)
        control:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
        local result = exercise(pawn, seed.ids)
        control:CloseMenu({})
        return result
    elseif action == "guest-projection" then
        assert(guest and not seed, "fixture expected the first unmodified guest inventory")
        local slots, observed = pawn.Save.PlayerEquipment, {}
        assert(slots:GetArrayNum() >= 8, "fixture stock quick-slot layout unavailable")
        for index, spec in ipairs(specs) do
            local row = slots[spec.index]
            local class = row[item_class]
            observed[index] = valid(class) and class:GetFullName() == "BlueprintGeneratedClass " .. spec.class
                and identity(row) or "-"
        end
        return table.concat(observed, ",")
    elseif action == "guest-ready" then
        assert(guest and not seed, "fixture guest check already applied")
        local ids = {}
        for id in (argument or ""):gmatch("[^,]+") do
            assert(#id == 32 and id:match("^[0-9A-F]+$"), "fixture invalid authoritative GUID")
            ids[#ids + 1] = id
        end
        assert(#ids == #specs, "fixture inventory identity set incomplete")
        seed = {pawn = pawn, ids = ids}
        select_pistol(pawn, ids)
        seed.pistol = pawn.EquipedItem
        return settled(pawn, seed)
    elseif action == "guest-cancel-inactive" or action == "guest-press-inactive" or action == "guest-press-selected" then
        assert(guest and seed and same(pawn, seed.pawn) and same(pawn.EquipedItem, seed.pistol)
            and pawn.Save.PlayerEquipIndex == specs[1].index - 1, "fixture guest no longer holds the selected pistol")
        if action == "guest-press-selected" then
            seed.pistol.Ammo = 2
            assert(seed.pistol.Ammo == 2, "fixture couldn't distinguish guest and authority rounds")
        end
        local before = settled(pawn, seed)
        if action == "guest-press-selected" then
            unequip(pawn, specs[1].index, seed.ids[1], 2)
        else
            drag(pawn, specs[3].index, seed.ids[3], action == "guest-cancel-inactive")
        end
        assert(settled(pawn, seed) == before, "the stock guest press mutated inventory before authority settled")
        if action ~= "guest-press-selected" then return before end
        local pistol = seed.pistol
        return before .. ";" .. table.concat({
            tostring(pistol:HasAuthority()), tostring(same(pistol.Instigator, pawn)),
            tostring(pistol.HeldByPlayer), tostring(pistol:IsActorBeingDestroyed()),
        }, ",")
    elseif action == "guest-equip-melee" then
        assert(guest and not seed and pawn.Save.PlayerEquipIndex == 0 and not valid(pawn.EquipedItem)
            and identity(pawn.Save.PlayerEquipment[1]) == "-", "fixture expected the initially selected empty melee slot")
        drag_from_backpack(pawn, argument, 1)
        return "pressed"
    elseif action == "guest-melee-held" then
        assert(guest and not seed, "fixture guest owner changed")
        assert(identity(pawn.Save.PlayerEquipment[1]) == argument and pawn.Save.PlayerEquipIndex == 0
            and item_actor(pawn, {class = melee_class}), "fixture expected the stock Old Pipe in hand")
        return "held"
    elseif action == "guest-worn-state" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        return worn_state(pawn)
    elseif action == "guest-worn-backpack" then
        assert(guest and (not seed or same(pawn, seed.pawn)), "fixture guest owner changed")
        local pack, result = pawn.Save.PlayerBackpack, {}
        for index = 1, pack:GetArrayNum() do result[index] = identity(pack[index]) end
        return table.concat(result, ",")
    elseif action == "guest-sort-backpack" or action == "guest-reequip-utility" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        if action == "guest-sort-backpack" then
            pawn:GetWorld().OwningGameInstance:SortItems(5)
        else
            equip_worn(pawn, seed.ids[3], false)
        end
        return "pressed"
    elseif action == "guest-swap-worn-pair" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        local helmet, armor = argument:match("^([%x]+),([%x]+)$")
        assert(helmet and #helmet == 32 and #armor == 32, "fixture expected the two replacement GUIDs")
        local state = {world = world, map = path:match("^([^.]+)"), server = true, listening = true,
            controller = control, local_pawn = {pawn = pawn, controller_object = control, name = pawn:GetFullName()}}
        equip_worn(pawn, helmet, false)
        inventory_view.sync(state, "connected")
        local function first_report()
            for _, record in ipairs(inventory_view.rows(state, "connected", table.pack)) do
                if record[1] == "native-inventory-stock-action" then return record end
            end
        end
        local first = assert(first_report())
        assert(first[7] == helmet and inventory_view.accept_layout_projection(pawn, tostring(first[5])))
        equip_worn(pawn, armor, true)
        inventory_view.sync(state, "connected")
        assert(first_report()[5] == first[5], "the newest swap replaced the older outstanding report")
        assert(not inventory_view.accept_layout_projection(pawn, tostring(first[5])),
            "an older reply could overwrite the newest local swap")
        local result = worn_state(pawn)
        assert(result == helmet .. "," .. armor, "both replacements must apply before yielding the game thread")
        return result
    elseif action == "guest-equip-helmet" or action == "guest-equip-armor" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        local prior = worn_state(pawn)
        equip_worn(pawn, argument, action == "guest-equip-armor")
        assert(worn_state(pawn) == prior, "the stock guest equipped a wearable before authority settled")
        return prior
    elseif action == "guest-remove-helmet" or action == "guest-drag-armor" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        local prior = worn_state(pawn)
        if action == "guest-remove-helmet" then unequip(pawn, 9, argument, 2) else drag(pawn, 10, argument, false) end
        assert(worn_state(pawn) == prior, "the stock guest removed worn equipment before authority settled")
        return prior
    elseif action == "guest-drop-helmet" or action == "guest-drop-armor" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        local prior = worn_state(pawn)
        if action == "guest-drop-helmet" then
            drop_helmet(pawn, argument)
        else
            drag(pawn, 10, argument, false, true)
        end
        assert(worn_state(pawn) == prior, "the stock guest dropped worn equipment before authority settled")
        return prior
    elseif action == "guest-read" then
        assert(guest and seed and same(pawn, seed.pawn), "fixture guest owner changed")
        return settled(pawn, seed)
    end
    error("unknown fixture action")
end

LoopInGameThreadWithDelay(40, function()
    local file = io.open(mailbox .. "/portable-unequip-command.tsv", "rb")
    if not file then return end
    local data = file:read(512)
    file:close()
    if not data then return end
    local ticket, action, argument = data:match("^(%x+)\t([a-z%-]+)\t?(.*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, argument)
    local output = io.open(mailbox .. "/portable-unequip-reply.tsv", "wb")
    if output then
        local message = tostring(response):gsub("[\r\n\t]", " ")
        output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
        output:close()
    end
end)
