#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_anomaly_fields
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    bool exact_observer_gaze(RC::Unreal::UObject* actor, RC::Unreal::UObject* pawn);
}
