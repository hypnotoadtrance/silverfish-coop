local contract = {}
local wire = require("native_wire_quest")

local library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local characters_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local boards = {
    [1] = {map = black_market, actor = "BountyBoard_BP_C_0", global_contracts = true},
    [4] = {map = scientist, actor = "BountyBoard_BP_C_2"},
    [5] = {map = wire.map, actor = "BountyBoard_BP_C_3"},
    [10] = {map = scav_bar, actor = "BountyBoard_BP_C_2", global_contracts = true},
}
local recipes = {
    ["native-computer-board"] = {
        id = "Clt_ComputerBoard", title = "Computer Board", fault = "computer_board",
        objective_path = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C",
        cash = 800, difficulty = 0, fail_on_death = false, redeem = 4,
        offered_boards = {4, 5}, factions = {2, 4, 5}, reputation = {[2] = 50, [4] = -5, [5] = -5},
        advantage = {[2] = 35}, redeem_map = scientist, wrong_board = "scientist_redemption_required",
        stage_objective = "SilverfishStageComputerBoardObjective",
    },
    ["native-coup-report"] = {
        id = "Clt_CoupReport_01", title = "Northern Passage", fault = "coup_report",
        objective_path = "/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C",
        cash = 1200, difficulty = 3, fail_on_death = false, redeem = 5,
        offered_boards = {5, 4}, factions = {2, 5}, reputation = {[2] = 60, [5] = -100},
        advantage = {[2] = 50}, redeem_map = wire.map, wrong_board = "borough_redemption_required",
        stage_objective = "SilverfishStageCoupReportObjective",
    },
    ["native-depot-documents"] = {
        id = "DepotDocs", title = "Depot Documents", fault = "depot_documents",
        objective_path = "/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C",
        cash = 1300, difficulty = 3, fail_on_death = false, redeem = 10,
        offered_boards = {10}, factions = {8}, reputation = {[8] = 25}, reputation_flags = {[8] = "Rep_Freelancer"},
        advantage = {}, no_track_flag = true,
        redeem_map = scav_bar, wrong_board = "scav_bar_redemption_required",
        stage_objective = "SilverfishStageDepotDocumentsObjective",
    },
    ["native-brandy"] = {
        id = "Clt_Brandy", title = "Fine Vintage", fault = "brandy",
        objective_path = "/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C",
        cash = 900, difficulty = 3, fail_on_death = false, redeem = 1,
        offered_boards = {1, 3}, scoped_boards = {1}, factions = {8}, reputation = {[8] = 15},
        reputation_flags = {[8] = "Rep_Freelancer"}, advantage = {}, no_track_flag = true,
        redeem_map = black_market, wrong_board = "black_market_redemption_required",
        stage_objective = "SilverfishStageBrandyObjective",
    },
    ["native-pocket-watch"] = {
        id = "Clt_Watch", title = "Pocket Watch", fault = "pocket_watch",
        objective_path = "/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C",
        cash = 1000, difficulty = 0, fail_on_death = false, redeem = 10,
        offered_boards = {10}, factions = {}, reputation = {}, advantage = {},
        no_track_flag = true, one_stack_unit = true,
        redeem_map = scav_bar, wrong_board = "scav_bar_redemption_required",
        stage_objective = "SilverfishStagePocketWatchObjective",
    },
    ["native-savlo-ring"] = {
        id = "Clct_EvilEye", title = "Savlo's Ring", fault = "savlo_ring",
        objective_path = "/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C",
        cash = 20000, difficulty = 10, fail_on_death = false, redeem = 10, fixture_xp = 10,
        offered_boards = {0, 10}, scoped_boards = {10}, factions = {5}, reputation = {[5] = -200},
        reputation_flags = {[5] = "Rep_Coup"}, advantage = {}, no_track_flag = true,
        redeem_map = scav_bar, wrong_board = "scav_bar_redemption_required",
        stage_objective = "SilverfishStageSavloRingObjective",
    },
}
local operations = {}
for prefix, spec in pairs(recipes) do
    spec.prefix, spec.boards = prefix, {}
    for _, id in ipairs(spec.scoped_boards or spec.offered_boards) do
        local board = boards[id]
        local world = board.map .. "." .. board.map:match("([^/]+)$")
        local path = world .. ":PersistentLevel." .. board.actor
        spec.boards[board.map] = {map = board.map, world_path = world, board_path = path, board_name = "BountyBoard_BP_C " .. path,
            board_id = id, quest_id = spec.id, global_contracts = board.global_contracts}
    end
    for _, action in ipairs({"preflight", "approach", "start", "turn-in"}) do
        operations[prefix .. "-" .. action] = {spec = spec, action = action}
    end
    if spec.fixture_xp then operations[prefix .. "-xp-fixture"] = {spec = spec, action = "xp-fixture"} end
end
local keys = {
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B",
    cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0",
    xp = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797",
    items = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A",
    objective = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083",
    consume = "ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333",
    redeem = "RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F",
    boards = "AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531",
    global = "AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698",
    kind = "QuestType_47_1031681D471E9929E30B17985E174BBE",
    difficulty = "Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85",
    fail = "Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4",
    prereq = "PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD",
    flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F",
    quests = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9",
    rep = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC",
    advantage = "Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2",
    ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F",
    track = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774",
}
local last

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function refuse(spec, reason) error("stock_" .. spec.fault .. "_" .. reason, 0) end
local function count(array) return array and array:GetArrayNum() end
local function int32(value)
    return type(value) == "number" and math.tointeger(value) and value >= -2147483648 and value <= 2147483647
end

local function wire_check(spec, fn, ...)
    local result = table.pack(pcall(fn, ...))
    if not result[1] then
        local reason = tostring(result[2]):match("^stock_wire_quest_(%w[%w_]+)$")
        if reason then refuse(spec, reason) end
        error(result[2], 0)
    end
    return table.unpack(result, 2, result.n)
end

local function exact_map(values, expected)
    if not values then return false end
    local size = 0
    for key, value in pairs(expected) do
        if not values:Contains(key) or values:Find(key):get() ~= value then return false end
        size = size + 1
    end
    return #values == size
end

local function recipe(spec, row)
    if not row then refuse(spec, "quest_row_unavailable") end
    local boards, objective, rep, advantage = row[keys.boards], row[keys.objective], row[keys.rep], row[keys.advantage]
    local ping, track = row[keys.ping], row[keys.track]
    if type(ping) == "userdata" then ping = ping:ToString() end
    if type(track) == "userdata" then track = track:ToString() end
    if row[keys.final] ~= 2 or row[keys.cash] ~= spec.cash or row[keys.xp] ~= 1 or row[keys.redeem] ~= spec.redeem
        or row[keys.kind] ~= 2 or row[keys.difficulty] ~= spec.difficulty
        or row[keys.fail] ~= spec.fail_on_death or row[keys.global] ~= true
        or row[keys.consume] ~= true or count(row[keys.items]) ~= 0 or count(row[keys.quests]) ~= 0
        or not row[keys.prereq] or #row[keys.prereq] ~= 0 or not row[keys.flags] or #row[keys.flags] ~= 0
        or ping ~= "None" or spec.no_track_flag and track ~= "None" and track ~= "" or count(boards) ~= #spec.offered_boards
        or count(objective) ~= 1 or not valid(objective[1])
        or objective[1]:GetFullName() ~= "BlueprintGeneratedClass " .. spec.objective_path
        or not exact_map(advantage, spec.advantage) then refuse(spec, "quest_recipe_unqualified") end
    for index, board_id in ipairs(spec.offered_boards) do
        if boards[index] ~= board_id then refuse(spec, "quest_recipe_unqualified") end
    end
    if not exact_map(rep, spec.reputation) then refuse(spec, "quest_reputation_unqualified") end
    if spec.one_stack_unit then
        local defaults = objective[1]:GetCDO()
        if not valid(defaults) or not same(defaults:GetClass(), objective[1]) or defaults.Stacks ~= true then
            refuse(spec, "objective_stack_class_unqualified")
        end
    end
    return objective[1]
end

local function reputation(spec, context)
    if #spec.factions == 0 then return {} end
    local library = StaticFindObject(characters_path)
    if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. characters_path then
        refuse(spec, "reputation_library_unavailable")
    end
    local values, seen = {}, {}
    for _, faction in ipairs(spec.factions) do
        local out = {}
        library:Compose_FactionRep_FlagName(faction, context.host, out)
        local key = out.Name
        local name = type(key) == "userdata" and key:ToString()
        if not name or key:GetComparisonIndex() == 0 or seen[name] or not name:match("^Rep_[%w_]+$")
            or spec.reputation_flags and spec.reputation_flags[faction] ~= name then
            refuse(spec, "reputation_flag_unqualified")
        end
        seen[name] = true
        local amount, found = wire_check(spec, wire.stock_flag, context.instance, key)
        values[faction] = {amount = amount, found = found}
    end
    return values
end

local function progress(spec, context)
    local row = wire_check(spec, wire.stock_row, context.save.ActiveQuests, context.key)
    local step = row and row[keys.step] or "-"
    if row then recipe(spec, row) end
    if step ~= "-" and step ~= 1 and step ~= 2 and step ~= -10 then refuse(spec, "quest_step_unqualified") end
    return {step = step, cash = wire_check(spec, wire.stock_flag, context.instance, "Player_Cash"),
        xp = wire_check(spec, wire.stock_flag, context.instance, "Player_Xp"), rep = reputation(spec, context)}
end

local function unchanged(context)
    return same(context.instance.SaveSlot_Ref, context.save) and same(context.host.Save, context.save)
        and same(context.interactor.Save, context.receiving_save)
end

local function valid_transition(spec, before, after, step, cash, xp, expected_rep)
    if after.step ~= step or after.cash ~= before.cash + cash or after.xp ~= before.xp + xp then return false end
    for _, faction in ipairs(spec.factions) do
        local observed = after.rep[faction]
        if (expected_rep and (observed.amount ~= expected_rep[faction] or not observed.found))
            or (not expected_rep and (observed.amount ~= before.rep[faction].amount or observed.found ~= before.rep[faction].found)) then
            return false
        end
    end
    return true
end

local function xp_fixture(spec, context, before, offered)
    if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
        or not same(context.interactor, context.host) or before.step ~= "-" or before.xp ~= 0
        or not context.save.ActiveQuests or #context.save.ActiveQuests ~= 0 or offered then
        refuse(spec, "xp_fixture_fresh_host_required")
    end
    local checked, after = pcall(function()
        context.instance:GivePlayer_XP(spec.fixture_xp)
        context.board:Configure_Stock()
        local newly_offered = wire.stock_row(context.board.Contracts, context.key)
        local observed = progress(spec, context)
        if not newly_offered or newly_offered[keys.step] ~= 1 or not recipe(spec, newly_offered)
            or not unchanged(context) or #context.save.ActiveQuests ~= 0
            or not valid_transition(spec, before, observed, "-", 0, spec.fixture_xp) then return end
        return observed
    end)
    if not checked or not after then error("mutation_unverified_stock_" .. spec.fault .. "_xp_fixture", 0) end
    return after
end

local function redeem(spec, context, before, objective, identity, owner)
    local library = StaticFindObject(library_path)
    if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. library_path then refuse(spec, "turn_in_library_unavailable") end
    local expected_rep = {}
    if #spec.factions > 0 then
        local effect, trait = FName("IntensePersonality", EFindName.FNAME_Find), {}
        if effect:GetComparisonIndex() == 0 then refuse(spec, "reputation_trait_unavailable") end
        context.instance:FindPassiveEffect(effect, true, trait, trait)
        if type(trait.EffectActive) ~= "boolean" or not int32(trait.Instances) or trait.Instances < 0 then
            refuse(spec, "trait_unqualified")
        end
        for _, faction in ipairs(spec.factions) do
            local prior = before.rep[faction]
            expected_rep[faction] = prior.amount + spec.reputation[faction] * (prior.found and 1 + trait.Instances or 1)
            if not int32(expected_rep[faction]) then refuse(spec, "reputation_range") end
        end
    end
    if not int32(before.cash + spec.cash) or not int32(before.xp + 1) then refuse(spec, "reward_range") end

    if owner == "guest" then
        local stage = _G[spec.stage_objective]
        if type(stage) ~= "function" then refuse(spec, "guest_objective_unavailable") end
        local accepted, reason = stage(context.interactor:GetAddress(),
            context.receiving_save:GetAddress(), context.save:GetAddress(), identity)
        if accepted ~= true then
            local fault = type(reason) == "string" and reason:match("^" .. spec.fault .. "_(objective_[%w_]+)$")
            if fault then refuse(spec, fault) end
            error(reason or "mutation_unverified_stock_" .. spec.fault .. "_objective", 0)
        end
    end
    local output = {}
    local checked, after = pcall(function()
        library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
        local observed = progress(spec, context)
        if not unchanged(context) or output.Success ~= true or valid(output.ConfirmationPopup)
            or not valid_transition(spec, before, observed, -10, spec.cash, 1, expected_rep)
            or wire.objective_guid(context.save, objective, spec.one_stack_unit)
            or owner == "guest" and wire.objective_guid(context.receiving_save, objective, spec.one_stack_unit) then return end
        return observed
    end)
    if not checked or not after then error("mutation_unverified_stock_" .. spec.fault .. "_turn_in", 0) end
    return after
end

function contract.command(fields, state, phase)
    local operation = fields[2]
    local request = operations[operation]
    if not request then return end
    local spec, action = request.spec, request.action
    last = nil
    local profile, name = spec.boards[state.map], fields[3]
    if not profile or #fields ~= (action == "turn-in" and 5 or 3) then refuse(spec, "arguments_unqualified") end
    if action == "approach" then return wire_check(spec, wire.stock_board_approach, state, phase, name, profile) end
    if action == "turn-in" and state.map ~= spec.redeem_map then refuse(spec, spec.wrong_board) end
    local context = wire_check(spec, wire.stock_board_context, state, phase, name, false, profile)
    local row = wire_check(spec, wire.stock_quest_table):FindRow(spec.id)
    local objective = recipe(spec, row)
    if row[keys.step] ~= 1 then refuse(spec, "template_step_unqualified") end
    local before = progress(spec, context)
    local offered
    if before.step == "-" then
        context.board:Configure_Stock()
        offered = wire_check(spec, wire.stock_row, context.board.Contracts, context.key)
        if action ~= "xp-fixture" then
            if not offered or offered[keys.step] ~= 1 then refuse(spec, "quest_not_offered") end
            recipe(spec, offered)
        end
    end
    local identity, owner
    if before.step == 1 or before.step == 2 then
        identity = wire_check(spec, wire.objective_guid, context.receiving_save, objective, spec.one_stack_unit)
        if identity then owner = same(context.receiving_save, context.save) and "host" or "guest" end
    end
    local after = before
    if action == "xp-fixture" then
        after = xp_fixture(spec, context, before, offered)
    elseif action == "start" and before.step == "-" then
        local checked, observed = pcall(function()
            context.instance:AddQuest_ByName(context.key)
            return progress(spec, context)
        end)
        if not checked or not unchanged(context) or not valid_transition(spec, before, observed, 1, 0, 0) then
            error("mutation_unverified_stock_" .. spec.fault .. "_start", 0)
        end
        after = observed
    elseif action == "turn-in" then
        if before.step ~= 1 and before.step ~= 2 then refuse(spec, "quest_not_active") end
        if not identity or fields[4] ~= identity or fields[5] ~= owner then refuse(spec, "objective_changed") end
        after = redeem(spec, context, before, objective, identity, owner)
    end
    last = {operation = operation, context = context, profile = profile, pawn = name, before = before, after = after,
        guid = identity or "-", owner = owner or "-", spec = spec, prefix = spec.prefix}
    return "verified stock " .. spec.title .. " contract"
end

function contract.rows(state, phase, status_row)
    local proof = last
    if not proof then return {} end
    if phase ~= "hosting" or state.map ~= proof.profile.map or not same(state.world, proof.context.world)
        or not valid(proof.context.host) or not valid(proof.context.interactor) or not valid(proof.context.instance)
        or not unchanged(proof.context) then
        last = nil
        return {}
    end
    local before, after = proof.before, proof.after
    local rows = {status_row(proof.prefix, proof.operation, state.map, proof.pawn, proof.profile.board_name,
        proof.spec.id, before.step, after.step, before.cash, after.cash, before.xp, after.xp, proof.guid, proof.owner,
        state.map == proof.spec.redeem_map and 1 or 0)}
    for _, faction in ipairs(proof.spec.factions) do
        rows[#rows + 1] = status_row(proof.prefix .. "-reputation", faction, before.rep[faction].amount, after.rep[faction].amount)
    end
    return rows
end

return contract
