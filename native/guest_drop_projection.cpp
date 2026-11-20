#include "guest_drop_projection.hpp"
#include "interaction_input.hpp"

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
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hooks = U::Hook;
    namespace input = silverfish::native_interaction_input;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hooks::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Position = std::array<double, 3>;

    constexpr auto map_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C");
    constexpr auto ammo_base_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto mesh_path = STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_Pistol.SM_Ammo_Pistol");
    constexpr auto interaction_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto ammo_begin_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C:ReceiveBeginPlay");
    constexpr auto convert_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_script_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr auto maximum_sequence = (std::int64_t{1} << 53) - 1;
    constexpr auto heartbeat = std::chrono::seconds(2);
    constexpr double range = 250.0;

    struct Actor
    {
        U::FWeakObjectPtr object, box, ammo_type, ammo_base, item_base;
        U::FWeakObjectPtr interaction, ammo_begin, converter, add_inventory;
        bool begin_veto{};
    } projection;

    struct Lease
    {
        U::FWeakObjectPtr pawn, world, driver, connection;
        std::string epoch, id;
        Position source{};
        double visual_z{};
        Clock::time_point expiry{};
        std::int64_t intent{}, last_press{};
        Clock::time_point intent_expiry{};
        bool visible{};
    } lease;

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

    U::UObject* local_pawn(std::uintptr_t address)
    {
        return input::current_guest_pawn(address);
    }

    bool scope_current()
    {
        auto* pawn = lease.pawn.Get();
        auto* world = lease.world.Get();
        auto* driver = lease.driver.Get();
        auto* connection = lease.connection.Get();
        return pawn && local_pawn(reinterpret_cast<std::uintptr_t>(pawn)) == pawn &&
            live(world) && live(driver) && live(connection) && Clock::now() < lease.expiry &&
            static_cast<U::AActor*>(pawn)->GetWorld() == world && object(world, STR("NetDriver")) == driver &&
            object(driver, STR("World")) == world && object(driver, STR("ServerConnection")) == connection &&
            object(connection, STR("Driver")) == driver;
    }

    void clear()
    {
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

    void tick()
    {
        if (!ready()) return;
        if (retire_requested.exchange(false, std::memory_order_acquire))
        {
            clear();
            return;
        }
        if (!projection.object.Get()) return;
        const auto now = Clock::now();
        if (now < next_check) return;
        next_check = now + std::chrono::milliseconds(100);
        if (!scope_current()) clear();
    }

    bool near_source(U::UObject* pawn)
    {
        auto location = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        const auto distance = std::hypot(location.GetX() - lease.source[0], location.GetY() - lease.source[1],
                                        location.GetZ() - lease.source[2]);
        return distance <= range;
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

    bool stock_schema(U::UClass* ammo, U::UClass* ammo_base, U::UClass* item_base)
    {
        if (!ammo || !ammo_base || !item_base || ammo->GetSuperStruct() != ammo_base || ammo_base->GetSuperStruct() != item_base)
            return false;
        auto* interaction = find<U::UFunction>(interaction_path);
        auto* begin = find<U::UFunction>(ammo_begin_path);
        auto* converter = find<U::UFunction>(convert_path);
        auto* add = find<U::UFunction>(add_path);
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* scene_type = find<U::UClass>(STR("/Script/Engine.SceneComponent"));
        auto* player = parameter<U::FObjectProperty>(interaction, STR("Player"), 0, 8);
        auto* hit = parameter<U::FObjectProperty>(interaction, STR("HitComponent"), 8, 8);
        auto* convert_item = parameter<U::FObjectProperty>(converter, STR("ItemActor"), 0, 8);
        auto* add_item = parameter<U::FObjectProperty>(add, STR("Item"), 0, 8);
        if (!player || !hit || !begin || !convert_item || !add_item || !pawn_type || !scene_type ||
            interaction->GetOuterPrivate() != item_base || interaction->GetNumParms() != 2 ||
            interaction->GetParmsSize() != 16 || interaction->GetReturnProperty() ||
            player->GetPropertyClass().Get() != pawn_type || hit->GetPropertyClass().Get() != scene_type ||
            begin->GetOuterPrivate() != ammo_base || begin->GetNumParms() || begin->GetParmsSize() ||
            convert_item->GetPropertyClass().Get() != item_base || add_item->GetPropertyClass().Get() != item_base)
            return false;
        for (auto* function : {interaction, begin, converter, add})
            if (function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return false;
        projection.ammo_type = ammo;
        projection.ammo_base = ammo_base;
        projection.item_base = item_base;
        projection.interaction = interaction;
        projection.ammo_begin = begin;
        projection.converter = converter;
        projection.add_inventory = add;
        return true;
    }

    void intercept(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        auto* actor = projection.object.Get();
        if (!actor || !ready() || !receiver || !function || call.OriginalFunctionCallPrevented()) return;
        if (receiver != actor)
        {
            // Blueprint conversion can invent a GUID even when the display actor has no item row.
            if (function == projection.converter.Get() &&
                read(parameter<U::FObjectProperty>(function, STR("ItemActor"), 0, 8), args) == actor)
                call.PreventOriginalFunctionCall();
            else if (function == projection.add_inventory.Get() &&
                     receiver == object(static_cast<U::AActor*>(actor)->GetWorld(), STR("OwningGameInstance")) &&
                     read(parameter<U::FObjectProperty>(function, STR("Item"), 0, 8), args) == actor)
                call.PreventOriginalFunctionCall();
            return;
        }
        auto* owner = function->GetOuterPrivate();
        if ((owner != projection.ammo_type.Get() && owner != projection.ammo_base.Get() && owner != projection.item_base.Get()) ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented()) return;
        if (function == projection.ammo_begin.Get()) projection.begin_veto = true;
        if (function != projection.interaction.Get() || !lease.visible || !scope_current() || !inert(actor)) return;
        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), args);
        auto* box = read(field<U::FObjectProperty>(function, STR("HitComponent")), args);
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        if (pawn != lease.pawn.Get() || !box || box != projection.box.Get() ||
            (frame && (!caller || caller->Object() != pawn || caller->Node() != find<U::UFunction>(pawn_script_path))) ||
            object(pawn, STR("InteractionTarget_Component")) != box ||
            !boolean(pawn, STR("Holding_Interact"), true) || !near_source(pawn) ||
            !input::guest_interaction_in_reach(pawn, actor, box) || sequence >= maximum_sequence ||
            !input::consume_current_press(pawn)) return;
        lease.intent = lease.last_press = ++sequence;
        lease.intent_expiry = Clock::now() + heartbeat;
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

        bool configure(U::AActor* actor, U::UObject* box, U::UObject* mesh)
        {
            // Cooked Player_FP.LookTrace uses TraceTypeQuery index 4 for InteractionTarget_Component.
            // DefaultEngine's Interactable profile maps that trace to ECC_GameTraceChannel3.
            constexpr std::uint8_t visibility = 3;
            constexpr std::uint8_t interactable = 16;
            constexpr std::uint8_t block = 2;
            if (channel(box, response, interactable, 0xff) != block || channel(box, response, visibility, 0xff) != 0)
                return false;
            for (auto* component : {box, mesh})
            {
                byte_call(component, simulate, 0);
                byte_call(component, overlap, 0);
                byte_call(component, all_channels, 0);
                byte_call(component, set_enabled, 0);
            }
            channel(box, set_channel, interactable, block);
            byte_call(box, set_enabled, 1); // QueryOnly, solely the stock interaction trace.
            actor->SetActorEnableCollision(true);
            if (byte_call(box, enabled, 0xff) != 1 || byte_call(mesh, enabled, 0xff) != 0) return false;
            for (auto* component : {box, mesh})
            {
                if (byte_call(component, simulating, 0) || byte_call(component, overlaps, 0)) return false;
                for (std::uint8_t index = 0; index < 32; ++index)
                    if (channel(component, response, index, 0xff) != (component == box && index == interactable ? block : 0))
                        return false;
            }
            return true;
        }
    };

    const char* spawn_projection()
    {
        auto* ammo = find<U::UClass>(ammo_path);
        auto* const world = lease.world.Get();
        Spawn spawn;
        Collision collision;
        if (!scope_current() || !stock_schema(ammo, find<U::UClass>(ammo_base_path), find<U::UClass>(item_path))
            || !spawn.bind() || !collision.bind()) return "stock_drop_projection_schema_unavailable";
        auto* stock_mesh = find<U::UObject>(mesh_path);
        auto* box_type = find<U::UClass>(STR("/Script/Engine.BoxComponent"));
        auto* mesh_type = find<U::UClass>(STR("/Script/Engine.StaticMeshComponent"));
        if (!stock_mesh || !box_type || !mesh_type) return "stock_drop_projection_assets_unavailable";

        DeferredArgs deferred;
        deferred.world = world;
        deferred.type = ammo;
        deferred.transform = U::FTransform{U::FQuat(0, 0, 0, 1),
            U::FVector(lease.source[0], lease.source[1], lease.visual_z), U::FVector(1, 1, 1)};
        spawn.statics->ProcessEvent(spawn.begin, &deferred);
        auto* actor = deferred.result;
        if (!mutable_actor(actor) || actor->GetClassPrivate() != ammo || actor->GetWorld() != world ||
            actor->HasAnyFlags(U::RF_WasLoaded)) return "stock_drop_projection_deferred_actor_unavailable";

        projection.object = actor;
        actor->SetFlags(U::RF_Transient);
        actor->SetActorEnableCollision(false);
        actor->SetActorHiddenInGame(true);
        if (!neutralize(actor)) return "stock_drop_projection_default_state_unqualified";

        FinishArgs finish;
        finish.actor = actor;
        finish.transform = deferred.transform;
        spawn.statics->ProcessEvent(spawn.finish, &finish);
        if (finish.result != actor || !mutable_actor(actor) || actor->GetWorld() != world || !projection.begin_veto)
            return "stock_drop_projection_stock_begin_veto_unverified";
        auto* box = object(actor, STR("Box"));
        auto* mesh = object(actor, STR("SM_Ammo_HevRifle"));
        if (!live(box) || box->GetOuterPrivate() != actor || box->GetClassPrivate() != box_type ||
            !live(mesh) || mesh->GetOuterPrivate() != actor || mesh->GetClassPrivate() != mesh_type ||
            object(mesh, STR("StaticMesh")) != stock_mesh) return "stock_drop_projection_components_unverified";
        actor->SetActorTickEnabled(false);
        if (!inert(actor) || actor->IsActorTickEnabled() || !byte(actor, STR("RemoteRole"), 0) ||
            !collision.configure(actor, box, mesh)) return "stock_drop_projection_inert_state_unverified";

        actor->SetActorHiddenInGame(false);
        auto actual = actor->K2_GetActorLocation();
        if (!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false) ||
            !scope_current() || std::hypot(actual.GetX() - lease.source[0], actual.GetY() - lease.source[1],
                                           actual.GetZ() - lease.visual_z) > 5)
            return "stock_drop_projection_visible_actor_unverified";
        projection.box = box;
        lease.visible = true;
        return nullptr;
    }

    int report(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    int sync(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 7 || !lua.is_integer(1) || !lua.is_string(2) || !lua.is_string(3))
            return report(lua, false, "stock_drop_projection_sync_arguments");
        for (int index = 4; index <= 7; ++index)
            if (!lua.is_number(index)) return report(lua, false, "stock_drop_projection_position_arguments");
        const auto address = static_cast<std::uintptr_t>(lua.get_integer());
        const std::string epoch{lua.get_string()}, id{lua.get_string()};
        Position source{};
        for (auto& axis : source) axis = lua.get_number();
        const double visual = lua.get_number();
        auto* pawn = local_pawn(address);
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        auto* driver = object(world, STR("NetDriver"));
        auto* connection = object(driver, STR("ServerConnection"));
        if (!pawn || !live(world) || world != find<U::UWorld>(map_path) || !live(driver) || !live(connection) ||
            !id_valid(epoch) || !id_valid(id) || !std::ranges::all_of(source, coordinate) ||
            !coordinate(visual) || visual > source[2] + 18 || visual < source[2] - 145)
        {
            clear();
            return report(lua, false, "stock_drop_projection_scope_unqualified");
        }

        if (lease.visible && scope_current() && lease.epoch == epoch && lease.id == id && lease.pawn.Get() == pawn &&
            lease.world.Get() == world && lease.driver.Get() == driver && lease.connection.Get() == connection &&
            lease.source == source && mutable_actor(projection.object.Get()))
        {
            lease.expiry = Clock::now() + heartbeat;
            return report(lua, true, "projected");
        }
        clear();
        if (mutable_actor(projection.object.Get())) return report(lua, false, "stock_drop_projection_retirement_pending");
        lease.pawn = pawn;
        lease.world = world;
        lease.driver = driver;
        lease.connection = connection;
        lease.epoch = epoch;
        lease.id = id;
        lease.source = source;
        lease.visual_z = visual;
        lease.expiry = Clock::now() + heartbeat;
        if (const auto* failure = spawn_projection())
        {
            clear();
            return report(lua, false, failure);
        }
        return report(lua, true, "projected");
    }

    int read_projection(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 0) return report(lua, false, "stock_drop_projection_read_arguments");
        if (!lease.visible || !scope_current() || !mutable_actor(projection.object.Get()))
        {
            if (lease.visible) clear();
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
        if (!lease.visible || !scope_current() || local_pawn(address) != lease.pawn.Get() ||
            !lease.intent || Clock::now() >= lease.intent_expiry) return 0;
        lease.intent = 0;
        lua.set_string(lease.epoch);
        lua.set_string(lease.id);
        return 2;
    }

    int clear_lua(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 0) return report(lua, false, "stock_drop_projection_clear_arguments");
        clear();
        return report(lua, !mutable_actor(projection.object.Get()), "stock_drop_projection_cleared");
    }
}

namespace silverfish::native_guest_drop_projection
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = Hooks::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* actor, U::UFunction* function, void* args) {
                intercept(call, actor, function, args, nullptr);
            }, {false, false, owner, STR("Guard one native guest drop projection")});
        callbacks[1] = Hooks::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* actor, U::FFrame& frame, void*) {
                intercept(call, actor, frame.Node(), frame.Locals(), &frame);
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
        lua.register_function("SilverfishReadNativeGuestDropProjection", read_projection);
        lua.register_function("SilverfishTakeNativeGuestDropProjectionPress", take_press);
        lua.register_function("SilverfishClearNativeGuestDropProjection", clear_lua);
    }

    void clear_scope()
    {
        if (ready()) clear();
        else retire_requested.store(true, std::memory_order_release);
    }
}
