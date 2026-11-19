#include "enemy_loot.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
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
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/PlaytestMap_01.PlaytestMap_01");
    constexpr auto npc_path = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto bandit_path = STR("/Game/Classes/NPCs/NPC_Bandit.NPC_Bandit_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Master_BP.Ammo_Master_BP_C");
    constexpr auto projectile_path =
        STR("/Game/Classes/Weapons/Wep_Assets/Projectiles/Wep_Projectile_Master_BP.Wep_Projectile_Master_BP_C");
    constexpr auto lease = std::chrono::milliseconds(1500);

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
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool out)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == out && !property->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize()
            ? property : nullptr;
    }

    U::UObject* object(U::UObject* receiver, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(receiver, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(receiver)) : nullptr;
    }

    bool boolean(U::UObject* receiver, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(receiver, name);
        return property && property->GetPropertyValueInContainer(receiver) == expected;
    }

    bool live(U::UObject* candidate)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject |
            U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return candidate && !candidate->HasAnyFlags(excluded) && !candidate->IsUnreachable();
    }

    bool authority(U::UObject* actor, U::UObject* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    U::UObject* sole_object(U::UObject* receiver, const U::TCHAR* name)
    {
        auto* property = field<U::FArrayProperty>(receiver, name);
        auto* inner = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        if (!inner) return nullptr;
        U::FScriptArrayHelper array(property, property->ContainerPtrToValuePtr<void>(receiver));
        return array.Num() == 1 ? inner->GetObjectPropertyValue(array.GetRawPtr(0)) : nullptr;
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_ENEMY_LOOT") && env("SILVERFISH_COOP_NATIVE_NPC_TEST") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    struct Participant
    {
        U::FWeakObjectPtr pawn{}, controller{}, player{}, save{};
    };

    struct Schema
    {
        U::UFunction *deal{}, *feedback{}, *die{}, *script{}, *loot{}, *chance{}, *passive{}, *calc{};
        U::UClass *bullet{}, *ammo{};
        U::FObjectProperty *instigator{}, *ammo_result{};
        U::FDoubleProperty *health{}, *damage{};
        U::FBoolProperty *lethal{}, *count{}, *bounty{};
        U::FIntProperty* instances{};
        U::FNameProperty* effect{};
    };

    struct Hit
    {
        U::FFrame* frame{};
        U::FWeakObjectPtr killer{};
        std::string projectile;
        double before{};
        bool died{};
    };

    struct Proof
    {
        std::string_view status{"watching"};
        int hits{}, loot_calls{}, bounty_calls{}, bounty_instances{}, ammo_count{};
        bool bounty_active{};
        U::FWeakObjectPtr killer{};
        std::string_view role{"-"};
        std::string killer_name, projectile, ammo_actor, ammo_class;
        double damage{}, before{}, after{};
    };

    struct Scope
    {
        U::FWeakObjectPtr world{}, instance{}, driver{}, npc{};
        Participant host{}, guest{};
        Schema schema{};
        Hit hit{};
        Proof proof{};
        U::FFrame* loot_frame{};
        Clock::time_point until{};
        std::uint64_t generation{};
    };

    Scope scope;
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};
    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};

    bool local_roster(const Scope& state)
    {
        auto* world = state.world.Get();
        auto* instance = state.instance.Get();
        auto* driver = state.driver.Get();
        auto* local = state.host.player.Get();
        auto* remote = state.guest.player.Get();
        if (!live(world) || !live(instance) || !live(driver) || !live(local) || !live(remote) || local == remote ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
            object(world, STR("OwningGameInstance")) != instance || object(world, STR("NetDriver")) != driver ||
            object(driver, STR("World")) != world || sole_object(instance, STR("LocalPlayers")) != local ||
            sole_object(driver, STR("ClientConnections")) != remote || object(remote, STR("Driver")) != driver ||
            object(remote, STR("OwningActor")) != state.guest.controller.Get() ||
            object(instance, STR("SaveSlot_Ref")) != state.host.save.Get() || state.host.save.Get() == state.guest.save.Get())
            return false;
        for (const auto* member : {&state.host, &state.guest})
        {
            auto* pawn = member->pawn.Get();
            auto* controller = member->controller.Get();
            auto* player = member->player.Get();
            if (!authority(pawn, world) || !authority(controller, world) || !live(member->save.Get()) ||
                object(pawn, STR("Save")) != member->save.Get() || object(pawn, STR("Controller")) != controller ||
                object(controller, STR("Pawn")) != pawn || object(controller, STR("Player")) != player ||
                object(player, STR("PlayerController")) != controller) return false;
        }
        return true;
    }

    bool current(const Scope& state)
    {
        auto* npc = state.npc.Get();
        auto* world = state.world.Get();
        return state.generation == generation.load(std::memory_order_acquire) && authority(npc, world) &&
            local_roster(state) && object(npc, STR("As Game Instance FP")) == state.instance.Get() &&
            static_cast<U::AActor*>(npc)->GetLevel() == object(world, STR("PersistentLevel"));
    }

    U::UFunction* script(U::UClass* type, const U::TCHAR* path, const U::TCHAR* name, int count)
    {
        RC::StringType full{path};
        full += STR(":");
        full += name;
        auto* function = find<U::UFunction>(full.c_str());
        return function && type && function->GetOuterPrivate() == type &&
            function->GetNumParms() == count && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    bool bind_schema(Scope& state)
    {
        auto* npc = state.npc.Get();
        auto* master = find<U::UClass>(npc_path);
        state.schema.bullet = find<U::UClass>(projectile_path);
        state.schema.ammo = find<U::UClass>(ammo_path);
        auto& schema = state.schema;
        schema.deal = script(master, npc_path, STR("DealDamage"), 7);
        schema.feedback = script(master, npc_path, STR("DamageFeedback"), 5);
        schema.die = script(master, npc_path, STR("Die"), 0);
        schema.script = script(master, npc_path, STR("ExecuteUbergraph_NPC_Master_BP"), 1);
        schema.loot = script(master, npc_path, STR("SpawnLoot"), 0);
        schema.calc = script(schema.bullet, projectile_path, STR("CalcDmg"), 1);
        schema.passive = script(state.instance.Get()->GetClassPrivate(), instance_path, STR("FindPassiveEffect"), 4);
        schema.chance = find<U::UFunction>(
            STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:LootItem_SpawnChance"));
        constexpr auto actor_path = STR("/Script/Engine.Actor");
        auto* actor = find<U::UClass>(actor_path);
        auto* item = find<U::UClass>(item_path);
        auto* ammo_class = field<U::FClassProperty>(npc, STR("AmmoItem_Class"));
        schema.health = field<U::FDoubleProperty>(npc, STR("Health"));
        schema.instigator = parameter<U::FObjectProperty>(schema.deal, STR("Instigator"), 0x150, false);
        schema.damage = parameter<U::FDoubleProperty>(schema.deal, STR("TotalDamage"), 0x178, true);
        schema.lethal = parameter<U::FBoolProperty>(schema.deal, STR("IsLethal"), 0x180, true);
        if (schema.loot)
        {
            // Numbered Kismet locals share a comparison index. Resolve the
            // exact reflected property rather than accepting the first alias.
            for (auto* property : U::TFieldRange<U::FProperty>(schema.loot, U::EFieldIterationFlags::None))
            {
                if (property->GetOffset_Internal() == 0x4b0 &&
                    property->GetFName().ToString() == STR("CallFunc_FinishSpawningActor_ReturnValue_3"))
                {
                    schema.ammo_result = U::CastField<U::FObjectProperty>(property);
                    break;
                }
            }
        }
        schema.effect = parameter<U::FNameProperty>(schema.passive, STR("EffectName"), 0, false);
        schema.count = parameter<U::FBoolProperty>(schema.passive, STR("CountInstances"), 8, false);
        schema.bounty = parameter<U::FBoolProperty>(schema.passive, STR("EffectActive"), 9, true);
        schema.instances = parameter<U::FIntProperty>(schema.passive, STR("Instances"), 0xc, true);
        return actor && item && master && npc->IsA(master) && schema.bullet && schema.ammo && schema.ammo->IsChildOf(item) &&
            schema.deal && npc->GetFunctionByNameInChain(U::FName{STR("DealDamage")}) == schema.deal &&
            schema.feedback && schema.die && schema.script && schema.loot && schema.calc && schema.passive &&
            schema.chance && schema.chance->GetOuterPrivate() == master && schema.chance->GetNumParms() == 2 &&
            U::CastField<U::FBoolProperty>(schema.chance->GetReturnProperty()) &&
            schema.health && schema.instigator && schema.instigator->GetPropertyClass().Get() == actor &&
            schema.damage && schema.lethal && schema.ammo_result && schema.ammo_result->GetArrayDim() == 1 &&
            schema.ammo_result->GetSize() == sizeof(void*) && schema.ammo_result->GetPropertyClass().Get() == item &&
            !schema.ammo_result->HasAnyPropertyFlags(U::CPF_Parm | U::CPF_OutParm | U::CPF_ReturnParm) &&
            ammo_class && ammo_class->GetMetaClass().Get() == item &&
            schema.effect && schema.count && schema.bounty && schema.instances;
    }

    bool set_participant(Participant& member, U::UObject* pawn, U::UClass* controller_type, U::UClass* save_type,
                         U::UClass* player_type)
    {
        auto* controller = object(pawn, STR("Controller"));
        auto* player = object(controller, STR("Player"));
        auto* save = object(pawn, STR("Save"));
        if (!live(controller) || controller->GetClassPrivate() != controller_type ||
            !live(player) || !player->IsA(player_type) || !live(save) || save->GetClassPrivate() != save_type)
            return false;
        member.pawn = pawn;
        member.controller = controller;
        member.player = player;
        member.save = save;
        return true;
    }

    std::string_view bind(Scope& next, Address host, Address guest, Address actor)
    {
        auto* world = find<U::UWorld>(world_path);
        auto* pawn_type = find<U::UClass>(pawn_path);
        auto* npc_type = find<U::UClass>(bandit_path);
        auto* store = find<U::UClass>(save_path);
        auto* controller = find<U::UClass>(controller_path);
        auto* instance = find<U::UClass>(instance_path);
        auto* local = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* connection = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        auto* owner = object(world, STR("OwningGameInstance"));
        if (!live(world) || !pawn_type || !npc_type || !store || !controller || !instance || !local || !connection ||
            !live(owner) || owner->GetClassPrivate() != instance) return "stock_enemy_loot_classes_unavailable";
        U::UObject* home{};
        U::UObject* peer{};
        U::UObject* bandit{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) == host && candidate->GetClassPrivate() == pawn_type) home = candidate;
            if (address(candidate) == guest && candidate->GetClassPrivate() == pawn_type) peer = candidate;
            if (address(candidate) == actor && candidate->GetClassPrivate() == npc_type) bandit = candidate;
            return home && peer && bandit ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        next.world = world;
        next.driver = object(world, STR("NetDriver"));
        next.instance = owner;
        next.npc = bandit;
        next.generation = generation.load(std::memory_order_acquire);
        if (!authority(bandit, world) || !boolean(bandit, STR("Alive"), true) || !boolean(bandit, STR("Initialized"), true) ||
            !set_participant(next.host, home, controller, store, local) ||
            !set_participant(next.guest, peer, controller, store, connection) || !current(next))
            return "stock_enemy_loot_authority_or_roster_unavailable";
        return bind_schema(next) ? std::string_view{} : "stock_enemy_loot_blueprint_schema_unavailable";
    }

    void* output(U::FFrame& frame, U::FProperty* property)
    {
        auto* entry = frame.OutParms();
        void* found{};
        for (int remaining = frame.Node()->GetNumParms(); entry && remaining > 0; --remaining, entry = entry->NextOutParm)
        {
            if (entry->Property != property) continue;
            if (found) return nullptr;
            found = entry->PropAddr;
        }
        return entry ? nullptr : found;
    }

    bool available()
    {
        return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw();
    }

    void prepare_hit(U::UObject* npc, U::FFrame& frame)
    {
        if (scope.hit.frame || scope.proof.killer.Get() || !frame.Locals() || !boolean(npc, STR("Alive"), true)) return;
        auto* caller = frame.PreviousFrame();
        auto* actor = caller ? caller->Object() : nullptr;
        if (!actor || caller->Node() != scope.schema.calc || !actor->IsA(scope.schema.bullet) ||
            !authority(actor, scope.world.Get())) return;
        const auto* parameter = scope.schema.instigator->ContainerPtrToValuePtr<void>(frame.Locals());
        auto* killer = scope.schema.instigator->GetObjectPropertyValue(parameter);
        if (!killer || (killer != scope.host.pawn.Get() && killer != scope.guest.pawn.Get()) ||
            object(actor, STR("Owner")) != killer || object(actor, STR("Instigator")) != killer) return;
        const auto health = scope.schema.health->GetPropertyValueInContainer(npc);
        if (!std::isfinite(health) || health <= 0) return;
        scope.hit = {&frame, U::FWeakObjectPtr{killer}, RC::to_utf8_string(actor->GetFullName()), health, false};
    }

    void pre(Iteration& iteration, U::UObject* object_receiver, U::FFrame& frame, void*)
    {
        if (!available() || object_receiver != scope.npc.Get() || frame.Object() != object_receiver ||
            iteration.OriginalFunctionCallPrevented()) return;
        auto* node = frame.Node();
        if ((node != scope.schema.deal && node != scope.schema.die && node != scope.schema.loot) ||
            Clock::now() >= scope.until || !current(scope)) return;
        if (node == scope.schema.deal)
        {
            prepare_hit(object_receiver, frame);
            return;
        }
        auto* caller = frame.PreviousFrame();
        if (!caller || caller->Object() != object_receiver) return;
        if (node == scope.schema.die && scope.hit.frame && caller->Node() == scope.schema.feedback &&
            caller->PreviousFrame() == scope.hit.frame &&
            object(object_receiver, STR("LastDamagedMe")) == scope.hit.killer.Get())
        {
            scope.hit.died = true;
        }
        if (node == scope.schema.loot && caller->Node() == scope.schema.script && scope.proof.killer.Get() &&
            scope.proof.loot_calls == 0 && boolean(object_receiver, STR("Alive"), false))
        {
            if (object(object_receiver, STR("LastDamagedMe")) != scope.proof.killer.Get())
            {
                scope.proof.status = "stock_loot_killer_changed";
                return;
            }
            scope.loot_frame = &frame;
            ++scope.proof.loot_calls;
        }
    }

    void finish_hit(Iteration& iteration, U::UObject* npc, U::FFrame& frame)
    {
        auto hit = std::move(scope.hit);
        scope.hit = {};
        auto* lethal = output(frame, scope.schema.lethal);
        auto* damage = output(frame, scope.schema.damage);
        auto* killer = hit.killer.Get();
        if (iteration.OriginalFunctionCallPrevented() || !killer || !lethal || !damage ||
            reinterpret_cast<Address>(damage) % alignof(double) != 0 || !current(scope)) return;
        const auto dealt = scope.schema.damage->GetPropertyValue(damage);
        const auto after = scope.schema.health->GetPropertyValueInContainer(npc);
        if (!std::isfinite(dealt) || !std::isfinite(after) || dealt <= 0 || after >= hit.before) return;
        ++scope.proof.hits;
        if (!scope.schema.lethal->GetPropertyValue(lethal)) return;
        if (!hit.died || after > 0 || !boolean(npc, STR("Alive"), false) || object(npc, STR("LastDamagedMe")) != killer)
        {
            scope.proof.status = "stock_lethal_kill_unverified";
            return;
        }
        scope.proof.killer = killer;
        scope.proof.killer_name = RC::to_utf8_string(killer->GetFullName());
        scope.proof.role = killer == scope.host.pawn.Get() ? "host" : "guest";
        scope.proof.projectile = std::move(hit.projectile);
        scope.proof.damage = dealt;
        scope.proof.before = hit.before;
        scope.proof.after = after;
        scope.proof.status = "stock_kill_confirmed";
    }

    void observe_bounty(Iteration& iteration, U::UObject* instance, U::FFrame& frame)
    {
        auto* caller = frame.PreviousFrame();
        const auto& schema = scope.schema;
        if (iteration.OriginalFunctionCallPrevented() || instance != scope.instance.Get() || !frame.Locals() ||
            !caller || caller->Node() != schema.chance || caller->Object() != scope.npc.Get() ||
            caller->PreviousFrame() != scope.loot_frame ||
            schema.effect->GetPropertyValueInContainer(frame.Locals()).ToString() != STR("Bounty") ||
            !schema.count->GetPropertyValueInContainer(frame.Locals())) return;
        auto* active = output(frame, schema.bounty);
        auto* instances = output(frame, schema.instances);
        if (!active || !instances || reinterpret_cast<Address>(instances) % alignof(std::int32_t) != 0) return;
        const auto count = schema.instances->GetPropertyValue(instances);
        if (count < 0) return;
        const auto value = schema.bounty->GetPropertyValue(active);
        if (scope.proof.bounty_calls && (scope.proof.bounty_active != value || scope.proof.bounty_instances != count))
        {
            scope.proof.status = "stock_bounty_cache_changed_during_loot";
        }
        ++scope.proof.bounty_calls;
        scope.proof.bounty_active = value;
        scope.proof.bounty_instances = count;
    }

    void finish_loot(Iteration& iteration, U::UObject* npc, U::FFrame& frame)
    {
        scope.loot_frame = nullptr;
        if (iteration.OriginalFunctionCallPrevented() || !frame.Locals() || !current(scope) ||
            object(npc, STR("LastDamagedMe")) != scope.proof.killer.Get()) return;
        if (scope.proof.status == "stock_kill_confirmed") scope.proof.status = "stock_loot_observed";
        auto* ammo = scope.schema.ammo_result->GetObjectPropertyValue(
            scope.schema.ammo_result->ContainerPtrToValuePtr<void>(frame.Locals()));
        auto* type = field<U::FClassProperty>(npc, STR("AmmoItem_Class"));
        if (!type) return;
        auto* expected = type->GetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(npc));
        if (!expected) return;
        if (!authority(ammo, scope.world.Get()) || ammo->GetClassPrivate() != expected || !ammo->IsA(scope.schema.ammo))
        {
            scope.proof.status = "stock_enemy_ammo_spawn_unverified";
            return;
        }
        auto* count = field<U::FIntProperty>(ammo, STR("Ammo"));
        const auto units = count ? count->GetPropertyValueInContainer(ammo) : -1;
        if (units < 3 || units > 9)
        {
            scope.proof.status = "stock_enemy_ammo_quantity_unverified";
            return;
        }
        scope.proof.ammo_actor = RC::to_utf8_string(ammo->GetFullName());
        scope.proof.ammo_class = RC::to_utf8_string(ammo->GetClassPrivate()->GetPathName());
        scope.proof.ammo_count = units;
    }

    void post(Iteration& iteration, U::UObject* object_receiver, U::FFrame& frame, void*)
    {
        if (!available()) return;
        if (&frame == scope.hit.frame && object_receiver == scope.npc.Get()) finish_hit(iteration, object_receiver, frame);
        if (!scope.loot_frame) return;
        if (&frame == scope.loot_frame && object_receiver == scope.npc.Get())
            finish_loot(iteration, object_receiver, frame);
        else if (frame.Node() == scope.schema.passive)
            observe_bounty(iteration, object_receiver, frame);
    }

    int answer(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        const auto host = lua.get_integer();
        const auto guest = lua.get_integer();
        const auto actor = lua.get_integer();
        if (host <= 0 || guest <= 0 || actor <= 0 || host == guest)
            return answer(lua, false, "native_enemy_loot_exact_watch_required");
        if (address(scope.host.pawn.Get()) == static_cast<Address>(host) &&
            address(scope.guest.pawn.Get()) == static_cast<Address>(guest) &&
            address(scope.npc.Get()) == static_cast<Address>(actor) && current(scope))
        {
            scope.until = Clock::now() + lease;
            return answer(lua, true, scope.proof.status);
        }
        scope = {};
        Scope next;
        if (const auto error = bind(next, static_cast<Address>(host), static_cast<Address>(guest), static_cast<Address>(actor));
            !error.empty()) return answer(lua, false, error);
        next.until = Clock::now() + lease;
        scope = std::move(next);
        return answer(lua, true, scope.proof.status);
    }

    int read(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        if (requested <= 0 || address(scope.host.pawn.Get()) != static_cast<Address>(requested) ||
            Clock::now() >= scope.until || !current(scope)) return 0;
        const auto& report = scope.proof;
        lua.set_string(report.status);
        lua.set_integer(report.hits);
        lua.set_string(report.role);
        lua.set_string(report.killer_name);
        lua.set_string(report.projectile);
        lua.set_number(report.damage);
        lua.set_number(report.before);
        lua.set_number(report.after);
        lua.set_integer(report.loot_calls);
        lua.set_integer(report.bounty_calls);
        lua.set_bool(report.bounty_active);
        lua.set_integer(report.bounty_instances);
        lua.set_string(report.ammo_actor);
        lua.set_string(report.ammo_class);
        lua.set_integer(report.ammo_count);
        return 15;
    }

    int clear(const Lua&)
    {
        silverfish::native_enemy_loot::clear_scope();
        return 0;
    }
}

namespace silverfish::native_enemy_loot
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            pre, {false, false, owner, STR("Observe stock enemy projectiles and death")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPostCallback(
            post, {false, false, owner, STR("Observe attributed stock enemy loot")});
        if (callbacks[0] == Hook::ERROR_ID || callbacks[1] == Hook::ERROR_ID)
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
        clear_scope();
        lua.register_function("SilverfishSetNativeEnemyLootScope", set_scope);
        lua.register_function("SilverfishReadNativeEnemyLoot", read);
        lua.register_function("SilverfishClearNativeEnemyLootScope", clear);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (U::IsInGameThreadRaw()) scope = {};
    }
}
