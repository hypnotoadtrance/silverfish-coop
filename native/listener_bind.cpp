#include "listener_bind.hpp"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <Windows.h>

#include <bit>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/FWorldContext.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Bind = decltype(&::bind);
    using GetOption = decltype(&::getsockopt);
    using SetError = decltype(&::WSASetLastError);
    constexpr auto mod_name = STR("SilverfishCoopNative");

    struct Request
    {
        U::FWeakObjectPtr source;
        std::wstring target;
        std::uint16_t port{};
        ULONGLONG expires{};
    } pending;

    struct SocketScope
    {
        const U::FWorldContext* context{};
        std::uint16_t stock_port{}, private_port{};
        bool opened{};
    };
    thread_local SocketScope load;
    H::GlobalCallbackId pre = H::ERROR_ID, post = H::ERROR_ID;
    H::GlobalCallbackId construct = H::ERROR_ID, tick_end = H::ERROR_ID;
    Bind system_bind{};
    GetOption socket_option{};
    SetError socket_error{};
    PVOID volatile* import_slot{};
    struct ClientRequest
    {
        U::FWeakObjectPtr world;
        U::FWeakObjectPtr pending_game;
        const U::UClass* pending_class{};
        const U::UClass* driver_class{};
        ULONGLONG expires{};
    } client;
    thread_local bool client_binding{};
    thread_local bool client_opened{};

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
        const auto* imports_table = reinterpret_cast<const IMAGE_IMPORT_DESCRIPTOR*>(data + imports.VirtualAddress);
        for (DWORD index = 0; index < imports.Size / sizeof(*imports_table) && imports_table[index].Name; ++index)
        {
            const auto& item = imports_table[index];
            if (!inside(item.Name, sizeof("WS2_32.dll")) ||
                _strnicmp(reinterpret_cast<const char*>(data + item.Name), "WS2_32.dll", sizeof("WS2_32.dll")) != 0)
                continue;
            for (DWORD offset = 0; offset < 2048; offset += sizeof(IMAGE_THUNK_DATA64))
            {
                const auto name_rva = item.OriginalFirstThunk + offset;
                const auto slot_rva = item.FirstThunk + offset;
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

    int WSAAPI route_bind(SOCKET socket, const sockaddr* address, int length)
    {
        if ((!load.context && !client_binding) || !address) return system_bind(socket, address, length);
        int type{}, bytes = sizeof(type);
        if (socket_option(socket, SOL_SOCKET, SO_TYPE, reinterpret_cast<char*>(&type), &bytes) != 0)
            return SOCKET_ERROR;
        if (type != SOCK_DGRAM) return system_bind(socket, address, length);
        const auto* incoming = length == sizeof(sockaddr_in) && address->sa_family == AF_INET
            ? reinterpret_cast<const sockaddr_in*>(address) : nullptr;
        constexpr auto loopback = std::byteswap(static_cast<std::uint32_t>(INADDR_LOOPBACK));
        if (client_binding)
        {
            if (!incoming || incoming->sin_port != 0 || client_opened)
            {
                socket_error(WSAEACCES);
                return SOCKET_ERROR;
            }
            auto local = *incoming;
            local.sin_addr.s_addr = loopback;
            const auto result = system_bind(socket, reinterpret_cast<sockaddr*>(&local), sizeof(local));
            client_opened = result == 0;
            return result;
        }
        if (incoming && incoming->sin_port != std::byteswap(load.stock_port) && incoming->sin_addr.s_addr == loopback)
            return system_bind(socket, address, length);
        if (!incoming || incoming->sin_port != std::byteswap(load.stock_port) || load.opened)
        {
            socket_error(WSAEACCES);
            return SOCKET_ERROR;
        }
        sockaddr_in local = *incoming;
        local.sin_addr.s_addr = loopback;
        local.sin_port = std::byteswap(load.private_port);
        const auto result = system_bind(socket, reinterpret_cast<sockaddr*>(&local), sizeof(local));
        load.opened = result == 0;
        return result;
    }

    bool exchange(PVOID expected, PVOID next)
    {
        DWORD protection{};
        auto* location = const_cast<PVOID*>(import_slot);
        if (!location || !VirtualProtect(location, sizeof(*location), PAGE_READWRITE, &protection)) return false;
        const auto previous = InterlockedCompareExchangePointer(import_slot, next, expected);
        DWORD ignored{};
        VirtualProtect(location, sizeof(*location), protection, &ignored);
        return previous == expected;
    }

    void restore()
    {
        exchange(reinterpret_cast<PVOID>(route_bind), reinterpret_cast<PVOID>(system_bind));
        load = {};
        client_binding = client_opened = false;
    }

    void before_load(H::TCallbackIterationData<bool>& result, U::UEngine*, U::FWorldContext& context,
                     U::FURL url, U::UPendingNetGame* connection, U::FString&)
    {
        if (!pending.port || !U::IsInGameThreadRaw()) return;
        bool listen = false;
        for (const auto& option : url.Op) listen |= std::wstring_view{*option} == L"listen";
        if (!listen) return;
        const auto* source = pending.source.Get();
        const bool from_source = source && source == context.GetThisCurrentWorld().Get();
        const bool to_target = !url.Map.IsEmpty() && std::wstring_view{*url.Map} == pending.target;
        if (!from_source && !to_target) return;
        const auto session = std::move(pending);
        pending = {};
        if (result.OriginalFunctionCallPrevented()) return;
        if (!from_source || !to_target || connection || !url.Host.IsEmpty()
            || url.Port <= 0 || url.Port > 65535 || GetTickCount64() > session.expires
            || !exchange(reinterpret_cast<PVOID>(system_bind), reinterpret_cast<PVOID>(route_bind)))
        {
            result.PreventOriginalFunctionCall();
            (void)result.TrySetReturnValue(false);
            return;
        }
        load = {&context, static_cast<std::uint16_t>(url.Port), session.port};
    }

    std::wstring package(std::string_view path, bool world_object)
    {
        if (path.size() > 180 || !path.starts_with("/Game/Classes/Maps/") ||
            path.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_/.") != std::string_view::npos ||
            path.find("..") != std::string_view::npos || (!world_object && path.find('.') != std::string_view::npos))
            return {};
        return {path.begin(), path.end()};
    }

    int arm(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2)
            || !lua.is_string(3) || !lua.is_string(4) || pre == H::ERROR_ID || post == H::ERROR_ID
            || !import_slot || *import_slot != reinterpret_cast<PVOID>(system_bind) || load.context) return 0;
        // The bridge getters remove the value they read from the Lua stack.
        auto destination = package(lua.get_string(4), false);
        const auto source_name = package(lua.get_string(3), true);
        const auto address = static_cast<std::uintptr_t>(lua.get_integer(2));
        const auto port = lua.get_integer(1);
        if (port < 1 || port > 65535 || source_name.empty() || destination.empty()) return 0;
        auto* world = U::UObjectGlobals::StaticFindObject<U::UWorld*>(nullptr, nullptr, source_name.c_str());
        if (!world || reinterpret_cast<std::uintptr_t>(world) != address || world->GetPathName() != source_name) return 0;
        pending = {};
        pending.source = world;
        pending.target = std::move(destination);
        pending.port = static_cast<std::uint16_t>(port);
        pending.expires = GetTickCount64() + 5000;
        lua.set_bool(true);
        return 1;
    }

    void on_construct(H::TCallbackIterationData<U::UObject*>& result, const U::FStaticConstructObjectParameters& params)
    {
        if (!U::IsInGameThreadRaw() || !client.expires || GetTickCount64() > client.expires
            || !client.world.Get() || result.OriginalFunctionCallPrevented() ||
            (params.SetFlags & (U::RF_ClassDefaultObject | U::RF_ArchetypeObject))) return;
        if (params.Class == client.pending_class)
        {
            client.pending_game = result.GetCurrentResolvedReturnValue();
            return;
        }
        if (params.Class != client.driver_class || !client.pending_game.Get() || !result.GetCurrentResolvedReturnValue())
            return;
        client = {};
        if (!load.context && !client_binding)
            client_binding = exchange(reinterpret_cast<PVOID>(system_bind), reinterpret_cast<PVOID>(route_bind));
    }

    int arm_client(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2)
            || construct == H::ERROR_ID || tick_end == H::ERROR_ID || !import_slot
            || *import_slot != reinterpret_cast<PVOID>(system_bind)) return 0;
        const auto source_name = package(lua.get_string(2), true);
        const auto address = static_cast<std::uintptr_t>(lua.get_integer(1));
        if (source_name.empty()) return 0;
        auto* world = U::UObjectGlobals::StaticFindObject<U::UWorld*>(nullptr, nullptr, source_name.c_str());
        if (!world || reinterpret_cast<std::uintptr_t>(world) != address || world->GetPathName() != source_name) return 0;
        auto* pending_class = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, STR("/Script/Engine.PendingNetGame"));
        auto* driver_class = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, STR("/Script/OnlineSubsystemUtils.IpNetDriver"));
        if (!pending_class || !driver_class) return 0;
        client = {};
        client.world = world;
        client.pending_class = pending_class;
        client.driver_class = driver_class;
        client.expires = GetTickCount64() + 5000;
        lua.set_bool(true);
        return 1;
    }
}

void silverfish::native_listener_bind::install_hooks()
{
    const auto game = GetModuleHandleW(nullptr);
    const auto winsock = GetModuleHandleW(L"WS2_32.dll");
    if (!game || !winsock) return;
    system_bind = std::bit_cast<Bind>(GetProcAddress(winsock, "bind"));
    socket_option = std::bit_cast<GetOption>(GetProcAddress(winsock, "getsockopt"));
    socket_error = std::bit_cast<SetError>(GetProcAddress(winsock, "WSASetLastError"));
    import_slot = find_bind_import(game);
    if (!system_bind || !socket_option || !socket_error || !import_slot) return;
    pre = H::RegisterLoadMapPreCallback(before_load, {false, false, mod_name, STR("Open a private co-op UDP listener")});
    post = H::RegisterLoadMapPostCallback(
        [](H::TCallbackIterationData<bool>&, U::UEngine*, U::FWorldContext& context, U::FURL,
           U::UPendingNetGame*, U::FString&) {
            if (load.context == &context) restore();
        }, {false, false, mod_name, STR("Restore Winsock after opening the co-op listener")});
    construct = H::RegisterStaticConstructObjectPostCallback(on_construct,
        {false, false, mod_name, STR("Bind the armed co-op guest net driver to loopback")});
    tick_end = H::RegisterEngineTickPostCallback(
        [](H::TCallbackIterationData<void>&, U::UEngine*, float, bool) {
            if (client_binding) restore();
            if (client.pending_game.Get() || (client.expires && GetTickCount64() > client.expires)) client = {};
        }, {false, false, mod_name, STR("Restore Winsock after opening the co-op guest socket")});
}

void silverfish::native_listener_bind::uninstall_hooks()
{
    if (pre != H::ERROR_ID) H::UnregisterCallback(pre);
    if (post != H::ERROR_ID) H::UnregisterCallback(post);
    if (construct != H::ERROR_ID) H::UnregisterCallback(construct);
    if (tick_end != H::ERROR_ID) H::UnregisterCallback(tick_end);
    pending = {};
    client = {};
    if (load.context || client_binding) restore();
}

void silverfish::native_listener_bind::register_lua(Lua& lua)
{
    lua.register_function("SilverfishArmNativeListenPort", arm);
    lua.register_function("SilverfishArmNativeGuestSocket", arm_client);
}
