#include "loot_crate_input.hpp"
#include "authored_loot_registry.hpp"
#include "datasilo_door.hpp"
#include "electronic_locker.hpp"
#include "guest_item.hpp"
#include "interaction_input.hpp"
#include "shared_loot.hpp"
#include "zone_exit.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <utility>
#include <cstdlib>
#include <deque>
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
#include <Unreal/NameTypes.hpp>
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

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto lease = std::chrono::milliseconds(1500);
    constexpr auto scene_recheck = std::chrono::milliseconds(900);
    constexpr auto receipt_lifetime = std::chrono::seconds(4);
    constexpr std::uint64_t max_sequence = (std::uint64_t{1} << 53) - 1;
    constexpr auto& profiles = silverfish::authored_loot::profiles;

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* prop = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return prop && prop->GetArrayDim() == 1 ? prop : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* method, const U::TCHAR* name, int offset)
    {
        auto* prop = field<T>(method, name);
        return prop && prop->GetOffset_Internal() == offset && prop->HasAnyPropertyFlags(U::CPF_Parm) &&
            !prop->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) &&
            prop->GetSize() + offset <= method->GetParmsSize() ? prop : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container))
                                     : nullptr;
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

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool actor_in_world(U::UObject* actor, U::UWorld* world)
    {
        return live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false) &&
            static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool has_role(U::UObject* actor, int role)
    {
        auto* value = field<U::FByteProperty>(actor, STR("Role"));
        return value && value->GetPropertyValueInContainer(actor) == role;
    }

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_LOOT") == 0 &&
            size == value.size() && value[0] == '1';
    }

    struct Local
    {
        U::UObject* pawn{};
        bool host{};
    };

    Local local_owner(U::UWorld* world)
    {
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        auto* server_slot = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        if (!live(driver) || object(driver, STR("World")) != world || !server_slot || !exact(instance, instance_path))
            return {};
        auto* server = read(server_slot, driver);
        auto* net_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        const bool host = server == nullptr;
        if (!host && (!live(server) || !net_type || !server->IsA(net_type) || object(server, STR("Driver")) != driver))
            return {};

        auto* players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = players ? U::CastField<U::FObjectProperty>(players->GetInner()) : nullptr;
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!entry || !local_type) return {};
        U::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        if (locals.Num() != 1) return {};
        auto* player = read(entry, locals.GetRawPtr());
        auto* controller = object(player, STR("PlayerController"));
        auto* pawn = object(controller, STR("Pawn"));
        const auto role = host ? 3 : 2;
        if (!live(player) || !player->IsA(local_type) || !exact(controller, controller_path) ||
            !exact(pawn, pawn_path) || !actor_in_world(controller, world) || !actor_in_world(pawn, world) ||
            !has_role(controller, role) || !has_role(pawn, role) ||
            object(controller, STR("Player")) != player || object(pawn, STR("Controller")) != controller)
            return {};
        return {pawn, host};
    }

    U::UObject* stock_box(U::UObject* crate)
    {
        auto* box = object(crate, STR("Box"));
        return exact(box, STR("/Script/Engine.BoxComponent")) && box->GetOuterPrivate() == crate ? box : nullptr;
    }

    struct PresentationBox
    {
        std::string_view state{"scope_inactive"};
        U::UObject* box{};
    };

    PresentationBox presentation_box(U::UObject* crate)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.BoxComponent"));
        auto* property = field<U::FObjectProperty>(crate, STR("Box"));
        if (!type || !property || property->GetPropertyClass().Get() != type) return {"box_field"};
        auto* box = read(property, crate);
        if (!box) return {"missing"};
        if (!live(box) || U::FWeakObjectPtr(box).Get(false) != box) return {"retired"};
        if (box->GetClassPrivate() != type || box->GetOuterPrivate() != crate) return {"box_identity"};

        // K2_DestroyComponent removes the object from the actor before the
        // Blueprint property necessarily clears. Only registered ownership
        // can supply the presentation or fixture a live query component.
        const auto owned = static_cast<U::AActor*>(crate)->K2_GetComponentsByClass(type);
        if (owned.Num() < 0 || owned.Num() > 16) return {"box_extent"};
        for (int index = 0; index < owned.Num(); ++index)
            if (owned[index] == box) return {"live", box};
        return {"retired"};
    }

    bool ready_profile(U::UObject* crate, U::UWorld* world, std::string_view* reason = nullptr)
    {
        const auto refuse = [&](std::string_view detail) {
            if (reason) *reason = detail;
            return false;
        };
        const auto* profile = silverfish::shared_loot::authored_profile(crate);
        if (!profile) return refuse("crate_ready_profile");
        if (!silverfish::shared_loot::qualified_crate_settings(crate, reason)) return false;
        const bool hinged = profile->stock_class == &silverfish::authored_loot::red_lockbox;
        auto* door = object(crate, hinged ? STR("Arrow_Hinge") : STR("SM_Prop_Cabinet_Small_Door_01"));
        if (!exact(door, hinged ? STR("/Script/Engine.ArrowComponent") : STR("/Script/Engine.StaticMeshComponent")) ||
            door->GetOuterPrivate() != crate)
            return refuse("crate_first_door");
        if (hinged)
        {
            auto* lid = object(crate, STR("SM_Lid"));
            auto* frame = object(crate, STR("SM_Prop_Cabinet_Small_02"));
            if (!exact(lid, STR("/Script/Engine.StaticMeshComponent")) || lid->GetOuterPrivate() != crate ||
                !exact(frame, STR("/Script/Engine.StaticMeshComponent")) || frame->GetOuterPrivate() != crate ||
                object(lid, STR("AttachParent")) != door || object(door, STR("AttachParent")) != frame)
                return refuse("red_lockbox_hinge_unavailable");
        }
        if (profile->stock_class->paired_doors)
        {
            auto* second = object(crate, STR("SM_Prop_Cabinet_Small_Door_02"));
            if (!exact(second, STR("/Script/Engine.StaticMeshComponent")) ||
                second == door || second->GetOuterPrivate() != crate) return refuse("cabinet_second_door");
        }
        if (boolean(crate, STR("Looted"), true)) return true;
        auto* value = field<U::FIntProperty>(crate, STR("SpawnedValue"));
        auto* instance = object(world, STR("OwningGameInstance"));
        if (!boolean(crate, STR("Looted"), false)) return refuse("crate_looted_field");
        if (!value || value->GetPropertyValueInContainer(crate) != 0) return refuse("crate_spawned_value");
        if (!instance || object(crate, STR("As Game Instance FP")) != instance) return refuse("crate_instance");
        const auto box = presentation_box(crate);
        if (!box.box) return refuse(box.state);
        silverfish::shared_loot::ContainerActors children;
        return silverfish::shared_loot::read_spawners(crate, instance, children, reason);
    }


    struct Scope
    {
        U::FWeakObjectPtr crate{}, pawn{};
        U::FWeakObjectPtr world{};
        bool host{};
        Clock::time_point expires{}, receipt{};
        std::uint64_t observed{};
        bool pried{};
        bool pry_started{};
        U::FWeakObjectPtr pry_guest{};
        Clock::time_point tool_deadline{}, recheck_at{};
    };
    std::array<Scope, profiles.size()> scopes;
    std::deque<Scope> discovered_scopes;
    std::vector<std::size_t> pending_receipts;
    std::uint64_t sequence{};
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};

    struct LockpickResult
    {
        U::FWeakObjectPtr pawn, crate, tool;
        std::string guid;
        int before{}, spent{};
        bool unlocked{}, hinged{};
        std::uint64_t sequence{};
        Clock::time_point deadline{};
    };
    LockpickResult pending_lockpick;
    std::vector<LockpickResult> lockpick_results;

    bool ready() { return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire); }

    Scope& scope_for(std::size_t index)
    {
        if (index < scopes.size()) return scopes[index];
        index -= scopes.size();
        if (index >= discovered_scopes.size()) discovered_scopes.resize(index + 1);
        return discovered_scopes[index];
    }

    bool stock_function(U::UObject* crate, U::UFunction* method, int count, int size)
    {
        return method->GetOuterPrivate() == crate->GetClassPrivate() && method->GetNumParms() == count &&
            method->GetParmsSize() == size && !method->GetReturnProperty() &&
            !method->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net);
    }

    // Both stock lockpick classes resume the attempt after their Blueprint Delay at entry 38.
    bool lockpick_completion(U::UObject* tool, U::UFunction* method, const void* args)
    {
        static const U::FName standard{STR("ExecuteUbergraph_Eqp_LockPick")};
        static const U::FName heavy{STR("ExecuteUbergraph_Eqp_LockPick_Hev")};
        const auto name = method->GetFName();
        if (!args || (name != standard && name != heavy) || !stock_function(tool, method, 1, sizeof(std::int32_t))) return false;
        const auto path = name == standard ? STR("/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C") :
            STR("/Game/Classes/Equipment/Eqp_LockPick_Hev.Eqp_LockPick_Hev_C");
        auto* entry = parameter<U::FIntProperty>(method, STR("EntryPoint"), 0);
        return exact(tool, path) && entry && entry->GetPropertyValueInContainer(args) == 38;
    }

    void begin_lockpick(Iteration& call, U::UObject* tool, U::UFunction* method, const void* args)
    {
        if (!ready() || !tool || !method || call.OriginalFunctionCallPrevented() || !lockpick_completion(tool, method, args)) return;
        auto* pawn = object(tool, STR("Player_Ref"));
        if (!pawn || silverfish::native_interaction_input::current_guest_pawn(reinterpret_cast<std::uintptr_t>(pawn)) != pawn ||
            !boolean(pawn, STR("Dead"), false) || object(pawn, STR("EquipedItem")) != tool) return;
        auto* crate = object(tool, STR("LockRef"));
        if (!crate || crate != object(tool, STR("TargetActor"))) return;
        if (!live(crate) || !boolean(crate, STR("bActorIsBeingDestroyed"), false) ||
            !boolean(crate, STR("Locked"), true)) return;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        const auto* profile = silverfish::shared_loot::authored_profile(crate);
        const bool hinged = !profile && silverfish::native_datasilo_door::guest_hinged_lock(crate) &&
            silverfish::native_zone::visible_actor_level(static_cast<U::AActor*>(crate), world);
        if (!hinged && !actor_in_world(crate, world)) return;
        if (hinged ? !boolean(crate, STR("LockBroken"), false) :
            !profile || !profile->stock_class->pry_entry || !boolean(crate, STR("Looted"), false)) return;
        auto* ammo = field<U::FIntProperty>(tool, STR("Ammo"));
        auto* chance = field<U::FDoubleProperty>(tool, STR("Target_PickChance"));
        if (!ammo || !chance || !(chance->GetPropertyValueInContainer(tool) > 0.0)) return;
        const auto units = ammo->GetPropertyValueInContainer(tool);
        auto guid = silverfish::native_guest_item::local_equipped_identity(pawn, tool);
        if (units < 1 || guid.empty()) return;
        pending_lockpick = {pawn, crate, tool, std::move(guid), units};
        pending_lockpick.hinged = hinged;
    }

    void finish_lockpick(Iteration& call, U::UObject* tool, U::UFunction* method, const void* args)
    {
        if (!ready() || !tool || !method || pending_lockpick.tool.Get(true) != tool ||
            !lockpick_completion(tool, method, args)) return;
        auto completed = std::exchange(pending_lockpick, {});
        auto* crate = completed.crate.Get();
        auto* pawn = completed.pawn.Get();
        if (call.OriginalFunctionCallPrevented() || !live(pawn) || !live(crate) || sequence == max_sequence) return;
        auto* ammo = field<U::FIntProperty>(tool, STR("Ammo"));
        if (!ammo) return;
        const auto spent = completed.before - ammo->GetPropertyValueInContainer(tool);
        if (spent < 0 || spent > 1) return;
        if (!completed.hinged) completed.unlocked = boolean(crate, STR("Locked"), false);
        if (!completed.unlocked && !spent) return;
        completed.spent = spent;
        completed.sequence = ++sequence;
        const auto now = Clock::now();
        completed.deadline = now + receipt_lifetime;
        std::erase_if(lockpick_results, [now](const auto& row) { return row.deadline <= now || !row.pawn.Get(); });
        if (lockpick_results.size() < 16) lockpick_results.push_back(std::move(completed));
    }

    int read_lockpicks(const Lua& lua)
    {
        if (!ready() || lockpick_results.empty() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto now = Clock::now();
        std::erase_if(lockpick_results, [now](const auto& row) { return row.deadline <= now || !row.pawn.Get(); });
        if (lockpick_results.empty()) return 0;
        const auto address = lua.get_integer();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(address) : nullptr;
        if (!pawn) return 0;
        int fields = 0;
        for (const auto& result : lockpick_results)
        {
            if (result.pawn.Get() != pawn) continue;
            auto* target = result.crate.Get();
            if (result.hinged)
            {
                auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
                auto* actor = static_cast<U::AActor*>(target);
                if (!live(actor) || !boolean(actor, STR("bActorIsBeingDestroyed"), false) ||
                    !silverfish::native_zone::visible_actor_level(actor, world)) continue;
                auto name = RC::to_utf8_string(actor->GetFullName());
                if (actor->GetLevel() != object(world, STR("PersistentLevel"))) name.insert(0, "stream:");
                lua.set_string(name);
            }
            else
            {
                const auto* profile = silverfish::shared_loot::authored_profile(target);
                if (!profile) continue;
                lua.set_integer(static_cast<std::int64_t>(profile - profiles.data() + 1));
            }
            lua.set_integer(static_cast<std::int64_t>(result.sequence));
            lua.set_string(result.guid);
            lua.set_bool(result.unlocked);
            lua.set_integer(result.before);
            lua.set_integer(result.spent);
            fields += 6;
        }
        return fields;
    }

    bool same_scope(const Scope& scope, U::UObject* crate, Local local)
    {
        return crate && crate == scope.crate.Get() && scope.host == local.host && local.pawn && local.pawn == scope.pawn.Get();
    }

    void arm_scope(Scope& scope, U::UObject* crate, U::UWorld* world, Local local, Clock::time_point now)
    {
        if (!same_scope(scope, crate, local)) scope = {crate, local.pawn, world, local.host};
        scope.expires = now + lease;
        scope.recheck_at = now + scene_recheck;
    }

    void record_interaction(Scope& scope, std::size_t index, Clock::time_point now, bool pried)
    {
        scope.observed = ++sequence;
        scope.receipt = now + receipt_lifetime;
        scope.pried = pried;
        if (std::ranges::find(pending_receipts, index) == pending_receipts.end())
            pending_receipts.push_back(index);
    }

    void intercept(Iteration& call, U::UObject* crate, U::UFunction* method, const void* args)
    {
        if (!ready() || !crate || !method || !args || call.OriginalFunctionCallPrevented()) return;
        static const U::FName open_name{STR("SendInteractionToActor")};
        static const auto scripts = [] {
            std::array<U::FName, silverfish::authored_loot::stock_classes.size()> names{};
            std::ranges::transform(silverfish::authored_loot::stock_classes, names.begin(), [](const auto* type) {
                const std::basic_string_view path{type->script};
                return U::FName(path.substr(path.find_last_of(':') + 1).data());
            });
            return names;
        }();
        const auto name = method->GetFName();
        const bool script = std::ranges::find(scripts, name) != scripts.end();
        if (name != open_name && !script) return;
        const auto* profile = silverfish::shared_loot::authored_profile(crate);
        if (!profile || method != find<U::UFunction>(script ? profile->stock_class->script : profile->stock_class->input)) return;
        auto* world = silverfish::shared_loot::authored_crate_world(crate);
        if (!world) return;
        const auto local = local_owner(world);
        if (!local.pawn) return;
        const auto index = silverfish::shared_loot::profile_index(*profile);
        auto& active = scope_for(index);
        const auto now = Clock::now();
        // Stock input qualifies the live target even before the background crate scan reaches it.
        if (!ready_profile(crate, world)) return;

        if (script)
        {
            auto* entry = parameter<U::FIntProperty>(method, STR("EntryPoint"), 0);
            if (!stock_function(crate, method, 1, sizeof(std::int32_t)) || !entry ||
                silverfish::shared_loot::permits_stock_script(crate, method)) return;
            arm_scope(active, crate, world, local, now);
            const auto point = entry->GetPropertyValueInContainer(args);
            const auto& stock = *profile->stock_class;
            if (silverfish::electronic_locker::is_electronic(*profile))
            {
                if (point == silverfish::electronic_locker::power_signal_entry && !local.host)
                {
                    call.PreventOriginalFunctionCall();
                    return;
                }
                if (point == silverfish::electronic_locker::power_open_entry)
                {
                    call.PreventOriginalFunctionCall();
                    if (call.OriginalFunctionCallPrevented() && local.host && boolean(crate, STR("Looted"), false))
                        silverfish::electronic_locker::observe_power_open(crate, *profile);
                    return;
                }
            }
            if (stock.pry_start_entry && point == stock.pry_start_entry && local.host &&
                now < active.tool_deadline && active.pry_guest.Get() && boolean(crate, STR("Locked"), true))
                active.pry_started = true;
            if (stock.pry_entry && point == stock.pry_entry)
            {
                // The stock pry delay ends in this independent roll entry.
                // Capture it for the host before any stock items can spawn.
                call.PreventOriginalFunctionCall();
                if (call.OriginalFunctionCallPrevented() && local.host && sequence < max_sequence &&
                    boolean(crate, STR("Locked"), stock.pry_start_entry != 0) && boolean(crate, STR("Looted"), false))
                {
                    record_interaction(active, index, now, true);
                    if (now >= active.tool_deadline)
                    {
                        active.pry_guest = U::FWeakObjectPtr{};
                        active.tool_deadline = {};
                    }
                }
            }
            else if (point == profile->stock_class->input_entry) call.PreventOriginalFunctionCall();
            return;
        }
        auto* player = parameter<U::FObjectProperty>(method, STR("Player"), 0);
        auto* component = parameter<U::FObjectProperty>(method, STR("HitComponent"), sizeof(void*));
        if (!stock_function(crate, method, 2, 2 * sizeof(void*)) || !player || !component ||
            player->GetPropertyClass().Get() != find<U::UClass>(pawn_path) ||
            component->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.SceneComponent"))) return;
        auto* pawn = read(player, args);
        auto* hit = read(component, args);
        if (silverfish::shared_loot::permits_stock_interaction(crate, pawn, hit, method)) return;

        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || pawn != local.pawn || sequence == max_sequence ||
            !boolean(crate, STR("Looted"), false) || !boolean(pawn, STR("Holding_Interact"), true) ||
            !hit || hit != stock_box(crate) || object(pawn, STR("InteractionTarget_Component")) != hit ||
            !silverfish::native_interaction_input::consume_current_press(pawn)) return;
        arm_scope(active, crate, world, local, now);
        record_interaction(active, index, now, false);
        if (!local.host && profile->stock_class->pry_entry)
            silverfish::shared_loot::jiggle_container_lock(crate);
    }

    int set_scope(const Lua& lua)
    {
        std::string_view status{"hook_unavailable"};
        if (ready())
        {
            status = "scope_arguments";
            const auto count = lua.get_stack_size();
            if (count == 3 && lua.is_integer(1) && lua.is_bool(2) && lua.is_integer(3))
            {
                const auto pawn = lua.get_integer();
                const bool host = lua.get_bool();
                const auto profile_index = lua.get_integer();
                const auto* profile = profile_index > 0 ?
                    silverfish::shared_loot::profile_at(static_cast<std::size_t>(profile_index - 1)) : nullptr;
                if (!profile)
                {
                    lua.set_bool(false);
                    lua.set_string(status);
                    return 2;
                }
                const auto index = static_cast<std::size_t>(profile_index - 1);
                auto& scope = scope_for(index);
                const auto now = Clock::now();
                auto* cached_pawn = scope.pawn.Get();
                // The periodic observer may reuse a recent live scope. The input
                // interceptor and open transaction still recheck the exact scene.
                if (cached_pawn && pawn == reinterpret_cast<std::intptr_t>(cached_pawn) && scope.host == host &&
                    now < scope.expires && now < scope.recheck_at && scope.crate.Get())
                {
                    scope.expires = now + lease;
                    lua.set_bool(true);
                    lua.set_string("bound");
                    return 2;
                }
                auto* crate = find<U::AActor>(profile->actor);
                auto* world = silverfish::shared_loot::authored_crate_world(crate, &status);
                if (world)
                {
                    status = "local_owner";
                    const auto local = local_owner(world);
                    if (local.pawn && pawn == reinterpret_cast<std::intptr_t>(local.pawn) && host == local.host)
                    {
                        status = "crate_profile_shape";
                        if (ready_profile(crate, world, &status))
                        {
                            arm_scope(scope, crate, world, local, now);
                            status = "bound";
                        }
                    }
                }
                if (status != "bound") scope = {};
            }
        }
        lua.set_bool(status == "bound");
        lua.set_string(status);
        return 2;
    }

    int status(const Lua& lua)
    {
        const auto count = lua.get_stack_size();
        const auto index = count >= 1 && count <= 2 && lua.is_integer(1) ? lua.get_integer() : 0;
        const bool snapshot = count == 2 && lua.is_bool(2) && lua.get_bool();
        const auto* scope = ready() && index > 0 && index <= static_cast<std::int64_t>(scopes.size() + discovered_scopes.size())
            ? &scope_for(static_cast<std::size_t>(index - 1)) : nullptr;
        auto* crate = scope ? scope->crate.Get() : nullptr;
        const auto now = Clock::now();
        bool active = false;
        if (scope && crate && now < scope->expires)
        {
            if (snapshot && now < scope->recheck_at)
            {
                // This branch supplies telemetry after set_scope. Stock callbacks and
                // mutations still perform full identity, transport, and scene checks.
                auto* world = static_cast<U::UWorld*>(scope->world.Get());
                const bool streamed = world &&
                    static_cast<U::AActor*>(crate)->GetLevel() != object(world, STR("PersistentLevel"));
                active = world && actor_in_world(scope->pawn.Get(), world) &&
                    (streamed ? silverfish::shared_loot::authored_crate_world(crate) == world
                              : actor_in_world(crate, world));
            }
            else
            {
                auto* world = silverfish::shared_loot::authored_crate_world(crate);
                active = world && same_scope(*scope, crate, local_owner(world));
            }
        }
        const auto box = active ? presentation_box(crate) : PresentationBox{};
        lua.set_bool(active);
        const bool observed = active && now < scope->receipt && scope->observed;
        lua.set_integer(observed ? static_cast<std::int64_t>(scope->observed) : 0);
        lua.set_string(box.state);
        lua.set_integer(reinterpret_cast<std::intptr_t>(box.box));
        lua.set_bool(observed && scope->pried);
        const bool guest_pry = observed && scope->pried && scope->tool_deadline != Clock::time_point{};
        auto* guest = guest_pry ? scope->pry_guest.Get() : nullptr;
        lua.set_integer(guest ? reinterpret_cast<std::intptr_t>(guest) : guest_pry ? -1 : 0);
        const auto powered = active && index <= static_cast<std::int64_t>(profiles.size()) &&
            scope->host && boolean(crate, STR("Looted"), false)
            ? silverfish::electronic_locker::pending_power_open(crate, profiles[static_cast<std::size_t>(index - 1)]) : 0;
        lua.set_integer(static_cast<std::int64_t>(powered));
        return 7;
    }

    int receipts(const Lua& lua)
    {
        if (!ready() || pending_receipts.empty() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto pawn = static_cast<std::uintptr_t>(lua.get_integer());
        const auto now = Clock::now();
        int values = 0;
        std::erase_if(pending_receipts, [now](std::size_t index) {
            const auto& scope = scope_for(index);
            return !scope.observed || now >= scope.receipt || !scope.pawn.Get() || !scope.crate.Get();
        });
        for (const auto index : pending_receipts)
        {
            const auto& scope = scope_for(index);
            if (reinterpret_cast<std::uintptr_t>(scope.pawn.Get()) != pawn) continue;
            const bool guest_pry = scope.pried && scope.tool_deadline != Clock::time_point{};
            auto* guest = guest_pry ? scope.pry_guest.Get() : nullptr;
            lua.set_integer(static_cast<std::int64_t>(index + 1));
            lua.set_integer(static_cast<std::int64_t>(scope.observed));
            lua.set_bool(scope.pried);
            lua.set_integer(guest ? reinterpret_cast<std::intptr_t>(guest) : guest_pry ? -1 : 0);
            values += 4;
        }
        return values;
    }

    int discovered_profile(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1) return 0;
        const silverfish::authored_loot::Profile* profile{};
        if (lua.is_integer(1))
        {
            const auto index = lua.get_integer();
            if (index > static_cast<std::int64_t>(profiles.size()))
                profile = silverfish::shared_loot::profile_at(static_cast<std::size_t>(index - 1));
        }
        else if (lua.is_string(1))
        {
            const auto value = lua.get_string();
            if (value.size() > 384) return 0;
            const auto path = RC::to_generic_string(value);
            profile = silverfish::shared_loot::authored_profile(find<U::UObject>(path.c_str()));
            if (profile && silverfish::shared_loot::profile_index(*profile) < profiles.size()) return 0;
        }
        if (!profile) return 0;
        auto* crate = find<U::UObject>(profile->actor);
        auto* world = silverfish::shared_loot::authored_crate_world(crate);
        if (!world || !silverfish::shared_loot::qualified_crate_settings(crate) ||
            silverfish::shared_loot::authored_profile(crate) != profile) return 0;
        const std::basic_string_view world_path{profile->world};
        const auto area = world_path.substr(0, world_path.find(STR('.')));
        const bool streamed = static_cast<U::AActor*>(crate)->GetLevel() != object(world, STR("PersistentLevel"));
        lua.set_integer(static_cast<std::int64_t>(silverfish::shared_loot::profile_index(*profile) + 1));
        lua.set_string(RC::to_utf8_string(crate->GetFullName()));
        lua.set_string(RC::to_utf8_string(profile->stock_class->actor_class));
        lua.set_integer(profile->salt);
        lua.set_integer(profile->stock_class->slots);
        lua.set_string(RC::to_utf8_string(area));
        lua.set_bool(streamed);
        return 7;
    }

    int arm_guest_pry(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
        {
            lua.set_bool(false);
            return 1;
        }
        const auto index = lua.get_integer();
        const auto pawn = static_cast<std::uintptr_t>(lua.get_integer());
        const auto save = static_cast<std::uintptr_t>(lua.get_integer());
        bool armed = false;
        if (index > 0 && index <= static_cast<std::int64_t>(scopes.size()))
        {
            auto& scope = scopes[static_cast<std::size_t>(index - 1)];
            auto* crate = scope.crate.Get();
            auto* world = silverfish::shared_loot::authored_crate_world(crate);
            const auto* profile = silverfish::shared_loot::authored_profile(crate);
            auto* instance = object(world, STR("OwningGameInstance"));
            auto* stock = object(instance, STR("SaveSlot_Ref"));
            const auto now = Clock::now();
            silverfish::shared_loot::EscrowOwner owner{};
            if (world && profile && profile->stock_class->pry_entry && scope.host && !scope.pried &&
                now < scope.expires && now >= scope.tool_deadline && same_scope(scope, crate, local_owner(world)) &&
                boolean(crate, STR("Locked"), true) && boolean(crate, STR("Looted"), false) &&
                silverfish::shared_loot::qualify_escrow_owner_in_world(world, pawn, save,
                    reinterpret_cast<std::uintptr_t>(stock), owner) && owner.save != owner.stock)
            {
                scope.pry_guest = owner.pawn;
                scope.pry_started = false;
                scope.tool_deadline = now + std::chrono::seconds(6);
                armed = true;
            }
        }
        lua.set_bool(armed);
        return 1;
    }

    int cancel_guest_pry(const Lua& lua)
    {
        bool cancelled = false;
        if (ready() && lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_integer(2))
        {
            const auto index = lua.get_integer();
            const auto pawn = lua.get_integer();
            if (index > 0 && index <= static_cast<std::int64_t>(scopes.size()))
            {
                auto& scope = scopes[static_cast<std::size_t>(index - 1)];
                if (scope.host && !scope.pried && !scope.pry_started && pawn > 0 &&
                    reinterpret_cast<std::intptr_t>(scope.pry_guest.Get()) == pawn &&
                    boolean(scope.crate.Get(), STR("Locked"), true))
                {
                    scope.pry_guest = U::FWeakObjectPtr{};
                    scope.tool_deadline = {};
                    cancelled = true;
                }
            }
        }
        lua.set_bool(cancelled);
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::native_loot_crate_input::clear_scope();
        return 0;
    }
}

namespace silverfish::native_loot_crate_input
{
    bool consume_guest_open(U::UObject* crate, U::UObject* pawn, std::uint64_t requested)
    {
        if (!ready() || !requested) return false;
        const auto* profile = shared_loot::authored_profile(crate);
        if (!profile) return false;
        auto& scope = scope_for(shared_loot::profile_index(*profile));
        if (scope.host || scope.pried || scope.observed != requested || scope.pawn.Get() != pawn ||
            scope.crate.Get() != crate || Clock::now() >= scope.receipt) return false;
        scope.observed = 0;
        return true;
    }

    void observe_hinged_pick(U::UObject* actor)
    {
        // A guarded PickLock call during this stock attempt means the Blueprint's success roll passed.
        if (pending_lockpick.hinged && pending_lockpick.crate.Get() == actor)
            pending_lockpick.unlocked = true;
    }

    bool consume_vehicle_pry(U::UObject* crate, U::UObject* pawn)
    {
        if (!ready()) return false;
        const auto* profile = silverfish::shared_loot::authored_profile(crate);
        if (!profile) return false;
        auto& scope = scopes[static_cast<std::size_t>(profile - profiles.data())];
        const auto now = Clock::now();
        auto* opener = scope.tool_deadline != Clock::time_point{} ? scope.pry_guest.Get() : scope.pawn.Get();
        if (scope.crate.Get() != crate || opener != pawn || !scope.host || !scope.pried || !scope.observed ||
            now >= scope.receipt || now >= scope.expires) return false;

        scope.observed = 0;
        scope.pried = false;
        scope.pry_guest = U::FWeakObjectPtr{};
        scope.tool_deadline = {};
        return true;
    }
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* crate, U::UFunction* method, void* parameters) {
                intercept(call, crate, method, parameters);
                begin_lockpick(call, crate, method, parameters);
            }, {false, false, STR("SilverfishCoopNative"), STR("Route qualified authored crate input")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* crate, U::FFrame& frame, void*) {
                intercept(call, crate, frame.Node(), frame.Locals());
            }, {false, false, STR("SilverfishCoopNative"), STR("Route qualified authored crate script")});
        callbacks[2] = Hook::RegisterProcessEventPostCallback(finish_lockpick,
            {false, false, STR("SilverfishCoopNative"), STR("Read completed stock lockpick attempts")});
        if (std::ranges::find(callbacks, Hook::ERROR_ID) != callbacks.end())
        {
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        for (auto& callback : callbacks)
        {
            if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishSetNativeLootCrateInputScope", set_scope);
        lua.register_function("SilverfishReadNativeLootCrateInput", status);
        lua.register_function("SilverfishReadNativeLootCrateReceipts", receipts);
        lua.register_function("SilverfishDiscoverNativeLootCrate", discovered_profile);
        lua.register_function("SilverfishReadNativeLockpickResults", read_lockpicks);
        lua.register_function("SilverfishClearNativeLootCrateInput", clear);
        lua.register_function("SilverfishArmNativeGuestVehiclePry", arm_guest_pry);
        lua.register_function("SilverfishCancelNativeGuestVehiclePry", cancel_guest_pry);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw())
        {
            scopes = {};
            discovered_scopes.clear();
            pending_receipts.clear();
            pending_lockpick = {};
            lockpick_results.clear();
            silverfish::electronic_locker::clear();
        }
    }
}
