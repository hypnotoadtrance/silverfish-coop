#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace RC::Unreal { class UObject; }

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::loot_escrow
{
    bool stock_spade_guid(RC::Unreal::UObject* save, std::string& guid);
    bool sealed_package_guid(RC::Unreal::UObject* save, std::string& guid);
    bool capture_sealed_package(RC::Unreal::UObject* pawn, RC::Unreal::UObject* package, std::uintptr_t scratch,
                                std::string& guid);
    bool verify_sealed_package(std::uintptr_t scratch, std::string_view guid, RC::Unreal::UObject* pawn = nullptr,
                               RC::Unreal::UObject* package = nullptr);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
