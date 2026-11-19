local valve = {}
local wire = require("native_wire_quest")
local transport = require("native_transport")
local guest_save = require("guest_save")
local players = require("players")

local area = "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P"
local world_path = area .. ".Subway_01_P"
local root = world_path .. ":PersistentLevel."
local valve_path = root .. "ValveSwitch_BP_C_1"
local trigger_path = root .. "Quest_Trigger_BP_C_1"
local sludge_path, audio_path = root .. "ToxicSludge_Plane_BP_C_1", root .. "AmbientSound_9"
local valve_class = "/Game/Classes/ScriptingActors/ValveSwitch_BP.ValveSwitch_BP_C"
local trigger_class = "/Game/Classes/ScriptingActors/Quest_Trigger_BP.Quest_Trigger_BP_C"
local sludge_class = "/Game/Classes/Actors/ToxicSludge_Plane_BP.ToxicSludge_Plane_BP_C"
local audio_class = "/Script/Engine.AmbientSound"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local art_path = "/Game/Classes/Items/Artifacts/Art_AcidResist.Art_AcidResist_C"
local mask_path = "/Game/Classes/Armor_Items/Helm_HazOp.Helm_HazOp_C"
local code_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local characters_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local table_path = "/Game/Classes/Libraries/DataTables/Quests_DT.Quests_DT"
local operations = {preflight = true, approach = true, start = true, ["turn-in"] = true, ["xp-fixture"] = true}
local board = {
    map = wire.map, world_path = wire.map .. ".Borough",
    board_name = wire.board_name, board_path = wire.board_name:match("^%S+ (.+)$"),
    board_id = 5, global_contracts = false, quest_id = "Sub_Valve",
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_INPUT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local step_key = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local cash_key = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local xp_key = "Reward_PlayerXp_44_8ED601354EEC8BFBAC19B8BE0F25C797"
local item_key = "Reward_Items_32_2B334D5F4DBA7AA760BF33A8E93A4C0A"
local rep_key = "ReputationGain_86_DA02B32344A59A6C236B848B22520EEC"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
-- The cooked Factions_Enum::NewEnumerator8 is runtime ordinal 2 (Commune).
local faction_flags = {[2] = "Rep_Commune", [4] = "Rep_State", [5] = "Rep_Coup"}
local reward_rep = {[2] = 150, [4] = -40, [5] = -50}
local factions = {2, 4, 5}
local action_reply, board_reply, reward_reply, projected, fixture_reply, retirement

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function refuse(reason) error("stock_subway_valve_" .. reason, 0) end
local function finite(value) return type(value) == "number" and value == value and math.abs(value) < math.huge end
local function integer(value)
    return finite(value) and math.tointeger(value) and value >= -2147483648 and value <= 2147483647
end
local function vector(point) return point and finite(point.X) and finite(point.Y) and finite(point.Z) end
local function square(a, b) return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 end
local function qualified_name(key, expected)
    return type(key) == "userdata" and key:GetComparisonIndex() ~= 0 and key:ToString() == expected
end
local function stock_name(expected)
    local key = FName(expected, EFindName.FNAME_Find)
    if not qualified_name(key, expected) then refuse("name_unavailable:" .. expected) end
    return key
end
local function valid_id(value, charset)
    return type(value) == "string" and #value == 32 and not value:find(charset) and value:find("[^0]")
end
local function slot_ok(name) return type(name) == "string" and name:sub(1, 7) == "SFCoop_" and valid_id(name:sub(8), "[^0-9a-f]") end
local function units(array) return array and array:GetArrayNum() end
local function row_count(map, size) return map and #map == size end

local function map_entries(map, expected)
    local count = 0
    if not map then return false end
    for id, value in pairs(expected) do
        if not map:Contains(id) or map:Find(id):get() ~= value then return false end
        count = count + 1
    end
    return #map == count
end

local function recipe(row)
    if not row then refuse("quest_row_unavailable") end
    local art, mask = StaticFindObject(art_path), StaticFindObject(mask_path)
    local items, boards = row[item_key], row.AppearOn_Boards_95_2FDB27AE4695B9213302E5897144A531
    local tracking = row.TrackFlag_ForStep_111_31112EA54B887C1D869E1291BC819774
    local checks = {
        {"final_step", row.Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B, 2},
        {"cash", row[cash_key], 0}, {"xp", row[xp_key], 1},
        {"type", row.QuestType_47_1031681D471E9929E30B17985E174BBE, 1},
        {"difficulty", row.Difficulty_12_9F1A01AD46151E4F5F152B82C99B5D85, 7},
        {"fail_on_death", row.Fail_OnDeath_9_BAADF6C6450D1EB972F6E8A33214ACE4, false},
        {"fail_penalty", row.FailPenalty_36_53777B2F407AD61DE139B3815A07F47F, 100},
        {"global_visibility", row.AppearOn_GlobalBountyBoards_78_B538291F4816D684913EAA8852507698, true},
        {"redeem_at", row.RedeemAt_83_FAE34B6C471D2019508A4898E9A0080F, 5},
        {"board_count", units(boards), 1}, {"board", boards and units(boards) == 1 and boards[1] or nil, 5},
        {"consume_items", row.ObjectiveItemsAreConsumed_61_4669760345DABF3B531CBF8AFABA6333, false},
        {"objective_count", units(row.Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083), 0},
        {"followup_count", units(row.Add_Quests_OnComplete_66_06ACA8C44193595C2DFAD8A85B7FC8B9), 0},
        {"completion_flags", row_count(row.Set_FlagsOnComplete_73_4E24EFBB44C3458694C7DCB8CEF1D03F, 0), true},
        {"prerequisites", row_count(row.PreReq_Flags_105_3FA9C82A48BE1675F41E15A54570DBAD, 0), true},
        {"advantage", row_count(row.Advantage_Faction_100_F1B0B60547EEC0A936CFECB9BE005CE2, 0), true},
        {"tracking_flag", tracking and tracking:ToString(), "None"},
        {"reputation", map_entries(row[rep_key], reward_rep), true},
        {"reward_count", units(items), 2},
    }
    for _, check in ipairs(checks) do
        if check[2] ~= check[3] then refuse("quest_recipe_" .. check[1] .. "_changed:" .. tostring(check[2])) end
    end
    if not same(items[1], art) then refuse("quest_recipe_artifact_class_changed") end
    if not same(items[2], mask) then refuse("quest_recipe_helmet_class_changed") end
    return art, mask
end

local function canonical(state, phase)
    if not enabled or phase ~= "hosting" then refuse("authority_unavailable") end
    local detail, fresh = transport.scoped_host(state, phase)
    local host = detail and detail.pawn
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(host) and valid(statics) and statics:GetGameInstance(host)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not fresh or not exact(host, pawn_class) or not exact(instance, instance_class) or not exact(save, save_class)
        or not same(host.Save, save) or not same(instance["As Player FP"], host) then refuse("canonical_save_unavailable") end
    return {host = host, instance = instance, save = save, world = state.world, statics = statics,
        key = stock_name(board.quest_id)}
end

local function completion_flag(context, step)
    local expected = "SubValve_Closed"
    local key = FName(expected, EFindName.FNAME_Find)
    if qualified_name(key, expected) then return wire.stock_flag(context.instance, key) end
    -- Before the Subway level script has loaded, its literal can be absent from the name pool.
    if (step == "-" or step == 1) and type(key) == "userdata"
        and key:GetComparisonIndex() == 0 and key:ToString() == "None" then return 0 end
    refuse("name_unavailable:" .. expected)
end

local function progress(context)
    local row = wire.stock_row(context.save.ActiveQuests, context.key)
    if row then recipe(row) end
    local step = row and row[step_key] or "-"
    if step ~= "-" and step ~= 1 and step ~= 2 and step ~= -10 then refuse("quest_step_unqualified") end
    local library = StaticFindObject(characters_path)
    if not valid(library) or library:GetFullName() ~= "FL_Characters_C " .. characters_path then
        refuse("reputation_library_unavailable")
    end
    local advantage = {}
    library:Compose_FactionAdvantage_FlagName(2, context.host, advantage)
    if not qualified_name(advantage.Name, "Adv_Commune") then refuse("advantage_flag_unqualified") end
    local stock = {step = step, flag = completion_flag(context, step),
        cash = wire.stock_flag(context.instance, stock_name("Player_Cash")),
        xp = wire.stock_flag(context.instance, stock_name("Player_Xp")),
        advantage = wire.stock_flag(context.instance, advantage.Name), rep = {}}
    for _, id in ipairs(factions) do
        local result = {}
        library:Compose_FactionRep_FlagName(id, context.host, result)
        if not qualified_name(result.Name, faction_flags[id]) then refuse("reputation_flag_unqualified:" .. id) end
        local amount, found = wire.stock_flag(context.instance, result.Name)
        stock.rep[id] = {amount = amount, found = found}
    end
    if stock.flag ~= 0 and stock.flag ~= 1 then refuse("completion_flag_unqualified") end
    return stock
end

local function matches(context)
    return same(context.host.Save, context.save) and same(context.instance.SaveSlot_Ref, context.save)
        and same(context.host:GetWorld(), context.world)
        and (not context.interactor or same(context.interactor.Save, context.receiving_save))
end

local function trait(context)
    local effect, result = FName("IntensePersonality", EFindName.FNAME_Find), {}
    if effect:GetComparisonIndex() == 0 then refuse("reputation_trait_unavailable") end
    context.instance:FindPassiveEffect(effect, true, result, result)
    if type(result.EffectActive) ~= "boolean" or not integer(result.Instances) or result.Instances < 0 then
        refuse("reputation_trait_unqualified")
    end
    return result.Instances
end

local function unchanged_reputation(before, after, delta, instances)
    for _, id in ipairs(factions) do
        local old, current = before.rep[id], after.rep[id]
        local wanted = delta and delta[id]
        local expected = old.amount + (wanted or 0) * (old.found and 1 + (instances or 0) or 1)
        if not integer(expected) or current.amount ~= expected or current.found ~= (old.found or wanted ~= nil) then return false end
    end
    return true
end

local function placed(world, path, class)
    local actor = StaticFindObject(path)
    if not valid(actor) then return nil, "absent" end
    if not exact(actor, class) or actor:GetFullName():match("^%S+ (.+)$") ~= path
        or not same(actor:GetWorld(), world) or not same(actor:GetOuter(), world.PersistentLevel) then return nil, "foreign" end
    if actor:IsActorBeingDestroyed() then return nil, "retired" end
    return actor, "present"
end

local function owned_audio(actor)
    local component = actor.AudioComponent
    if not valid(component) then return nil, "absent" end
    if exact(component, "/Script/Engine.AudioComponent") and same(component:GetOwner(), actor) then return component, "owned" end
    return nil, "foreign"
end

local function dormant(actor)
    return actor.bHidden == true and actor:GetActorEnableCollision() == false and actor:IsActorTickEnabled() == false
end

local function retired(world, actor, kind)
    if not actor or not retirement or not same(retirement.world, world) or not same(retirement[kind], actor) or not dormant(actor) then
        return false
    end
    if kind == "sludge" then return true end
    local audio, status = owned_audio(actor)
    return retirement.audio_muted == true and (status == "absent" or same(retirement.component, audio) and audio.VolumeMultiplier == 0)
end

local function subway_scene(state)
    local world = state.world
    if not enabled or state.map ~= area or state.listening ~= true or not valid(world)
        or world:GetFullName() ~= "World " .. world_path then return end
    local control = placed(world, valve_path, valve_class)
    if not control or control.Lock_AfterUse ~= true or control.SaveState_Mode ~= 2 or control.bReplicates ~= false
        or type(control.Powered) ~= "boolean" then return end
    local pos = control:K2_GetActorLocation()
    if not vector(pos) or square(pos, {X = -10650, Y = -8328.04707757, Z = 286.86104472}) > 1 then return end
    local loaded_box_type = StaticFindObject("/Script/Engine.BoxComponent")
    if not valid(loaded_box_type) then return end
    local boxes = control:K2_GetComponentsByClass(loaded_box_type)
    local shape = type(boxes)
    if shape == "userdata" and type(boxes.type) == "function" then shape = boxes:type() end
    local count = (shape == "table" or shape == "TArray") and #boxes or nil
    if count ~= 0 and count ~= 1 then return end
    local box = count == 1 and boxes[1] or nil
    if type(box) == "userdata" and type(box.type) == "function" and box:type() == "RemoteUnrealParam" then box = box:get() end
    if count == 1 and (not same(box, control.Box) or not exact(box, "/Script/Engine.BoxComponent")
        or not same(box:GetOwner(), control) or box:GetFullName() ~= "BoxComponent " .. valve_path .. ".Box") then return end
    local sludge, sludge_state = placed(world, sludge_path, sludge_class)
    local audio, audio_state = placed(world, audio_path, audio_class)
    if sludge_state == "foreign" or audio_state == "foreign" then return end
    if retired(world, sludge, "sludge") then sludge, sludge_state = nil, "retired" end
    if retired(world, audio, "audio") then audio, audio_state = nil, "retired" end
    return {world = world, control = control, box = box, sludge = sludge, audio = audio,
        sludge_state = sludge_state, audio_state = audio_state,
        closed = count == 0 and not sludge and not audio, present = count == 1 and sludge ~= nil and audio ~= nil}
end

local function qualified_retirement(scene)
    return (not scene.sludge or scene.sludge.bReplicates == false)
        and (not scene.audio or scene.audio.bReplicates == false and valid(owned_audio(scene.audio)))
end

local function retire_scene_actors(scene)
    local receipt = retirement and same(retirement.world, scene.world) and retirement or {world = scene.world}
    for _, kind in ipairs({"sludge", "audio"}) do
        local actor = scene[kind]
        if actor then
            actor:SetActorEnableCollision(false)
            actor:SetActorTickEnabled(false)
            actor:SetActorHiddenInGame(true)
            if not dormant(actor) then error("mutation_unverified_stock_subway_valve_" .. kind .. "_retirement", 0) end
            receipt[kind] = actor
        end
    end
    if scene.audio then
        local component = owned_audio(scene.audio)
        if not valid(component) then error("mutation_unverified_stock_subway_valve_audio_owner", 0) end
        -- Mixer playback state can outlive Destroy in a headless world. Verify the authored source is silent.
        if component.VolumeMultiplier ~= 0 then component:SetVolumeMultiplier(0) end
        if component.VolumeMultiplier ~= 0 then error("mutation_unverified_stock_subway_valve_audio_mute", 0) end
        receipt.component, receipt.audio_muted = component, true
        component:Stop()
    end
    retirement = receipt
    if scene.sludge then scene.sludge:K2_DestroyActor() end
    if scene.audio then scene.audio:K2_DestroyActor() end
end

local function actor_for(context, state, phase, name)
    if name == context.host:GetFullName() then
        if context.host.Dead ~= false or players.is_downed(context.host) ~= false then refuse("standing_pawn_required") end
        return context.host
    end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not exact(pawn, pawn_class) or not exact(save, save_class) or same(save, context.save) or not same(pawn.Save, save)
        or not players.is_standing_guest(pawn, name) or pawn.Dead ~= false then refuse("standing_pawn_required") end
    return pawn
end

local function sight(pawn, scene, eye)
    local reach = pawn.InteractionRange
    local target = scene.box:K2_GetComponentLocation()
    local location, trace = pawn:K2_GetActorLocation(), StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not vector(location) or not vector(target) or not vector(eye) or not finite(reach) or reach <= 0 or reach > 1000 or not valid(trace)
        or square(location, target) > reach^2 then return false end
    local distance = math.sqrt(square(eye, target))
    if distance <= 1 or distance >= reach then return false end
    local function ending(range)
        local scale = range/distance
        return {X = eye.X + (target.X-eye.X)*scale, Y = eye.Y + (target.Y-eye.Y)*scale, Z = eye.Z + (target.Z-eye.Z)*scale}
    end
    local color, skip, hit = {R = 0, G = 0, B = 0, A = 0}, {pawn}, {}
    local held = pawn.EquipedItem
    if valid(held) and same(held:GetOwner(), pawn) then skip[#skip+1] = held end
    if trace:LineTraceSingle(pawn, eye, ending(reach), 1, true, skip, 0, hit, true, color, color, 0) == true then
        local impact = (hit.OutHit or hit).ImpactPoint
        if not vector(impact) then return false end
        reach = math.min(reach, math.sqrt(square(eye, impact)))
    end
    hit = {}
    if reach <= 1 or trace:LineTraceSingle(pawn, eye, ending(reach), 4, false, skip, 0, hit, true, color, color, 0) ~= true then return false end
    local component = (hit.OutHit or hit).Component
    return component and same(component:Get(), scene.box)
end

local function activate(state, phase, pawn_name, epoch)
    local context = canonical(state, phase)
    local scene = subway_scene(state)
    local trigger = scene and placed(scene.world, trigger_path, trigger_class)
    if not valid_id(epoch, "[^0-9a-f]") or not scene or not scene.present or not scene.control:HasAuthority()
        or not qualified_retirement(scene)
        or not trigger or not trigger:HasAuthority() or trigger.ForceComplete ~= true or trigger.Target_QuestStep ~= 1
        or trigger.Add_QuestStep ~= 1 or not trigger.TrackedQuest or not same(trigger.TrackedQuest.DataTable, StaticFindObject(table_path))
        or trigger.TrackedQuest.RowName:ToString() ~= board.quest_id or not map_entries(trigger.Rep_OnComplete, {[2] = 20})
        or not map_entries(trigger.Adv_OnComplete, {[2] = 200}) or not row_count(trigger.Set_SaveFlags, 0)
        or type(SilverfishDispatchNativeSubwayValve) ~= "function" then refuse("exact_objective_unavailable") end
    local before = progress(context)
    if before.step ~= 1 or before.flag ~= 0 then refuse("active_objective_required") end
    local pawn = actor_for(context, state, phase, pawn_name)
    local camera = pawn.Camera
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn)
        or not sight(pawn, scene, camera:K2_GetComponentLocation()) then refuse("stock_valve_out_of_reach_or_blocked") end
    local instances = trait(context)
    if not integer(before.advantage + 200) then refuse("advantage_range") end
    local ok, dispatched = pcall(SilverfishDispatchNativeSubwayValve, pawn:GetAddress(), scene.control:GetAddress(), scene.box:GetAddress())
    if ok and dispatched == false then refuse("native_dispatch_refused") end
    if not ok or dispatched ~= true then error("mutation_unverified_stock_subway_valve_dispatch", 0) end
    local checked, after = pcall(progress, context)
    if not checked or not matches(context) or after.step ~= 2 or after.flag ~= 1 or after.cash ~= before.cash or after.xp ~= before.xp
        or after.advantage ~= before.advantage + 200 or not unchanged_reputation(before, after, {[2] = 20}, instances) then
        error("mutation_unverified_stock_subway_valve_objective", 0)
    end
    action_reply = {world = context.world, pawn = pawn_name, epoch = epoch, before = before, after = after}
    return "stock Flood Control valve and quest transition verified"
end

local function finalize(state, phase, pawn_name, epoch)
    local context = canonical(state, phase)
    local scene, proof = subway_scene(state), action_reply
    if not scene or not scene.control:HasAuthority() or not proof or not same(context.world, proof.world)
        or proof.pawn ~= pawn_name or proof.epoch ~= epoch or not valid_id(epoch, "[^0-9a-f]")
        or not qualified_retirement(scene) then refuse("scene_finalization_scope_unavailable") end
    if scene.box then refuse("scene_stock_box_still_present") end
    local before = progress(context)
    if before.step ~= 2 or before.flag ~= 1 or before.cash ~= proof.after.cash or before.xp ~= proof.after.xp
        or before.advantage ~= proof.after.advantage or not unchanged_reputation(proof.after, before) then
        refuse("scene_stock_objective_changed")
    end
    -- The stock live callback fades the audio and advances the quest. Its saved-flag level initialization
    -- removes these two exact actors. Match that scene here without replaying the level script or quest.
    local checked, stable = pcall(function()
        retire_scene_actors(scene)
        local after = progress(context)
        return matches(context) and after.step == before.step and after.flag == before.flag and after.cash == before.cash
            and after.xp == before.xp and after.advantage == before.advantage and unchanged_reputation(before, after)
    end)
    if not checked or not stable then error("mutation_unverified_stock_subway_valve_scene_finalization", 0) end
    return "exact Flood Control authority presentation requested"
end

local function identity(row)
    local guid = row and row[guid_field]
    if not guid then return end
    local words = {guid.A, guid.B, guid.C, guid.D}
    for index = 1, 4 do if not integer(words[index]) then return end end
    local result = string.format("%08X%08X%08X%08X", words[1]&0xffffffff, words[2]&0xffffffff,
        words[3]&0xffffffff, words[4]&0xffffffff)
    if valid_id(result, "[^0-9A-F]") then return result end
end

local function rewards_in(save, art, mask)
    local result = {}
    local function record(row, backpack)
        local class = row and row[class_field]
        if not (same(class, art) or same(class, mask)) then return end
        local guid = identity(row)
        if not guid or result[guid] then refuse("reward_inventory_identity_unavailable") end
        result[guid] = {backpack = backpack, index = same(class, art) and 1 or 2}
    end
    for index, items in ipairs({save.PlayerBackpack, save.PlayerEquipment}) do
        local size = units(items)
        if not size or size > 4096 then refuse("reward_inventory_extent_unavailable") end
        for position = 1, size do record(items[position], index == 1) end
    end
    record(save.PlayerArmor, false)
    record(save.PlayerHelmet, false)
    return result
end

local function additions(before, after)
    local found = {}
    for guid, item in pairs(after) do
        local original = before[guid]
        if not original then
            if not item.backpack or found[item.index] then return end
            found[item.index] = guid
        elseif original.backpack ~= item.backpack or original.index ~= item.index then return end
    end
    for guid in pairs(before) do if not after[guid] then return end end
    if found[1] and found[2] then return found end
end

local function board_action(state, phase, name, operation)
    local context = wire.stock_board_context(state, phase, name, false, board)
    local template = wire.stock_quest_table():FindRow(board.quest_id)
    local art, mask = recipe(template)
    if template[step_key] ~= 1 then refuse("quest_template_step_changed") end
    local before, after, bundle = progress(context)
    after = before
    if before.flag ~= ((before.step == 2 or before.step == -10) and 1 or 0) then refuse("quest_and_world_flag_disagree") end
    local function offered()
        context.board:Configure_Stock()
        local row = wire.stock_row(context.board.Contracts, context.key)
        if not row then return false end
        recipe(row)
        if row[step_key] ~= 1 then refuse("offered_step_changed") end
        return true
    end
    if operation == "xp-fixture" then
        if not fixtures or not same(context.host, context.interactor) or before.step ~= "-" or before.xp ~= 0
            or #context.save.ActiveQuests ~= 0 or offered() then refuse("xp_fixture_fresh_host_required") end
        local checked, observed = pcall(function()
            context.instance:GivePlayer_XP(7)
            return offered() and progress(context) or nil
        end)
        if not checked or not observed or not matches(context) or observed.step ~= "-" or observed.xp ~= 7
            or observed.flag ~= 0 or observed.cash ~= before.cash or observed.advantage ~= before.advantage
            or not unchanged_reputation(before, observed) then error("mutation_unverified_stock_subway_valve_xp_fixture", 0) end
        after = observed
    elseif before.step == "-" and operation ~= "turn-in" and not offered() then
        refuse("quest_not_offered")
    end
    if operation == "start" then
        if before.step ~= "-" then refuse("quest_already_started_or_completed") end
        local checked, observed = pcall(function()
            context.instance:AddQuest_ByName(context.key)
            return progress(context)
        end)
        if not checked or not observed or not matches(context) or observed.step ~= 1 or observed.flag ~= 0
            or observed.xp ~= before.xp or observed.cash ~= before.cash or observed.advantage ~= before.advantage
            or not unchanged_reputation(before, observed) then error("mutation_unverified_stock_subway_valve_start", 0) end
        after = observed
    elseif operation == "turn-in" then
        if before.step ~= 2 or before.flag ~= 1 then refuse("quest_objective_not_complete") end
        local library = StaticFindObject(code_path)
        if not valid(library) or library:GetFullName() ~= "FL_CodeLib01_C " .. code_path
            or not integer(before.xp + 1) or type(SilverfishCaptureSubwayValveRewards) ~= "function"
            or type(SilverfishGrantSubwayValveRewards) ~= "function" then refuse("settlement_unavailable") end
        local prior, instances = rewards_in(context.save, art, mask), trait(context)
        local checked, observed, gained = pcall(function()
            local output = {}
            library:TurnIn_Quest(context.key, context.instance, true, context.host, output, output)
            local proof = progress(context)
            if output.Success ~= true or valid(output.ConfirmationPopup) or not matches(context) or proof.step ~= -10
                or proof.flag ~= 1 or proof.cash ~= before.cash or proof.xp ~= before.xp + 1 or proof.advantage ~= before.advantage
                or not unchanged_reputation(before, proof, reward_rep, instances) then return end
            return proof, additions(prior, rewards_in(context.save, art, mask))
        end)
        if not checked or not observed or not gained then error("mutation_unverified_stock_subway_valve_turn_in", 0) end
        after, bundle = observed, gained
    end
    board_reply = {world = context.world, pawn = name, operation = operation, before = before, after = after, bundle = bundle}
    return "verified stock Flood Control contract"
end

local function escrow(fields, state, phase, operation)
    local context = canonical(state, phase)
    local slot, art, mask = fields[3], fields[4], fields[5]
    if not slot_ok(slot) or not valid_id(art, "[^0-9A-F]") or not valid_id(mask, "[^0-9A-F]") or art == mask
        or progress(context).step ~= -10 or type(SilverfishVerifySubwayValveRewards) ~= "function" then refuse("reward_scope_unavailable") end
    local name, assigned_art, assigned_mask = context.host:GetFullName(), art, mask
    local detail
    if operation == "reward-capture" then
        if #fields ~= 5 or state.map ~= board.map or context.statics:DoesSaveGameExist(slot, 0) ~= false
            or type(SilverfishCaptureSubwayValveRewards) ~= "function" then refuse("reward_capture_scope_unavailable") end
        local scratch = context.statics:CreateSaveGameObject(StaticFindObject(save_class))
        if not exact(scratch, save_class) then refuse("reward_scratch_unavailable") end
        local ok, reason = SilverfishCaptureSubwayValveRewards(context.host:GetAddress(), context.save:GetAddress(), scratch:GetAddress(), art, mask)
        if ok ~= true or reason ~= "captured" or context.statics:SaveGameToSlot(scratch, slot, 0) ~= true then
            error("mutation_unverified_stock_subway_valve_reward_capture:" .. tostring(reason), 0)
        end
        local reopened = context.statics:LoadGameFromSlot(slot, 0)
        if not exact(reopened, save_class)
            or not SilverfishVerifySubwayValveRewards(reopened:GetAddress(), art, mask, scratch:GetAddress()) then
            error("mutation_unverified_stock_subway_valve_reward_saved_template", 0)
        end
        detail = reason
    else
        name, assigned_art, assigned_mask = fields[6], fields[7], fields[8]
        if #fields ~= 8 or name == context.host:GetFullName() or not valid_id(assigned_art, "[^0-9A-F]")
            or not valid_id(assigned_mask, "[^0-9A-F]") or assigned_art == assigned_mask
            or type(SilverfishGrantSubwayValveRewards) ~= "function" then refuse("reward_grant_scope_unavailable") end
        local pawn, save = guest_save.exact_item_context(state, phase, name)
        local saved = context.statics:LoadGameFromSlot(slot, 0)
        if not exact(pawn, pawn_class) or not exact(save, save_class) or same(save, context.save) or not same(pawn.Save, save)
            or not exact(saved, save_class) or not SilverfishVerifySubwayValveRewards(saved:GetAddress(), art, mask) then
            refuse("reward_recipient_or_template_unavailable")
        end
        local ok, reason = SilverfishGrantSubwayValveRewards(pawn:GetAddress(), save:GetAddress(), context.save:GetAddress(),
            saved:GetAddress(), art, mask, assigned_art, assigned_mask)
        if ok ~= true or (reason ~= "granted" and reason ~= "already_granted") then error(tostring(reason), 0) end
        detail = reason
    end
    reward_reply = {world = context.world, pawn = name, slot = slot, state = detail, art = art, mask = mask,
        assigned_art = assigned_art, assigned_mask = assigned_mask}
    return "exact Flood Control reward bundle verified"
end

local function replica(state, phase)
    if phase ~= "connected" or state.server ~= true then return end
    local scene = subway_scene(state)
    if not scene then return end
    local driver, detail, controller = scene.world.NetDriver, state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    local connection = valid(driver) and driver.ServerConnection
    if not valid(connection) or not same(driver.World, scene.world) or not same(connection.Driver, driver)
        or not exact(pawn, pawn_class) or not exact(controller, controller_class) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), scene.world) or not same(controller:GetWorld(), scene.world)
        or not pawn:IsLocallyControlled() or not controller:IsLocalController() or pawn:HasAuthority() or controller:HasAuthority()
        or pawn:GetFullName() ~= detail.name then return end
    scene.pawn, scene.connection = pawn, connection
    return scene
end

local function project(state, phase, epoch, name)
    local scene = replica(state, phase)
    if not scene or not valid_id(epoch, "[^0-9a-f]") or name ~= scene.pawn:GetFullName()
        or not qualified_retirement(scene)
        or not valid(StaticFindObject(valve_class .. ":Lock_Valve")) then refuse("exact_replica_required") end
    if not scene.closed then
        if scene.box then scene.control:Lock_Valve() end
        retire_scene_actors(scene)
    end
    projected = {world = scene.world, pawn = scene.pawn, epoch = epoch, connection = scene.connection}
    return "exact local Flood Control presentation requested"
end

local function fixture(state, phase, pawn_name, operation)
    if not fixtures then refuse("fixture_disabled") end
    local context = canonical(state, phase)
    local scene = subway_scene(state)
    if not scene or not scene.present or progress(context).step ~= 1 then refuse("fixture_active_valve_required") end
    local pawn = actor_for(context, state, phase, pawn_name)
    local camera, center = pawn.Camera, scene.box:K2_GetComponentLocation()
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn)
        or not vector(center) then refuse("fixture_camera_unavailable") end
    local function position()
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local capsule = pawn.CapsuleComponent
        local origin, rotation = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
        if not valid(trace) or not exact(capsule, "/Script/Engine.CapsuleComponent")
            or not same(capsule:GetOwner(), pawn) or not vector(origin) or not rotation then refuse("fixture_floor_unavailable") end
        local height = capsule:GetScaledCapsuleHalfHeight()
        if not finite(height) or height < 20 or height > 150 then refuse("fixture_floor_unavailable") end
        local ignored, color, moved = {pawn}, {R = 0, G = 0, B = 0, A = 0}, false
        for _, entity in ipairs(state.pawns or {}) do
            if valid(entity.pawn) and not same(entity.pawn, pawn) then ignored[#ignored+1] = entity.pawn end
        end
        local top, bottom = center.Z + 110, center.Z - 240
        for _, distance in ipairs({120, 150, 185, 95}) do
            for angle = 0, 7 do
                local x, y = center.X + distance*math.cos(angle*math.pi/4), center.Y + distance*math.sin(angle*math.pi/4)
                local hit = {}
                local found = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom},
                    0, false, ignored, 0, hit, true, color, color, 0)
                hit = hit.OutHit or hit
                if found == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= .7
                    and hit.ImpactPoint.Z >= bottom and hit.ImpactPoint.Z < top - 1 then
                    local at = {X = x, Y = y, Z = hit.ImpactPoint.Z + height + 2}
                    if pawn:K2_TeleportTo(at, rotation) then
                        moved = true
                        local actual = pawn:K2_GetActorLocation()
                        if vector(actual) and square(actual, at) <= 12^2 and sight(pawn, scene, camera:K2_GetComponentLocation()) then
                            pawn:ForceNetUpdate()
                            return
                        end
                    end
                end
            end
        end
        if moved then
            if not pawn:K2_TeleportTo(origin, rotation) then error("mutation_unverified_subway_valve_fixture_rollback", 0) end
            local actual = pawn:K2_GetActorLocation()
            if not vector(actual) or square(actual, origin) > 12^2 then error("mutation_unverified_subway_valve_fixture_rollback", 0) end
            pawn:ForceNetUpdate()
        end
        refuse("fixture_clear_floor_unavailable")
    end
    if operation == "position-fixture" then
        position()
        fixture_reply = {world = scene.world, pawn = pawn_name, operation = "positioned"}
        return "exact Flood Control valve approach verified"
    end
    if not same(pawn, context.host) or type(SilverfishProbeNativeInteractInput) ~= "function" then
        refuse("fixture_exact_local_input_required")
    end
    local before, eye = camera.RelativeRotation, camera:K2_GetComponentLocation()
    if not before or not finite(before.Pitch) or not finite(before.Yaw) or not finite(before.Roll)
        or not vector(eye) or not sight(pawn, scene, eye) then refuse("fixture_stock_sight_unavailable") end
    local original = {Pitch = before.Pitch, Yaw = before.Yaw, Roll = before.Roll}
    local x, y, z = center.X-eye.X, center.Y-eye.Y, center.Z-eye.Z
    local aimed = {Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0}
    local ok, failure = pcall(function()
        camera:K2_SetWorldRotation(aimed, false, {}, true)
        pawn:LookTrace()
        if pawn.CanInteract ~= true or not same(pawn.InteractionTarget_Component, scene.box) then refuse("fixture_stock_focus_unavailable") end
        local accepted, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if accepted ~= true or reason ~= "stock_started_consumed" then
            error("mutation_unverified_subway_valve_fixture_input:" .. tostring(reason), 0)
        end
    end)
    local restored, verified = pcall(function()
        if not same(pawn.Camera, camera) then return false end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        pawn:LookTrace()
        local actual = camera.RelativeRotation
        return actual and math.abs((actual.Pitch-original.Pitch+180)%360-180) <= .05
            and math.abs((actual.Yaw-original.Yaw+180)%360-180) <= .05
            and math.abs((actual.Roll-original.Roll+180)%360-180) <= .05
    end)
    if not restored or not verified then error("mutation_unverified_subway_valve_fixture_camera", 0) end
    if not ok then error(failure, 0) end
    fixture_reply = {world = scene.world, pawn = pawn_name, operation = "stock_input"}
    return "exact Flood Control stock Interact consumed"
end

function valve.clear()
    action_reply, board_reply, reward_reply, projected, fixture_reply, retirement = nil, nil, nil, nil, nil, nil
end

function valve.command(fields, state, phase)
    local operation = type(fields[2]) == "string" and fields[2]:match("^native%-subway%-valve%-(.+)$")
    if not operation then return end
    if operation == "activate" and #fields == 4 then
        action_reply = nil
        return activate(state, phase, fields[3], fields[4])
    elseif operation == "finalize" and #fields == 4 then
        return finalize(state, phase, fields[3], fields[4])
    elseif operation == "project" and #fields == 4 then
        return project(state, phase, fields[3], fields[4])
    elseif operation == "reward-capture" or operation == "reward-grant" then
        reward_reply = nil
        return escrow(fields, state, phase, operation)
    elseif #fields == 3 and (operation == "position-fixture" or operation == "input-fixture") then
        fixture_reply = nil
        return fixture(state, phase, fields[3], operation)
    elseif operations[operation] and #fields == 3 then
        board_reply = nil
        if operation == "approach" then return wire.stock_board_approach(state, phase, fields[3], board) end
        return board_action(state, phase, fields[3], operation)
    end
    refuse("arguments_unqualified")
end

function valve.rows(state, phase, row)
    local rows = {}
    if phase == "hosting" then
        local scene = subway_scene(state)
        local side = scene and (scene.closed and "closed" or scene.present and "present" or "settling")
        if side then rows[#rows+1] = row("native-subway-valve-scene", area, side, "host", "-",
            scene.box and "present" or "absent", scene.sludge_state, scene.audio_state, scene.control.Powered and 1 or 0) end
        if fixture_reply and scene and same(scene.world, fixture_reply.world) then
            rows[#rows+1] = row("native-subway-valve-fixture", area, fixture_reply.pawn, fixture_reply.operation)
        end
        local objective = action_reply
        if objective and scene and same(scene.world, objective.world) then
            local before, after = objective.before, objective.after
            rows[#rows+1] = row("native-subway-valve-objective", area, "ValveSwitch_BP_C " .. valve_path,
                "Quest_Trigger_BP_C " .. trigger_path, objective.pawn, objective.epoch, before.step, after.step,
                before.flag, after.flag, before.cash, after.cash, before.xp, after.xp, before.rep[2].amount,
                after.rep[2].amount, before.advantage, after.advantage)
        end
        local result = board_reply
        if result and state.map == board.map and same(result.world, state.world) then
            local before, after, bundle = result.before, result.after, result.bundle or {"-", "-"}
            rows[#rows+1] = row("native-subway-valve-board", result.operation, board.map, result.pawn, board.board_name,
                before.step, after.step, after.flag, before.cash, after.cash, before.xp, after.xp, bundle[1], bundle[2])
            for _, id in ipairs(factions) do
                rows[#rows+1] = row("native-subway-valve-rep", id, before.rep[id].amount, after.rep[id].amount)
            end
        end
        local reward = reward_reply
        if reward and same(reward.world, state.world) then
            rows[#rows+1] = row("native-subway-valve-reward", state.map, reward.pawn, reward.state, reward.slot,
                reward.art, reward.mask, reward.assigned_art, reward.assigned_mask)
        end
    elseif phase == "connected" then
        local scene = replica(state, phase)
        if scene then
            local current = projected and same(scene.world, projected.world) and same(scene.pawn, projected.pawn)
                and same(scene.connection, projected.connection)
            local surface = scene.closed and "closed" or scene.present and "present" or "settling"
            rows[#rows+1] = row("native-subway-valve-scene", area, surface, scene.pawn:GetFullName(), current and projected.epoch or "-",
                scene.box and "present" or "absent", scene.sludge_state, scene.audio_state, scene.control.Powered and 1 or 0)
        end
    end
    return rows
end

return valve
