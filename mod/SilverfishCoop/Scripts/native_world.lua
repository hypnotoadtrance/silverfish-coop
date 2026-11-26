-- Inspect the stock merchants and one staged quest on the authoritative host.
local native_world = {}
local routes = require("zone_maps")
local world_seed = require("world_seed")

native_world.prepare_seed = world_seed.prepare_seed
native_world.prepare_loaded_seed = world_seed.prepare_loaded_seed
native_world.prepare_travel_seed = world_seed.prepare_travel_seed

local playtest_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local shop_class_path = "/Game/Classes/Actors/Shop_BP.Shop_BP_C"
local shop_ids = { "Shop_BP_C_0", "Shop_BP_C_1" }
local buckets = {
    { "stock", "StockInventory" },
    { "finite", "FiniteInventory" },
    { "buyback", "BuyBackInventory" },
}
local max_offers_per_bucket = 24
local gameplay_statics_path = "/Script/Engine.Default__GameplayStatics"
local quest_library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local quest_id = "Jmr_CrossRoads"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local quest_step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local quest_final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B"
local quest_cash = "Reward_Cash_24_CDC8F5D24E1EBE65914E3B88621E50D0"
local kismet_math_path = "/Script/Engine.Default__KismetMathLibrary"
local storm_seed = "Seed_16_224EDCA44017165C45B378BCE4251868"
local storm_enabled = "Activated_19_0B381CD94413F01EAE590297A57BD87B"
local storm_class = "Storm_7_1FF2FAC343119542F0A40FAB3A93D49F"
local rule_fields = {
    { "extraction_mode", "ExtractionMode_45_FC0B60E8470417127E7367B262395F41" },
    { "permadeath", "PermaDeath_47_9B31E81C4D87339B7E5858915E149876" },
    { "replenish_loot", "ReplenishLoot_5_0542042A4D8D98E206019BA9B5A3E23F" },
    { "save_on_level_transition", "SaveOn_LevelTransition_7_B709174E4B3AAF09E16C5DA11B61EA65" },
    { "can_save_in_menu", "CanSave_InMenu_11_62445BFB482BAF72F8AEECB71E78E918" },
    { "fail_contracts_on_death", "FailContracts_OnDeath_9_2B5C7A364BAB9B21A5358E8D6A55369F" },
    { "items_kept_on_death", "Items_KeptOnDeath_14_9602AB054254974168B43BB0C2924307" },
    { "lose_slot_per_death", "LoseSlot_PerDeath_41_60DB8A134132D666D7FD9E9714F51A77" },
    { "randomize_loot", "Randomize_Loot_17_A163022E40E9CA62A6DCC1B791990083" },
    { "randomize_npcs", "Randomize_NPCs_19_3AF748864F3403D76A5428AA591EFA30" },
    { "drop_bone_pile", "Drop_BonePile_43_877E5A2C4F77E5E91CB0ED82EBC3738D" },
}

local cached_rows
local cached_time
local cached_world

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function host_pawn(state, phase)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase == "hosting" and (playtest_maps[state.map] or (authored_routes and routes[state.map]))
        and valid(pawn) and pawn:HasAuthority() then
        return pawn
    end
end

local function int32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil
        and value >= -2147483648 and value <= 2147483647
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 10000000
end

local function brief(message)
    return tostring(message):sub(1, 180)
end

local function shop_actors(world)
    local statics, class = StaticFindObject(gameplay_statics_path), StaticFindObject(shop_class_path)
    local actors
    if valid(statics) and valid(class) then
        local output = {}
        statics:GetAllActorsOfClass(world, class, output)
        actors = output.OutActors or output
    else
        actors = FindAllOf("Shop_BP_C") or {}
    end
    local matches = {}
    for _, actor in ipairs(actors) do
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        if valid(actor) and actor:HasAuthority() and not actor:IsActorBeingDestroyed() then
            local name = actor:GetFullName()
            local id = name:match("^Shop_BP_C /[^%s]+:PersistentLevel%.(Shop_BP_C_[01])$")
            local owner_world = actor:GetWorld()
            if id and valid(owner_world) and owner_world:GetAddress() == world:GetAddress() then
                if matches[id] ~= nil then
                    matches[id] = false
                else
                    matches[id] = actor
                end
            end
        end
    end
    return matches
end

local function guid_string(guid)
    if not guid or not int32(guid.A) or not int32(guid.B)
        or not int32(guid.C) or not int32(guid.D) then
        return "-"
    end
    if guid.A == 0 and guid.B == 0 and guid.C == 0 and guid.D == 0 then
        return "-"
    end
    return string.format("%08x%08x%08x%08x", guid.A & 0xffffffff, guid.B & 0xffffffff,
        guid.C & 0xffffffff, guid.D & 0xffffffff)
end

local function offer_row(status_row, id, bucket, index, item)
    if not item then error("Inventory item is unavailable", 0) end
    local class = item[item_class]
    local class_path = valid(class) and class:GetFullName():match("^%S+ (%S+)$")
    local ammo, value = item[item_ammo], item[item_value]
    if not class_path or #class_path > 256 or not class_path:match("^/Game/[A-Za-z0-9_/%-]+%.[A-Za-z0-9_%-]+$")
        or not int32(ammo) or not int32(value) then
        error("Inventory item schema is unavailable", 0)
    end
    local ok, guid = pcall(guid_string, item[item_guid])
    return status_row("native-offer", id, bucket, index - 1, class_path, ok and guid or "-", ammo, value)
end

local function append_shop(rows, state_row, id, actor)
    if actor == false then error("Shop actor identifier is ambiguous", 0) end
    if not actor then
        rows[#rows + 1] = state_row("native-shop", id, "-", "-", "-", "-", "-", "missing", "placed actor not found")
        return
    end

    local position, cash = actor:K2_GetActorLocation(), actor.Cash
    if not finite(position.X) or not finite(position.Y) or not finite(position.Z) or not int32(cash) then
        error("Shop position or cash schema is unavailable", 0)
    end

    local offers = {}
    local notes = {}
    for _, bucket in ipairs(buckets) do
        local label, field = bucket[1], bucket[2]
        local ok, items, count = pcall(function()
            local stock = actor[field]
            return stock, stock:GetArrayNum()
        end)
        if not ok or not int32(count) or count < 0 then
            notes[#notes + 1] = label .. " array unavailable"
        else
            if count > max_offers_per_bucket then
                notes[#notes + 1] = label .. " truncated at " .. max_offers_per_bucket
            end
            local failures = 0
            -- UE4SS's TArray indexing is one-based and can append on invalid reads.
            -- Bound all reads by the real current length while on the game thread.
            for index = 1, math.min(count, max_offers_per_bucket) do
                local read, result = pcall(function()
                    return offer_row(state_row, id, label, index, items[index])
                end)
                if read then offers[#offers + 1] = result else failures = failures + 1 end
            end
            if failures > 0 then notes[#notes + 1] = label .. " unavailable entries: " .. failures end
        end
    end
    rows[#rows + 1] = state_row("native-shop", id, actor:GetFullName(), position.X, position.Y, position.Z,
        cash, #notes == 0 and "ok" or "partial", brief(table.concat(notes, ", ")))
    for _, offer in ipairs(offers) do rows[#rows + 1] = offer end
end

local function game_instance(pawn)
    local statics = StaticFindObject(gameplay_statics_path)
    if not valid(statics) then error("Quest GameplayStatics is unavailable", 0) end
    local instance = statics:GetGameInstance(pawn)
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not valid(instance.SaveSlot_Ref) then
        error("Quest host game-instance save object is unavailable", 0)
    end
    return instance
end

local function scene_text(value)
    if type(value) == "userdata" then value = value:ToString() end
    if type(value) == "string" and #value <= 256 then return value end
end

local function scene_bool(value)
    if type(value) == "boolean" then return value and 1 or 0 end
end

local function scene_int(value)
    if int32(value) then return value end
end

local function scene_count(value)
    local count = #value
    if int32(count) and count >= 0 and count <= 1000000 then return count end
end

local function scene_seconds(timespan)
    local math_cdo = StaticFindObject(kismet_math_path)
    if not valid(math_cdo) then return nil end
    local result = {}
    math_cdo:BreakTimespan(timespan, result, result, result, result, result)
    for _, name in ipairs({ "Days", "Hours", "Minutes", "Seconds", "Milliseconds" }) do
        if not int32(result[name]) then return nil end
    end
    if math.abs(result.Days) > 10675200 or math.abs(result.Hours) > 23 or math.abs(result.Minutes) > 59
        or math.abs(result.Seconds) > 59 or math.abs(result.Milliseconds) > 999 then
        return nil
    end
    return result.Days * 86400 + result.Hours * 3600 + result.Minutes * 60
        + result.Seconds + result.Milliseconds / 1000
end

local function scene_class(class)
    if not valid(class) then return "-" end
    local path = class:GetFullName():match("^%S+ (%S+)$")
    if path and #path <= 256 and path:match("^/Game/[A-Za-z0-9_/%-]+%.[A-Za-z0-9_%-]+$") then
        return path
    end
end

local function scene_field(notes, id, object, field, convert)
    local ok, value = pcall(function()
        return convert(object[field])
    end)
    if ok and value ~= nil then return value end
    notes[#notes + 1] = id
    return "-"
end

function native_world.reputation_flags(save)
    local flags = save.Flags
    local count = scene_count(flags)
    if not count or count > 1024 then error("Native flags exceed the telemetry bound", 0) end
    local result = {}
    flags:ForEach(function(key, raw_points)
        local name = scene_text(key:get():ToString())
        if name and name:match("^Rep_[A-Za-z0-9_%-]+$") then
            local points = raw_points:get()
            if int32(points) then result[#result + 1] = { name, points } end
            if #result >= 32 then return true end
        end
    end)
    table.sort(result, function(a, b) return a[1] < b[1] end)
    return result
end

local function append_scene(rows, state_row, map, instance)
    local notes = {}
    local save = instance.SaveSlot_Ref
    local level = scene_field(notes, "saved level", save, "Level", scene_text)
    local entrance = scene_field(notes, "entrance", save, "Entrance", scene_text)
    local rest_level = scene_field(notes, "rest level", save, "RespawnLevel", scene_text)
    local bed = scene_field(notes, "bed", save, "Bed", scene_text)
    local seed = scene_field(notes, "instance seed", save, "InstanceSeed", scene_int)
    local seconds = scene_field(notes, "game time", save, "GameTime", scene_seconds)
    local weather = scene_field(notes, "weather offset", save, "WeatherOffset", function(value)
        if finite(value) then return value end
    end)
    local storm = scene_field(notes, "ongoing storm", instance, "OngoingStorm", scene_bool)
    local artifacts = scene_field(notes, "artifact areas", save, "Artifacts", scene_count)
    local temporary = scene_field(notes, "temporary activations", save, "TempActivations", scene_count)
    local storm_events = scene_field(notes, "storm activations", save, "StormActivations", scene_count)

    local storm_track = save.StormTrack
    local track_seed = scene_field(notes, "storm track seed", storm_track, storm_seed, scene_int)
    local track_on = scene_field(notes, "storm track activation", storm_track, storm_enabled, scene_bool)
    local storm_asset = scene_field(notes, "storm class", storm_track, storm_class, scene_class)

    local rules = save.GameRules
    for _, rule in ipairs(rule_fields) do
        local key, field = rule[1], rule[2]
        local convert = key == "items_kept_on_death" and scene_int or scene_bool
        rows[#rows + 1] = state_row("native-rule", key, scene_field(notes, key, rules, field, convert))
    end

    local rep_available, reputation = pcall(native_world.reputation_flags, save)
    if rep_available then
        for _, flag in ipairs(reputation) do
            rows[#rows + 1] = state_row("native-reputation", flag[1], flag[2])
        end
    else
        notes[#notes + 1] = "reputation flags"
    end

    local ok, static_seed = pcall(world_seed.static_placement_seed, save)
    if ok and static_seed ~= nil then
        rows[#rows + 1] = state_row("native-static-world-seed", map, static_seed)
    end

    rows[#rows + 1] = state_row("native-scene", map, level, entrance, rest_level, bed, seed, seconds,
        weather, storm, artifacts, temporary, storm_events, track_seed, track_on, storm_asset,
        #notes == 0 and "ok" or "partial", brief(table.concat(notes, ", ")))
end

local function quest_fields(quest)
    local step, final_step, reward = quest[quest_step], quest[quest_final], quest[quest_cash]
    if not int32(step) or not int32(final_step) or final_step < 1 or not int32(reward) or reward < 0 then
        error("Stock quest fields are unavailable", 0)
    end
    local state = step == -10 and "completed"
        or step > 0 and step <= final_step and "active"
        or "inactive"
    return { state = state, step = step, final_step = final_step, reward = reward }
end

local function live_quest(instance, key)
    local quests = instance.SaveSlot_Ref.ActiveQuests
    local found = quests:Contains(key)
    if type(found) ~= "boolean" then error("Stock quest map returned an incompatible result", 0) end
    if not found then return { state = "missing" } end
    return quest_fields(quests:Find(key):get())
end

local function reflected_quest(library, instance, pawn, key)
    local status, quest = {}, {}
    library:Check_QuestStatus(instance, key, pawn, status, quest, status)
    if type(status.Found) ~= "boolean" or type(status.Active) ~= "boolean" then
        error("Stock Check_QuestStatus output shape is unavailable", 0)
    end
    if not status.Found then
        -- The stock Active expression still evaluates the fallback struct's
        -- default step 1 and final step 2 when its map lookup misses.
        return { state = "missing" }
    end
    local result = quest_fields(quest)
    if (result.state == "active") ~= status.Active then
        error("Stock Check_QuestStatus disagrees with its quest progress", 0)
    end
    return result
end

local function same_quest(first, second)
    return first.state == second.state and first.step == second.step
        and first.final_step == second.final_step and first.reward == second.reward
end

local function exact_actor(class, world, actor_name)
    local match
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if valid(actor) and actor:HasAuthority() and actor:GetFullName():match(":" .. "PersistentLevel%." .. actor_name .. "$") then
            local actor_world = actor:GetWorld()
            if valid(actor_world) and actor_world:GetAddress() == world:GetAddress() then
                if match then error("The authored objective actor is ambiguous", 0) end
                match = actor
            end
        end
    end
    return match
end

local function signal_authored_jammer(state, pawn)
    if state.map ~= crossroads or not authored_routes then error("The CrossRoads objective is unavailable", 0) end
    local world = pawn:GetWorld()
    local trigger = exact_actor("Quest_Trigger_BP_C", world, "Quest_Trigger_BP_C_1")
    local jammer = exact_actor("SignalJammer_BP_C", world, "SignalJammer_BP_C_1")
    if not trigger or not jammer or not trigger.ForceComplete or trigger.Target_QuestStep ~= -1
        or trigger.Add_QuestStep ~= 1 or trigger.TrackedQuest.RowName:ToString() ~= quest_id then
        error("The authored jammer objective could not be qualified", 0)
    end
    local receivers = jammer.Activate_OnDeath
    if #receivers ~= 1 or not valid(receivers[1]) or receivers[1]:GetAddress() ~= trigger:GetAddress() then
        error("The stock jammer does not target this objective actor", 0)
    end
    trigger:Send_PowerSignal(true, jammer)
end

function native_world.command(fields, state, phase)
    local action = fields[2]
    if action ~= "quest-probe" and action ~= "native-quest-project" and action ~= "native-quest-turn-in"
        and action ~= "native-quest-trigger" then
        return nil
    end
    local target_step = action == "native-quest-project" and #fields == 3 and tonumber(fields[3])
    if action == "native-quest-project" then
        if target_step ~= 1 and target_step ~= 2 and target_step ~= -10 then error("Invalid quest projection", 0) end
    elseif #fields ~= 2 then
        error("Invalid native quest command", 0)
    end
    local pawn = host_pawn(state, phase)
    if not pawn then error("Only the active test-map host can probe the stock quest", 0) end

    local instance = game_instance(pawn)
    local library = StaticFindObject(quest_library_path)
    if not valid(library) then error("Stock quest library is unavailable", 0) end
    local key = FName(quest_id)
    local before = reflected_quest(library, instance, pawn, key)
    if not same_quest(before, live_quest(instance, key)) then
        error("Stock quest lookup disagrees with the authoritative quest map", 0)
    end
    if action == "native-quest-turn-in" then
        if before.state == "completed" then return "native quest already turned in" end
        if before.step ~= before.final_step or before.step ~= 2 or before.reward ~= 200 then
            error("The stock objective has not been completed", 0)
        end
        local result = {}
        library:TurnIn_Quest(key, instance, true, pawn, result, result)
        cached_rows = nil
        if result.Success ~= true or live_quest(instance, key).state ~= "completed" then
            error("The stock quest did not accept turn-in", 0)
        end
        return "native quest turned in and verified"
    end

    if action == "native-quest-trigger" then
        if before.step ~= 1 then error("Start the authored quest before powering its objective", 0) end
        signal_authored_jammer(state, pawn)
        cached_rows = nil
        if live_quest(instance, key).step ~= 2 then error("The authored objective did not advance", 0) end
        return "authored objective signal advanced the stock quest"
    end

    if before.state == "missing" then
        instance:AddQuest_ByName(key)
        before = live_quest(instance, key)
    end
    if before.final_step ~= 2 or before.reward ~= 200 then
        error("The stock quest does not match the staged table row", 0)
    end
    if target_step and target_step ~= before.step then
        library:AddStep_ToQuest(key, target_step - before.step, instance, pawn)
    end
    cached_rows = nil
    local after = reflected_quest(library, instance, pawn, key)
    if not same_quest(after, live_quest(instance, key)) or (target_step and after.step ~= target_step) then
        error("Native quest projection failed verification", 0)
    end
    return "native quest state verified"
end

local function world_rows(state, phase, status_row)
    local pawn = host_pawn(state, phase)
    if not pawn then
        cached_rows = nil
        return {}
    end
    local world = pawn:GetWorld()
    if not valid(world) then return {} end

    local now, world_address = os.time(), world:GetAddress()
    if cached_rows and cached_world == world_address and cached_time == now then return cached_rows end
    local rows = {}
    local ok, actors = pcall(shop_actors, world)
    for _, id in ipairs(shop_ids) do
        local actor = ok and actors[id]
        local reported, reason = false, actors
        if ok then reported, reason = pcall(append_shop, rows, status_row, id, actor) end
        if not reported then
            local name = actor and actor:GetFullName() or "-"
            rows[#rows + 1] = status_row("native-shop", id, name, "-", "-", "-", "-", "error", brief(reason))
        end
    end
    local loaded, instance = pcall(game_instance, pawn)
    local read, quest = false, instance
    if loaded then read, quest = pcall(live_quest, instance, FName(quest_id)) end
    if read then
        rows[#rows + 1] = status_row("native-quest", quest_id, quest.state, quest.step or "-",
            quest.final_step or "-", quest.reward or "-", "")
    else
        rows[#rows + 1] = status_row("native-quest", quest_id, "error", "-", "-", "-", brief(quest))
    end
    local scene_read, scene_error = false, instance
    if loaded then
        scene_read, scene_error = pcall(append_scene, rows, status_row, state.map, instance)
    end
    if not scene_read then
        rows[#rows + 1] = status_row("native-scene", state.map, "-", "-", "-", "-", "-", "-", "-", "-",
            "-", "-", "-", "-", "-", "-", "error", brief(scene_error))
    end
    cached_rows, cached_time, cached_world = rows, now, world_address
    return rows
end

function native_world.rows(state, phase, status_row)
    local proof = world_seed.row(state, phase, status_row)
    local rows = world_rows(state, phase, status_row)
    return proof and { proof, table.unpack(rows) } or rows
end

return native_world
