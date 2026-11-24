local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local seat = mailbox and mailbox:match("[/\\]portable%-title%-%x+[/\\]([a-z]+)[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or (seat ~= "first" and seat ~= "second") then return end

local prefix = mailbox .. "/portable-held-queue-"
local weapon = "/Game/Classes/Weapons/Wep_SMG_01.Wep_SMG_01_C"
local factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
local last, attempted, attempted_pawn

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), "stock asset unavailable: " .. path)
    return object
end

local function identity(row)
    if not row or not valid(row[item_class]) then return "-" end
    local guid = row[item_guid]
    return ("%08X%08X%08X%08X"):format(guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
end

local function current_player()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World /Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P",
        "the held-fire fixture requires the stock Observatory")
    local statics = asset("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "the exact local pawn is unavailable")
    return world, pawn
end

local function seed(name)
    local world, host = current_player()
    assert(seat == "first" and host:HasAuthority(), "only the authoritative seat can seed the SMG")
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
    local rows = guest.Save.PlayerEquipment
    local class = asset(weapon)
    local slot
    for index = 1, rows:GetArrayNum() do
        assert(not same(rows[index][item_class], class), "the guest already has a fixture SMG in their equipment")
        if not slot and index - 1 ~= guest.Save.PlayerEquipIndex and identity(rows[index]) == "-" then slot = index end
    end
    assert(slot, "the held-fire fixture needs an open utility slot")
    local defaults = class:GetCDO()
    local output = {}
    asset(factory):ItemActorToStruct(defaults, guest, output)
    local row = output.Item or output
    row[ammo] = 7
    assert(same(row[item_class], class) and type(defaults.DataStruct) == "userdata", "stock SMG factory row is unavailable")
    rows[slot] = row
    rows[slot][payload] = defaults.DataStruct
    local entry = rows[slot]
    assert(same(entry[item_class], class) and entry[ammo] == 7, "the detached guest did not receive the stock SMG row")
    local encoded = identity(entry)
    assert(encoded ~= string.rep("0", 32), "the stock SMG row did not receive its own GUID")
    return encoded
end

local function select_stock(name, guid)
    local world, pawn = current_player()
    assert(seat == "second" and not pawn:HasAuthority() and pawn:GetFullName() == name,
        "only the exact connected guest may select the stock item")
    local save, held = pawn.Save, pawn.EquipedItem
    local rows, selected = save.PlayerEquipment, save.PlayerEquipIndex
    local row = selected >= 0 and selected < rows:GetArrayNum() and rows[selected + 1] or nil
    local held_class = valid(held) and held:GetClass():GetFullName() or "-"
    local label = ("%d;%s;%s"):format(selected, identity(row), held_class)
    for slot = 1, rows:GetArrayNum() do
        local next_row = rows[slot]
        if identity(next_row) == guid then
            if selected == slot - 1 and valid(held) and same(held:GetClass(), next_row[item_class])
                and same(held:GetOwner(), pawn) and same(held.Instigator, pawn) and same(held:GetWorld(), world) then
                return "selected;" .. label
            end
            if valid(held) and (not row or not same(held:GetClass(), row[item_class])) then return "conflict;" .. label end
            if pawn:Check_CanUseEquipment() ~= true then return "blocked;" .. label end
            if attempted ~= guid or attempted_pawn ~= name then
                attempted, attempted_pawn = guid, name
                pawn:Equip_NextItem(slot - 1, false)
            end
            return "selecting;" .. label
        end
    end
    return "waiting;" .. label
end

local function verify_semi_fence(name, guid)
    local world, pawn = current_player()
    assert(seat == "second" and not pawn:HasAuthority() and pawn:GetFullName() == name,
        "only the exact connected guest may check the stock fire selector")
    local save, held = pawn.Save, pawn.EquipedItem
    local rows, selected = save.PlayerEquipment, save.PlayerEquipIndex
    local row = selected >= 0 and selected < rows:GetArrayNum() and rows[selected + 1] or nil
    local class = asset(weapon)
    assert(identity(row) == guid and same(row[item_class], class) and valid(held)
        and same(held:GetClass(), class) and same(held:GetOwner(), pawn) and same(held.Instigator, pawn)
        and same(held:GetWorld(), world) and held.HasSelectFire == true and held.SemiAutoSelected == false,
        "the exact selected SMG must start in stock automatic mode")
    assert(type(SilverfishProbeNativeGuestHeldFire) == "function", "the guest held-fire fixture is unavailable")
    held:ItemFireSelect(true)
    local semi = held.SemiAutoSelected == true
    local ok, result
    if semi then
        ok, result = SilverfishProbeNativeGuestHeldFire(pawn:GetAddress(), "press")
        if ok == true then SilverfishProbeNativeGuestHeldFire(pawn:GetAddress(), "release") end
        held:ItemFireSelect(true)
    end
    assert(semi and held.SemiAutoSelected == false, "stock selection did not switch to semi and back")
    assert(ok == false and result == "native_guest_held_fire_fixture_requires_automatic_mode", tostring(result))
    return "semi blocked; auto restored"
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local input = request:read(650)
    request:close()
    if not input then return end
    local ticket, action, pawn, guid = input:match("^(%x+)\t([a-z]+)\t(Player_FP_C [^%c]+)\t(%x*)$")
    if not ticket or #ticket ~= 32 or ticket == last or #pawn > 512
        or (action ~= "seed" or guid ~= "") and ((action ~= "select" and action ~= "mode") or #guid ~= 32) then return end
    last = ticket
    local handler = action == "mode" and verify_semi_fence or action == "select" and select_stock or seed
    local ok, response = pcall(handler, pawn, guid)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[%c]", " ")
    reply:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    reply:close()
end)
