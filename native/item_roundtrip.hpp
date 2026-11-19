#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish
{
    void register_native_item_roundtrip_lua(RC::LuaMadeSimple::Lua& lua);
}
