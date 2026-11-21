#include <Windows.h>
#include <bcrypt.h>
#include <shellapi.h>
#include <Python.h>

#include <array>
#include <atomic>
#include <bit>
#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include <DynamicOutput/DynamicOutput.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <glaze/glaze.hpp>

#ifndef SILVERFISH_RUNTIME_MANIFEST_SHA256
#define SILVERFISH_RUNTIME_MANIFEST_SHA256 ""
#endif

static_assert(PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 14 && sizeof(void*) == 8);

namespace
{
    namespace fs = std::filesystem;
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    constexpr auto mod_name = STR("SilverfishCoopEmbeddedRuntime");
    constexpr wchar_t startup_env[] = L"SILVERFISH_COOP_EMBEDDED_STARTUP";
    constexpr wchar_t bridge_env[] = L"SILVERFISH_COOP_BRIDGE";
    constexpr wchar_t embedded_env[] = L"SILVERFISH_COOP_EMBEDDED_ENGINE";
    constexpr auto python_dll = L"python314.dll";
    constexpr std::string_view manifest_digest = SILVERFISH_RUNTIME_MANIFEST_SHA256;
    constexpr std::size_t max_path = 32768;
    constexpr std::size_t max_manifest = 512 * 1024;
    constexpr std::size_t max_runtime = 60 * 1024 * 1024;
    constexpr std::size_t max_entries = 4096;
    constexpr std::string_view entry_file = "app/tools/silverfish/embedded_startup.py";
    constexpr std::array module_paths{"python314.zip", ".", "site-packages", "app"};
    int module_anchor{};
    std::atomic<bool> started{false};

    class Handle
    {
        HANDLE handle = nullptr;

    public:
        explicit Handle(HANDLE value) : handle(value) {}
        Handle(const Handle&) = delete;
        Handle& operator=(const Handle&) = delete;
        Handle(Handle&& other) noexcept : handle(std::exchange(other.handle, nullptr)) {}
        ~Handle() { if (*this) CloseHandle(handle); }
        explicit operator bool() const { return handle && handle != INVALID_HANDLE_VALUE; }
        HANDLE get() const { return handle; }
    };

    template<class Text>
    bool lower_hex(Text value, std::size_t length)
    {
        if (value.size() != length) return false;
        for (const auto byte : value)
            if ((byte < '0' || byte > '9') && (byte < 'a' || byte > 'f')) return false;
        return true;
    }

    std::wstring environment(const wchar_t* name)
    {
        std::wstring value(max_path, L'\0');
        const auto count = GetEnvironmentVariableW(name, value.data(), static_cast<DWORD>(value.size()));
        if (!count || count >= value.size()) return {};
        value.resize(count);
        return value;
    }

    std::wstring startup_nonce()
    {
        wchar_t value[33]{};
        if (GetEnvironmentVariableW(startup_env, value, 33) != 32) return {};
        const std::wstring_view id(value, 32);
        return lower_hex(id, 32) && id.find_first_not_of(L'0') != id.npos ? std::wstring(id) : std::wstring{};
    }

    bool windows_equal(std::wstring_view left, std::wstring_view right)
    {
        return !left.empty() && !right.empty() &&
            CompareStringOrdinal(left.data(), static_cast<int>(left.size()), right.data(),
                static_cast<int>(right.size()), TRUE) == CSTR_EQUAL;
    }

    bool windows_path_equal(fs::path left, fs::path right)
    {
        return windows_equal(left.make_preferred().native(), right.make_preferred().native());
    }

    fs::path module_image(HMODULE module)
    {
        std::wstring image(max_path, L'\0');
        const auto count = GetModuleFileNameW(module, image.data(), static_cast<DWORD>(image.size()));
        if (!count || count >= image.size()) return {};
        image.resize(count);
        return fs::path(image);
    }

    bool exact_path(const fs::path& path, bool directory)
    {
        if (!path.is_absolute() || path.lexically_normal() != path) return false;
        const auto attributes = GetFileAttributesW(path.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || attributes & FILE_ATTRIBUTE_REPARSE_POINT ||
            bool(attributes & FILE_ATTRIBUTE_DIRECTORY) != directory) return false;
        return windows_path_equal(path, fs::canonical(path));
    }

    bool profile_name(std::wstring_view name)
    {
        auto alphanumeric = [](wchar_t ch) {
            return (ch >= L'a' && ch <= L'z') || (ch >= L'A' && ch <= L'Z') || (ch >= L'0' && ch <= L'9');
        };
        if (name.empty() || name.size() > 64 || !alphanumeric(name.front())) return false;
        for (auto ch : name) if (!alphanumeric(ch) && ch != L'-' && ch != L'_') return false;
        return true;
    }

    struct CommandArguments
    {
        int count{};
        wchar_t** values = CommandLineToArgvW(GetCommandLineW(), &count);
        ~CommandArguments() { if (values) LocalFree(values); }
    };

    bool matches_command_line(const fs::path& image, const fs::path& profile, bool legacy_loopback)
    {
        const CommandArguments arguments;
        if (!arguments.values || arguments.count < 2 ||
            !windows_path_equal(arguments.values[0], image)) return false;
        bool userdir = false, loopback = false;
        for (int index = 1; index < arguments.count; ++index)
        {
            const std::wstring_view argument(arguments.values[index]);
            const auto separator = argument.find(L'=');
            const auto name = argument.substr(0, separator);
            const auto value = separator == argument.npos ? std::wstring_view{} : argument.substr(separator + 1);
            if (windows_equal(name, L"-userdir"))
            {
                const fs::path supplied(value);
                if (userdir || !windows_path_equal(supplied, profile) || !exact_path(supplied, true)) return false;
                userdir = true;
            }
            if (windows_equal(name, L"-multihome"))
            {
                if (loopback || value != L"127.0.0.1") return false;
                loopback = true;
            }
            if (windows_equal(name, L"-port")) return false;
        }
        return userdir && (loopback == legacy_loopback);
    }

    fs::path qualify_startup(HMODULE module, std::wstring_view nonce, const wchar_t*& state)
    {
        state = L"startup_scope_environment_refused";
        if (startup_nonce() != nonce || environment(embedded_env) != L"1") return {};
        const auto image = module_image(nullptr);
        const auto addon = module_image(module);
        state = L"startup_scope_image_refused";
        if (!exact_path(image, false) || !exact_path(addon, false)) return {};
        const auto binaries = image.parent_path();
        const auto stage = binaries.parent_path().parent_path().parent_path();
        const auto work = stage.parent_path();
        const auto mod = binaries / L"ue4ss" / L"Mods" / mod_name;
        state = L"startup_scope_stage_refused";
        if (stage.filename() != L"stage" ||
            !windows_path_equal(image, stage / L"SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe") ||
            !windows_path_equal(addon, mod / L"dlls/main.dll") ||
            !exact_path(stage / L"silverfish-coop-stage.json", false)) return {};

        const fs::path bridge(environment(bridge_env));
        const auto bridge_folder = bridge.parent_path();
        const auto run = bridge_folder.parent_path();
        const auto run_profile = run.parent_path();
        const auto role_root = run_profile.parent_path();
        const auto profile = run_profile / L"game-user";
        const bool guest = windows_path_equal(role_root, work / L"clients");
        state = L"startup_scope_bridge_refused";
        if (bridge.filename() != nonce || bridge_folder.filename() != L"game-bridge" || run.filename() != L"run" ||
            !profile_name(run_profile.filename().native()) ||
            (!guest && !windows_path_equal(role_root, work / L"campaigns"))) return {};
        for (const auto& path : {role_root, run_profile, run, bridge_folder, bridge})
            if (!exact_path(path, true)) return {};

        state = L"startup_scope_arguments_refused";
        const bool resident = environment(L"SILVERFISH_COOP_RESIDENT_TITLE") == L"1";
        if (!matches_command_line(image, profile, guest && !resident)) return {};
        return mod / L"runtime";
    }

    struct Manifest
    {
        int schema{};
        std::string platform;
        std::string python_version;
        std::string python_library;
        std::vector<std::string> module_paths;
        std::uint64_t payload_bytes{};
        std::map<std::string, std::string> files;

        struct glaze
        {
            static constexpr auto value = glz::object(
                "schema", &Manifest::schema, "platform", &Manifest::platform,
                "python_version", &Manifest::python_version, "python_library", &Manifest::python_library,
                "module_paths", &Manifest::module_paths, "payload_bytes", &Manifest::payload_bytes,
                "files", &Manifest::files);
        };
    };

    std::optional<std::string> read(HANDLE file, std::size_t limit)
    {
        LARGE_INTEGER size{};
        if (!GetFileSizeEx(file, &size) || size.QuadPart < 0 || static_cast<std::uint64_t>(size.QuadPart) > limit)
            return std::nullopt;
        std::string data(static_cast<std::size_t>(size.QuadPart), '\0');
        DWORD count = 0;
        if (!ReadFile(file, data.data(), static_cast<DWORD>(data.size()), &count, nullptr) || count != data.size())
            return std::nullopt;
        return data;
    }

    Handle open_pinned(const fs::path& file)
    {
        Handle handle(CreateFileW(file.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
            OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr));
        BY_HANDLE_FILE_INFORMATION information{};
        if (!handle || GetFileType(handle.get()) != FILE_TYPE_DISK ||
            !GetFileInformationByHandle(handle.get(), &information) ||
            information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))
            return Handle(nullptr);
        return handle;
    }

    class Sha256
    {
        BCRYPT_ALG_HANDLE algorithm = nullptr;

    public:
        Sha256() { BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0); }
        ~Sha256() { if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0); }

        bool matches(std::string_view data, std::string_view expected) const
        {
            if (!algorithm || !lower_hex(expected, 64)) return false;
            std::array<UCHAR, 32> hash{};
            if (BCryptHash(algorithm, nullptr, 0,
                reinterpret_cast<PUCHAR>(const_cast<char*>(data.data())), static_cast<ULONG>(data.size()),
                hash.data(), static_cast<ULONG>(hash.size())) < 0) return false;
            constexpr std::string_view digits = "0123456789abcdef";
            for (std::size_t index = 0; index < hash.size(); ++index)
                if (expected[2 * index] != digits[hash[index] >> 4] ||
                    expected[2 * index + 1] != digits[hash[index] & 15]) return false;
            return true;
        }
    };

    bool portable_file(std::string_view path)
    {
        while (!path.empty())
        {
            const auto separator = path.find('/');
            const auto part = path.substr(0, separator);
            if (part.empty() || part.back() == '.') return false;
            for (char ch : part)
                if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')
                    || ch == '.' || ch == '-' || ch == '_')) return false;
            if (separator == path.npos) return true;
            path.remove_prefix(separator + 1);
        }
        return false;
    }

    struct RuntimePackage
    {
        fs::path root;
        std::vector<Handle> files;
    };

    std::optional<RuntimePackage> verify_package(const fs::path& root)
    {
        if (!exact_path(root, true)) return std::nullopt;
        Sha256 sha;
        const auto manifest_path = root / L"runtime-manifest.json";
        if (!exact_path(manifest_path, false)) return std::nullopt;
        auto manifest_file = open_pinned(manifest_path);
        const auto manifest_bytes = manifest_file ? read(manifest_file.get(), max_manifest) : std::nullopt;
        if (!manifest_bytes || !sha.matches(*manifest_bytes, manifest_digest)) return std::nullopt;
        Manifest manifest;
        constexpr glz::opts manifest_options{.error_on_unknown_keys = false};
        if (glz::read<manifest_options>(manifest, *manifest_bytes) || manifest.schema != 1 ||
            manifest.platform != "win-amd64" || manifest.python_version != PY_VERSION ||
            manifest.python_library != "python314.dll" || manifest.module_paths.size() != module_paths.size() ||
            !manifest.payload_bytes || manifest.payload_bytes > max_runtime ||
            manifest.files.empty() || manifest.files.size() > max_entries) return std::nullopt;
        for (std::size_t index = 0; index < module_paths.size(); ++index)
            if (manifest.module_paths[index] != module_paths[index]) return std::nullopt;
        for (auto file : {std::string_view("python314.dll"), std::string_view("python314.zip"),
             std::string_view("python314._pth"), std::string_view("app/tools/__init__.py"),
             std::string_view("app/tools/silverfish/__init__.py"), entry_file})
            if (!manifest.files.contains(std::string(file))) return std::nullopt;
        if (!exact_path(root / L"app", true) || !exact_path(root / L"site-packages", true)) return std::nullopt;

        RuntimePackage result{root, {}};
        result.files.reserve(manifest.files.size() + 1);
        result.files.push_back(std::move(manifest_file));
        std::size_t entries = 0, bytes = 0;
        for (const auto& item : fs::recursive_directory_iterator(root))
        {
            if (++entries > max_entries) return std::nullopt;
            const auto& path = item.path();
            const auto relative = path.lexically_relative(root).generic_string();
            if (!portable_file(relative)) return std::nullopt;
            if (item.is_directory())
            {
                if (!exact_path(path, true)) return std::nullopt;
                continue;
            }
            if (path == manifest_path) continue;
            const auto digest = manifest.files.find(relative);
            if (digest == manifest.files.end() || !exact_path(path, false)) return std::nullopt;
            auto file = open_pinned(path);
            const auto data = file ? read(file.get(), static_cast<std::size_t>(manifest.payload_bytes) - bytes) : std::nullopt;
            if (!data || !sha.matches(*data, digest->second)) return std::nullopt;
            if (relative == "python314._pth" && *data != "python314.zip\n.\nsite-packages\napp\n") return std::nullopt;
            bytes += data->size();
            result.files.push_back(std::move(file));
            manifest.files.erase(digest);
        }
        if (!manifest.files.empty() || bytes != manifest.payload_bytes) return std::nullopt;
        return result;
    }

    template<class Function>
    Function resolve(HMODULE module, const char* name)
    {
        return std::bit_cast<Function>(GetProcAddress(module, name));
    }

    const wchar_t* run_python(const fs::path& runtime)
    {
        if (GetModuleHandleW(python_dll) || GetModuleHandleW(L"python3.dll")) return L"python_already_loaded";
        const auto library = runtime / python_dll;
        // Loaded extensions and the interpreter stay mapped for the lifetime of this engine.
        const auto module = LoadLibraryExW(library.c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!module || !windows_path_equal(module_image(module), library))
            return L"python_library_unavailable";
#define SF_PYTHON_API(symbol) const auto symbol = resolve<decltype(&::symbol)>(module, #symbol); \
    if (!symbol) return L"python_api_unavailable";
        SF_PYTHON_API(PyConfig_InitIsolatedConfig)
        SF_PYTHON_API(PyConfig_SetString)
        SF_PYTHON_API(PyConfig_SetArgv)
        SF_PYTHON_API(PyConfig_Clear)
        SF_PYTHON_API(PyWideStringList_Append)
        SF_PYTHON_API(PyStatus_Exception)
        SF_PYTHON_API(Py_IsInitialized)
        SF_PYTHON_API(Py_InitializeFromConfig)
        SF_PYTHON_API(PyImport_ImportModule)
        SF_PYTHON_API(PyObject_GetAttrString)
        SF_PYTHON_API(PyObject_CallNoArgs)
        SF_PYTHON_API(Py_GetConstantBorrowed)
        SF_PYTHON_API(Py_DecRef)
        SF_PYTHON_API(PyErr_Clear)
        SF_PYTHON_API(Py_FinalizeEx)
#undef SF_PYTHON_API
        if (Py_IsInitialized()) return L"python_already_initialized";
        PyConfig config;
        PyConfig_InitIsolatedConfig(&config);
        config.use_environment = 0;
        config.user_site_directory = 0;
        config.install_signal_handlers = 0;
        config.configure_c_stdio = 0;
        config.parse_argv = 0;
        config.site_import = 0;
        config.safe_path = 1;
        config.write_bytecode = 0;
        config.module_search_paths_set = 1;
        wchar_t program[] = L"SilverfishCoopEmbeddedRuntime";
        wchar_t* argv[]{program};
        const auto ok = [&](PyStatus status) { return !PyStatus_Exception(status); };
        bool init = ok(PyConfig_SetString(&config, &config.home, runtime.c_str())) &&
                    ok(PyConfig_SetArgv(&config, 1, argv));
        for (const auto& path : {runtime / L"python314.zip", runtime, runtime / L"site-packages", runtime / L"app"})
            init = init && ok(PyWideStringList_Append(&config.module_search_paths, path.c_str()));
        init = init && ok(Py_InitializeFromConfig(&config));
        PyConfig_Clear(&config);
        if (!init) return L"python_initialization_failed";

        auto* imported = PyImport_ImportModule("tools.silverfish.embedded_startup");
        auto* entry = imported ? PyObject_GetAttrString(imported, "run") : nullptr;
        auto* result = entry ? PyObject_CallNoArgs(entry) : nullptr;
        const bool completed = result && result == Py_GetConstantBorrowed(Py_CONSTANT_NONE);
        if (result) Py_DecRef(result);
        if (entry) Py_DecRef(entry);
        if (imported) Py_DecRef(imported);
        PyErr_Clear();
        const int finalized = Py_FinalizeEx();
        if (!completed) return L"embedded_startup_failed";
        return finalized == 0 ? L"finished" : L"python_finalization_failed";
    }

    void worker(HMODULE module, std::wstring nonce, DWORD game_thread) noexcept
    {
        const wchar_t* state = L"preflight_error";
        try
        {
            const auto root = qualify_startup(module, nonce, state);
            if (!root.empty())
            {
                state = L"runtime_package_refused";
                if (auto runtime = verify_package(root))
                {
                    RC::Output::send(STR("[SilverfishCoopEmbeddedRuntime] state=starting pid={} game_thread={} worker_thread={}\n"),
                        GetCurrentProcessId(), game_thread, GetCurrentThreadId());
                    state = L"embedded_startup_failed";
                    state = run_python(runtime->root);
                }
            }
        }
        catch (...) {}
        RC::Output::send(STR("[SilverfishCoopEmbeddedRuntime] state={}\n"), state);
    }

    class EmbeddedRuntime final : public RC::CppUserModBase
    {
        Hook::GlobalCallbackId tick = Hook::ERROR_ID;

    public:
        EmbeddedRuntime()
        {
            ModName = mod_name;
            ModVersion = STR("0.1");
            ModIntendedSDKVersion = STR("4.0.0");
        }

        void on_unreal_init() override
        {
            auto nonce = startup_nonce();
            if (!lower_hex(manifest_digest, 64) || nonce.empty() || environment(embedded_env) != L"1" ||
                environment(bridge_env).empty()) return;
            tick = Hook::RegisterEngineTickPostCallback(
                [nonce = std::move(nonce)](Hook::TCallbackIterationData<void>&, U::UEngine*, float, bool idle) {
                    if (idle || !U::IsInGameThreadRaw() || started.exchange(true, std::memory_order_acq_rel)) return;
                    HMODULE module{};
                    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                        reinterpret_cast<LPCWSTR>(&module_anchor), &module)) return;
                    try { std::thread(worker, module, nonce, GetCurrentThreadId()).detach(); }
                    catch (...) { RC::Output::send(STR("[SilverfishCoopEmbeddedRuntime] state=worker_unavailable\n")); }
                }, {false, false, mod_name, STR("Start the qualified embedded co-op runtime")});
        }

        ~EmbeddedRuntime() override
        {
            if (tick != Hook::ERROR_ID) Hook::UnregisterCallback(tick);
        }
    };
}

extern "C" __declspec(dllexport) RC::CppUserModBase* start_mod()
{
    return new EmbeddedRuntime();
}

extern "C" __declspec(dllexport) void uninstall_mod(RC::CppUserModBase* mod)
{
    delete mod;
}
