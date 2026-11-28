#include "world_scene.hpp"

#include "interaction_input.hpp"
#include "local_bridge.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace bridge = silverfish::local_bridge;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto rules_path = STR("/Game/Classes/Libraries/Structs/GameMode_Rules_Struct.GameMode_Rules_Struct");
    constexpr auto storm_path = STR("/Game/Classes/Weather/StormTrack.StormTrack");
    constexpr auto artifacts_path = STR("/Game/Classes/Libraries/Structs/GeneratedArtifacts_Struct.GeneratedArtifacts_Struct");
    constexpr auto timespan_path = STR("/Script/CoreUObject.Timespan");
    constexpr int count_limit = 1'000'000;
    constexpr int flag_limit = 1024;
    constexpr int sparse_flag_limit = 4096;
    constexpr int reputation_limit = 32;

    struct Rule
    {
        std::string_view label;
        const U::TCHAR* name;
        bool integer{};
    };

    constexpr std::array rules{
        Rule{"extraction_mode", STR("ExtractionMode_45_FC0B60E8470417127E7367B262395F41")},
        Rule{"permadeath", STR("PermaDeath_47_9B31E81C4D87339B7E5858915E149876")},
        Rule{"replenish_loot", STR("ReplenishLoot_5_0542042A4D8D98E206019BA9B5A3E23F")},
        Rule{"save_on_level_transition", STR("SaveOn_LevelTransition_7_B709174E4B3AAF09E16C5DA11B61EA65")},
        Rule{"can_save_in_menu", STR("CanSave_InMenu_11_62445BFB482BAF72F8AEECB71E78E918")},
        Rule{"fail_contracts_on_death", STR("FailContracts_OnDeath_9_2B5C7A364BAB9B21A5358E8D6A55369F")},
        Rule{"items_kept_on_death", STR("Items_KeptOnDeath_14_9602AB054254974168B43BB0C2924307"), true},
        Rule{"lose_slot_per_death", STR("LoseSlot_PerDeath_41_60DB8A134132D666D7FD9E9714F51A77")},
        Rule{"randomize_loot", STR("Randomize_Loot_17_A163022E40E9CA62A6DCC1B791990083")},
        Rule{"randomize_npcs", STR("Randomize_NPCs_19_3AF748864F3403D76A5428AA591EFA30")},
        Rule{"drop_bone_pile", STR("Drop_BonePile_43_877E5A2C4F77E5E91CB0ED82EBC3738D")},
    };

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::Cast<T>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
    }

    template <typename T>
    T* field(U::UStruct* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* object_field(U::UObject* object, const U::TCHAR* name)
    {
        return object ? field<T>(object->GetClassPrivate(), name) : nullptr;
    }

    U::UObject* object_value(const U::FObjectProperty* field, const void* container)
    {
        return field ? field->GetObjectPropertyValue(field->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::FStructProperty* struct_slot(U::UStruct* owner, const U::TCHAR* name, U::UScriptStruct* type)
    {
        auto* property = field<U::FStructProperty>(owner, name);
        return property && type && property->GetStruct().Get() == type ? property : nullptr;
    }

    U::FArrayProperty* string_array(U::UClass* owner, const U::TCHAR* name)
    {
        auto* array = field<U::FArrayProperty>(owner, name);
        return array && U::CastField<U::FStrProperty>(array->GetInner()) ? array : nullptr;
    }

    struct SceneSchema
    {
        U::FWeakObjectPtr instance_type, save_type, timespan_type, rules_type, storm_type, artifacts_type;
        U::FObjectProperty* canonical_save{};
        U::FBoolProperty* ongoing_storm{};
        std::array<U::FStrProperty*, 4> locations{};
        U::FIntProperty* seed{};
        U::FDoubleProperty* weather{};
        U::FStructProperty* time{};
        U::FStructProperty* rules_slot{};
        std::array<U::FProperty*, rules.size()> rule_fields{};
        U::FStructProperty* storm_slot{};
        U::FIntProperty* storm_seed{};
        U::FBoolProperty* storm_activated{};
        U::FObjectProperty* storm_class{};
        U::FMapProperty* artifacts{};
        U::FArrayProperty* temporary{};
        U::FArrayProperty* storm_events{};
        U::FMapProperty* flags{};
        U::FNameProperty* flag_key{};
        U::FIntProperty* flag_value{};
        U::FScriptMapLayout flag_layout{};

        bool current() const
        {
            return instance_type.Get() && save_type.Get() && timespan_type.Get() && rules_type.Get() &&
                storm_type.Get() && artifacts_type.Get();
        }

        bool bind()
        {
            auto* instance = find<U::UClass>(instance_path);
            auto* save = find<U::UClass>(save_path);
            auto* timespan = find<U::UScriptStruct>(timespan_path);
            auto* game_rules = find<U::UScriptStruct>(rules_path);
            auto* track = find<U::UScriptStruct>(storm_path);
            auto* artifact_row = find<U::UScriptStruct>(artifacts_path);
            if (!instance || !save || !timespan || !game_rules || !track || !artifact_row) return false;
            canonical_save = field<U::FObjectProperty>(instance, STR("SaveSlot_Ref"));
            ongoing_storm = field<U::FBoolProperty>(instance, STR("OngoingStorm"));
            locations = {field<U::FStrProperty>(save, STR("Level")), field<U::FStrProperty>(save, STR("Entrance")),
                field<U::FStrProperty>(save, STR("RespawnLevel")), field<U::FStrProperty>(save, STR("Bed"))};
            seed = field<U::FIntProperty>(save, STR("InstanceSeed"));
            weather = field<U::FDoubleProperty>(save, STR("WeatherOffset"));
            time = struct_slot(save, STR("GameTime"), timespan);
            rules_slot = struct_slot(save, STR("GameRules"), game_rules);
            storm_slot = struct_slot(save, STR("StormTrack"), track);
            storm_seed = field<U::FIntProperty>(track, STR("Seed_16_224EDCA44017165C45B378BCE4251868"));
            storm_activated = field<U::FBoolProperty>(track, STR("Activated_19_0B381CD94413F01EAE590297A57BD87B"));
            storm_class = field<U::FObjectProperty>(track, STR("Storm_7_1FF2FAC343119542F0A40FAB3A93D49F"));
            temporary = string_array(save, STR("TempActivations"));
            storm_events = string_array(save, STR("StormActivations"));
            artifacts = field<U::FMapProperty>(save, STR("Artifacts"));
            auto* artifact_value = artifacts ? U::CastField<U::FStructProperty>(artifacts->GetValueProp()) : nullptr;
            flags = field<U::FMapProperty>(save, STR("Flags"));
            flag_key = flags ? U::CastField<U::FNameProperty>(flags->GetKeyProp()) : nullptr;
            flag_value = flags ? U::CastField<U::FIntProperty>(flags->GetValueProp()) : nullptr;
            if (!canonical_save || canonical_save->GetPropertyClass().Get() != save || !ongoing_storm ||
                !std::ranges::all_of(locations, [](const auto* property) { return property != nullptr; }) ||
                !seed || !weather || !time || time->GetSize() != sizeof(std::int64_t) ||
                !rules_slot || !storm_slot || !storm_seed || !storm_activated || !storm_class ||
                !temporary || !storm_events || !artifact_value || artifact_value->GetStruct().Get() != artifact_row ||
                !U::CastField<U::FStrProperty>(artifacts->GetKeyProp()) || !flag_key || !flag_value ||
                flag_key->GetArrayDim() != 1 || flag_value->GetArrayDim() != 1 ||
                flag_key->GetSize() != sizeof(U::FName) || flag_key->GetOffset_Internal() != 0 ||
                flag_value->GetSize() != sizeof(std::int32_t) || !flag_key->HasAnyPropertyFlags(U::CPF_HasGetValueTypeHash))
                return false;
            flag_layout = U::FScriptMap::GetScriptLayout(flag_key->GetSize(), flag_key->GetMinAlignment(),
                flag_value->GetSize(), flag_value->GetMinAlignment());
            if (flag_value->GetOffset_Internal() != flag_layout.ValueOffset) return false;

            for (std::size_t index = 0; index < rules.size(); ++index)
            {
                const auto& rule = rules[index];
                rule_fields[index] = rule.integer ? static_cast<U::FProperty*>(field<U::FIntProperty>(game_rules, rule.name))
                    : field<U::FBoolProperty>(game_rules, rule.name);
                if (!rule_fields[index]) return false;
            }
            instance_type = instance;
            save_type = save;
            timespan_type = timespan;
            rules_type = game_rules;
            storm_type = track;
            artifacts_type = artifact_row;
            return true;
        }
    };

    bool ascii_token(char character)
    {
        return (character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') ||
            (character >= '0' && character <= '9') || character == '_' || character == '-';
    }

    std::optional<std::string> storm_class_path(U::UObject* object)
    {
        if (!object) return "-";
        if (!live(object) || !U::Cast<U::UClass>(object)) return {};
        const auto path = RC::to_string(object->GetPathName());
        if (path.size() > 256 || !path.starts_with("/Game/")) return {};
        const auto separator = path.find('.');
        if (separator == path.npos || separator <= 6 || separator + 1 == path.size()) return {};
        const std::string_view view{path};
        if (!std::ranges::all_of(view.substr(6, separator - 6), [](char c) { return ascii_token(c) || c == '/'; }) ||
            !std::ranges::all_of(view.substr(separator + 1), ascii_token)) return {};
        return path;
    }

    void text_field(std::string& rows, std::string_view value)
    {
        rows.push_back('\t');
        bridge::append_status_text(rows, value);
    }

    void integer_field(std::string& rows, int value)
    {
        rows.push_back('\t');
        rows.append(std::to_string(value));
    }

    void float_field(std::string& rows, double value)
    {
        rows.push_back('\t');
        bridge::append_status_number(rows, value);
    }

    bool append_flags(std::string& rows, std::string_view map, const SceneSchema& schema, U::UObject* save)
    {
        auto* flags = schema.flags->ContainerPtrToValuePtr<U::FScriptMap>(save);
        if (flags->Num() < 0 || flags->Num() > flag_limit || flags->GetMaxIndex() < flags->Num() ||
            flags->GetMaxIndex() > sparse_flag_limit) return false;

        std::array<std::pair<std::string, int>, reputation_limit> reputation;
        std::size_t count{};
        // UE4SS TMap:ForEach traverses these sparse indices before Lua sorts the first 32 matches.
        for (int index = 0; index < flags->GetMaxIndex(); ++index)
        {
            if (!flags->IsValidIndex(index)) continue;
            const auto* pair = flags->GetData(index, schema.flag_layout);
            if (!pair) return false;
            auto name = RC::to_string(schema.flag_key->GetPropertyValue(pair).ToString());
            if (name.size() <= 4 || name.size() > 256 || !name.starts_with("Rep_") ||
                !std::ranges::all_of(std::string_view{name}.substr(4), ascii_token)) continue;
            reputation[count++] = {std::move(name), schema.flag_value->GetPropertyValueInContainer(pair)};
            if (count == reputation.size()) break;
        }
        const auto end = reputation.begin() + count;
        std::sort(reputation.begin(), end);
        for (auto item = reputation.begin(); item != end; ++item)
        {
            rows.append("native-reputation");
            text_field(rows, item->first);
            integer_field(rows, item->second);
            rows.push_back('\n');
        }

        rows.append("native-static-world-seed");
        text_field(rows, map);
        const U::FName name{STR("Static_WorldSeed"), U::FNAME_Find};
        const auto* value = name.IsNone() ? nullptr : flags->FindValue(&name, schema.flag_layout,
            [&schema](const void* key) { return schema.flag_key->GetValueTypeHash(key); },
            [&schema](const void* first, const void* second) { return schema.flag_key->Identical(first, second); });
        if (value) integer_field(rows, schema.flag_value->GetPropertyValue(value));
        else text_field(rows, "-");
        rows.push_back('\n');
        return true;
    }

    std::optional<std::string> scene_rows(const SceneSchema& schema, std::string_view map,
        U::UObject* instance, U::UObject* save)
    {
        std::array<std::string, 4> locations;
        for (std::size_t index = 0; index < locations.size(); ++index)
        {
            const auto& value = schema.locations[index]->GetPropertyValueInContainer(save);
            if (value.Len() < 0 || value.Len() > 256) return {};
            locations[index] = RC::to_utf8_string(*value);
            if (locations[index].size() > 256) return {};
        }
        std::int64_t ticks{};
        std::memcpy(&ticks, schema.time->ContainerPtrToValuePtr<void>(save), sizeof(ticks));
        if (ticks < 0) return {};
        const auto milliseconds = ticks / 10'000;
        const auto seconds = static_cast<double>(milliseconds / 1000) + static_cast<double>(milliseconds % 1000) / 1000;
        const auto weather = schema.weather->GetPropertyValueInContainer(save);
        const auto artifacts = schema.artifacts->ContainerPtrToValuePtr<U::FScriptMap>(save)->Num();
        U::FScriptArrayHelper temporary(schema.temporary, schema.temporary->ContainerPtrToValuePtr<void>(save));
        U::FScriptArrayHelper events(schema.storm_events, schema.storm_events->ContainerPtrToValuePtr<void>(save));
        if (!std::isfinite(weather) || std::abs(weather) > 10'000'000 ||
            artifacts < 0 || artifacts > count_limit || temporary.Num() < 0 || temporary.Num() > count_limit ||
            events.Num() < 0 || events.Num() > count_limit) return {};

        const auto* track = schema.storm_slot->ContainerPtrToValuePtr<void>(save);
        const auto storm_class = storm_class_path(object_value(schema.storm_class, track));
        if (!storm_class) return {};
        std::string rows;
        rows.reserve(1024);
        const auto* game_rules = schema.rules_slot->ContainerPtrToValuePtr<void>(save);
        for (std::size_t index = 0; index < rules.size(); ++index)
        {
            const auto& rule = rules[index];
            auto* property = schema.rule_fields[index];
            rows.append("native-rule");
            text_field(rows, rule.label);
            integer_field(rows, rule.integer ? static_cast<U::FIntProperty*>(property)->GetPropertyValueInContainer(game_rules)
                : static_cast<U::FBoolProperty*>(property)->GetPropertyValueInContainer(game_rules));
            rows.push_back('\n');
        }
        if (!append_flags(rows, map, schema, save)) return {};
        rows.append("native-scene");
        text_field(rows, map);
        for (const auto& location : locations) text_field(rows, location);
        integer_field(rows, schema.seed->GetPropertyValueInContainer(save));
        float_field(rows, seconds);
        float_field(rows, weather);
        integer_field(rows, schema.ongoing_storm->GetPropertyValueInContainer(instance));
        integer_field(rows, artifacts);
        integer_field(rows, temporary.Num());
        integer_field(rows, events.Num());
        integer_field(rows, schema.storm_seed->GetPropertyValueInContainer(track));
        integer_field(rows, schema.storm_activated->GetPropertyValueInContainer(track));
        text_field(rows, *storm_class);
        rows.append("\tok\t");
        return rows;
    }

    int read_scene(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables() ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2)) return 0;
        const auto requested = lua.get_integer();
        const auto map = lua.get_string();
        if (requested <= 0 || !silverfish::world_path::package(map)) return 0;
        auto* pawn = silverfish::native_interaction_input::current_local_pawn(static_cast<std::uintptr_t>(requested));
        auto* role = object_field<U::FByteProperty>(pawn, STR("Role"));
        if (!role || role->GetPropertyValueInContainer(pawn) != 3) return 0;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        if (!live(world)) return 0;
        const auto path = world->GetPathName();
        if (!silverfish::world_path::object(std::wstring_view{path}) ||
            RC::to_string(path.substr(0, path.find('.'))) != map) return 0;
        auto* driver = object_value(object_field<U::FObjectProperty>(world, STR("NetDriver")), world);
        auto* server_slot = live(driver) ? object_field<U::FObjectProperty>(driver, STR("ServerConnection")) : nullptr;
        if (!server_slot || object_value(server_slot, driver)) return 0;
        auto* instance = object_value(object_field<U::FObjectProperty>(world, STR("OwningGameInstance")), world);
        if (!live(instance)) return 0;

        static SceneSchema schema;
        if (!schema.current())
        {
            SceneSchema next;
            if (!next.bind()) return 0;
            schema = next;
        }
        if (instance->GetClassPrivate() != schema.instance_type.Get()) return 0;
        auto* save = object_value(schema.canonical_save, instance);
        if (!live(save) || save->GetClassPrivate() != schema.save_type.Get() ||
            object_value(object_field<U::FObjectProperty>(pawn, STR("Save")), pawn) != save) return 0;
        const auto frame = scene_rows(schema, map, instance, save);
        if (!frame) return 0;
        lua.set_string(*frame);
        return 1;
    }
}

void silverfish::native_world_scene::register_lua(Lua& lua)
{
    lua.register_function("SilverfishReadNativeWorldSceneRows", read_scene);
}
