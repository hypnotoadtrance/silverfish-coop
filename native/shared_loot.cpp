#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>

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
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto crate_path =
        STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.LootCrate_Small_BP_C_3");
    constexpr auto crate_class = STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C");
    constexpr auto parent_class =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C");
    constexpr auto child_class = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto open_path =
        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:SendInteractionToActor");
    constexpr auto crate_script_path =
        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:ExecuteUbergraph_LootCrate_Small_BP");
    constexpr auto karma_path =
        STR("/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C:AffectKarma");
    constexpr auto spawn_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:SpawnLoot");
    constexpr auto roll_path = STR("/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C:RollKarma");
    constexpr auto passive_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:FindPassiveEffect");
    constexpr auto box_location_path = STR("/Script/Engine.SceneComponent:K2_GetComponentLocation");
    constexpr auto box_collision_path = STR("/Script/Engine.PrimitiveComponent:GetCollisionEnabled");
    constexpr auto character_type_path =
        STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct");
    constexpr auto permanent_effects_name = STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00");
    constexpr auto authored_salt = -1911374248;
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

    std::string_view qualify(Address crate, Address pawn, Address box, Address save, Address stock, Context& context)
    {
        context.world = find<Unreal::UWorld>(world_path);
        context.crate = find<Unreal::UObject>(crate_path);
        auto* crate_type = find<Unreal::UClass>(crate_class);
        if (!live(context.world) || !live(context.crate) || address(context.crate) != crate || !crate_type ||
            context.crate->GetClassPrivate() != crate_type ||
            !authority(context.crate, context.world)) return "exact_stock_crate_unavailable";
        auto* level = object_field(context.world, STR("PersistentLevel"));
        auto* salt = field<Unreal::FIntProperty>(context.crate, STR("SpawnSalt"));
        auto* value = field<Unreal::FIntProperty>(context.crate, STR("SpawnedValue"));
        if (!level || static_cast<Unreal::AActor*>(context.crate)->GetLevel() != level ||
            !salt || salt->GetPropertyValueInContainer(context.crate) != authored_salt ||
            !value || value->GetPropertyValueInContainer(context.crate) != 0 ||
            !read_bool(context.crate, STR("Looted"), false) || !read_bool(context.crate, STR("IgnoreKarma"), false) ||
            !read_bool(context.crate, STR("GuaranteeSpawn"), false)) return "stock_crate_not_ready";

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
        schema.effect = argument<Unreal::FNameProperty>(schema.passive, STR("EffectName"), false, 0);
        schema.count = argument<Unreal::FBoolProperty>(schema.passive, STR("CountInstances"), false, 8);
        schema.active = argument<Unreal::FBoolProperty>(schema.passive, STR("EffectActive"), true, 9);
        schema.instances = argument<Unreal::FIntProperty>(schema.passive, STR("Instances"), true, 0xc);
        return schema.opener && schema.opener->GetPropertyClass().Get() == context.pawn->GetClassPrivate() &&
               schema.hit_component && schema.hit_component->GetPropertyClass().Get() == scene_type &&
               schema.item && schema.item->GetPropertyClass().Get() == schema.actor_type &&
               schema.effect && schema.count && schema.active && schema.instances;
    }

    struct OpenScope
    {
        const Context& context;
        const Schema& schema;
        std::array<Unreal::FWeakObjectPtr, 2> items{};
        std::array<bool, 2> produced{};
        std::array<int, 2> spawns{};
        std::array<int, 2> rolls{};
        int accounts{};
        std::string_view problem{};
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

    void override_luck(OpenScope& scope, Iteration& iteration, Unreal::UObject* object, Unreal::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        if (object != scope.context.instance || !caller) return;
        const auto index = spawner_index(scope.context, caller->Object());
        const bool account = caller->Object() == scope.context.crate && caller->Node() == scope.schema.karma;
        const bool roll = index >= 0 && caller->Node() == scope.schema.roll;
        if (!account && !roll) return;

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
        const auto slot = static_cast<std::size_t>(index);
        if (!caller || caller->Object() != scope.context.crate || caller->Node() != scope.schema.script ||
            frame.Object() != component || !out || reinterpret_cast<Address>(out) % alignof(void*) != 0 ||
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
        if (lua.get_stack_size() != 6 || !lua.is_bool(6))
            return result(lua, false, "native_loot_exact_open_arguments_required");
        std::array<Address, 5> addresses{};
        if (!read_addresses(lua, addresses)) return result(lua, false, "native_loot_exact_open_arguments_required");
        latest_roll = {};
        Context context;
        context.lucky = lua.get_bool();
        if (const auto failure = qualify(addresses[0], addresses[1], addresses[2], addresses[3], addresses[4], context);
            !failure.empty()) return result(lua, false, failure);

        Schema schema;
        if (!bind_schema(context, schema)) return result(lua, false, "stock_loot_blueprint_schema_unavailable");
        OpenParams input;
        schema.opener->SetObjectPropertyValue(schema.opener->ContainerPtrToValuePtr<void>(&input), context.pawn);
        schema.hit_component->SetObjectPropertyValue(schema.hit_component->ContainerPtrToValuePtr<void>(&input), context.box);
        OpenScope scope{context, schema};
        {
            ActiveOpen guard(scope);
            context.crate->ProcessEvent(schema.open, &input);
        }

        const bool looted = authority(context.crate, context.world) && read_bool(context.crate, STR("Looted"), true);
        auto* first = scope.items[0].Get();
        auto* second = scope.items[1].Get();
        if (scope.problem.empty())
        {
            if (!looted) scope.problem = "stock_loot_open_not_marked_looted";
            else if (scope.accounts != 1) scope.problem = "stock_loot_open_karma_query_unverified";
            else if (scope.spawns != std::array{1, 1}) scope.problem = "stock_loot_open_roll_calls_unverified";
            else if (object_field(context.crate, STR("Player")) != context.pawn)
                scope.problem = "stock_loot_open_opener_changed";
            else if (object_field(context.world, STR("OwningGameInstance")) != context.instance ||
                     object_field(context.instance, STR("SaveSlot_Ref")) != context.stock ||
                     object_field(context.pawn, STR("Save")) != context.save)
                scope.problem = "stock_loot_open_save_owner_changed";
            else if ((scope.produced[0] && !authority(first, context.world)) ||
                     (scope.produced[1] && !authority(second, context.world)) || (first && first == second))
                scope.problem = "stock_loot_open_spawned_item_unverified";
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
        return result(lua, ok, ok ? "opened" : scope.problem, looted, address(first), address(second), traits, true);
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
        context.world = find<Unreal::UWorld>(world_path);
        const auto owner_error = qualify_opener(addresses[0], addresses[1], addresses[2], context);
        if (!owner_error.empty()) return seed_result(lua, false, owner_error);

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
}

namespace silverfish::shared_loot
{
    bool qualify_escrow_owner(Address pawn, Address save, Address stock, EscrowOwner& owner)
    {
        if (!Unreal::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire) || opening) return false;
        Context context;
        context.world = find<Unreal::UWorld>(world_path);
        if (!qualify_opener(pawn, save, stock, context).empty()) return false;
        owner = {context.world, context.pawn, context.save, context.stock};
        return true;
    }

    bool verified_escrow_roll(Address crate, const std::array<Address, 2>& addresses,
                              EscrowOwner& owner, std::array<Unreal::UObject*, 2>& actors, bool consume)
    {
        auto* expected = latest_roll.crate.Get();
        if (!crate || !expected || crate != address(expected) || Clock::now() >= latest_roll.expires ||
            expected != find<Unreal::UObject>(crate_path) ||
            !qualify_escrow_owner(address(latest_roll.pawn.Get()), address(latest_roll.save.Get()),
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

    void install_hooks()
    {
        if (!enabled() || callback != Hook::ERROR_ID) return;
        callback = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            capture, {false, false, owner, STR("Observe exact stock crate loot")});
        installed.store(callback != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        callback = Hook::ERROR_ID;
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishOpenNativeLootCrate", open_crate);
        if (env_flag("SILVERFISH_COOP_NATIVE_FIXTURES"))
            lua.register_function("SilverfishSeedNativeLootLuckyLooter", seed_lucky_looter);
    }
}
