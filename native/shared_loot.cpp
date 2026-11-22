#include "shared_loot.hpp"
#include "authored_loot_registry.hpp"
#include "loot_crate_input.hpp"
#include "electronic_locker.hpp"
#include "loot_escrow.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <numeric>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/Engine/UDataTable.hpp>
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
    using silverfish::shared_loot::ContainerActors;
    using silverfish::shared_loot::max_container_slots;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto parent_class =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C");
    constexpr auto child_class = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto weapon_class = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C");
    constexpr auto replenish_enum = STR("/Game/Classes/Libraries/Enums_Lib/RespawnSetting_Enum.RespawnSetting_Enum");
    constexpr auto karma_path =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C:AffectKarma");
    constexpr auto spawn_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:SpawnLoot");
    constexpr auto roll_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:RollKarma");
    constexpr auto decide_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:DecideObject");
    constexpr auto table_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:RollTable");
    constexpr auto passive_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:FindPassiveEffect");
    constexpr auto items_library = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.Default__FL_Items_C");
    constexpr auto find_key_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Items.FL_Items_C:FindItem_InBackPack");
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
        ContainerActors spawners{};
        const silverfish::authored_loot::Profile* profile{};
        bool lucky{};
        bool guarantee_spawn{};
        bool red_lockbox_empty{};
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

    Unreal::UObject* live_object_at(Address requested)
    {
        if (!requested) return nullptr;
        // Resolve an address through the engine registry once, then let its
        // weak serial reject destroyed objects and recycled addresses.
        constexpr std::size_t cache_size = 64;
        thread_local std::array<Unreal::FWeakObjectPtr, cache_size> objects{};
        auto& cached = objects[(requested >> 4) % cache_size];
        if (auto* object = cached.Get(); address(object) == requested && live(object)) return object;

        Unreal::UObject* found{};
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != requested) return RC::LoopAction::Continue;
            if (live(candidate)) found = candidate;
            return RC::LoopAction::Break;
        });
        if (found) cached = Unreal::FWeakObjectPtr(found);
        return found;
    }

    Unreal::UObject* matching_pawn(Unreal::UObject* player, Address requested)
    {
        if (!live(player)) return nullptr;
        auto* controller = object_field(player, STR("PlayerController"));
        auto* pawn = live(controller) ? object_field(controller, STR("Pawn")) : nullptr;
        return address(pawn) == requested && live(pawn) ? pawn : nullptr;
    }

    Unreal::UObject* connected_pawn(Address requested, Unreal::UObject* instance, Unreal::UObject* driver)
    {
        if (!requested || !live(instance)) return nullptr;
        auto* players = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* local_entry = players ? Unreal::CastField<Unreal::FObjectProperty>(players->GetInner()) : nullptr;
        if (!local_entry) return nullptr;
        Unreal::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        if (locals.Num() != 1) return nullptr;
        if (auto* pawn = matching_pawn(local_entry->GetObjectPropertyValue(locals.GetRawPtr(0)), requested)) return pawn;

        if (!live(driver)) return nullptr;
        auto* clients = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* remote_entry = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
        if (!remote_entry) return nullptr;
        Unreal::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(driver));
        if (remotes.Num() < 1 || remotes.Num() > max_clients) return nullptr;
        for (int index = 0; index < remotes.Num(); ++index)
        {
            auto* connection = remote_entry->GetObjectPropertyValue(remotes.GetRawPtr(index));
            if (auto* pawn = matching_pawn(connection, requested)) return pawn;
        }
        return nullptr;
    }

    std::string_view qualify_opener(Address pawn, Address save, Address stock, Context& context,
                                    bool capture_only = false)
    {
        auto* player_type = find<Unreal::UClass>(pawn_class);
        auto* save_type = find<Unreal::UClass>(save_class);
        if (!live(context.world) || !player_type || !save_type) return "stock_opener_world_or_class_unavailable";
        context.instance = object_field(context.world, STR("OwningGameInstance"));
        auto* driver = object_field(context.world, STR("NetDriver"));
        context.pawn = connected_pawn(pawn, context.instance, driver);
        if (!context.pawn || context.pawn->GetClassPrivate() != player_type ||
            !authority(context.pawn, context.world) || (!capture_only && !read_bool(context.pawn, STR("Dead"), false)))
            return "standing_stock_opener_unavailable";
        context.save = object_field(context.pawn, STR("Save"));
        context.stock = object_field(context.instance, STR("SaveSlot_Ref"));
        context.controller = object_field(context.pawn, STR("Controller"));
        context.player = object_field(context.controller, STR("Player"));
        auto* instance_type = find<Unreal::UClass>(instance_class);
        auto* controller_type = find<Unreal::UClass>(controller_class);
        auto* health = field<Unreal::FDoubleProperty>(context.save, STR("PlayerHealth"));
        const auto hp = health ? health->GetPropertyValueInContainer(context.save) : 0.0;
        if (!instance_type || !controller_type || !live(context.instance) ||
            context.instance->GetClassPrivate() != instance_type || !live(context.save) || !live(context.stock) ||
            context.save->GetClassPrivate() != save_type || context.stock->GetClassPrivate() != save_type ||
            address(context.save) != save || address(context.stock) != stock || !health || !std::isfinite(hp) ||
            (capture_only ? context.save != context.stock : hp <= 0.0) ||
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
        auto* player = live_object_at(pawn);
        if (!player || player->GetClassPrivate() != find<Unreal::UClass>(pawn_class)) return false;
        auto* world = static_cast<Unreal::AActor*>(player)->GetWorld();
        if (!live(world)) return false;
        // Looking up every authored map with StaticFindObject makes each unloaded map
        // miss fall back to a full Unreal object-table scan. Only this pawn's world matters.
        const auto path = world->GetPathName();
        if (std::ranges::none_of(silverfish::authored_loot::worlds,
                [&path](const auto* authored) { return path == authored; })) return false;
        context.world = world;
        return qualify_opener(pawn, save, stock, context).empty();
    }

    std::string_view qualify(Address crate, Address pawn, Address box, Address save, Address stock, Context& context)
    {
        context.crate = live_object_at(crate);
        context.profile = silverfish::shared_loot::authored_profile(context.crate);
        context.world = silverfish::shared_loot::authored_crate_world(context.crate);
        if (!context.profile || !context.world ||
            !authority(context.crate, context.world)) return "exact_stock_crate_unavailable";
        auto* value = field<Unreal::FIntProperty>(context.crate, STR("SpawnedValue"));
        if (!value || value->GetPropertyValueInContainer(context.crate) != 0 ||
            !read_bool(context.crate, STR("Looted"), false) ||
            !silverfish::shared_loot::qualified_crate_settings(context.crate)) return "stock_crate_not_ready";
        context.guarantee_spawn = read_bool(context.crate, STR("GuaranteeSpawn"), true);
        if (context.profile->stock_class == &silverfish::authored_loot::red_lockbox)
        {
            auto* empty = field<Unreal::FBoolProperty>(context.crate, STR("Empty"));
            auto* chance = field<Unreal::FDoubleProperty>(context.crate, STR("SpawnChance_Weapons"));
            if (!empty || !chance || chance->GetPropertyValueInContainer(context.crate) != 0.8 ||
                !read_bool(context.crate, STR("Seeded"), true) || !read_bool(context.crate, STR("ForceOverride_MapLootTables"), true))
                return "stock_red_lockbox_roll_schema_unavailable";
            context.red_lockbox_empty = empty->GetPropertyValueInContainer(context.crate);
        }

        if (const auto error = qualify_opener(pawn, save, stock, context); !error.empty()) return error;
        context.box = object_field(context.crate, STR("Box"));
        auto* component_type = find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"));
        if (!component_type || !live(context.box) || address(context.box) != box ||
            context.box->GetClassPrivate() != component_type || context.box->GetOuterPrivate() != context.crate ||
            object_field(context.crate, STR("As Game Instance FP")) != context.instance)
            return "stock_crate_components_unavailable";
        if (!silverfish::shared_loot::read_spawners(context.crate, context.instance, context.spawners))
            return "stock_crate_child_spawner_unavailable";
        if (!query_collision_enabled(context.box)) return "stock_crate_box_not_interactable";
        return {};
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
        Unreal::UFunction* find_key{};
        Unreal::UObject* key_library{};
        Unreal::UClass* key{};
        Unreal::FClassProperty* wanted_key{};
        Unreal::FObjectProperty* key_context{};
        Unreal::FObjectProperty* key_world{};
        Unreal::FBoolProperty* key_found{};
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
        Unreal::UClass* weapon_type{};
    };

    bool blueprint(Unreal::UFunction* function, Unreal::UClass* owner_class, int parameters)
    {
        return function && owner_class && function->GetOuterPrivate() == owner_class &&
               function->GetNumParms() == parameters && !function->GetReturnProperty() &&
               !function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net);
    }

    bool bind_schema(const Context& context, Schema& schema)
    {
        schema.open = find<Unreal::UFunction>(context.profile->stock_class->input);
        schema.script = find<Unreal::UFunction>(context.profile->stock_class->script);
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
        if (context.profile->stock_class->weapon_first) schema.weapon_type = find<Unreal::UClass>(weapon_class);
        schema.effect = argument<Unreal::FNameProperty>(schema.passive, STR("EffectName"), false, 0);
        schema.count = argument<Unreal::FBoolProperty>(schema.passive, STR("CountInstances"), false, 8);
        schema.active = argument<Unreal::FBoolProperty>(schema.passive, STR("EffectActive"), true, 9);
        schema.instances = argument<Unreal::FIntProperty>(schema.passive, STR("Instances"), true, 0xc);
        return schema.opener && schema.opener->GetPropertyClass().Get() == context.pawn->GetClassPrivate() &&
               schema.hit_component && schema.hit_component->GetPropertyClass().Get() == scene_type &&
               schema.item && schema.item->GetPropertyClass().Get() == schema.actor_type &&
               schema.guaranteed && (!context.profile->stock_class->weapon_first || schema.weapon_type) &&
               schema.effect && schema.count && schema.active && schema.instances;
    }

    bool bind_key_lookup(Unreal::UClass* key, Schema& schema)
    {
        schema.key = key;
        schema.key_library = find<Unreal::UObject>(items_library);
        schema.find_key = find<Unreal::UFunction>(find_key_path);
        if (!schema.key_library || !blueprint(schema.find_key, schema.key_library->GetClassPrivate(), 7)) return false;
        schema.wanted_key = argument<Unreal::FClassProperty>(schema.find_key, STR("Item"), false, 0);
        schema.key_context = argument<Unreal::FObjectProperty>(schema.find_key, STR("WorldContextObject"), false, sizeof(void*));
        schema.key_world = argument<Unreal::FObjectProperty>(schema.find_key, STR("__WorldContext"), false, 2 * sizeof(void*));
        schema.key_found = argument<Unreal::FBoolProperty>(schema.find_key, STR("ItemFound"), true, 3 * sizeof(void*));
        return schema.wanted_key && schema.wanted_key->GetMetaClass().Get() == schema.item_type &&
               schema.key_context && schema.key_world && schema.key_found;
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

    struct SpawnObservation
    {
        void* output{};
        bool guaranteed{};
        bool finished{};
        bool actor_returned{};
        bool weapon_returned{};
    };

    struct ArmorLockerRoll
    {
        Unreal::FStructProperty* stream{};
        std::array<std::int32_t, 2> final_seeds{};
        std::array<bool, 3> draws{true, false, false};

        bool predict(const Context& context)
        {
            auto* stream_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.RandomStream"));
            stream = field<Unreal::FStructProperty>(context.crate, STR("StreamSeed"));
            auto* initial = field<Unreal::FIntProperty>(stream_type, STR("InitialSeed"));
            auto* current = field<Unreal::FIntProperty>(stream_type, STR("Seed"));
            auto* math = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetMathLibrary"));
            auto* draw = find<Unreal::UFunction>(STR("/Script/Engine.KismetMathLibrary:RandomBoolWithWeightFromStream"));
            struct Draw
            {
                std::array<std::int32_t, 2> seeds{};
                float weight{};
                std::uint8_t accepted{};
            } parameters;
            auto* input = field<Unreal::FStructProperty>(draw, STR("RandomStream"));
            auto* weight = argument<Unreal::FFloatProperty>(draw, STR("Weight"), false, offsetof(Draw, weight));
            auto* output = field<Unreal::FBoolProperty>(draw, STR("ReturnValue"));
            if (!stream_type || !stream || stream->GetStruct().Get() != stream_type || stream->GetSize() != sizeof(parameters.seeds) ||
                !initial || initial->GetOffset_Internal() != 0 || !current || current->GetOffset_Internal() != sizeof(std::int32_t) ||
                !math || !draw || draw->GetOuterPrivate() != math->GetClassPrivate() || !draw->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
                draw->GetNumParms() != 3 || draw->GetParmsSize() <= offsetof(Draw, accepted) || draw->GetParmsSize() > sizeof(Draw) ||
                !input || !input->HasAnyPropertyFlags(Unreal::CPF_Parm) || input->GetOffset_Internal() != 0 ||
                input->GetStruct().Get() != stream_type || input->GetSize() != sizeof(parameters.seeds) ||
                !weight || !output || output != draw->GetReturnProperty() || output->GetOffset_Internal() != offsetof(Draw, accepted))
                return false;

            const auto* stock = stream->ContainerPtrToValuePtr<std::array<std::int32_t, 2>>(context.crate);
            parameters.seeds = *stock;
            const auto initial_seed = parameters.seeds[0];
            // Use the engine on a copy. The stock ladder still owns and advances the live stream.
            constexpr std::array chances{0.66f, 0.33f};
            for (std::size_t index = 0; index < chances.size(); ++index)
            {
                const auto before = parameters.seeds[1];
                parameters.weight = chances[index];
                math->ProcessEvent(draw, &parameters);
                if (parameters.seeds[0] != initial_seed || parameters.seeds[1] == before) return false;
                draws[index + 1] = context.guarantee_spawn || output->GetPropertyValueInContainer(&parameters);
            }
            final_seeds = parameters.seeds;
            return true;
        }
    };

    struct OpenScope
    {
        const Context& context;
        const Schema& schema;
        std::array<Unreal::FWeakObjectPtr, max_container_slots> items{};
        std::array<bool, max_container_slots> produced{};
        std::array<std::string, max_container_slots> item_classes{};
        std::array<SpawnObservation, max_container_slots> spawns{};
        std::array<int, max_container_slots> rolls{};
        int accounts{};
        int key_checks{};
        std::string_view problem{};
        SeedFixture* fixture{};
        std::array<int, 2> table_draws{};
        std::array<int, 2> karma_draws{};
        ArmorLockerRoll armor{};
    };

    using Clock = std::chrono::steady_clock;

    struct VerifiedRoll
    {
        Unreal::FWeakObjectPtr crate{};
        Unreal::FWeakObjectPtr pawn{};
        Unreal::FWeakObjectPtr save{};
        Unreal::FWeakObjectPtr stock{};
        std::array<Unreal::FWeakObjectPtr, max_container_slots> items{};
        std::array<bool, max_container_slots> produced{};
        Clock::time_point expires{};
    };

    thread_local OpenScope* opening = nullptr;
    thread_local VerifiedRoll latest_roll;
    std::atomic<bool> installed{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;
    Hook::GlobalCallbackId spawn_callback = Hook::ERROR_ID;

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
        if (!component) return -1;
        const auto match = std::ranges::find(context.spawners, component);
        return match == context.spawners.end() ? -1 : static_cast<int>(match - context.spawners.begin());
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
        // AffectKarma uses the container's setting. Weapon-first crates can
        // independently select the gun draw's guarantee through the stock stream.
        if (scope.context.profile->stock_class->armor_first || (account ? scope.context.guarantee_spawn :
            !scope.spawns[index].output || scope.spawns[index].guaranteed || scope.spawns[index].finished))
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

    void* stock_spawn_output(const OpenScope& scope, Iteration& iteration, Unreal::UObject* component, Unreal::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        auto* out = find_output(frame, scope.schema.item);
        return caller && caller->Object() == scope.context.crate && caller->Node() == scope.schema.script &&
               frame.Object() == component && out && reinterpret_cast<Address>(out) % alignof(void*) == 0 &&
               !iteration.OriginalFunctionCallPrevented() ? out : nullptr;
    }

    void prepare_spawn(Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame, void* result_value)
    {
        if (!opening || !object || !installed.load(std::memory_order_acquire)) return;
        auto& scope = *opening;
        if (frame.Node() != scope.schema.spawn)
        {
            seed_fixture_draw(iteration, object, frame, result_value);
            return;
        }
        const auto index = spawner_index(scope.context, object);
        if (index < 0) return;
        auto* out = stock_spawn_output(scope, iteration, object, frame);
        auto& prepared = scope.spawns[static_cast<std::size_t>(index)];
        const auto* locals = frame.Locals();
        if (!out || !locals || prepared.output)
        {
            scope.problem = "stock_loot_spawner_output_unverified";
            return;
        }
        // The Blueprint loop reuses the output variable. SpawnLoot leaves it
        // untouched when no class is selected, so clear it before each draw.
        scope.schema.item->SetObjectPropertyValue(out, nullptr);
        prepared.output = out;
        prepared.guaranteed = scope.schema.guaranteed->GetPropertyValueInContainer(locals);
    }

    void capture_item(OpenScope& scope, Iteration& iteration, Unreal::UObject* component, Unreal::FFrame& frame)
    {
        const auto index = spawner_index(scope.context, component);
        if (index < 0) return;
        auto* out = stock_spawn_output(scope, iteration, component, frame);
        const auto slot = static_cast<std::size_t>(index);
        auto& spawn = scope.spawns[slot];
        if (!out || out != spawn.output || spawn.finished)
        {
            scope.problem = "stock_loot_spawner_frame_unverified";
            return;
        }
        spawn.finished = true;
        auto* actor = scope.schema.item->GetObjectPropertyValue(out);
        if (!actor) return;
        if (!live(actor) || !actor->IsA(scope.schema.actor_type) || !authority(actor, scope.context.world))
        {
            scope.problem = "stock_loot_spawner_result_unverified";
            return;
        }
        spawn.actor_returned = true;
        spawn.weapon_returned = scope.schema.weapon_type && actor->IsA(scope.schema.weapon_type);
        if (actor->IsA(scope.schema.item_type))
        {
            scope.produced[slot] = true;
            scope.items[slot] = actor;
            scope.item_classes[slot] = RC::to_utf8_string(actor->GetClassPrivate()->GetPathName());
        }
        else if (scope.context.profile->pinned_furniture && scope.context.profile->stock_class == &silverfish::authored_loot::office_desk)
            scope.problem = "stock_office_desk_item_class_unverified";
    }

    bool verified_spawn_route(const OpenScope& scope)
    {
        const auto& stock = *scope.context.profile->stock_class;
        const bool guaranteed = scope.context.guarantee_spawn;
        if (&stock == &silverfish::authored_loot::red_lockbox)
        {
            const auto& gun = scope.spawns[3];
            const bool ammo = !scope.context.red_lockbox_empty && gun.weapon_returned;
            const bool fallback = !scope.context.red_lockbox_empty && !gun.finished;
            const std::array expected{ammo, fallback, ammo, !scope.context.red_lockbox_empty && gun.finished};
            for (std::size_t index = 0; index < expected.size(); ++index)
            {
                const auto& spawn = scope.spawns[index];
                if (spawn.finished != expected[index] || (spawn.finished && spawn.guaranteed != guaranteed)) return false;
            }
            return true;
        }
        if (stock.armor_first)
        {
            const auto& expected = scope.armor;
            if (!expected.stream ||
                *expected.stream->ContainerPtrToValuePtr<std::array<std::int32_t, 2>>(scope.context.crate) != expected.final_seeds)
                return false;
            for (std::size_t index = 0; index < expected.draws.size(); ++index)
            {
                const auto& spawn = scope.spawns[index];
                if (spawn.finished != expected.draws[index] || (spawn.finished && !spawn.guaranteed)) return false;
            }
            return true;
        }
        if (!stock.weapon_first)
        {
            return std::all_of(scope.spawns.begin(), scope.spawns.begin() + stock.slots, [guaranteed](const auto& spawn) {
                return spawn.finished && (!guaranteed || spawn.guaranteed);
            });
        }

        // Cooked gun crates always try the last slot first. A Weapon_Master
        // result runs the two ammo slots. An empty regular or Cult gun draw
        // runs all four item slots. A non-weapon result skips both loops.
        const auto& gun = scope.spawns[4];
        const bool gun_only = &stock == &silverfish::authored_loot::weapon_crate;
        if (!gun.finished || (gun_only && gun.guaranteed != guaranteed)) return false;
        const bool fallback = !gun_only && !gun.actor_returned;
        for (std::size_t index = 0; index < 4; ++index)
        {
            const bool ammo = gun.weapon_returned && (index == 0 || index == 3);
            const auto& slot = scope.spawns[index];
            if (slot.finished != (fallback || ammo) || (slot.finished && slot.guaranteed != (ammo || guaranteed)))
                return false;
        }
        return true;
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
        else if (opening->schema.find_key && frame.Node() == opening->schema.find_key)
        {
            auto& scope = *opening;
            const auto& schema = scope.schema;
            auto* caller = frame.PreviousFrame();
            const auto* locals = frame.Locals();
            auto* found = find_output(frame, schema.key_found);
            if (!caller || caller->Node() != schema.script || caller->Object() != scope.context.crate ||
                object != schema.key_library || frame.Object() != object || !locals || !found ||
                schema.wanted_key->GetObjectPropertyValue(schema.wanted_key->ContainerPtrToValuePtr<void>(locals)) != schema.key ||
                schema.key_context->GetObjectPropertyValue(schema.key_context->ContainerPtrToValuePtr<void>(locals)) != scope.context.crate ||
                schema.key_world->GetObjectPropertyValue(schema.key_world->ContainerPtrToValuePtr<void>(locals)) != scope.context.crate ||
                ++scope.key_checks != 1 || iteration.OriginalFunctionCallPrevented())
            {
                scope.problem = "stock_container_key_lookup_unverified";
                return;
            }
            // This stock branch only consumes ItemFound. Resolve it against
            // the opener's verified backpack without rebinding the world save.
            iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented()) schema.key_found->SetPropertyValue(found, true);
            else scope.problem = "stock_container_key_lookup_unverified";
        }
    }

    int result(const Lua& lua, bool ok, std::string_view detail, bool looted = false,
               Address item1 = 0, Address item2 = 0, int trait_calls = 0, bool invoked = false,
               std::span<Unreal::UObject* const> extra_items = {})
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        lua.set_bool(looted);
        lua.set_integer(static_cast<std::int64_t>(item1));
        lua.set_integer(static_cast<std::int64_t>(item2));
        lua.set_integer(trait_calls);
        lua.set_bool(invoked);
        for (auto* item : extra_items) lua.set_integer(static_cast<std::int64_t>(address(item)));
        return static_cast<int>(7 + extra_items.size());
    }

    std::string materialization_failure(const OpenScope& scope, const ContainerActors& actors)
    {
        constexpr auto prefix = "stock_loot_open_spawned_item_unverified:";
        for (std::size_t i = 0; i < actors.size(); ++i)
        {
            auto* actor = actors[i];
            if (actor && std::find(actors.begin(), actors.begin() + i, actor) != actors.begin() + i)
                return std::string(prefix) + "duplicate_child_output:" + scope.item_classes[i];
            if (!scope.produced[i] || authority(actor, scope.context.world)) continue;
            auto* known = actor ? actor : scope.items[i].Get(true);
            const char* cause = "authority_changed";
            if (!known) cause = "stale_weak_object";
            else if (!actor) cause = "pending_kill";
            else if (!live(actor)) cause = "destroyed_or_unreachable";
            else if (!read_bool(actor, STR("bActorIsBeingDestroyed"), false)) cause = "actor_being_destroyed";
            else if (static_cast<Unreal::AActor*>(actor)->GetWorld() != scope.context.world) cause = "world_changed";
            return std::string(prefix) + std::to_string(i + 1) + ":" + cause + ":" + scope.item_classes[i];
        }
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
        if (argc < 6 || argc > 9 || !lua.is_bool(6) || (argc >= 7 && !lua.is_bool(7)) ||
            (argc >= 8 && !lua.is_bool(8)) || (argc == 9 && !lua.is_integer(9)))
            return result(lua, false, "native_loot_exact_open_arguments_required");
        std::array<Address, 5> addresses{};
        if (!read_addresses(lua, addresses)) return result(lua, false, "native_loot_exact_open_arguments_required");
        latest_roll = {};
        Context context;
        context.lucky = lua.get_bool();
        const bool force_nonempty = argc >= 7 && lua.get_bool();
        const bool pried = argc >= 8 && lua.get_bool();
        const std::int64_t power_receipt = argc == 9 ? lua.get_integer() : 0;
        const bool powered = power_receipt > 0;
        if (power_receipt < 0 || (argc == 9 && (!powered || force_nonempty || pried)))
            return result(lua, false, "stock_electronic_power_receipt_unavailable");
        if (force_nonempty && pried) return result(lua, false, "stock_loot_pry_fixture_scope_unavailable");
        if (force_nonempty && !seeded_fixture_enabled())
            return result(lua, false, "stock_loot_fixture_seed_unavailable");
        if (const auto failure = qualify(addresses[0], addresses[1], addresses[2], addresses[3], addresses[4], context);
            !failure.empty()) return result(lua, false, failure);
        Unreal::UClass* key{};
        if (!pried && !powered && context.profile->stock_class->pry_entry && read_bool(context.crate, STR("Locked"), true))
        {
            auto* key_field = field<Unreal::FClassProperty>(context.crate, STR("KeyItem"));
            if (key_field) key = static_cast<Unreal::UClass*>(
                key_field->GetObjectPropertyValue(key_field->ContainerPtrToValuePtr<void>(context.crate)));
            if (!key || !silverfish::loot_escrow::backpack_has_class(context.save, key))
            {
                const bool played = silverfish::shared_loot::jiggle_container_lock(context.crate);
                return result(lua, false, played ? "stock_vehicle_locked" : "stock_vehicle_lock_unavailable");
            }
        }

        Schema schema;
        if (!bind_schema(context, schema)) return result(lua, false, "stock_loot_blueprint_schema_unavailable");
        if (key && !bind_key_lookup(key, schema)) return result(lua, false, "stock_container_key_schema_unavailable");
        auto* opener = pried || powered ? field<Unreal::FObjectProperty>(context.crate, STR("Player")) : nullptr;
        if ((pried || powered) && (!opener || opener->GetPropertyClass().Get() != context.pawn->GetClassPrivate() ||
            schema.script->GetParmsSize() != sizeof(std::int32_t) ||
            !argument<Unreal::FIntProperty>(schema.script, STR("EntryPoint"), false, 0)))
            return result(lua, false, "stock_loot_deferred_open_schema_unavailable");
        if (pried && (!context.profile->stock_class->pry_entry ||
            !silverfish::native_loot_crate_input::consume_vehicle_pry(context.crate, context.pawn)))
            return result(lua, false, "stock_loot_pry_receipt_unavailable");
        if (powered && (context.save != context.stock || !silverfish::electronic_locker::is_electronic(*context.profile) ||
            !silverfish::electronic_locker::consume_power_open(context.crate, *context.profile, power_receipt)))
            return result(lua, false, "stock_electronic_power_receipt_unavailable");
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
        if (context.profile->stock_class->armor_first && !scope.armor.predict(context))
            return result(lua, false, "stock_armor_locker_stream_unavailable");
        if (force_nonempty) scope.fixture = &fixture;
        bool restored = true;
        {
            ActiveOpen guard(scope);
            if (pried || powered)
            {
                // The delayed entry does not assign the opener the way Interact does.
                opener->SetObjectPropertyValue(opener->ContainerPtrToValuePtr<void>(context.crate), context.pawn);
                auto point = powered ? silverfish::electronic_locker::power_open_entry : context.profile->stock_class->pry_entry;
                context.crate->ProcessEvent(schema.script, &point);
            }
            else context.crate->ProcessEvent(schema.open, &input);
            if (force_nonempty) restored = fixture.restore();
        }

        const bool looted = authority(context.crate, context.world) && read_bool(context.crate, STR("Looted"), true);
        const bool opener_unchanged = object_field(context.crate, STR("Player")) == context.pawn;
        const bool save_owner_unchanged = object_field(context.world, STR("OwningGameInstance")) == context.instance &&
            object_field(context.instance, STR("SaveSlot_Ref")) == context.stock &&
            object_field(context.pawn, STR("Save")) == context.save;
        const int traits = std::accumulate(scope.rolls.begin(), scope.rolls.end(), scope.accounts);
        if (key && context.profile->stock_class->key_unlock_only)
        {
            auto* spawned = field<Unreal::FIntProperty>(context.crate, STR("SpawnedValue"));
            const bool unlocked = scope.problem.empty() && scope.key_checks == 1 && opener_unchanged && save_owner_unchanged &&
                authority(context.crate, context.world) && read_bool(context.crate, STR("Locked"), false) &&
                read_bool(context.crate, STR("Looted"), false) && spawned && spawned->GetPropertyValueInContainer(context.crate) == 0 &&
                object_field(context.crate, STR("Box")) == context.box && query_collision_enabled(context.box) &&
                traits == 0 && std::ranges::none_of(scope.spawns, [](const auto& spawn) { return spawn.output || spawn.finished; });
            return result(lua, unlocked, unlocked ? "unlocked" : "stock_secure_locker_unlock_unverified",
                          looted, 0, 0, traits, true);
        }
        ContainerActors items{};
        for (std::size_t index = 0; index < items.size(); ++index) items[index] = scope.items[index].Get();
        auto* first = items[0];
        auto* second = items[1];
        const auto slots = context.profile->stock_class->slots;
        std::string item_problem;
        if (scope.problem.empty())
        {
            if (!restored) scope.problem = "stock_loot_fixture_original_stream_unverified";
            else if (key && scope.key_checks != 1) scope.problem = "stock_container_key_lookup_missing";
            else if (!looted) scope.problem = "stock_loot_open_not_marked_looted";
            else if (pried && !read_bool(context.crate, STR("Locked"), false))
                scope.problem = "stock_loot_pry_lock_not_released";
            else if (!context.guarantee_spawn && !context.profile->stock_class->armor_first && scope.accounts != 1)
                scope.problem = "stock_loot_open_karma_query_unverified";
            else if (!verified_spawn_route(scope))
                scope.problem = "stock_loot_open_roll_calls_unverified";
            else if (!opener_unchanged)
                scope.problem = "stock_loot_open_opener_changed";
            else if (!save_owner_unchanged)
                scope.problem = "stock_loot_open_save_owner_changed";
            else if (item_problem = materialization_failure(scope, items); !item_problem.empty())
                scope.problem = item_problem;
            else if (force_nonempty)
            {
                if (scope.table_draws != std::array{1, 1} || scope.karma_draws != std::array{1, 1} ||
                    scope.rolls[0] != 1 || scope.rolls[1] != 1)
                    scope.problem = "stock_loot_fixture_seed_route_unverified";
                else if (!first && !second)
                    scope.problem = "stock_loot_fixture_no_stock_spawn";
                else if ((first && first->GetClassPrivate() != fixture.selected[0]) ||
                         (second && second->GetClassPrivate() != fixture.selected[1]))
                    scope.problem = "stock_loot_fixture_spawn_class_unverified";
            }
        }
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
        const auto extra = slots > 2 ? std::span<Unreal::UObject* const>{items}.subspan(2, slots - 2) :
                                      std::span<Unreal::UObject* const>{};
        return result(lua, ok, ok ? detail : scope.problem, looted, address(first), address(second), traits, true, extra);
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
    using StockLootWeights = std::unordered_map<Unreal::UClass*, double>;

    bool read_loot_weights(Unreal::FMapProperty* property, const void* container, Unreal::UClass* item_base,
                           StockLootWeights& weights)
    {
        if (!property || !container || !item_base) return false;
        auto* key = Unreal::CastField<Unreal::FClassProperty>(property->GetKeyProp());
        auto* weight = Unreal::CastField<Unreal::FDoubleProperty>(property->GetValueProp());
        if (!key || key->GetMetaClass().Get() != item_base || key->GetOffset_Internal() != 0 ||
            key->GetSize() != sizeof(void*) || !weight || weight->GetSize() != sizeof(double)) return false;
        const auto layout = Unreal::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), weight->GetSize(), weight->GetMinAlignment());
        const auto offset = weight->GetOffset_Internal();
        if (offset != 0 && offset != layout.ValueOffset) return false;

        const auto* map = property->ContainerPtrToValuePtr<Unreal::FScriptMap>(container);
        const auto count = map->Num();
        const auto maximum = map->GetMaxIndex();
        if (count < 1 || count > maximum || maximum > 128) return false;
        for (int index = 0; index < maximum; ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = static_cast<const std::byte*>(map->GetData(index, layout));
            if (!entry) return false;
            auto* type = Unreal::Cast<Unreal::UClass>(key->GetObjectPropertyValue(entry));
            const auto value = weight->GetPropertyValue(entry + layout.ValueOffset);
            if (!live(type) || !type->IsChildOf(item_base) || !std::isfinite(value) || value <= 0.0 ||
                !weights.emplace(type, value).second) return false;
        }
        return weights.size() == static_cast<std::size_t>(count);
    }

    Unreal::UDataTable* stock_loot_table(Unreal::FMapProperty*& weights)
    {
        constexpr auto loot_table_path = STR("/Game/Classes/Libraries/DataTables/LootTables_DT.LootTables_DT");
        auto* table_class = find<Unreal::UClass>(STR("/Script/Engine.DataTable"));
        auto* loot = find<Unreal::UDataTable>(loot_table_path);
        auto* row_type = find<Unreal::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/LootTable_Struct.LootTable_Struct"));
        weights = field<Unreal::FMapProperty>(row_type, STR("LootMap_11_CA9A8DCF42896A52267711B3383A0F7A"));
        return live(loot) && table_class && loot->GetClassPrivate() == table_class && row_type &&
               loot->GetRowStruct().Get() == row_type && weights ? loot : nullptr;
    }

    bool add_stock_item_weights(const StockLootWeights& source, double multiplier, StockLootWeights& expected)
    {
        for (const auto& [type, weight] : source)
        {
            auto* defaults = type->GetClassDefaultObject().Get();
            auto* category = field<Unreal::FByteProperty>(defaults, STR("ItemType"));
            if (!defaults || !defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !category) return false;
            const auto value = category->GetPropertyValueInContainer(defaults);
            // The stock controller places weapons and body armor in separate maps.
            if (value != 4 && value != 7) expected.insert_or_assign(type, weight * multiplier);
        }
        return true;
    }

    std::string_view verify_lab_cabinet_loot(Unreal::UObject* cabinet, Unreal::FMapProperty* items)
    {
        constexpr auto controller_path = STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P:PersistentLevel.DynaimicSpawnControler_BP_C_1");
        constexpr auto controller_type = STR("/Game/Classes/Actors/DynaimicSpawnControler_BP.DynaimicSpawnControler_BP_C");
        constexpr auto region_table_path = STR("/Game/Classes/Libraries/DataTables/Region_SpawnPool_DT.Region_SpawnPool_DT");
        auto* controller = find<Unreal::AActor>(controller_path);
        auto* controller_class = find<Unreal::UClass>(controller_type);
        auto* world = static_cast<Unreal::AActor*>(cabinet)->GetWorld();
        if (!live(controller) || !controller_class || controller->GetClassPrivate() != controller_class ||
            !live(world) || controller->GetWorld() != world || controller->GetLevel() != object_field(world, STR("PersistentLevel")))
            return "cabinet_lab_loot_controller";

        auto* handle_type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* spawn_table = field<Unreal::FStructProperty>(controller, STR("SpawnTable"));
        auto* table_field = field<Unreal::FObjectProperty>(handle_type, STR("DataTable"));
        auto* row_field = field<Unreal::FNameProperty>(handle_type, STR("RowName"));
        auto* table_class = find<Unreal::UClass>(STR("/Script/Engine.DataTable"));
        auto* region = find<Unreal::UObject>(region_table_path);
        const Unreal::FName lab_row{STR("Lab_E206"), Unreal::FNAME_Find};
        if (!handle_type || !spawn_table || spawn_table->GetStruct().Get() != handle_type || !table_class ||
            !table_field || table_field->GetPropertyClass().Get() != table_class || !row_field || lab_row.IsNone() ||
            !live(region) || region->GetClassPrivate() != table_class)
            return "cabinet_lab_loot_source_schema";
        const void* handle = spawn_table->ContainerPtrToValuePtr<void>(controller);
        if (table_field->GetObjectPropertyValue(table_field->ContainerPtrToValuePtr<void>(handle)) != region ||
            row_field->GetPropertyValueInContainer(handle) != lab_row) return "cabinet_lab_loot_source_changed";

        auto* region_modifier = field<Unreal::FDoubleProperty>(cabinet, STR("RegionSpawnMod"));
        if (!read_bool(cabinet, STR("ForceOverride_MapLootTables"), false) || !region_modifier ||
            region_modifier->GetPropertyValueInContainer(cabinet) != 1.0) return "cabinet_lab_loot_mode_changed";
        Unreal::FMapProperty* loot_weights{};
        auto* table = stock_loot_table(loot_weights);
        auto* item_base = find<Unreal::UClass>(item_class);
        if (!table || !item_base) return "cabinet_lab_loot_table_schema";

        struct WeightedRow { const Unreal::TCHAR* name; double multiplier; std::size_t count; };
        constexpr std::array rows{WeightedRow{STR("Security_Basic"), 0.5, 12}, WeightedRow{STR("LabSupplies"), 1.0, 47}};
        StockLootWeights expected;
        // Stock Configure_LootTables overwrites duplicate class weights with the later region row.
        for (const auto& row : rows)
        {
            const Unreal::FName name{row.name, Unreal::FNAME_Find};
            const auto* data = name.IsNone() ? nullptr : table->FindRowUnchecked(name);
            StockLootWeights weights;
            if (!read_loot_weights(loot_weights, data, item_base, weights) || weights.size() != row.count)
                return "cabinet_lab_loot_table_row";
            if (!add_stock_item_weights(weights, row.multiplier, expected)) return "cabinet_lab_loot_item_category";
        }
        StockLootWeights active;
        if (!read_loot_weights(items, cabinet, item_base, active)) return "cabinet_lab_loot_items_schema";
        return active == expected ? std::string_view{} : "cabinet_lab_loot_items_changed";
    }

    std::string_view verify_office_desk_loot(Unreal::UObject* desk, Unreal::FMapProperty* items)
    {
        Unreal::FMapProperty* loot_weights{};
        auto* table = stock_loot_table(loot_weights);
        auto* item_base = find<Unreal::UClass>(item_class);
        if (!table || !item_base) return "desk_office_loot_schema";
        if (!read_bool(desk, STR("ForceOverride_MapLootTables"), false)) return "desk_office_loot_mode";
        const Unreal::FName row_name{STR("Office"), Unreal::FNAME_Find};
        const auto* row = row_name.IsNone() ? nullptr : table->FindRowUnchecked(row_name);
        StockLootWeights weights, expected, active;
        if (!read_loot_weights(loot_weights, row, item_base, weights) || weights.size() != 38 ||
            !add_stock_item_weights(weights, 1.0, expected)) return "desk_office_loot_row";
        if (!read_loot_weights(items, desk, item_base, active)) return "desk_office_loot_items_schema";
        return active == expected ? std::string_view{} : "desk_office_loot_items_changed";
    }

    bool verify_office_desk_scene(Unreal::UObject* desk)
    {
        auto* component_type = find<Unreal::UClass>(STR("/Script/Engine.StaticMeshComponent"));
        auto* frame_mesh = find<Unreal::UObject>(STR("/Game/Assets/StaticMeshes/Mesh_Misc/SM_Loot_Desk.SM_Loot_Desk"));
        auto* door_mesh = find<Unreal::UObject>(STR("/Game/Assets/StaticMeshes/Mesh_Misc/SM_Loot_Desk_Door_L.SM_Loot_Desk_Door_L"));
        auto* frame = object_field(desk, STR("SM_Prop_Cabinet_Small_02"));
        auto* first = object_field(desk, STR("SM_Prop_Cabinet_Small_Door_01"));
        auto* second = object_field(desk, STR("SM_Prop_Cabinet_Small_Door_02"));
        const auto mesh = [&](Unreal::UObject* component, Unreal::UObject* stock) {
            return component_type && live(stock) && live(component) && component->GetClassPrivate() == component_type &&
                   component->GetOuterPrivate() == desk && object_field(component, STR("StaticMesh")) == stock;
        };
        if (frame == first || frame == second || first == second || !mesh(frame, frame_mesh) ||
            !mesh(first, door_mesh) || !mesh(second, door_mesh) ||
            object_field(first, STR("AttachParent")) != frame || object_field(second, STR("AttachParent")) != frame)
            return false;
        if (read_bool(desk, STR("Looted"), true)) return true;
        auto* box_type = find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"));
        auto* box = object_field(desk, STR("Box"));
        return box_type && live(box) && box->GetClassPrivate() == box_type && box->GetOuterPrivate() == desk &&
               object_field(box, STR("AttachParent")) == frame;
    }
}

namespace silverfish::shared_loot
{
    const authored_loot::Profile* authored_profile(Unreal::UObject* crate)
    {
        if (!live(crate)) return nullptr;
        static const auto profiles_by_path = [] {
            std::unordered_map<std::basic_string_view<Unreal::TCHAR>, const authored_loot::Profile*> index;
            index.reserve(authored_loot::profiles.size());
            for (const auto& profile : authored_loot::profiles) index.emplace(profile.actor, &profile);
            return index;
        }();
        const auto path = crate->GetPathName();
        const auto match = profiles_by_path.find(path);
        return match != profiles_by_path.end() &&
                crate->GetClassPrivate() == find<Unreal::UClass>(match->second->stock_class->actor_class)
            ? match->second : nullptr;
    }

    Unreal::UWorld* authored_crate_world(Unreal::UObject* crate, std::string_view* failure)
    {
        const auto refuse = [&](std::string_view reason) -> Unreal::UWorld* {
            if (failure) *failure = reason;
            return nullptr;
        };
        if (!live(crate)) return refuse("crate_missing");
        const auto* profile = authored_profile(crate);
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

    bool qualified_crate_settings(Unreal::UObject* crate, std::string_view* reason)
    {
        const auto refuse = [&](std::string_view detail) {
            if (reason) *reason = detail;
            return false;
        };
        const auto* profile = authored_profile(crate);
        if (!profile) return refuse("crate_settings_profile");
        if (!read_bool(crate, STR("GuaranteeSpawn"), profile->guarantee_spawn)) return refuse("crate_settings_guarantee");
        if (!read_bool(crate, STR("IgnoreKarma"), profile->stock_class->armor_first)) return refuse("crate_settings_ignore_karma");
        if (profile->stock_class->armor_first && !read_bool(crate, STR("Seeded"), true)) return refuse("crate_settings_seeded");
        if (profile->stock_class->pry_entry)
        {
            auto* key_field = field<Unreal::FClassProperty>(crate, STR("KeyItem"));
            if (!field<Unreal::FBoolProperty>(crate, STR("Locked")) || !key_field)
                return refuse("stock_container_lock_schema_unqualified");
            auto* key = static_cast<Unreal::UClass*>(key_field->GetObjectPropertyValue(key_field->ContainerPtrToValuePtr<void>(crate)));
            auto* base = find<Unreal::UClass>(item_class);
            if (profile->key_class_name ? !live(key) || !base || !key->IsChildOf(base) ||
                key->GetName() != profile->key_class_name : key != nullptr)
                return refuse("stock_container_key_unqualified");
        }
        // Keep the previously live-qualified stock route on its existing guards.
        if (profile == &authored_loot::profiles.front()) return true;
        auto* defaults = crate->GetClassPrivate()->GetClassDefaultObject().Get();
        if (!defaults || defaults->GetClassPrivate() != crate->GetClassPrivate()) return refuse("crate_settings_defaults");
        if (!read_bool(crate, STR("Ping_IfOpened"), profile->stock_class->stock_ping || profile->extra_open_ping))
            return refuse("crate_settings_ping");
        if (profile->stock_class != &authored_loot::small_crate)
        {
            auto* opened = field<Unreal::FMulticastInlineDelegateProperty>(crate, STR("On_Opened"));
            if (!opened || opened->GetSize() != sizeof(Unreal::FMulticastScriptDelegate))
                return refuse("container_opened_delegate_schema");
            if (opened->ContainerPtrToValuePtr<Unreal::FMulticastScriptDelegate>(crate)->IsBound())
                return refuse("container_opened_delegate_bound");
        }
        if (profile->pinned_furniture)
        {
            auto* items = field<Unreal::FMapProperty>(crate, STR("Loot Table_Items"));
            auto* rate = field<Unreal::FDoubleProperty>(crate, STR("SpawnChance_Item"));
            if (!items) return refuse("cabinet_default_items_schema");
            const bool desk = profile->stock_class == &authored_loot::office_desk;
            const auto failure = desk
                ? verify_office_desk_loot(crate, items) : verify_lab_cabinet_loot(crate, items);
            if (!failure.empty()) return refuse(failure);
            if (desk && !verify_office_desk_scene(crate)) return refuse("desk_office_scene_changed");
            if (!rate) return refuse("cabinet_default_chance_schema");
            if (rate->GetPropertyValueInContainer(defaults) != 0.2 ||
                rate->GetPropertyValueInContainer(crate) != rate->GetPropertyValueInContainer(defaults))
                return refuse("cabinet_default_chance_changed");
            if (!read_bool(crate, STR("bReplicates"), false)) return refuse("cabinet_replication");
        }

        auto* replenish = crate->GetPropertyByNameInChain(STR("Replenish_Mode"));
        auto* handle = field<Unreal::FStructProperty>(crate, STR("Loot_Override"));
        if (!replenish || replenish->GetArrayDim() != 1 || !handle) return refuse("crate_settings_replenish_handle");
        if (profile->replenish_override)
        {
            auto* mode = Unreal::CastField<Unreal::FByteProperty>(replenish);
            auto* stock_enum = find<Unreal::UEnum>(replenish_enum);
            if (!mode || mode->GetSize() != 1 || !stock_enum || mode->GetEnum().Get() != stock_enum)
                return refuse("crate_settings_replenish_schema");
            if (mode->GetPropertyValueInContainer(crate) != profile->replenish_override)
                return refuse("crate_settings_replenish_override");
        }
        else if (!replenish->Identical(replenish->ContainerPtrToValuePtr<void>(crate),
                                       replenish->ContainerPtrToValuePtr<void>(defaults))) return refuse("crate_settings_replenish_default");
        auto* live_handle = handle->ContainerPtrToValuePtr<void>(crate);
        if (!profile->loot_override)
        {
            if (!handle->Identical(live_handle, handle->ContainerPtrToValuePtr<void>(defaults)))
                return refuse("crate_settings_loot_override_default");
            return true;
        }

        auto* type = find<Unreal::UScriptStruct>(STR("/Script/Engine.DataTableRowHandle"));
        auto* table_class = find<Unreal::UClass>(STR("/Script/Engine.DataTable"));
        auto* stock_table = find<Unreal::UObject>(
            STR("/Game/Classes/Libraries/DataTables/LootTables_DT.LootTables_DT"));
        auto* table = field<Unreal::FObjectProperty>(type, STR("DataTable"));
        auto* row = field<Unreal::FNameProperty>(type, STR("RowName"));
        if (!type || handle->GetStruct().Get() != type || !table_class || !live(stock_table) ||
            stock_table->GetClassPrivate() != table_class || !table || table->GetPropertyClass().Get() != table_class ||
            table->GetOffset_Internal() != 0 || !row || row->GetOffset_Internal() != sizeof(void*))
            return refuse("crate_settings_loot_override_schema");

        // Cooked rows may omit the table pointer and let the stock container
        // use LootTables_DT. Any explicitly assigned table must be that asset.
        auto* assigned = table->GetObjectPropertyValue(table->ContainerPtrToValuePtr<void>(live_handle));
        if ((assigned && assigned != stock_table) ||
            row->GetPropertyValueInContainer(live_handle).ToString() != profile->loot_override)
            return refuse("crate_settings_loot_override_row");
        return true;
    }

    bool jiggle_container_lock(Unreal::UObject* crate)
    {
        const auto* profile = authored_profile(crate);
        if (!profile || !profile->stock_class->pry_entry || !read_bool(crate, STR("Locked"), true) ||
            !read_bool(crate, STR("Looted"), false)) return false;
        auto* jiggle = crate->GetFunctionByNameInChain(Unreal::FName{profile->stock_class->jiggle_function});
        if (!blueprint(jiggle, crate->GetClassPrivate(), 0) || jiggle->GetParmsSize() != 0) return false;
        crate->ProcessEvent(jiggle, nullptr);
        return true;
    }

    bool read_spawners(Unreal::UObject* crate, Unreal::UObject* instance, ContainerActors& spawners, std::string_view* reason)
    {
        const auto refuse = [&](std::string_view detail) {
            if (reason) *reason = detail;
            return false;
        };
        const auto* profile = authored_profile(crate);
        auto* type = find<Unreal::UClass>(child_class);
        if (!profile || !type || !instance) return refuse("crate_children_context");
        constexpr std::array regular{STR("Loot_Spawner"), STR("Loot_Spawner1"), STR("Loot_Spawner2"), STR("Loot_Spawner3"),
                                     STR("Loot_Spawner4"), STR("Loot_Spawner5"), STR("Loot_Spawner6")};
        const auto& stock = *profile->stock_class;
        if (!stock.slots || stock.slots > regular.size()) return refuse("crate_children_capacity");
        const auto fields = stock.spawner_names.empty() ? std::span<const Unreal::TCHAR* const>{regular}.first(stock.slots)
                                                        : stock.spawner_names;
        if (fields.size() != stock.slots || fields.size() > spawners.size()) return refuse("crate_children_fields");
        spawners = {};
        for (std::size_t index = 0; index < fields.size(); ++index)
        {
            auto* child = object_field(crate, fields[index]);
            if (!live(child) || child->GetClassPrivate() != type) return refuse("crate_child_class");
            if (child->GetOuterPrivate() != crate || object_field(child, STR("Container")) != crate)
                return refuse("crate_child_owner");
            if (object_field(child, STR("As Game Instance FP")) != instance) return refuse("crate_child_instance");
            if (std::find(spawners.begin(), spawners.begin() + index, child) != spawners.begin() + index)
                return refuse("crate_child_duplicate");
            if (profile->pinned_furniture)
            {
                auto* salt = field<Unreal::FIntProperty>(child, STR("SpawnerSalt"));
                if (!salt || salt->GetPropertyValueInContainer(child) != profile->child_salts[index])
                    return refuse("cabinet_child_salt");
            }
            spawners[index] = child;
        }
        if (&stock != &authored_loot::small_crate)
        {
            const auto children = static_cast<Unreal::AActor*>(crate)->K2_GetComponentsByClass(type);
            if (children.Num() != stock.slots) return refuse("crate_children_count");
            for (int index = 0; index < children.Num(); ++index)
                if (!children[index] || std::ranges::find(spawners, children[index]) == spawners.end())
                    return refuse("crate_children_membership");
        }
        return true;
    }

    int qualified_container_capacity(Address address_value, Unreal::UWorld* expected_world)
    {
        if (!address_value || !Unreal::IsInGameThreadRaw() || opening) return 0;
        auto* crate = live_object_at(address_value);
        const auto* profile = authored_profile(crate);
        auto* world = authored_crate_world(crate);
        if (!profile || !world || (expected_world && expected_world != world) || !authority(crate, world) ||
            !qualified_crate_settings(crate) || !read_bool(crate, STR("Looted"), true)) return 0;
        return profile->stock_class->slots;
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

    bool qualify_stock_drop_capture(Unreal::UWorld* world, Address pawn, Address stock, EscrowOwner& owner)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || opening) return false;
        Context context;
        context.world = world;
        if (!qualify_opener(pawn, stock, stock, context, true).empty()) return false;
        owner = {context.world, context.pawn, context.save, context.stock};
        return true;
    }

    bool verified_escrow_roll(Address crate, const ContainerAddresses& addresses, std::size_t slots,
                              EscrowOwner& owner, ContainerActors& actors, bool consume)
    {
        auto* expected = latest_roll.crate.Get();
        auto* world = authored_crate_world(expected);
        const auto* profile = authored_profile(expected);
        if (!crate || !expected || crate != address(expected) || Clock::now() >= latest_roll.expires ||
            !world || !profile || slots != profile->stock_class->slots ||
            !qualify_escrow_owner_in_world(world, address(latest_roll.pawn.Get()), address(latest_roll.save.Get()),
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
        spawn_callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            prepare_spawn, {false, false, owner, STR("Prepare exact stock crate spawn outputs")});
        if (callback == Hook::ERROR_ID || spawn_callback == Hook::ERROR_ID) uninstall_hooks();
        else installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        if (spawn_callback != Hook::ERROR_ID) Hook::UnregisterCallback(spawn_callback);
        callback = Hook::ERROR_ID;
        spawn_callback = Hook::ERROR_ID;
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishOpenNativeLootCrate", open_crate);
        if (env_flag("SILVERFISH_COOP_NATIVE_FIXTURES"))
            lua.register_function("SilverfishSeedNativeLootLuckyLooter", seed_lucky_looter);
    }
}
