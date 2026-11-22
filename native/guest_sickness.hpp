#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_guest_sickness
{
    struct SicknessSnapshot
    {
        std::int32_t guest_sickness{}, host_sickness{};
        bool guest_found{}, host_found{};
        int infect_reads{}, infect_writes{};
    };

    bool read_exact(RC::Unreal::UObject* pawn, RC::Unreal::UObject* detached, RC::Unreal::UObject* stock,
                    SicknessSnapshot& out);
    bool read_passive_amplification(RC::Unreal::UObject* save, std::int32_t& bonus);
    bool barter_psy(RC::Unreal::UObject* pawn, RC::Unreal::UObject* detached, RC::Unreal::UObject* stock,
                    std::int32_t amount, bool apply);
    void install_hooks();
    void uninstall_hooks();
    void clear_scope();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
