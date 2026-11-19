#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;

    constexpr int max_guests = 4;
    constexpr int max_effects = 256;
    constexpr double max_gap = 0.5;
    constexpr double max_step = 1.0 / 30.0;
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto regen_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:RegenHealth");

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && opt_in("SILVERFISH_COOP_NATIVE_GUEST_REGEN");
    }

    template <typename T>
    T* find(const U::TCHAR* path) { return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path); }

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

    bool is_false(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && !property->GetPropertyValueInContainer(owner);
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(U::UObject* object, U::UClass* type) { return type && live(object) && object->GetClassPrivate() == type; }
    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    bool authority(U::UObject* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            is_false(actor, STR("bActorIsBeingDestroyed")) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    U::FObjectProperty* object_inner(U::FArrayProperty* property)
    {
        return property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
    }

    struct Scope
    {
        U::FWeakObjectPtr pawn, save, stock, world, instance, controller, driver, connection;

        bool current() const
        {
            auto* player = pawn.Get();
            auto* detached = save.Get();
            auto* canonical = stock.Get();
            auto* current_world = U::Cast<U::UWorld>(world.Get());
            auto* game = instance.Get();
            auto* control = controller.Get();
            auto* net = driver.Get();
            auto* remote = connection.Get();
            if (!live(current_world) || !authority(player, current_world) || !authority(control, current_world) ||
                !live(detached) || !live(canonical) || detached == canonical || !live(game) || !live(net) || !live(remote) ||
                object(player, STR("Save")) != detached || object(player, STR("Controller")) != control ||
                object(player, STR("As Game Instance FP")) != game || object(control, STR("Pawn")) != player ||
                object(control, STR("Player")) != remote || object(current_world, STR("OwningGameInstance")) != game ||
                object(game, STR("SaveSlot_Ref")) != canonical || object(current_world, STR("NetDriver")) != net ||
                !field<U::FObjectProperty>(net, STR("ServerConnection")) || object(net, STR("ServerConnection")) ||
                object(net, STR("World")) != current_world || object(remote, STR("Driver")) != net ||
                object(remote, STR("OwningActor")) != control || object(remote, STR("PlayerController")) != control)
                return false;

            auto* local = field<U::FArrayProperty>(game, STR("LocalPlayers"));
            auto* clients = field<U::FArrayProperty>(net, STR("ClientConnections"));
            auto* local_entry = object_inner(local);
            auto* client_entry = object_inner(clients);
            auto* local_class = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
            auto* remote_class = find<U::UClass>(STR("/Script/Engine.NetConnection"));
            if (!local_entry || !client_entry || !local_class || !remote_class || !remote->IsA(remote_class))
                return false;

            U::FScriptArrayHelper locals(local, local->ContainerPtrToValuePtr<void>(game));
            if (locals.Num() != 1) return false;
            auto* local_player = local_entry->GetObjectPropertyValue(locals.GetRawPtr());
            auto* host_controller = object(local_player, STR("PlayerController"));
            auto* host_pawn = object(host_controller, STR("Pawn"));
            if (!live(local_player) || !local_player->IsA(local_class) ||
                !exact(host_controller, control->GetClassPrivate()) || !authority(host_controller, current_world) ||
                object(host_controller, STR("Player")) != local_player ||
                !exact(host_pawn, player->GetClassPrivate()) || !authority(host_pawn, current_world) ||
                object(host_pawn, STR("Controller")) != host_controller || object(host_pawn, STR("Save")) != canonical)
                return false;

            U::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(net));
            if (remotes.Num() < 1 || remotes.Num() > max_guests) return false;
            std::array<U::UObject*, max_guests> seen{};
            bool found = false;
            for (int index = 0; index < remotes.Num(); ++index)
            {
                auto* client = client_entry->GetObjectPropertyValue(remotes.GetRawPtr(index));
                if (!live(client) || !client->IsA(remote_class) || std::ranges::find(seen, client) != seen.end())
                    return false;
                seen[static_cast<std::size_t>(index)] = client;
                if (client == remote) found = true;
                else if (object(object(object(client, STR("PlayerController")), STR("Pawn")), STR("Save")) == detached)
                    return false;
            }
            return found;
        }

        bool bind(Address requested, Address detached, Address canonical)
        {
            auto* pawn_class = find<U::UClass>(pawn_path);
            auto* save_class = find<U::UClass>(save_path);
            auto* instance_class = find<U::UClass>(instance_path);
            auto* controller_class = find<U::UClass>(controller_path);
            if (!pawn_class || !save_class || !instance_class || !controller_class) return false;
            U::UObject* player{};
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (address(candidate) != requested) return RC::LoopAction::Continue;
                if (exact(candidate, pawn_class)) player = candidate;
                return RC::LoopAction::Break;
            });
            if (!player) return false;
            auto* actual_world = static_cast<U::AActor*>(player)->GetWorld();
            auto* game = object(actual_world, STR("OwningGameInstance"));
            auto* control = object(player, STR("Controller"));
            auto* actual_save = object(player, STR("Save"));
            auto* canonical_save = object(game, STR("SaveSlot_Ref"));
            if (!exact(actual_save, save_class) || !exact(canonical_save, save_class) || !exact(game, instance_class) ||
                !exact(control, controller_class) || address(actual_save) != detached || address(canonical_save) != canonical)
                return false;
            pawn = player;
            save = actual_save;
            stock = canonical_save;
            world = actual_world;
            instance = game;
            controller = control;
            driver = object(actual_world, STR("NetDriver"));
            connection = object(control, STR("Player"));
            return current();
        }
    };

    struct ClockParams
    {
        U::UObject* context;
        double result;
    };

    double world_seconds(U::UObject* world)
    {
        auto* function = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:GetTimeSeconds"));
        auto* defaults = find<U::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
        auto* input = field<U::FObjectProperty>(function, STR("WorldContextObject"));
        auto* output = function ? U::CastField<U::FDoubleProperty>(function->GetReturnProperty()) : nullptr;
        constexpr auto flags = static_cast<U::EFunctionFlags>(U::FUNC_Native | U::FUNC_Static);
        if (!defaults || !function || !input || !output || !function->HasAllFunctionFlags(flags) ||
            function->GetOuterPrivate() != defaults->GetClassPrivate() || function->GetNumParms() != 2 ||
            function->GetParmsSize() != sizeof(ClockParams) || input->GetOffset_Internal() != offsetof(ClockParams, context) ||
            output->GetOffset_Internal() != offsetof(ClockParams, result)) return -1.0;
        ClockParams params{world, -1.0};
        defaults->ProcessEvent(function, &params);
        return params.result;
    }

    struct Passives
    {
        U::FName regen{STR("RegenBoost"), U::FNAME_Find};
        U::FName hunger{STR("Hunger"), U::FNAME_Find};
        U::FName attuned{STR("AttunedMadness"), U::FNAME_Find};
        int regen_count{}, hunger_count{};
        bool attuned_present{};

        void add(const U::FName& name)
        {
            regen_count += name == regen;
            hunger_count += name == hunger;
            attuned_present |= name == attuned;
        }

        bool own_traits(U::UObject* save)
        {
            auto* type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct"));
            auto* character = field<U::FStructProperty>(save, STR("Character"));
            auto* effects = field<U::FArrayProperty>(type, STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"));
            auto* name = effects ? U::CastField<U::FNameProperty>(effects->GetInner()) : nullptr;
            if (!type || !character || character->GetStruct().Get() != type || !name ||
                name->GetArrayDim() != 1 || name->GetOffset_Internal() != 0 || regen.IsNone() || hunger.IsNone() || attuned.IsNone())
                return false;
            U::FScriptArrayHelper permanent(effects, effects->ContainerPtrToValuePtr<void>(character->ContainerPtrToValuePtr<void>(save)));
            if (permanent.Num() < 0 || permanent.Num() > max_effects) return false;
            for (int i = 0; i < permanent.Num(); ++i) add(name->GetPropertyValue(permanent.GetRawPtr(i)));

            auto* temporary = field<U::FMapProperty>(save, STR("PlayerTempEffects"));
            auto* key = temporary ? U::CastField<U::FNameProperty>(temporary->GetKeyProp()) : nullptr;
            auto* value = temporary ? U::CastField<U::FStructProperty>(temporary->GetValueProp()) : nullptr;
            auto* timespan = find<U::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
            if (!key || key->GetSize() != sizeof(U::FName) || key->GetOffset_Internal() != 0 ||
                !value || !timespan || value->GetStruct().Get() != timespan) return false;
            const auto layout = U::FScriptMap::GetScriptLayout(
                key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
            if (value->GetOffset_Internal() != layout.ValueOffset) return false;
            const auto* map = temporary->ContainerPtrToValuePtr<U::FScriptMap>(save);
            const int extent = map->GetMaxIndex();
            if (map->Num() < 0 || map->Num() > extent || extent > max_effects) return false;
            for (int i = 0; i < extent; ++i)
            {
                if (!map->IsValidIndex(i)) continue;
                const auto* entry = map->GetData(i, layout);
                if (!entry) return false;
                add(key->GetPropertyValue(entry));
            }
            return !attuned_present;
        }

        bool gear_has_no_relevant_effect(U::UObject* save) const
        {
            auto* type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct"));
            auto* base = find<U::UClass>(STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"));
            auto* cls = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
            if (!type || !base || !cls || cls->GetMetaClass().Get() != base) return false;
            const auto safe_row = [&](const void* row) {
                auto* entry = cls->GetObjectPropertyValue(cls->ContainerPtrToValuePtr<void>(row));
                if (!entry) return true;
                auto* item = U::Cast<U::UClass>(entry);
                if (!live(item) || !item->IsChildOf(base)) return false;
                auto* defaults = item->GetClassDefaultObject().Get();
                auto* property = field<U::FArrayProperty>(defaults, STR("PassiveEffects"));
                auto* effect = property ? U::CastField<U::FStrProperty>(property->GetInner()) : nullptr;
                if (!defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) || defaults->GetClassPrivate() != item ||
                    !effect || effect->GetArrayDim() != 1 || effect->GetOffset_Internal() != 0) return false;
                U::FScriptArrayHelper names(property, property->ContainerPtrToValuePtr<void>(defaults));
                if (names.Num() < 0 || names.Num() > max_effects) return false;
                for (int i = 0; i < names.Num(); ++i)
                {
                    const auto& text = effect->GetPropertyValue(names.GetRawPtr(i));
                    if (text.Len() < 0 || text.Len() > 256) return false;
                    if (text.IsEmpty()) continue;
                    const U::FName name{*text, U::FNAME_Find};
                    if (name == regen || name == hunger || name == attuned) return false;
                }
                return true;
            };
            const auto exact_row = [&](U::FStructProperty* property) {
                return property && property->GetStruct().Get() == type;
            };
            // Inspect all four stores. Refusing matching CDO effects even in dormant Backpack rows avoids
            // assuming the stock backpack-category predicate for newly supported gear.
            for (auto* part : {STR("PlayerBackpack"), STR("PlayerEquipment")})
            {
                auto* property = field<U::FArrayProperty>(save, part);
                if (!property || !exact_row(U::CastField<U::FStructProperty>(property->GetInner()))) return false;
                U::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(save));
                if (rows.Num() < 0 || rows.Num() > 4096) return false;
                for (int i = 0; i < rows.Num(); ++i) if (!safe_row(rows.GetRawPtr(i))) return false;
            }
            for (auto* part : {STR("PlayerArmor"), STR("PlayerHelmet")})
            {
                auto* property = field<U::FStructProperty>(save, part);
                if (!exact_row(property) || !safe_row(property->ContainerPtrToValuePtr<void>(save))) return false;
            }
            return true;
        }
    };

    struct Vitals
    {
        double hp{}, ceiling{}, food{}, stim{}, rads{};

        bool finite() const
        {
            return std::isfinite(hp) && std::isfinite(ceiling) && std::isfinite(food) &&
                std::isfinite(stim) && std::isfinite(rads);
        }

        bool operator==(const Vitals&) const = default;
    };

    struct Snapshot
    {
        Vitals vitals{};
        double delay{}, rate{};

        bool operator==(const Snapshot&) const = default;
    };

    struct Health
    {
        U::UObject* pawn{};
        U::UObject* save{};
        U::UObject* stock{};
        U::UFunction* function{};
        U::FDoubleProperty *hp{}, *ceiling{}, *food{}, *stim{}, *rads{}, *maximum{}, *delay{}, *rate{}, *delta{}, *boost{};
        U::FIntProperty* hunger{};
        U::FFloatProperty* dilation{};

        bool bind(const Scope& scope)
        {
            pawn = scope.pawn.Get();
            save = scope.save.Get();
            stock = scope.stock.Get();
            hp = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
            ceiling = field<U::FDoubleProperty>(save, STR("PlayerRegen"));
            food = field<U::FDoubleProperty>(save, STR("PlayerFood"));
            stim = field<U::FDoubleProperty>(save, STR("PlayerStim"));
            rads = field<U::FDoubleProperty>(save, STR("PlayerRads"));
            maximum = field<U::FDoubleProperty>(pawn, STR("MaxHealth"));
            delay = field<U::FDoubleProperty>(pawn, STR("Regen_Delay"));
            rate = field<U::FDoubleProperty>(pawn, STR("RegenRate"));
            delta = field<U::FDoubleProperty>(pawn, STR("DeltaT"));
            boost = field<U::FDoubleProperty>(pawn, STR("Stimmed_RegenBoost"));
            hunger = field<U::FIntProperty>(pawn, STR("HungerStack"));
            dilation = field<U::FFloatProperty>(pawn, STR("CustomTimeDilation"));
            function = find<U::UFunction>(regen_path);
            return hp && ceiling && food && stim && rads && maximum && delay && rate && delta && boost && hunger && dilation &&
                function && function->GetOuterPrivate() == pawn->GetClassPrivate() && function->GetNumParms() == 0 &&
                function->GetParmsSize() == 0 && !function->GetReturnProperty() &&
                !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net | U::FUNC_Static);
        }

        Vitals read(U::UObject* owner) const
        {
            return {hp->GetPropertyValueInContainer(owner), ceiling->GetPropertyValueInContainer(owner),
                food->GetPropertyValueInContainer(owner), stim->GetPropertyValueInContainer(owner),
                rads->GetPropertyValueInContainer(owner)};
        }

        Snapshot snapshot() const { return {read(save), delay->GetPropertyValueInContainer(pawn), rate->GetPropertyValueInContainer(pawn)}; }

        void restore(const Snapshot& before) const
        {
            hp->SetPropertyValueInContainer(save, before.vitals.hp);
            food->SetPropertyValueInContainer(save, before.vitals.food);
            stim->SetPropertyValueInContainer(save, before.vitals.stim);
            delay->SetPropertyValueInContainer(pawn, before.delay);
            rate->SetPropertyValueInContainer(pawn, before.rate);
        }

        struct TemporaryInputs
        {
            const Health& health;
            double previous_delta = health.delta->GetPropertyValueInContainer(health.pawn);
            double previous_boost = health.boost->GetPropertyValueInContainer(health.pawn);
            int previous_hunger = health.hunger->GetPropertyValueInContainer(health.pawn);

            ~TemporaryInputs()
            {
                health.delta->SetPropertyValueInContainer(health.pawn, previous_delta);
                health.boost->SetPropertyValueInContainer(health.pawn, previous_boost);
                health.hunger->SetPropertyValueInContainer(health.pawn, previous_hunger);
            }
        };

        void step(double seconds, double regen_boost, int hunger_stack) const
        {
            // Both fields normally come from the canonical GI passive cache. Only the pawn's temporary
            // inputs are overridden with its detached Save's effects, and no callback or latent work is used.
            TemporaryInputs restore_inputs{*this};
            delta->SetPropertyValueInContainer(pawn, seconds);
            boost->SetPropertyValueInContainer(pawn, regen_boost);
            hunger->SetPropertyValueInContainer(pawn, hunger_stack);
            pawn->ProcessEvent(function, nullptr);
        }
    };

    bool approximately(double actual, double wanted)
    {
        return std::isfinite(actual) && std::abs(actual - wanted) <= 1e-7;
    }

    bool prove_step(const Snapshot& before, const Snapshot& after, double seconds, double boost)
    {
        const auto& start = before.vitals;
        const auto& end = after.vitals;
        if (!end.finite() || end.ceiling != start.ceiling || end.rads != start.rads ||
            !approximately(end.stim, std::max(0.0, start.stim - seconds * 0.1))) return false;
        if (before.delay > 0.0)
            return end.hp == start.hp && end.food == start.food && after.rate == before.rate &&
                approximately(after.delay, before.delay - seconds);

        const double rate = std::clamp(start.food / 100.0, 0.0, 1.0) * 2.0 + start.stim;
        const double next_hp = std::min(start.hp + seconds * (rate * boost), start.ceiling);
        return approximately(end.hp, next_hp) && approximately(after.rate, rate) && after.delay == before.delay &&
            end.food <= start.food && end.food >= std::max(0.0, start.food - 0.004) - 1e-7;
    }

    struct Entry
    {
        Address requested{};
        Scope scope;
        double last{-1.0}, total{};
        std::int64_t pulses{};
        bool blocked{};
    };

    std::array<Entry, max_guests> entries{};

    Entry* qualify(Address requested, Address save, Address canonical)
    {
        Entry* available{};
        for (auto& entry : entries)
        {
            if (entry.requested == requested && address(entry.scope.pawn.Get()) == requested &&
                address(entry.scope.save.Get()) == save && address(entry.scope.stock.Get()) == canonical)
            {
                if (entry.scope.current()) return &entry;
                entry.last = -1.0;
                return nullptr;
            }
            if (entry.requested == requested || !entry.scope.pawn.Get() || !entry.scope.world.Get())
            {
                entry = {};
                available = &entry;
            }
        }
        if (!available || !available->scope.bind(requested, save, canonical)) return nullptr;
        available->requested = requested;
        return available;
    }

    int reply(const Lua& lua, std::string_view status)
    {
        lua.set_string(status);
        return 1;
    }

    int reply(const Lua& lua, std::string_view status, const Entry& entry, const Snapshot& before,
              const Snapshot& after, double seconds)
    {
        reply(lua, status);
        lua.set_integer(entry.pulses);
        lua.set_number(entry.total);
        lua.set_number(seconds);
        lua.set_number(before.vitals.hp);
        lua.set_number(after.vitals.hp);
        lua.set_number(after.vitals.ceiling);
        lua.set_number(before.delay);
        lua.set_number(after.delay);
        lua.set_number(before.vitals.food);
        lua.set_number(after.vitals.food);
        lua.set_number(before.vitals.stim);
        lua.set_number(after.vitals.stim);
        return 13;
    }

    int pulse(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 3 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return reply(lua, "refused_scope");
        const auto requested = lua.get_integer();
        const auto save = lua.get_integer();
        const auto canonical = lua.get_integer();
        if (requested <= 0 || save <= 0 || canonical <= 0 || save == canonical) return reply(lua, "refused_identity");
        auto* entry = qualify(requested, save, canonical);
        if (!entry) return reply(lua, "refused_exact_detached_save");
        if (entry->blocked) return reply(lua, "refused_unverified_prior_pulse");

        const auto now = world_seconds(entry->scope.world.Get());
        const auto prior = entry->last;
        entry->last = std::isfinite(now) && now >= 0.0 ? now : -1.0;
        if (entry->last < 0.0) return reply(lua, "refused_world_clock");

        auto* pawn = entry->scope.pawn.Get();
        if (!pawn || static_cast<U::AActor*>(pawn)->IsActorTickEnabled() || !is_false(pawn, STR("Dead")) ||
            !is_false(pawn, STR("IsWeaponBlocked")) || !is_false(pawn, STR("UsingItem")))
        {
            entry->last = -1.0;
            return reply(lua, "paused_pawn_gate");
        }
        Health health;
        if (!health.bind(entry->scope)) return reply(lua, "refused_health_schema");
        const auto before = health.snapshot();
        const auto host = health.read(health.stock);
        const auto maximum = health.maximum->GetPropertyValueInContainer(pawn);
        const double dilation = health.dilation->GetPropertyValueInContainer(pawn);
        const auto& initial = before.vitals;
        if (!initial.finite() || !host.finite() || !std::isfinite(maximum) || !std::isfinite(before.delay) ||
            !std::isfinite(before.rate) || !std::isfinite(dilation) || dilation < 0.0 ||
            initial.hp <= 1.0 || initial.ceiling > maximum || initial.hp > initial.ceiling ||
            initial.food < 0.0 || initial.food > 200.0 || initial.stim < 0.0 || initial.rads < 0.0)
        {
            entry->last = -1.0;
            return reply(lua, "paused_vitals");
        }

        const double elapsed = now - prior;
        const double seconds = elapsed * dilation;
        Passives owned;
        if (!owned.own_traits(health.save) || !owned.gear_has_no_relevant_effect(health.save))
        {
            entry->last = -1.0;
            return reply(lua, "refused_unqualified_passives");
        }
        if (prior < 0.0 || elapsed < 0.0 || elapsed > max_gap || seconds > max_gap)
            return reply(lua, "anchored", *entry, before, before, 0.0);
        if (seconds <= 0.0) return reply(lua, "paused_game_clock", *entry, before, before, 0.0);

        const double boost = 1.0 + owned.regen_count * 0.3;
        const int steps = static_cast<int>(std::ceil(seconds / max_step));
        const double step = seconds / steps;
        auto previous = before;
        for (int i = 0; i < steps; ++i)
        {
            health.step(step, boost, owned.hunger_count);
            const bool exact_scope = entry->scope.current();
            const bool host_unchanged = exact_scope && health.read(health.stock) == host;
            if (host_unchanged && health.maximum->GetPropertyValueInContainer(pawn) == maximum)
            {
                const auto after = health.snapshot();
                if (prove_step(previous, after, step, boost))
                {
                    previous = after;
                    continue;
                }
            }
            entry->blocked = true;
            if (exact_scope) health.restore(before);
            return reply(lua, host_unchanged && health.snapshot() == before
                ? "rolled_back_readback" : "mutation_unverified_readback");
        }

        entry->total += seconds;
        ++entry->pulses;
        return reply(lua, "applied", *entry, before, previous, seconds);
    }

    int clear(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() > 1 ||
            (lua.get_stack_size() == 1 && !lua.is_integer(1))) return 0;
        if (lua.get_stack_size() == 0) entries = {};
        else
        {
            const auto requested = lua.get_integer();
            if (requested > 0)
            {
                for (auto& entry : entries)
                    if (entry.requested == static_cast<Address>(requested)) entry = {};
            }
        }
        return 0;
    }
}

void silverfish::native_guest_regen::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishPulseNativeGuestRegen", pulse);
    lua.register_function("SilverfishClearNativeGuestRegen", clear);
}
