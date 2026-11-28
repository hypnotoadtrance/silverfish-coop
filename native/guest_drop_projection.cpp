#include "guest_drop_projection.hpp"
#include "content_paths.hpp"
#include "guest_drop_items.hpp"
#include "guest_drop_maps.hpp"
#include "interaction_input.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/Rotator.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hooks = U::Hook;
    namespace input = silverfish::native_interaction_input;
    namespace items = silverfish::native_guest_drop_items;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hooks::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Position = std::array<double, 3>;

    constexpr auto ammo_base_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto health_item_base_path = STR("/Game/Classes/Items/HealthItem_Master_BP.HealthItem_Master_BP_C");
    constexpr auto consumable_base_path = STR("/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C");
    constexpr auto weapon_base_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C");
    constexpr auto equipment_base_path = STR("/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto interaction_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto ammo_begin_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C:ReceiveBeginPlay");
    constexpr auto consumable_begin_path = STR("/Game/Classes/Items/Consumable_Master_BP.Consumable_Master_BP_C:ReceiveBeginPlay");
    constexpr auto weapon_begin_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C:ReceiveBeginPlay");
    constexpr auto convert_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto floor_query_path = STR("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C:FindFloorItems");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_script_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr auto maximum_sequence = (std::int64_t{1} << 53) - 1;
    constexpr auto heartbeat = std::chrono::seconds(2);
    constexpr auto hidden_retention = std::chrono::seconds(8);
    constexpr auto pose_blend = std::chrono::milliseconds(80);
    constexpr double range = 250.0;
    constexpr std::size_t component_limit = 24;

    struct Actor
    {
        U::FWeakObjectPtr object, box, display_type, item_base;
        U::FWeakObjectPtr interaction, begin, converter, add_inventory;
        bool begin_veto{};
    };

    struct Lease
    {
        struct Motion
        {
            Position position, rotation;
            Clock::time_point start;
        };

        U::FWeakObjectPtr pawn, world, driver, connection, stock_type;
        bool host{};
        const items::StackDisplay* display{};
        std::string epoch, id;
        Position source{};
        Position rotation{}, scale{1, 1, 1};
        double visual_z{};
        int stack_count{};
        Clock::time_point expiry{};
        std::int64_t intent{}, last_press{};
        Clock::time_point intent_expiry{}, preview_until{};
        std::int64_t preview_press{};
        bool visible{}, claimable{true}, simulating{}, preview_fall{};
        std::optional<Motion> motion;
    };

    struct Projection
    {
        Actor actor;
        Lease lease;
    };

    std::array<Projection, 65> projections;
    std::size_t active_projection_slots{};

    std::int64_t sequence{};
    std::array callbacks{Hooks::ERROR_ID, Hooks::ERROR_ID, Hooks::ERROR_ID};
    std::atomic<bool> installed{}, retire_requested{};
    Clock::time_point next_check{};

    template <class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <class T> T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, int size, bool output = false)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            (output || !property->HasAnyPropertyFlags(U::CPF_ReturnParm)) &&
            (output || !property->HasAnyPropertyFlags(U::CPF_OutParm) || property->HasAnyPropertyFlags(U::CPF_ConstParm)) &&
            property->GetOffset_Internal() == offset && property->GetSize() == size ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(owner, name), owner);
    }

    bool object_empty(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property && !read(property, owner);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool byte(U::UObject* owner, const U::TCHAR* name, int expected)
    {
        auto* property = field<U::FByteProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool mutable_actor(U::UObject* actor)
    {
        return live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool ready() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    bool id_valid(std::string_view id)
    {
        return id.size() == 32 && std::ranges::all_of(id, [](char character) {
            return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
        });
    }

    bool coordinate(double axis) { return std::isfinite(axis) && std::abs(axis) <= 1e7; }

    bool same_scene(const Lease& lease)
    {
        auto* pawn = lease.pawn.Get();
        auto* world = lease.world.Get();
        auto* driver = lease.driver.Get();
        auto* connection = lease.connection.Get();
        auto* controller = object(pawn, STR("Controller"));
        auto* current_connection = object(driver, STR("ServerConnection"));
        const bool same_connection = lease.host ? !current_connection
            : live(connection) && current_connection == connection && object(connection, STR("Driver")) == driver;
        return mutable_actor(pawn) && live(world) && live(driver) && live(controller) && same_connection &&
            object(controller, STR("Pawn")) == pawn && static_cast<U::AActor*>(pawn)->GetWorld() == world &&
            object(world, STR("NetDriver")) == driver && object(driver, STR("World")) == world;
    }

    bool scope_current(const Lease& lease)
    {
        auto* pawn = lease.pawn.Get();
        return Clock::now() < lease.expiry && same_scene(lease) &&
            input::current_local_pawn(reinterpret_cast<std::uintptr_t>(pawn)) == pawn;
    }

    void clear(Projection& entry)
    {
        auto& [projection, lease] = entry;
        lease = {};
        auto* actor = static_cast<U::AActor*>(projection.object.Get());
        if (mutable_actor(actor))
        {
            actor->SetActorEnableCollision(false);
            actor->SetActorHiddenInGame(true);
            actor->K2_DestroyActor();
        }
        if (!mutable_actor(projection.object.Get())) projection = {};
    }

    bool show(Lease& lease, U::AActor* actor)
    {
        if (!mutable_actor(actor) || !scope_current(lease)) return false;
        auto actual = actor->K2_GetActorLocation();
        const auto offset = std::hypot(actual.GetX() - lease.source[0], actual.GetY() - lease.source[1],
                                       actual.GetZ() - lease.visual_z);
        if (!std::isfinite(offset) || offset > 5) return false;
        actor->SetActorEnableCollision(lease.claimable);
        actor->SetActorHiddenInGame(false);
        if (actor->GetActorEnableCollision() != lease.claimable || !boolean(actor, STR("bHidden"), false)) return false;
        lease.visible = true;
        return true;
    }

    void restore_pickup_preview(Projection& entry)
    {
        auto& [projection, lease] = entry;
        if (!std::exchange(lease.preview_press, 0) || !lease.visible) return;
        auto* actor = static_cast<U::AActor*>(projection.object.Get());
        if (mutable_actor(actor)) actor->SetActorHiddenInGame(false);
    }

    void conceal(Projection& entry)
    {
        auto& [projection, lease] = entry;
        if (!lease.visible) return;
        if (lease.simulating)
        {
            clear(entry);
            return;
        }
        lease.visible = false;
        lease.intent = 0;
        lease.preview_press = 0;
        lease.motion.reset();
        auto* actor = static_cast<U::AActor*>(projection.object.Get());
        if (!mutable_actor(actor)) return;
        actor->SetActorEnableCollision(false);
        actor->SetActorHiddenInGame(true);
        if (actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), true)) clear(entry);
    }

    bool update_pose(U::AActor* actor, const Position& position, const Position& rotation);

    void tick()
    {
        if (!ready()) return;
        if (retire_requested.exchange(false, std::memory_order_acquire))
        {
            for (auto& entry : std::span{projections}.first(active_projection_slots)) clear(entry);
        }
        const auto now = Clock::now();
        for (auto& [projection, lease] : std::span{projections}.first(active_projection_slots))
        {
            if (!lease.motion) continue;
            auto* actor = static_cast<U::AActor*>(projection.object.Get());
            if (!mutable_actor(actor) || !scope_current(lease))
            {
                lease.motion.reset();
                continue;
            }
            const auto& previous = *lease.motion;
            const auto progress = std::clamp(std::chrono::duration<double>(now - previous.start) / pose_blend, 0.0, 1.0);
            Position position{};
            Position rotation{};
            for (std::size_t axis = 0; axis < position.size(); ++axis)
            {
                position[axis] = std::lerp(previous.position[axis], lease.source[axis], progress);
                rotation[axis] = previous.rotation[axis] +
                    std::remainder(lease.rotation[axis] - previous.rotation[axis], 360.0) * progress;
            }
            if (!update_pose(actor, position, rotation) || progress == 1.0) lease.motion.reset();
        }
        if (now < next_check) return;
        next_check = now + std::chrono::milliseconds(100);
        for (auto& entry : std::span{projections}.first(active_projection_slots))
        {
            if (!entry.actor.object.Get()) continue;
            if (!same_scene(entry.lease) || now >= entry.lease.expiry + hidden_retention) clear(entry);
            else if (!scope_current(entry.lease)) conceal(entry);
            else if (entry.lease.preview_press && now >= entry.lease.preview_until) restore_pickup_preview(entry);
        }
        while (active_projection_slots && !projections[active_projection_slots - 1].actor.object.Get())
            --active_projection_slots;
    }

    bool near_source(const Lease& lease, U::UObject* pawn)
    {
        auto location = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        const auto distance = std::hypot(location.GetX() - lease.source[0], location.GetY() - lease.source[1],
                                        location.GetZ() - lease.source[2]);
        return distance <= range;
    }

    Projection* projection_for(U::UObject* actor)
    {
        if (!actor || !ready()) return nullptr;
        const auto active = std::span{projections}.first(active_projection_slots);
        const auto entry = std::ranges::find_if(active, [actor](const Projection& projection) {
            return projection.actor.object.Get() == actor;
        });
        return entry == active.end() ? nullptr : &*entry;
    }

    void enqueue_pickup(Lease& lease)
    {
        lease.intent = lease.last_press = ++sequence;
        lease.intent_expiry = Clock::now() + heartbeat;
    }

    bool inert(U::UObject* actor)
    {
        const auto zero = [actor](const U::TCHAR* name) {
            auto* property = field<U::FIntProperty>(actor, name);
            return property && property->GetPropertyValueInContainer(actor) == 0;
        };
        return zero(STR("Ammo")) && zero(STR("Value")) && boolean(actor, STR("Stacks"), false) &&
            boolean(actor, STR("Physics"), false) && boolean(actor, STR("bReplicates"), false) &&
            byte(actor, STR("Item_RespawnMode"), 0) && object_empty(actor, STR("As Game Instance FP")) &&
            object_empty(actor, STR("Player_Ref")) && object_empty(actor, STR("Owner")) && object_empty(actor, STR("Instigator"));
    }

    bool neutralize(U::UObject* actor)
    {
        auto* ammo = field<U::FIntProperty>(actor, STR("Ammo"));
        auto* value = field<U::FIntProperty>(actor, STR("Value"));
        auto* stacks = field<U::FBoolProperty>(actor, STR("Stacks"));
        auto* physics = field<U::FBoolProperty>(actor, STR("Physics"));
        auto* mode = field<U::FByteProperty>(actor, STR("Item_RespawnMode"));
        if (!ammo || !value || !stacks || !physics || !mode) return false;
        ammo->SetPropertyValue(ammo->ContainerPtrToValuePtr<void>(actor), 0);
        value->SetPropertyValue(value->ContainerPtrToValuePtr<void>(actor), 0);
        stacks->SetPropertyValueInContainer(actor, false);
        physics->SetPropertyValueInContainer(actor, false);
        mode->SetPropertyValue(mode->ContainerPtrToValuePtr<void>(actor), 0);
        return inert(actor);
    }

    bool stock_schema(Projection& entry, U::UClass* type, U::UClass* item_base)
    {
        auto& [projection, lease] = entry;
        const bool stock_loot = lease.stock_type.Get() != nullptr;
        const bool food = lease.display && lease.display->kind == items::StackKind::food;
        const bool ammo = lease.display && !food;
        auto* base = stock_loot ? item_base : find<U::UClass>(food ? consumable_base_path : ammo ? ammo_base_path : weapon_base_path);
        auto* intermediate = (ammo || stock_loot) ? nullptr : find<U::UClass>(food ? health_item_base_path : equipment_base_path);
        if (!type || !base || !item_base || type == item_base || !type->IsChildOf(item_base)) return false;
        if (!stock_loot && ((!ammo && !intermediate) ||
            type->GetSuperStruct() != (food ? intermediate : base) ||
            base->GetSuperStruct() != (lease.display ? item_base : intermediate) ||
            (intermediate && intermediate->GetSuperStruct() != (food ? base : item_base))))
            return false;
        auto* interaction = stock_loot ? type->GetFunctionByNameInChain(U::FName(STR("SendInteractionToActor")))
                                       : find<U::UFunction>(interaction_path);
        auto* interaction_owner = interaction ? U::Cast<U::UClass>(interaction->GetOuterPrivate()) : nullptr;
        auto* begin = stock_loot ? type->GetFunctionByNameInChain(U::FName(STR("ReceiveBeginPlay")))
            : find<U::UFunction>(food ? consumable_begin_path : ammo ? ammo_begin_path : weapon_begin_path);
        auto* converter = find<U::UFunction>(convert_path);
        auto* add = find<U::UFunction>(add_path);
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* scene_type = find<U::UClass>(STR("/Script/Engine.SceneComponent"));
        auto* player = parameter<U::FObjectProperty>(interaction, STR("Player"), 0, 8);
        auto* hit = parameter<U::FObjectProperty>(interaction, STR("HitComponent"), 8, 8);
        auto* convert_item = parameter<U::FObjectProperty>(converter, STR("ItemActor"), 0, 8);
        auto* add_item = parameter<U::FObjectProperty>(add, STR("Item"), 0, 8);
        if (!player || !hit || (!stock_loot && !begin) || !convert_item || !add_item || !pawn_type || !scene_type ||
            !interaction_owner || !interaction_owner->IsChildOf(item_base) || !type->IsChildOf(interaction_owner) ||
            (!stock_loot && interaction_owner != item_base) || interaction->GetNumParms() != 2 ||
            interaction->GetParmsSize() != 16 || interaction->GetReturnProperty() ||
            player->GetPropertyClass().Get() != pawn_type || hit->GetPropertyClass().Get() != scene_type ||
            (begin && ((!stock_loot && begin->GetOuterPrivate() != base) || begin->GetNumParms() ||
                       begin->GetParmsSize() || type->GetFunctionByNameInChain(begin->GetFName()) != begin)) ||
            type->GetFunctionByNameInChain(interaction->GetFName()) != interaction ||
            convert_item->GetPropertyClass().Get() != item_base || add_item->GetPropertyClass().Get() != item_base)
            return false;
        for (auto* function : {interaction, begin, converter, add})
            if (function && function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return false;
        projection.display_type = type;
        projection.item_base = item_base;
        projection.interaction = interaction;
        projection.begin = begin;
        projection.converter = converter;
        projection.add_inventory = add;
        return true;
    }

    void intercept(Projection& entry, Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        auto& [projection, lease] = entry;
        auto* actor = projection.object.Get();
        if (!actor || !ready() || !receiver || !function || call.OriginalFunctionCallPrevented()) return;
        if (receiver != actor)
        {
            // Blueprint conversion can invent a GUID even when the display actor has no item row.
            if (function == projection.converter.Get() &&
                read(parameter<U::FObjectProperty>(function, STR("ItemActor"), 0, 8), args) == actor)
            {
                // The exact stock floor query may make a display row. The co-op floor handler
                // still owns every claim for this actor.
                auto* caller = frame ? frame->PreviousFrame() : nullptr;
                if (!caller || caller->Node() != find<U::UFunction>(floor_query_path) ||
                    object(caller->Object(), STR("PlayerFP")) != lease.pawn.Get() ||
                    !lease.claimable || !lease.visible || !scope_current(lease) || !inert(actor))
                    call.PreventOriginalFunctionCall();
            }
            else if (function == projection.add_inventory.Get() &&
                     receiver == object(static_cast<U::AActor*>(actor)->GetWorld(), STR("OwningGameInstance")) &&
                     read(parameter<U::FObjectProperty>(function, STR("Item"), 0, 8), args) == actor)
                call.PreventOriginalFunctionCall();
            return;
        }
        auto* owner = U::Cast<U::UClass>(function->GetOuterPrivate());
        auto* item_base = U::Cast<U::UClass>(projection.item_base.Get());
        if (!owner || !item_base || !owner->IsChildOf(item_base) ||
            !actor->GetClassPrivate()->IsChildOf(owner) || function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented()) return;
        if (function == projection.begin.Get()) projection.begin_veto = true;
        if (function != projection.interaction.Get() || !lease.claimable || !lease.visible ||
            !scope_current(lease) || !inert(actor)) return;
        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), args);
        auto* box = read(field<U::FObjectProperty>(function, STR("HitComponent")), args);
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        if (pawn != lease.pawn.Get() || !box || box != projection.box.Get() ||
            (frame && (!caller || caller->Object() != pawn || caller->Node() != find<U::UFunction>(pawn_script_path))) ||
            object(pawn, STR("InteractionTarget_Component")) != box ||
            !boolean(pawn, STR("Holding_Interact"), true) || !near_source(lease, pawn) ||
            !input::local_interaction_in_reach(pawn, actor, box) || sequence >= maximum_sequence ||
            !input::consume_current_press(pawn)) return;
        enqueue_pickup(lease);
    }

    bool object_parameter(U::UFunction* call, const U::TCHAR* name, int offset, U::UClass* type, bool result = false)
    {
        auto* property = parameter<U::FObjectProperty>(call, name, offset, 8, result);
        return type && property && property->HasAnyPropertyFlags(U::CPF_ReturnParm) == result &&
            property->GetPropertyClass().Get() == type;
    }

    bool transform_parameter(U::UFunction* call)
    {
        auto* property = parameter<U::FStructProperty>(call, STR("SpawnTransform"), 0x10, 0x60);
        auto* structure = find<U::UScriptStruct>(STR("/Script/CoreUObject.Transform"));
        return property && structure && property->GetStruct().Get() == structure;
    }

    bool update_pose(U::AActor* actor, const Position& position, const Position& rotation)
    {
        static auto* function = find<U::UFunction>(STR("/Script/Engine.Actor:K2_SetActorLocationAndRotation"));
        static auto* location = parameter<U::FStructProperty>(function, STR("NewLocation"), 0, sizeof(Position));
        static auto* orientation = parameter<U::FStructProperty>(function, STR("NewRotation"), 0x18, sizeof(Position));
        static auto* hit = parameter<U::FStructProperty>(function, STR("SweepHitResult"), 0x38, 0xF8, true);
        static auto* teleport = parameter<U::FBoolProperty>(function, STR("bTeleport"), 0x130, 1);
        static auto* success = parameter<U::FBoolProperty>(function, STR("ReturnValue"), 0x131, 1, true);
        static const bool qualified = location && orientation && hit && teleport && success &&
            location->GetStruct().Get() == find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector")) &&
            orientation->GetStruct().Get() == find<U::UScriptStruct>(STR("/Script/CoreUObject.Rotator")) &&
            hit->GetStruct().Get() == find<U::UScriptStruct>(STR("/Script/Engine.HitResult")) &&
            parameter<U::FBoolProperty>(function, STR("bSweep"), 0x30, 1) &&
            function->GetOuterPrivate() == find<U::UClass>(STR("/Script/Engine.Actor")) &&
            function->GetNumParms() == 6 && function->GetParmsSize() == 0x132 &&
            function->HasAnyFunctionFlags(U::FUNC_Native) && success->HasAnyPropertyFlags(U::CPF_ReturnParm);
        if (!qualified) return false;
        // UE5 uses doubles for both positions and rotators. The UE4SS actor
        // convenience wrapper writes single-precision rotation fields.
        alignas(std::max_align_t) std::array<std::byte, 0x132> parameters{};
        hit->InitializeValue_InContainer(parameters.data());
        std::memcpy(location->ContainerPtrToValuePtr<void>(parameters.data()), position.data(), sizeof(position));
        std::memcpy(orientation->ContainerPtrToValuePtr<void>(parameters.data()), rotation.data(), sizeof(rotation));
        teleport->SetPropertyValueInContainer(parameters.data(), true);
        actor->ProcessEvent(function, parameters.data());
        const bool applied = success->GetPropertyValueInContainer(parameters.data());
        hit->DestroyValue_InContainer(parameters.data());
        if (applied) return true;

        // Unreal may report no movement when a serialized settle pose rounds to the existing transform.
        auto point = actor->K2_GetActorLocation();
        auto facing = actor->K2_GetActorRotation();
        const Position actual_position{point.GetX(), point.GetY(), point.GetZ()};
        const Position actual_rotation{facing.GetPitch(), facing.GetYaw(), facing.GetRoll()};
        for (std::size_t axis = 0; axis < position.size(); ++axis)
        {
            if (!(std::abs(actual_position[axis] - position[axis]) <= .001 &&
                  std::abs(std::remainder(actual_rotation[axis] - rotation[axis], 360.0)) <= .001))
                return false;
        }
        return true;
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

    struct Collision
    {
        U::UFunction* simulate{};
        U::UFunction* overlap{};
        U::UFunction* set_enabled{};
        U::UFunction* all_channels{};
        U::UFunction* set_channel{};
        U::UFunction* enabled{};
        U::UFunction* response{};
        U::UFunction* simulating{};
        U::UFunction* overlaps{};

        template <class T> U::UFunction* native(const U::TCHAR* path, U::UClass* owner, const U::TCHAR* name,
                                               bool returns = false)
        {
            auto* function = find<U::UFunction>(path);
            auto* property = parameter<T>(function, name, 0, 1, returns);
            return property && function->GetOuterPrivate() == owner && function->GetNumParms() == 1 &&
                function->GetParmsSize() == 1 && function->HasAnyFunctionFlags(U::FUNC_Native) &&
                property->HasAnyPropertyFlags(U::CPF_ReturnParm) == returns ? function : nullptr;
        }

        U::UFunction* channel_call(const U::TCHAR* path, U::UClass* owner, const U::TCHAR* response_name,
                                   bool returns)
        {
            auto* function = find<U::UFunction>(path);
            auto* channel = parameter<U::FByteProperty>(function, STR("Channel"), 0, 1);
            auto* response = parameter<U::FByteProperty>(function, response_name, 1, 1, returns);
            return channel && response && function->GetOuterPrivate() == owner && function->GetNumParms() == 2 &&
                function->GetParmsSize() == 2 && function->HasAnyFunctionFlags(U::FUNC_Native) &&
                response->HasAnyPropertyFlags(U::CPF_ReturnParm) == returns ? function : nullptr;
        }

        bool bind()
        {
            auto* primitive = find<U::UClass>(STR("/Script/Engine.PrimitiveComponent"));
            auto* scene = find<U::UClass>(STR("/Script/Engine.SceneComponent"));
            if (!primitive || !scene) return false;
            simulate = native<U::FBoolProperty>(STR("/Script/Engine.PrimitiveComponent:SetSimulatePhysics"), primitive, STR("bSimulate"));
            overlap = native<U::FBoolProperty>(STR("/Script/Engine.PrimitiveComponent:SetGenerateOverlapEvents"), primitive,
                                                STR("bInGenerateOverlapEvents"));
            set_enabled = native<U::FByteProperty>(STR("/Script/Engine.PrimitiveComponent:SetCollisionEnabled"), primitive, STR("NewType"));
            all_channels = native<U::FByteProperty>(STR("/Script/Engine.PrimitiveComponent:SetCollisionResponseToAllChannels"),
                                                    primitive, STR("NewResponse"));
            enabled = native<U::FByteProperty>(STR("/Script/Engine.PrimitiveComponent:GetCollisionEnabled"), primitive, STR("ReturnValue"), true);
            simulating = native<U::FBoolProperty>(STR("/Script/Engine.SceneComponent:IsAnySimulatingPhysics"), scene, STR("ReturnValue"), true);
            overlaps = native<U::FBoolProperty>(STR("/Script/Engine.PrimitiveComponent:GetGenerateOverlapEvents"), primitive,
                                                STR("ReturnValue"), true);
            set_channel = channel_call(STR("/Script/Engine.PrimitiveComponent:SetCollisionResponseToChannel"), primitive,
                                       STR("NewResponse"), false);
            response = channel_call(STR("/Script/Engine.PrimitiveComponent:GetCollisionResponseToChannel"), primitive,
                                    STR("ReturnValue"), true);
            return simulate && overlap && set_enabled && all_channels && enabled && simulating && overlaps && set_channel && response;
        }

        static int byte_call(U::UObject* component, U::UFunction* function, std::uint8_t argument)
        {
            component->ProcessEvent(function, &argument);
            return argument;
        }

        static int channel(U::UObject* component, U::UFunction* function, std::uint8_t number, std::uint8_t effect)
        {
            std::array arguments{number, effect};
            component->ProcessEvent(function, arguments.data());
            return arguments[1];
        }

        bool world_body(U::UObject* box, bool enabled_physics, bool static_only = false) const
        {
            constexpr std::uint8_t world_static = 0, world_dynamic = 1, interactable = 16, ignore = 0, block = 2;
            constexpr std::uint8_t query_only = 1, query_and_physics = 3;
            const auto mode = enabled_physics ? query_and_physics : query_only;
            const auto effect = enabled_physics ? block : ignore;
            const auto dynamic_effect = static_only ? ignore : effect;
            const auto interactable_effect = enabled_physics ? ignore : block;
            if (!enabled_physics) byte_call(box, simulate, 0);
            channel(box, set_channel, world_static, effect);
            channel(box, set_channel, world_dynamic, dynamic_effect);
            if (static_only) channel(box, set_channel, interactable, interactable_effect);
            byte_call(box, set_enabled, mode);
            if (enabled_physics) byte_call(box, simulate, 1);
            return byte_call(box, enabled, 0xff) == mode &&
                (byte_call(box, simulating, 0) != 0) == enabled_physics &&
                channel(box, response, world_static, 0xff) == effect &&
                channel(box, response, world_dynamic, 0xff) == dynamic_effect &&
                (!static_only || channel(box, response, interactable, 0xff) == interactable_effect);
        }

        const char* configure(U::AActor* actor, U::UObject* box, std::span<U::UObject* const> components, bool stock_ammo)
        {
            // Cooked Player_FP.LookTrace uses TraceTypeQuery index 4 for InteractionTarget_Component.
            // DefaultEngine's Interactable profile maps that trace to ECC_GameTraceChannel3.
            constexpr std::uint8_t visibility = 3;
            constexpr std::uint8_t interactable = 16;
            constexpr std::uint8_t block = 2;
            if (stock_ammo && (channel(box, response, interactable, 0xff) != block ||
                channel(box, response, visibility, 0xff) != 0))
                return "stock_drop_projection_stock_box_profile_unverified";
            for (auto* component : components)
            {
                byte_call(component, simulate, 0);
                byte_call(component, overlap, 0);
                byte_call(component, all_channels, 0);
                byte_call(component, set_enabled, 0);
            }
            channel(box, set_channel, interactable, block);
            byte_call(box, set_enabled, 1); // QueryOnly.
            actor->SetActorEnableCollision(true);
            if (!stock_ammo)
            {
                // A disabled owner masks the effective mode seen by SetCollisionEnabled.
                // The pistol has no active overlap, simulation, or mesh responses at this point.
                for (auto* component : components)
                    if (component != box) byte_call(component, set_enabled, 0);
            }
            for (auto* component : components)
            {
                const auto expected_mode = component == box ? 1 : 0;
                const auto actual_mode = byte_call(component, enabled, 0xff);
                if (actual_mode != expected_mode)
                {
                    static thread_local std::string detail;
                    detail = "stock_drop_projection_collision_mode_unverified:" + RC::to_utf8_string(component->GetName())
                        + ":expected=" + std::to_string(expected_mode) + ":actual=" + std::to_string(actual_mode);
                    return detail.c_str();
                }
                if (byte_call(component, simulating, 0) || byte_call(component, overlaps, 0))
                    return "stock_drop_projection_physics_or_overlap_unverified";
                // A disabled pistol mesh may report its stock body responses without a query shape.
                if (!stock_ammo && component != box) continue;
                for (std::uint8_t index = 0; index < 32; ++index)
                    if (channel(component, response, index, 0xff) != (component == box && index == interactable ? block : 0))
                        return "stock_drop_projection_collision_channel_unverified";
            }
            return nullptr;
        }
    };

    bool owned_component(U::AActor* actor, U::UObject* component, const U::UClass* type)
    {
        return type && live(component) && component->GetOuterPrivate() == actor && component->GetClassPrivate() == type;
    }

    const char* item_components(U::AActor* actor, U::UObject* box, Collision& collision, bool stock_loot)
    {
        auto* mesh_type = find<U::UClass>(STR("/Script/Engine.StaticMeshComponent"));
        auto* skeletal_type = find<U::UClass>(STR("/Script/Engine.SkeletalMeshComponent"));
        auto* primitive = find<U::UClass>(STR("/Script/Engine.PrimitiveComponent"));
        auto* component_type = find<U::UClass>(STR("/Script/Engine.ActorComponent"));
        if (!primitive || !component_type || !mesh_type || !skeletal_type)
            return "stock_drop_projection_component_types_unavailable";
        auto* tick_on = collision.native<U::FBoolProperty>(STR("/Script/Engine.ActorComponent:SetComponentTickEnabled"),
                                                          component_type, STR("bEnabled"));
        auto* ticking = collision.native<U::FBoolProperty>(STR("/Script/Engine.ActorComponent:IsComponentTickEnabled"),
                                                          component_type, STR("ReturnValue"), true);
        auto* primary_tick = field<U::FStructProperty>(component_type, STR("PrimaryComponentTick"));
        auto* can_tick = primary_tick ? field<U::FBoolProperty>(primary_tick->GetStruct().Get(), STR("bCanEverTick")) : nullptr;
        if (!tick_on || !ticking || !can_tick) return "stock_drop_projection_tick_schema_unavailable";
        constexpr std::array mesh_fields{STR("Mesh_Frame"), STR("Mesh_Mag"), STR("Mesh_Slide")};
        constexpr std::array mesh_assets{
            STR("/Game/Classes/Weapons/Wep_Assets/Wep_Models/LightPistol_Frame.LightPistol_Frame"),
            STR("/Game/Classes/Weapons/Wep_Assets/Wep_Models/LightPistol_Mag.LightPistol_Mag"),
            STR("/Game/Classes/Weapons/Wep_Assets/Wep_Models/LightPistol_Slide.LightPistol_Slide"),
        };
        std::array<U::UObject*, 5> required{box};
        if (!stock_loot)
        {
            auto* skeleton = object(actor, STR("Wep_Skeli"));
            if (!owned_component(actor, skeleton, skeletal_type)) return "stock_drop_projection_pistol_skeleton_unverified";
            required[1] = skeleton;
            for (std::size_t index = 0; index < mesh_fields.size(); ++index)
            {
                auto* mesh = object(actor, mesh_fields[index]);
                auto* asset = find<U::UObject>(mesh_assets[index]);
                if (!owned_component(actor, mesh, mesh_type) || !asset || object(mesh, STR("StaticMesh")) != asset)
                    return "stock_drop_projection_pistol_stock_mesh_unverified";
                required[index + 2] = mesh;
            }
        }
        const auto owned_primitives = actor->K2_GetComponentsByClass(primitive);
        const auto owned_components = actor->K2_GetComponentsByClass(component_type);
        if (owned_primitives.Num() < 2 ||
            owned_primitives.Num() > static_cast<int>(component_limit) ||
            owned_components.Num() < owned_primitives.Num() || owned_components.Num() > static_cast<int>(component_limit))
            return "stock_drop_projection_component_census_invalid";
        std::array<U::UObject*, component_limit> components{};
        for (int index = 0; index < owned_primitives.Num(); ++index)
        {
            auto* component = owned_primitives[index];
            const auto prior = std::span{components}.first(index);
            if (!live(component) || component->GetOuterPrivate() != actor ||
                std::ranges::find(prior, component) != prior.end())
                return "stock_drop_projection_primitive_ownership_unverified";
            components[index] = component;
        }
        const auto live_primitives = std::span{components}.first(owned_primitives.Num());
        if (!std::ranges::all_of(required, [&](auto* component) {
            return !component || std::ranges::find(live_primitives, component) != live_primitives.end();
        })) return "stock_drop_projection_component_missing_from_census";
        if (stock_loot && !std::ranges::any_of(live_primitives, [&](auto* component) {
            return (component->IsA(mesh_type) && live(object(component, STR("StaticMesh")))) ||
                (component->IsA(skeletal_type) && live(object(component, STR("SkinnedAsset"))));
        })) return "stock_drop_projection_stock_model_unavailable";
        for (int index = 0; index < owned_components.Num(); ++index)
        {
            auto* component = owned_components[index];
            if (!live(component) || component->GetOuterPrivate() != actor)
                return "stock_drop_projection_component_ownership_unverified";
            if (!can_tick->GetPropertyValueInContainer(primary_tick->ContainerPtrToValuePtr<void>(component))) continue;
            Collision::byte_call(component, tick_on, 0);
            if (Collision::byte_call(component, ticking, 0)) return "stock_drop_projection_component_tick_not_disabled";
        }
        return collision.configure(actor, box, live_primitives, false);
    }

    const char* spawn_projection(Projection& entry)
    {
        auto& [projection, lease] = entry;
        auto* type = U::Cast<U::UClass>(lease.stock_type.Get());
        const bool stock_loot = type != nullptr;
        if (!type) type = find<U::UClass>(lease.display ? lease.display->path : items::pistol);
        auto* defaults = type ? type->GetClassDefaultObject().Get() : nullptr;
        auto* const world = U::Cast<U::UWorld>(lease.world.Get());
        Spawn spawn;
        Collision collision;
        if (!scope_current(lease) || !stock_schema(entry, type, find<U::UClass>(item_path))
            || !defaults || defaults->GetClassPrivate() != type || !boolean(defaults, STR("bReplicates"), false)
            || !spawn.bind() || !collision.bind()) return "stock_drop_projection_schema_unavailable";
        auto* box_type = find<U::UClass>(STR("/Script/Engine.BoxComponent"));
        if (!box_type) return "stock_drop_projection_assets_unavailable";

        DeferredArgs deferred;
        deferred.world = world;
        deferred.type = type;
        const auto& [pitch, yaw, roll] = lease.rotation;
        const auto& [sx, sy, sz] = lease.scale;
        deferred.transform = U::FTransform{U::FRotator(pitch, yaw, roll).Quaternion(),
            U::FVector(lease.source[0], lease.source[1], lease.visual_z), U::FVector(sx, sy, sz)};
        spawn.statics->ProcessEvent(spawn.begin, &deferred);
        auto* actor = deferred.result;
        if (!mutable_actor(actor) || actor->GetClassPrivate() != type || actor->GetWorld() != world ||
            actor->HasAnyFlags(U::RF_WasLoaded)) return "stock_drop_projection_deferred_actor_unavailable";

        projection.object = actor;
        const auto slot = static_cast<std::size_t>(&entry - projections.data());
        active_projection_slots = std::max(active_projection_slots, slot + 1);
        actor->SetFlags(U::RF_Transient);
        actor->SetActorEnableCollision(false);
        actor->SetActorHiddenInGame(true);
        if (!neutralize(actor)) return "stock_drop_projection_default_state_unqualified";

        FinishArgs finish;
        finish.actor = actor;
        finish.transform = deferred.transform;
        spawn.statics->ProcessEvent(spawn.finish, &finish);
        if (finish.result != actor || !mutable_actor(actor) || (projection.begin.Get() && !projection.begin_veto) ||
            !silverfish::native_guest_drop_maps::persistent_actor(actor, world))
            return "stock_drop_projection_stock_begin_veto_unverified";
        auto* box = object(actor, STR("Box"));
        if (!owned_component(actor, box, box_type)) return "stock_drop_projection_components_unverified";
        actor->SetActorTickEnabled(false);
        if (!inert(actor) || actor->IsActorTickEnabled() || !byte(actor, STR("RemoteRole"), 0))
            return "stock_drop_projection_inert_state_unverified";
        if (lease.display)
        {
            const bool ammo = lease.display->kind == items::StackKind::ammo;
            auto* mesh = object(actor, ammo ? STR("SM_Ammo_HevRifle") : STR("DisplayMesh"));
            auto* asset = find<U::UObject>(lease.display->mesh);
            if (!owned_component(actor, mesh, find<U::UClass>(STR("/Script/Engine.StaticMeshComponent"))) ||
                !asset || object(mesh, STR("StaticMesh")) != asset)
                return "stock_drop_projection_components_unverified";
            auto* primitive = find<U::UClass>(STR("/Script/Engine.PrimitiveComponent"));
            if (!primitive) return "stock_drop_projection_component_type_unavailable";
            const auto owned = actor->K2_GetComponentsByClass(primitive);
            if (owned.Num() != 2 || !((owned[0] == box && owned[1] == mesh) || (owned[0] == mesh && owned[1] == box)))
                return "stock_drop_projection_stock_component_census_unverified";
            const std::array components{box, mesh};
            if (const auto* failure = collision.configure(actor, box, components, ammo)) return failure;
        }
        else if (const auto* failure = item_components(actor, box, collision, stock_loot)) return failure;

        if (!show(lease, actor)) return "stock_drop_projection_visible_actor_unverified";
        projection.box = box;
        return nullptr;
    }

    int report(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int sync_projection(const Lua& lua, Projection& entry)
    {
        auto& [projection, lease] = entry;
        const auto arguments = lua.get_stack_size();
        const bool stock_loot = &entry != &projections.front();
        if (!ready() || (stock_loot ? arguments != 15 && arguments != 16 : arguments != 7 && arguments != 8) ||
            !lua.is_integer(1) || !lua.is_string(2) || !lua.is_string(3) ||
            (arguments >= 8 && !lua.is_string(8)))
            return report(lua, false, "stock_drop_projection_sync_arguments");
        for (int index = 4; index <= 7; ++index)
            if (!lua.is_number(index)) return report(lua, false, "stock_drop_projection_position_arguments");
        for (int index = 9; index <= std::min(arguments, 14); ++index)
            if (!lua.is_number(index)) return report(lua, false, "stock_drop_projection_transform_arguments");
        if (stock_loot && !lua.is_integer(15))
            return report(lua, false, "stock_drop_projection_stack_count_invalid");
        if (arguments == 16 && !lua.is_bool(16))
            return report(lua, false, "stock_drop_projection_pickup_mode_invalid");
        const bool claimable = arguments != 16 || lua.get_bool(16);
        const auto address = static_cast<std::uintptr_t>(lua.get_integer());
        const std::string epoch{lua.get_string()}, id{lua.get_string()};
        Position source{};
        for (auto& axis : source) axis = lua.get_number();
        const double visual = lua.get_number();
        const auto class_path = arguments >= 8 ? lua.get_string() : std::string_view{};
        if (arguments >= 8 && !silverfish::content_paths::item_class(class_path))
        {
            clear(entry);
            return report(lua, false, "stock_drop_projection_class_unqualified");
        }
        const auto path = RC::to_wstring(class_path);
        auto* type = stock_loot ? find<U::UClass>(path.c_str()) : nullptr;
        const auto* display = !stock_loot ? (arguments == 7 ? &items::stacks.front() : items::find_stack(path)) : nullptr;
        if (stock_loot ? !type : !display && path != items::pistol)
        {
            clear(entry);
            return report(lua, false, "stock_drop_projection_class_unqualified");
        }
        Position rotation{}, scale{1, 1, 1};
        int stack_count{};
        if (stock_loot)
        {
            for (auto& angle : rotation) angle = lua.get_number();
            for (auto& factor : scale) factor = lua.get_number();
            if (!std::ranges::all_of(rotation, [](double angle) { return std::abs(angle) <= 360; }) ||
                !std::ranges::all_of(scale, [](double factor) { return factor != 0 && std::abs(factor) <= 100; }) ||
                visual != source[2]) return report(lua, false, "stock_drop_projection_transform_unqualified");
            const auto count = lua.get_integer();
            if (count < 0 || count > std::numeric_limits<int>::max())
                return report(lua, false, "stock_drop_projection_stack_count_invalid");
            stack_count = static_cast<int>(count);
        }
        auto* pawn = input::current_local_pawn(address);
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = object(driver, STR("ServerConnection"));
        if (!pawn || !live(world) || !live(driver) ||
            !silverfish::native_guest_drop_maps::stock_map(world, stock_loot) ||
            (connection && !live(connection)) ||
            !id_valid(epoch) || !id_valid(id) || !std::ranges::all_of(source, coordinate) ||
            !coordinate(visual) || visual > source[2] + 18 || visual < source[2] - 145)
        {
            clear(entry);
            return report(lua, false, "stock_drop_projection_scope_unqualified");
        }

        auto* existing = static_cast<U::AActor*>(projection.object.Get());
        if (Clock::now() < lease.expiry + hidden_retention && lease.epoch == epoch && lease.id == id &&
            lease.pawn.Get() == pawn && lease.world.Get() == world && lease.driver.Get() == driver &&
            lease.host == (connection == nullptr) && lease.connection.Get() == connection && lease.display == display &&
            lease.stock_type.Get() == type && lease.scale == scale && mutable_actor(existing) &&
            (stock_loot || (lease.rotation == rotation && lease.source == source && lease.visual_z == visual)) &&
            existing->GetClassPrivate() == projection.display_type.Get() && live(projection.box.Get()) &&
            object(existing, STR("Box")) == projection.box.Get())
        {
            if (lease.simulating)
            {
                // Keep the current Chaos pose while the ledger still carries the last settled transform.
                lease.expiry = Clock::now() + heartbeat;
                lease.stack_count = stack_count;
                lease.claimable = claimable;
                return report(lua, true, "projected");
            }
            if (lease.source != source || lease.rotation != rotation || lease.visual_z != visual ||
                (claimable && !lease.claimable))
            {
                if (!claimable && !lease.claimable && lease.visible)
                {
                    auto point = existing->K2_GetActorLocation();
                    auto facing = existing->K2_GetActorRotation();
                    lease.motion = Lease::Motion{{point.GetX(), point.GetY(), point.GetZ()},
                        {facing.GetPitch(), facing.GetYaw(), facing.GetRoll()}, Clock::now()};
                }
                else
                {
                    lease.motion.reset();
                    if (!update_pose(existing, {source[0], source[1], visual}, rotation))
                        return report(lua, false, "stock_drop_projection_pose_change_unverified");
                }
                lease.source = source;
                lease.rotation = rotation;
                lease.visual_z = visual;
            }
            lease.expiry = Clock::now() + heartbeat;
            lease.stack_count = stack_count;
            const bool changed_mode = lease.claimable != claimable;
            lease.claimable = claimable;
            if (changed_mode)
            {
                lease.intent = 0;
                restore_pickup_preview(entry);
            }
            if ((!changed_mode && lease.visible) || (inert(existing) && show(lease, existing)))
                return report(lua, true, "projected");
            clear(entry);
            return report(lua, false, "stock_drop_projection_reappear_unverified");
        }
        clear(entry);
        if (mutable_actor(projection.object.Get())) return report(lua, false, "stock_drop_projection_retirement_pending");
        lease.pawn = pawn;
        lease.world = world;
        lease.driver = driver;
        lease.connection = connection;
        lease.host = connection == nullptr;
        lease.display = display;
        lease.stock_type = type;
        lease.rotation = rotation;
        lease.scale = scale;
        lease.epoch = epoch;
        lease.id = id;
        lease.source = source;
        lease.visual_z = visual;
        lease.stack_count = stack_count;
        lease.claimable = claimable;
        lease.expiry = Clock::now() + heartbeat;
        if (const auto* failure = spawn_projection(entry))
        {
            clear(entry);
            return report(lua, false, failure);
        }
        return report(lua, true, "projected");
    }

    Projection* requested_projection(const Lua& lua, int argument)
    {
        if (lua.get_stack_size() == 0) return &projections.front();
        if (!lua.is_integer(argument)) return nullptr;
        const auto slot = lua.get_integer(argument);
        return slot >= 0 && slot < static_cast<std::int64_t>(projections.size()) ? &projections[slot] : nullptr;
    }

    int sync(const Lua& lua)
    {
        return sync_projection(lua, projections.front());
    }

    int sync_world_item(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (arguments != 16 && arguments != 17) return report(lua, false, "stock_drop_projection_sync_arguments");
        auto* entry = requested_projection(lua, arguments);
        if (!entry || entry == &projections.front()) return report(lua, false, "stock_drop_projection_slot_unqualified");
        return sync_projection(lua, *entry);
    }

    int set_world_item_physics(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!ready() || (arguments != 4 && arguments != 5) || !lua.is_string(2) || !lua.is_string(3) ||
            !lua.is_bool(4) || (arguments == 5 && !lua.is_bool(5)))
            return report(lua, false, "stock_world_loot_physics_arguments");
        auto* entry = requested_projection(lua, 1);
        const std::string epoch{lua.get_string()}, id{lua.get_string()};
        const bool simulate = lua.get_bool();
        const bool preview = arguments == 5 && lua.get_bool();
        if (!entry || entry == &projections.front())
            return report(lua, false, "stock_world_loot_physics_slot_unqualified");
        auto& [projection, lease] = *entry;
        auto* actor = static_cast<U::AActor*>(projection.object.Get());
        auto* box = projection.box.Get();
        if (lease.epoch != epoch || lease.id != id || !lease.visible || !scope_current(lease) ||
            !mutable_actor(actor) || !live(box) || object(actor, STR("RootComponent")) != box ||
            object(actor, STR("Box")) != box || (preview && (!simulate || lease.host || lease.claimable)) ||
            (simulate && !lease.simulating && ((!lease.claimable && !preview) || !inert(actor))))
            return report(lua, false, "stock_world_loot_physics_scope_unqualified");
        if (lease.simulating == simulate) return report(lua, true, "stock_world_loot_physics_unchanged");

        Collision collision;
        auto* physics = field<U::FBoolProperty>(actor, STR("Physics"));
        if (!physics || !collision.bind()) return report(lua, false, "stock_world_loot_physics_schema");
        if (preview)
        {
            actor->SetActorEnableCollision(true);
            if (!actor->GetActorEnableCollision()) return report(lua, false, "stock_world_loot_physics_collision_unavailable");
        }
        if (!collision.world_body(box, simulate, preview || lease.preview_fall))
        {
            if (preview) actor->SetActorEnableCollision(false);
            if (!collision.world_body(box, lease.simulating, preview || lease.preview_fall)) clear(*entry);
            return report(lua, false, "stock_world_loot_physics_body_unverified");
        }
        physics->SetPropertyValueInContainer(actor, simulate);
        restore_pickup_preview(*entry);
        if (!simulate && lease.preview_fall) actor->SetActorEnableCollision(lease.claimable);
        lease.simulating = simulate;
        lease.preview_fall = preview;
        lease.motion.reset();
        lease.intent = 0;
        if (!simulate)
        {
            auto point = actor->K2_GetActorLocation();
            auto rotation = actor->K2_GetActorRotation();
            lease.source = {point.GetX(), point.GetY(), point.GetZ()};
            lease.rotation = {rotation.GetPitch(), rotation.GetYaw(), rotation.GetRoll()};
            lease.visual_z = point.GetZ();
        }
        return report(lua, true, simulate ? "stock_world_loot_physics_active" : "stock_world_loot_physics_settled");
    }

    int read_projection(const Lua& lua)
    {
        auto* entry = requested_projection(lua, 1);
        if (!ready() || !entry || lua.get_stack_size() > 1) return report(lua, false, "stock_drop_projection_read_arguments");
        auto& [projection, lease] = *entry;
        if (!lease.visible || !scope_current(lease) || !mutable_actor(projection.object.Get()))
        {
            if (lease.visible) conceal(*entry);
            return report(lua, false, "stock_drop_projection_inactive");
        }
        lua.set_bool(true);
        lua.set_string(lease.epoch);
        lua.set_string(lease.id);
        lua.set_string(RC::to_utf8_string(projection.object.Get()->GetFullName()));
        lua.set_integer(lease.last_press);
        return 5;
    }

    int take_press(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = static_cast<std::uintptr_t>(lua.get_integer());
        auto* pawn = input::current_local_pawn(address);
        if (!pawn) return 0;
        for (auto& entry : std::span{projections}.first(active_projection_slots))
        {
            auto& lease = entry.lease;
            auto* actor = static_cast<U::AActor*>(entry.actor.object.Get());
            if (!lease.intent || Clock::now() >= lease.intent_expiry ||
                !lease.visible || pawn != lease.pawn.Get() || !scope_current(lease) || !mutable_actor(actor)) continue;
            const auto press = std::exchange(lease.intent, 0);
            if (&entry != &projections.front() && lease.claimable && !lease.simulating &&
                !lease.preview_press && boolean(actor, STR("bHidden"), false))
            {
                // Keep the claim lease and query collision live while the world item is hidden locally.
                actor->SetActorHiddenInGame(true);
                lease.preview_press = press;
                lease.preview_until = Clock::now() + std::chrono::milliseconds(1500);
            }
            lua.set_string(lease.epoch);
            lua.set_string(lease.id);
            lua.set_integer(press);
            return 3;
        }
        return 0;
    }

    int cancel_pickup_preview(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_string(2) ||
            !lua.is_string(3) || !lua.is_integer(4)) return 0;
        auto* pawn = input::current_local_pawn(static_cast<std::uintptr_t>(lua.get_integer()));
        const std::string epoch{lua.get_string()}, id{lua.get_string()};
        const auto press = lua.get_integer();
        if (!pawn || press <= 0 || !id_valid(epoch) || !id_valid(id)) return 0;
        for (auto& entry : std::span{projections}.first(active_projection_slots))
        {
            const auto& lease = entry.lease;
            if (lease.preview_press != press || lease.pawn.Get() != pawn ||
                lease.epoch != epoch || lease.id != id || !scope_current(lease)) continue;
            restore_pickup_preview(entry);
            break;
        }
        return 0;
    }

    int clear_lua(const Lua& lua)
    {
        auto* entry = requested_projection(lua, 1);
        if (!ready() || !entry || lua.get_stack_size() > 1) return report(lua, false, "stock_drop_projection_clear_arguments");
        clear(*entry);
        return report(lua, !mutable_actor(entry->actor.object.Get()), "stock_drop_projection_cleared");
    }

    std::optional<Position> scene_vector(U::UObject* component, const U::TCHAR* function_name)
    {
        auto* function = find<U::UFunction>(function_name);
        auto* value = function ? U::CastField<U::FStructProperty>(function->GetReturnProperty()) : nullptr;
        if (!live(component) || !value || value->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector")) ||
            value->GetOffset_Internal() != 0 || function->GetParmsSize() != sizeof(Position) ||
            function->GetNumParms() != 1 || !function->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        Position result{};
        component->ProcessEvent(function, result.data());
        return std::ranges::all_of(result, coordinate) ? std::optional{result} : std::nullopt;
    }

    bool clear_floor_sight(U::UObject* pawn, U::UObject* actor, const Position& eye, const Position& item)
    {
        auto* library = find<U::UObject>(STR("/Script/Engine.Default__KismetSystemLibrary"));
        auto* fn = find<U::UFunction>(STR("/Script/Engine.KismetSystemLibrary:LineTraceSingle"));
        if (!library || !fn || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            !fn->HasAnyFunctionFlags(U::FUNC_Native) || fn->GetNumParms() != 13 || fn->GetParmsSize() > 512)
            return false;
        auto* context = parameter<U::FObjectProperty>(fn, STR("WorldContextObject"), 0, sizeof(void*));
        auto* start = parameter<U::FStructProperty>(fn, STR("Start"), 8, sizeof(Position));
        auto* end = parameter<U::FStructProperty>(fn, STR("End"), 0x20, sizeof(Position));
        auto* vec = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        auto* channel = parameter<U::FByteProperty>(fn, STR("TraceChannel"), 0x38, 1);
        auto* complex = parameter<U::FBoolProperty>(fn, STR("bTraceComplex"), 0x39, 1);
        auto* ignore = field<U::FArrayProperty>(fn, STR("ActorsToIgnore"));
        auto* ignored = ignore ? U::CastField<U::FObjectProperty>(ignore->GetInner()) : nullptr;
        auto* hit = field<U::FStructProperty>(fn, STR("OutHit"));
        auto* self = field<U::FBoolProperty>(fn, STR("bIgnoreSelf"));
        auto* hit_found = U::CastField<U::FBoolProperty>(fn->GetReturnProperty());
        if (!context || !start || !end || !vec || start->GetStruct().Get() != vec || end->GetStruct().Get() != vec ||
            !channel || !complex || !ignored || !hit || hit->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/Engine.HitResult")) ||
            !self || !hit_found) return false;
        struct Parameters
        {
            alignas(16) std::array<std::byte, 512> storage{};
            U::FArrayProperty* ignore;
            U::FStructProperty* hit;
            ~Parameters()
            {
                hit->DestroyValue_InContainer(storage.data());
                ignore->DestroyValue_InContainer(storage.data());
            }
        } args{{}, ignore, hit};
        auto* data = args.storage.data();
        ignore->InitializeValue_InContainer(data);
        hit->InitializeValue_InContainer(data);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(data), pawn);
        std::memcpy(start->ContainerPtrToValuePtr<void>(data), eye.data(), sizeof(eye));
        std::memcpy(end->ContainerPtrToValuePtr<void>(data), item.data(), sizeof(item));
        channel->SetPropertyValueInContainer(data, 1); // Stock Visibility trace
        complex->SetPropertyValueInContainer(data, true);
        self->SetPropertyValueInContainer(data, true);
        U::FScriptArrayHelper ignored_actors(ignore, ignore->ContainerPtrToValuePtr<void>(data));
        ignored->SetObjectPropertyValue(ignored_actors.GetRawPtr(ignored_actors.AddValue()), actor);
        if (auto* held = object(pawn, STR("EquipedItem")); live(held))
            ignored->SetObjectPropertyValue(ignored_actors.GetRawPtr(ignored_actors.AddValue()), held);
        library->ProcessEvent(fn, data);
        return !hit_found->GetPropertyValueInContainer(data);
    }

    bool floor_box_visible(U::UObject* pawn, U::UObject* actor, U::UObject* box, const Position& eye)
    {
        const auto center = scene_vector(box, STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"));
        if (!center) return false;
        if (clear_floor_sight(pawn, actor, eye, *center)) return true;
        const auto extent = scene_vector(box, STR("/Script/Engine.BoxComponent:GetScaledBoxExtent"));
        if (!extent) return false;
        for (const auto& [size, path] : {
            std::pair{(*extent)[2], STR("/Script/Engine.SceneComponent:GetUpVector")},
            std::pair{(*extent)[1], STR("/Script/Engine.SceneComponent:GetRightVector")}})
        {
            if (size <= 0 || size > 100) continue;
            const auto axis = scene_vector(box, path);
            if (!axis) continue;
            for (const auto sign : {-0.6, 0.6})
            {
                auto point = *center;
                for (std::size_t i = 0; i < point.size(); ++i) point[i] += (*axis)[i] * size * sign;
                if (clear_floor_sight(pawn, actor, eye, point)) return true;
            }
        }
        return false;
    }
}

bool silverfish::native_actor_pose::set_location_rotation(U::AActor* actor, const Position& position, const Position& rotation)
{
    return update_pose(actor, position, rotation);
}

namespace silverfish::native_guest_drop_projection
{
    bool owns_actor(U::UObject* actor)
    {
        return projection_for(actor) != nullptr;
    }

    std::vector<U::UObject*> visible_nearby_actors(U::UObject* pawn, std::span<U::UObject* const> shown)
    {
        if (!ready() || !live(pawn) || !active_projection_slots ||
            input::current_local_pawn(reinterpret_cast<std::uintptr_t>(pawn)) != pawn) return {};
        auto* camera = object(pawn, STR("Camera"));
        if (!live(camera) || camera->GetOuterPrivate() != pawn) return {};
        const auto eye = scene_vector(camera, STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"));
        if (!eye) return {};
        std::vector<U::UObject*> visible;
        for (const auto& [projection, lease] : std::span{projections}.first(active_projection_slots))
        {
            auto* actor = projection.object.Get();
            auto* box = projection.box.Get();
            if (!lease.claimable || !lease.visible || lease.pawn.Get() != pawn ||
                std::ranges::find(shown, actor) != shown.end() ||
                !scope_current(lease) || !live(actor) || !inert(actor) ||
                !near_source(lease, pawn) || !input::local_interaction_in_reach(pawn, actor, box)) continue;
            if (floor_box_visible(pawn, actor, box, *eye)) visible.push_back(actor);
        }
        return visible;
    }

    std::optional<int> nearby_stack_count(U::UObject* actor, U::UObject* pawn)
    {
        auto* entry = projection_for(actor);
        if (!entry || !entry->lease.claimable || !entry->lease.stack_count || !entry->lease.visible ||
            entry->lease.pawn.Get() != pawn || !scope_current(entry->lease) || !inert(actor)) return {};
        auto* defaults = actor->GetClassPrivate()->GetClassDefaultObject().Get();
        return boolean(defaults, STR("Stacks"), true) ? std::optional{entry->lease.stack_count} : std::nullopt;
    }

    bool queue_nearby_pickup(U::UObject* actor, U::UObject* pawn)
    {
        auto* entry = projection_for(actor);
        if (!entry) return false;
        auto& [projection, lease] = *entry;
        auto* box = projection.box.Get();
        if (!pawn || pawn != lease.pawn.Get() || !lease.claimable || !lease.visible || !scope_current(lease) ||
            !inert(actor) || !near_source(lease, pawn) || sequence >= maximum_sequence ||
            !input::local_interaction_in_reach(pawn, actor, box)) return false;
        enqueue_pickup(lease);
        return true;
    }

    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = Hooks::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* function, void* args) {
                if (!ready()) return;
                for (auto& entry : std::span{projections}.first(active_projection_slots))
                    intercept(entry, call, actor, function, args, nullptr);
            }, {false, false, owner, STR("Guard one native guest drop projection")});
        callbacks[1] = Hooks::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* actor, U::FFrame& frame, void*) {
                if (!ready()) return;
                for (auto& entry : std::span{projections}.first(active_projection_slots))
                    intercept(entry, call, actor, frame.Node(), frame.Locals(), &frame);
            }, {false, false, owner, STR("Guard Blueprint interaction with one native guest drop projection")});
        callbacks[2] = Hooks::RegisterEngineTickPreCallback(
            [](Iteration&, U::UEngine*, float, bool) { tick(); },
            {false, false, owner, STR("Expire the exact native guest drop projection")});
        if (std::ranges::any_of(callbacks, [](auto callback) { return callback == Hooks::ERROR_ID; }))
        {
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
        installed.store(false, std::memory_order_release);
        for (auto& callback : callbacks)
        {
            if (callback != Hooks::ERROR_ID) Hooks::UnregisterCallback(callback);
            callback = Hooks::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishSyncNativeGuestDropProjection", sync);
        lua.register_function("SilverfishSyncNativeWorldLootItem", sync_world_item);
        lua.register_function("SilverfishSetNativeWorldLootPhysics", set_world_item_physics);
        lua.register_function("SilverfishReadNativeGuestDropProjection", read_projection);
        lua.register_function("SilverfishTakeNativeGuestDropProjectionPress", take_press);
        lua.register_function("SilverfishCancelNativeWorldLootPreview", cancel_pickup_preview);
        lua.register_function("SilverfishClearNativeGuestDropProjection", clear_lua);
    }

    void clear_scope()
    {
        if (!ready())
        {
            retire_requested.store(true, std::memory_order_release);
            return;
        }
        for (auto& entry : std::span{projections}.first(active_projection_slots)) clear(entry);
    }
}
