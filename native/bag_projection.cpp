#include "bag_projection.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;

    constexpr auto bag_path = STR("/Game/Classes/Actors/BoneBag_BP.BoneBag_BP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto mesh_path = STR("/Game/Classes/NPCs/NPC_Assets/CharacterModels/Heads_Gear/BackPack_02.BackPack_02");
    constexpr auto interact_path = STR("/Game/Classes/Actors/BoneBag_BP.BoneBag_BP_C:SendInteractionToActor");
    constexpr auto begin_play_path = STR("/Game/Classes/Actors/BoneBag_BP.BoneBag_BP_C:ReceiveBeginPlay");
    constexpr auto pawn_script_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr std::size_t maximum_bags = 128;
    constexpr auto maximum_sequence = (std::int64_t{1} << 53) - 1;
    constexpr double maximum_range_squared = 250.0 * 250.0;

    struct Scope
    {
        U::FWeakObjectPtr pawn, world, controller, driver, type;
        std::string epoch;
        bool host{};
    } scope;

    struct Bag
    {
        std::string id;
        U::FWeakObjectPtr actor, box;
        std::array<double, 3> source{};
        bool active{}, begin_veto{}, ready{};
    };

    struct Intent
    {
        std::string id;
        Clock::time_point expires{};
        std::int64_t sequence{};
        bool fixture{};
    };

    std::vector<Bag> bags;
    Intent intent;
    std::int64_t sequence{};
    U::FWeakObjectPtr fixture_actor;
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    template <class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <class T> T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, int size, bool result = false)
    {
        auto* prop = field<T>(function, name);
        return prop && prop->HasAnyPropertyFlags(U::CPF_Parm) &&
            prop->HasAnyPropertyFlags(U::CPF_ReturnParm) == result &&
            (result || !prop->HasAnyPropertyFlags(U::CPF_OutParm) || prop->HasAnyPropertyFlags(U::CPF_ConstParm)) &&
            prop->GetOffset_Internal() == offset && prop->GetSize() == size ? prop : nullptr;
    }

    U::UObject* read(U::FObjectProperty* prop, const void* container)
    {
        return prop && container ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(owner, name), owner);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* prop = field<U::FBoolProperty>(owner, name);
        return prop && prop->GetPropertyValueInContainer(owner) == expected;
    }

    int role(U::UObject* actor, const U::TCHAR* name = STR("Role"))
    {
        auto* prop = field<U::FByteProperty>(actor, name);
        return prop ? prop->GetPropertyValueInContainer(actor) : -1;
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool headless() { return env("SILVERFISH_COOP_HEADLESS"); }

    bool fixture_enabled(bool host)
    {
        return headless() && (!host || env("SILVERFISH_COOP_NATIVE_FIXTURES"));
    }

    bool ready() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    bool id_valid(std::string_view id)
    {
        return id.size() == 32 && std::ranges::all_of(id, [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
        });
    }

    bool path_valid(std::string_view path)
    {
        return path.starts_with("/Game/") && path.size() <= 512 && std::ranges::all_of(path, [](char c) {
            return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                c == '/' || c == ':' || c == '.' || c == '_';
        });
    }

    bool local_scope(const Scope& state)
    {
        auto* pawn = state.pawn.Get();
        auto* world = state.world.Get();
        auto* controller = state.controller.Get();
        auto* driver = state.driver.Get();
        if (!live(pawn) || pawn->GetClassPrivate() != find<U::UClass>(pawn_path) ||
            !live(controller) || controller->GetClassPrivate() != find<U::UClass>(controller_path) ||
            !live(world) || !live(driver) || object(world, STR("NetDriver")) != driver ||
            object(driver, STR("World")) != world || object(controller, STR("Pawn")) != pawn ||
            object(pawn, STR("Controller")) != controller || static_cast<U::AActor*>(pawn)->GetWorld() != world ||
            static_cast<U::AActor*>(controller)->GetWorld() != world || role(pawn) != (state.host ? 3 : 2)) return false;

        auto* player = object(controller, STR("Player"));
        auto* local = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* server_prop = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (!live(player) || !local || !player->IsA(local) || object(player, STR("PlayerController")) != controller ||
            !live(instance) || instance->GetClassPrivate() != find<U::UClass>(instance_path) || !server_prop) return false;
        auto* server = read(server_prop, driver);
        if (state.host) return !server && role(controller) == 3;
        auto* connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(server) && connection && server->IsA(connection) && object(server, STR("Driver")) == driver;
    }

    bool mutable_actor(U::UObject* actor)
    {
        return live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    bool inert(U::UObject* actor)
    {
        auto* contents = field<U::FArrayProperty>(actor, STR("Contents"));
        auto* inner = contents ? U::CastField<U::FStructProperty>(contents->GetInner()) : nullptr;
        auto* id = field<U::FStructProperty>(actor, STR("BagId"));
        auto* instance = field<U::FObjectProperty>(actor, STR("As Game Instance FP"));
        auto* guid = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
        if (!inner || !id || !guid || id->GetStruct().Get() != guid || id->GetSize() != 16 ||
            !instance || read(instance, actor) || !boolean(actor, STR("bReplicates"), false)) return false;
        U::FScriptArrayHelper inventory(contents, contents->ContainerPtrToValuePtr<void>(actor));
        if (inventory.Num() != 0) return false;
        const auto* bytes = id->ContainerPtrToValuePtr<const std::uint8_t>(actor);
        return std::ranges::all_of(std::span{bytes, std::size_t{16}}, [](auto byte) { return byte == 0; });
    }

    void prune()
    {
        std::erase_if(bags, [](const Bag& bag) { return !mutable_actor(bag.actor.Get()); });
    }

    void retire(Bag& bag)
    {
        bag.active = false;
        bag.ready = false;
        if (bag.id == intent.id) intent = {};
        auto* actor = static_cast<U::AActor*>(bag.actor.Get());
        if (!mutable_actor(actor)) return;
        actor->SetActorEnableCollision(false);
        actor->SetActorHiddenInGame(true);
        actor->K2_DestroyActor();
    }

    void clear()
    {
        scope = {};
        intent = {};
        fixture_actor = U::FWeakObjectPtr{};
        for (auto& bag : bags) retire(bag);
        prune();
    }

    Bag* bag_by_id(std::string_view id)
    {
        auto found = std::ranges::find_if(bags, [id](const Bag& bag) { return bag.active && bag.id == id; });
        return found == bags.end() ? nullptr : &*found;
    }

    bool within_range(const Bag& bag, U::UObject* pawn)
    {
        auto loc = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        const auto dx = loc.GetX() - bag.source[0];
        const auto dy = loc.GetY() - bag.source[1];
        const auto dz = loc.GetZ() - bag.source[2];
        const auto distance = dx * dx + dy * dy + dz * dz;
        return std::isfinite(distance) && distance <= maximum_range_squared;
    }

    bool interactable(const Bag& bag, U::UObject* pawn, U::UObject* hit)
    {
        auto* actor = bag.actor.Get();
        return bag.active && bag.ready && pawn && pawn == scope.pawn.Get() && bag.box.Get() == hit && live(hit) &&
            mutable_actor(actor) && hit->GetOuterPrivate() == actor && boolean(pawn, STR("Dead"), false) &&
            static_cast<U::AActor*>(actor)->GetWorld() == scope.world.Get() && within_range(bag, pawn);
    }

    U::UFunction* interaction(U::UClass* type)
    {
        auto* call = find<U::UFunction>(interact_path);
        if (!call || call->GetOuterPrivate() != type || call->GetNumParms() != 2 || call->GetParmsSize() != 16 ||
            call->GetReturnProperty() || call->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return nullptr;
        auto* player = parameter<U::FObjectProperty>(call, STR("Player"), 0, 8);
        auto* hit = parameter<U::FObjectProperty>(call, STR("HitComponent"), 8, 8);
        return player && player->GetPropertyClass().Get() == find<U::UClass>(pawn_path) &&
            hit && hit->GetPropertyClass().Get() == find<U::UClass>(STR("/Script/Engine.SceneComponent")) ? call : nullptr;
    }

    void intercept(Iteration& call, U::UObject* actor, U::UFunction* function, const void* parms, U::FFrame* frame)
    {
        if (!ready() || !actor || !function || bags.empty() || call.OriginalFunctionCallPrevented()) return;
        // Match the exact actor we spawned, including any hidden actor pending retirement.
        auto found = std::ranges::find_if(bags, [actor](const Bag& bag) { return bag.actor.Get() == actor; });
        if (found == bags.end() || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetReturnProperty()) return;
        auto& bag = *found;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented()) return;
        if (function == find<U::UFunction>(begin_play_path)) bag.begin_veto = true;
        if (!bag.ready || function != interaction(actor->GetClassPrivate()) || !local_scope(scope)) return;

        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), parms);
        auto* hit = read(field<U::FObjectProperty>(function, STR("HitComponent")), parms);
        if (!interactable(bag, pawn, hit)) return;
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        const bool fixture = !frame && fixture_actor.Get() == actor;
        const bool stock = caller && caller->Object() == pawn && caller->Node() == find<U::UFunction>(pawn_script_path) &&
            object(pawn, STR("InteractionTarget_Component")) == hit && boolean(pawn, STR("Holding_Interact"), true);
        if ((fixture || stock) && sequence < maximum_sequence)
            intent = {bag.id, Clock::now() + std::chrono::seconds(4), ++sequence, fixture};
    }

    bool object_parameter(U::UFunction* call, const U::TCHAR* name, int offset, U::UClass* type, bool result = false)
    {
        auto* property = parameter<U::FObjectProperty>(call, name, offset, 8, result);
        return type && property && property->GetPropertyClass().Get() == type;
    }

    bool transform_parameter(U::UFunction* call)
    {
        auto* property = parameter<U::FStructProperty>(call, STR("SpawnTransform"), 0x10, 0x60);
        auto* structure = find<U::UScriptStruct>(STR("/Script/CoreUObject.Transform"));
        return property && structure && property->GetStruct().Get() == structure;
    }

    bool byte_enum(U::UFunction* call, const U::TCHAR* name, int offset, const U::TCHAR* path)
    {
        auto* property = parameter<U::FEnumProperty>(call, name, offset, 1);
        return property && property->GetEnum().Get() == find<U::UEnum>(path) &&
            U::CastField<U::FByteProperty>(property->GetUnderlyingProperty());
    }

    struct DeferredArgs
    {
        U::UObject* world{};
        U::UClass* type{};
        U::FTransform_As500Plus transform;
        std::uint8_t collision{1}; // AlwaysSpawn
        U::UObject* owner{};
        std::uint8_t scale{1}; // MultiplyWithRoot
        U::AActor* result{};
    };

    struct FinishArgs
    {
        U::AActor* actor{};
        U::FTransform_As500Plus transform;
        std::uint8_t scale{1};
        U::AActor* result{};
    };

    static_assert(offsetof(DeferredArgs, transform) == 0x10 && offsetof(DeferredArgs, result) == 0x88);
    static_assert(offsetof(FinishArgs, transform) == 0x10 && offsetof(FinishArgs, result) == 0x78);

    struct Spawn
    {
        U::UObject* statics{};
        U::UFunction* begin{};
        U::UFunction* finish{};

        bool bind()
        {
            statics = find<U::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
            begin = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:BeginDeferredActorSpawnFromClass"));
            finish = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:FinishSpawningActor"));
            auto* actor = find<U::UClass>(STR("/Script/Engine.Actor"));
            auto* object = find<U::UClass>(STR("/Script/CoreUObject.Object"));
            auto* type = parameter<U::FClassProperty>(begin, STR("ActorClass"), 8, 8);
            return statics && begin && finish && begin->GetOuterPrivate() == statics->GetClassPrivate() &&
                finish->GetOuterPrivate() == statics->GetClassPrivate() && begin->GetNumParms() == 7 &&
                begin->GetParmsSize() == 0x90 && finish->GetNumParms() == 4 && finish->GetParmsSize() == 0x80 &&
                type && type->GetMetaClass().Get() == actor && transform_parameter(begin) && transform_parameter(finish) &&
                object_parameter(begin, STR("WorldContextObject"), 0, object) &&
                object_parameter(begin, STR("Owner"), 0x78, actor) && object_parameter(begin, STR("ReturnValue"), 0x88, actor, true) &&
                object_parameter(finish, STR("Actor"), 0, actor) && object_parameter(finish, STR("ReturnValue"), 0x78, actor, true) &&
                byte_enum(begin, STR("CollisionHandlingOverride"), 0x70, STR("/Script/Engine.ESpawnActorCollisionHandlingMethod")) &&
                byte_enum(begin, STR("TransformScaleMethod"), 0x80, STR("/Script/Engine.ESpawnActorScaleMethod")) &&
                byte_enum(finish, STR("TransformScaleMethod"), 0x70, STR("/Script/Engine.ESpawnActorScaleMethod"));
        }
    };

    const char* spawn_bag(std::string_view id, const std::array<double, 3>& source, double visual_z)
    {
        auto* type = U::Cast<U::UClass>(scope.type.Get());
        auto* world = scope.world.Get();
        if (!type || !local_scope(scope)) return "projection_scope_unavailable";
        prune();
        if (const auto* existing = bag_by_id(id))
            return existing->ready && existing->source == source ? "projected" : "projection_conflict";
        if (bags.size() >= maximum_bags) return "projection_capacity";
        auto* defaults = type->GetClassDefaultObject().Get();
        auto* begin_play = find<U::UFunction>(begin_play_path);
        Spawn spawn;
        if (!defaults || !inert(defaults) || !interaction(type) || !begin_play || begin_play->GetOuterPrivate() != type ||
            !spawn.bind()) return "projection_stock_schema_unavailable";

        DeferredArgs deferred;
        deferred.world = world;
        deferred.type = type;
        deferred.transform = U::FTransform{U::FQuat(0, 0, 0, 1), U::FVector(source[0], source[1], visual_z), U::FVector(1, 1, 1)};
        spawn.statics->ProcessEvent(spawn.begin, &deferred);
        auto* actor = deferred.result;
        if (!mutable_actor(actor) || actor->GetClassPrivate() != type || actor->GetWorld() != world)
            return "projection_spawn_unavailable";

        bags.push_back({std::string{id}, actor, {}, source, true, false, false});
        auto& bag = bags.back();
        auto* fall = field<U::FBoolProperty>(actor, STR("Fall"));
        if (fall && inert(actor))
        {
            fall->SetPropertyValueInContainer(actor, false);
            FinishArgs finish;
            finish.actor = actor;
            finish.transform = deferred.transform;
            spawn.statics->ProcessEvent(spawn.finish, &finish);
            auto* box = object(actor, STR("Box"));
            auto* mesh = object(actor, STR("BackPack_02"));
            auto* box_type = find<U::UClass>(STR("/Script/Engine.BoxComponent"));
            auto* backpack = find<U::UObject>(mesh_path);
            if (finish.result == actor && mutable_actor(actor) && bag.begin_veto && inert(actor) &&
                boolean(actor, STR("Fall"), false) && role(actor, STR("RemoteRole")) == 0 &&
                live(box) && box->GetOuterPrivate() == actor && box->GetClassPrivate() == box_type &&
                live(mesh) && mesh->GetOuterPrivate() == actor && backpack && object(mesh, STR("StaticMesh")) == backpack &&
                actor->GetWorld() == world && actor->GetActorEnableCollision())
            {
                bag.box = box;
                bag.ready = true;
                return "projected";
            }
        }
        retire(bag);
        prune();
        return "projection_inert_actor_unverified";
    }

    int result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 4 || !lua.is_string(1) || !lua.is_string(2) ||
            !lua.is_bool(3) || !lua.is_string(4)) return result(lua, false, "projection_scope_arguments");
        // LuaMadeSimple getters remove the argument, so consume the first slot in order.
        const std::string pawn_path_arg{lua.get_string()};
        const std::string world_path_arg{lua.get_string()};
        const bool host = lua.get_bool();
        const std::string epoch{lua.get_string()};
        if (!path_valid(pawn_path_arg) || !path_valid(world_path_arg) || !id_valid(epoch))
            return result(lua, false, "projection_scope_arguments");
        auto* world = find<U::UWorld>(RC::to_wstring(world_path_arg).c_str());
        auto* pawn = find<U::AActor>(RC::to_wstring(pawn_path_arg).c_str());
        auto* type = find<U::UClass>(bag_path);
        if (scope.world.Get() == world && scope.pawn.Get() == pawn && scope.epoch == epoch && scope.host == host &&
            scope.type.Get() == type && local_scope(scope)) return result(lua, true, "projection_scope_bound");
        clear();
        Scope next{pawn, world, object(pawn, STR("Controller")), object(world, STR("NetDriver")), type, epoch, host};
        if (!local_scope(next)) return result(lua, false, "projection_scope_unavailable");
        if (!type) return result(lua, false, "projection_stock_class_unavailable");
        scope = next;
        return result(lua, true, "projection_scope_bound");
    }

    int show(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 5 || !lua.is_string(1))
            return result(lua, false, "projection_position_arguments");
        const std::string id{lua.get_string()};
        std::array<double, 4> position{};
        for (std::size_t i = 0; i < position.size(); ++i)
        {
            if (!lua.is_number()) return result(lua, false, "projection_position_arguments");
            position[i] = lua.get_number();
        }
        if (!id_valid(id) || !std::ranges::all_of(position, [](double x) { return std::isfinite(x) && std::abs(x) <= 1e8; }) ||
            std::abs(position[3] - position[2]) > 150.0) return result(lua, false, "projection_position_arguments");
        const std::string_view status = spawn_bag(id, {position[0], position[1], position[2]}, position[3]);
        return result(lua, status == "projected", status);
    }

    int remove(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_string())
            return result(lua, false, "projection_id_arguments");
        const std::string id{lua.get_string()};
        if (!id_valid(id)) return result(lua, false, "projection_id_arguments");
        if (auto* bag = bag_by_id(id)) retire(*bag);
        prune();
        return result(lua, true, "projection_removed");
    }

    int focused(const Lua& lua)
    {
        auto* pawn = ready() ? scope.pawn.Get() : nullptr;
        const bool valid = pawn && lua.get_stack_size() == 1 && lua.is_integer(1) &&
            static_cast<std::uintptr_t>(lua.get_integer()) == reinterpret_cast<std::uintptr_t>(pawn) && local_scope(scope);
        auto* component = valid ? object(pawn, STR("InteractionTarget_Component")) : nullptr;
        if (component)
        {
            for (const auto& bag : bags)
            {
                if (interactable(bag, pawn, component))
                {
                    lua.set_string(bag.id);
                    return 1;
                }
            }
        }
        lua.set_string("");
        return 1;
    }

    int read_status(const Lua& lua)
    {
        const bool valid = ready() && local_scope(scope);
        const auto count = valid ? std::ranges::count_if(bags, [](const Bag& bag) { return bag.active && bag.ready; }) : 0;
        auto* bag = count && Clock::now() < intent.expires ? bag_by_id(intent.id) : nullptr;
        lua.set_bool(valid);
        lua.set_string(valid ? scope.epoch : "");
        lua.set_integer(count);
        lua.set_string(bag ? intent.id : "");
        lua.set_integer(bag ? intent.sequence : 0);
        lua.set_bool(bag && intent.fixture);
        return 6;
    }

    int probe(const Lua& lua)
    {
        if (!ready() || !fixture_enabled(scope.host) || lua.get_stack_size() != 1 || !lua.is_string(1) || !local_scope(scope))
            return result(lua, false, "projection_fixture_unavailable");
        auto* bag = bag_by_id(lua.get_string());
        auto* pawn = scope.pawn.Get();
        auto* actor = bag ? bag->actor.Get() : nullptr;
        auto* box = bag ? bag->box.Get() : nullptr;
        auto* wrapper = actor ? interaction(actor->GetClassPrivate()) : nullptr;
        if (!bag || !wrapper || !interactable(*bag, pawn, box) || !inert(actor))
            return result(lua, false, "projection_fixture_actor_unqualified");
        const auto before = sequence;
        std::array parms{pawn, box};
        fixture_actor = actor;
        actor->ProcessEvent(wrapper, parms.data());
        fixture_actor = U::FWeakObjectPtr{};
        const bool proved = sequence == before + 1 && intent.id == bag->id && intent.fixture && inert(actor);
        return result(lua, proved, proved ? "projection_stock_event_vetoed" : "projection_fixture_unverified");
    }

    int reset(const Lua&)
    {
        if (U::IsInGameThreadRaw()) clear();
        return 0;
    }
}

namespace silverfish::native_bag_projection
{
    void install_hooks()
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* function, void* parms) {
                intercept(call, actor, function, parms, nullptr);
            }, {false, false, STR("SilverfishCoopNative"), STR("Isolate co-op stock backpack event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* actor, U::FFrame& frame, void*) {
                intercept(call, actor, frame.Node(), frame.Locals(), &frame);
            }, {false, false, STR("SilverfishCoopNative"), STR("Isolate co-op stock backpack script")});
        const bool success = std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; });
        if (!success) uninstall_hooks();
        installed.store(success, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
        installed.store(false, std::memory_order_release);
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeBagProjectionScope", set_scope);
        lua.register_function("SilverfishShowNativeBagProjection", show);
        lua.register_function("SilverfishRemoveNativeBagProjection", remove);
        lua.register_function("SilverfishFocusedNativeBagProjection", focused);
        lua.register_function("SilverfishReadNativeBagProjection", read_status);
        lua.register_function("SilverfishClearNativeBagProjection", reset);
        if (headless()) lua.register_function("SilverfishProbeNativeBagProjection", probe);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) clear();
    }
}
