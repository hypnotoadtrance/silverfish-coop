#pragma once

#include <string>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; class UWorld; }

namespace silverfish::native_save_scope
{
    struct TitleResult
    {
        bool accepted{}, mutated{};
        std::string_view message;
    };

    TitleResult select_at_title(RC::Unreal::UWorld* world, std::string_view id);
    TitleResult release_at_title(RC::Unreal::UWorld* world, RC::Unreal::UObject* controller, std::string_view id);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    bool active();
    std::string selected_id();
    void clear();
    void uninstall_hooks();
}
