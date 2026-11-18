#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish
{
    void register_native_save_copy_lua(RC::LuaMadeSimple::Lua& lua);
}
