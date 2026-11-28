#pragma once

#include <string>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::local_bridge
{
    bool queue_input(std::string record);
    void append_status_text(std::string& row, std::string_view text);
    void append_status_number(std::string& row, double value);
    void initialize();
    void install_native_dispatch();
    void uninstall_native_dispatch();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void release_script_driver();
    void stop();
}
