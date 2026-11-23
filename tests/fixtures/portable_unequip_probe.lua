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
    assert(not valid(pawn.EquipedItem), "fixture expected empty hands before stock selection")
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

local function unequip(pawn, index, guid, repeat_count)
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
    assert(valid(card) and same(slot.ItemManager, manager) and same(slot.Target, pawn) and slot.InventoryIndex == 0
        and slot.ItemIndex == index - 1 and same(card.InventoryWidget, slot) and same(card.ItemManager, manager)
        and identity(card.Item) == guid and identity(save.PlayerEquipment[index]) == guid,
        "fixture stock quick-slot card mismatch")
    manager:OpenContextMenu(card)
    local menu = manager.ItemContextMenu
    assert(same(menu["Item Card"], card) and valid(menu.Button_Unequip) and menu.Button_Unequip:IsVisible(),
        "fixture stock Unequip button is not available")
    for _ = 1, repeat_count do
        menu:BndEvt__ItemContextMenu_Button_Unequip_K2Node_ComponentBoundEvent_4_Pressed__DelegateSignature()
    end
    if menu:IsVisible() then manager:CloseContextMenu() end
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
    local retired = not valid(pistol) or pistol:IsActorBeingDestroyed()
    return table.concat({before, inactive, after, tostring(save.PlayerEquipIndex), held, tostring(retired), tostring(live)}, ";")
end

local function settled(pawn, capture)
    local held = item_actor(pawn, specs[1]) and "pistol" or item_actor(pawn, specs[2]) and "fallback" or "other"
    local pistol = capture.pistol
    local retired = not valid(pistol) or pistol:IsActorBeingDestroyed()
    local equipped = pawn.EquipedItem
    return table.concat({rows(pawn.Save, capture.ids), tostring(pawn.Save.PlayerEquipIndex),
        held, tostring(retired), valid(equipped) and tostring(equipped.Ammo) or "-"}, ";")
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
    if action == "remote-seed" or action == "remote-ready" or action == "remote-ammo" or action == "remote-read" then
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
        if action == "remote-ready" then
            assert(not remote_seed.pistol and item_actor(remote, specs[1]) and remote.EquipedItem.Ammo == stored_rounds
                and remote.Save.PlayerEquipIndex == specs[1].index - 1, "guest stock selection did not settle on host")
            remote_seed.pistol = remote.EquipedItem
        elseif action == "remote-ammo" then
            assert(same(remote.EquipedItem, remote_seed.pistol) and remote.Save.PlayerEquipment[specs[1].index][ammo] == stored_rounds,
                "host live rounds no longer match the named fixture")
            remote_seed.pistol.Ammo = live_rounds
        end
        return settled(remote, remote_seed)
    elseif action == "baseline" then
        assert(not guest and #driver.ClientConnections == 0 and not seed, "fixture needs its private solo host")
        seed = {pawn = pawn, ids = make_rows(pawn)}
        select_pistol(pawn, seed.ids)
        assert(control.MenuStack:GetArrayNum() == 0, "fixture expected closed solo inventory")
        local input = asset("/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory")
        control:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
        local result = exercise(pawn, seed.ids)
        control:CloseMenu({})
        return result
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
    elseif action == "guest-press-inactive" or action == "guest-press-selected" then
        assert(guest and seed and same(pawn, seed.pawn) and same(pawn.EquipedItem, seed.pistol)
            and pawn.Save.PlayerEquipIndex == specs[1].index - 1, "fixture guest no longer holds the selected pistol")
        if action == "guest-press-selected" then
            seed.pistol.Ammo = 2
            assert(seed.pistol.Ammo == 2, "fixture couldn't distinguish guest and authority rounds")
        end
        local before = settled(pawn, seed)
        local column = action == "guest-press-inactive" and 3 or 1
        unequip(pawn, specs[column].index, seed.ids[column], 2)
        assert(settled(pawn, seed) == before, "the stock guest press mutated inventory before authority settled")
        return before
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
        output:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(response):gsub("[\r\n\t]", " "))
        output:close()
    end
end)
