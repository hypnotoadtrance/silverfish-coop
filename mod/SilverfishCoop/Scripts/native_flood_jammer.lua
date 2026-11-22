local wire = require("native_wire_quest")
local guest_save = require("guest_save")
local aim_fixture = require("native_flood_jammer_aim_fixture")
local profiles = require("native_jammer_profiles")

local quest = {}
local code_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local character_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local commands = {preflight = true, approach = true, start = true, ["turn-in"] = true, ["xp-fixture"] = true}
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
    penalty = "FailPenalty_36_53777B2F407AD61DE139B3815A07F47F",
    prereq = "PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD",
    flags = "Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F",
    quests = "Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9",
    rep = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC",
    advantage = "Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2",
    ping = "GlobalPing_OnTurnIn_108_68CA1968452BFC635A107EBB1EE3AA7F",
    tracked_flag = "TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774",
}
local last, observer_live

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function refuse(profile, reason) error(profile.error_tag .. reason, 0) end
local function count(array) return array and array:GetArrayNum() end
local function int32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil and value >= -2147483648 and value <= 2147483647
end

local function wire_check(profile, fn, ...)
    local output = table.pack(pcall(fn, ...))
    if not output[1] then
        local reason = tostring(output[2]):match("^stock_wire_quest_(%w[%w_]+)$")
        if reason then refuse(profile, reason) end
        error(output[2], 0)
    end
    return table.unpack(output, 2, output.n)
end

local function name_text(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function recipe(profile, row)
    if not row then refuse(profile, "quest_row_unavailable") end
    local boards, rep, advantage = row[keys.boards], row[keys.rep], row[keys.advantage]
    local flags = row[keys.flags]
    local completion = profile.completion_flag and FName(profile.completion_flag, EFindName.FNAME_Find)
    if not flags or #flags ~= (completion and 1 or 0)
        or completion and (completion:GetComparisonIndex() == 0 or not flags:Contains(completion) or flags:Find(completion):get() ~= 1) then
        refuse(profile, "quest_completion_flag_unqualified")
    end
    if row[keys.final] ~= 2 or row[keys.cash] ~= profile.cash or row[keys.xp] ~= 1 or row[keys.redeem] ~= 0
        or row[keys.kind] ~= 2 or row[keys.difficulty] ~= (profile.difficulty or 0) or row[keys.fail] ~= false or row[keys.penalty] ~= profile.penalty
        or row[keys.global] ~= true or row[keys.consume] ~= false or count(row[keys.items]) ~= 0
        or count(row[keys.objective]) ~= 0 or count(row[keys.quests]) ~= 0
        or not row[keys.prereq] or #row[keys.prereq] ~= 0
        or name_text(row[keys.ping]) ~= "None" or name_text(row[keys.tracked_flag]) ~= "None"
        or count(boards) ~= #profile.board_order or not rep or #rep ~= #profile.factions or not advantage or #advantage ~= 0 then
        refuse(profile, "quest_recipe_unqualified")
    end
    for index, id in ipairs(profile.board_order) do
        if boards[index] ~= id then refuse(profile, "quest_board_recipe_unqualified") end
    end
    for _, id in ipairs(profile.factions) do
        if not rep:Contains(id) then refuse(profile, "quest_reputation_faction_" .. id .. "_missing") end
        if rep:Find(id):get() ~= profile.reward_rep[id] then refuse(profile, "quest_reputation_faction_" .. id .. "_amount_changed") end
    end
end

local function flag_snapshot(context, library, faction, advantage)
    local profile = context.jammer_profile
    local out = {}
    if advantage then library:Compose_FactionAdvantage_FlagName(faction, context.host, out)
    else library:Compose_FactionRep_FlagName(faction, context.host, out) end
    local key = out.Name
    if type(key) ~= "userdata" or key:GetComparisonIndex() == 0 then refuse(profile, "faction_flag_unqualified") end
    local amount, found = wire_check(profile, wire.stock_flag, context.instance, key)
    return {amount = amount, found = found}
end

local function progress(context)
    local profile = context.jammer_profile
    local row = wire_check(profile, wire.stock_row, context.save.ActiveQuests, context.key)
    if row then recipe(profile, row) end
    local step = row and row[keys.step] or "-"
    if step ~= "-" and step ~= 1 and step ~= 2 and step ~= -7 and step ~= -10 then refuse(profile, "quest_step_unqualified") end
    local characters = StaticFindObject(character_path)
    if not valid(characters) or characters:GetFullName() ~= "FL_Characters_C " .. character_path then
        refuse(profile, "character_library_unavailable")
    end
    local rep, advantage = {}, {}
    for _, faction in ipairs(profile.factions) do
        rep[faction] = flag_snapshot(context, characters, faction, false)
        advantage[faction] = flag_snapshot(context, characters, faction, true)
    end
    local completion = profile.completion_flag and wire_check(profile, wire.stock_flag, context.instance, profile.completion_flag)
    return {step = step, completion = completion, cash = wire_check(profile, wire.stock_flag, context.instance, "Player_Cash"),
        xp = wire_check(profile, wire.stock_flag, context.instance, "Player_Xp"), rep = rep, advantage = advantage}
end

local function unchanged(context)
    return same(context.instance.SaveSlot_Ref, context.save) and same(context.host.Save, context.save)
        and same(context.interactor.Save, context.receiving_save)
end

local function same_flag(first, second)
    return first.amount == second.amount and first.found == second.found
end

local function transition(profile, before, after, step, cash, xp, expected_rep)
    if after.step ~= step or after.cash ~= before.cash + cash or after.xp ~= before.xp + xp
        or before.completion ~= after.completion then return false end
    for _, faction in ipairs(profile.factions) do
        local actual = after.rep[faction]
        if not same_flag(after.advantage[faction], before.advantage[faction])
            or expected_rep and (actual.amount ~= expected_rep[faction] or not actual.found)
            or not expected_rep and not same_flag(actual, before.rep[faction]) then return false end
    end
    return true
end

local function redeem(context, before)
    local profile = context.jammer_profile
    local library = StaticFindObject(code_path)
    if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. code_path then refuse(profile, "redemption_library_unavailable") end
    if not int32(before.cash + profile.cash) or not int32(before.xp + 1) then refuse(profile, "reward_range") end

    local trait, effect = {}, FName("IntensePersonality", EFindName.FNAME_Find)
    if effect:GetComparisonIndex() == 0 then refuse(profile, "reputation_trait_unavailable") end
    context.instance:FindPassiveEffect(effect, true, trait, trait)
    if type(trait.EffectActive) ~= "boolean" or not int32(trait.Instances) or trait.Instances < 0 then refuse(profile, "trait_unqualified") end
    local expected_rep = {}
    for _, faction in ipairs(profile.factions) do
        local prior = before.rep[faction]
        expected_rep[faction] = prior.amount + profile.reward_rep[faction] * (prior.found and 1 + trait.Instances or 1)
        if not int32(expected_rep[faction]) then refuse(profile, "reputation_range") end
    end

    local output = {}
    local checked, after = pcall(function()
        library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
        local observed = progress(context)
        if unchanged(context) and output.Success == true and not valid(output.ConfirmationPopup)
            and transition(profile, before, observed, -10, profile.cash, 1, expected_rep) then return observed end
    end)
    if not checked or not after then error("mutation_unverified_" .. profile.error_tag .. "turn_in", 0) end
    return after
end

local function xp_fixture(context, before)
    local profile = context.jammer_profile
    if not profile.xp_fixture or not fixtures or not same(context.interactor, context.host)
        or before.step ~= "-" or before.xp ~= 0 or not context.save.ActiveQuests or #context.save.ActiveQuests ~= 0 then
        refuse(profile, "xp_fixture_fresh_host_required")
    end
    context.board:Configure_Stock()
    if wire_check(profile, wire.stock_row, context.board.Contracts, context.key) then
        refuse(profile, "xp_fixture_quest_already_offered")
    end
    local checked, after = pcall(function()
        context.instance:GivePlayer_XP(profile.xp_fixture)
        context.board:Configure_Stock()
        local offered = wire.stock_row(context.board.Contracts, context.key)
        if not offered or offered[keys.step] ~= 1 then return end
        recipe(profile, offered)
        local observed = progress(context)
        if unchanged(context) and #context.save.ActiveQuests == 0
            and transition(profile, before, observed, "-", 0, profile.xp_fixture) then return observed end
    end)
    if not checked or not after then error("mutation_unverified_" .. profile.error_tag .. "xp_fixture", 0) end
    return after
end

function quest.command(fields, state, phase)
    local full_operation = fields[2]
    if type(full_operation) ~= "string" then return end
    local tag, operation = full_operation:match("^(native%-%l+%-jammer)%-(.+)$")
    if not tag then tag, operation = full_operation:match("^(native%-lenny)%-(.+)$") end
    if not tag then tag, operation = full_operation:match("^(native%-spratt)%-(.+)$") end
    local profile = profiles.by_tag[tag]
    if not profile then return end
    local alternate = profiles.alternate[tag]
    if alternate and state.map == alternate.map then profile = alternate end
    if operation == "aim" and profile.objective_map then
        if #fields ~= 4 or state.map ~= profile.objective_map then refuse(profile, "aim_arguments_unqualified") end
        return aim_fixture.aim(state, phase, fields[3], fields[4], profile)
    end
    if not commands[operation] or operation == "xp-fixture" and not profile.xp_fixture then return end
    last = nil
    local pawn = fields[3]
    if #fields ~= 3 or state.map ~= profile.map then refuse(profile, "arguments_unqualified") end
    if operation == "approach" then return wire_check(profile, wire.stock_board_approach, state, phase, pawn, profile) end
    local context = wire_check(profile, wire.stock_board_context, state, phase, pawn, false, profile)
    context.jammer_profile = profile
    local template = wire_check(profile, wire.stock_quest_table):FindRow(profile.quest_id)
    recipe(profile, template)
    if template[keys.step] ~= 1 then refuse(profile, "quest_template_step_unqualified") end
    local before = progress(context)
    if profile.xp_fixture and context.board.ShowAllContracts ~= false then refuse(profile, "board_stock_filter_unqualified") end
    if profile.completion_flag then
        local wanted = (before.step == 2 or before.step == -10) and 1 or 0
        if before.completion ~= wanted then refuse(profile, "stock_completion_flag_changed") end
    end
    if before.step == "-" and operation ~= "turn-in" and operation ~= "xp-fixture" then
        if profile.xp_fixture and before.xp < profile.difficulty then refuse(profile, "quest_not_offered") end
        context.board:Configure_Stock()
        local offered = wire_check(profile, wire.stock_row, context.board.Contracts, context.key)
        if not offered or offered[keys.step] ~= 1 then refuse(profile, "quest_not_offered") end
        recipe(profile, offered)
    end

    local after = before
    if operation == "xp-fixture" then
        after = xp_fixture(context, before)
    elseif operation == "start" then
        if before.step ~= "-" then refuse(profile, "quest_already_started_or_completed") end
        local checked, observed = pcall(function()
            context.instance:AddQuest_ByName(context.key)
            return progress(context)
        end)
        if not checked or not unchanged(context) or not transition(profile, before, observed, 1, 0, 0) then
            error("mutation_unverified_" .. profile.error_tag .. "start", 0)
        end
        after = observed
    elseif operation == "turn-in" then
        if before.step ~= 2 then refuse(profile, "quest_objective_not_complete") end
        after = redeem(context, before)
    end
    last = {operation = full_operation, pawn = pawn, context = context, before = before, after = after}
    return "verified stock " .. (profile.title or "Signal Jammer contract")
end

local function objective_row(state, phase, status_row)
    local host = state.local_pawn and state.local_pawn.pawn
    local profile = profiles.by_map[state.map]
    if not profile or not enabled or phase ~= "hosting" or state.listening ~= true or state.server ~= false
        or not valid(host) or not valid(state.world) or state.world:GetFullName() ~= "World " .. profile.objective_world
        or type(SilverfishWatchFloodJammer) ~= "function" or type(SilverfishReadFloodJammer) ~= "function" then
        if observer_live and type(SilverfishClearFloodJammer) == "function" then SilverfishClearFloodJammer() end
        observer_live = false
        return
    end
    local save = host.Save
    if not valid(save) then return end
    local guests = {}
    for _, detail in ipairs(state.pawns) do
        if detail.name ~= state.local_pawn.name then
            local verified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, detail.name)
            if verified and valid(pawn) and valid(detached) then
                guests[#guests + 1] = pawn:GetAddress()
                guests[#guests + 1] = detached:GetAddress()
            end
        end
    end
    local ready, reason = SilverfishWatchFloodJammer(host:GetAddress(), save:GetAddress(), table.unpack(guests))
    observer_live = ready == true
    local proof, before, after, xp_before, xp_after, role, shooter, projectile = SilverfishReadFloodJammer(host:GetAddress())
    if not proof then proof = reason or "stock_observer_unavailable" end
    return status_row(profile.tag .. "-objective", profile.objective_map, profile.jammer_name, profile.trigger_name, proof,
        before or "-", after or "-", xp_before or "-", xp_after or "-", role or "-", shooter or "-", projectile or "-")
end

function quest.rows(state, phase, status_row)
    local objective = objective_row(state, phase, status_row)
    local shot_line = aim_fixture.row(state, phase, status_row)
    if objective then
        last = nil
        return {objective, shot_line}
    end
    if shot_line then return {shot_line} end
    local proof = last
    if not proof then return {} end
    local profile = proof.context.jammer_profile
    if phase ~= "hosting" or state.map ~= profile.map or not same(state.world, proof.context.world)
        or not valid(proof.context.board) or not unchanged(proof.context) then
        last = nil
        return {}
    end
    local before, after = proof.before, proof.after
    local rows = {status_row(profile.tag, proof.operation, profile.map, proof.pawn, profile.board_name,
        profile.quest_id, before.step, after.step, before.cash, after.cash, before.xp, after.xp)}
    for _, faction in ipairs(profile.factions) do
        rows[#rows + 1] = status_row(profile.tag .. "-reputation", faction, before.rep[faction].amount, after.rep[faction].amount)
    end
    return rows
end

return quest
