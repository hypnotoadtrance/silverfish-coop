-- Run with Lua 5.4 from the repository root.
local names, flags = {}, {Player_Cash = 30, Player_Xp = 0}
local name_type = {}
local function name(value)
    return setmetatable({GetComparisonIndex = function() return value == "None" and 0 or 1 end,
        ToString = function() return value end}, name_type)
end
for _, value in ipairs({"Sub_Valve", "Player_Cash", "Player_Xp"}) do names[value] = name(value) end

local function array(values)
    values.GetArrayNum = function(self) return #self end
    return values
end
local function map(values, size)
    return setmetatable({Contains = function(_, key) return values[key] ~= nil end,
        Find = function(_, key) return {get = function() return values[key] end} end},
        {__len = function() return size end})
end
local function object(label)
    local result = {IsValid = function() return true end, GetFullName = function() return label end}
    result.GetAddress = function() return result end
    return result
end
local map_path = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local chars = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local library = object("FL_Characters_C " .. chars)
local artifact, helmet = object("artifact"), object("helmet")
local host, world, save, game, board = object("host"), object("world"), object("save"), {}, {}
save.ActiveQuests = {}
host.Save, host.GetWorld, game.SaveSlot_Ref = save, function() return world end, save
local step = 1
local function stock_row(active)
    return setmetatable({}, {__index = function(_, key)
        local fields = {Current_Step = active and step or 1, Objective_FinalStep = 2, Reward_Cash = 0, Reward_PlayerXp = 1, QuestType = 1,
        Difficulty = 7, Fail_OnDeath = false, FailPenalty = 100, AppearOn_GlobalBountyBoards = true, RedeemAt = 5,
        AppearOn_Boards = array({5}), ObjectiveItemsAreConsumed = false, Objective_Items = array({}),
        Add_Quests_OnComplete = array({}), Set_FlagsOnComplete = {}, PreReq_Flags = {}, Advantage_Faction = {},
        TrackFlag_ForStep = name("None"), ReputationGain = map({[2] = 150, [4] = -40, [5] = -50}, 3),
        Reward_Items = array({artifact, helmet})}
        return fields[key:match("^(.-)_%d+_")]
    end})
end
local row = stock_row(false)
local board_name = "BountyBoard_BP_C " .. map_path .. ".Borough:PersistentLevel.BountyBoard_BP_C_0"
local state = {world = world, map = map_path}
local grants, configured, wrong_faction_flag = 0, 0, false
local rep_names = {[2] = "Rep_Commune", [4] = "Rep_State", [5] = "Rep_Coup"}
function library:Compose_FactionRep_FlagName(id, pawn, result)
    assert(pawn == host and rep_names[id])
    local value = wrong_faction_flag and id == 2 and "Rep_Freelancer" or rep_names[id]
    names[value] = name(value)
    result.Name = names[value]
end
function library:Compose_FactionAdvantage_FlagName(id, pawn, result)
    assert(id == 2 and pawn == host)
    names.Adv_Commune = name("Adv_Commune")
    result.Name = names.Adv_Commune
end
function game:GivePlayer_XP(delta)
    assert(delta == 7 and flags.Player_Xp == 0)
    grants, flags.Player_Xp = grants+1, flags.Player_Xp+delta
end
function board:Configure_Stock()
    configured = configured+1
    self.Contracts = flags.Player_Xp >= 7 and {Sub_Valve = row} or {}
end
local wire = {map = map_path, board_name = board_name}
function wire.stock_board_context(given_state, phase, pawn, approach, profile)
    assert(given_state == state and phase == "hosting" and pawn == "host" and approach == false)
    assert(profile.quest_id == "Sub_Valve")
    return {world = world, host = host, interactor = host, receiving_save = save, save = save,
        instance = game, key = names.Sub_Valve, board = board}
end
function wire.stock_quest_table()
    return {FindRow = function(_, key) assert(key == "Sub_Valve"); return row end}
end
function wire.stock_row(rows, key)
    assert(key:ToString() == "Sub_Valve")
    return rows.Sub_Valve
end
function wire.stock_flag(instance, key)
    assert(instance == game and names[key:ToString()] == key)
    local value = flags[key:ToString()]
    return value or 0, value ~= nil
end
local function require_mocks(module) return module == "native_wire_quest" and wire or {} end
local environment = setmetatable({require = require_mocks, os = {getenv = function() return "1" end},
    StaticFindObject = function(path)
        if path == chars then return library end
        if path:match("%.Art_AcidResist_C$") then return artifact end
        if path:match("%.Helm_HazOp_C$") then return helmet end
    end,
    EFindName = {FNAME_Find = 0}, FName = function(value, mode)
        assert(mode == 0)
        return names[value] or name("None")
    end,
    type = function(value) return getmetatable(value) == name_type and "userdata" or type(value) end},
    {__index = _G})
local valve = assert(loadfile("mod/SilverfishCoop/Scripts/native_subway_valve.lua", "t", environment))()
local function command(action) return valve.command({"nonce", "native-subway-valve-" .. action, "host"}, state, "hosting") end
local function rejects(action, reason)
    local ok, result = pcall(command, action)
    assert(not ok and result == "stock_subway_valve_" .. reason, tostring(result))
end
wrong_faction_flag = true
rejects("xp-fixture", "reputation_flag_unqualified:2")
assert(grants == 0 and configured == 0)
wrong_faction_flag = false
assert(command("xp-fixture") and grants == 1 and configured == 2 and not names.SubValve_Closed)
assert(command("preflight"))
local receipt = valve.rows(state, "hosting", table.pack)[1]
assert(receipt[2] == "preflight" and receipt[8] == 0 and receipt[12] == 7)

save.ActiveQuests.Sub_Valve = stock_row(true)
assert(command("preflight"))
step = 2
rejects("preflight", "name_unavailable:SubValve_Closed")
names.SubValve_Closed, flags.SubValve_Closed = name("SubValve_Closed"), 1
assert(command("preflight"))
receipt = valve.rows(state, "hosting", table.pack)[1]
assert(receipt[6] == 2 and receipt[8] == 1 and grants == 1)
