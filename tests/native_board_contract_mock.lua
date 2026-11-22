package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function ref(value) return {get = function() return value end} end
local function array(rows)
    return setmetatable(rows, {__index = {GetArrayNum = function(self) return #self end}})
end
local function map(values)
    return setmetatable(values, {__index = {
        ForEach = function(self, read) for key, value in pairs(self) do read(ref(key), ref(value)) end end,
    }})
end
local next_address = 100
local function object(fields)
    next_address = next_address + 1
    local address = next_address
    fields.IsValid = function() return true end
    fields.GetAddress = function() return address end
    return fields
end

local step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local xp = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"
local items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local objective = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083"
local consumed = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333"
local redeem = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F"
local kind = "QuestType_47_1031681D471E9929E30B17985E174BBE"
local flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F"
local chained = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9"
local rep = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC"
local ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F"
local track = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_units = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local eye = "Clt_Eye_02"
local kill = "Asn_EggHead"
local item_path = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
local class = object({GetFullName = function() return "BlueprintGeneratedClass " .. item_path end})
class.GetCDO = function() return object({Stacks = false, GetClass = function() return class end}) end
local row_item = {[item_class] = class, [item_units] = 1, [item_guid] = {A = 1, B = 2, C = 3, D = 4}}
local want_guid = "00000001000000020000000300000004"

local function quest(at, class_value, payout, flag_values, rep_values)
    return {
        [step] = 1, [final] = 2, [cash] = payout, [xp] = 1, [objective] = array({class_value}),
        [consumed] = class_value ~= nil, [redeem] = at, [kind] = 2, [items] = array({}),
        [flags] = map(flag_values or {}), [chained] = array({}), [rep] = map(rep_values or {}),
        [ping] = "None", [track] = "None",
    }
end

local eye_recipe, kill_recipe = quest(2, class, 801), quest(0, nil, 3000, {BountyDone = 1}, {[8] = 20})
local store = object({ActiveQuests = {}, PlayerBackpack = array({}), PlayerEquipment = array({})})
local player = object({Save = store})
local bag = object({PlayerBackpack = array({}), PlayerEquipment = array({})})
local guest = object({Save = bag})
local world = object({})
local host_flags = {Player_Cash = 17, Player_Xp = 3, Rep_Freelancer = 2}
local game_instance = object({SaveSlot_Ref = store})
game_instance.FindPassiveEffect = function(_, _, _, out) out.EffectActive, out.Instances = true, 1 end
game_instance.AddQuest_ByName = function(_, key) store.ActiveQuests[key] = assert(({[eye] = eye_recipe, [kill] = kill_recipe})[key]) end
local board = object({TurnIn_ID = 2, RedeemableQuests = array({}), BountyBoard_RedeemableHere = true,
    AcceptAllGlobalContracts = false})
local fresh = false
board.Configure_Stock = function(self)
    self.Contracts = {}
    if fresh and not store.ActiveQuests[eye] then self.Contracts[eye] = eye_recipe end
    if fresh and not store.ActiveQuests[kill] then self.Contracts[kill] = kill_recipe end
end

package.loaded.native_wire_quest = {
    live_stock_board_context = function(state, phase, pawn, board_name, key)
        assert(state.world == world and phase == "hosting" and pawn == "Guest" and board_name == "Board")
        return {key = key, world = world, board = board, save = store, receiving_save = bag, host = player,
            interactor = guest, instance = game_instance}
    end,
    stock_row = function(rows, key) return rows[key] end,
    stock_flag = function(_, key) return host_flags[key] or 0, host_flags[key] ~= nil end,
}
local function stock_finalize(_, key, instance, no_confirmation, host, out)
    assert(instance == game_instance and host == player and no_confirmation)
    local contract = assert(store.ActiveQuests[key])
    if contract[consumed] then
        assert(store.PlayerBackpack[1] == row_item)
        store.PlayerBackpack = array({})
    else
        assert(contract[step] >= contract[final])
    end
    contract[step] = -10
    host_flags.Player_Cash = host_flags.Player_Cash + contract[cash]
    host_flags.Player_Xp = host_flags.Player_Xp + contract[xp]
    for flag, number in pairs(contract[flags]) do host_flags[flag] = number end
    for _, number in pairs(contract[rep]) do host_flags.Rep_Freelancer = host_flags.Rep_Freelancer + number * 2 end
    out.Success = true
end

function StaticFindObject(path)
    if path:match("/FL_Characters%.") then
        return object({GetFullName = function() return "FL_Characters_C " .. path end,
            Compose_FactionRep_FlagName = function(_, faction, host, out)
                assert(faction == 8 and host == player)
                out.Name = "Rep_Freelancer"
            end})
    end
    assert(path:match("/FL_CodeLib01%."))
    return object({GetFullName = function() return "FL_CodeLib01_C " .. path end, TurnIn_Quest = stock_finalize})
end
EFindName = {FNAME_Find = 1}
function FName(text) return text end
function SilverfishStageBoardObjective(pawn, source, dest, guid, quest_id, path, limit, payout, experience)
    assert(pawn == guest:GetAddress() and source == bag:GetAddress() and dest == store:GetAddress())
    assert(guid == want_guid and quest_id == eye and path == item_path and limit == 2 and payout == 801 and experience == 1)
    store.PlayerBackpack[1] = table.remove(bag.PlayerBackpack, 1)
    return true
end

local mod = require("native_board_contract")
local state = {world = world, map = "ShopKeep"}
local function action(verb, mode, id, guid)
    return mod.command({"1", "native-board-contract", verb, mode, "Guest", "Board", id, guid}, state, "hosting")
end
local function denied(reason, verb, mode, id, guid)
    local ok, failure = pcall(action, verb, mode, id, guid)
    assert(not ok and failure == "stock_board_contract_" .. reason, failure)
end
local function proof()
    return mod.rows(state, "hosting", table.pack)[1]
end

denied("not_offered", "start", "inspect", eye)
fresh = true
action("start", "inspect", eye)
assert(proof()[8] == "qualified" and proof()[9] == "-" and proof()[11] == 1)
action("start", "apply", eye, "-")
assert(proof()[8] == "applied" and proof()[10] == 1)
denied("missing_objective", "turnin", "inspect", eye)
store.PlayerBackpack[1] = row_item
denied("missing_objective", "turnin", "inspect", eye)
store.PlayerBackpack = array({})
bag.PlayerBackpack[1] = row_item
board.TurnIn_ID = 1
denied("wrong_turnin", "turnin", "inspect", eye)
board.TurnIn_ID = 2
action("turnin", "inspect", eye)
assert(proof()[18] == want_guid and proof()[16] == 801 and proof()[9] == 1)
denied("objective_changed", "turnin", "apply", eye, "-")
action("turnin", "apply", eye, want_guid)
assert(proof()[10] == -10 and proof()[13] == 818 and #bag.PlayerBackpack == 0 and #store.PlayerBackpack == 0)
action("turnin", "inspect", eye)
assert(proof()[8] == "already" and host_flags.Player_Cash == 818)

action("start", "apply", kill, "-")
denied("wrong_turnin", "turnin", "inspect", kill)
board.AcceptAllGlobalContracts = true
denied("objective_not_complete", "turnin", "inspect", kill)
kill_recipe[step] = 2
kill_recipe[items] = array({class})
denied("specialized_recipe", "turnin", "inspect", kill)
kill_recipe[items] = array({})
action("turnin", "inspect", kill)
assert(proof()[18] == "-" and proof()[16] == 3000 and proof()[9] == 2)
action("turnin", "apply", kill, "-")
assert(proof()[13] == 3818 and host_flags.BountyDone == 1 and host_flags.Rep_Freelancer == 42)
print("Stock board live-row qualification and settlement passed")
