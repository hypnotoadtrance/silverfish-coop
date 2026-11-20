local contract = {}
local wire = require("native_wire_quest")

local library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local characters_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local shopkeep = "/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local boards = {
    [1] = {map = black_market, actor = "BountyBoard_BP_C_0", global_contracts = true},
    [2] = {map = shopkeep, actor = "BountyBoard_BP_C_1"},
    [3] = {map = observatory, actor = "BountyBoard_BP_C_0", global_contracts = true},
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
    ["native-missing-contact"] = {
        id = "Note_Farmhouse", title = "Missing Contact", fault = "missing_contact",
        quest_display_alias = "Note_FarmHouse",
        objective_path = "/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C",
        cash = 450, difficulty = 0, fail_on_death = false, redeem = 1,
        offered_boards = {1}, factions = {8, 4}, reputation = {[8] = 40, [4] = -5},
        reputation_flags = {[8] = "Rep_Freelancer", [4] = "Rep_State"}, advantage = {[8] = 5},
        advantage_flags = {[8] = "Adv_Freelancer"}, no_track_flag = true, canonical_objective_only = true,
        redeem_map = black_market, wrong_board = "black_market_redemption_required",
    },
    ["native-dead-drop"] = {
        id = "DeadDrop_CrossRoads", title = "CrossRoads Dead Drop", fault = "dead_drop",
        objective_path = "/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C",
        cash = 300, difficulty = 1, fail_on_death = false, redeem = 1, fixture_xp = 1,
        offered_boards = {1, 3}, factions = {8, 4}, reputation = {[8] = 40, [4] = -5},
        reputation_flags = {[8] = "Rep_Freelancer", [4] = "Rep_State"}, advantage = {[8] = 20},
        advantage_flags = {[8] = "Adv_Freelancer"}, no_track_flag = true,
        redeem_map = black_market, wrong_board = "black_market_redemption_required",
        stage_objective = "SilverfishStageCrossroadsDeadDropObjective",
    },
    ["native-stolen-bag"] = {
        id = "Clt_StolenBag", title = "Stolen Goods", fault = "stolen_bag",
        objective_path = "/Game/Classes/Items/Key_Items/Brokers_ArtifactsBag.Brokers_ArtifactsBag_C",
        cash = 1200, difficulty = 5, fail_on_death = false, redeem = 1, fixture_xp = 5,
        offered_boards = {1, 3}, scoped_boards = {1}, factions = {8, 5}, reputation = {[8] = 100, [5] = -150},
        reputation_flags = {[8] = "Rep_Freelancer", [5] = "Rep_Coup"}, advantage = {[8] = 60},
        advantage_flags = {[8] = "Adv_Freelancer"}, no_track_flag = true,
        redeem_map = black_market, wrong_board = "black_market_redemption_required",
        stage_objective = "SilverfishStageStolenBagObjective",
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
    ["native-pump-room"] = {
        id = "Clt_EatherPumpTape", title = "Pump Room", fault = "pump_room",
        objective_path = "/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C",
        cash = 3200, difficulty = 8, fail_on_death = false, redeem = 1, fixture_xp = 8,
        offered_boards = {1, 3}, scoped_boards = {1}, factions = {8, 3}, reputation = {[8] = 50, [3] = -100},
        reputation_flags = {[8] = "Rep_Freelancer", [3] = "Rep_Fae"}, advantage = {[8] = 70}, no_track_flag = true,
        redeem_map = black_market, wrong_board = "black_market_redemption_required",
        stage_objective = "SilverfishStagePumpRoomObjective",
    },
    ["native-molasses"] = {
        id = "Clt_Molasis", title = "Get Molasses", fault = "molasses",
        objective_path = "/Game/Classes/Items/Item_Molasis.Item_Molasis_C",
        cash = 30, difficulty = 0, fail_on_death = false, redeem = 2,
        offered_boards = {2}, factions = {}, reputation = {}, advantage = {}, no_track_flag = true,
        redeem_map = shopkeep, wrong_board = "shopkeep_redemption_required",
        stage_objective = "SilverfishStageMolassesObjective",
    },
    ["native-savlo-keycard"] = {
        id = "Clct_SavloKey", title = "Hall Pass", fault = "savlo_keycard",
        objective_path = "/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C",
        cash = 50000, difficulty = 14, fail_on_death = false, redeem = 3, fixture_xp = 14,
        offered_boards = {3, 1}, scoped_boards = {3}, factions = {8}, reputation = {[8] = 100},
        reputation_flags = {[8] = "Rep_Freelancer"}, advantage = {[8] = 300},
        advantage_flags = {[8] = "Adv_Freelancer"}, no_track_flag = true,
        redeem_map = observatory, wrong_board = "observatory_redemption_required",
        stage_objective = "SilverfishStageSavloKeycardObjective",
    },
    ["native-black-box"] = {
        id = "Clct_Radio_Scar", title = "Black Box", fault = "black_box",
        objective_path = "/Game/Classes/Items/Key_Items/Item_InniRadio_01.Item_InniRadio_01_C",
        cash = 12000, difficulty = 12, fail_on_death = false, redeem = 3, fixture_xp = 12,
        offered_boards = {3, 1}, factions = {8}, reputation = {[8] = 50},
        reputation_flags = {[8] = "Rep_Freelancer"}, advantage = {[8] = 50},
        advantage_flags = {[8] = "Adv_Freelancer"}, no_track_flag = true,
        redeem_map = observatory, wrong_board = "observatory_redemption_required",
        stage_objective = "SilverfishStageBlackBoxObjective", completion_flag = "Simmi_BlackBox",
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
            board_id = id, quest_id = spec.id, quest_display_alias = spec.quest_display_alias,
            global_contracts = board.global_contracts}
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
    local flags = row[keys.flags]
    local completion = spec.completion_flag and FName(spec.completion_flag, EFindName.FNAME_Find)
    if type(ping) == "userdata" then ping = ping:ToString() end
    if type(track) == "userdata" then track = track:ToString() end
    if row[keys.final] ~= 2 or row[keys.cash] ~= spec.cash or row[keys.xp] ~= 1 or row[keys.redeem] ~= spec.redeem
        or row[keys.kind] ~= 2 or row[keys.difficulty] ~= spec.difficulty
        or row[keys.fail] ~= spec.fail_on_death or row[keys.global] ~= true
        or row[keys.consume] ~= true or count(row[keys.items]) ~= 0 or count(row[keys.quests]) ~= 0
        or not row[keys.prereq] or #row[keys.prereq] ~= 0
        or not flags or #flags ~= (completion and 1 or 0)
        or ping ~= "None" or spec.no_track_flag and track ~= "None" and track ~= "" or count(boards) ~= #spec.offered_boards
        or count(objective) ~= 1 or not valid(objective[1])
        or objective[1]:GetFullName() ~= "BlueprintGeneratedClass " .. spec.objective_path
        or not exact_map(advantage, spec.advantage) then refuse(spec, "quest_recipe_unqualified") end
    for index, board_id in ipairs(spec.offered_boards) do
        if boards[index] ~= board_id then refuse(spec, "quest_recipe_unqualified") end
    end
    if completion and (completion:GetComparisonIndex() == 0 or completion:ToString() ~= spec.completion_flag
        or not flags:Contains(completion) or flags:Find(completion):get() ~= 1) then
        refuse(spec, "quest_completion_recipe_unqualified")
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

local function characters(spec)
    local library = StaticFindObject(characters_path)
    if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. characters_path then
        refuse(spec, "reputation_library_unavailable")
    end
    return library
end

local function reputation(spec, context)
    if #spec.factions == 0 then return {} end
    local library = characters(spec)
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
    local advantage = {}
    if spec.advantage_flags then
        local library = characters(spec)
        for faction, expected in pairs(spec.advantage_flags) do
            local out = {}
            library:Compose_FactionAdvantage_FlagName(faction, context.host, out)
            local key = out.Name
            if type(key) ~= "userdata" or key:GetComparisonIndex() == 0 or key:ToString() ~= expected then
                refuse(spec, "advantage_flag_unqualified")
            end
            local amount, found = wire_check(spec, wire.stock_flag, context.instance, key)
            advantage[faction] = {amount = amount, found = found}
        end
    end
    local completion
    if spec.completion_flag then
        local amount, found = wire_check(spec, wire.stock_flag, context.instance, spec.completion_flag)
        if amount ~= (step == -10 and 1 or 0) or step == -10 and not found then
            refuse(spec, "quest_completion_state_unqualified")
        end
        completion = {amount = amount, found = found}
    end
    return {step = step, cash = wire_check(spec, wire.stock_flag, context.instance, "Player_Cash"),
        xp = wire_check(spec, wire.stock_flag, context.instance, "Player_Xp"),
        rep = reputation(spec, context), advantage = advantage, completion = completion}
end

local function unchanged(context)
    return same(context.instance.SaveSlot_Ref, context.save) and same(context.host.Save, context.save)
        and same(context.interactor.Save, context.receiving_save)
end

local function valid_transition(spec, before, after, step, cash, xp, expected_rep)
    if after.step ~= step or after.cash ~= before.cash + cash or after.xp ~= before.xp + xp then
        return false, ("balances:step=%s/%s,cash=%s/%s,xp=%s/%s"):format(
            after.step, step, after.cash, before.cash + cash, after.xp, before.xp + xp)
    end
    for _, faction in ipairs(spec.factions) do
        local observed = after.rep[faction]
        if (expected_rep and (observed.amount ~= expected_rep[faction] or not observed.found))
            or (not expected_rep and (observed.amount ~= before.rep[faction].amount or observed.found ~= before.rep[faction].found)) then
            local amount = expected_rep and expected_rep[faction] or before.rep[faction].amount
            return false, ("reputation[%d]=%d/%d,found=%s"):format(faction, observed.amount, amount, tostring(observed.found))
        end
    end
    -- Stock TurnIn_Quest reads ReputationGain. Advantage_Faction
    -- is part of the authored recipe and is applied by other quest graphs.
    for faction, previous in pairs(before.advantage) do
        local observed = after.advantage[faction]
        if observed.amount ~= previous.amount or observed.found ~= previous.found then
            return false, ("advantage[%d]=%d/%d,found=%s"):format(
                faction, observed.amount, previous.amount, tostring(observed.found))
        end
    end
    if before.completion then
        local expected = step == -10 and 1 or before.completion.amount
        local found = step == -10 or before.completion.found
        if not after.completion or after.completion.amount ~= expected or after.completion.found ~= found then
            return false, "completion_flag_changed"
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
    local checked, after, reason = pcall(function()
        library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
        if not unchanged(context) then return nil, "save_ownership_changed" end
        if output.Success ~= true or valid(output.ConfirmationPopup) then return nil, "stock_confirmation_incomplete" end
        local observed = progress(spec, context)
        local reconciled, detail = valid_transition(spec, before, observed, -10, spec.cash, 1, expected_rep)
        if not reconciled then return nil, detail end
        if wire.objective_guid(context.save, objective, spec.one_stack_unit)
            or owner == "guest" and wire.objective_guid(context.receiving_save, objective, spec.one_stack_unit) then
            return nil, "objective_still_present"
        end
        return observed
    end)
    if not checked or not after then
        local detail = checked and (reason or "unavailable") or tostring(after):sub(1, 250)
        error("mutation_unverified_stock_" .. spec.fault .. "_turn_in:" .. detail, 0)
    end
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
    if (before.step == 1 or before.step == 2) and
        (not spec.canonical_objective_only or same(context.receiving_save, context.save)) then
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
    if proof.spec.completion_flag then
        rows[#rows + 1] = status_row(proof.prefix .. "-completion", proof.spec.completion_flag,
            before.completion.amount, before.completion.found and 1 or 0, after.completion.amount, after.completion.found and 1 or 0)
    end
    return rows
end

return contract
