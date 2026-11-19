#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::native_fresh_clock
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
