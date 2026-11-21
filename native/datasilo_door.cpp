#include "datasilo_door.hpp"
#include "shared_loot.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdlib>

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
    struct ToolPry
    {
        U::FWeakObjectPtr door;
        bool observed{};
    };
    ToolPry tool_pry;

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

    bool ordinary_door(U::UObject* door)
    {
        auto* type = door->GetClassPrivate();
        auto* base = find<U::UClass>(base_type);
        auto* pry = field<U::FBoolProperty>(door, STR("Can_PryOpen"));
        if (!type || !base || !type->IsChildOf(base) || !pry || !pry->GetPropertyValueInContainer(door)) return false;
        auto* root = object(door, STR("RootComponent"));
        auto* arrow = object(door, STR("Arrow"));
        auto* panel = object(door, STR("DoorMesh"));
        auto* knob = object(door, STR("Box_Knob"));
        return live(root) && live(arrow) && live(panel) && live(knob) &&
            arrow->GetClassPrivate() == find<U::UClass>(STR("/Script/Engine.ArrowComponent")) &&
            panel->GetClassPrivate() == find<U::UClass>(STR("/Script/Engine.StaticMeshComponent")) &&
            knob->GetClassPrivate() == find<U::UClass>(STR("/Script/Engine.BoxComponent")) &&
            object(arrow, STR("AttachParent")) == root && object(panel, STR("AttachParent")) == arrow &&
            object(knob, STR("AttachParent")) == panel;
    }

    Seat seat(U::UObject* door, bool allow_ordinary = false)
    {
        if (!live(door)) return Seat::unavailable;
        const bool special = door->GetClassPrivate() == find<U::UClass>(door_type) && door->GetPathName() == door_path;
        if (!special && !(allow_ordinary && ordinary_door(door))) return Seat::unavailable;
        auto* actor = static_cast<U::AActor*>(door);
        auto* world = actor->GetWorld();
        auto* ending = field<U::FBoolProperty>(door, STR("bActorIsBeingDestroyed"));
        if (!live(world) || (special && world->GetPathName() != world_path) || !ending || ending->GetPropertyValueInContainer(door) ||
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

    int graph_entry(U::UFunction* function, const void* args)
    {
        auto* entry = field<U::FIntProperty>(function, STR("EntryPoint"));
        if (!entry || !args || !entry->HasAnyPropertyFlags(U::CPF_Parm) || entry->GetOffset_Internal() != 0 ||
            function->GetNumParms() != 1 || function->GetReturnProperty()) return -1;
        return entry->GetPropertyValueInContainer(args);
    }

    void intercept(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!available() || !live(receiver) || !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        const bool script_graph = name == graph;
        if (!script_graph && std::ranges::find(wrappers, name) == wrappers.end()) return;
        if (function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        if (script_graph ? function->GetOuterPrivate() != find<U::UClass>(base_type) :
            receiver->GetClassPrivate()->GetFunctionByNameInChain(name) != function) return;
        const int offset = script_graph ? graph_entry(function, args) : -1;
        if (offset == 5349 && receiver == tool_pry.door.Get() && seat(receiver, true) == Seat::host)
        {
            tool_pry.observed = true;
            return;
        }
        if (seat(receiver, true) != Seat::guest) return;
        if (!script_graph || (offset >= 0 && std::ranges::any_of(stock_entries, [offset](const auto& item) {
                return item.offset == offset;
            }))) call.PreventOriginalFunctionCall();
    }

    std::uintptr_t address(const U::UObject* value)
    {
        return reinterpret_cast<std::uintptr_t>(value);
    }

    int begin_tool_pry(const Lua& lua)
    {
        const auto fail = [&] { lua.set_bool(false); return 1; };
        if (!available() || tool_pry.door.Get() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3)) return fail();
        const auto target = static_cast<std::uintptr_t>(lua.get_integer());
        const auto pawn = static_cast<std::uintptr_t>(lua.get_integer());
        const auto save = static_cast<std::uintptr_t>(lua.get_integer());
        if (!target || !pawn || !save) return fail();
        U::UObject* door{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != target) return RC::LoopAction::Continue;
            door = candidate;
            return RC::LoopAction::Break;
        });
        if (!live(door) || !ordinary_door(door) || seat(door, true) != Seat::host) return fail();
        auto* locked = field<U::FBoolProperty>(door, STR("Locked"));
        auto* broken = field<U::FBoolProperty>(door, STR("LockBroken"));
        if (!locked || !broken || !locked->GetPropertyValueInContainer(door) || broken->GetPropertyValueInContainer(door))
            return fail();
        auto* world = static_cast<U::AActor*>(door)->GetWorld();
        auto* stock = object(object(world, STR("OwningGameInstance")), STR("SaveSlot_Ref"));
        silverfish::shared_loot::EscrowOwner owner{};
        if (!silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save, address(stock), owner) ||
            owner.save == owner.stock) return fail();
        tool_pry.door = door;
        tool_pry.observed = false;
        lua.set_bool(true);
        return 1;
    }

    int finish_tool_pry(const Lua& lua)
    {
        const bool observed = available() && lua.get_stack_size() == 1 && lua.is_integer(1) &&
            static_cast<std::uintptr_t>(lua.get_integer()) == address(tool_pry.door.Get()) && tool_pry.observed;
        tool_pry = {};
        lua.set_bool(observed);
        return 1;
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
            {false, false, owner, STR("Guard stock door event")});
        hooks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* receiver, U::FFrame& frame, void*) {
                if (frame.Object() == receiver) intercept(call, receiver, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Guard stock door script")});
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
        tool_pry = {};
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
        lua.register_function("SilverfishStockDoorGuardAvailable", [](const Lua& state) {
            state.set_bool(available());
            return 1;
        });
        lua.register_function("SilverfishBeginNativeGuestDoorPry", begin_tool_pry);
        lua.register_function("SilverfishFinishNativeGuestDoorPry", finish_tool_pry);
    }
}
