local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local world_path = map .. ".CrossRoads_P"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local slot = "SFCoop_" .. string.rep("1", 32)
local escrow, owned, spade = string.rep("A", 32), string.rep("B", 32), string.rep("C", 32)
local actor = "Package_Item_FarmHouse_C " .. world_path .. ":PersistentLevel.Package_Item_FarmHouse_C_2147480001"
local contact = {1665.2142939210705, 5811.305953931523, 339.15110679941967}
local dirt = "PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/PM_Dirt.PM_Dirt"
local identity, index = {}, 0

local function object(name, class)
    index = index + 1
    local address = index
    return {
        GetAddress = function() return address end,
        GetFullName = function() return name end,
        GetClass = function() return identity[class] end,
        IsValid = function() return true end,
        HasAuthority = function() return true end,
    }
end

for _, class in ipairs({pawn_class, save_class, instance_class}) do identity[class] = object(class) end
local world = object("World " .. world_path)
local pawn = object("Player_FP_C " .. world_path .. ":PersistentLevel.Player_FP_C_1", pawn_class)
local save, instance = object("canonical save", save_class), object("instance", instance_class)
pawn.GetWorld, pawn.Save = function() return world end, save
instance["As Player FP"], instance.SaveSlot_Ref = pawn, save
local statics = object("statics")
identity[statics_path] = statics
function statics:GetGameInstance(p) assert(p == pawn); return instance end
local staged
function statics:CreateSaveGameObject(class)
    assert(class == identity[save_class])
    staged = object("scratch", save_class)
    return staged
end
function statics:SaveGameToSlot(object, name, user)
    assert(object == staged and name == slot and user == 0)
    return true
end
function statics:LoadGameFromSlot(name, user)
    assert(staged and name == slot and user == 0)
    return object("readback", save_class)
end

local role = {map = map, world = world, listening = true, server = false,
    local_pawn = {pawn = pawn, name = pawn:GetFullName()}}
local phase, quest_step, renews, captured = "ready", nil, 0, false
local material, package_name, package_guid = "", "", ""
local heartbeat, serial, fail_ack = 100, 0, false
local env = setmetatable({
    os = {getenv = function() return "1" end, time = function() return 100 end},
    io = {open = function(path, mode)
        assert(path == "bridge/native-host-dig-live-heartbeat" and mode == "rb")
        return {read = function() return tostring(heartbeat) end, close = function() end}
    end},
    require = function(name)
        assert(name == "native_wire_quest")
        return {stock_row = function() return quest_step and {[step_field] = quest_step} end}
    end,
    StaticFindObject = function(path) return identity[path] end,
    EFindName = {FNAME_Find = 1},
    FName = function(name, mode)
        assert(name == "Note_Farmhouse" and mode == 1)
        return {GetComparisonIndex = function() return 9 end, ToString = function() return "note_farmhouse" end}
    end,
    SilverfishRenewNativeHostDigScope = function(w, p)
        assert(w == world:GetAddress() and p == pawn:GetAddress())
        renews = renews + 1
        return true
    end,
    SilverfishReadNativeHostDigScope = function(w, p)
        assert(w == world:GetAddress() and p == pawn:GetAddress())
        return phase, serial, phase == "ready" and "" or spade, package_name, package_guid,
            contact[1], contact[2], contact[3], material, 0, captured and escrow or "", ""
    end,
    SilverfishCaptureNativeHostDigEscrow = function(w, p, sequence, address)
        assert(w == world:GetAddress() and p == pawn:GetAddress() and sequence == serial and address == staged:GetAddress())
        captured = true
        return true, "captured", escrow
    end,
    SilverfishVerifyNativeHostDigEscrow = function(_, _, sequence, address, guid)
        return sequence == serial and address ~= staged:GetAddress() and guid == escrow
    end,
    SilverfishAcknowledgeNativeHostDig = function(_, _, sequence, _, guid)
        assert(sequence == serial and guid == escrow)
        return not fail_ack, "stock_live_dig_commit_changed"
    end,
    SilverfishRestoreNativeHostDigEscrow = function(_, _, address, expected_escrow, expected_spade, x, y, z, surface)
        assert(address ~= staged:GetAddress() and expected_escrow == escrow and expected_spade == spade)
        assert(x == contact[1] and y == contact[2] and z == contact[3] and surface == dirt)
        phase = "escrowed"
        return true
    end,
}, {__index = _G})

local dig = assert(loadfile("mod/SilverfishCoop/Scripts/native_host_dig_live.lua", "t", env))()
dig.start("bridge/")
local function sync() dig.sync(role, "hosting") end
local function rows() return dig.rows(role, "hosting", table.pack) end
local function allowed(token) return dig.capture_allowed(role, "hosting", token) end
local function command(verb, ...)
    return dig.command({"nonce", "native-host-dig-live-" .. verb, pawn:GetFullName(), ...}, role, "hosting")
end

sync()
assert(renews == 1 and allowed() and rows()[1][4] == "ready" and rows()[1][5] == 0)
heartbeat, quest_step = 97, 1
sync()
assert(renews == 1)
heartbeat, quest_step = 100, -10
sync()
assert(renews == 1)
phase, serial, material, quest_step = "contact", 4, dirt, 1
sync()
assert(not allowed())
phase = nil
sync()
assert(not allowed())
phase, package_name = "unclaimed", actor
assert(not allowed("dig@4@escrowed@" .. escrow))
assert(command("escrow", "4", slot))
local record = rows()[1]
assert(record.n == 16 and record[4] == "unclaimed" and record[7] == actor and record[15] == slot)
assert(allowed("dig@4@escrowed@" .. escrow))
assert(not allowed("dig@5@escrowed@" .. escrow) and not allowed())
phase, fail_ack = "collecting", true
assert(not allowed("dig@4@escrowed@" .. escrow))
assert(command("ack", "4", "escrowed", escrow))
phase, package_guid = "collected", owned
assert(not allowed("dig@4@escrowed@" .. escrow) and allowed("dig@4@owned@" .. owned))
quest_step = -10
assert(not allowed("dig@4@owned@" .. owned))
phase, quest_step = "owned", 1
assert(allowed() and not allowed("dig@4@owned@" .. owned))
dig.clear()
assert(command("restore", slot, escrow, spade, contact[1], contact[2], contact[3], dirt))
assert(rows()[1][4] == "escrowed" and allowed())
assert(not pcall(command, "restore", slot, escrow, spade, contact[1] + 200, contact[2], contact[3], dirt))
role.server = true
assert(#rows() == 0 and not pcall(command, "escrow", "4", slot))
