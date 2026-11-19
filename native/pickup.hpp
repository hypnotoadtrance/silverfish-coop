#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish
{
    void register_native_pickup_lua(RC::LuaMadeSimple::Lua& lua);
}
