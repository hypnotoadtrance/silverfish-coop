package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local area = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local level = area .. ".Shop_Scientist_01"
local board_path = level .. ":PersistentLevel.BountyBoard_BP_C_2"
local station_path = level .. ":PersistentLevel.DataTape_DownloadStation_BP_C_1"
local station_class = "/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C"
local objective_path = "/Game/Classes/Items/Artifacts/Art_DrillHead.Art_DrillHead_C"
local armor_path = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
local tape_path = "/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C"
local code_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local function guid(value) return string.format("%08X", value) .. string.rep("0", 24) end
local function object(name, class)
    local value = {}
    function value:IsValid() return true end
    function value:GetClass() return class end
    function value:GetAddress() return self end
    function value:GetFullName() return name end
    function value:HasAuthority() return true end
    function value:IsActorBeingDestroyed() return false end
    return value
end
local function array(rows)
    function rows:GetArrayNum() return #self end
    return rows
end
local function typed(class, value) return {[item_field] = class, [guid_field] = {A = value, B = 0, C = 0, D = 0}} end
local assets = {}
local function stock(path)
    if not assets[path] then assets[path] = object(path) end
    return assets[path]
end
StaticFindObject = stock
EFindName = {FNAME_Find = 0}
FName = function(value)
    return {name = value, GetComparisonIndex = function() return 1 end}
end

local world, host, guest = object("World " .. level), object("Player_FP_C Host"), object("Player_FP_C Guest")
local instance = object("GameInstance")
local function save()
    local value = object("SaveGame_BP_C stock", stock(save_path))
    value.PlayerBackpack, value.PlayerEquipment = array({}), array({})
    value.PlayerArmor, value.PlayerHelmet = {}, {}
    return value
end
local host_save, guest_save = save(), save()
host.Save, guest.Save, instance.SaveSlot_Ref, instance["As Player FP"] = host_save, guest_save, host_save, host
local board = object("BountyBoard_BP_C " .. board_path)
local station = object("DataTape_DownloadStation_BP_C " .. station_path, stock(station_class))
local script = object("Scientist level", stock(level .. "_C"))
function station:GetWorld() return world end
function script:GetWorld() return world end
script["As Game Instance FP"], script.PlayerIsWelcome = instance, true
world.PersistentLevel, world.NetDriver = {LevelScriptActor = script}, object("Net Driver")
world.NetDriver.World = world
assets[station_path] = station
local state = {world = world, map = area, listening = true, server = false,
    pawns = {{name = host:GetFullName(), pawn = host}, {name = guest:GetFullName(), pawn = guest}}}
local objective, armor, tape = stock(objective_path), stock(armor_path), stock(tape_path)
local prior_tape = stock("/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C")
local canonical = {host = host, instance = instance, save = host_save, world = world}
local progress, flags, events, listeners, tape_mutation, slots
local function reset()
    progress = {step = -10, prototype = 1, Topic_MicroDrill = 1, Mosby_SM_Readings = 2,
        Mosby_DrillTest = 0, cash = 700, xp = 10, Topic_DrillLab = 0}
    flags = {rep2 = 80, rep4 = -100, adv2 = 12}
    events, slots = {}, {}
    listeners, tape_mutation = 1, true
    host_save.PlayerBackpack, guest_save.PlayerBackpack = array({typed(armor, 9)}), array({typed(objective, 1)})
    station.Tapes_DataBase, script.DrillTested = array({prior_tape}), false
end

package.loaded.native_dialogue_mosby = {map = area, world = level,
    progress = function()
        local copy = {}
        for key, value in pairs(progress) do copy[key] = value end
        return copy, canonical
    end}
package.loaded.native_wire_quest = {
    stock_board_context = function(actual, phase, name, approach, profile)
        assert(actual == state and phase == "hosting" and not approach)
        assert(profile.board_path == board_path and profile.board_id == 4 and profile.quest_id == "Clt_DrillHead")
        local receiver = name == host:GetFullName() and host or guest
        assert(receiver:GetFullName() == name)
        return {world = world, host = host, instance = instance, save = host_save, interactor = receiver,
            receiving_save = receiver.Save, board = board, key = FName(profile.quest_id)}
    end,
    stock_flag = function(actual, key)
        assert(actual == instance)
        return flags[key.name] or 0, flags[key.name] ~= nil
    end,
}
package.loaded.guest_save = {exact_item_context = function(actual, phase, name)
    assert(actual == state and phase == "hosting" and name == guest:GetFullName())
    return guest, guest_save
end}
package.loaded.authored_zone_maps = {[area] = {}}

assets[code_path] = object("FL_CodeLib01_C " .. code_path)
assets[code_path].TurnIn_Quest = function(_, key, game, confirmed, pawn, out, copy)
    assert(key.name == "Clt_DrillHead" and game == instance and confirmed == true and pawn == host and out == copy)
    assert(host_save.PlayerBackpack[2][item_field] == objective and #guest_save.PlayerBackpack == 0)
    assert(progress.prototype == 1 and progress.Mosby_DrillTest == 0)
    events[#events + 1] = "stock turn-in"
    host_save.PlayerBackpack[2] = typed(armor, 11)
    progress.prototype, progress.Mosby_DrillTest, progress.cash, progress.xp = -10, 1, 10700, 13
    flags.rep2, flags.rep3, flags.rep4 = 680, -200, -500
    out.Success = true
end

assets[character_path] = object("FL_Characters_C " .. character_path)
assets[character_path].Compose_FactionRep_FlagName = function(_, faction, pawn, out)
    assert(pawn == host)
    out.Name = FName("rep" .. faction)
end
assets[character_path].Compose_FactionAdvantage_FlagName = function(_, faction, pawn, out)
    assert(pawn == host)
    out.Name = FName("adv" .. faction)
end
function instance:FindPassiveEffect(key, equipped, out, copy)
    assert(key.name == "IntensePersonality" and equipped and out == copy)
    out.EffectActive, out.Instances = true, 1
end
function board:Configure_Stock()
    assert(progress.prototype == -10 and script.DrillTested == false)
    events[#events + 1] = "stock board refresh"
end
SilverfishPrototypeDrillBoard = function(pawn, save_ref, board_ref, level_ref, action)
    assert(pawn == host and save_ref == host_save and board_ref == board and level_ref == script)
    if listeners ~= 1 then return false, "prototype_drill_board_exact_listener_unqualified" end
    if action == "check" then
        assert(progress.prototype == 1 and #station.Tapes_DataBase == 1)
        return true, "verified"
    end
    assert(action == "broadcast" and progress.prototype == -10 and script.DrillTested == false)
    assert(events[#events] == "stock board refresh")
    events[#events + 1] = "stock delegate broadcast"
    script.DrillTested = true
    if tape_mutation then station.Tapes_DataBase[2] = tape end
    return true, "broadcast"
end
SilverfishStagePrototypeDrillObjective = function(pawn, detached, save_ref, id)
    assert(pawn == guest and detached == guest_save and save_ref == host_save and id == guid(1))
    events[#events + 1] = "verified custody handoff"
    host_save.PlayerBackpack[2] = table.remove(detached.PlayerBackpack, 1)
    return true, "moved"
end
local statics = object("GameplayStatics")
assets["/Script/Engine.Default__GameplayStatics"] = statics
function statics:DoesSaveGameExist(slot, index)
    assert(index == 0)
    return slots[slot] ~= nil
end
function statics:CreateSaveGameObject(class)
    assert(class == stock(save_path))
    return save()
end
function statics:SaveGameToSlot(scratch, slot, index)
    assert(index == 0 and #scratch.PlayerBackpack == 1)
    local loaded = save()
    table.insert(loaded.PlayerBackpack, scratch.PlayerBackpack[1])
    slots[slot] = loaded
    return true
end
function statics:LoadGameFromSlot(slot, index)
    assert(index == 0)
    return slots[slot]
end
SilverfishCapturePrototypeDrillReward = function(pawn, save_ref, scratch, id)
    assert(pawn == host and save_ref == host_save and id == guid(11))
    table.insert(scratch.PlayerBackpack, typed(armor, 11))
    return true, "captured"
end
SilverfishVerifyPrototypeDrillReward = function(loaded, id, original)
    assert(id == guid(11) and #loaded.PlayerBackpack == 1 and loaded.PlayerBackpack[1][item_field] == armor)
    if original then assert(original ~= loaded and original.PlayerBackpack[1][guid_field].A == 11) end
    return true, "verified"
end
SilverfishGrantPrototypeDrillReward = function(pawn, detached, save_ref, loaded, id, assigned)
    assert(pawn == guest and detached == guest_save and save_ref == host_save and id == guid(11) and assigned == guid(12))
    assert(loaded.PlayerBackpack[1][guid_field].A == 11)
    if detached.PlayerBackpack[1] then
        assert(#detached.PlayerBackpack == 1 and detached.PlayerBackpack[1][guid_field].A == 12)
        return true, "already_granted"
    end
    table.insert(detached.PlayerBackpack, typed(armor, 12))
    return true, "granted"
end

local getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" or name == "SILVERFISH_COOP_NATIVE_GUEST_SAVE" then return "1" end
    return getenv(name)
end
local drill = require("native_prototype_drill_turnin")
os.getenv = getenv
local who = guest:GetFullName()
local function command(action, ...)
    return drill.command({"request", "native-prototype-drill-" .. action, ...}, state, "hosting")
end
local function rows() return drill.rows(state, "hosting", table.pack) end
local function refuses(reason, action, ...)
    local ok, error_text = pcall(command, action, ...)
    assert(not ok and error_text == reason, tostring(error_text))
    assert(#rows() == 0)
end

reset()
assert(command("preflight", who) and #events == 0)
local before = rows()
assert(#before == 4 and before[1].n == 23 and before[1][7] == guid(1) and before[1][8] == "guest")
assert(before[1][10] == 1 and before[1][11] == 1 and before[1][19] == 0 and before[1][22] == 0 and before[1][23] == 1)
assert(before[2].n == 10 and before[2][2] == 2 and before[4][2] == 4)
refuses("native_prototype_drill_exact_objective_required", "turn-in", who, guid(2))
assert(#events == 0 and #guest_save.PlayerBackpack == 1)
listeners = 2
refuses("prototype_drill_board_exact_listener_unqualified", "turn-in", who, guid(1))
assert(#events == 0 and #guest_save.PlayerBackpack == 1)
listeners = 1
assert(command("turn-in", who, guid(1)))
assert(table.concat(events, ",") == "verified custody handoff,stock turn-in,stock board refresh,stock delegate broadcast")
local after = rows()
assert(after[1][9] == guid(11) and after[1][10] == 1 and after[1][11] == -10)
assert(after[1][12] == 700 and after[1][13] == 10700 and after[1][14] == 10 and after[1][15] == 13)
assert(after[1][16] == 0 and after[1][17] == 1 and after[1][18] == 0 and after[1][19] == 1)
assert(after[1][20] == "DataTape_DownloadStation_BP_C " .. station_path and after[1][21] == 0 and after[1][22] == 1)
assert(after[2][5] == 680 and after[2][7] == 12 and after[2][9] == 12)
assert(after[3][3] == 0 and after[3][4] == 0 and after[3][5] == -200 and after[3][6] == 1)
assert(#guest_save.PlayerBackpack == 0)
refuses("native_prototype_drill_exact_objective_required", "turn-in", who, guid(1))
assert(#events == 4 and #station.Tapes_DataBase == 2)

local slot = "SFCoop_" .. string.rep("c", 32)
assert(command("reward-capture", slot, guid(11)))
assert(rows()[1].n == 7 and rows()[1][4] == "captured" and #host_save.PlayerBackpack == 2)
assert(command("reward-grant", slot, guid(11), who, guid(12)))
assert(rows()[1][4] == "granted" and rows()[1][7] == guid(12))
assert(command("reward-grant", slot, guid(11), who, guid(12)))
assert(rows()[1][4] == "already_granted" and #guest_save.PlayerBackpack == 1 and #host_save.PlayerBackpack == 2)

reset()
tape_mutation = false
refuses("mutation_unverified_prototype_drill_redemption", "turn-in", who, guid(1))
assert(#events == 4 and progress.prototype == -10)
print("Prototype Drill stock redemption, delegate, escrow and refusal mock passed")
