#include "guest_health.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <limits>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace health = silverfish::native_guest_health;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr std::size_t hit_points = 0, maximum = 1, food = 2, regeneration = 3, stimulant = 4;
    constexpr std::size_t fatigue = 5, intoxication = 6, sickness = 7, radiation = 8;
    constexpr double tolerance = .0001;
    constexpr std::wstring_view slot_prefix = STR("SFCoopGuest_");

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

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool is_false(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && !property->GetPropertyValueInContainer(owner);
    }

    bool matches(double current, double requested)
    {
        return std::abs(current - requested) <= tolerance;
    }

    struct Binding
    {
        U::FWeakObjectPtr pawn, controller, world, instance, save, pawn_class, save_class;
        std::string map, session, epoch, token, name;
        std::uint64_t id{};
        std::array<U::FDoubleProperty*, 9> numeric{};
        U::FMapProperty* flags{};
        U::FNameProperty* key{};
        U::FIntProperty* value{};
        U::FScriptMapLayout layout{};
        U::FIntProperty* pawn_intoxication{};

        bool bind_schema(U::UObject* player, U::UObject* view)
        {
            auto* player_type = player->GetClassPrivate();
            auto* view_type = view->GetClassPrivate();
            if (view_type->GetPathName() != STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")) return false;
            numeric[hit_points] = field<U::FDoubleProperty>(view_type, STR("PlayerHealth"));
            numeric[maximum] = field<U::FDoubleProperty>(player_type, STR("MaxHealth"));
            numeric[food] = field<U::FDoubleProperty>(view_type, STR("PlayerFood"));
            numeric[regeneration] = field<U::FDoubleProperty>(view_type, STR("PlayerRegen"));
            numeric[stimulant] = field<U::FDoubleProperty>(view_type, STR("PlayerStim"));
            numeric[radiation] = field<U::FDoubleProperty>(view_type, STR("PlayerRads"));
            const auto qualified = [&](std::size_t index, U::UClass* owner) {
                auto* p = numeric[index];
                return p && p->GetSize() == sizeof(double) && p->GetOffset_Internal() >= 0 &&
                    p->GetOffset_Internal() <= owner->GetPropertiesSize() - static_cast<int>(sizeof(double));
            };
            for (const auto index : {hit_points, food, regeneration, stimulant, radiation})
                if (!qualified(index, view_type)) return false;
            if (!qualified(maximum, player_type)) return false;
            pawn_intoxication = field<U::FIntProperty>(player_type, STR("Intoxication"));
            flags = field<U::FMapProperty>(view_type, STR("Flags"));
            key = flags ? U::CastField<U::FNameProperty>(flags->GetKeyProp()) : nullptr;
            value = flags ? U::CastField<U::FIntProperty>(flags->GetValueProp()) : nullptr;
            if (!pawn_intoxication || !key || !value || key->GetOffset_Internal() != 0 ||
                key->GetArrayDim() != 1 || value->GetArrayDim() != 1 || key->GetSize() != sizeof(U::FName) ||
                value->GetSize() != sizeof(std::int32_t) || !key->HasAnyPropertyFlags(U::CPF_HasGetValueTypeHash))
                return false;
            layout = U::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(),
                value->GetSize(), value->GetMinAlignment());
            if (value->GetOffset_Internal() != layout.ValueOffset) return false;
            pawn_class = player_type;
            save_class = view_type;
            return true;
        }

        bool current(const health::ClientSeat& seat) const
        {
            auto* view = save.Get();
            auto* local_player = object(seat.controller, STR("Player"));
            auto* viewport = object(local_player, STR("ViewportClient"));
            if (!id || pawn.Get() != seat.pawn || controller.Get() != seat.controller ||
                world.Get() != seat.world || instance.Get() != seat.instance || !live(view) ||
                pawn_class.Get() != seat.pawn->GetClassPrivate() || save_class.Get() != view->GetClassPrivate() ||
                !is_false(seat.pawn, STR("Dead")) || !is_false(seat.controller, STR("Loading")) ||
                !live(viewport) || object(viewport, STR("World")) != seat.world ||
                object(viewport, STR("GameInstance")) != seat.instance ||
                object(seat.pawn, STR("Save")) != view || object(seat.instance, STR("SaveSlot_Ref")) != view ||
                !is_false(seat.instance, STR("SaveAfterTransition"))) return false;
            auto* slot = field<U::FStrProperty>(seat.instance, STR("CurrentSaveSlot"));
            if (!slot) return false;
            const auto& selected = slot->GetPropertyValueInContainer(seat.instance);
            if (selected.Len() != slot_prefix.size() + token.size()) return false;
            const std::wstring_view path{*selected, static_cast<std::size_t>(selected.Len())};
            return path.starts_with(slot_prefix) && std::ranges::equal(path.substr(slot_prefix.size()), token);
        }

        double read_numeric(std::size_t index) const
        {
            return numeric[index]->GetPropertyValueInContainer(index == maximum ? pawn.Get() : save.Get());
        }

        std::optional<std::array<std::int32_t, 3>> read_flags() const
        {
            auto* values = flags->ContainerPtrToValuePtr<U::FScriptMap>(save.Get());
            if (values->Num() < 0 || values->Num() > values->GetMaxIndex() || values->GetMaxIndex() >= 16384)
                return {};
            const auto hash = [&](const void* data) { return key->GetValueTypeHash(data); };
            const auto equal = [&](const void* a, const void* b) { return key->Identical(a, b); };
            static const std::array names{U::FName{STR("Player_Fatigue")}, U::FName{STR("Player_Intox")},
                U::FName{STR("Sickness")}};
            std::array<std::int32_t, 3> result{};
            for (std::size_t index = 0; index < names.size(); ++index)
                if (const auto* found = values->FindValue(&names[index], layout, hash, equal))
                    result[index] = value->GetPropertyValue(found);
            return result;
        }
    };

    Binding binding;
    std::uint64_t next_binding{};

    bool save_token(std::string_view value)
    {
        return value.size() == 32 && value.find_first_not_of('0') != value.npos &&
            std::ranges::all_of(value, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
    }

    int bind(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 7 || !lua.is_integer(1) || !lua.is_integer(2)) return 0;
        auto* stack = lua.get_lua_state();
        for (int index = 3; index <= 7; ++index)
            if (lua_type(stack, index) != LUA_TSTRING) return 0;
        const auto text = [stack](int index) -> std::string_view {
            std::size_t size{};
            const auto* data = lua_tolstring(stack, index, &size);
            return {data, size};
        };
        const auto map = text(3), session = text(4), epoch = text(5), token = text(6), name = text(7);
        if (!save_token(token) || !silverfish::world_path::package(map) || !name.starts_with("Player_FP_C ") ||
            name.size() > 512 || name.find_first_of(std::string_view{"\r\n\t\0", 4}) != name.npos) return 0;
        const auto seat = health::client_damage_seat(session, epoch);
        if (!seat || reinterpret_cast<std::uintptr_t>(seat->pawn) != static_cast<std::uintptr_t>(lua_tointeger(stack, 1)))
            return 0;
        auto* view = object(seat->instance, STR("SaveSlot_Ref"));
        if (!live(view) || reinterpret_cast<std::uintptr_t>(view) != static_cast<std::uintptr_t>(lua_tointeger(stack, 2)))
            return 0;
        if (binding.map == map && binding.session == session && binding.epoch == epoch && binding.token == token &&
            binding.name == name && binding.save.Get() == view && binding.current(*seat)) return 0;
        const auto path = RC::to_string(seat->world->GetPathName());
        auto* outer = view->GetOuterPrivate();
        if (std::string_view(path).substr(0, path.find('.')) != map || RC::to_string(seat->pawn->GetFullName()) != name ||
            !outer || outer->GetPathName() != STR("/Engine/Transient")) return 0;
        Binding fresh;
        if (!fresh.bind_schema(seat->pawn, view)) return 0;
        fresh.pawn = seat->pawn;
        fresh.controller = seat->controller;
        fresh.world = seat->world;
        fresh.instance = seat->instance;
        fresh.save = view;
        fresh.map = map;
        fresh.session = session;
        fresh.epoch = epoch;
        fresh.token = token;
        fresh.name = name;
        fresh.id = ++next_binding;
        if (fresh.current(*seat)) binding = std::move(fresh);
        return 0;
    }

    template <typename T>
    std::optional<T> number(std::string_view text)
    {
        if (text.empty() || text.size() > 32) return {};
        T value{};
        const auto* end = text.data() + text.size();
        const auto parsed = std::from_chars(text.data(), end, value);
        if (parsed.ec != std::errc{} || parsed.ptr != end) return {};
        return value;
    }

    bool flag_field(std::size_t index)
    {
        return index >= fatigue && index <= sickness;
    }
}

std::optional<health::Prepared> health::prepare(std::span<const std::string_view> fields)
{
    if (!U::IsInGameThreadRaw() || !binding.id || (fields.size() != 9 && fields.size() != 11 &&
        fields.size() != 12 && fields.size() != 14 && fields.size() != 15 && fields.size() != 16) ||
        fields[1] != "native-guest-health-project" || fields[2] != binding.map || fields[3] != binding.session ||
        fields[4] != binding.epoch || fields[5] != binding.token || fields[6] != binding.name) return {};
    Prepared operation{};
    operation.count = fields.size() - 7;
    operation.binding = binding.id;
    for (std::size_t index = 0; index < operation.count; ++index)
    {
        const auto raw = fields[index + 7];
        if (index > maximum && raw == "-") continue;
        const auto value = flag_field(index) ? number<std::int32_t>(raw).transform([](auto v) { return double(v); })
            : number<double>(raw);
        if (!value || !std::isfinite(*value)) return {};
        operation.values[index] = value;
    }
    const double limit = *operation.values[maximum];
    const std::array<double, 9> upper{limit, 1'000'000, 200, limit, 1'000'000, 100, 100,
        double((std::numeric_limits<std::int32_t>::max)()), 1'000'000};
    for (std::size_t index = 0; index < operation.count; ++index)
    {
        const auto value = operation.values[index];
        const double lower = index == maximum ? 1 : index == sickness ? double((std::numeric_limits<std::int32_t>::min)()) : 0;
        if (value && (*value < lower || *value > upper[index])) return {};
    }
    const auto seat = client_damage_seat(binding.session, binding.epoch);
    if (!seat || !binding.current(*seat)) return {};

    if (operation.values[fatigue] || operation.values[intoxication] || operation.values[sickness])
    {
        const auto flags = binding.read_flags();
        if (!flags) return {};
        for (std::size_t index = fatigue; index <= sickness; ++index)
            if (operation.values[index] && *operation.values[index] != (*flags)[index - fatigue]) return {};
        if (operation.values[intoxication] &&
            binding.pawn_intoxication->GetPropertyValueInContainer(seat->pawn) != *operation.values[intoxication])
            return {};
    }
    // The Lua path refreshes the stock inventory info when these fields change.
    const auto food_changed = operation.values[food] && !matches(binding.read_numeric(food), *operation.values[food]);
    const auto rads_changed = operation.values[radiation] && !matches(binding.read_numeric(radiation), *operation.values[radiation]);
    if (food_changed || rads_changed)
    {
        auto* menus = field<U::FArrayProperty>(seat->controller, STR("MenuStack"));
        if (!menus || U::FScriptArrayHelper(menus, menus->ContainerPtrToValuePtr<void>(seat->controller)).Num() != 0)
            return {};
    }
    return operation;
}

std::optional<std::string> health::apply(const Prepared& operation)
{
    // The ticket has been claimed. Confirm the binding still owns the same
    // seat before writing to its private save.
    if (!binding.id || operation.binding != binding.id || !U::IsInGameThreadRaw()) return {};
    const auto seat = client_damage_seat(binding.session, binding.epoch);
    if (!seat || !binding.current(*seat)) return {};
    auto* pawn = seat->pawn;
    auto* save = binding.save.Get();
    for (const auto index : {maximum, hit_points, food, regeneration, stimulant, radiation})
        if (const auto value = operation.values[index]; value && !matches(binding.read_numeric(index), *value))
            binding.numeric[index]->SetPropertyValueInContainer(index == maximum ? pawn : save, *value);

    auto actual = operation.values;
    if (actual[fatigue] || actual[intoxication] || actual[sickness])
    {
        const auto flags = binding.read_flags();
        if (!flags) return {};
        for (std::size_t index = fatigue; index <= sickness; ++index)
            if (actual[index]) actual[index] = (*flags)[index - fatigue];
        if (actual[intoxication] &&
            binding.pawn_intoxication->GetPropertyValueInContainer(pawn) != *actual[intoxication]) return {};
    }
    std::string reply;
    reply.reserve(operation.count * 16 + 1);
    for (std::size_t index = 0; index < operation.count; ++index)
    {
        reply.push_back('\t');
        if (!actual[index])
        {
            reply.push_back('-');
            continue;
        }
        if (!flag_field(index)) actual[index] = binding.read_numeric(index);
        const auto value = *actual[index];
        if (!matches(value, *operation.values[index])) return {};
        std::array<char, 64> buffer{};
        const auto end = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
        if (end.ec != std::errc{}) return {};
        reply.append(buffer.data(), end.ptr);
    }
    reply.push_back('\n');
    return reply;
}

void health::register_lua(Lua& lua)
{
    lua.register_function("SilverfishBindNativeGuestHealth", bind);
}
