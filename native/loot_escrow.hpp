#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace RC::Unreal { class UObject; class UClass; }

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::loot_escrow
{
    bool convert_stock_item(RC::Unreal::UObject* pawn, RC::Unreal::UObject* actor, void* initialized_row);
    bool backpack_has_class(RC::Unreal::UObject* save, RC::Unreal::UClass* item_class);
    bool stock_spade_guid(RC::Unreal::UObject* save, std::string& guid);
    bool sealed_package_guid(RC::Unreal::UObject* save, std::string& guid);
    bool capture_sealed_package(RC::Unreal::UObject* pawn, RC::Unreal::UObject* package, std::uintptr_t scratch,
                                std::string& guid);
    bool verify_sealed_package(std::uintptr_t scratch, std::string_view guid, RC::Unreal::UObject* pawn = nullptr,
                               RC::Unreal::UObject* package = nullptr);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
