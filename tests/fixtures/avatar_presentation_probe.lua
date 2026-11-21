-- Stock appearance and weapon fixtures for a private, two-player headless campaign.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
    or not mailbox or not mailbox:match("[/\\]campaigns[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local command, reply = mailbox .. "/avatar-probe-command.tsv", mailbox .. "/avatar-probe-reply.tsv"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local mesh_root = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
local inventory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local face_field = "Mesh_Head_60_630BB776410363C64BE2C89A3F72B1C5"
local plumage_field = "Mesh_Plumage_80_4B6C3DD94D27DEC901D6829472D5F546"
local skin_field = "Color_Skin_53_0B756DAC41D1CF33A2BB2480770F4445"

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    assert(valid(object), "avatar_probe_asset_unavailable")
    return object
end

local function player_pair()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver)
        and not valid(driver.ServerConnection) and #driver.ClientConnections == 1, "avatar_probe_world_unavailable")
    local host, guest
    for _, pawn in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(pawn) and pawn:GetFullName():match("^Player_FP_C /Game/") and same(pawn:GetWorld(), world)
            and pawn:HasAuthority() and not pawn:IsActorBeingDestroyed() then
            if pawn:IsLocallyControlled() then
                assert(not host, "avatar_probe_duplicate_host")
                host = pawn
            else
                assert(not guest, "avatar_probe_duplicate_guest")
                guest = pawn
            end
        end
    end
    assert(host and guest and valid(guest.Save) and same(host.Save, world.OwningGameInstance.SaveSlot_Ref)
        and not same(host.Save, guest.Save), "avatar_probe_detached_guest_unavailable")
    return host, guest
end

local function style(pawn, face, plumage, tint)
    local selection = pawn.Save.Character
    selection[face_field] = asset(mesh_root .. face .. "." .. face)
    selection[plumage_field] = asset(mesh_root .. plumage .. "." .. plumage)
    selection[skin_field] = tint
    local jacket = asset("/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C")
    local out = {}
    asset(inventory):MakeDefaultItem(jacket, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], jacket), "avatar_probe_hunter_jacket_factory_unavailable")
    pawn.Save.PlayerArmor = row
end

local function equip(pawn, blueprint)
    local save = pawn.Save
    local slots = save.PlayerEquipment
    assert(slots:GetArrayNum() >= 2 and not valid(slots[1][item_class]) and not valid(slots[2][item_class]),
        "avatar_probe_empty_equipment_slots_unavailable")
    local expected = asset("/Game/Classes/Weapons/" .. blueprint .. "." .. blueprint .. "_C")
    local out = {}
    asset(inventory):MakeDefaultItem(expected, pawn, out)
    local row = out.Item or out
    assert(same(row[item_class], expected), "avatar_probe_item_factory_unavailable")
    local result = {}
    pawn:GetWorld().OwningGameInstance:EquipItemToLoadout(row, result)
    assert(result.EquippedSuccessfully == true or result[1] == true, "avatar_probe_stock_loadout_refused")
    local equipment = pawn.EquipedItem
    assert(valid(equipment) and same(equipment:GetClass(), expected) and same(equipment:GetOwner(), pawn),
        "avatar_probe_stock_equip_unavailable")
end

local function run(operation)
    local host, guest = player_pair()
    if operation == "equip" then
        style(host, "SM_Head_UV_02_EarRing", "SM_Plumage_007", { R = .2, G = .7, B = .35, A = 1 })
        style(guest, "SM_Head_UV_Fangs_01", "SM_Plumage_012", { R = .8, G = .25, B = .33, A = 1 })
        equip(host, "Wep_Pistol")
    elseif operation == "holster" then
        local save = host.Save
        local index = save.PlayerEquipIndex + 1
        local row = save.PlayerEquipment[index]
        assert(valid(row[item_class]), "avatar_probe_stock_loadout_unavailable")
        host:GetWorld().OwningGameInstance:RemoveItemFromLoadout(row)
        assert(not valid(save.PlayerEquipment[index][item_class]), "avatar_probe_stock_holster_unavailable")
    else
        error("avatar_probe_unknown_operation", 0)
    end
end

local last
LoopInGameThreadWithDelay(250, function()
    local file = io.open(command, "rb")
    if not file then return false end
    local content = file:read(100) or ""
    file:close()
    local id, operation = content:match("^(%x+)\t(%l+)\n$")
    if not id or #id ~= 32 or id == last then return false end
    last = id
    local success, issue = pcall(run, operation)
    local output = io.open(reply, "wb")
    if output then
        output:write(id .. "\t" .. (success and "ok" or tostring(issue)) .. "\n")
        output:close()
    end
    return false
end)
