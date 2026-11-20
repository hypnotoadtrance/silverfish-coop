#include "datasilo_door.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdlib>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P");
    constexpr auto door_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.Door_Metal_Sliding_01_BP_C_1");
    constexpr auto door_type = STR("/Game/Assets/StaticMeshes/Doors/Door_Metal_Sliding_01_BP.Door_Metal_Sliding_01_BP_C");
    constexpr auto base_type = STR("/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C");
    constexpr auto instance_type = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto graph_name = STR("ExecuteUbergraph_BasicDoor_Master");

    struct StockEntry
    {
        const U::TCHAR* wrapper;
        int offset;
    };

    // Only locally initiated door actions use these cooked entry points. Latent completion and replication do not.
    constexpr std::array stock_entries{
        StockEntry{STR("SendInteractionToActor"), 870},
        StockEntry{STR("Send_PowerSignal"), 4857},
        StockEntry{STR("OpenDoor"), 2079},
        StockEntry{STR("PickLock"), 5344},
        StockEntry{STR("PriedOpen_Lock"), 5349},
        StockEntry{STR("UnlockEvent"), 4414},
        StockEntry{STR("Unlock_Sister"), -1},
        StockEntry{STR("Jiggle_Locked"), 5364},
        StockEntry{STR("AI_OverrideAction"), 3253},
        StockEntry{STR("BndEvt__BasicDoor_Master_Box_AIOpen_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature__DelegateSignature"), 3143},
        StockEntry{STR("BndEvt__BasicDoor_Master_Box_AIOpen_Back_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"), 3689},
    };

    std::array<U::FName, stock_entries.size()> wrappers{};
    U::FName graph{};
    std::atomic<bool> installed{};
    std::array<Hook::GlobalCallbackId, 2> hooks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->IsUnreachable() && !value->HasAnyFlags(excluded);
    }

    enum class Seat { unavailable, host, guest };

    Seat seat(U::UObject* door)
    {
        if (!live(door) || door->GetClassPrivate() != find<U::UClass>(door_type) || door->GetPathName() != door_path)
            return Seat::unavailable;
        auto* actor = static_cast<U::AActor*>(door);
        auto* world = actor->GetWorld();
        auto* ending = field<U::FBoolProperty>(door, STR("bActorIsBeingDestroyed"));
        if (!live(world) || world->GetPathName() != world_path || !ending || ending->GetPropertyValueInContainer(door) ||
            actor->GetLevel() != object(world, STR("PersistentLevel"))) return Seat::unavailable;
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        if (!live(instance) || instance->GetClassPrivate() != find<U::UClass>(instance_type) || !live(driver) ||
            object(driver, STR("World")) != world || !field<U::FObjectProperty>(driver, STR("ServerConnection")))
            return Seat::unavailable;
        auto* server = object(driver, STR("ServerConnection"));
        if (server) return live(server) && object(server, STR("Driver")) == driver ? Seat::guest : Seat::unavailable;
        auto* role = field<U::FByteProperty>(door, STR("Role"));
        return role && role->GetPropertyValueInContainer(door) == 3 ? Seat::host : Seat::unavailable;
    }

    bool guarded_graph_entry(U::UFunction* function, const void* args)
    {
        auto* entry = field<U::FIntProperty>(function, STR("EntryPoint"));
        if (!entry || !args || !entry->HasAnyPropertyFlags(U::CPF_Parm) || entry->GetOffset_Internal() != 0 ||
            function->GetNumParms() != 1 || function->GetReturnProperty()) return false;
        const int offset = entry->GetPropertyValueInContainer(args);
        return offset >= 0 && std::ranges::any_of(stock_entries, [offset](const auto& item) { return item.offset == offset; });
    }

    void intercept(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!available() || !receiver || !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        const bool script_graph = name == graph;
        if (!script_graph && std::ranges::find(wrappers, name) == wrappers.end()) return;
        if (function->GetOuterPrivate() != find<U::UClass>(base_type) ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || seat(receiver) != Seat::guest) return;
        if (!script_graph || guarded_graph_entry(function, args)) call.PreventOriginalFunctionCall();
    }
}

namespace silverfish::native_datasilo_door
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        for (std::size_t i = 0; i < stock_entries.size(); ++i)
            wrappers[i] = U::FName{stock_entries[i].wrapper, U::FNAME_Add};
        graph = U::FName{graph_name, U::FNAME_Add};
        hooks[0] = Hook::RegisterProcessEventPreCallback(intercept,
            {false, false, owner, STR("Guard Data Silo upper door event")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* receiver, U::FFrame& frame, void*) {
                if (frame.Object() == receiver) intercept(call, receiver, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Guard Data Silo upper door script")});
        if (hooks[0] == Hook::ERROR_ID || hooks[1] == Hook::ERROR_ID)
        {
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        for (auto& hook : hooks)
        {
            if (hook != Hook::ERROR_ID) Hook::UnregisterCallback(hook);
            hook = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishDataSiloDoorGuardAvailable", [](const Lua& state) {
            state.set_bool(available() && seat(find<U::UObject>(door_path)) != Seat::unavailable);
            return 1;
        });
    }
}
