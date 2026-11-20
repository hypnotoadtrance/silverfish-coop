#pragma once

#include <array>
#include <cstdint>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::native_guest_armor
{
    bool worn_crafting_passive(std::uintptr_t pawn, std::uintptr_t save, std::array<std::uint32_t, 4>& suit);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
