#pragma once

#include <string>

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::local_bridge
{
    bool queue_input(std::string record);
    void install_native_dispatch();
    void uninstall_native_dispatch();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void stop();
}
