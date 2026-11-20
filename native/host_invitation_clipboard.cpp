#include "host_invitation_clipboard.hpp"

#include <string_view>

#include <Windows.h>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/UnrealInitializer.hpp>

namespace
{
    using Lua = RC::LuaMadeSimple::Lua;
    namespace U = RC::Unreal;

    bool enabled(const wchar_t* name)
    {
        wchar_t flag[2]{};
        return GetEnvironmentVariableW(name, flag, 2) == 1 && flag[0] == L'1';
    }

    bool exact_operation(std::string_view value)
    {
        return value.size() == 32 && value.find_first_not_of("0123456789abcdef") == value.npos
            && value.find_first_not_of('0') != value.npos;
    }

    bool copy_text(HWND window, std::string_view text)
    {
        const int length = static_cast<int>(text.size());
        const int units = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), length, nullptr, 0);
        if (units <= 0) return false;
        const HGLOBAL block = GlobalAlloc(GMEM_MOVEABLE, (static_cast<SIZE_T>(units) + 1) * sizeof(wchar_t));
        if (!block) return false;
        auto* output = static_cast<wchar_t*>(GlobalLock(block));
        if (!output)
        {
            GlobalFree(block);
            return false;
        }
        const bool converted = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), length, output, units) == units;
        output[units] = L'\0';
        GlobalUnlock(block);
        if (converted && OpenClipboard(window))
        {
            // Native ownership of the movable block transfers only on success.
            const bool copied = EmptyClipboard() && SetClipboardData(CF_UNICODETEXT, block);
            CloseClipboard();
            if (copied) return true;
        }
        GlobalFree(block);
        return false;
    }

    bool copy_invitation(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || enabled(L"SILVERFISH_COOP_HEADLESS")
            || !enabled(L"SILVERFISH_COOP_NATIVE_EVENTS") || !enabled(L"SILVERFISH_COOP_NATIVE_ZONE")
            || lua.get_stack_size() != 2 || !lua.is_string(1) || !lua.is_string(2)) return false;
        DWORD pid = 0;
        const auto window = GetForegroundWindow();
        if (!window || !GetWindowThreadProcessId(window, &pid) || pid != GetCurrentProcessId()) return false;

        size_t data_bytes = 0, op_bytes = 0;
        const auto* data = lua_tolstring(lua.get_lua_state(), 1, &data_bytes);
        const auto* operation = lua_tolstring(lua.get_lua_state(), 2, &op_bytes);
        if (!data || !operation || data_bytes == 0 || data_bytes > 32768
            || !exact_operation({operation, op_bytes})) return false;

        std::string_view invite(data, data_bytes);
        if (invite.ends_with("\n")) invite.remove_suffix(1);
        if (invite.ends_with("\r")) invite.remove_suffix(1);
        if (!invite.starts_with("{\"schema\":") || !invite.ends_with("}")
            || invite.find_first_of(std::string_view("\0\r\n\t", 4)) != invite.npos) return false;
        return copy_text(window, invite);
    }
}

void silverfish::host_invitation_clipboard::register_lua(Lua& lua)
{
    lua.register_function("SilverfishCopyHostInvitation", [](const Lua& state) {
        state.set_bool(copy_invitation(state));
        return 1;
    });
}
