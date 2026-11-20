#include <windows.h>
#include <bcrypt.h>

#include <array>
#include <cstddef>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include <DynamicOutput/DynamicOutput.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

#ifndef SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256
#define SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256 ""
#endif

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    constexpr auto mod_name = STR("SilverfishCoopBootstrap");
    constexpr auto world_name = STR("World /Game/Classes/Maps/MainMenu_P.MainMenu_P");
    constexpr wchar_t coordinator_name[] = L"SilverfishCoopCoordinator.exe";
    constexpr std::string_view coordinator_hash = SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256;
    constexpr int path_capacity = 32768;
    int module_anchor;

    class Handle
    {
        HANDLE value = nullptr;

    public:
        Handle() = default;
        explicit Handle(HANDLE handle) : value(handle) {}
        Handle(const Handle&) = delete;
        Handle& operator=(const Handle&) = delete;
        Handle(Handle&& other) noexcept : value(std::exchange(other.value, nullptr)) {}
        Handle& operator=(Handle&& other) noexcept
        {
            if (this != &other)
            {
                reset();
                value = std::exchange(other.value, nullptr);
            }
            return *this;
        }
        ~Handle() { reset(); }

        explicit operator bool() const { return value && value != INVALID_HANDLE_VALUE; }
        HANDLE get() const { return value; }
        void reset()
        {
            if (*this) CloseHandle(value);
            value = nullptr;
        }
    };

    enum class Phase { unavailable, checking, offered, queued, started, failed };

    struct Package
    {
        std::wstring path;
        std::wstring directory;
        Handle directory_lock;
        Handle executable_lock;
    };

    std::mutex state_mutex;
    Phase phase = Phase::unavailable;
    Package package;
    std::string generation;
    std::string request;
    bool preflight_requested = false;

    bool live(U::UObject* object)
    {
        constexpr auto dead = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(dead) && !object->IsUnreachable();
    }

    U::FObjectProperty* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = U::CastField<U::FObjectProperty>(object->GetPropertyByNameInChain(name));
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, U::UObject* object)
    {
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool standalone_title()
    {
        if (!U::IsInGameThreadRaw() ||
            GetEnvironmentVariableW(L"SILVERFISH_COOP_BRIDGE", nullptr, 0) != 0) return false;
        auto* viewport = U::UObjectGlobals::FindFirstOf(STR("GameViewportClient"));
        if (!live(viewport) || !viewport->GetClassPrivate() ||
            viewport->GetClassPrivate()->GetFullName() != STR("Class /Script/Engine.GameViewportClient")) return false;
        auto* world = read(object_field(viewport, STR("World")), viewport);
        if (!live(world) || world->GetFullName() != world_name) return false;
        auto* driver = object_field(world, STR("NetDriver"));
        return driver && !read(driver, world);
    }

    bool foreground_selection()
    {
        DWORD foreground_pid = 0;
        const auto foreground = GetForegroundWindow();
        return foreground && GetWindowThreadProcessId(foreground, &foreground_pid) != 0 &&
               foreground_pid == GetCurrentProcessId() && (GetAsyncKeyState(VK_F7) & 0x8000) != 0;
    }

    bool exact_id(std::string_view value)
    {
        if (value.size() != 32) return false;
        bool nonzero = false;
        for (const char byte : value)
        {
            if ((byte < '0' || byte > '9') && (byte < 'a' || byte > 'f')) return false;
            nonzero |= byte != '0';
        }
        return nonzero;
    }

    template <size_t Size>
    std::string hex(const std::array<UCHAR, Size>& bytes)
    {
        constexpr std::string_view digits = "0123456789abcdef";
        std::string text(Size * 2, '0');
        for (size_t index = 0; index < Size; ++index)
        {
            text[index * 2] = digits[bytes[index] >> 4];
            text[index * 2 + 1] = digits[bytes[index] & 15];
        }
        return text;
    }

    std::wstring dos_path(std::wstring value)
    {
        if (value.starts_with(L"\\\\?\\")) value.erase(0, 4);
        if (value.size() < 4 || value[1] != L':' || value[2] != L'\\' || value.find(L'"') != value.npos) return {};
        return value;
    }

    std::wstring final_path(HANDLE handle)
    {
        std::wstring result(path_capacity, L'\0');
        const DWORD length = GetFinalPathNameByHandleW(handle, result.data(), path_capacity,
                                                     FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
        if (length == 0 || length >= path_capacity) return {};
        result.resize(length);
        return dos_path(std::move(result));
    }

    bool same_path(const std::wstring& first, const std::wstring& second)
    {
        return !first.empty() && !second.empty() &&
               CompareStringOrdinal(first.c_str(), -1, second.c_str(), -1, TRUE) == CSTR_EQUAL;
    }

    bool plain_disk_file(const Handle& file, bool directory)
    {
        BY_HANDLE_FILE_INFORMATION information{};
        return file && GetFileType(file.get()) == FILE_TYPE_DISK &&
               GetFileInformationByHandle(file.get(), &information) &&
               !(information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
               ((information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) == directory;
    }

    bool matches_hash(HANDLE file)
    {
        BCRYPT_ALG_HANDLE algorithm = nullptr;
        if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return false;
        BCRYPT_HASH_HANDLE hash = nullptr;
        bool success = BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
        std::array<UCHAR, 65536> buffer{};
        while (success)
        {
            DWORD count = 0;
            success = ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &count, nullptr) != FALSE;
            if (!success || !count) break;
            success = BCryptHashData(hash, buffer.data(), count, 0) >= 0;
        }
        std::array<UCHAR, 32> digest{};
        success = success && BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0;
        if (hash) BCryptDestroyHash(hash);
        BCryptCloseAlgorithmProvider(algorithm, 0);
        return success && hex(digest) == coordinator_hash;
    }

    Package find_package(HMODULE module)
    {
        if (coordinator_hash.size() != 64) return {};
        std::wstring image(path_capacity, L'\0');
        const DWORD length = GetModuleFileNameW(module, image.data(), path_capacity);
        if (length == 0 || length >= path_capacity) return {};
        image.resize(length);
        const Handle loaded_module(CreateFileW(image.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ,
            nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!plain_disk_file(loaded_module, false)) return {};
        const auto native_path = final_path(loaded_module.get());
        if (native_path.empty()) return {};
        Package result;
        result.directory = native_path.substr(0, native_path.find_last_of(L'\\'));
        result.directory_lock = Handle(CreateFileW(result.directory.c_str(), FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!plain_disk_file(result.directory_lock, true) ||
            !same_path(result.directory, final_path(result.directory_lock.get()))) return {};
        result.path = result.directory + L"\\" + coordinator_name;
        result.executable_lock = Handle(CreateFileW(result.path.c_str(), GENERIC_READ, FILE_SHARE_READ,
            nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!plain_disk_file(result.executable_lock, false) ||
            !same_path(result.path, final_path(result.executable_lock.get())) ||
            !matches_hash(result.executable_lock.get())) return {};
        return result;
    }

    void check_package(HMODULE module) noexcept
    {
        try
        {
            auto found = find_package(module);
            std::array<UCHAR, 16> random{};
            if (found.executable_lock && BCryptGenRandom(nullptr, random.data(), static_cast<ULONG>(random.size()),
                                                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) >= 0)
            {
                auto next = hex(random);
                if (exact_id(next))
                {
                    std::lock_guard lock(state_mutex);
                    package = std::move(found);
                    generation = std::move(next);
                    phase = Phase::offered;
                    return;
                }
            }
        }
        catch (...) {}
        std::lock_guard lock(state_mutex);
        phase = Phase::unavailable;
    }

    class ChildAttributes
    {
        std::vector<std::byte> storage;
        LPPROC_THREAD_ATTRIBUTE_LIST attributes = nullptr;

    public:
        ~ChildAttributes()
        {
            if (attributes) DeleteProcThreadAttributeList(attributes);
        }

        LPPROC_THREAD_ATTRIBUTE_LIST restrict_handles(std::array<HANDLE, 2>& handles)
        {
            SIZE_T bytes = 0;
            InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
            if (!bytes || GetLastError() != ERROR_INSUFFICIENT_BUFFER) return nullptr;
            storage.resize(bytes);
            auto* candidate = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
            if (!InitializeProcThreadAttributeList(candidate, 1, 0, &bytes)) return nullptr;
            attributes = candidate;
            return UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles.data(),
                                             handles.size() * sizeof(HANDLE), nullptr, nullptr) ? attributes : nullptr;
        }
    };

    const wchar_t* start_coordinator(const Package& current, const std::string& receipt)
    {
        BOOL parent_in_job = FALSE;
        if (!IsProcessInJob(GetCurrentProcess(), nullptr, &parent_in_job)) return L"parent_job_unavailable";
        HANDLE read_raw = nullptr, write_raw = nullptr;
        if (!CreatePipe(&read_raw, &write_raw, nullptr, 4096)) return L"receipt_pipe_unavailable";
        const Handle reader(read_raw);
        Handle writer(write_raw);
        if (!SetHandleInformation(reader.get(), HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT))
            return L"receipt_pipe_unavailable";
        SECURITY_ATTRIBUTES inheritable{sizeof(inheritable), nullptr, TRUE};
        const Handle quiet(CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                       &inheritable, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
        if (!quiet) return L"quiet_stdio_unavailable";
        STARTUPINFOEXW startup{};
        startup.StartupInfo.cb = sizeof(startup);
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
        startup.StartupInfo.wShowWindow = SW_HIDE;
        startup.StartupInfo.hStdInput = reader.get();
        startup.StartupInfo.hStdOutput = quiet.get();
        startup.StartupInfo.hStdError = quiet.get();
        ChildAttributes attributes;
        std::array<HANDLE, 2> handles{reader.get(), quiet.get()};
        startup.lpAttributeList = attributes.restrict_handles(handles);
        if (!startup.lpAttributeList) return L"handle_list_unavailable";

        // Only the packaged executable enters argv. The caller's IDs stay on stdin.
        std::wstring command = L"\"" + current.path + L"\"";
        std::wstring child_image(path_capacity, L'\0');
        DWORD flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT;
        if (parent_in_job) flags |= CREATE_BREAKAWAY_FROM_JOB;
        PROCESS_INFORMATION created{};
        if (!CreateProcessW(current.path.c_str(), command.data(), nullptr, nullptr, TRUE, flags, nullptr,
                            current.directory.c_str(), &startup.StartupInfo, &created))
            return parent_in_job ? L"job_breakaway_rejected" : L"process_create_failed";
        const Handle process(created.hProcess), thread(created.hThread);
        const auto reject = [&](const wchar_t* reason) {
            TerminateProcess(process.get(), 1);
            return reason;
        };
        BOOL child_in_job = FALSE;
        if (!IsProcessInJob(process.get(), nullptr, &child_in_job) || child_in_job) return reject(L"child_job_not_detached");
        DWORD size = path_capacity;
        if (!QueryFullProcessImageNameW(process.get(), 0, child_image.data(), &size))
            return reject(L"child_image_unavailable");
        child_image.resize(size);
        if (!same_path(current.path, dos_path(std::move(child_image)))) return reject(L"child_image_changed");
        DWORD written = 0;
        const auto expected = static_cast<DWORD>(receipt.size());
        if (!WriteFile(writer.get(), receipt.data(), expected, &written, nullptr) || written != expected)
            return reject(L"receipt_write_failed");
        writer.reset();
        if (ResumeThread(thread.get()) != 1) return reject(L"resume_failed");
        return nullptr;
    }

    void launch(Package current, const std::string& receipt) noexcept
    {
        const wchar_t* failure = L"unexpected_worker_error";
        try { failure = start_coordinator(current, receipt); }
        catch (...) {}
        {
            std::lock_guard lock(state_mutex);
            phase = failure ? Phase::failed : Phase::started;
        }
        if (failure) RC::Output::send(STR("[SilverfishCoopBootstrap] native_launch_failed={}\n"), failure);
        else RC::Output::send(STR("[SilverfishCoopBootstrap] native_launch_started\n"));
    }

    int read_offer(const Lua& lua)
    {
        if (lua.get_stack_size() != 0 || !standalone_title()) return 0;
        std::lock_guard lock(state_mutex);
        if (!preflight_requested)
        {
            preflight_requested = true;
            HMODULE module = nullptr;
            if (coordinator_hash.size() == 64 && GetModuleHandleExW(
                GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                reinterpret_cast<LPCWSTR>(&module_anchor), &module))
            {
                phase = Phase::checking;
                try { std::thread(check_package, module).detach(); }
                catch (...) { phase = Phase::unavailable; }
            }
        }
        if (phase != Phase::offered) return 0;
        lua.set_integer(1);
        lua.set_string(generation);
        return 2;
    }

    bool queue_open(const Lua& lua)
    {
        if (lua.get_stack_size() != 2 || !lua.is_string(1) || !lua.is_string(2) ||
            !standalone_title() || !foreground_selection()) return false;
        size_t offered_length = 0, requested_length = 0;
        const auto* offered_string = lua_tolstring(lua.get_lua_state(), 1, &offered_length);
        const auto* requested_string = lua_tolstring(lua.get_lua_state(), 2, &requested_length);
        if (!offered_string || !requested_string || offered_length != 32 || requested_length != 32) return false;
        const std::string_view offered(offered_string, offered_length), requested(requested_string, requested_length);
        if (!exact_id(offered) || !exact_id(requested)) return false;
        std::lock_guard lock(state_mutex);
        if (phase != Phase::offered || offered != generation) return false;
        phase = Phase::queued;
        try
        {
            request = requested;
            const auto receipt = "1\topen-coop\t" + generation + "\t" + request + "\n";
            std::thread(launch, std::move(package), receipt).detach();
            return true;
        }
        catch (...)
        {
            phase = Phase::failed;
            return false;
        }
    }

    int request_open(const Lua& lua)
    {
        lua.set_bool(queue_open(lua));
        return 1;
    }

    std::string_view phase_name()
    {
        switch (phase)
        {
        case Phase::checking: return "checking";
        case Phase::offered: return "offered";
        case Phase::queued: return "queued";
        case Phase::started: return "started";
        case Phase::failed: return "failed";
        default: return "unavailable";
        }
    }

    int read_status(const Lua& lua)
    {
        if (lua.get_stack_size() != 0 || !U::IsInGameThreadRaw()) return 0;
        std::lock_guard lock(state_mutex);
        lua.set_integer(1);
        lua.set_string(phase_name());
        if (generation.empty()) lua.set_nil(); else lua.set_string(generation);
        if (request.empty()) lua.set_nil(); else lua.set_string(request);
        return 4;
    }

    class Bootstrap final : public RC::CppUserModBase
    {
    public:
        Bootstrap()
        {
            ModName = mod_name;
            ModVersion = STR("0.1");
            ModIntendedSDKVersion = STR("4.0.0");
        }

        void on_lua_start(RC::StringViewType name, Lua& lua, Lua&, Lua&, Lua*) override
        {
            if (name != mod_name || GetEnvironmentVariableW(L"SILVERFISH_COOP_BRIDGE", nullptr, 0) != 0) return;
            lua.register_function("SilverfishReadCoopBootstrapOffer", read_offer);
            lua.register_function("SilverfishRequestCoopBootstrapOpen", request_open);
            lua.register_function("SilverfishReadCoopBootstrapLaunchStatus", read_status);
        }
    };
}

extern "C" __declspec(dllexport) RC::CppUserModBase* start_mod()
{
    return new Bootstrap();
}

extern "C" __declspec(dllexport) void uninstall_mod(RC::CppUserModBase* mod)
{
    delete mod;
}
