#include "shared_loot.hpp"
#include "authored_loot_registry.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto crate_class = STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C");
    constexpr auto parent_class =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C");
    constexpr auto child_class = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto replenish_enum = STR("/Game/Classes/Libraries/Enums_Lib/RespawnSetting_Enum.RespawnSetting_Enum");
    constexpr auto open_path =
        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:SendInteractionToActor");
    constexpr auto crate_script_path =
        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:ExecuteUbergraph_LootCrate_Small_BP");
    constexpr auto karma_path =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C:AffectKarma");
    constexpr auto spawn_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:SpawnLoot");
    constexpr auto roll_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:RollKarma");
    constexpr auto decide_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:DecideObject");
    constexpr auto table_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:RollTable");
    constexpr auto passive_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:FindPassiveEffect");
    constexpr auto box_location_path = STR("/Script/Engine.SceneComponent:K2_GetComponentLocation");
    constexpr auto box_collision_path = STR("/Script/Engine.PrimitiveComponent:GetCollisionEnabled");
    constexpr auto character_type_path =
        STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct");
    constexpr auto permanent_effects_name = STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00");
    constexpr int max_clients = 4;
    constexpr int max_effects = 256;

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* prop = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <typename T>
    T* argument(Unreal::UFunction* fn, const Unreal::TCHAR* name, bool output, std::size_t offset)
    {
        auto* prop = field<T>(fn, name);
        return prop && prop->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
               prop->HasAnyPropertyFlags(Unreal::CPF_OutParm) == output &&
               !prop->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) &&
               prop->GetOffset_Internal() == static_cast<int>(offset) ? prop : nullptr;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* prop = field<Unreal::FObjectProperty>(object, name);
        return prop ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool read_bool(Unreal::UObject* object, const Unreal::TCHAR* name, bool expected)
    {
        auto* prop = field<Unreal::FBoolProperty>(object, name);
        return prop && prop->GetPropertyValueInContainer(object) == expected;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool authority(Unreal::UObject* object, Unreal::UWorld* world)
    {
        auto* role = field<Unreal::FByteProperty>(object, STR("Role"));
        return live(object) && role && role->GetPropertyValueInContainer(object) == 3 &&
               read_bool(object, STR("bActorIsBeingDestroyed"), false) &&
               static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    Address address(Unreal::UObject* object)
    {
        return reinterpret_cast<Address>(object);
    }

    bool env_flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
               size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env_flag("SILVERFISH_COOP_NATIVE_LOOT");
    }

    bool seeded_fixture_enabled()
    {
        return env_flag("SILVERFISH_COOP_NATIVE_FIXTURES") && env_flag("SILVERFISH_COOP_HEADLESS");
    }

    struct Context
    {
        Unreal::UWorld* world{};
        Unreal::UObject* crate{};
        Unreal::UObject* pawn{};
        Unreal::UObject* box{};
        Unreal::UObject* controller{};
        Unreal::UObject* player{};
        Unreal::UObject* save{};
        Unreal::UObject* stock{};
        Unreal::UObject* instance{};
        std::array<Unreal::UObject*, 2> spawners{};
        bool lucky{};
        bool guarantee_spawn{};
    };

    bool qualified_connection(const Context& context, Unreal::UObject* driver, Unreal::UClass* controller_type)
    {
        auto* players = field<Unreal::FArrayProperty>(context.instance, STR("LocalPlayers"));
        auto* local_entry = players ? Unreal::CastField<Unreal::FObjectProperty>(players->GetInner()) : nullptr;
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* net_type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        if (!local_entry || !local_type || !net_type) return false;
        Unreal::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(context.instance));
        if (locals.Num() != 1) return false;
        auto* local = local_entry->GetObjectPropertyValue(locals.GetRawPtr(0));
        auto* local_controller = object_field(local, STR("PlayerController"));
        auto* host = object_field(local_controller, STR("Pawn"));
        if (!live(local) || !local->IsA(local_type) || !authority(local_controller, context.world) ||
            local_controller->GetClassPrivate() != controller_type ||
            object_field(local_controller, STR("Player")) != local || !authority(host, context.world) ||
            host->GetClassPrivate() != context.pawn->GetClassPrivate() ||
            object_field(host, STR("Controller")) != local_controller ||
            object_field(host, STR("Save")) != context.stock) return false;
        if (context.player == local)
        {
            return context.pawn == host && context.save == context.stock;
        }
        if (context.save == context.stock || !context.player->IsA(net_type) ||
            object_field(context.player, STR("Driver")) != driver ||
            object_field(context.player, STR("PlayerController")) != context.controller ||
            object_field(context.player, STR("OwningActor")) != context.controller) return false;

        auto* clients = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* remote_entry = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
        if (!remote_entry) return false;
        Unreal::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(driver));
        if (remotes.Num() < 1 || remotes.Num() > max_clients) return false;
        std::array<Unreal::UObject*, max_clients> seen{};
        bool matched = false;
        for (int index = 0; index < remotes.Num(); ++index)
        {
            auto* connection = remote_entry->GetObjectPropertyValue(remotes.GetRawPtr(index));
            if (!live(connection) || !connection->IsA(net_type) ||
                std::ranges::find(seen, connection) != seen.end()) return false;
            seen[static_cast<std::size_t>(index)] = connection;
            if (connection == context.player)
            {
                matched = true;
                continue;
            }
            auto* controller = object_field(connection, STR("PlayerController"));
            auto* pawn = object_field(controller, STR("Pawn"));
            if (pawn && object_field(pawn, STR("Save")) == context.save) return false;
        }
        return matched;
    }

    bool qualified_reach(const Context& context)
    {
        auto* range_prop = field<Unreal::FDoubleProperty>(context.pawn, STR("InteractionRange"));
        auto* location = find<Unreal::UFunction>(box_location_path);
        auto* vector = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        auto* output = field<Unreal::FStructProperty>(location, STR("ReturnValue"));
        struct BoxLocation
        {
            double x{};
            double y{};
            double z{};
        } result;
        if (!range_prop || !location || !vector || !output || output->GetStruct().Get() != vector ||
            output != location->GetReturnProperty() || location->GetNumParms() != 1 ||
            location->GetParmsSize() != sizeof(result) || output->GetOffset_Internal() != 0 ||
            !location->HasAnyFunctionFlags(Unreal::FUNC_Native)) return false;

        context.box->ProcessEvent(location, &result);
        auto source = static_cast<Unreal::AActor*>(context.pawn)->K2_GetActorLocation();
        const auto dx = result.x - source.GetX();
        const auto dy = result.y - source.GetY();
        const auto dz = result.z - source.GetZ();
        const auto range = range_prop->GetPropertyValueInContainer(context.pawn);
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(range) && range > 0.0 && range <= 1000.0 &&
               std::isfinite(squared) && squared <= range * range;
    }

    bool query_collision_enabled(Unreal::UObject* box)
    {
        auto* function = find<Unreal::UFunction>(box_collision_path);
        auto* output = field<Unreal::FByteProperty>(function, STR("ReturnValue"));
        std::uint8_t mode{};
        if (!function || !output || output != function->GetReturnProperty() ||
            output->GetOffset_Internal() != 0 || function->GetNumParms() != 1 ||
            function->GetParmsSize() != sizeof(mode) || !function->HasAnyFunctionFlags(Unreal::FUNC_Native))
            return false;
        box->ProcessEvent(function, &mode);
        return mode == 1 || mode == 3;
    }

    std::string_view qualify_opener(Address pawn, Address save, Address stock, Context& context)
    {
        auto* player_type = find<Unreal::UClass>(pawn_class);
        auto* save_type = find<Unreal::UClass>(save_class);
        if (!live(context.world) || !player_type || !save_type) return "stock_opener_world_or_class_unavailable";
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != pawn) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == player_type) context.pawn = candidate;
            return RC::LoopAction::Break;
        });
        if (!authority(context.pawn, context.world) || !read_bool(context.pawn, STR("Dead"), false))
            return "standing_stock_opener_unavailable";
        context.save = object_field(context.pawn, STR("Save"));
        context.instance = object_field(context.world, STR("OwningGameInstance"));
        context.stock = object_field(context.instance, STR("SaveSlot_Ref"));
        context.controller = object_field(context.pawn, STR("Controller"));
        context.player = object_field(context.controller, STR("Player"));
        auto* driver = object_field(context.world, STR("NetDriver"));
        auto* instance_type = find<Unreal::UClass>(instance_class);
        auto* controller_type = find<Unreal::UClass>(controller_class);
        auto* health = field<Unreal::FDoubleProperty>(context.save, STR("PlayerHealth"));
        const auto hp = health ? health->GetPropertyValueInContainer(context.save) : 0.0;
        if (!instance_type || !controller_type || !live(context.instance) ||
            context.instance->GetClassPrivate() != instance_type || !live(context.save) || !live(context.stock) ||
            context.save->GetClassPrivate() != save_type || context.stock->GetClassPrivate() != save_type ||
            address(context.save) != save || address(context.stock) != stock || !std::isfinite(hp) || hp <= 0.0 ||
            !authority(context.controller, context.world) || context.controller->GetClassPrivate() != controller_type ||
            object_field(context.controller, STR("Pawn")) != context.pawn || !live(context.player) ||
            !live(driver) || object_field(driver, STR("World")) != context.world ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) ||
            object_field(driver, STR("ServerConnection")) || !qualified_connection(context, driver, controller_type))
            return "stock_opener_ownership_unavailable";
        return {};
    }

    bool qualify_authored_opener(Address pawn, Address save, Address stock, Context& context)
    {
        for (const auto* path : silverfish::authored_loot::worlds)
        {
            auto* world = find<Unreal::UWorld>(path);
            if (!world) continue;
            Context next;
            next.world = world;
            if (qualify_opener(pawn, save, stock, next).empty())
            {
                context = next;
                return true;
            }
        }
        return false;
    }

    std::string_view qualify(Address crate, Address pawn, Address box, Address save, Address stock, Context& context)
    {
        for (const auto& profile : silverfish::authored_loot::profiles)
        {
            auto* candidate = find<Unreal::UObject>(profile.actor);
            if (candidate && address(candidate) == crate)
            {
                context.crate = candidate;
                break;
            }
        }
        context.world = silverfish::shared_loot::authored_crate_world(context.crate);
        if (!context.world ||
            !authority(context.crate, context.world)) return "exact_stock_crate_unavailable";
        auto* value = field<Unreal::FIntProperty>(context.crate, STR("SpawnedValue"));
        if (!value || value->GetPropertyValueInContainer(context.crate) != 0 ||
            !read_bool(context.crate, STR("Looted"), false) ||
            !silverfish::shared_loot::qualified_crate_settings(context.crate)) return "stock_crate_not_ready";
        context.guarantee_spawn = read_bool(context.crate, STR("GuaranteeSpawn"), true);

        if (const auto error = qualify_opener(pawn, save, stock, context); !error.empty()) return error;
        context.box = object_field(context.crate, STR("Box"));
        context.spawners = {object_field(context.crate, STR("Loot_Spawner")),
                            object_field(context.crate, STR("Loot_Spawner1"))};
        auto* component_type = find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"));
        auto* spawner_type = find<Unreal::UClass>(child_class);
        if (!component_type || !spawner_type || !live(context.box) || address(context.box) != box ||
            context.box->GetClassPrivate() != component_type || context.box->GetOuterPrivate() != context.crate ||
            object_field(context.crate, STR("As Game Instance FP")) != context.instance ||
            context.spawners[0] == context.spawners[1]) return "stock_crate_components_unavailable";
        for (auto* child : context.spawners)
        {
            if (!live(child) || child->GetClassPrivate() != spawner_type || child->GetOuterPrivate() != context.crate ||
                object_field(child, STR("Container")) != context.crate ||
                object_field(child, STR("As Game Instance FP")) != context.instance)
                return "stock_crate_child_spawner_unavailable";
        }
        if (!query_collision_enabled(context.box)) return "stock_crate_box_not_interactable";
        return qualified_reach(context) ? std::string_view{} : "stock_crate_out_of_reach";
    }

    struct OpenParams
    {
        Unreal::UObject* player{};
        Unreal::UObject* hit{};
    };

    struct Schema
    {
        Unreal::UFunction* open{};
        Unreal::UFunction* script{};
        Unreal::UFunction* roll{};
        Unreal::UFunction* karma{};
        Unreal::UFunction* spawn{};
        Unreal::UFunction* passive{};
        Unreal::FObjectProperty* hit_component{};
        Unreal::FObjectProperty* opener{};
        Unreal::FObjectProperty* item{};
        Unreal::FBoolProperty* guaranteed{};
        Unreal::FNameProperty* effect{};
        Unreal::FBoolProperty* count{};
        Unreal::FBoolProperty* active{};
        Unreal::FIntProperty* instances{};
        Unreal::UClass* item_type{};
        Unreal::UClass* actor_type{};
    };

    bool blueprint(Unreal::UFunction* function, Unreal::UClass* owner_class, int parameters)
    {
        return function && owner_class && function->GetOuterPrivate() == owner_class &&
               function->GetNumParms() == parameters && !function->GetReturnProperty() &&
               !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net);
    }

    bool bind_schema(const Context& context, Schema& schema)
    {
        schema.open = find<Unreal::UFunction>(open_path);
        schema.script = find<Unreal::UFunction>(crate_script_path);
        schema.karma = find<Unreal::UFunction>(karma_path);
        schema.spawn = find<Unreal::UFunction>(spawn_path);
        schema.roll = find<Unreal::UFunction>(roll_path);
        schema.passive = find<Unreal::UFunction>(passive_path);
        auto* crate_type = context.crate->GetClassPrivate();
        auto* spawner_type = context.spawners.front()->GetClassPrivate();
        auto* container_type = find<Unreal::UClass>(parent_class);
        auto* scene_type = find<Unreal::UClass>(STR("/Script/Engine.SceneComponent"));
        schema.item_type = find<Unreal::UClass>(item_class);
        schema.actor_type = find<Unreal::UClass>(STR("/Script/Engine.Actor"));
        if (!blueprint(schema.open, crate_type, 2) || !blueprint(schema.script, crate_type, 1) ||
            !blueprint(schema.karma, container_type, 0) || !blueprint(schema.roll, spawner_type, 2) ||
            !blueprint(schema.spawn, spawner_type, 10) ||
            !blueprint(schema.passive, context.instance->GetClassPrivate(), 4) ||
            !scene_type || !schema.item_type || !schema.actor_type ||
            schema.open->GetParmsSize() != sizeof(OpenParams) ||
            schema.passive->GetParmsSize() != 16 || schema.spawn->GetParmsSize() != 0x70)
            return false;

        schema.opener = argument<Unreal::FObjectProperty>(schema.open, STR("Player"), false, offsetof(OpenParams, player));
        schema.hit_component =
            argument<Unreal::FObjectProperty>(schema.open, STR("HitComponent"), false, offsetof(OpenParams, hit));
        schema.item = argument<Unreal::FObjectProperty>(schema.spawn, STR("Object"), true, 0x68);
        schema.guaranteed = argument<Unreal::FBoolProperty>(schema.spawn, STR("GuaranteedLoot"), false, 0x60);
        schema.effect = argument<Unreal::FNameProperty>(schema.passive, STR("EffectName"), false, 0);
        schema.count = argument<Unreal::FBoolProperty>(schema.passive, STR("CountInstances"), false, 8);
        schema.active = argument<Unreal::FBoolProperty>(schema.passive, STR("EffectActive"), true, 9);
        schema.instances = argument<Unreal::FIntProperty>(schema.passive, STR("Instances"), true, 0xc);
        return schema.opener && schema.opener->GetPropertyClass().Get() == context.pawn->GetClassPrivate() &&
               schema.hit_component && schema.hit_component->GetPropertyClass().Get() == scene_type &&
               schema.item && schema.item->GetPropertyClass().Get() == schema.actor_type &&
               (!context.guarantee_spawn || schema.guaranteed) && schema.effect && schema.count && schema.active && schema.instances;
    }

    // Its first UE FRandomStream fraction is exactly zero. Check this on a
    // scratch copy with the actual engine before changing a fixture's stream.
    constexpr std::int32_t first_fraction_zero = -1768226079;

    struct SeedFixtureProfile
    {
        const Unreal::TCHAR* crate;
        std::array<std::int32_t, 2> child_salts;
    };
    constexpr std::array seed_fixture_profiles{
        SeedFixtureProfile{silverfish::authored_loot::profiles.front().actor, {-73835290, -28604056}},
        SeedFixtureProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.LootCrate_Small_BP_C_3"),
            {-36556924, -94896472}},
    };

    struct SeedFixture
    {
        Unreal::UFunction* table{};
        Unreal::UFunction* decide{};
        Unreal::FStructProperty* stream{};
        Unreal::FIntProperty* initial{};
        Unreal::FIntProperty* current{};
        Unreal::FBoolProperty* seeded{};
        Unreal::FMapProperty* table_items{};
        Unreal::FClassProperty* roll_item{};
        std::array<Unreal::UClass*, 2> selected{};
        std::array<Unreal::FWeakObjectPtr, 2> children{};
        std::array<std::array<std::int32_t, 2>, 2> previous{};
        std::array<bool, 2> changed{};

        std::string_view first_weighted_item(Unreal::FMapProperty* property, const void* container,
                                             Unreal::UClass* item_base, Unreal::UClass*& selected_item) const
        {
            if (!property) return "stock_loot_fixture_map_property_unavailable";
            if (!container) return "stock_loot_fixture_map_storage_unavailable";
            auto* key = Unreal::CastField<Unreal::FClassProperty>(property->GetKeyProp());
            auto* numeric = property->GetValueProp();
            auto* double_weight = Unreal::CastField<Unreal::FDoubleProperty>(numeric);
            auto* float_weight = Unreal::CastField<Unreal::FFloatProperty>(numeric);
            if (!key || key->GetMetaClass().Get() != item_base || key->GetOffset_Internal() != 0 ||
                key->GetSize() != sizeof(void*)) return "stock_loot_fixture_map_class_key_unverified";
            if ((!double_weight && !float_weight) ||
                numeric->GetSize() != (double_weight ? sizeof(double) : sizeof(float)))
                return "stock_loot_fixture_map_weight_schema_unverified";
            const auto layout = Unreal::FScriptMap::GetScriptLayout(
                key->GetSize(), key->GetMinAlignment(), numeric->GetSize(), numeric->GetMinAlignment());
            const auto declared_offset = numeric->GetOffset_Internal();
            if (declared_offset != 0 && declared_offset != layout.ValueOffset)
                return "stock_loot_fixture_map_pair_layout_unverified";
            const auto* map = property->ContainerPtrToValuePtr<Unreal::FScriptMap>(container);
            const auto count = map->Num();
            const auto maximum = map->GetMaxIndex();
            if (count == 0 && maximum == 0) return "stock_loot_fixture_map_empty";
            if (count < 1 || maximum > 128 || count > maximum) return "stock_loot_fixture_map_extent_unverified";
            for (int index = 0; index < maximum; ++index)
            {
                if (!map->IsValidIndex(index)) continue;
                const auto* entry = static_cast<const std::byte*>(map->GetData(index, layout));
                if (!entry) return "stock_loot_fixture_map_entry_unavailable";
                const auto* value = entry + layout.ValueOffset;
                const double chance = double_weight ? double_weight->GetPropertyValue(value)
                                                    : float_weight->GetPropertyValue(value);
                // A zero roll uses <= in the stock selector, so preceding zero-weight
                // entries cannot be skipped. The authored fixture has positive weights.
                if (!std::isfinite(chance) || chance <= 0.0)
                    return "stock_loot_fixture_map_first_weight_unverified";
                auto* object = key->GetObjectPropertyValue(entry);
                if (!object) return "stock_loot_fixture_map_first_class_missing";
                auto* type = Unreal::Cast<Unreal::UClass>(object);
                if (!live(type) || !type->IsChildOf(item_base))
                    return "stock_loot_fixture_map_first_class_unverified";
                selected_item = type;
                return {};
            }
            return "stock_loot_fixture_map_sparse_entry_missing";
        }

        std::string_view bind(const Context& context, const Schema& schema)
        {
            const auto crate_path = context.crate->GetPathName();
            const auto profile = std::ranges::find_if(seed_fixture_profiles,
                [&](const SeedFixtureProfile& entry) { return crate_path == entry.crate; });
            if (profile == seed_fixture_profiles.end()) return "stock_loot_fixture_crate_unqualified";
            auto* type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.RandomStream"));
            auto* child_type = context.spawners.front()->GetClassPrivate();
            table = find<Unreal::UFunction>(table_path);
            decide = find<Unreal::UFunction>(decide_path);
            stream = field<Unreal::FStructProperty>(context.spawners.front(), STR("Stream"));
            initial = field<Unreal::FIntProperty>(type, STR("InitialSeed"));
            current = field<Unreal::FIntProperty>(type, STR("Seed"));
            seeded = argument<Unreal::FBoolProperty>(table, STR("Seeded"), false, 0x50);
            table_items = field<Unreal::FMapProperty>(table, STR("Table"));
            roll_item = argument<Unreal::FClassProperty>(schema.roll, STR("Item"), false, 0);
            if (!type || !blueprint(table, child_type, 3) || !blueprint(decide, child_type, 5) ||
                !stream || stream->GetStruct().Get() != type || stream->GetSize() != 8 ||
                !initial || initial->GetOffset_Internal() != 0 || initial->GetSize() != 4 ||
                !current || current->GetOffset_Internal() != 4 || current->GetSize() != 4 ||
                !seeded || !table_items || !table_items->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
                table_items->GetOffset_Internal() != 0 || !roll_item)
                return "stock_loot_fixture_schema_unavailable";

            Unreal::UClass* authored_first{};
            if (const auto failure = first_weighted_item(
                field<Unreal::FMapProperty>(context.crate, STR("Loot Table_Items")), context.crate,
                schema.item_type, authored_first); !failure.empty()) return failure;
            auto* slot_value = field<Unreal::FDoubleProperty>(context.crate, STR("SpawnerExpectedValue"));
            const auto expected_value = slot_value ? slot_value->GetPropertyValueInContainer(context.crate) : 0.0;
            if (!std::isfinite(expected_value) || expected_value <= 0.0)
                return "stock_loot_fixture_slot_value_unverified";

            for (std::size_t index = 0; index < children.size(); ++index)
            {
                auto* child = context.spawners[index];
                auto* salt = field<Unreal::FIntProperty>(child, STR("SpawnerSalt"));
                if (!salt || salt->GetPropertyValueInContainer(child) != profile->child_salts[index])
                    return "stock_loot_fixture_authored_spawner_changed";
                children[index] = child;
            }

            auto* math = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetMathLibrary"));
            auto* draw = find<Unreal::UFunction>(STR("/Script/Engine.KismetMathLibrary:RandomFloatFromStream"));
            auto* input = field<Unreal::FStructProperty>(draw, STR("Stream"));
            auto* output = field<Unreal::FFloatProperty>(draw, STR("ReturnValue"));
            struct Draw
            {
                std::int32_t initial_seed{first_fraction_zero};
                std::int32_t seed{first_fraction_zero};
                float fraction{-1.0f};
            } test;
            if (!math || !draw || draw->GetOuterPrivate() != math->GetClassPrivate() ||
                !draw->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
                draw->GetNumParms() != 2 || draw->GetParmsSize() != sizeof(test) ||
                !input || !input->HasAnyPropertyFlags(Unreal::CPF_Parm) || input->GetStruct().Get() != type ||
                input->GetOffset_Internal() != 0 || input->GetSize() != 8 ||
                !output || output != draw->GetReturnProperty() || output->GetOffset_Internal() != offsetof(Draw, fraction))
                return "stock_loot_fixture_random_schema_unavailable";
            math->ProcessEvent(draw, &test);
            return test.fraction == 0.0f ? std::string_view{} : "stock_loot_fixture_seed_unverified";
        }

        bool reset_draw(std::size_t index, Unreal::UObject* child)
        {
            if (!live(child) || children[index].Get() != child) return false;
            auto* value = stream->ContainerPtrToValuePtr<void>(child);
            if (!changed[index])
            {
                previous[index] = {initial->GetPropertyValueInContainer(value),
                                   current->GetPropertyValueInContainer(value)};
                changed[index] = true;
            }
            initial->SetPropertyValueInContainer(value, first_fraction_zero);
            current->SetPropertyValueInContainer(value, first_fraction_zero);
            return true;
        }

        bool restore()
        {
            bool complete = true;
            for (std::size_t index = 0; index < children.size(); ++index)
            {
                if (!changed[index]) continue;
                auto* child = children[index].Get();
                if (!live(child))
                {
                    complete = false;
                    continue;
                }
                auto* value = stream->ContainerPtrToValuePtr<void>(child);
                initial->SetPropertyValueInContainer(value, previous[index][0]);
                current->SetPropertyValueInContainer(value, previous[index][1]);
                if (initial->GetPropertyValueInContainer(value) != previous[index][0] ||
                    current->GetPropertyValueInContainer(value) != previous[index][1])
                    complete = false;
            }
            return complete;
        }
    };

    struct OpenScope
    {
        const Context& context;
        const Schema& schema;
        std::array<Unreal::FWeakObjectPtr, 2> items{};
        std::array<bool, 2> produced{};
        std::array<std::string, 2> item_classes{};
        std::array<int, 2> spawns{};
        std::array<int, 2> rolls{};
        int accounts{};
        std::string_view problem{};
        SeedFixture* fixture{};
        std::array<int, 2> table_draws{};
        std::array<int, 2> karma_draws{};
    };

    using Clock = std::chrono::steady_clock;

    struct VerifiedRoll
    {
        Unreal::FWeakObjectPtr crate{};
        Unreal::FWeakObjectPtr pawn{};
        Unreal::FWeakObjectPtr save{};
        Unreal::FWeakObjectPtr stock{};
        std::array<Unreal::FWeakObjectPtr, 2> items{};
        std::array<bool, 2> produced{};
        Clock::time_point expires{};
    };

    thread_local OpenScope* opening = nullptr;
    thread_local VerifiedRoll latest_roll;
    std::atomic<bool> installed{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;
    Hook::GlobalCallbackId fixture_callback = Hook::ERROR_ID;

    struct ActiveOpen
    {
        explicit ActiveOpen(OpenScope& scope) { opening = &scope; }
        ~ActiveOpen() { opening = nullptr; }
    };

    void* find_output(Unreal::FFrame& frame, Unreal::FProperty* property)
    {
        auto* out = frame.OutParms();
        void* address = nullptr;
        for (int remaining = frame.Node()->GetNumParms(); out && remaining > 0; --remaining, out = out->NextOutParm)
        {
            if (out->Property != property) continue;
            if (address) return nullptr;
            address = out->PropAddr;
        }
        return out ? nullptr : address;
    }

    int spawner_index(const Context& context, Unreal::UObject* component)
    {
        if (component == context.spawners[0]) return 0;
        if (component == context.spawners[1]) return 1;
        return -1;
    }

    void seed_fixture_draw(Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || !opening || !opening->fixture || !opening->problem.empty()) return;
        auto& scope = *opening;
        auto& fixture = *scope.fixture;
        const bool table = frame.Node() == fixture.table;
        if (!table && frame.Node() != scope.schema.roll) return;
        const auto index = spawner_index(scope.context, object);
        if (index < 0) return;
        const auto slot = static_cast<std::size_t>(index);
        auto* decide = frame.PreviousFrame();
        auto* spawn = decide ? decide->PreviousFrame() : nullptr;
        auto* script = spawn ? spawn->PreviousFrame() : nullptr;
        auto* locals = frame.Locals();
        if (frame.Object() != object || !locals || !decide || decide->Node() != fixture.decide ||
            decide->Object() != object || !spawn || spawn->Node() != scope.schema.spawn ||
            spawn->Object() != object || !script || script->Node() != scope.schema.script ||
            script->Object() != scope.context.crate || iteration.OriginalFunctionCallPrevented())
        {
            scope.problem = "stock_loot_fixture_draw_frame_unverified";
            return;
        }
        if (table)
        {
            auto* items = fixture.table_items->HasAnyPropertyFlags(Unreal::CPF_OutParm)
                ? find_output(frame, fixture.table_items) : locals;
            if (++scope.table_draws[slot] != 1 || fixture.seeded->GetPropertyValueInContainer(locals))
            {
                scope.problem = "stock_loot_fixture_stock_seed_branch_changed";
                return;
            }
            scope.problem = fixture.first_weighted_item(
                fixture.table_items, items, scope.schema.item_type, fixture.selected[slot]);
            if (!scope.problem.empty()) return;
            if (!fixture.reset_draw(slot, object))
            {
                scope.problem = "stock_loot_fixture_child_stream_unverified";
                return;
            }
            fixture.seeded->SetPropertyValueInContainer(locals, true);
        }
        else if (++scope.karma_draws[slot] != 1 || scope.table_draws[slot] != 1 ||
                 fixture.roll_item->GetObjectPropertyValue(fixture.roll_item->ContainerPtrToValuePtr<void>(locals)) !=
                     fixture.selected[slot] || !fixture.reset_draw(slot, object))
        {
            scope.problem = "stock_loot_fixture_karma_draw_unverified";
        }
    }

    void override_luck(OpenScope& scope, Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        if (object != scope.context.instance || !caller) return;
        const auto index = spawner_index(scope.context, caller->Object());
        const bool account = caller->Object() == scope.context.crate && caller->Node() == scope.schema.karma;
        const bool roll = index >= 0 && caller->Node() == scope.schema.roll;
        if (!account && !roll) return;
        // GuaranteeSpawn bypasses both AffectKarma's accounting branch and
        // each child's RollKarma. The stock weighted table still runs.
        if (scope.context.guarantee_spawn)
        {
            scope.problem = "stock_loot_guaranteed_karma_branch_changed";
            return;
        }

        const auto& schema = scope.schema;
        const auto* locals = frame.Object() == object ? frame.Locals() : nullptr;
        auto* active = find_output(frame, schema.active);
        auto* instances = find_output(frame, schema.instances);
        if (!locals || schema.effect->GetPropertyValueInContainer(locals).ToString() != STR("LuckyLooter") ||
            schema.count->GetPropertyValueInContainer(locals) || !active || !instances || active == instances ||
            reinterpret_cast<Address>(instances) % alignof(std::int32_t) != 0 || iteration.OriginalFunctionCallPrevented())
        {
            scope.problem = "stock_loot_trait_frame_unverified";
            return;
        }
        int& hits = account ? scope.accounts : scope.rolls[static_cast<std::size_t>(index)];
        if (++hits != 1)
        {
            scope.problem = "stock_loot_trait_call_repeated";
            return;
        }

        // The exact output properties refer to the already-prepared caller
        // storage. Leave the canonical save and global passive cache intact.
        schema.active->SetPropertyValue(active, scope.context.lucky);
        schema.instances->SetPropertyValue(instances, scope.context.lucky ? 1 : 0);
    }

    void capture_item(OpenScope& scope, Iteration& iteration, Unreal::UObject* component, Unreal::FFrame& frame)
    {
        const auto index = spawner_index(scope.context, component);
        if (index < 0) return;
        auto* caller = frame.PreviousFrame();
        auto* out = find_output(frame, scope.schema.item);
        const auto* locals = scope.context.guarantee_spawn ? frame.Locals() : nullptr;
        const bool guaranteed_branch = !scope.context.guarantee_spawn ||
            (locals && scope.schema.guaranteed->GetPropertyValueInContainer(locals));
        const auto slot = static_cast<std::size_t>(index);
        if (!caller || caller->Object() != scope.context.crate || caller->Node() != scope.schema.script ||
            frame.Object() != component || !out || reinterpret_cast<Address>(out) % alignof(void*) != 0 || !guaranteed_branch ||
            iteration.OriginalFunctionCallPrevented() || ++scope.spawns[slot] != 1)
        {
            scope.problem = "stock_loot_spawner_frame_unverified";
            return;
        }
        auto* actor = scope.schema.item->GetObjectPropertyValue(out);
        if (!actor) return;
        if (!live(actor) || !actor->IsA(scope.schema.actor_type) || !authority(actor, scope.context.world))
        {
            scope.problem = "stock_loot_spawner_result_unverified";
            return;
        }
        if (actor->IsA(scope.schema.item_type))
        {
            scope.produced[slot] = true;
            scope.items[slot] = actor;
            scope.item_classes[slot] = RC::to_utf8_string(actor->GetClassPrivate()->GetPathName());
        }
    }

    void capture(Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void*)
    {
        if (!opening || !object || !installed.load(std::memory_order_acquire)) return;
        if (frame.Node() == opening->schema.passive)
        {
            override_luck(*opening, iteration, object, frame);
        }
        else if (frame.Node() == opening->schema.spawn)
        {
            capture_item(*opening, iteration, object, frame);
        }
    }

    int result(const Lua& lua, bool ok, std::string_view detail, bool looted = false,
               Address item1 = 0, Address item2 = 0, int trait_calls = 0, bool invoked = false)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        lua.set_bool(looted);
        lua.set_integer(static_cast<std::int64_t>(item1));
        lua.set_integer(static_cast<std::int64_t>(item2));
        lua.set_integer(trait_calls);
        lua.set_bool(invoked);
        return 7;
    }

    std::string materialization_failure(const OpenScope& scope, const std::array<Unreal::UObject*, 2>& actors)
    {
        constexpr auto prefix = "stock_loot_open_spawned_item_unverified:";
        for (std::size_t i = 0; i < actors.size(); ++i)
        {
            auto* actor = actors[i];
            if (!scope.produced[i] || authority(actor, scope.context.world)) continue;
            auto* known = actor ? actor : scope.items[i].Get(true);
            const char* cause = "authority_changed";
            if (!known) cause = "stale_weak_object";
            else if (!actor) cause = "pending_kill";
            else if (!live(actor)) cause = "destroyed_or_unreachable";
            else if (!read_bool(actor, STR("bActorIsBeingDestroyed"), false)) cause = "actor_being_destroyed";
            else if (static_cast<Unreal::AActor*>(actor)->GetWorld() != scope.context.world) cause = "world_changed";
            return std::string(prefix) + (i == 0 ? "first:" : "second:") + cause + ":" + scope.item_classes[i];
        }
        if (actors[0] && actors[0] == actors[1])
            return std::string(prefix) + "duplicate_child_output:" + scope.item_classes[0];
        return {};
    }

    template <std::size_t Count>
    bool read_addresses(const Lua& lua, std::array<Address, Count>& addresses)
    {
        for (auto& input_address : addresses)
        {
            if (!lua.is_integer(1)) return false;
            const auto value = lua.get_integer();
            if (value <= 0) return false;
            input_address = static_cast<Address>(value);
        }
        return true;
    }

    int open_crate(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || opening)
            return result(lua, false, "native_loot_scope_unavailable");
        const auto argc = lua.get_stack_size();
        if ((argc != 6 && argc != 7) || !lua.is_bool(6) || (argc == 7 && !lua.is_bool(7)))
            return result(lua, false, "native_loot_exact_open_arguments_required");
        std::array<Address, 5> addresses{};
        if (!read_addresses(lua, addresses)) return result(lua, false, "native_loot_exact_open_arguments_required");
        latest_roll = {};
        Context context;
        context.lucky = lua.get_bool();
        const bool force_nonempty = argc == 7 && lua.get_bool();
        if (force_nonempty && (!seeded_fixture_enabled() || fixture_callback == Hook::ERROR_ID))
            return result(lua, false, "stock_loot_fixture_seed_unavailable");
        if (const auto failure = qualify(addresses[0], addresses[1], addresses[2], addresses[3], addresses[4], context);
            !failure.empty()) return result(lua, false, failure);

        Schema schema;
        if (!bind_schema(context, schema)) return result(lua, false, "stock_loot_blueprint_schema_unavailable");
        SeedFixture fixture;
        if (force_nonempty)
        {
            if (const auto failure = fixture.bind(context, schema); !failure.empty())
                return result(lua, false, failure);
        }
        OpenParams input;
        schema.opener->SetObjectPropertyValue(schema.opener->ContainerPtrToValuePtr<void>(&input), context.pawn);
        schema.hit_component->SetObjectPropertyValue(schema.hit_component->ContainerPtrToValuePtr<void>(&input), context.box);
        OpenScope scope{context, schema};
        if (force_nonempty) scope.fixture = &fixture;
        bool restored = true;
        {
            ActiveOpen guard(scope);
            context.crate->ProcessEvent(schema.open, &input);
            if (force_nonempty) restored = fixture.restore();
        }

        const bool looted = authority(context.crate, context.world) && read_bool(context.crate, STR("Looted"), true);
        auto* first = scope.items[0].Get();
        auto* second = scope.items[1].Get();
        std::string item_problem;
        if (scope.problem.empty())
        {
            if (!restored) scope.problem = "stock_loot_fixture_original_stream_unverified";
            else if (!looted) scope.problem = "stock_loot_open_not_marked_looted";
            else if (!context.guarantee_spawn && scope.accounts != 1)
                scope.problem = "stock_loot_open_karma_query_unverified";
            else if (scope.spawns != std::array{1, 1}) scope.problem = "stock_loot_open_roll_calls_unverified";
            else if (object_field(context.crate, STR("Player")) != context.pawn)
                scope.problem = "stock_loot_open_opener_changed";
            else if (object_field(context.world, STR("OwningGameInstance")) != context.instance ||
                     object_field(context.instance, STR("SaveSlot_Ref")) != context.stock ||
                     object_field(context.pawn, STR("Save")) != context.save)
                scope.problem = "stock_loot_open_save_owner_changed";
            else if (item_problem = materialization_failure(scope, {first, second}); !item_problem.empty())
                scope.problem = item_problem;
            else if (force_nonempty)
            {
                if (scope.table_draws != std::array{1, 1} || scope.karma_draws != std::array{1, 1} ||
                    scope.rolls != std::array{1, 1})
                    scope.problem = "stock_loot_fixture_seed_route_unverified";
                else if (!first && !second)
                    scope.problem = "stock_loot_fixture_no_stock_spawn";
                else if ((first && first->GetClassPrivate() != fixture.selected[0]) ||
                         (second && second->GetClassPrivate() != fixture.selected[1]))
                    scope.problem = "stock_loot_fixture_spawn_class_unverified";
            }
        }
        const int traits = scope.accounts + scope.rolls[0] + scope.rolls[1];
        const bool ok = scope.problem.empty();
        if (ok)
        {
            latest_roll.crate = context.crate;
            latest_roll.pawn = context.pawn;
            latest_roll.save = context.save;
            latest_roll.stock = context.stock;
            latest_roll.items = scope.items;
            latest_roll.produced = scope.produced;
            latest_roll.expires = Clock::now() + std::chrono::seconds(2);
        }
        const std::string_view detail = force_nonempty ? "opened_fixture_seeded" : "opened";
        return result(lua, ok, ok ? detail : scope.problem, looted, address(first), address(second), traits, true);
    }

    int seed_result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int seed_lucky_looter(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || opening || !env_flag("SILVERFISH_COOP_NATIVE_FIXTURES"))
            return seed_result(lua, false, "native_loot_lucky_fixture_unavailable");
        std::array<Address, 3> addresses{};
        if (lua.get_stack_size() != 3 || !read_addresses(lua, addresses) || addresses[1] == addresses[2])
            return seed_result(lua, false, "native_loot_exact_detached_guest_required");

        Context context;
        if (!qualify_authored_opener(addresses[0], addresses[1], addresses[2], context))
            return seed_result(lua, false, "native_loot_exact_detached_guest_required");

        auto* character_type = find<Unreal::UScriptStruct>(character_type_path);
        auto* character = field<Unreal::FStructProperty>(context.save, STR("Character"));
        auto* effects = field<Unreal::FArrayProperty>(character_type, permanent_effects_name);
        auto* entry = effects ? Unreal::CastField<Unreal::FNameProperty>(effects->GetInner()) : nullptr;
        if (!character_type || !character || character->GetStruct().Get() != character_type || !entry ||
            character->GetOffset_Internal() != 0x210 || effects->GetOffset_Internal() != 0xb0 ||
            entry->GetArrayDim() != 1 || entry->GetOffset_Internal() != 0)
            return seed_result(lua, false, "native_loot_lucky_trait_schema_unavailable");

        const Unreal::FName lucky{STR("LuckyLooter"), Unreal::FNAME_Find};
        if (lucky.IsNone() || lucky.ToString() != STR("LuckyLooter"))
            return seed_result(lua, false, "native_loot_lucky_trait_name_unavailable");
        auto* guest_character = character->ContainerPtrToValuePtr<void>(context.save);
        auto* host_character = character->ContainerPtrToValuePtr<void>(context.stock);
        auto* guest_address = effects->ContainerPtrToValuePtr<void>(guest_character);
        auto* host_address = effects->ContainerPtrToValuePtr<void>(host_character);
        if (guest_address == host_address) return seed_result(lua, false, "native_loot_lucky_trait_save_alias");
        Unreal::FScriptArrayHelper guest(effects, guest_address);
        Unreal::FScriptArrayHelper host(effects, host_address);
        const auto guest_count = guest.Num();
        const auto host_count = host.Num();
        if (guest_count < 0 || guest_count > max_effects || host_count < 0 || host_count > max_effects ||
            (guest_count && host_count && guest.GetRawPtr(0) == host.GetRawPtr(0)))
            return seed_result(lua, false, "native_loot_lucky_trait_array_unavailable");

        std::array<Unreal::FName, max_effects> previous_guest{}, previous_host{};
        for (int index = 0; index < guest_count; ++index)
        {
            const auto value = entry->GetPropertyValue(guest.GetRawPtr(index));
            if (value == lucky) return seed_result(lua, true, "present");
            previous_guest[static_cast<std::size_t>(index)] = value;
        }
        if (guest_count == max_effects) return seed_result(lua, false, "native_loot_lucky_trait_array_full");
        for (int index = 0; index < host_count; ++index)
            previous_host[static_cast<std::size_t>(index)] = entry->GetPropertyValue(host.GetRawPtr(index));

        const auto appended = guest.AddValue();
        if (appended != guest_count || guest.Num() != guest_count + 1)
            return seed_result(lua, false, "mutation_unverified_native_loot_lucky_append");
        entry->SetPropertyValue(guest.GetRawPtr(appended), lucky);

        Context current;
        current.world = context.world;
        if (!qualify_opener(addresses[0], addresses[1], addresses[2], current).empty() ||
            current.instance != context.instance || current.controller != context.controller ||
            current.player != context.player || host.Num() != host_count || guest.Num() != guest_count + 1 ||
            entry->GetPropertyValue(guest.GetRawPtr(appended)) != lucky)
            return seed_result(lua, false, "mutation_unverified_native_loot_lucky_ownership_or_count");
        for (int index = 0; index < guest_count; ++index)
        {
            if (entry->GetPropertyValue(guest.GetRawPtr(index)) != previous_guest[static_cast<std::size_t>(index)])
                return seed_result(lua, false, "mutation_unverified_native_loot_lucky_guest_readback");
        }
        for (int index = 0; index < host_count; ++index)
        {
            if (entry->GetPropertyValue(host.GetRawPtr(index)) != previous_host[static_cast<std::size_t>(index)])
                return seed_result(lua, false, "mutation_unverified_native_loot_lucky_host_readback");
        }
        return seed_result(lua, true, "seeded");
    }
    const silverfish::authored_loot::Profile* profile_for(Unreal::UObject* crate)
    {
        if (!live(crate) || crate->GetClassPrivate() != find<Unreal::UClass>(crate_class)) return nullptr;
        const auto path = crate->GetPathName();
        const auto& profiles = silverfish::authored_loot::profiles;
        const auto match = std::ranges::find_if(profiles, [&](const auto& entry) { return path == entry.actor; });
        return match == profiles.end() ? nullptr : &*match;
    }
}

namespace silverfish::shared_loot
{
    Unreal::UWorld* authored_crate_world(Unreal::UObject* crate, std::string_view* failure)
    {
        const auto refuse = [&](std::string_view reason) -> Unreal::UWorld* {
            if (failure) *failure = reason;
            return nullptr;
        };
        if (!live(crate)) return refuse("crate_missing");
        const auto* profile = profile_for(crate);
        if (!profile) return refuse("crate_identity");
        auto* actor = static_cast<Unreal::AActor*>(crate);
        auto* world = actor->GetWorld();
        if (!live(world) || world->GetPathName() != profile->world ||
            world != find<Unreal::UWorld>(profile->world)) return refuse("world_identity");
        if (!read_bool(crate, STR("bActorIsBeingDestroyed"), false)) return refuse("crate_destroying");
        auto* level = object_field(world, STR("PersistentLevel"));
        if (!level || actor->GetLevel() != level) return refuse("crate_level");
        auto* salt = field<Unreal::FIntProperty>(crate, STR("SpawnSalt"));
        if (!salt || salt->GetPropertyValueInContainer(crate) != profile->salt) return refuse("crate_salt");
        return world;
    }

    bool qualified_crate_settings(Unreal::UObject* crate)
    {
        const auto* profile = profile_for(crate);
        if (!profile || !read_bool(crate, STR("GuaranteeSpawn"), profile->guarantee_spawn) ||
            !read_bool(crate, STR("IgnoreKarma"), false)) return false;
        // Keep the previously live-qualified stock route on its existing guards.
        if (profile == &authored_loot::profiles.front()) return true;
        auto* defaults = crate->GetClassPrivate()->GetClassDefaultObject().Get();
        if (!defaults || defaults->GetClassPrivate() != crate->GetClassPrivate() ||
            !read_bool(crate, STR("Ping_IfOpened"), false)) return false;

        auto* replenish = crate->GetPropertyByNameInChain(STR("Replenish_Mode"));
        auto* handle = field<Unreal::FStructProperty>(crate, STR("Loot_Override"));
        if (!replenish || replenish->GetArrayDim() != 1 || !handle) return false;
        if (profile->replenish_override)
        {
            auto* mode = Unreal::CastField<Unreal::FByteProperty>(replenish);
            auto* stock_enum = find<Unreal::UEnum>(replenish_enum);
            if (!mode || mode->GetSize() != 1 || !stock_enum || mode->GetEnum().Get() != stock_enum ||
                mode->GetPropertyValueInContainer(crate) != profile->replenish_override) return false;
        }
        else if (!replenish->Identical(replenish->ContainerPtrToValuePtr<void>(crate),
                                       replenish->ContainerPtrToValuePtr<void>(defaults))) return false;
        auto* live_handle = handle->ContainerPtrToValuePtr<void>(crate);
        if (!profile->loot_override)
            return handle->Identical(live_handle, handle->ContainerPtrToValuePtr<void>(defaults));

        auto* type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table_class = find<Unreal::UClass>(STR("/Script/Engine.DataTable"));
        auto* stock_table = find<Unreal::UObject>(
            STR("/Game/Classes/Libraries/DataTables/LootTables_DT.LootTables_DT"));
        auto* table = field<Unreal::FObjectProperty>(type, STR("DataTable"));
        auto* row = field<Unreal::FNameProperty>(type, STR("RowName"));
        if (!type || handle->GetStruct().Get() != type || !table_class || !live(stock_table) ||
            stock_table->GetClassPrivate() != table_class || !table || table->GetPropertyClass().Get() != table_class ||
            table->GetOffset_Internal() != 0 || !row || row->GetOffset_Internal() != sizeof(void*)) return false;

        // Cooked rows may omit the table pointer and let the stock container
        // use LootTables_DT. Any explicitly assigned table must be that asset.
        auto* assigned = table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(live_handle));
        return (!assigned || assigned == stock_table) &&
               row->GetPropertyValueInContainer(live_handle).ToString() == profile->loot_override;
    }

    bool qualify_escrow_owner(Address pawn, Address save, Address stock, EscrowOwner& owner)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || opening) return false;
        Context context;
        if (!qualify_authored_opener(pawn, save, stock, context)) return false;
        owner = {context.world, context.pawn, context.save, context.stock};
        return true;
    }

    bool qualify_escrow_owner_in_world(Unreal::UWorld* world, Address pawn, Address save, Address stock, EscrowOwner& owner)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || opening) return false;
        Context context;
        context.world = world;
        if (!qualify_opener(pawn, save, stock, context).empty()) return false;
        owner = {context.world, context.pawn, context.save, context.stock};
        return true;
    }

    bool verified_escrow_roll(Address crate, const std::array<Address, 2>& addresses,
                              EscrowOwner& owner, std::array<Unreal::UObject*, 2>& actors, bool consume)
    {
        auto* expected = latest_roll.crate.Get();
        auto* world = authored_crate_world(expected);
        if (!crate || !expected || crate != address(expected) || Clock::now() >= latest_roll.expires ||
            !world || !qualify_escrow_owner_in_world(world, address(latest_roll.pawn.Get()), address(latest_roll.save.Get()),
                                                     address(latest_roll.stock.Get()), owner) ||
            !authority(expected, owner.world) || !read_bool(expected, STR("Looted"), true) ||
            object_field(expected, STR("Player")) != owner.pawn) return false;

        for (std::size_t index = 0; index < actors.size(); ++index)
        {
            auto* item = latest_roll.items[index].Get();
            if (addresses[index] != address(item) || (latest_roll.produced[index] && !authority(item, owner.world)))
                return false;
            actors[index] = item;
        }
        if (consume) latest_roll = {};
        return true;
    }

    bool permits_stock_interaction(Unreal::UObject* crate, Unreal::UObject* pawn,
                                   Unreal::UObject* hit, Unreal::UFunction* function)
    {
        return opening && crate == opening->context.crate && pawn == opening->context.pawn &&
            hit == opening->context.box && function == opening->schema.open;
    }

    bool permits_stock_script(Unreal::UObject* crate, Unreal::UFunction* function)
    {
        return opening && crate == opening->context.crate && function == opening->schema.script;
    }

    void install_hooks()
    {
        if (!enabled() || callback != Hook::ERROR_ID) return;
        callback = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            capture, {false, false, owner, STR("Observe exact stock crate loot")});
        if (callback != Hook::ERROR_ID && seeded_fixture_enabled())
        {
            fixture_callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(
                seed_fixture_draw, {false, false, owner, STR("Seed exact staged stock crate draws")});
        }
        installed.store(callback != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        if (fixture_callback != Hook::ERROR_ID) Hook::UnregisterCallback(fixture_callback);
        callback = Hook::ERROR_ID;
        fixture_callback = Hook::ERROR_ID;
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishOpenNativeLootCrate", open_crate);
        if (env_flag("SILVERFISH_COOP_NATIVE_FIXTURES"))
            lua.register_function("SilverfishSeedNativeLootLuckyLooter", seed_lucky_looter);
    }
}
