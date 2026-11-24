#include "guest_primary_input.hpp"
#include "guest_item.hpp"
#include "guest_melee.hpp"
#include "guest_weapon_registry.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

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

#include <Windows.h>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::intptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto action_path = STR("/Game/Classes/Player/Player_Inputs/IA_Use_Primary.IA_Use_Primary");
    constexpr auto started_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36");
    constexpr auto completed_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_37");
    constexpr auto aim_action_path = STR("/Game/Classes/Player/Player_Inputs/IA_Use_Secondary.IA_Use_Secondary");
    constexpr auto aim_started_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_33");
    constexpr auto aim_canceled_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_34");
    constexpr auto aim_completed_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_Use_Secondary_K2Node_EnhancedInputActionEvent_35");
    constexpr auto toggle_path = STR("/Game/Classes/Player/Player_Inputs/IA_ModeToggle.IA_ModeToggle");
    constexpr auto toggle_completed_path =
        STR("/Game/Classes/Player/Player_FP.Player_FP_C:InpActEvt_IA_ModeToggle_K2Node_EnhancedInputActionEvent_29");
    constexpr auto bridge_world = STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P");
    constexpr auto camera_path = STR("/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C");
    constexpr auto old_pipe_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C");
    constexpr auto camera_primary_path = STR("/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C:Use_Item_Primary");
    constexpr std::string_view camera_quest = "Photo_DB_Turrets";
    constexpr auto camera_quest_name = STR("Photo_DB_Turrets");
    constexpr auto quest_struct = STR("/Game/Classes/Libraries/Structs/Quest_Struct.Quest_Struct");
    constexpr auto quest_step = STR("Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A");
    constexpr auto quest_end = STR("Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_struct = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto item_class = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto item_guid = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr auto item_ammo = STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788");
    constexpr auto scope_lease = std::chrono::seconds(2);
    constexpr auto receipt_lifetime = std::chrono::milliseconds(1500);
    constexpr auto release_lease = std::chrono::seconds(12);
    constexpr auto held_lease = std::chrono::seconds(8);
    constexpr auto held_edge_window = std::chrono::milliseconds(750);
    constexpr auto held_probe_period = std::chrono::milliseconds(90);
    constexpr auto held_fixture_lease = std::chrono::seconds(3);
    constexpr std::size_t receipt_limit = 8;
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;

    using Equipment = silverfish::guest_weapon_profiles::Profile;
    constexpr Equipment camera{"camera", camera_path, false, 0, 0, 0.0, false, nullptr, false};
    constexpr Equipment old_pipe{"old-pipe", old_pipe_path, false, 0, 0, 0.0, false, nullptr, false};
    constexpr Equipment melee{"melee", nullptr, false, 0, 0, 0.0, false, nullptr, false};
    constexpr Equipment blowtorch{"blowtorch", STR("/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C"),
                                    false, 0, 0, 0.0, false, nullptr, false};
    constexpr Equipment crowbar{"crowbar", STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C"),
                                   false, 0, 0, 0.0, false, nullptr, false};
    constexpr Equipment spade{"spade", STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C"),
                              false, 0, 0, 0.0, false, nullptr, false};

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* input(U::UFunction* function, const U::TCHAR* name, int offset)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize() ?
            property : nullptr;
    }

    U::UObject* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool false_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property && !property->GetPropertyValueInContainer(object);
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool guest_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT") && env("SILVERFISH_COOP_NATIVE_ZONE");
    }

    bool host_enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_HOST_CAMERA_PRIMARY_INPUT") && env("SILVERFISH_COOP_NATIVE_ZONE");
    }

    bool enabled() { return host_enabled() || guest_enabled(); }

    bool held_enabled() { return guest_enabled() && env("SILVERFISH_COOP_NATIVE_GUEST_HELD_FIRE"); }

    bool fixtures(bool host)
    {
        return env("SILVERFISH_COOP_HEADLESS") &&
            env(host ? "SILVERFISH_COOP_NATIVE_FIXTURES" : "SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES");
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool parse_guid(std::string_view text, Guid& value)
    {
        if (text.size() != 32) return false;
        for (std::size_t index = 0; index < value.size(); ++index)
        {
            const auto part = text.substr(index * 8, 8);
            for (const char c : part)
                if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'))) return false;
            const auto conversion = std::from_chars(part.data(), part.data() + 8, value[index], 16);
            if (conversion.ec != std::errc{} || conversion.ptr != part.data() + 8) return false;
        }
        return value != Guid{};
    }

    struct Wrapper
    {
        U::FWeakObjectPtr function{};
        U::FStructProperty* value{};
        U::FFloatProperty* elapsed{};
        U::FFloatProperty* triggered{};
        U::FObjectProperty* source{};
    };

    struct LocalPlayer
    {
        U::FWeakObjectPtr world{}, controller{}, pawn{}, local{}, instance{}, driver{}, server{};
        U::FWeakObjectPtr held{}, stock{};
        bool mirrored{};
        bool host{};
    };

    struct MappedInput
    {
        U::FWeakObjectPtr subsystem{}, query{}, key_down{};
        U::FObjectProperty* action{};
        U::FArrayProperty* keys{};
        U::FStructProperty* key{};
        U::FBoolProperty* down{};
    };

    struct Scope
    {
        LocalPlayer seat{};
        U::FWeakObjectPtr action{};
        Wrapper started{}, completed{};
        Clock::time_point expires{};
        const Equipment* item{};
        Guid guid{};
        std::string text;
        std::uint64_t generation{};
        MappedInput mapped{};
        U::FWeakObjectPtr aim_action{};
        std::array<Wrapper, 3> aim_wrappers{};
        bool aiming{};
    };

    struct CapturedPress
    {
        LocalPlayer seat{};
        U::FWeakObjectPtr action{};
        Wrapper completed{};
        Clock::time_point expires{};
        std::uint64_t generation{};
        std::uint64_t stroke{};
        bool local_melee{};
        bool fire_on_release{};
    };

    struct Receipt { std::uint64_t sequence{}; Clock::time_point expires{}; };
    struct TapReceipt : Receipt
    {
        silverfish::native_guest_item::ShotView view{};
        bool underbarrel{};
    };
    enum class HeldEdge { press, release, cancel };
    enum class HeldProof { none, mapped_key, edge_window, stock_melee_edges, fixture };
    enum class FixtureDispatch { none, tap, held };
    struct HeldReceipt
    {
        Receipt receipt{};
        std::uint64_t stroke{};
        HeldEdge edge{};
        std::int64_t duration_ms{};
        silverfish::native_guest_item::ShotView view{};
    };
    struct HeldStroke
    {
        std::uint64_t id{};
        HeldProof proof{};
        Clock::time_point started{}, expires{}, next_probe{}, next_view{};
    };
    Scope scope;
    CapturedPress press;
    std::array<TapReceipt, receipt_limit> receipts{};
    std::size_t head{}, count{};
    std::uint64_t sequence{};
    HeldStroke held;
    std::array<HeldReceipt, receipt_limit> held_receipts{};
    std::size_t held_head{}, held_count{};
    std::uint64_t held_sequence{};
    FixtureDispatch fixture_dispatch{};
    std::atomic<std::uint64_t> generation{1};
    std::atomic<bool> installed{};
    std::atomic<bool> protect_host_camera{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool current_player(const LocalPlayer& seat)
    {
        auto* world = seat.world.Get();
        auto* controller = seat.controller.Get();
        auto* pawn = seat.pawn.Get();
        auto* local = seat.local.Get();
        auto* instance = seat.instance.Get();
        auto* driver = seat.driver.Get();
        auto* server = seat.server.Get();
        auto* role = field<U::FByteProperty>(pawn, STR("Role"));
        if (!live(world) || !live(pawn) || !role || role->GetPropertyValueInContainer(pawn) != (seat.host ? 3 : 2) ||
            !false_field(pawn, STR("bActorIsBeingDestroyed")) || !live(controller) ||
            !false_field(controller, STR("bActorIsBeingDestroyed")) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            !live(local) || !live(instance) || !live(driver) ||
            object_field(pawn, STR("Controller")) != controller || object_field(controller, STR("Pawn")) != pawn ||
            object_field(controller, STR("Player")) != local || object_field(local, STR("PlayerController")) != controller ||
            object_field(world, STR("OwningGameInstance")) != instance || object_field(world, STR("NetDriver")) != driver ||
            object_field(driver, STR("World")) != world || object_field(driver, STR("ServerConnection")) != server) return false;
        if (seat.host)
        {
            auto* stock = seat.stock.Get();
            auto* controller_role = field<U::FByteProperty>(controller, STR("Role"));
            if (server || !field<U::FObjectProperty>(driver, STR("ServerConnection")) ||
                !controller_role || controller_role->GetPropertyValueInContainer(controller) != 3 ||
                instance->GetClassPrivate() != find<U::UClass>(instance_path) ||
                object_field(instance, STR("As Player FP")) != pawn || object_field(pawn, STR("As Game Instance FP")) != instance ||
                !live(stock) || stock->GetClassPrivate() != find<U::UClass>(save_path) ||
                object_field(instance, STR("SaveSlot_Ref")) != stock || object_field(pawn, STR("Save")) != stock) return false;
        }
        else if (!live(server) || object_field(server, STR("Driver")) != driver) return false;
        auto* players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = players ? U::CastField<U::FObjectProperty>(players->GetInner()) : nullptr;
        if (!entry) return false;
        U::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        return locals.Num() == 1 && entry->GetObjectPropertyValue(locals.GetRawPtr(0)) == local;
    }

    struct ItemRow
    {
        U::UScriptStruct* type{};
        U::FClassProperty* klass{};
        U::FStructProperty* guid{};
        U::FIntProperty* ammo{};
        std::array<U::FIntProperty*, 4> words{};

        bool bind()
        {
            type = find<U::UScriptStruct>(item_struct);
            klass = field<U::FClassProperty>(type, item_class);
            guid = field<U::FStructProperty>(type, item_guid);
            ammo = field<U::FIntProperty>(type, item_ammo);
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            if (!type || !klass || !guid || guid->GetSize() != 16 || !guid_type || guid->GetStruct().Get() != guid_type) return false;
            constexpr std::array names{STR("A"), STR("B"), STR("C"), STR("D")};
            for (std::size_t i = 0; i < words.size(); ++i)
            {
                words[i] = field<U::FIntProperty>(guid_type, names[i]);
                if (!words[i] || words[i]->GetOffset_Internal() != static_cast<int>(i * sizeof(std::int32_t))) return false;
            }
            return true;
        }

        U::FArrayProperty* array(U::UObject* save, const U::TCHAR* name) const
        {
            auto* rows = field<U::FArrayProperty>(save, name);
            auto* inner = rows ? U::CastField<U::FStructProperty>(rows->GetInner()) : nullptr;
            return inner && inner->GetStruct().Get() == type ? rows : nullptr;
        }

        U::UObject* row_class(const void* row) const
        {
            return klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(row));
        }

        Guid identity(const void* row) const
        {
            const auto* raw = guid->ContainerPtrToValuePtr<void>(row);
            Guid found{};
            for (std::size_t i = 0; i < found.size(); ++i)
                found[i] = static_cast<std::uint32_t>(words[i]->GetPropertyValueInContainer(raw));
            return found;
        }
    };

    bool matching_local_item(const Scope& current, Guid* observed = nullptr)
    {
        auto* pawn = current.seat.pawn.Get();
        if (!field<U::FObjectProperty>(pawn, STR("EquipedItem")) || !field<U::FObjectProperty>(pawn, STR("HeldObject")) ||
            object_field(pawn, STR("HeldObject"))) return false;
        auto* held = object_field(pawn, STR("EquipedItem"));
        // Stock held-actor references are not replicated. An authenticated
        // server authorization can cover an otherwise empty local proxy.
        if (!current.seat.mirrored) return !observed && !current.seat.host && held == nullptr;
        auto* item = current.item == &melee ? (live(held) ? held->GetClassPrivate() : nullptr)
                                            : find<U::UClass>(current.item->path);
        if (!live(held) || held != current.seat.held.Get() || !item || held->GetClassPrivate() != item ||
            object_field(held, STR("Owner")) != pawn || object_field(held, STR("Instigator")) != pawn ||
            static_cast<U::AActor*>(held)->GetWorld() != current.seat.world.Get()) return false;
        if (current.item->kind == "camera")
        {
            auto* held_by_player = field<U::FBoolProperty>(held, STR("HeldByPlayer"));
            if (!held_by_player || !held_by_player->GetPropertyValueInContainer(held) ||
                object_field(held, STR("User")) != pawn) return false;
            if (current.seat.host && !observed)
            {
                auto* lens = object_field(held, STR("SceneCaptureComponent2D"));
                auto* target = object_field(lens, STR("TextureTarget"));
                auto* fov = field<U::FFloatProperty>(lens, STR("FOVAngle"));
                if (!live(lens) || lens->GetOuterPrivate() != held ||
                    lens->GetClassPrivate() != find<U::UClass>(STR("/Script/Engine.SceneCaptureComponent2D")) ||
                    !live(target) || target != find<U::UObject>(STR("/Game/Assets/Textures/VirtualTextures/VT_Camera.VT_Camera")) ||
                    target->GetClassPrivate() != find<U::UClass>(STR("/Script/Engine.TextureRenderTarget2D")) || !fov) return false;
                const auto angle = fov->GetPropertyValueInContainer(lens);
                if (!std::isfinite(angle) || angle <= 0.0f || angle >= 179.0f) return false;
            }
        }
        auto* save = object_field(pawn, STR("Save"));
        if (!live(save) || save->GetClassPrivate() != find<U::UClass>(save_path)) return false;

        auto* selected = field<U::FIntProperty>(save, STR("PlayerEquipIndex"));
        ItemRow schema;
        if (!selected || !schema.bind()) return false;
        auto* rows = schema.array(save, STR("PlayerEquipment"));
        if (!rows) return false;
        U::FScriptArrayHelper items(rows, rows->ContainerPtrToValuePtr<void>(save));
        const auto index = selected->GetPropertyValueInContainer(save);
        if (index < 0 || index >= items.Num() || items.Num() > 4096) return false;
        const auto* row = items.GetRawPtr(index);
        if (schema.row_class(row) != item) return false;
        if (current.item == &old_pipe || current.item == &melee || current.item == &crowbar || current.item == &spade)
        {
            auto* amount = field<U::FIntProperty>(held, STR("Ammo"));
            auto* held_by_player = field<U::FBoolProperty>(held, STR("HeldByPlayer"));
            if (!schema.ammo || schema.ammo->GetPropertyValueInContainer(row) != -1 ||
                !amount || amount->GetPropertyValueInContainer(held) != -1 || !held_by_player ||
                !held_by_player->GetPropertyValueInContainer(held) || object_field(held, STR("User")) != pawn) return false;
        }
        const auto actual = schema.identity(row);
        if (!observed) return actual == current.guid;
        *observed = actual;
        return actual != Guid{};
    }

    bool active_photo_quest(U::UObject* save)
    {
        auto* type = find<U::UScriptStruct>(quest_struct);
        auto* map = field<U::FMapProperty>(save, STR("ActiveQuests"));
        auto* key = map ? U::CastField<U::FNameProperty>(map->GetKeyProp()) : nullptr;
        auto* row = map ? U::CastField<U::FStructProperty>(map->GetValueProp()) : nullptr;
        auto* step = field<U::FIntProperty>(type, quest_step);
        auto* final = field<U::FIntProperty>(type, quest_end);
        const U::FName id{camera_quest_name, U::FNAME_Find};
        if (!type || !key || !row || row->GetStruct().Get() != type || key->GetOffset_Internal() != 0 ||
            !step || !final || id.IsNone() || id.ToString() != camera_quest_name) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), row->GetSize(), row->GetMinAlignment());
        if (row->GetOffset_Internal() != layout.ValueOffset) return false;
        auto* quests = map->ContainerPtrToValuePtr<U::FScriptMap>(save);
        const auto extent = quests->GetMaxIndex();
        if (quests->Num() < 1 || quests->Num() > extent || extent > 4096) return false;
        const void* matched{};
        for (int index = 0; index < extent; ++index)
        {
            if (!quests->IsValidIndex(index)) continue;
            const auto* item = quests->GetData(index, layout);
            if (!item) return false;
            if (key->GetPropertyValue(item) != id) continue;
            if (matched) return false;
            matched = row->ContainerPtrToValuePtr<void>(item);
        }
        if (!matched || final->GetPropertyValueInContainer(matched) != 3) return false;
        const auto active_step = step->GetPropertyValueInContainer(matched);
        return active_step == 1 || active_step == 2;
    }

    bool qualified(const Scope& current, Clock::time_point now)
    {
        return current.item && current.generation == generation.load(std::memory_order_acquire) &&
            now < current.expires && current_player(current.seat) && matching_local_item(current) &&
            (!current.seat.host || active_photo_quest(current.seat.stock.Get()));
    }

    bool camera_scope(U::UObject* world)
    {
        auto* camera = find<U::UClass>(camera_path);
        auto* function = find<U::UFunction>(camera_primary_path);
        return live(world) && world->GetPathName() == bridge_world && find<U::UWorld>(bridge_world) == world &&
            camera && function && function->GetOuterPrivate() == camera && function->GetNumParms() == 2 &&
            function->GetParmsSize() == 16 && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) &&
            input<U::FBoolProperty>(function, STR("Held"), 0) &&
            input<U::FDoubleProperty>(function, STR("VarianceScaler"), 8);
    }

    bool can_act(const LocalPlayer& seat)
    {
        auto* pawn = seat.pawn.Get();
        return false_field(seat.controller.Get(), STR("Loading")) && false_field(pawn, STR("Dead")) &&
            false_field(pawn, STR("IsWeaponBlocked")) && false_field(pawn, STR("UsingItem")) &&
            false_field(pawn, STR("Reloading")) && false_field(pawn, STR("IsChangingAmmo")) &&
            false_field(pawn, STR("Speaking")) && false_field(pawn, STR("HeadUnderWater")) &&
            false_field(pawn, STR("Wep_HeldAside")) && false_field(pawn, STR("OnLadder"));
    }

    bool verifies_source(const Wrapper& wrapper, U::UObject* action, const void* arguments)
    {
        if (!arguments || !action || !wrapper.source ||
            wrapper.source->GetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(arguments)) != action)
            return false;
        const auto elapsed = wrapper.elapsed->GetPropertyValueInContainer(arguments);
        const auto triggered = wrapper.triggered->GetPropertyValueInContainer(arguments);
        return std::isfinite(elapsed) && elapsed >= 0 && std::isfinite(triggered) && triggered >= 0;
    }

    bool local_game_focused()
    {
        DWORD process{};
        const auto window = GetForegroundWindow();
        return window && GetWindowThreadProcessId(window, &process) != 0 && process == GetCurrentProcessId();
    }

    void bind_mapped_input(Scope& next)
    {
        auto* local = next.seat.local.Get();
        auto* action = next.action.Get();
        auto* type = find<U::UClass>(STR("/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem"));
        auto* interface_type = find<U::UClass>(STR("/Script/EnhancedInput.EnhancedInputSubsystemInterface"));
        auto* key_type = find<U::UScriptStruct>(STR("/Script/InputCore.Key"));
        auto* key_down = find<U::UFunction>(STR("/Script/Engine.PlayerController:IsInputKeyDown"));
        if (!local || !action || !type || !key_type || !key_down) return;
        U::UObject* subsystem{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object, int, int) {
            if (object->GetClassPrivate() != type || object->GetOuterPrivate() != local || !live(object))
                return RC::LoopAction::Continue;
            if (subsystem)
            {
                subsystem = nullptr;
                return RC::LoopAction::Break;
            }
            subsystem = object;
            return RC::LoopAction::Continue;
        });
        if (!subsystem) return;
        auto* query = subsystem->GetFunctionByNameInChain(U::FName{STR("QueryKeysMappedToAction")});
        if (!query || (query->GetOuterPrivate() != type && query->GetOuterPrivate() != interface_type) ||
            !query->HasAnyFunctionFlags(U::FUNC_Native) || query->HasAnyFunctionFlags(U::FUNC_Net) ||
            query->GetNumParms() != 2 || query->GetParmsSize() > 128 ||
            !key_down->HasAnyFunctionFlags(U::FUNC_Native) || key_down->HasAnyFunctionFlags(U::FUNC_Net) ||
            key_down->GetNumParms() != 2 || key_down->GetParmsSize() > 128) return;
        auto* query_action = input<U::FObjectProperty>(query, STR("Action"), 0);
        auto* keys = U::CastField<U::FArrayProperty>(query->GetReturnProperty());
        auto* inner = keys ? U::CastField<U::FStructProperty>(keys->GetInner()) : nullptr;
        auto* key = input<U::FStructProperty>(key_down, STR("Key"), 0);
        auto* down = U::CastField<U::FBoolProperty>(key_down->GetReturnProperty());
        if (!query_action || query_action->GetPropertyClass().Get() != action->GetClassPrivate() ||
            !inner || inner->GetStruct().Get() != key_type || !key || key->GetStruct().Get() != key_type ||
            !down || !down->HasAnyPropertyFlags(U::CPF_ReturnParm) || !keys->HasAnyPropertyFlags(U::CPF_ReturnParm) ||
            keys->GetOffset_Internal() < query_action->GetSize() ||
            keys->GetOffset_Internal() + keys->GetSize() > query->GetParmsSize() ||
            down->GetOffset_Internal() < key->GetSize() ||
            down->GetOffset_Internal() + down->GetSize() > key_down->GetParmsSize()) return;
        next.mapped = {subsystem, query, key_down, query_action, keys, key, down};
    }

    struct ReflectedValue
    {
        U::FProperty* property;
        void* buffer;
        ReflectedValue(U::FProperty* property, void* buffer) : property(property), buffer(buffer)
        {
            property->InitializeValue_InContainer(buffer);
        }
        ~ReflectedValue() { property->DestroyValue_InContainer(buffer); }
        ReflectedValue(const ReflectedValue&) = delete;
        ReflectedValue& operator=(const ReflectedValue&) = delete;
    };

    enum class KeyState { unavailable, up, down };

    KeyState mapped_key_state(const Scope& current)
    {
        const auto& mapped = current.mapped;
        auto* subsystem = mapped.subsystem.Get();
        auto* query = static_cast<U::UFunction*>(mapped.query.Get());
        auto* key_down = static_cast<U::UFunction*>(mapped.key_down.Get());
        auto* controller = current.seat.controller.Get();
        auto* action = current.action.Get();
        if (!live(subsystem) || !live(query) || !live(key_down) || !live(controller) || !live(action) ||
            subsystem->GetOuterPrivate() != current.seat.local.Get()) return KeyState::unavailable;
        alignas(std::max_align_t) std::array<std::byte, 128> query_args{};
        ReflectedValue key_array(mapped.keys, query_args.data());
        mapped.action->SetObjectPropertyValue(mapped.action->ContainerPtrToValuePtr<void>(query_args.data()), action);
        subsystem->ProcessEvent(query, query_args.data());
        U::FScriptArrayHelper keys(mapped.keys, mapped.keys->ContainerPtrToValuePtr<void>(query_args.data()));
        if (keys.Num() < 0 || keys.Num() > 16) return KeyState::unavailable;
        for (int index = 0; index < keys.Num(); ++index)
        {
            alignas(std::max_align_t) std::array<std::byte, 128> key_args{};
            ReflectedValue key(mapped.key, key_args.data());
            mapped.key->CopyCompleteValue(mapped.key->ContainerPtrToValuePtr<void>(key_args.data()), keys.GetRawPtr(index));
            controller->ProcessEvent(key_down, key_args.data());
            if (mapped.down->GetPropertyValueInContainer(key_args.data())) return KeyState::down;
        }
        return KeyState::up;
    }

    Wrapper bind_wrapper(U::UClass* pawn, U::UObject* action, const U::TCHAR* path, std::uint8_t expected_trigger)
    {
        auto* function = find<U::UFunction>(path);
        auto* value_type = find<U::UScriptStruct>(STR("/Script/EnhancedInput.InputActionValue"));
        if (!function || function->GetOuterPrivate() != pawn || function->GetNumParms() != 4 ||
            function->GetParmsSize() != 0x30 || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !value_type) return {};
        auto* binding = find<U::UObject>(
            STR("/Game/Classes/Player/Player_FP.Player_FP_C:EnhancedInputActionDelegateBinding_0"));
        auto* rows = field<U::FArrayProperty>(binding, STR("InputActionDelegateBindings"));
        auto* inner = rows ? U::CastField<U::FStructProperty>(rows->GetInner()) : nullptr;
        auto* type = find<U::UScriptStruct>(STR("/Script/EnhancedInput.BlueprintEnhancedInputActionBinding"));
        auto* name = field<U::FNameProperty>(type, STR("FunctionNameToBind"));
        auto* source = field<U::FObjectProperty>(type, STR("InputAction"));
        auto* trigger = field<U::FEnumProperty>(type, STR("TriggerEvent"));
        auto* trigger_type = find<U::UEnum>(STR("/Script/EnhancedInput.ETriggerEvent"));
        auto* trigger_value = trigger ? trigger->GetUnderlyingProperty() : nullptr;
        if (!live(binding) || binding->GetOuterPrivate() != pawn || !inner || !type || inner->GetStruct().Get() != type ||
            !name || !source || !trigger || !trigger_type || trigger->GetEnum().Get() != trigger_type ||
            !trigger_value || trigger_value->GetSize() != 1) return {};
        U::FScriptArrayHelper bindings(rows, rows->ContainerPtrToValuePtr<void>(binding));
        if (bindings.Num() < 1 || bindings.Num() > 128) return {};
        int matches = 0;
        for (int i = 0; i < bindings.Num(); ++i)
        {
            const auto* row = bindings.GetRawPtr(i);
            if (name->GetPropertyValueInContainer(row) != function->GetFName()) continue;
            if (source->GetObjectPropertyValue(source->ContainerPtrToValuePtr<void>(row)) != action ||
                trigger_value->GetUnsignedIntPropertyValue(trigger->ContainerPtrToValuePtr<void>(row)) != expected_trigger)
                return {};
            ++matches;
        }
        if (matches != 1) return {};
        Wrapper result;
        result.value = input<U::FStructProperty>(function, STR("ActionValue"), 0);
        result.elapsed = input<U::FFloatProperty>(function, STR("ElapsedTime"), 0x20);
        result.triggered = input<U::FFloatProperty>(function, STR("TriggeredTime"), 0x24);
        result.source = input<U::FObjectProperty>(function, STR("SourceAction"), 0x28);
        if (!result.value || result.value->GetSize() != 0x20 || result.value->GetStruct().Get() != value_type ||
            !result.elapsed || !result.triggered || !result.source ||
            result.source->GetPropertyClass().Get() != action->GetClassPrivate()) return {};
        result.function = function;
        return result;
    }

    std::string_view bind_player(Scope& next, U::UObject* pawn, Address requested_controller, Address requested_world,
                                 bool equipping, Guid* observed = nullptr)
    {
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* controller_type = find<U::UClass>(controller_path);
        auto* connection_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* action_type = find<U::UClass>(STR("/Script/EnhancedInput.InputAction"));
        const bool toggle = next.item == &crowbar;
        auto* action = find<U::UObject>(toggle ? toggle_path : action_path);
        if (!pawn_type || !controller_type || !connection_type || !local_type ||
            !action_type || !live(action) || action->GetClassPrivate() != action_type)
            return "stock_guest_primary_classes_unavailable";
        if (!live(pawn) || pawn->GetClassPrivate() != pawn_type) return "stock_guest_primary_pawn_unavailable";
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* local = object_field(controller, STR("Player"));
        auto* driver = object_field(world, STR("NetDriver"));
        auto* server = object_field(driver, STR("ServerConnection"));
        if (address(world) != requested_world || address(controller) != requested_controller ||
            !live(controller) || controller->GetClassPrivate() != controller_type || !live(local) ||
            !local->IsA(local_type) || (next.seat.host ? server != nullptr : !live(server) || !server->IsA(connection_type)))
            return "stock_guest_primary_connection_unavailable";
        if (next.item->kind == "camera" && !camera_scope(world)) return "stock_guest_camera_primary_unqualified";
        const bool uses_melee = next.item == &old_pipe || next.item == &melee;
        if (uses_melee && (next.seat.host || !silverfish::native_guest_melee::ordinary_world(world)))
            return "stock_guest_melee_primary_unqualified";
        next.seat.pawn = pawn;
        next.seat.world = world;
        next.seat.controller = controller;
        next.seat.instance = object_field(world, STR("OwningGameInstance"));
        next.seat.local = local;
        next.seat.driver = driver;
        next.seat.server = server;
        if (next.seat.host) next.seat.stock = object_field(next.seat.instance.Get(), STR("SaveSlot_Ref"));
        auto* held = object_field(pawn, STR("EquipedItem"));
        if (next.item == &melee && held && !silverfish::native_guest_melee::ordinary_weapon(held))
            return "stock_guest_melee_equipment_unqualified";
        next.seat.mirrored = held != nullptr;
        next.seat.held = held;
        next.action = action;
        if (!current_player(next.seat) || (!equipping && !matching_local_item(next, observed)))
            return "stock_guest_primary_local_equipment_unqualified";
        if (next.seat.host && !active_photo_quest(next.seat.stock.Get())) return "stock_host_camera_quest_inactive";
        if (equipping) return {};
        if (toggle)
        {
            next.completed = bind_wrapper(pawn_type, action, toggle_completed_path, 16);
            return next.completed.function.Get() ? std::string_view{} : "stock_guest_crowbar_wrapper_unavailable";
        }
        next.started = bind_wrapper(pawn_type, action, started_path, 2);
        if (!next.started.function.Get()) return "stock_guest_primary_wrappers_unavailable";
        if (observed) return {};
        next.completed = bind_wrapper(pawn_type, action, completed_path, 16);
        if (!next.seat.host && !uses_melee && next.item != &blowtorch && next.item != &camera && next.item != &spade)
        {
            if (auto* aim = find<U::UObject>(aim_action_path); live(aim) && aim->GetClassPrivate() == action_type)
            {
                next.aim_wrappers = {bind_wrapper(pawn_type, aim, aim_started_path, 2),
                                     bind_wrapper(pawn_type, aim, aim_canceled_path, 8),
                                     bind_wrapper(pawn_type, aim, aim_completed_path, 16)};
                if (next.aim_wrappers[0].function.Get() && next.aim_wrappers[1].function.Get() &&
                    next.aim_wrappers[2].function.Get()) next.aim_action = aim;
            }
        }
        if (next.completed.function.Get() && !next.seat.host && held_enabled() &&
            silverfish::guest_weapon_profiles::stock_held_fire(next.item->kind))
            bind_mapped_input(next);
        return next.completed.function.Get() ? std::string_view{} : "stock_guest_primary_wrappers_unavailable";
    }

    std::string_view bind(Scope& next, Address requested_pawn, Address requested_controller, Address requested_world,
                          bool equipping = false)
    {
        U::UObject* pawn{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != requested_pawn) return RC::LoopAction::Continue;
            pawn = candidate;
            return RC::LoopAction::Break;
        });
        return bind_player(next, pawn, requested_controller, requested_world, equipping);
    }

    bool available() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    void discard_expired(Clock::time_point now)
    {
        while (count && now >= receipts[head].expires)
        {
            head = (head + 1) % receipt_limit;
            --count;
        }
    }

    bool scoped_held_fire()
    {
        // Keep the receipt route open after a mode change so it can drain a cancellation.
        return scope.item && !scope.seat.host && held_enabled() &&
            silverfish::guest_weapon_profiles::stock_held_fire(scope.item->kind);
    }

    bool stock_held_fire_selected()
    {
        auto* receiver = scope.seat.held.Get();
        if (!scope.seat.mirrored || !live(receiver) || object_field(receiver, STR("InputIntercept"))) return false;
        auto* selector = field<U::FBoolProperty>(receiver, STR("HasSelectFire"));
        return selector && (!selector->GetPropertyValueInContainer(receiver) ||
            false_field(receiver, STR("SemiAutoSelected")));
    }

    bool scoped_melee_edges()
    {
        return !scope.seat.host && (scope.item == &old_pipe || scope.item == &melee);
    }

    void discard_held(Clock::time_point now)
    {
        while (held_count && now >= held_receipts[held_head].receipt.expires)
        {
            held_head = (held_head + 1) % receipt_limit;
            --held_count;
        }
    }

    bool queue_held(HeldEdge edge, std::uint64_t stroke, Clock::time_point now, std::int64_t duration_ms = 0)
    {
        discard_held(now);
        if (held_count == receipt_limit || held_sequence == max_sequence) return false;
        auto& receipt = held_receipts[(held_head + held_count) % receipt_limit];
        receipt = {{++held_sequence, now + receipt_lifetime}, stroke, edge, duration_ms};
        if (edge == HeldEdge::press && scoped_held_fire())
            receipt.view = silverfish::native_guest_item::capture_shot_view(scope.seat.pawn.Get(), scope.seat.held.Get());
        ++held_count;
        return true;
    }

    void stop_held(HeldEdge edge, Clock::time_point now)
    {
        if (!held.id) return;
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - held.started);
        const auto duration = std::clamp(elapsed, std::chrono::milliseconds{0}, std::chrono::milliseconds{9000});
        queue_held(edge, held.id, now, duration.count());
        if (press.stroke == held.id && press.local_melee)
        {
            silverfish::native_guest_melee::present_local_input(press.seat.pawn.Get(), press.seat.held.Get(), false);
            press.local_melee = false;
        }
        if (edge == HeldEdge::cancel && press.stroke == held.id && press.expires > now + held_probe_period)
            press.expires = now + held_probe_period;
        held = {};
    }

    bool begin_held(Clock::time_point now)
    {
        auto proof = HeldProof::fixture;
        auto until = now + held_fixture_lease;
        if (fixture_dispatch != FixtureDispatch::held)
        {
            if (!local_game_focused()) return false;
            if (scoped_melee_edges())
            {
                // This stock Enhanced Input action has explicit Started and Completed wrappers.
                // Preserve the real edge interval rather than guessing from a platform key.
                proof = HeldProof::stock_melee_edges;
                until = now + held_lease;
            }
            else
            {
                const auto key = mapped_key_state(scope);
                if (key == KeyState::up) return false;
                proof = key == KeyState::down ? HeldProof::mapped_key : HeldProof::edge_window;
                until = now + (proof == HeldProof::mapped_key ? held_lease : held_edge_window);
            }
        }
        if (held.id || held_sequence == max_sequence || !queue_held(HeldEdge::press, held_sequence + 1, now))
            return false;
        held = {held_sequence, proof, now, until, now + held_probe_period};
        press.stroke = held.id;
        if (scoped_melee_edges() && scope.seat.mirrored)
            press.local_melee = silverfish::native_guest_melee::present_local_input(
                scope.seat.pawn.Get(), scope.seat.held.Get(), true);
        return true;
    }

    void check_held(Clock::time_point now)
    {
        if (!held.id) return;
        if (now >= held.expires || !qualified(scope, now) || !can_act(scope.seat) ||
            (scoped_held_fire() && !stock_held_fire_selected()) ||
            (held.proof != HeldProof::fixture && !local_game_focused()))
        {
            stop_held(HeldEdge::cancel, now);
            return;
        }
        if (held.proof == HeldProof::mapped_key && now >= held.next_probe)
        {
            if (mapped_key_state(scope) != KeyState::down) stop_held(HeldEdge::cancel, now);
            else held.next_probe = now + held_probe_period;
        }
    }

    bool host_shutter_candidate(U::UFunction* function)
    {
        if (!protect_host_camera.load(std::memory_order_acquire)) return false;
        static const U::FName start_name{STR("InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36")};
        return function->GetFName() == start_name;
    }

    bool unleased_host_shutter(U::UObject* pawn, U::UFunction* function, const void* arguments)
    {
        auto* type = find<U::UClass>(pawn_path);
        if (!type || !live(pawn) || pawn->GetClassPrivate() != type || function->GetOuterPrivate() != type) return false;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        if (!live(world) || world->GetPathName() != bridge_world) return false;
        Scope local;
        local.seat.host = true;
        local.item = &camera;
        // The stock camera can still advance the objective if its display
        // lens is unsuitable for an authorized capture. Verify ownership here.
        if (!bind_player(local, pawn, address(object_field(pawn, STR("Controller"))), address(world),
                         false, &local.guid).empty()) return false;
        return function == local.started.function.Get() && verifies_source(local.started, local.action.Get(), arguments);
    }

    enum class StartedRoute { stock, block, authorized };

    constexpr StartedRoute select_started_route(bool leased, bool exact_host_shutter)
    {
        if (leased) return StartedRoute::authorized;
        return exact_host_shutter ? StartedRoute::block : StartedRoute::stock;
    }

    static_assert(select_started_route(false, false) == StartedRoute::stock);
    static_assert(select_started_route(false, true) == StartedRoute::block);
    static_assert(select_started_route(true, false) == StartedRoute::authorized);
    static_assert(select_started_route(true, true) == StartedRoute::authorized);

    void queue_tap(Clock::time_point now, U::UObject* underbarrel = nullptr)
    {
        if (sequence == max_sequence) return;
        discard_expired(now);
        if (count == receipt_limit) return;
        auto& receipt = receipts[(head + count) % receipt_limit];
        receipt = {{++sequence, now + receipt_lifetime}, {}, underbarrel != nullptr};
        if (std::ranges::find(silverfish::guest_weapon_profiles::firearms, scope.item->kind, &Equipment::kind)
            != silverfish::guest_weapon_profiles::firearms.end())
            receipt.view = silverfish::native_guest_item::capture_shot_view(scope.seat.pawn.Get(), scope.seat.held.Get(), underbarrel);
        ++count;
    }

    void intercept(Iteration& iteration, U::UObject* pawn, U::UFunction* function, const void* arguments)
    {
        if (!available() || !pawn || !function || iteration.OriginalFunctionCallPrevented()) return;
        if (auto* action = scope.aim_action.Get(); pawn == scope.seat.pawn.Get() && action)
        {
            for (std::size_t i = 0; i < scope.aim_wrappers.size(); ++i)
            {
                const auto& wrapper = scope.aim_wrappers[i];
                if (wrapper.function.Get() != function) continue;
                if (verifies_source(wrapper, action, arguments))
                    scope.aiming = i == 0 && qualified(scope, Clock::now()) && can_act(scope.seat) &&
                        false_field(pawn, STR("Sprinting"));
                return;
            }
        }
        if (scope.item == &crowbar && pawn == scope.seat.pawn.Get() && function == scope.completed.function.Get())
        {
            const auto now = Clock::now();
            if (!qualified(scope, now) || !verifies_source(scope.completed, scope.action.Get(), arguments)) return;
            // A long hold controls the stock flashlight. The short release is the item action.
            const float triggered = scope.completed.triggered->GetPropertyValueInContainer(arguments);
            if (!(triggered >= 0.0f && triggered < 0.25f)) return;
            iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented() && can_act(scope.seat)) queue_tap(now);
            return;
        }
        const bool released = pawn == press.seat.pawn.Get() && function == press.completed.function.Get();
        const bool started = pawn == scope.seat.pawn.Get() && function == scope.started.function.Get();
        const bool host_candidate = host_shutter_candidate(function);
        if (!released && !started && !host_candidate) return;
        const auto now = Clock::now();
        const auto active_generation = generation.load(std::memory_order_acquire);
        if (released && press.generation == active_generation && now < press.expires && current_player(press.seat) &&
            verifies_source(press.completed, press.action.Get(), arguments))
        {
            auto* current = object_field(pawn, STR("EquipedItem"));
            const bool same_actor = press.seat.mirrored ? current && current == press.seat.held.Get() : !current;
            if (press.stroke != 0 && press.stroke == held.id)
                stop_held(same_actor ? HeldEdge::release : HeldEdge::cancel, now);
            const bool fire_on_release = press.fire_on_release;
            press = {};
            if (same_actor)
            {
                iteration.PreventOriginalFunctionCall();
                if (fire_on_release && iteration.OriginalFunctionCallPrevented() && qualified(scope, now) &&
                    scope.item->action_on_release && scope.seat.pawn.Get() == pawn && can_act(scope.seat) &&
                    !object_field(current, STR("InputIntercept")))
                    queue_tap(now);
            }
            return;
        }
        const bool leased = started && qualified(scope, now) && verifies_source(scope.started, scope.action.Get(), arguments);
        const auto route = select_started_route(leased, !leased && host_candidate && unleased_host_shutter(pawn, function, arguments));
        if (route == StartedRoute::stock) return;
        iteration.PreventOriginalFunctionCall();
        if (route != StartedRoute::authorized || !iteration.OriginalFunctionCallPrevented()) return;
        if (press.generation == active_generation && now < press.expires &&
            press.seat.pawn.Get() == pawn && press.seat.controller.Get() == scope.seat.controller.Get() &&
            press.seat.world.Get() == scope.seat.world.Get()) return;
        press = {scope.seat, scope.action, scope.completed, now + release_lease, active_generation};
        if (!can_act(scope.seat)) return;
        auto* receiver = scope.seat.held.Get();
        if (object_field(receiver, STR("InputIntercept")))
        {
            if (auto* child = silverfish::native_guest_item::active_loki_barrel(pawn, receiver)) queue_tap(now, child);
            return;
        }
        if (scope.item->action_on_release)
        {
            press.fire_on_release = true;
            return;
        }
        if (fixture_dispatch != FixtureDispatch::tap &&
            ((scoped_held_fire() && stock_held_fire_selected()) || scoped_melee_edges()))
        {
            begin_held(now);
            return;
        }
        queue_tap(now);
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua, bool host)
    {
        const auto size = lua.get_stack_size();
        if (!available() || (size != 5 && size != 6) || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5) || (size == 6 && !lua.is_string(6)))
            return result(lua, false, "native_guest_primary_exact_scope_required");
        const auto pawn = lua.get_integer();
        const auto controller = lua.get_integer();
        const auto world = lua.get_integer();
        const std::string text{lua.get_string()};
        const auto kind = lua.get_string();
        Scope next;
        next.seat.host = host;
        if (pawn <= 0 || controller <= 0 || world <= 0 || !parse_guid(text, next.guid))
            return result(lua, false, "native_guest_primary_exact_scope_required");
        if (kind == camera.kind) next.item = &camera;
        else if (kind == old_pipe.kind) next.item = &old_pipe;
        else if (kind == melee.kind) next.item = &melee;
        else if (kind == blowtorch.kind) next.item = &blowtorch;
        else if (!host && kind == crowbar.kind) next.item = &crowbar;
        else if (!host && kind == spade.kind) next.item = &spade;
        else
            for (const auto& candidate : silverfish::guest_weapon_profiles::firearms)
                if (candidate.kind == kind) next.item = &candidate;
        if (!next.item || (host && next.item->kind != "camera"))
            return result(lua, false, "native_guest_primary_weapon_unqualified");
        if ((next.item->kind == "camera") != (size == 6) || (size == 6 && lua.get_string() != camera_quest))
            return result(lua, false, "native_guest_primary_quest_scope_required");
        const auto now = Clock::now();
        if (scope.seat.host == host && scope.item == next.item && scope.guid == next.guid && address(scope.seat.pawn.Get()) == pawn &&
            address(scope.seat.controller.Get()) == controller && address(scope.seat.world.Get()) == world &&
            qualified(scope, now))
        {
            scope.expires = now + scope_lease;
            return result(lua, true, scope.seat.mirrored ? "stock_held" : "host_equipped");
        }
        scope = {};
        head = count = 0;
        held = {};
        held_head = held_count = 0;
        if (const auto failure = bind(next, pawn, controller, world); !failure.empty()) return result(lua, false, failure);
        next.expires = now + scope_lease;
        next.text = text;
        next.generation = generation.load(std::memory_order_acquire);
        scope = std::move(next);
        return result(lua, true, scope.seat.mirrored ? "stock_held" : "host_equipped");
    }

    int push_shot_view(const Lua& lua, const silverfish::native_guest_item::ShotView& view)
    {
        if (view.direction == silverfish::native_guest_item::ShotVector{}) return 0;
        for (const auto axis : view.direction) lua.set_number(axis);
        if (!view.eye || !view.target) return 3;
        for (const auto axis : *view.eye) lua.set_number(axis);
        for (const auto axis : *view.target) lua.set_number(axis);
        if (!view.muzzle) return 9;
        for (const auto axis : *view.muzzle) lua.set_number(axis);
        if (!view.contact || view.contact_actor.empty()) return 12;
        lua.set_string(view.contact_actor);
        for (const auto axis : *view.contact) lua.set_number(axis);
        return 16;
    }

    int take(const Lua& lua, bool host, bool peek = false)
    {
        if (!available() || scope.seat.host != host || !count || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto pawn = lua.get_integer();
        const auto now = Clock::now();
        if (pawn != address(scope.seat.pawn.Get()) || !qualified(scope, now)) return 0;
        if (!can_act(scope.seat))
        {
            // Pending taps cannot fire when a temporary weapon lock expires.
            head = count = 0;
            return 0;
        }
        discard_expired(now);
        if (!count) return 0;
        const auto& receipt = receipts[head];
        lua.set_integer(static_cast<std::int64_t>(receipt.sequence));
        lua.set_integer(pawn);
        lua.set_string(scope.text);
        lua.set_string(scope.item->kind);
        if (!host) lua.set_bool(receipt.underbarrel);
        const int fields = (host ? 4 : 5) + push_shot_view(lua, receipt.view);
        if (!peek)
        {
            head = (head + 1) % receipt_limit;
            --count;
        }
        return fields;
    }

    int read_aim(const Lua& lua)
    {
        auto* pawn = scope.seat.pawn.Get();
        if (!available() || scope.seat.host || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            lua.get_integer() != address(pawn)) return 0;
        if (scope.aiming && (!qualified(scope, Clock::now()) || !can_act(scope.seat) ||
            !false_field(pawn, STR("Sprinting")))) scope.aiming = false;
        lua.set_bool(scope.aiming);
        return 1;
    }

    int take_held(const Lua& lua, bool melee_edges, bool peek = false)
    {
        if (!available() || !(melee_edges ? scoped_melee_edges() : scoped_held_fire()) ||
            lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto pawn = lua.get_integer();
        if (pawn != address(scope.seat.pawn.Get())) return 0;
        const auto now = Clock::now();
        check_held(now);
        discard_held(now);
        if (!held_count || !current_player(scope.seat)) return 0;
        const auto& event = held_receipts[held_head];
        lua.set_integer(static_cast<std::int64_t>(event.receipt.sequence));
        lua.set_integer(pawn);
        lua.set_string(scope.text);
        lua.set_string(event.edge == HeldEdge::press ? "press" : event.edge == HeldEdge::release ? "release" : "cancel");
        lua.set_integer(static_cast<std::int64_t>(event.stroke));
        if (melee_edges) lua.set_integer(event.duration_ms);
        const int fields = melee_edges ? 6 : 5 + push_shot_view(lua, event.view);
        if (!peek)
        {
            held_head = (held_head + 1) % receipt_limit;
            --held_count;
        }
        return fields;
    }

    int commit_guest_input(const Lua& lua)
    {
        if (!available() || scope.seat.host || lua.get_stack_size() != 3 || !lua.is_integer(1) ||
            !lua.is_integer(2) || !lua.is_string(3)) return 0;
        const auto pawn = lua.get_integer();
        const auto sequence = lua.get_integer();
        const auto channel = lua.get_string();
        bool committed = false;
        if (pawn == address(scope.seat.pawn.Get()) && sequence > 0)
        {
            const auto number = static_cast<std::uint64_t>(sequence);
            if (channel == "tap" && count && receipts[head].sequence == number)
            {
                head = (head + 1) % receipt_limit;
                --count;
                committed = true;
            }
            else if (channel == "held" && scoped_held_fire() && held_count && held_receipts[held_head].receipt.sequence == number)
            {
                held_head = (held_head + 1) % receipt_limit;
                --held_count;
                committed = true;
            }
        }
        lua.set_bool(committed);
        return 1;
    }

    int read_held(const Lua& lua)
    {
        if (!available() || !scoped_held_fire() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto pawn = lua.get_integer();
        if (pawn != address(scope.seat.pawn.Get())) return 0;
        check_held(Clock::now());
        if (!held.id) return 0;
        lua.set_integer(static_cast<std::int64_t>(held.id));
        lua.set_integer(pawn);
        lua.set_string(scope.text);
        lua.set_string(held.proof == HeldProof::mapped_key ? "mapped-key" :
            held.proof == HeldProof::edge_window ? "edge-window" : "headless-fixture");
        return 4;
    }

    int sample_held_view(const Lua& lua)
    {
        if (!available() || !scoped_held_fire() || !held.id || lua.get_stack_size() != 1 ||
            !lua.is_integer(1) || lua.get_integer() != address(scope.seat.pawn.Get())) return 0;
        const auto now = Clock::now();
        if (now < held.next_view) return 0;
        check_held(now);
        if (!held.id) return 0;
        held.next_view = now + std::chrono::milliseconds{80};
        const auto view = silverfish::native_guest_item::capture_shot_view(scope.seat.pawn.Get(), scope.seat.held.Get());
        if (view.direction == silverfish::native_guest_item::ShotVector{}) return 0;
        lua.set_integer(static_cast<std::int64_t>(held.id));
        return 1 + push_shot_view(lua, view);
    }

    int clear(bool host)
    {
        if (U::IsInGameThreadRaw() && scope.seat.host == host)
        {
            scope = {};
            head = count = 0;
            held = {};
            held_head = held_count = 0;
            // A consumed Started may still need its matching physical release.
            // This one old, exact stroke cannot enqueue another shot.
        }
        return 0;
    }

    struct FixtureMode
    {
        explicit FixtureMode(FixtureDispatch value) { fixture_dispatch = value; }
        ~FixtureMode() { fixture_dispatch = FixtureDispatch::none; }
        FixtureMode(const FixtureMode&) = delete;
        FixtureMode& operator=(const FixtureMode&) = delete;
    };

    struct ValueFactory
    {
        U::UObject* library{};
        U::UFunction* function{};
        U::FDoubleProperty* x{};
        U::FStructProperty* output{};

        bool bind(U::UObject* action, U::UScriptStruct* value_type)
        {
            library = find<U::UObject>(STR("/Script/EnhancedInput.Default__EnhancedInputLibrary"));
            function = find<U::UFunction>(STR("/Script/EnhancedInput.EnhancedInputLibrary:MakeInputActionValueOfType"));
            if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !function ||
                function->GetOuterPrivate() != library->GetClassPrivate() || !function->HasAnyFunctionFlags(U::FUNC_Native) ||
                function->HasAnyFunctionFlags(U::FUNC_Net) || function->GetNumParms() != 5 ||
                function->GetParmsSize() != 0x40) return false;
            x = input<U::FDoubleProperty>(function, STR("X"), 0);
            output = field<U::FStructProperty>(function, STR("ReturnValue"));
            auto* type = find<U::UEnum>(STR("/Script/EnhancedInput.EInputActionValueType"));
            auto* value = input<U::FEnumProperty>(function, STR("ValueType"), 0x18);
            auto* action_value = field<U::FEnumProperty>(action, STR("ValueType"));
            auto* number = action_value ? action_value->GetUnderlyingProperty() : nullptr;
            return x && input<U::FDoubleProperty>(function, STR("Y"), 8) &&
                input<U::FDoubleProperty>(function, STR("Z"), 0x10) && type &&
                value && value->GetEnum().Get() == type && value->GetSize() == 1 &&
                action_value && action_value->GetEnum().Get() == type && number && number->GetSize() == 1 &&
                number->GetUnsignedIntPropertyValue(action_value->ContainerPtrToValuePtr<void>(action)) == 0 &&
                output && output == function->GetReturnProperty() && output->GetOffset_Internal() == 0x20 &&
                output->GetSize() == 0x20 && output->GetStruct().Get() == value_type;
        }

        void dispatch(U::UObject* pawn, U::UObject* action, const Wrapper& wrapper, bool down) const
        {
            alignas(std::max_align_t) std::array<std::byte, 0x40> packed{};
            alignas(std::max_align_t) std::array<std::byte, 0x30> input{};
            ReflectedValue generated(output, packed.data());
            ReflectedValue value(wrapper.value, input.data());
            x->SetPropertyValueInContainer(packed.data(), down ? 1.0 : 0.0);
            library->ProcessEvent(function, packed.data());
            wrapper.value->CopyCompleteValue(wrapper.value->ContainerPtrToValuePtr<void>(input.data()),
                output->ContainerPtrToValuePtr<void>(packed.data()));
            wrapper.elapsed->SetPropertyValueInContainer(input.data(), down ? 0.0f : 0.05f);
            wrapper.triggered->SetPropertyValueInContainer(input.data(), down ? 0.0f : 0.05f);
            wrapper.source->SetObjectPropertyValue(wrapper.source->ContainerPtrToValuePtr<void>(input.data()), action);
            pawn->ProcessEvent(static_cast<U::UFunction*>(wrapper.function.Get()), input.data());
        }
    };

    int probe(const Lua& lua, bool host)
    {
        if (!available() || scope.seat.host != host || !fixtures(host) || lua.get_stack_size() != 1 || !lua.is_integer(1))
            return result(lua, false, "native_guest_primary_fixture_unavailable");
        const auto now = Clock::now();
        auto* pawn = scope.seat.pawn.Get();
        auto* action = scope.action.Get();
        if (lua.get_integer() != address(pawn) || !qualified(scope, now) || !can_act(scope.seat))
            return result(lua, false, "native_guest_primary_fixture_scope_unavailable");
        discard_expired(now);
        if (count || (press.seat.pawn.Get() && now < press.expires) || sequence == max_sequence)
            return result(lua, false, "native_guest_primary_fixture_input_busy");
        const bool toggle = scope.item == &crowbar;
        const auto& first = toggle ? scope.completed : scope.started;
        ValueFactory factory;
        if (!factory.bind(action, first.value->GetStruct().Get()))
            return result(lua, false, "native_guest_primary_fixture_value_unavailable");
        const FixtureMode fixture(FixtureDispatch::tap);
        const auto before = sequence;
        if (!toggle)
        {
            factory.dispatch(pawn, action, first, true);
            if (press.seat.pawn.Get() != pawn || !qualified(scope, Clock::now()))
                return result(lua, false, "native_guest_primary_fixture_start_unverified");
            if (scope.item->action_on_release && (sequence != before || count != 0))
                return result(lua, false, "native_guest_primary_fixture_fired_before_release");
        }
        factory.dispatch(pawn, action, scope.completed, false);
        if (press.seat.pawn.Get() || sequence != before + 1 || count != 1)
            return result(lua, false, "native_guest_primary_fixture_release_unverified");
        result(lua, true, "stock_pressed");
        lua.set_integer(static_cast<std::int64_t>(sequence));
        lua.set_integer(toggle ? 1 : 2);
        return 4;
    }

    int probe_held(const Lua& lua, bool melee_edges)
    {
        if (!available() || !(melee_edges ? scoped_melee_edges() : scoped_held_fire()) ||
            !fixtures(false) || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_string(2))
            return result(lua, false, "native_guest_held_fire_fixture_unavailable");
        const auto pawn_address = lua.get_integer();
        const auto edge = lua.get_string();
        const auto now = Clock::now();
        auto* pawn = scope.seat.pawn.Get();
        auto* action = scope.action.Get();
        if (pawn_address != address(pawn) || (edge != "press" && edge != "release") ||
            !qualified(scope, now) || !can_act(scope.seat))
            return result(lua, false, "native_guest_held_fire_fixture_scope_unavailable");
        check_held(now);
        discard_held(now);
        const bool starts = edge == "press";
        if (starts && !melee_edges && !stock_held_fire_selected())
            return result(lua, false, "native_guest_held_fire_fixture_requires_automatic_mode");
        if (held_count || count || fixture_dispatch != FixtureDispatch::none ||
            (starts ? held.id != 0 || (press.seat.pawn.Get() && now < press.expires) :
                held.id == 0 || held.proof != HeldProof::fixture || press.stroke != held.id))
            return result(lua, false, "native_guest_held_fire_fixture_input_busy");
        ValueFactory factory;
        if (!factory.bind(action, scope.started.value->GetStruct().Get()))
            return result(lua, false, "native_guest_held_fire_fixture_value_unavailable");
        const FixtureMode fixture(FixtureDispatch::held);
        const auto before = held_sequence;
        const auto stroke = starts ? before + 1 : held.id;
        factory.dispatch(pawn, action, starts ? scope.started : scope.completed, starts);
        if (held_sequence != before + 1 || held_count != 1 ||
            (starts ? held.id != stroke || press.stroke != stroke : held.id != 0 || press.seat.pawn.Get()))
            return result(lua, false, "native_guest_held_fire_fixture_edge_unverified");
        result(lua, true, starts ? "stock_press" : "stock_release");
        lua.set_integer(static_cast<std::int64_t>(held_sequence));
        lua.set_integer(static_cast<std::int64_t>(stroke));
        return 4;
    }

    int equip_host_fixture(const Lua& lua)
    {
        if (!available() || !host_enabled() || !fixtures(true) || lua.get_stack_size() != 5 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5))
            return result(lua, false, "stock_host_camera_fixture_unavailable");
        const auto pawn_address = lua.get_integer();
        const auto controller_address = lua.get_integer();
        const auto world_address = lua.get_integer();
        Scope next;
        next.seat.host = true;
        next.item = &camera;
        if (pawn_address <= 0 || controller_address <= 0 || world_address <= 0 ||
            !parse_guid(lua.get_string(), next.guid) || lua.get_string() != camera_quest)
            return result(lua, false, "stock_host_camera_fixture_identity_unqualified");
        if (const auto failure = bind(next, pawn_address, controller_address, world_address, true); !failure.empty())
            return result(lua, false, failure);
        if (!can_act(next.seat)) return result(lua, false, "stock_host_camera_fixture_pawn_unavailable");

        auto* pawn = next.seat.pawn.Get();
        auto* stock = next.seat.stock.Get();
        auto* klass = find<U::UClass>(camera_path);
        auto* selected = field<U::FIntProperty>(stock, STR("PlayerEquipIndex"));
        ItemRow schema;
        if (!klass || !selected || !schema.bind() || !schema.ammo) return result(lua, false, "stock_host_camera_inventory_unavailable");
        auto* backpack = schema.array(stock, STR("PlayerBackpack"));
        auto* loadout = schema.array(stock, STR("PlayerEquipment"));
        if (!backpack || !loadout) return result(lua, false, "stock_host_camera_inventory_unavailable");
        U::FScriptArrayHelper bag(backpack, backpack->ContainerPtrToValuePtr<void>(stock));
        U::FScriptArrayHelper slots(loadout, loadout->ContainerPtrToValuePtr<void>(stock));
        const auto bag_count = bag.Num();
        const auto slot_count = slots.Num();
        const auto previous = selected->GetPropertyValueInContainer(stock);
        if (bag_count < 0 || bag_count > 4096 || slot_count < 1 || slot_count > 4096)
            return result(lua, false, "stock_host_camera_inventory_unavailable");

        int source_index = -1, target_index = -1, empty_slot = -1;
        for (int i = 0; i < bag_count; ++i)
        {
            const auto* row = bag.GetRawPtr(i);
            if (schema.identity(row) != next.guid) continue;
            if (source_index >= 0 || schema.row_class(row) != klass)
                return result(lua, false, "stock_host_camera_guid_conflict");
            source_index = i;
        }
        for (int i = 0; i < slot_count; ++i)
        {
            const auto* row = slots.GetRawPtr(i);
            if (schema.identity(row) == next.guid)
            {
                if (source_index >= 0 || target_index >= 0 || schema.row_class(row) != klass)
                    return result(lua, false, "stock_host_camera_guid_conflict");
                target_index = i;
            }
            else if (empty_slot < 0 && i != previous && !schema.row_class(row) && schema.identity(row) == Guid{})
                empty_slot = i;
        }
        if (target_index < 0 && (source_index < 0 || empty_slot < 0))
            return result(lua, false, "stock_host_camera_owned_row_or_empty_slot_unavailable");
        auto* held = next.seat.held.Get();
        if (target_index >= 0 && target_index == previous && live(held) && matching_local_item(next))
            return result(lua, true, "stock_camera_equipped");
        if (live(held) && (previous < 0 || previous >= slot_count || target_index == previous ||
            schema.row_class(slots.GetRawPtr(previous)) != held->GetClassPrivate() ||
            object_field(held, STR("Owner")) != pawn || object_field(held, STR("Instigator")) != pawn))
            return result(lua, false, "stock_host_camera_previous_equipment_unqualified");
        const auto* item = source_index >= 0 ? bag.GetRawPtr(source_index) : slots.GetRawPtr(target_index);
        const auto loaded = schema.ammo->GetPropertyValueInContainer(item);
        if (loaded != -1 && loaded != 0) return result(lua, false, "stock_host_camera_item_unqualified");

        auto* can_equip = find<U::UFunction>(STR("/Game/Classes/Player/Player_FP.Player_FP_C:Check_CanUseEquipment"));
        auto* allowed = field<U::FBoolProperty>(can_equip, STR("ReturnValue"));
        auto* equip = find<U::UFunction>(STR("/Game/Classes/Player/Player_FP.Player_FP_C:Equip_NextItem"));
        struct Parameters { std::int32_t index{}; bool prev{}; } parameters;
        auto* index = input<U::FIntProperty>(equip, STR("Index"), offsetof(Parameters, index));
        auto* prev = input<U::FBoolProperty>(equip, STR("Prev"), offsetof(Parameters, prev));
        if (!allowed || allowed != can_equip->GetReturnProperty() || allowed->GetOffset_Internal() != 0 ||
            can_equip->GetOuterPrivate() != pawn->GetClassPrivate() || can_equip->GetNumParms() != 1 ||
            can_equip->GetParmsSize() != sizeof(bool) || !index || !prev || equip->GetReturnProperty() ||
            equip->GetOuterPrivate() != pawn->GetClassPrivate() || equip->GetNumParms() != 2 ||
            equip->GetParmsSize() < offsetof(Parameters, prev) + 1 || equip->GetParmsSize() > sizeof(Parameters) ||
            equip->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
            return result(lua, false, "stock_host_camera_equip_schema_unavailable");
        bool eligible{};
        pawn->ProcessEvent(can_equip, &eligible);
        if (!eligible || !current_player(next.seat)) return result(lua, false, "stock_host_camera_equip_refused");

        if (source_index >= 0)
        {
            target_index = empty_slot;
            U::CastField<U::FStructProperty>(loadout->GetInner())->CopyCompleteValue(slots.GetRawPtr(target_index), item);
            bag.RemoveValues(source_index, 1);
        }
        index->SetPropertyValueInContainer(&parameters, target_index);
        prev->SetPropertyValueInContainer(&parameters, false);
        pawn->ProcessEvent(equip, &parameters);
        next.seat.held = object_field(pawn, STR("EquipedItem"));
        next.seat.mirrored = next.seat.held.Get() != nullptr;
        auto* actor_ammo = field<U::FIntProperty>(next.seat.held.Get(), STR("Ammo"));
        if (!current_player(next.seat) || selected->GetPropertyValueInContainer(stock) != target_index ||
            !matching_local_item(next) || slots.Num() != slot_count || bag.Num() != bag_count - (source_index >= 0 ? 1 : 0) ||
            !actor_ammo || actor_ammo->GetPropertyValueInContainer(next.seat.held.Get()) != loaded ||
            schema.ammo->GetPropertyValueInContainer(slots.GetRawPtr(target_index)) != loaded)
            return result(lua, false, "mutation_unverified_stock_host_camera_equip");
        return result(lua, true, "stock_camera_equipped");
    }
}

namespace silverfish::native_guest_primary_input
{
    bool matches_stock_spade_started(U::UObject* pawn, U::UFunction* function, const void* arguments)
    {
        static const U::FName name{STR("InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36")};
        if (!U::IsInGameThreadRaw() || !function || function->GetFName() != name || !arguments ||
            !live(pawn) || pawn->GetClassPrivate() != find<U::UClass>(pawn_path)) return false;
        auto* action = find<U::UObject>(action_path);
        auto* type = find<U::UClass>(STR("/Script/EnhancedInput.InputAction"));
        if (!live(action) || !type || action->GetClassPrivate() != type) return false;
        const auto wrapper = bind_wrapper(pawn->GetClassPrivate(), action, started_path, 2);
        return function == wrapper.function.Get() && verifies_source(wrapper, action, arguments);
    }

    bool dispatch_crossroads_spade_fixture(RC::Unreal::UObject* pawn, RC::Unreal::UObject* spade, bool down)
    {
        if (!U::IsInGameThreadRaw() || !env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") ||
            !env("SILVERFISH_COOP_NATIVE_ZONE") || !env("SILVERFISH_COOP_NATIVE_FIXTURES") || !env("SILVERFISH_COOP_HEADLESS") ||
            !live(pawn) || !live(spade) || pawn->GetClassPrivate() != find<U::UClass>(pawn_path) ||
            spade->GetClassPrivate() != find<U::UClass>(STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C")) ||
            object_field(pawn, STR("EquipedItem")) != spade) return false;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        auto* role = field<U::FByteProperty>(pawn, STR("Role"));
        if (!live(world) || world->GetPathName() != STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P") ||
            !role || role->GetPropertyValueInContainer(pawn) != 3) return false;
        auto* action = find<U::UObject>(action_path);
        auto* type = find<U::UClass>(STR("/Script/EnhancedInput.InputAction"));
        if (!live(action) || !type || action->GetClassPrivate() != type) return false;
        const auto wrapper = bind_wrapper(pawn->GetClassPrivate(), action, down ? started_path : completed_path, down ? 2 : 16);
        ValueFactory factory;
        if (!wrapper.function.Get() || !factory.bind(action, wrapper.value->GetStruct().Get())) return false;
        factory.dispatch(pawn, action, wrapper, down);
        return true;
    }

    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            intercept, {false, false, owner, STR("Route stock guest primary input")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, U::UObject* pawn, U::FFrame& frame, void*) {
                if (frame.Object() == pawn) intercept(iteration, pawn, frame.Node(), frame.Locals());
            }, {false, false, owner, STR("Route Blueprint guest primary input")});
        if (callbacks[0] == Hook::ERROR_ID || callbacks[1] == Hook::ERROR_ID)
        {
            uninstall_hooks();
            return;
        }
        protect_host_camera.store(host_enabled(), std::memory_order_release);
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        protect_host_camera.store(false, std::memory_order_release);
        clear_scope();
        for (auto& callback : callbacks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishNativeGameWindowFocused", [](const Lua& state) {
            state.set_bool(state.get_stack_size() == 0 && U::IsInGameThreadRaw() && local_game_focused());
            return 1;
        });
        if (!enabled()) return;
        clear_scope();
        if (host_enabled())
        {
            lua.register_function("SilverfishSetNativeHostCameraScope", [](const Lua& lua) { return set_scope(lua, true); });
            lua.register_function("SilverfishTakeNativeHostCameraIntent", [](const Lua& lua) { return take(lua, true); });
            lua.register_function("SilverfishClearNativeHostCameraScope", [](const Lua&) { return clear(true); });
            if (fixtures(true))
            {
                lua.register_function("SilverfishProbeNativeHostCameraInput", [](const Lua& lua) { return probe(lua, true); });
                lua.register_function("SilverfishEquipNativeHostCameraFixture", equip_host_fixture);
            }
        }
        if (guest_enabled())
        {
            lua.register_function("SilverfishSetNativeGuestPrimaryScope", [](const Lua& lua) { return set_scope(lua, false); });
            lua.register_function("SilverfishTakeNativeGuestPrimaryIntent", [](const Lua& lua) { return take(lua, false); });
            lua.register_function("SilverfishPeekNativeGuestPrimaryIntent", [](const Lua& lua) { return take(lua, false, true); });
            lua.register_function("SilverfishCommitNativeGuestPrimaryInput", commit_guest_input);
            lua.register_function("SilverfishTakeNativeGuestMeleeInput", [](const Lua& lua) { return take_held(lua, true); });
            lua.register_function("SilverfishClearNativeGuestPrimaryScope", [](const Lua&) { return clear(false); });
            lua.register_function("SilverfishReadNativeGuestAim", read_aim);
            if (fixtures(false))
            {
                lua.register_function("SilverfishProbeNativeGuestPrimaryInput", [](const Lua& lua) { return probe(lua, false); });
                lua.register_function("SilverfishProbeNativeGuestMeleeInput", [](const Lua& lua) { return probe_held(lua, true); });
            }
            if (held_enabled())
            {
                lua.register_function("SilverfishTakeNativeGuestHeldFireIntent", [](const Lua& lua) { return take_held(lua, false); });
                lua.register_function("SilverfishPeekNativeGuestHeldFireIntent", [](const Lua& lua) { return take_held(lua, false, true); });
                lua.register_function("SilverfishReadNativeGuestHeldFire", read_held);
                lua.register_function("SilverfishSampleNativeGuestHeldAim", sample_held_view);
                if (fixtures(false))
                    lua.register_function("SilverfishProbeNativeGuestHeldFire", [](const Lua& lua) { return probe_held(lua, false); });
            }
        }
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (!U::IsInGameThreadRaw()) return;
        if (press.local_melee)
            silverfish::native_guest_melee::present_local_input(press.seat.pawn.Get(), press.seat.held.Get(), false);
        scope = {};
        press = {};
        head = count = 0;
        held = {};
        held_head = held_count = 0;
    }
}
