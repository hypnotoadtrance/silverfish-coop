package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}
package.loaded.players = {}
local stock_keys = require("native_wire_quest")

local function ref(value) return {get = function() return value end} end
local function flag_name(value) return {ToString = function() return value end} end
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
local multi_reward = "MultiReward"
local item_path = "/Game/Classes/Items/Item_MonsterEye.Item_MonsterEye_C"
local class = object({GetFullName = function() return "BlueprintGeneratedClass " .. item_path end})
class.GetCDO = function() return object({Stacks = true, GetClass = function() return class end}) end
local row_item = {[item_class] = class, [item_units] = 2, [item_guid] = {A = 1, B = 2, C = 3, D = 4}}
local spare_stack = {[item_class] = class, [item_units] = 1, [item_guid] = {A = 5, B = 6, C = 7, D = 8}}
local portion = {[item_class] = class, [item_units] = 1, [item_guid] = {A = 9, B = 8, C = 7, D = 6}}
local want_guid = "00000001000000020000000300000004"
local split_guid = "00000009000000080000000700000006"
local host_stack = {[item_class] = class, [item_units] = 3, [item_guid] = {A = 4, B = 6, C = 8, D = 10}}

local function quest(at, class_value, payout, flag_values, rep_values)
    return {
        [step] = 1, [final] = 2, [cash] = payout, [xp] = 1, [objective] = array({class_value}),
        [consumed] = class_value ~= nil, [redeem] = at, [kind] = 2, [items] = array({}),
        [flags] = map(flag_values or {}), [chained] = array({}), [rep] = map(rep_values or {}),
        [ping] = "None", [track] = flag_name("None"),
    }
end

local eye_recipe, kill_recipe = quest(2, class, 801), quest(0, nil, 3000, {BountyDone = 1}, {[8] = 20})
local complex_recipe = quest(2, class, 100)
complex_recipe[items] = array({class})
complex_recipe[chained] = array({eye})
local store = object({ActiveQuests = map({}), PlayerBackpack = array({}), PlayerEquipment = array({})})
local player = object({Save = store})
local bag = object({PlayerBackpack = array({}), PlayerEquipment = array({})})
local guest = object({Save = bag})
local world = object({})
local host_flags = {Player_Cash = 17, Player_Xp = 3, Rep_Freelancer = 2}
local stock_pings = {}
store.Flags = {
    Contains = function(_, key) return host_flags[key:ToString()] ~= nil end,
    Find = function(_, key) return ref(host_flags[key:ToString()]) end,
}
local game_instance = object({SaveSlot_Ref = store})
game_instance.FindPassiveEffect = function(_, _, _, out) out.EffectActive, out.Instances = true, 1 end
game_instance.AddQuest_ByName = function(_, key)
    store.ActiveQuests[key] = assert(({[eye] = eye_recipe, [kill] = kill_recipe, [multi_reward] = complex_recipe})[key])
end
local board = object({TurnIn_ID = 2, RedeemableQuests = array({}), BountyBoard_RedeemableHere = true,
    AcceptAllGlobalContracts = false})
local fresh = false
board.Configure_Stock = function(self)
    self.Contracts = map({})
    if fresh and not store.ActiveQuests[eye] then self.Contracts[eye] = eye_recipe end
    if fresh and not store.ActiveQuests[kill] then self.Contracts[kill] = kill_recipe end
    if fresh and not store.ActiveQuests[multi_reward] then self.Contracts[multi_reward] = complex_recipe end
end

package.loaded.native_wire_quest = {
    stock_quest_id = stock_keys.stock_quest_id,
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
    if contract[objective]:GetArrayNum() > 0 and contract[consumed] then
        assert(store.PlayerBackpack[1] == portion)
        store.PlayerBackpack = array({})
    else
        assert(require("native_quest_catalog").tracked_step(store, contract) >= contract[final])
    end
    contract[step] = -10
    host_flags.Player_Cash = host_flags.Player_Cash + contract[cash]
    host_flags.Player_Xp = host_flags.Player_Xp + contract[xp]
    for flag, number in pairs(contract[flags]) do host_flags[flag] = number end
    for _, number in pairs(contract[rep]) do host_flags.Rep_Freelancer = host_flags.Rep_Freelancer + number * 2 end
    if contract[ping] ~= "None" then stock_pings[#stock_pings + 1] = contract[ping] end
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
function SilverfishRedeemBoardObjective(pawn, source, dest, guid, quest_id, path, limit, payout, experience, units)
    assert(pawn == guest:GetAddress() and source == bag:GetAddress() and dest == store:GetAddress())
    assert(guid == want_guid and quest_id == eye and path == item_path and limit == 2 and payout == 801 and experience == 1)
    assert(units == 2 and bag.PlayerBackpack[1] == row_item)
    row_item[item_units] = 1
    local host_pack = store.PlayerBackpack
    assert(host_pack[1] == host_stack)
    store.PlayerBackpack = array({portion})
    local result = {}
    stock_finalize(nil, quest_id, game_instance, true, player, result)
    store.PlayerBackpack = host_pack
    assert(result.Success)
    return true, "redeemed", split_guid
end

local mod = require("native_board_contract")
local state = {world = world, map = "ShopKeep"}
local inspection
local function proof()
    return mod.rows(state, "hosting", table.pack)[1]
end

local function action(verb, mode, id, guid, units)
    local arguments = {"1", "native-board-contract", verb, mode, "Guest", "Board", id}
    if mode == "apply" then arguments[8], arguments[9], arguments[10] = guid, units or "-", inspection end
    local response = mod.command(arguments, state, "hosting")
    local row = proof()
    if mode == "inspect" and row[8] == "qualified" then
        inspection = table.concat({row[9], row[11], row[16], row[17], row[20]}, "|")
    end
    return response
end
local function denied(reason, verb, mode, id, guid, units)
    local ok, failure = pcall(action, verb, mode, id, guid, units)
    assert(not ok and failure == "stock_board_contract_" .. reason, failure)
end
denied("not_offered", "start", "inspect", eye)
fresh = true
local function catalog(specialist, excluded)
    mod.command({"1", "native-board-contract-catalog", "Guest", "Board", specialist, excluded}, state, "hosting")
    return proof()
end
local offered = "Asn_EggHead,Clt_Eye_02,MultiReward"
assert(catalog()[7] == offered and catalog()[8] == "-")
assert(catalog("-", kill)[7] == offered)
assert(catalog(kill, kill)[7] == offered)
action("start", "inspect", multi_reward)
assert(proof()[8] == "qualified" and proof()[11] == 1)
action("start", "apply", multi_reward, "-")
assert(proof()[8] == "applied" and proof()[10] == 1 and proof()[13] == 17 and proof()[15] == 3)
assert(store.ActiveQuests[multi_reward] == complex_recipe)
denied("specialized_recipe", "turnin", "inspect", multi_reward)
assert(catalog()[7] == "Asn_EggHead,Clt_Eye_02" and catalog()[8] == "-")
action("start", "inspect", eye)
assert(proof()[8] == "qualified" and proof()[9] == "-" and proof()[11] == 1)
action("start", "apply", eye, "-")
assert(proof()[8] == "applied" and proof()[10] == 1)
denied("missing_objective", "turnin", "inspect", eye)
store.PlayerBackpack[1] = row_item
denied("missing_objective", "turnin", "inspect", eye)
store.PlayerBackpack = array({host_stack})
bag.PlayerBackpack[1] = row_item
bag.PlayerBackpack[2] = row_item
assert(catalog()[8] == "-")
denied("objective_guid_unqualified", "turnin", "inspect", eye)
bag.PlayerBackpack[2] = spare_stack
assert(catalog()[7] == kill and catalog()[8] == eye)
board.TurnIn_ID = 1
assert(catalog()[8] == "-")
denied("wrong_turnin", "turnin", "inspect", eye)
board.TurnIn_ID = 2
eye_recipe[ping] = "Coup_HuntPlayer"
assert(catalog()[8] == "-")
denied("specialized_recipe", "turnin", "inspect", eye)
eye_recipe[ping] = "None"
action("turnin", "inspect", eye)
assert(proof()[18] == want_guid and proof()[16] == 801 and proof()[9] == 1)
denied("objective_changed", "turnin", "apply", eye, "-")
denied("objective_changed", "turnin", "apply", eye, want_guid, "1")
denied("objective_changed", "turnin", "apply", eye, "00000005000000060000000700000008", "1")
eye_recipe[cash] = 802
denied("inspection_changed", "turnin", "apply", eye, want_guid, "2")
assert(eye_recipe[step] == 1 and host_flags.Player_Cash == 17 and row_item[item_units] == 2)
eye_recipe[cash] = 801
action("turnin", "apply", eye, want_guid, "2")
assert(proof()[10] == -10 and proof()[13] == 818 and #store.PlayerBackpack == 1)
assert(store.PlayerBackpack[1] == host_stack and host_stack[item_units] == 3)
assert(proof()[18] == want_guid and proof()[19] == 2 and proof()[20] == 1 and proof()[21] == split_guid)
assert(#bag.PlayerBackpack == 2 and bag.PlayerBackpack[1] == row_item and row_item[item_units] == 1)
assert(bag.PlayerBackpack[2] == spare_stack and spare_stack[item_units] == 1)
action("turnin", "inspect", eye)
assert(proof()[8] == "already" and host_flags.Player_Cash == 818)

action("start", "inspect", kill)
action("start", "apply", kill, "-")
denied("wrong_turnin", "turnin", "inspect", kill)
board.AcceptAllGlobalContracts = true
kill_recipe[track] = flag_name("TrackedKills")
denied("objective_not_complete", "turnin", "inspect", kill)
host_flags.TrackedKills = 1
denied("objective_not_complete", "turnin", "inspect", kill)
host_flags.TrackedKills = 3
kill_recipe[items] = array({class})
assert(catalog()[8] == "-" and catalog(kill)[8] == kill)
denied("specialized_recipe", "turnin", "inspect", kill)
kill_recipe[items] = array({})
kill_recipe[ping] = "Coup_HuntPlayer"
action("turnin", "inspect", kill)
assert(proof()[18] == "-" and proof()[16] == 3000 and proof()[9] == 1)
action("turnin", "apply", kill, "-")
assert(proof()[13] == 3818 and host_flags.BountyDone == 1 and host_flags.Rep_Freelancer == 42)
assert(require("native_quest_catalog").tracked_step(store, kill_recipe) == -10)
action("turnin", "apply", kill, "-")
assert(proof()[8] == "already" and #stock_pings == 1 and stock_pings[1] == "Coup_HuntPlayer")
assert(host_flags.Player_Cash == 3818)

local photo = "Photo_BrnzQuarry"
local photo_recipe = quest(0, nil, 100)
photo_recipe[consumed], photo_recipe[step] = true, 2
store.ActiveQuests[photo] = photo_recipe
assert(catalog()[8] == "-")
denied("specialized_recipe", "turnin", "inspect", photo)
assert(photo_recipe[step] == 2 and host_flags.Player_Cash == 3818)
assert(store.PlayerBackpack[1] == host_stack and bag.PlayerBackpack[1] == row_item and #stock_pings == 1)

local missing, display = "Note_Farmhouse", "Note_FarmHouse"
local configure = board.Configure_Stock
local missing_recipe = quest(2, class, 450)
missing_recipe[items] = array({class}) -- A specialist recipe cannot use the ordinary turn-in path.
board.Configure_Stock = function(self)
    configure(self)
    if not store.ActiveQuests[display] then self.Contracts[display] = missing_recipe end
end
assert(catalog(missing)[7] == missing)
store.ActiveQuests[display] = missing_recipe
assert(catalog()[8] == "-")
assert(catalog(missing)[7] == "-" and catalog(missing)[8] == missing)
function FName(text)
    return {GetComparisonIndex = function() return 1 end,
        ToString = function() return text == missing and display or text end}
end
assert(stock_keys.stock_quest_key(missing):ToString() == display)
assert(stock_keys.stock_quest_id("Other_Quest") == "Other_Quest")
print("Stock board live-row qualification and settlement passed")
