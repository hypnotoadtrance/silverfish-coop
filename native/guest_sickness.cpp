#include "guest_sickness.hpp"

#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <optional>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto infect_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:InfectPlayer");
    constexpr auto slow_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:SlowCheck");
    constexpr auto check_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Check_SaveFlag");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Add_PointTo_SaveFlag");
    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr int max_entries = 4;
    constexpr int max_flags = 16384;
    constexpr auto lease = std::chrono::seconds(2);

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && opt_in("SILVERFISH_COOP_NATIVE_GUEST_SICKNESS");
    }

    bool fixtures()
    {
        return opt_in("SILVERFISH_COOP_NATIVE_FIXTURES") && opt_in("SILVERFISH_COOP_HEADLESS");
    }

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

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    template <typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool output = false)
    {
        auto* property = field<T>(function, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == output &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= function->GetParmsSize()
            ? property : nullptr;
    }

    U::UFunction* blueprint(const U::TCHAR* path, U::UClass* owner, int count, int size)
    {
        auto* function = find<U::UFunction>(path);
        return owner && function && function->GetOuterPrivate() == owner &&
            function->GetNumParms() == count && function->GetParmsSize() == size && !function->GetReturnProperty() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    struct Schema
    {
        U::UFunction* infect{};
        U::UFunction* slow{};
        U::UFunction* check_flag{};
        U::UFunction* add_flag{};
        U::FIntProperty* amount{};
        U::FNameProperty* check_key{};
        U::FBoolProperty* check_found{};
        U::FIntProperty* check_value{};
        U::FNameProperty* add_key{};
        U::FIntProperty* add_value{};
        U::FBoolProperty* overwrite{};
        U::FBoolProperty* force_decrease{};
        U::FName sickness{}, fatigue{};

        bool bind()
        {
            auto* pawn = find<U::UClass>(pawn_path);
            auto* instance = find<U::UClass>(instance_path);
            infect = blueprint(infect_path, pawn, 1, 4);
            slow = blueprint(slow_path, pawn, 0, 0);
            check_flag = blueprint(check_path, instance, 3, 16);
            add_flag = blueprint(add_path, instance, 4, 14);
            if (!infect || !slow || !check_flag || !add_flag) return false;
            amount = parameter<U::FIntProperty>(infect, STR("InfectionAmmount"), 0);
            check_key = parameter<U::FNameProperty>(check_flag, STR("Key"), 0);
            check_found = parameter<U::FBoolProperty>(check_flag, STR("Found"), 8, true);
            check_value = parameter<U::FIntProperty>(check_flag, STR("Value"), 12, true);
            add_key = parameter<U::FNameProperty>(add_flag, STR("KeyID"), 0);
            add_value = parameter<U::FIntProperty>(add_flag, STR("Value"), 8);
            overwrite = parameter<U::FBoolProperty>(add_flag, STR("Overwrite"), 12);
            force_decrease = parameter<U::FBoolProperty>(add_flag, STR("Force_Decrease"), 13);
            sickness = U::FName{STR("Sickness"), U::FNAME_Find};
            fatigue = U::FName{STR("Player_Fatigue"), U::FNAME_Find};
            return amount && check_key && check_found && check_value && add_key && add_value &&
                overwrite && force_decrease && !sickness.IsNone() && !fatigue.IsNone();
        }
    } schema;

    struct Flag
    {
        bool found{};
        std::int32_t value{};
        bool operator==(const Flag&) const = default;
    };

    struct Flags
    {
        U::FScriptMap* map{};
        U::FNameProperty* key{};
        U::FIntProperty* value{};
        U::FScriptMapLayout layout{};

        bool bind(U::UObject* save)
        {
            auto* property = field<U::FMapProperty>(save, STR("Flags"));
            key = property ? U::CastField<U::FNameProperty>(property->GetKeyProp()) : nullptr;
            value = property ? U::CastField<U::FIntProperty>(property->GetValueProp()) : nullptr;
            if (!key || !value || key->GetArrayDim() != 1 || value->GetArrayDim() != 1 ||
                key->GetSize() != sizeof(U::FName) || key->GetOffset_Internal() != 0 ||
                value->GetSize() != sizeof(std::int32_t) || !key->HasAnyPropertyFlags(U::CPF_HasGetValueTypeHash)) return false;
            layout = U::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(),
                value->GetSize(), value->GetMinAlignment());
            if (value->GetOffset_Internal() != layout.ValueOffset) return false;
            map = property->ContainerPtrToValuePtr<U::FScriptMap>(save);
            return map && map->Num() >= 0 && map->Num() <= map->GetMaxIndex() && map->GetMaxIndex() <= max_flags;
        }

        void* find_value(const U::FName& name) const
        {
            return map->FindValue(&name, layout, [&](const void* raw) { return key->GetValueTypeHash(raw); },
                [&](const void* a, const void* b) { return key->Identical(a, b); });
        }

        Flag read(const U::FName& name) const
        {
            const auto* pair = find_value(name);
            return {pair != nullptr, pair ? value->GetPropertyValue(pair) : 0};
        }

        bool write(const U::FName& name, Flag before, std::int32_t expected) const
        {
            const auto count = map->Num();
            if (!before.found && map->GetMaxIndex() >= max_flags) return false;
            auto* pair = map->FindOrAdd(&name, layout, [&](const void* raw) { return key->GetValueTypeHash(raw); },
                [&](const void* a, const void* b) { return key->Identical(a, b); },
                [&](void* destination_key, void* destination_value) {
                    key->InitializeValue(destination_key);
                    key->SetPropertyValue(destination_key, name);
                    value->InitializeValue(destination_value);
                });
            if (!pair) return false;
            value->SetPropertyValue(pair, expected);
            return map->Num() == count + (before.found ? 0 : 1) && read(name) == Flag{true, expected};
        }
    };

    struct Proof
    {
        int infect_reads{}, infect_writes{}, slow_reads{}, slow_writes{};
        Flag previous{}, current{}, host{};
        std::string_view status{"watching"};
        bool quarantined{};
    };

    struct Entry
    {
        U::FWeakObjectPtr pawn{}, guest{}, stock{}, instance{};
        Clock::time_point expires{};
        Proof proof{};
        std::uint64_t generation{};
    };

    std::array<Entry, max_entries> entries{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;
    std::atomic<std::uint64_t> generation{1};
    bool schema_bound{};

    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    Entry* entry_for(U::UObject* object)
    {
        if (!object) return nullptr;
        const auto active = generation.load(std::memory_order_acquire);
        const auto found = std::ranges::find_if(entries, [&](const Entry& entry) {
            return entry.generation == active && entry.pawn.Get() == object;
        });
        return found == entries.end() ? nullptr : &*found;
    }

    Entry* entry_for(Address requested)
    {
        if (!requested) return nullptr;
        const auto active = generation.load(std::memory_order_acquire);
        const auto found = std::ranges::find_if(entries, [&](const Entry& entry) {
            return entry.generation == active && address(entry.pawn.Get()) == requested;
        });
        return found == entries.end() ? nullptr : &*found;
    }

    bool current(const Entry& entry)
    {
        return entry.generation == generation.load(std::memory_order_acquire) && Clock::now() < entry.expires &&
            silverfish::native_guest_regen::qualifies_detached_guest(entry.pawn.Get(), entry.guest.Get(), entry.stock.Get());
    }

    void* output(U::FFrame& frame, U::FProperty* property)
    {
        auto* item = frame.OutParms();
        void* data{};
        for (int remaining = frame.Node()->GetNumParms(); item && remaining > 0; --remaining, item = item->NextOutParm)
        {
            if (item->Property != property) continue;
            if (data) return nullptr;
            data = item->PropAddr;
        }
        return item ? nullptr : data;
    }

    std::optional<std::int32_t> write_value(bool infect, U::FFrame& caller, void* locals, Flag before)
    {
        const auto value = schema.add_value->GetPropertyValueInContainer(locals);
        const bool overwrite = schema.overwrite->GetPropertyValueInContainer(locals);
        const bool force = schema.force_decrease->GetPropertyValueInContainer(locals);
        if (infect)
        {
            if (!caller.Locals() || overwrite || force) return {};
            const auto amount = schema.amount->GetPropertyValueInContainer(caller.Locals());
            if (amount > 0 ? value < 1 || value > amount : amount >= 0 || value != amount || before.value <= 0) return {};
        }
        else if (overwrite && force && value >= 0 && value <= 120) return value;
        else if (overwrite || force || value != -1 || !before.found) return {};
        const auto sum = static_cast<std::int64_t>(before.value) + value;
        if (sum < std::numeric_limits<std::int32_t>::min() || sum > std::numeric_limits<std::int32_t>::max()) return {};
        return static_cast<std::int32_t>(sum);
    }

    // Only direct Blueprint callers with the exact personal keys are subject
    // to redirection. Nothing changes the GameInstance save pointer or cache.
    void redirect(Iteration& iteration, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!U::IsInGameThreadRaw() || !schema_bound || iteration.OriginalFunctionCallPrevented()) return;
        const bool lookup = frame.Node() == schema.check_flag;
        if (!lookup && frame.Node() != schema.add_flag) return;
        auto* caller = frame.PreviousFrame();
        if (!caller || frame.Object() != receiver) return;
        const bool infect = caller->Node() == schema.infect;
        if (!infect && caller->Node() != schema.slow) return;
        auto* entry = entry_for(caller->Object());
        auto* locals = frame.Locals();
        if (!entry || !locals || receiver != entry->instance.Get()) return;
        const auto key = (lookup ? schema.check_key : schema.add_key)->GetPropertyValueInContainer(locals);
        if (key != schema.sickness && (!lookup || !infect || key != schema.fatigue)) return;

        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        auto& proof = entry->proof;
        Flags detached, stock;
        const bool qualified = !proof.quarantined && current(*entry) &&
            detached.bind(entry->guest.Get()) && stock.bind(entry->stock.Get());
        if (lookup)
        {
            auto* found = output(frame, schema.check_found);
            auto* value = output(frame, schema.check_value);
            if (!found || !value || found == value || reinterpret_cast<Address>(value) % alignof(std::int32_t))
            {
                if (!proof.quarantined) proof.status = "refused_stock_output";
                return;
            }
            const auto observed = qualified ? detached.read(key) : Flag{};
            schema.check_found->SetPropertyValue(found, observed.found);
            schema.check_value->SetPropertyValue(value, observed.value);
            if (!qualified)
            {
                if (!proof.quarantined) proof.status = "refused_detached_binding";
                return;
            }
            ++(infect ? proof.infect_reads : proof.slow_reads);
            return;
        }
        if (!qualified)
        {
            if (!proof.quarantined) proof.status = "refused_detached_binding";
            return;
        }
        const auto before = detached.read(key);
        const auto host = stock.read(key);
        const auto expected = write_value(infect, *caller, locals, before);
        if (!expected) { proof.status = "refused_stock_write_parameters"; return; }
        const auto wrote = detached.write(key, before, *expected);
        proof.previous = before;
        proof.current = detached.read(key);
        proof.host = stock.read(key);
        if (!wrote || !current(*entry) || proof.host != host || proof.current != Flag{true, *expected})
        {
            proof.status = "mutation_unverified_stock_sickness_readback";
            proof.quarantined = true;
            return;
        }
        ++(infect ? proof.infect_writes : proof.slow_writes);
        proof.status = infect ? "redirected_infection" : "redirected_sickness_tick";
    }

    bool available() { return U::IsInGameThreadRaw() && callback != Hook::ERROR_ID; }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
            return result(lua, false, "refused_sickness_scope");
        const auto pawn_address = lua.get_integer();
        const auto guest_address = lua.get_integer();
        const auto stock_address = lua.get_integer();
        if (pawn_address <= 0 || guest_address <= 0 || stock_address <= 0 || guest_address == stock_address)
            return result(lua, false, "refused_sickness_scope");
        if (!schema_bound) schema_bound = schema.bind();
        if (!schema_bound) return result(lua, false, "refused_stock_sickness_schema");

        auto* existing = entry_for(static_cast<Address>(pawn_address));
        auto* pawn = existing ? existing->pawn.Get() : nullptr;
        if (!pawn)
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (address(candidate) != static_cast<Address>(pawn_address)) return RC::LoopAction::Continue;
                pawn = candidate;
                return RC::LoopAction::Break;
            });
        auto* guest = object(pawn, STR("Save"));
        auto* instance = object(pawn, STR("As Game Instance FP"));
        auto* stock = object(instance, STR("SaveSlot_Ref"));
        Flags detached_flags, stock_flags;
        if (address(guest) != static_cast<Address>(guest_address) || address(stock) != static_cast<Address>(stock_address) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, guest, stock) ||
            !detached_flags.bind(guest) || !stock_flags.bind(stock))
            return result(lua, false, "refused_detached_binding");
        if (existing && existing->guest.Get() == guest && existing->stock.Get() == stock && existing->instance.Get() == instance)
        {
            existing->expires = Clock::now() + lease;
            return result(lua, true, existing->proof.status);
        }
        const auto active = generation.load(std::memory_order_acquire);
        const auto empty = std::ranges::find_if(entries, [active](const Entry& entry) {
            return entry.generation != active || !entry.pawn.Get();
        });
        if (!existing && empty == entries.end()) return result(lua, false, "refused_sickness_roster_full");
        auto& entry = existing ? *existing : *empty;
        entry = {};
        entry.pawn = pawn;
        entry.guest = guest;
        entry.stock = stock;
        entry.instance = instance;
        entry.expires = Clock::now() + lease;
        entry.generation = active;
        return result(lua, true, entry.proof.status);
    }

    int read(const Lua& lua)
    {
        if (!available() || !schema_bound || lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        auto* entry = requested > 0 ? entry_for(static_cast<Address>(requested)) : nullptr;
        if (!entry || !current(*entry)) return 0;
        Flags guest, stock;
        if (!guest.bind(entry->guest.Get()) || !stock.bind(entry->stock.Get())) return 0;
        const auto value = guest.read(schema.sickness);
        const auto canonical = stock.read(schema.sickness);
        const auto& proof = entry->proof;
        lua.set_string(proof.status);
        lua.set_integer(proof.infect_reads);
        lua.set_integer(proof.infect_writes);
        lua.set_integer(proof.slow_reads);
        lua.set_integer(proof.slow_writes);
        lua.set_integer(value.value);
        lua.set_integer(canonical.value);
        lua.set_integer(proof.previous.value);
        lua.set_integer(proof.current.value);
        return 9;
    }

    int clear(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() > 1 || (lua.get_stack_size() && !lua.is_integer(1))) return 0;
        if (!lua.get_stack_size()) silverfish::native_guest_sickness::clear_scope();
        else if (const auto requested = lua.get_integer(); requested > 0)
        {
            if (auto* entry = entry_for(static_cast<Address>(requested))) *entry = {};
        }
        return 0;
    }

    int probe(const Lua& lua)
    {
        if (!available() || !fixtures() || !schema_bound || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_integer(2)) return result(lua, false, "refused_sickness_fixture");
        const auto requested = lua.get_integer();
        const auto input = lua.get_integer();
        auto* entry = requested > 0 ? entry_for(static_cast<Address>(requested)) : nullptr;
        if (!entry || !current(*entry) || entry->proof.quarantined || input == 0 || input < -120 || input > 120)
            return result(lua, false, "refused_sickness_fixture");
        Flags guest, stock;
        if (!guest.bind(entry->guest.Get()) || !stock.bind(entry->stock.Get()))
            return result(lua, false, "refused_sickness_fixture_flags");
        const auto previous = guest.read(schema.sickness);
        const auto host = stock.read(schema.sickness);
        const auto reads = entry->proof.infect_reads;
        const auto writes = entry->proof.infect_writes;
        auto amount = static_cast<std::int32_t>(input);
        entry->pawn.Get()->ProcessEvent(schema.infect, &amount);
        const auto expected_writes = input > 0 || previous.value > 0 ? 1 : 0;
        if (!current(*entry) || host != stock.read(schema.sickness) || entry->proof.infect_reads != reads + 1 ||
            entry->proof.infect_writes != writes + expected_writes)
        {
            entry->proof.status = "mutation_unverified_stock_sickness_fixture";
            entry->proof.quarantined = true;
            return result(lua, false, entry->proof.status);
        }
        return result(lua, true, "stock_infection_verified");
    }
}

bool silverfish::native_guest_sickness::read_exact(RC::Unreal::UObject* pawn, RC::Unreal::UObject* detached,
                                                   RC::Unreal::UObject* stock, SicknessSnapshot& out)
{
    if (!available() || !schema_bound) return false;
    auto* entry = entry_for(pawn);
    if (!entry || entry->guest.Get() != detached || entry->stock.Get() != stock ||
        !current(*entry) || entry->proof.quarantined) return false;
    Flags guest, host;
    if (!guest.bind(detached) || !host.bind(stock)) return false;
    const auto personal = guest.read(schema.sickness);
    const auto canonical = host.read(schema.sickness);
    out = {personal.value, canonical.value, personal.found, canonical.found,
        entry->proof.infect_reads, entry->proof.infect_writes};
    return true;
}

void silverfish::native_guest_sickness::install_hooks()
{
    if (!enabled() || callback != Hook::ERROR_ID) return;
    callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(
        redirect, {false, false, owner, STR("Redirect exact guest Sickness flag effects")});
}

void silverfish::native_guest_sickness::uninstall_hooks()
{
    if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
    callback = Hook::ERROR_ID;
    schema_bound = false;
    schema = {};
    entries = {};
}

void silverfish::native_guest_sickness::clear_scope()
{
    generation.fetch_add(1, std::memory_order_release);
}

void silverfish::native_guest_sickness::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    clear_scope();
    lua.register_function("SilverfishSetNativeGuestSickness", set_scope);
    lua.register_function("SilverfishReadNativeGuestSickness", read);
    lua.register_function("SilverfishClearNativeGuestSickness", clear);
    if (fixtures()) lua.register_function("SilverfishProbeNativeGuestSickness", probe);
}
