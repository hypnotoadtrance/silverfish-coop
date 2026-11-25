#pragma once

#include <array>
#include <optional>
#include <string>
#include <string_view>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class FStructProperty;
    class UObject;
}

namespace silverfish::native_guest_item
{
    using ShotVector = std::array<double, 3>;
    struct ShotView
    {
        ShotVector direction{};
        std::optional<ShotVector> eye, target, muzzle;
        std::string contact_actor;
        std::optional<ShotVector> contact;
    };

    RC::Unreal::UObject* active_loki_barrel(RC::Unreal::UObject* pawn, RC::Unreal::UObject* receiver);
    std::string local_equipped_identity(RC::Unreal::UObject* pawn, RC::Unreal::UObject* receiver);
    ShotView capture_shot_view(RC::Unreal::UObject* pawn, RC::Unreal::UObject* held, RC::Unreal::UObject* barrel = nullptr);
    bool take_ammo_supported(RC::Unreal::UObject* item_class, int rounds);
    std::optional<int> quick_drop_ammo(RC::Unreal::UObject* pawn, RC::Unreal::UObject* save,
                                       RC::Unreal::UObject* item_class, int slot, int stored);
    std::string_view finish_local_removal(RC::Unreal::UObject* pawn, RC::Unreal::UObject* save,
                                         RC::Unreal::UObject* prior_actor, int slot, std::string_view identity,
                                         std::optional<int> rounds);
    bool split_stock_stack(RC::Unreal::UObject* context, RC::Unreal::FStructProperty* row_type, const void* input,
                           int amount, void* portion, void* remainder);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void shutdown();
}
