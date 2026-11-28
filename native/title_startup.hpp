#pragma once

#include "title_entry.hpp"

namespace RC::Unreal { class UEngine; }

namespace silverfish::native_title_startup
{
    void install_hooks();
    void uninstall_hooks();
    bool command_attachment_ready();
    std::optional<native_title_entry::Input> command_context(RC::Unreal::UEngine* engine);
}
