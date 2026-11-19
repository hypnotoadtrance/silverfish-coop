#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::native_guest_armor
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
