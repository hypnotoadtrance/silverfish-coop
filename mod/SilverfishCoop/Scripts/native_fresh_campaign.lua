local fresh = {}

local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local enabled = bridge and bridge ~= "" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local menu = "/Game/Classes/Maps/MainMenu_P"
local destination = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local level = "Observitory_P"
local preset = "wanderer"
local modes = {
    Mercy = {extraction = false, replenish = true, keep = 0},
    Respawn_WtPenalty = {extraction = true, replenish = true, keep = 5},
    Decimation = {extraction = true, replenish = false, keep = 0},
}
local statics_path = "/Script/Engine.Default__GameplayStatics"
local maths_path = "/Script/Engine.Default__KismetMathLibrary"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local menu_mode_path = "/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C"
local character_library_path = "/Game/Classes/Libraries/Fuctions_Lib/FL_Characters.Default__FL_Characters_C"
local armor_path = "/Game/Classes/Armor_Items/Armor_ExpiditionLight.Armor_ExpiditionLight_C"
local bag_path = "/Game/Classes/Items/Random_GearBag_Starter.Random_GearBag_Starter_C"
local note_path = "/Game/Classes/Items/Key_Items/Note_Wanderer.Note_Wanderer_C"
local storm_path = "/Game/Classes/Weather/Data/LightningStorm_Data.LightningStorm_Data"
local storm_class_path = "/Game/Classes/Weather/Storms/LightningStorm_BP.LightningStorm_BP_C"
local storm_curve_path = "/Game/Classes/Weather/StormCurves/GenericStorm_Curve.GenericStorm_Curve"
local table_directory = "/Game/Classes/Libraries/DataTables/"
local struct_directory = "/Game/Classes/Libraries/Structs/"

local appearance = "Starting_Armor_26_62DBA26E4A5E2A9843D8FB9F8A3D9B76"
local starting_items = "Starting_Items_30_E5CF623041C2520E535C948426AB9126"
local traits = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
local reputation = "Starting_FactionReputations_22_BC948164417D9446CCEF4F953610EBD3"
local starting_flags = "StartingFlags_94_A3FF4E4E4B692345BB21B4B1FBDC5CF7"
local spawn_map = "Spawn_Map_35_781D11264117AA867DE5FBBB008A9151"
local spawn_checkpoint = "Spawn_Checkpoint_38_978BB7B54C6EABDF3C57BEB0ADB078F6"
local cash = "Starting_Cash_48_B196F1C1461EF00CACE44F9AD878A91A"
local debt = "Starting_Debt_50_EE8B9A9D4D616032557355923045AEB5"
local xp = "Starting_Xp_46_4ABC028142EB31EE24A69C8807EFB5EB"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local storm_class = "Storm_7_1FF2FAC343119542F0A40FAB3A93D49F"
local storm_curve = "Curve_4_01ED66844315D5CE3688EEA5ABE10B40"
local storm_seed = "Seed_16_224EDCA44017165C45B378BCE4251868"
local storm_time = "TimeZero_10_E5159B6049F6FE6711AEBFB70381881E"
local storm_rate = "PlayRate_13_BE5598FD40BAEA467B961581B19E0DF6"
local storm_active = "Activated_19_0B381CD94413F01EAE590297A57BD87B"

local common_rules = {
    PermaDeath_47_9B31E81C4D87339B7E5858915E149876 = false,
    SaveOn_LevelTransition_7_B709174E4B3AAF09E16C5DA11B61EA65 = true,
    CanSave_InMenu_11_62445BFB482BAF72F8AEECB71E78E918 = false,
    FailContracts_OnDeath_9_2B5C7A364BAB9B21A5358E8D6A55369F = false,
    Randomize_Loot_17_A163022E40E9CA62A6DCC1B791990083 = false,
    Randomize_NPCs_19_3AF748864F3403D76A5428AA591EFA30 = false,
    Min_PlayerExp_36_08B8B29F4779435201A379AB42F1DA69 = 0,
    LoseSlot_PerDeath_41_60DB8A134132D666D7FD9E9714F51A77 = false,
}
-- The serialized NewEnumerator suffixes differ from the runtime values in Factions_Enum.
local wanderer_rep = { [2] = 0, [3] = -400, [4] = -800, [5] = -300, [6] = -1000, [7] = -1000, [8] = 0 }
local faction_advantage = { [2] = 3000, [3] = 3000, [4] = 4000, [5] = 3500, [8] = 2500 }
local receipt

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and not object:GetFullName():find("Default__", 1, true)
        and same(object:GetClass(), StaticFindObject(path))
end

local function text(value)
    if type(value) == "userdata" then return value:ToString() end
    if type(value) == "string" then return value end
end

local function named(class, path)
    return valid(class) and class:GetFullName() == "BlueprintGeneratedClass " .. path
end

local function authored_curve(curve)
    return valid(curve) and curve:GetFullName() == "CurveVector " .. storm_curve_path
end

local function array_count(array)
    return array and array:GetArrayNum()
end

local function i32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil and value >= -2147483648 and value <= 2147483647
end

local function require_exact(value, reason)
    if not value then error("native_fresh_" .. reason, 0) end
    return value
end

local function asset(path)
    local found = StaticFindObject(path)
    if not valid(found) then
        LoadAsset(path)
        found = StaticFindObject(path)
    end
    return found
end

local function data_row(table_name, struct_name, key)
    local path = table_directory .. table_name .. "." .. table_name
    local table_asset = asset(path)
    require_exact(valid(table_asset) and table_asset:GetFullName() == "DataTable " .. path, "table_unavailable")
    local schema = table_asset:GetRowStruct()
    require_exact(valid(schema) and schema:GetFullName() == "UserDefinedStruct " ..
        struct_directory .. struct_name .. "." .. struct_name, "table_schema_changed")
    return require_exact(table_asset:FindRow(key), "table_row_unavailable")
end

local function matches_rule_row(row, mode)
    for field, value in pairs(common_rules) do if row[field] ~= value then return false end end
    return row.ExtractionMode_45_FC0B60E8470417127E7367B262395F41 == mode.extraction
        and row.ReplenishLoot_5_0542042A4D8D98E206019BA9B5A3E23F == mode.replenish
        and row.Items_KeptOnDeath_14_9602AB054254974168B43BB0C2924307 == mode.keep
        and row.ObservitoryVendor_33_F1F6482F4AA17EFFFBEE508D9D657CAE == mode.extraction
        and row.Lock_SaveSlot_38_7CD8888F49C79B45763265A468989B23 == mode.extraction
        and row.Drop_BonePile_43_877E5A2C4F77E5E91CB0ED82EBC3738D == mode.extraction
        and array_count(row.CustomRule_Tags_31_F8A608C241E19BEBBE44A9BFCCDA335C) == 0
end

local function fname(name)
    local key = FName(name, EFindName.FNAME_Find)
    return require_exact(key:GetComparisonIndex() ~= 0 and key:ToString() == name and key, "stock_flag_name_unavailable")
end

local function matches_map(map, expected, count)
    if not map or #map ~= count then return false end
    for key, value in pairs(expected) do
        if map:Contains(key) ~= true or map:Find(key):get() ~= value then return false end
    end
    return true
end

local function character_row()
    local row = data_row("PlayerClasses_DT", "PlayerCharacter_Struct", "Wanderer")
    local gear, passives = row[starting_items], row[traits]
    require_exact(named(row[appearance], armor_path) and array_count(gear) == 2 and named(gear[1], bag_path)
        and named(gear[2], note_path), "wanderer_loadout_changed")
    require_exact(array_count(passives) == 1 and text(passives[1]) == "WeirdLuck", "wanderer_traits_changed")
    require_exact(row[cash] == 23 and row[debt] == 3015220 and row[xp] == 0, "wanderer_starting_balances_changed")
    require_exact(text(row[spawn_map]) == level and text(row[spawn_checkpoint]) == "", "wanderer_spawn_changed")
    require_exact(matches_map(row[reputation], wanderer_rep, 7), "wanderer_reputation_changed")
    require_exact(matches_map(row[starting_flags], { [fname("Class_Wander")] = 1 }, 1), "wanderer_starting_flags_changed")
    return row
end

local function owner(state)
    local controller, world = state.controller, state.world
    if state.server or not valid(world) or not exact(controller, controller_path)
        or not controller:IsLocalController() or not controller:HasAuthority() or not same(controller:GetWorld(), world) then return end
    local basename = state.map and state.map:match("/([%w_]+)$")
    if not basename or world:GetFullName() ~= "World " .. state.map .. "." .. basename then return end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return end
    local instance = statics:GetGameInstance(controller)
    if not exact(instance, instance_path) or not exact(instance.SaveSlot_Ref, save_path) then return end
    return instance, statics
end

local function qualified_title(state, phase)
    if not enabled or phase ~= "ready" or state.map ~= menu or state.listening or state.clients ~= 0 or state.local_pawn then return end
    local instance, statics = owner(state)
    if not instance then return end
    local menu_mode = statics:GetGameMode(state.controller)
    if not exact(menu_mode, menu_mode_path) or not same(menu_mode:GetWorld(), state.world)
        or exact(state.controller.Pawn, pawn_path) or exact(instance["As Player FP"], pawn_path) then return end
    return instance
end

local function flag(instance, key, expected)
    local result = {}
    instance:Check_SaveFlag(type(key) == "string" and fname(key) or key, result, result)
    return result.Found == true and result.Value == expected
end

local function derived_seed(seed, offset)
    return (seed * 48271 + offset) % 2147483646 + 1
end

local function starter(seed, slot)
    return {
        slot = slot, seed = seed,
        world_seed = derived_seed(seed, 7143), artifact_seed = derived_seed(seed, 132001),
        storm_seed = derived_seed(seed, 1171451), weather = (seed * 23311 + 19) % 5001 - 5000,
        rate = 0.75 + ((seed * 337) % 351) / 1000,
    }
end

local function timespan_is(maths, value, days, hours, minutes)
    local parts = {}
    maths:BreakTimespan(value, parts, parts, parts, parts, parts)
    return parts.Days == days and parts.Hours == hours and parts.Minutes == minutes
        and parts.Seconds == 0 and parts.Milliseconds == 0
end

local function preserved_inventory(save)
    local source, rows = save.PlayerBackpack, {}
    require_exact(array_count(source) == 2 and named(source[1][item_class], bag_path)
        and named(source[2][item_class], note_path) and named(save.PlayerArmor[item_class], armor_path), "loadout_readback_failed")
    for index, item in ipairs({ source[1], source[2], save.PlayerArmor }) do
        local guid = item[item_guid]
        require_exact(guid and i32(guid.A) and i32(guid.B) and i32(guid.C) and i32(guid.D), "invalid_loadout_guid")
        rows[index] = string.format("%08X%08X%08X%08X", guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff)
        require_exact(rows[index]:find("[^0]"), "invalid_loadout_guid")
    end
    require_exact(rows[1] ~= rows[2] and rows[1] ~= rows[3] and rows[2] ~= rows[3], "duplicate_loadout_guid")
    return table.concat(rows, ",")
end

local function verify(instance, save, planned, strict)
    require_exact(same(instance.SaveSlot_Ref, save) and text(instance.CurrentSaveSlot) == planned.slot
        and instance.SaveAfterTransition == false and instance.Travel_TimeStep == 0 and instance.Respawning == false,
        "canonical_slot_readback_failed")
    require_exact(matches_rule_row(save.GameRules, modes[planned.difficulty]) and text(save.Level) == level and text(save.RespawnLevel) == level
        and text(save.Bed) == "" and text(save.Entrance) == "MainMenu" and save.InstanceSeed == planned.seed,
        "world_readback_failed")
    require_exact(save.WeatherOffset == planned.weather and save.CombineStashes == false and save.BarterOnly == false
        and flag(instance, "Static_WorldSeed", planned.world_seed) and flag(instance, "ArtifactSeed", planned.artifact_seed),
        "seed_readback_failed")
    require_exact(flag(instance, "Player_Cash", 23) and flag(instance, "Player_Debt", 3015220) and flag(instance, "Player_Xp", 0)
        and flag(instance, "Class_Wander", 1) and #save.ActiveQuests == 0, "starter_flags_readback_failed")
    local track = save.StormTrack
    require_exact(named(track[storm_class], storm_class_path) and track[storm_seed] == planned.storm_seed
        and track[storm_rate] == planned.rate and authored_curve(track[storm_curve]) and track[storm_active] == false,
        "weather_readback_failed")
    local loadout = preserved_inventory(save)
    if strict then
        local maths = StaticFindObject(maths_path)
        require_exact(valid(maths) and timespan_is(maths, save.GameTime, 1, 6, 45), "game_time_readback_failed")
        require_exact(timespan_is(maths, track[storm_time], 2, 15, 0), "storm_time_readback_failed")
    end
    return loadout
end

local function starting_weather()
    local maths = StaticFindObject(maths_path)
    local storm = asset(storm_path)
    require_exact(valid(maths) and valid(storm) and storm:GetFullName() == "StormData_C " .. storm_path,
        "starting_storm_asset_unqualified")
    require_exact(named(storm.Storm, storm_class_path) and authored_curve(storm.Curve), "starting_storm_defaults_changed")
    require_exact(storm.Min_PlayRate == 0.75 and storm.Max_PlayRate == 1.1, "starting_storm_rate_changed")
    return storm.Storm, storm.Curve
end

local function start_clock(controller, save, planned, authored_storm, authored_storm_curve)
    -- Character_Editor_Widget:Finilize_SaveSettings also initializes campaign time and this storm track.
    save.WeatherOffset = planned.weather
    save.CombineStashes = false
    save.BarterOnly = false
    local track = save.StormTrack
    track[storm_class] = authored_storm
    track[storm_curve] = authored_storm_curve
    track[storm_seed] = planned.storm_seed
    track[storm_rate] = planned.rate
    track[storm_active] = false
    local called, result, before, after, old_storm, next_storm =
        pcall(SilverfishSetNativeFreshClock, controller:GetAddress(), save:GetAddress())
    if not called or type(result) ~= "string" then error("native_fresh_clock_call_failed", 0) end
    if result ~= "applied" and result:match("^[a-z_]+$") then error("native_fresh_clock_" .. result, 0) end
    require_exact(result == "applied" and math.type(before) == "integer" and after == 1107000000000
        and math.type(old_storm) == "integer" and next_storm == 2268000000000, "clock_receipt_unqualified")
end

function fresh.prepare(state, phase, name, slot, seed_text, difficulty)
    difficulty = difficulty or "Mercy"
    local mode = modes[difficulty]
    local seed = type(seed_text) == "string" and #seed_text <= 6 and seed_text:match("^[1-9][0-9]*$") and tonumber(seed_text)
    require_exact(not receipt and name == preset and mode and type(slot) == "string" and #slot == 43
        and slot:match("^SFCoopLive_[0-9a-f]+$") and seed and seed <= 999999, "preset_arguments_invalid")
    local instance = require_exact(qualified_title(state, phase), "title_authority_required")
    local old_save = instance.SaveSlot_Ref
    local old_slot, old_autosave = text(instance.CurrentSaveSlot), instance.SaveAfterTransition
    local old_travel, old_respawning = instance.Travel_TimeStep, instance.Respawning
    require_exact(old_slot == "None" and type(old_autosave) == "boolean"
        and type(old_travel) == "number" and old_respawning == false and instance.BackPackOpen == false
        and array_count(instance.PassiveEffects) == 0, "fresh_title_state_unqualified")

    local character = character_row()
    local rules = data_row("GameMode_Presets_DT", "GameMode_Rules_Struct", difficulty)
    require_exact(matches_rule_row(rules, mode), "difficulty_rules_changed")
    require_exact(matches_map(instance.FactionAdvantage_Defaults, faction_advantage, 5), "faction_defaults_changed")
    local library = asset(character_library_path)
    require_exact(valid(library), "faction_library_unqualified")
    local authored_storm, authored_storm_curve = starting_weather()
    require_exact(type(SilverfishSetNativeFreshClock) == "function", "clock_addon_unavailable")
    local planned = starter(seed, slot)
    planned.difficulty = difficulty
    local ok, prepared = pcall(function()
        -- NewSave allocates in memory and sets CurrentSaveSlot. No save writer or stock travel is invoked here.
        instance:NewSave(slot)
        local save = instance.SaveSlot_Ref
        require_exact(exact(save, save_path) and not same(save, old_save) and text(instance.CurrentSaveSlot) == slot
            and array_count(save.PlayerBackpack) == 0 and #save.ActiveQuests == 0, "fresh_stock_save_unqualified")
        instance.SaveAfterTransition = false
        instance.Travel_TimeStep = 0
        instance.Respawning = false
        save.GameRules = rules
        instance:Apply_CharacterTraits(character)
        require_exact(text(save.Level) == level and text(save.Entrance) == "" and array_count(instance.PassiveEffects) == 1
            and text(instance.PassiveEffects[1]) == "WeirdLuck", "stock_character_application_failed")

        save.Entrance = "MainMenu"
        save.RespawnLevel = level
        save.Bed = ""
        save.InstanceSeed = seed
        instance:Add_PointTo_SaveFlag(FName("Static_WorldSeed"), planned.world_seed, true, true)
        instance:Add_PointTo_SaveFlag(FName("ArtifactSeed"), planned.artifact_seed, true, true)
        start_clock(state.controller, save, planned, authored_storm, authored_storm_curve)
        for faction, points in pairs(faction_advantage) do
            local out = {}
            instance:Add_Faction_Advantage(faction, points)
            library:Compose_FactionAdvantage_FlagName(faction, state.controller, out)
            require_exact(out.Name and flag(instance, out.Name, points), "faction_advantage_readback_failed")
        end
        for faction, points in pairs(wanderer_rep) do
            local out = {}
            library:Compose_FactionRep_FlagName(faction, state.controller, out)
            require_exact(out.Name and text(out.Name):match("^Rep_") and flag(instance, out.Name, points), "reputation_readback_failed")
        end
        planned.loadout = verify(instance, save, planned, true)
        planned.instance = instance:GetAddress()
        planned.save = save:GetAddress()
        return planned
    end)
    if not ok then
        instance.SaveSlot_Ref = old_save
        instance.CurrentSaveSlot = old_slot
        instance.SaveAfterTransition = old_autosave
        instance.Travel_TimeStep = old_travel
        instance.Respawning = old_respawning
        instance.PassiveEffects = {}
        require_exact(same(instance.SaveSlot_Ref, old_save) and text(instance.CurrentSaveSlot) == old_slot
            and instance.SaveAfterTransition == old_autosave and instance.Travel_TimeStep == old_travel
            and instance.Respawning == old_respawning and array_count(instance.PassiveEffects) == 0, "rollback_unverified")
        error(prepared, 0)
    end
    receipt = prepared
    return "stock Wanderer campaign prepared for listen travel"
end

local function outcome(state, phase)
    if phase == "ready" and state.map == menu then return "prepared", "stock_title_save_verified" end
    if phase ~= "hosting" or state.map ~= destination or not state.listening then return end
    if receipt.world and (not valid(state.world) or state.world:GetAddress() ~= receipt.world) then return end
    if receipt.result then return table.unpack(receipt.result) end
    local instance = owner(state)
    local pawn = state.local_pawn and state.local_pawn.pawn
    local save = instance and instance.SaveSlot_Ref
    if not instance or not exact(pawn, pawn_path) or not pawn:HasAuthority() or not same(pawn.Controller, state.controller)
        or not same(state.controller.Pawn, pawn) or not same(pawn.Save, save) then return end
    local verified, readback = pcall(verify, instance, save, receipt, false)
    local matches = verified and readback == receipt.loadout and instance:GetAddress() == receipt.instance
        and save:GetAddress() == receipt.save
    local detail = matches and "authoritative_pawn_adopted_stock_save"
        or verified and "different_save_or_loadout" or tostring(readback):match("native_fresh_[a-z_]+") or "stock_arrival_unqualified"
    receipt.world = state.world:GetAddress()
    receipt.result = { matches and "verified" or "changed", detail }
    return table.unpack(receipt.result)
end

function fresh.rows(state, phase, status_row)
    if not receipt then return {} end
    local result, detail = outcome(state, phase)
    if not result then return {} end
    return {
        status_row("native-fresh-campaign", result, preset, receipt.difficulty, destination, receipt.slot, receipt.seed,
            receipt.world_seed, receipt.artifact_seed, receipt.storm_seed, 23, 3015220, 0, 2, armor_path, level, detail),
    }
end

return fresh
