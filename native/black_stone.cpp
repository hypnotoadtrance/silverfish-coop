#include "black_stone.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>
#include <utility>
#include <vector>

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
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P");
    constexpr auto stone_path = STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P:PersistentLevel.Art_BlackStone_C_1");
    constexpr auto stone_class = STR("/Game/Classes/Items/Artifacts/Art_BlackStone.Art_BlackStone_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto interact_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto put_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:PutInInventory");
    constexpr auto end_play_path = STR("/Game/Classes/Items/Artifacts/Art_BlackStone.Art_BlackStone_C:Test");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_script = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto row_schema = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto scope_lease = std::chrono::milliseconds(1250);
    constexpr auto verification_lease = std::chrono::milliseconds(600);
    constexpr auto receipt_lease = std::chrono::seconds(3);
    constexpr int max_inventory = 4096;

    enum class Entry { None, StockF, Fixture };

    struct InteractionArgs
    {
        Unreal::UObject* player{};
        Unreal::UObject* component{};
    };

    struct PriorRow
    {
        Guid guid{};
        Unreal::UObject* item_class{};
        std::int32_t ammo{};
        std::int32_t value{};
        double weight{};

        bool operator==(const PriorRow&) const = default;
    };

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr save;
        Unreal::FWeakObjectPtr stone;
        Unreal::UFunction* interaction{};
        Address stone_address{};
        bool host{};
        bool accepting{};
        bool fixture{};
        bool fixture_script_seen{};
        Entry attempt{};
        const Unreal::FFrame* frame{};
        bool saw_put{};
        bool saw_add{};
        bool saw_destroyed{};
        bool verifying{};
        Clock::time_point verify_expires{};
        std::int32_t ammo{};
        std::vector<PriorRow> prior;
        Entry receipt{};
        Guid guid{};
        Clock::time_point receipt_expires{};
        std::string_view status{"unbound"};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    std::array<Hook::GlobalCallbackId, 3> callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};
    thread_local Scope scope;

    bool env(const char* key)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), key) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled() { return env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_TRANSPORT"); }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* value = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    template <typename T>
    T* input(Unreal::UFunction* function, const Unreal::TCHAR* name, std::size_t offset)
    {
        auto* value = field<T>(function, name);
        return value && value->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
            !value->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) &&
            value->GetOffset_Internal() == static_cast<int>(offset) ? value : nullptr;
    }

    Unreal::UObject* read_object(Unreal::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    Unreal::UObject* object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        return read_object(field<Unreal::FObjectProperty>(owner, name), owner);
    }

    bool boolean(Unreal::UObject* owner, const Unreal::TCHAR* name, bool expected)
    {
        auto* value = field<Unreal::FBoolProperty>(owner, name);
        return value && value->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool authority(Unreal::UObject* actor)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    bool placed_stone(Unreal::UObject* actor)
    {
        static const Unreal::FName name{STR("Art_BlackStone_C")};
        auto* type = actor ? actor->GetClassPrivate() : nullptr;
        return type && type->GetFName() == name && type->GetPathName() == stone_class && actor->GetPathName() == stone_path;
    }

    bool current_owner(const Scope& current)
    {
        auto* pawn = current.pawn.Get();
        auto* controller = current.controller.Get();
        auto* player = current.player.Get();
        auto* world = current.world.Get();
        auto* instance = current.instance.Get();
        auto* driver = current.driver.Get();
        if (!live(pawn) || !live(controller) || !live(player) || !live(world) || !live(instance) || !live(driver) ||
            static_cast<Unreal::AActor*>(pawn)->GetWorld() != world || static_cast<Unreal::AActor*>(controller)->GetWorld() != world ||
            object(pawn, STR("Controller")) != controller || object(controller, STR("Pawn")) != pawn ||
            object(controller, STR("Player")) != player || object(world, STR("OwningGameInstance")) != instance ||
            object(world, STR("NetDriver")) != driver || object(driver, STR("World")) != world ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection"))) return false;

        auto* local_players = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* local_entry = local_players ? Unreal::CastField<Unreal::FObjectProperty>(local_players->GetInner()) : nullptr;
        if (!local_entry) return false;
        Unreal::FScriptArrayHelper players(local_players, local_players->ContainerPtrToValuePtr<void>(instance));
        if (players.Num() != 1 || local_entry->GetObjectPropertyValue(players.GetRawPtr()) != player) return false;

        auto* server = object(driver, STR("ServerConnection"));
        if (!current.host) return live(server);
        auto* save = current.save.Get();
        return !server && authority(pawn) && authority(controller) && live(save) &&
            boolean(pawn, STR("Dead"), false) && boolean(controller, STR("Loading"), false) &&
            object(instance, STR("SaveSlot_Ref")) == save && object(pawn, STR("Save")) == save;
    }

    bool exact_placed_world(Unreal::UObject* stone, Unreal::UObject* world)
    {
        return placed_stone(stone) && live(stone) && static_cast<Unreal::AActor*>(stone)->GetWorld() == world &&
            static_cast<Unreal::AActor*>(stone)->GetLevel() == object(world, STR("PersistentLevel")) &&
            boolean(stone, STR("IsDisarmed"), true) && boolean(stone, STR("Physics"), false);
    }

    bool configure(Address pawn_address, Address stone_address, bool host, bool accepting)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && address(scope.pawn.Get()) == pawn_address && scope.host == host &&
            (stone_address == scope.stone_address || (stone_address == 0 &&
                (scope.verifying || scope.saw_destroyed || !live(scope.stone.Get())))))
        {
            if (!current_owner(scope) || (stone_address && !exact_placed_world(scope.stone.Get(), scope.world.Get()))) return false;
            scope.expires = Clock::now() + scope_lease;
            scope.accepting = accepting;
            return true;
        }

        auto* world = find<Unreal::UWorld>(world_path);
        auto* stone = find<Unreal::UObject>(stone_path);
        auto* pawn_type = find<Unreal::UClass>(pawn_class);
        if (!stone_address || !live(world) || !pawn_type ||
            address(stone) != stone_address || !exact_placed_world(stone, world)) return false;

        Scope next;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == pawn_type) next.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* controller = object(next.pawn.Get(), STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* save = object(instance, STR("SaveSlot_Ref"));
        auto* player_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!live(controller) || controller->GetClassPrivate() != find<Unreal::UClass>(controller_class) ||
            !live(instance) || instance->GetClassPrivate() != find<Unreal::UClass>(instance_class) ||
            !live(player) || !player_type || !player->IsA(player_type) ||
            (host && (!live(save) || save->GetClassPrivate() != find<Unreal::UClass>(save_class)))) return false;

        next.controller = controller;
        next.player = player;
        next.world = world;
        next.instance = instance;
        next.driver = object(world, STR("NetDriver"));
        next.save = save;
        next.stone = stone;
        next.stone_address = stone_address;
        next.host = host;
        next.accepting = accepting;
        next.interaction = find<Unreal::UFunction>(interact_path);
        if (!current_owner(next) || !next.interaction) return false;
        next.generation = epoch;
        next.expires = Clock::now() + scope_lease;
        next.status = "armed";
        scope = std::move(next);
        return true;
    }

    struct Backpack
    {
        Unreal::FArrayProperty* array{};
        Unreal::FClassProperty* item{};
        Unreal::FIntProperty* ammo{};
        Unreal::FIntProperty* value{};
        Unreal::FDoubleProperty* weight{};
        Unreal::FStructProperty* guid{};
        std::array<Unreal::FIntProperty*, 4> words{};

        bool read(Unreal::UObject* save)
        {
            array = field<Unreal::FArrayProperty>(save, STR("PlayerBackpack"));
            auto* element = array ? Unreal::CastField<Unreal::FStructProperty>(array->GetInner()) : nullptr;
            auto* type = element ? element->GetStruct().Get() : nullptr;
            auto* guid_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            if (!type || type != find<Unreal::UScriptStruct>(row_schema) || !guid_type) return false;
            item = field<Unreal::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
            ammo = field<Unreal::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
            value = field<Unreal::FIntProperty>(type, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
            weight = field<Unreal::FDoubleProperty>(type, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
            guid = field<Unreal::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
            if (!item || !ammo || !value || !weight || !guid || guid->GetStruct().Get() != guid_type) return false;
            constexpr std::array names{STR("A"), STR("B"), STR("C"), STR("D")};
            for (std::size_t index = 0; index < words.size(); ++index)
                words[index] = field<Unreal::FIntProperty>(guid_type, names[index]);
            return std::ranges::all_of(words, [](auto* value) { return value != nullptr; });
        }

        Guid identity(const void* row) const
        {
            const auto* value = guid->ContainerPtrToValuePtr<void>(row);
            Guid id{};
            for (std::size_t index = 0; index < words.size(); ++index)
                id[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(value));
            return id;
        }

        Unreal::UObject* item_type(const void* row) const
        {
            return item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(row));
        }

        PriorRow stamp(const void* row) const
        {
            return {identity(row), item_type(row), ammo->GetPropertyValueInContainer(row),
                value->GetPropertyValueInContainer(row), weight->GetPropertyValueInContainer(row)};
        }
    };

    bool snapshot(Unreal::UObject* stone)
    {
        auto* save = scope.save.Get();
        auto* ammo = field<Unreal::FIntProperty>(stone, STR("Ammo"));
        auto* stacks = field<Unreal::FBoolProperty>(stone, STR("Stacks"));
        Backpack bag;
        if (!ammo || !stacks || !bag.read(save)) return false;
        Unreal::FScriptArrayHelper rows(bag.array, bag.array->ContainerPtrToValuePtr<void>(save));
        if (rows.Num() >= max_inventory) return false;
        scope.prior.clear();
        scope.prior.reserve(static_cast<std::size_t>(rows.Num()));
        const bool may_stack = stacks->GetPropertyValueInContainer(stone);
        for (int index = 0; index < rows.Num(); ++index)
        {
            const auto* row = rows.GetRawPtr(index);
            if (may_stack && bag.item_type(row) == stone->GetClassPrivate()) return false;
            scope.prior.push_back(bag.stamp(row));
        }
        scope.ammo = ammo->GetPropertyValueInContainer(stone);
        return true;
    }

    bool pickup_owner_current()
    {
        return generation.load(std::memory_order_acquire) == scope.generation && current_owner(scope);
    }

    std::string_view added_stone(Guid& identity)
    {
        auto* save = scope.save.Get();
        auto* type = find<Unreal::UClass>(stone_class);
        Backpack bag;
        if (!type || !bag.read(save)) return "stock_pickup_backpack_schema_unavailable";
        Unreal::FScriptArrayHelper rows(bag.array, bag.array->ContainerPtrToValuePtr<void>(save));
        if (rows.Num() != static_cast<int>(scope.prior.size()) + 1) return "stock_pickup_backpack_count_unverified";
        for (int index = 0; index < rows.Num() - 1; ++index)
        {
            if (bag.stamp(rows.GetRawPtr(index)) != scope.prior[static_cast<std::size_t>(index)])
                return "stock_pickup_prior_backpack_changed";
        }
        const auto* added = rows.GetRawPtr(rows.Num() - 1);
        identity = bag.identity(added);
        if (bag.item_type(added) != type || bag.ammo->GetPropertyValueInContainer(added) != scope.ammo ||
            identity == Guid{} || std::ranges::find(scope.prior, identity, &PriorRow::guid) != scope.prior.end())
            return "stock_pickup_typed_new_guid_unverified";
        return {};
    }

    void end_attempt(std::string_view status)
    {
        scope.attempt = Entry::None;
        scope.frame = nullptr;
        scope.verifying = false;
        scope.status = status;
    }

    void verify_consumed()
    {
        if (!scope.verifying) return;
        if (!pickup_owner_current()) return end_attempt("stock_pickup_owner_changed");

        const auto now = Clock::now();
        if (now >= scope.verify_expires) return end_attempt("stock_pickup_consumption_unverified");
        // The stock multicast can dispatch without exposing its Blueprint delegate to the hook.
        // This weak pointer has the original actor's index and serial, so a new actor cannot satisfy it.
        auto* stone = scope.stone.Get();
        if (!scope.saw_destroyed && live(stone) && !boolean(stone, STR("bActorIsBeingDestroyed"), true)) return;

        Guid current;
        if (const auto error = added_stone(current); !error.empty()) return end_attempt(error);
        if (current != scope.guid) return end_attempt("stock_pickup_guid_changed_before_consumption");

        scope.receipt = scope.attempt;
        scope.receipt_expires = Clock::now() + receipt_lease;
        end_attempt("pickup_verified");
    }

    bool unchanged_backpack()
    {
        auto* save = scope.save.Get();
        Backpack bag;
        if (!bag.read(save)) return false;
        Unreal::FScriptArrayHelper rows(bag.array, bag.array->ContainerPtrToValuePtr<void>(save));
        if (rows.Num() != static_cast<int>(scope.prior.size())) return false;
        for (int index = 0; index < rows.Num(); ++index)
            if (bag.stamp(rows.GetRawPtr(index)) != scope.prior[static_cast<std::size_t>(index)]) return false;
        return true;
    }

    bool complete_stock_put()
    {
        // The authored stone has stock ItemType 0 and a nonstacking stock backpack branch at 0xB79.
        // Re-enter exactly that helper only if the original wrapper never reached it or GameInstance Add.
        auto* stone = scope.stone.Get();
        auto* function = find<Unreal::UFunction>(put_path);
        auto* category = field<Unreal::FByteProperty>(stone, STR("ItemType"));
        if (scope.saw_put || scope.saw_add || scope.saw_destroyed || !authority(stone) ||
            !exact_placed_world(stone, scope.world.Get()) || !category || category->GetPropertyValueInContainer(stone) != 0 ||
            !boolean(stone, STR("Stacks"), false) || !unchanged_backpack() || !function ||
            function->GetOuterPrivate() != find<Unreal::UClass>(item_class) || function->GetNumParms() != 0 ||
            function->GetParmsSize() != 0 || function->GetReturnProperty()) return false;
        stone->ProcessEvent(function, nullptr);
        return true;
    }

    void finish_pickup()
    {
        if (!pickup_owner_current()) return end_attempt("stock_pickup_owner_changed");
        Guid identity;
        auto error = added_stone(identity);
        if (error == "stock_pickup_backpack_count_unverified" && complete_stock_put())
        {
            if (!pickup_owner_current()) return end_attempt("stock_pickup_owner_changed");
            error = added_stone(identity);
            if (error == "stock_pickup_backpack_count_unverified") return end_attempt("stock_pickup_direct_stock_put_no_append");
        }
        if (!error.empty())
        {
            if (error == "stock_pickup_backpack_count_unverified")
            {
                if (scope.saw_add) return end_attempt("stock_pickup_stock_add_no_append");
                if (scope.saw_put) return end_attempt("stock_pickup_stock_put_no_append");
            }
            return end_attempt(error);
        }

        scope.guid = identity;
        scope.verifying = true;
        scope.frame = nullptr;
        scope.verify_expires = Clock::now() + verification_lease;
        scope.status = "pickup_verifying";
        verify_consumed();
    }

    bool bound()
    {
        return installed.load(std::memory_order_acquire) && scope.generation == generation.load(std::memory_order_acquire) &&
            scope.interaction && current_owner(scope);
    }

    bool nearby(Unreal::UObject* pawn, Unreal::UObject* stone)
    {
        auto* range = field<Unreal::FDoubleProperty>(pawn, STR("InteractionRange"));
        if (!range) return false;
        const auto maximum = range->GetPropertyValueInContainer(pawn) + 150.0;
        if (!std::isfinite(maximum) || maximum <= 150.0 || maximum > 1500.0) return false;
        auto first = static_cast<Unreal::AActor*>(pawn)->K2_GetActorLocation();
        auto second = static_cast<Unreal::AActor*>(stone)->K2_GetActorLocation();
        const auto dx = first.GetX() - second.GetX();
        const auto dy = first.GetY() - second.GetY();
        const auto dz = first.GetZ() - second.GetZ();
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(squared) && squared <= maximum * maximum;
    }

    Entry provenance(Unreal::FFrame* frame, Unreal::UObject* pawn, Unreal::UObject* stone, Unreal::UObject* hit)
    {
        if (!live(hit) || hit->GetOuterPrivate() != stone ||
            (hit != object(stone, STR("Box")) && hit != object(stone, STR("SM_Prop_Artifact_056")))) return Entry::None;
        if (!frame)
            return scope.fixture && hit == object(stone, STR("Box")) && nearby(pawn, stone) ? Entry::Fixture : Entry::None;

        auto* caller = frame->PreviousFrame();
        return caller && caller->Object() == pawn && caller->Node() == find<Unreal::UFunction>(pawn_script) &&
            object(pawn, STR("InteractionTarget_Component")) == hit && boolean(pawn, STR("Holding_Interact"), true)
            ? Entry::StockF : Entry::None;
    }

    bool admitted_stone_call(Unreal::UObject* item)
    {
        return scope.attempt != Entry::None && !scope.verifying && scope.stone.Get() == item && authority(item) &&
            pickup_owner_current() && Clock::now() < scope.expires;
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, const void* args,
                   Unreal::FFrame* frame)
    {
        if (!Unreal::IsInGameThreadRaw() || !context || !function || !installed.load(std::memory_order_acquire) ||
            iteration.OriginalFunctionCallPrevented()) return;
        static const Unreal::FName interact_name{STR("SendInteractionToActor")};
        static const Unreal::FName end_play_name{STR("Test")};
        static const Unreal::FName put_name{STR("PutInInventory")};
        static const Unreal::FName add_name{STR("AddItemToInventory")};
        const auto name = function->GetFName();
        if (name == add_name && function == find<Unreal::UFunction>(add_path))
        {
            auto* argument = input<Unreal::FObjectProperty>(function, STR("Item"), 0);
            auto* item = read_object(argument, args);
            if (!placed_stone(item)) return;
            auto* stacks = input<Unreal::FBoolProperty>(function, STR("TryStack"), sizeof(void*));
            if (!admitted_stone_call(item) || context != scope.instance.Get() || !stacks || stacks->GetPropertyValueInContainer(args))
            {
                iteration.PreventOriginalFunctionCall();
                return;
            }
            scope.saw_add = true;
            return;
        }
        if ((name != interact_name && name != end_play_name && name != put_name) || !placed_stone(context)) return;

        if (name == put_name && function == find<Unreal::UFunction>(put_path))
        {
            if (admitted_stone_call(context)) scope.saw_put = true;
            else iteration.PreventOriginalFunctionCall();
            return;
        }

        if (name == end_play_name && function == find<Unreal::UFunction>(end_play_path))
        {
            // Stock Test can append the stone on a level change even if nobody touched it.
            iteration.PreventOriginalFunctionCall();
            if (scope.attempt == Entry::None || scope.stone_address != address(context) ||
                scope.generation != generation.load(std::memory_order_acquire) || !args) return;
            auto* actor = input<Unreal::FObjectProperty>(function, STR("Actor"), 0);
            auto* reason = input<Unreal::FByteProperty>(function, STR("EndPlayReason"), sizeof(void*));
            if (actor && reason && read_object(actor, args) == context && reason->GetPropertyValueInContainer(args) == 0)
                scope.saw_destroyed = true;
            return;
        }
        if (name != interact_name || function != find<Unreal::UFunction>(interact_path)) return;

        // Explicitly deny both a guest replica calling its own stock F and an authority-side remote pawn.
        auto* player = input<Unreal::FObjectProperty>(function, STR("Player"), offsetof(InteractionArgs, player));
        auto* passed_hit = input<Unreal::FObjectProperty>(function, STR("HitComponent"), offsetof(InteractionArgs, component));
        auto* pawn = read_object(player, args);
        if (!scope.host || pawn != scope.pawn.Get())
        {
            scope.status = "guest_or_unknown_denied";
            iteration.PreventOriginalFunctionCall();
            return;
        }
        if (frame && scope.fixture && scope.attempt == Entry::Fixture && !scope.fixture_script_seen &&
            admitted_stone_call(context) && read_object(passed_hit, args) == object(context, STR("Box")))
        {
            // A direct ProcessEvent fixture may enter the same wrapper through the Blueprint VM.
            scope.fixture_script_seen = true;
            return;
        }
        if (!bound() || !scope.accepting || Clock::now() >= scope.expires || scope.stone.Get() != context ||
            !authority(context) || !exact_placed_world(context, scope.world.Get()) ||
            scope.attempt != Entry::None || scope.receipt != Entry::None || !player || !passed_hit ||
            function->GetOuterPrivate() != find<Unreal::UClass>(item_class) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != sizeof(InteractionArgs) || function->GetReturnProperty())
        {
            scope.status = "host_not_qualified";
            iteration.PreventOriginalFunctionCall();
            return;
        }
        const auto entry = provenance(frame, pawn, context, read_object(passed_hit, args));
        if (entry == Entry::None || !snapshot(context))
        {
            scope.status = "host_pickup_unqualified";
            iteration.PreventOriginalFunctionCall();
            return;
        }
        scope.saw_put = false;
        scope.saw_add = false;
        scope.saw_destroyed = false;
        scope.fixture_script_seen = false;
        scope.attempt = entry;
        scope.frame = frame;
        scope.status = "stock_pickup_pending";
    }

    void post_script(Iteration&, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (Unreal::IsInGameThreadRaw() && scope.attempt == Entry::StockF && scope.frame == &frame &&
            scope.stone_address == address(context) && scope.interaction == frame.Node()) finish_pickup();
    }

    int lua_set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) && lua.get_stack_size() == 4 &&
            lua.is_integer(1) && lua.is_integer(2) && lua.is_bool(3) && lua.is_bool(4))
        {
            const auto pawn = lua.get_integer();
            const auto stone = lua.get_integer();
            const auto host = lua.get_bool();
            const auto accepting = lua.get_bool();
            accepted = pawn > 0 && stone >= 0 && configure(static_cast<Address>(pawn), static_cast<Address>(stone), host, accepting);
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_take_receipt(const Lua& lua)
    {
        std::array<char, 32> guid{};
        std::string_view entry;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0)
        {
            verify_consumed();
            if (scope.receipt != Entry::None)
            {
                if (bound() && Clock::now() < scope.expires && Clock::now() < scope.receipt_expires)
                {
                    constexpr std::string_view digits{"0123456789ABCDEF"};
                    for (std::size_t word = 0; word < scope.guid.size(); ++word)
                        for (std::size_t digit = 0; digit < 8; ++digit)
                            guid[word * 8 + digit] = digits[(scope.guid[word] >> ((7 - digit) * 4)) & 0xF];
                    entry = scope.receipt == Entry::StockF ? "stock_f" : "fixture";
                }
                scope.receipt = Entry::None;
                scope.status = entry.empty() ? "pickup_receipt_expired" : "pickup_taken";
            }
        }
        lua.set_bool(!entry.empty());
        lua.set_string(entry.empty() ? std::string_view{} : std::string_view{guid.data(), guid.size()});
        lua.set_string(entry);
        return 3;
    }

    int lua_status(const Lua& lua)
    {
        const bool armed = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && bound();
        if (armed) verify_consumed();
        lua.set_bool(installed.load(std::memory_order_acquire));
        lua.set_bool(armed);
        lua.set_bool(armed && Clock::now() < scope.expires);
        lua.set_string(armed ? scope.status : "unbound");
        return 4;
    }

    int lua_probe(const Lua& lua)
    {
        bool ok = false;
        std::string_view status{"black_stone_fixture_scope_required"};
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_integer(2) &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS") && bound() && scope.host &&
            scope.accepting && Clock::now() < scope.expires)
        {
            const auto pawn_address = lua.get_integer();
            const auto stone_address = lua.get_integer();
            auto* pawn = scope.pawn.Get();
            auto* stone = scope.stone.Get();
            if (stone && pawn_address > 0 && address(pawn) == static_cast<Address>(pawn_address) && stone_address > 0 &&
                address(stone) == static_cast<Address>(stone_address) && scope.attempt == Entry::None &&
                scope.receipt == Entry::None && exact_placed_world(stone, scope.world.Get()) && nearby(pawn, stone))
            {
                InteractionArgs args{pawn, object(stone, STR("Box"))};
                scope.fixture = true;
                stone->ProcessEvent(scope.interaction, &args);
                scope.fixture = false;
                if (scope.attempt == Entry::Fixture) finish_pickup();
                ok = scope.receipt == Entry::Fixture || (scope.attempt == Entry::Fixture && scope.verifying);
                status = scope.status;
            }
        }
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int lua_clear(const Lua&)
    {
        silverfish::native_black_stone::clear_scope();
        return 0;
    }
}

namespace silverfish::native_black_stone
{
    void install_hooks()
    {
        if (!enabled() || callbacks[0] != Hook::ERROR_ID) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* function, void* args) {
                intercept(iteration, context, function, args, nullptr);
            }, {false, false, owner, STR("Guard exact Black Ocean pickup event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*) {
                intercept(iteration, context, frame.Node(), frame.Locals(), &frame);
            }, {false, false, owner, STR("Guard exact Black Ocean pickup script")});
        callbacks[2] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            post_script, {false, false, owner, STR("Verify exact Black Ocean pickup")});
        const bool ready = std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; });
        if (!ready) uninstall_hooks();
        installed.store(ready, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishSetNativeBlackStoneScope", lua_set_scope);
        lua.register_function("SilverfishTakeNativeBlackStonePickup", lua_take_receipt);
        lua.register_function("SilverfishReadNativeBlackStoneStatus", lua_status);
        lua.register_function("SilverfishClearNativeBlackStoneScope", lua_clear);
        if (env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS"))
            lua.register_function("SilverfishProbeNativeBlackStonePickup", lua_probe);
    }

    void clear_scope() { generation.fetch_add(1, std::memory_order_acq_rel); }
}
