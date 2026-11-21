#include <winsock2.h>
#include <Windows.h>

#include <atomic>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Bind = decltype(&::bind);
    constexpr auto mod_name = STR("TitleListenProof");
    constexpr auto package = STR("/Game/Classes/Maps/PlaytestMap_01");
    Bind system_bind{};
    PVOID volatile* import_slot{};
    H::GlobalCallbackId pre = H::ERROR_ID, post = H::ERROR_ID;
    std::atomic<UINT16> requested{};
    thread_local bool loading_listen_map{};
    int mapped{}, bound{};

    PVOID volatile* find_bind_import(HMODULE image)
    {
        const auto* data = reinterpret_cast<const std::byte*>(image);
        const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(data);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < sizeof(*dos) || dos->e_lfanew > 4096)
            return nullptr;
        const auto* pe = reinterpret_cast<const IMAGE_NT_HEADERS64*>(data + dos->e_lfanew);
        if (pe->Signature != IMAGE_NT_SIGNATURE || pe->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
            return nullptr;
        const auto size = pe->OptionalHeader.SizeOfImage;
        const auto inside = [size](DWORD rva, size_t n) { return rva && rva < size && n <= size - rva; };
        const auto imports = pe->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
        if (!inside(imports.VirtualAddress, imports.Size)) return nullptr;
        const auto* item = reinterpret_cast<const IMAGE_IMPORT_DESCRIPTOR*>(data + imports.VirtualAddress);
        for (DWORD index = 0; index < imports.Size / sizeof(*item) && item[index].Name; ++index)
        {
            const auto& descriptor = item[index];
            if (!inside(descriptor.Name, sizeof("WS2_32.dll")) ||
                _strnicmp(reinterpret_cast<const char*>(data + descriptor.Name), "WS2_32.dll", sizeof("WS2_32.dll")) != 0)
                continue;
            for (DWORD offset = 0; offset < 2048; offset += sizeof(IMAGE_THUNK_DATA64))
            {
                const auto name_rva = descriptor.OriginalFirstThunk + offset;
                const auto slot_rva = descriptor.FirstThunk + offset;
                if (!inside(name_rva, sizeof(IMAGE_THUNK_DATA64)) || !inside(slot_rva, sizeof(IMAGE_THUNK_DATA64)))
                    return nullptr;
                const auto ordinal = reinterpret_cast<const IMAGE_THUNK_DATA64*>(data + name_rva)->u1.Ordinal;
                if (!ordinal) return nullptr;
                if (IMAGE_SNAP_BY_ORDINAL64(ordinal) && IMAGE_ORDINAL64(ordinal) == 2)
                    return reinterpret_cast<PVOID volatile*>(const_cast<std::byte*>(data + slot_rva));
            }
        }
        return nullptr;
    }

    int WSAAPI probe_bind(SOCKET socket, const sockaddr* address, int length)
    {
        const auto port = requested.load();
        if (!port || !address) return system_bind(socket, address, length);
        int type{}, bytes = sizeof(type);
        if (getsockopt(socket, SOL_SOCKET, SO_TYPE, reinterpret_cast<char*>(&type), &bytes) != 0 || type != SOCK_DGRAM)
            return system_bind(socket, address, length);
        const auto* incoming = length == sizeof(sockaddr_in) && address->sa_family == AF_INET
            ? reinterpret_cast<const sockaddr_in*>(address) : nullptr;
        if (incoming && incoming->sin_port != htons(7777) && incoming->sin_addr.s_addr == htonl(INADDR_LOOPBACK))
            return system_bind(socket, address, length);
        if (!incoming || incoming->sin_port != htons(7777) || !loading_listen_map || bound != 0)
        {
            WSASetLastError(WSAEACCES);
            return SOCKET_ERROR;
        }
        sockaddr_in private_address{*incoming};
        private_address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        private_address.sin_port = htons(port);
        const int result = system_bind(socket, reinterpret_cast<sockaddr*>(&private_address), sizeof(private_address));
        if (!result) ++bound;
        return result;
    }

    bool exchange(PVOID from, PVOID to)
    {
        DWORD protection{};
        auto* location = const_cast<PVOID*>(import_slot);
        if (!location || !VirtualProtect(location, sizeof(*location), PAGE_READWRITE, &protection)) return false;
        const auto previous = InterlockedCompareExchangePointer(import_slot, to, from);
        DWORD ignored{};
        VirtualProtect(location, sizeof(*location), protection, &ignored);
        return previous == from;
    }

    void release_import()
    {
        if (requested.exchange(0))
            exchange(reinterpret_cast<PVOID>(probe_bind), reinterpret_cast<PVOID>(system_bind));
    }

    bool is_opted_in()
    {
        wchar_t value[2]{};
        return GetEnvironmentVariableW(L"SF_LISTEN_NATIVE_PROBE", value, 2) == 1 && value[0] == L'1';
    }

    int arm(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer() || requested.load() ||
            pre == H::ERROR_ID || post == H::ERROR_ID) return 0;
        const auto port = lua.get_integer();
        if (port < 1024 || port > 65535 || port == 7777) return 0;
        const auto image = GetModuleHandleW(nullptr);
        const auto winsock = GetModuleHandleW(L"WS2_32.dll");
        system_bind = winsock ? std::bit_cast<Bind>(GetProcAddress(winsock, "bind")) : nullptr;
        import_slot = image ? find_bind_import(image) : nullptr;
        if (!system_bind || !exchange(reinterpret_cast<PVOID>(system_bind), reinterpret_cast<PVOID>(probe_bind)))
            return 0;
        mapped = bound = 0;
        requested.store(static_cast<UINT16>(port));
        lua.set_bool(true);
        return 1;
    }

    int observation(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 0) return 0;
        lua.set_integer(mapped);
        lua.set_integer(bound);
        lua.set_bool(!requested.load() && import_slot && *import_slot == reinterpret_cast<PVOID>(system_bind));
        return 3;
    }

    class ListenProof final : public RC::CppUserModBase
    {
    public:
        ListenProof()
        {
            ModName = mod_name;
            ModVersion = STR("0.1");
            ModIntendedSDKVersion = STR("4.0.0");
        }

        void on_unreal_init() override
        {
            if (!is_opted_in()) return;
            pre = H::RegisterLoadMapPreCallback(
                [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext&, U::FURL url,
                   U::UPendingNetGame* pending, U::FString&) {
                    if (!requested.load() || !U::IsInGameThreadRaw() || pending || url.Map.IsEmpty()
                        || std::wstring_view{*url.Map} != package || !url.Host.IsEmpty()) return;
                    for (const auto& option : url.Op)
                    {
                        if (std::wstring_view{*option} == L"listen")
                        {
                            loading_listen_map = true;
                            ++mapped;
                            break;
                        }
                    }
                }, {false, false, mod_name, STR("Scope the test listener to its stock load")});
            post = H::RegisterLoadMapPostCallback(
                [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext&, U::FURL,
                   U::UPendingNetGame*, U::FString&) {
                    if (!loading_listen_map) return;
                    loading_listen_map = false;
                    release_import();
                },
                {false, false, mod_name, STR("Clear the test listener scope after stock load")});
        }

        void on_lua_start(RC::StringViewType name, Lua& lua, Lua&, Lua&, Lua*) override
        {
            if (name != mod_name || !is_opted_in()) return;
            lua.register_function("SilverfishArmTitleListenProbe", arm);
            lua.register_function("SilverfishReadTitleListenProbe", observation);
        }

        ~ListenProof() override
        {
            if (pre != H::ERROR_ID) H::UnregisterCallback(pre);
            if (post != H::ERROR_ID) H::UnregisterCallback(post);
            release_import();
        }
    };
}

extern "C" __declspec(dllexport) RC::CppUserModBase* start_mod()
{
    return new ListenProof();
}

extern "C" __declspec(dllexport) void uninstall_mod(RC::CppUserModBase* mod)
{
    delete mod;
}
