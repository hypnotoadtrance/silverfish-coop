#include <Windows.h>
#include <bcrypt.h>
#include <shellapi.h>
#include <shlobj.h>
#include <Python.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>
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
    constexpr wchar_t installed_env[] = L"SILVERFISH_COOP_INSTALLED_TITLE";
    constexpr wchar_t headless_env[] = L"SILVERFISH_COOP_HEADLESS";
    constexpr auto install_config = L"resident-install.json";
    constexpr auto portable_config = L"resident-portable.json";
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

    bool exists_on_disk(const fs::path& path)
    {
        const auto attributes = GetFileAttributesW(path.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES) return true;
        const auto error = GetLastError();
        if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) return false;
        throw std::system_error(static_cast<int>(error), std::system_category());
    }

    fs::path runtime_path(const fs::path& mod)
    {
        if (!exists_on_disk(mod / portable_config)) return mod / L"runtime";
        auto prefix = manifest_digest.substr(0, 16);
        return mod / (L"runtime-" + std::wstring(prefix.begin(), prefix.end()));
    }

    fs::path stage_manifest(const fs::path& game)
    {
        const auto portable = game / L"SilverFish/silverfish-coop-stage.json";
        return exists_on_disk(portable) ? portable : game / L"silverfish-coop-stage.json";
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

    bool matches_command_line(const fs::path& image, const fs::path& profile, const fs::path& work,
                              bool other_profile, bool legacy_loopback, bool stock_profile = false)
    {
        const CommandArguments arguments;
        if (!arguments.values || arguments.count < 1 ||
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
                if (stock_profile || userdir || !exact_path(supplied, true)) return false;
                if (!windows_path_equal(supplied, profile))
                {
                    const auto seat = supplied.parent_path();
                    if (!other_profile || !windows_equal(supplied.filename().native(), L"game-user") ||
                        !windows_path_equal(seat.parent_path(), work / L"titles") ||
                        !profile_name(seat.filename().native()) || !exact_path(seat, true) ||
                        !exact_path(seat.parent_path(), true)) return false;
                }
                userdir = true;
            }
            if (windows_equal(name, L"-multihome"))
            {
                if (loopback || value != L"127.0.0.1") return false;
                loopback = true;
            }
            if (windows_equal(name, L"-port")) return false;
        }
        return (userdir != stock_profile) && (loopback == legacy_loopback);
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
        const auto mod = binaries / L"ue4ss" / L"Mods" / mod_name;
        const bool installed = environment(installed_env) == L"1";
        state = L"startup_scope_stage_refused";
        if ((!installed && stage.filename() != L"stage") ||
            !windows_path_equal(image, stage / L"SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe") ||
            !windows_path_equal(addon, mod / L"dlls/main.dll") ||
            !exact_path(stage_manifest(stage), false) ||
            (installed && !(exact_path(mod / install_config, false) ||
                            exact_path(mod / portable_config, false)))) return {};

        const fs::path bridge(environment(bridge_env));
        const auto bridge_folder = bridge.parent_path();
        const auto run = bridge_folder.parent_path();
        const auto run_profile = run.parent_path();
        const auto role_root = run_profile.parent_path();
        const auto work = installed ? role_root.parent_path() : stage.parent_path();
        const auto profile = run_profile / L"game-user";
        const bool guest = windows_path_equal(role_root, work / L"clients");
        const bool resident = environment(L"SILVERFISH_COOP_RESIDENT_TITLE") == L"1";
        const bool unified = environment(L"SILVERFISH_COOP_UNIFIED_TITLE") == L"1";
        const bool valid_root = unified ? resident && windows_path_equal(role_root, work / L"titles")
                                        : guest || windows_path_equal(role_root, work / L"campaigns");
        state = L"startup_scope_bridge_refused";
        if (bridge.filename() != nonce || bridge_folder.filename() != L"game-bridge" || run.filename() != L"run" ||
            !profile_name(run_profile.filename().native()) || !valid_root || (installed && !unified)) return {};
        for (const auto& path : {role_root, run_profile, run, bridge_folder, bridge})
            if (!exact_path(path, true)) return {};

        state = L"startup_scope_arguments_refused";
        if (!matches_command_line(image, profile, work, resident && !unified, guest && !resident,
                                  installed && environment(headless_env) != L"1")) return {};
        return runtime_path(mod);
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

    struct InstalledBoot
    {
        int schema{}, port{};
        bool isolated{};
        std::string work, seat, campaign, bind, shipping_sha256;
        std::map<std::string, std::string> features;

        struct glaze
        {
            static constexpr auto value = glz::object(
                "schema", &InstalledBoot::schema, "work", &InstalledBoot::work, "seat", &InstalledBoot::seat,
                "campaign", &InstalledBoot::campaign, "bind", &InstalledBoot::bind, "port", &InstalledBoot::port,
                "isolated", &InstalledBoot::isolated, "features", &InstalledBoot::features,
                "shipping_sha256", &InstalledBoot::shipping_sha256);
        };
    };

    struct PortableBoot
    {
        int schema{};
        std::string shipping_sha256;
        std::map<std::string, std::string> features;
        std::map<std::string, std::string> isolated_features;

        struct glaze
        {
            static constexpr auto value = glz::object(
                "schema", &PortableBoot::schema, "shipping_sha256", &PortableBoot::shipping_sha256,
                "features", &PortableBoot::features, "isolated_features", &PortableBoot::isolated_features);
        };
    };

    bool under(const fs::path& child, const fs::path& root)
    {
        for (auto path = child; !path.empty(); path = path.parent_path())
        {
            if (windows_path_equal(path, root)) return true;
            if (path == path.root_path()) break;
        }
        return false;
    }

    std::wstring make_nonce()
    {
        std::array<UCHAR, 16> bytes{};
        if (BCryptGenRandom(nullptr, bytes.data(), static_cast<ULONG>(bytes.size()),
                            BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) return {};
        constexpr std::wstring_view digits = L"0123456789abcdef";
        std::wstring result;
        for (auto byte : bytes) { result += digits[byte >> 4]; result += digits[byte & 15]; }
        return result;
    }

    bool matches_shipping_image(const fs::path& image, std::string_view digest)
    {
        constexpr std::size_t max_image = 256 * 1024 * 1024;
        const auto file = open_pinned(image);
        const auto body = file ? read(file.get(), max_image) : std::nullopt;
        return body && Sha256{}.matches(*body, digest);
    }

    std::optional<fs::path> portable_workspace(bool& isolated)
    {
        const CommandArguments arguments;
        if (!arguments.values) return std::nullopt;
        fs::path profile;
        bool offscreen = false, null_renderer = false;
        for (int index = 1; index < arguments.count; ++index)
        {
            const std::wstring_view argument(arguments.values[index]);
            if (windows_equal(argument, L"-SilverfishCoopIsolated")) isolated = true;
            if (windows_equal(argument, L"-RenderOffScreen")) offscreen = true;
            if (windows_equal(argument, L"-NullRHI")) null_renderer = true;
            const auto equals = argument.find(L'=');
            if (equals != argument.npos && windows_equal(argument.substr(0, equals), L"-userdir"))
                profile = argument.substr(equals + 1);
        }
        if (isolated)
        {
            const auto title = profile.parent_path();
            const auto titles = title.parent_path();
            if (!offscreen || !null_renderer || !exact_path(profile, true) ||
                !windows_path_equal(profile.filename(), L"game-user") ||
                !windows_path_equal(title.filename(), L"local") ||
                !windows_path_equal(titles.filename(), L"titles")) return std::nullopt;
            return titles.parent_path();
        }
        if (!profile.empty() || offscreen || null_renderer) return std::nullopt;
        std::array<wchar_t, MAX_PATH> local{};
        if (FAILED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, local.data())) ||
            !exact_path(fs::path(local.data()), true)) return std::nullopt;
        return fs::path(local.data()) / L"SilverfishCoop" / L"Resident";
    }

    bool ensure_directory(const fs::path& path)
    {
        if (exact_path(path, true)) return true;
        if (!path.is_absolute() || path.lexically_normal() != path || path == path.root_path() ||
            exists_on_disk(path) || !ensure_directory(path.parent_path())) return false;
        fs::create_directory(path);
        return exact_path(path, true);
    }

    std::optional<InstalledBoot> load_boot_config(const fs::path& mod)
    {
        InstalledBoot config;
        const auto descriptor = mod / install_config;
        const auto portable = mod / portable_config;
        const bool has_descriptor = exists_on_disk(descriptor);
        if (has_descriptor)
        {
            const auto handle = exact_path(descriptor, false) ? open_pinned(descriptor) : Handle(nullptr);
            const auto bytes = handle ? read(handle.get(), 8192) : std::nullopt;
            if (!bytes || glz::read_json(config, *bytes) || config.schema != 1) return std::nullopt;
        }
        if (exists_on_disk(portable))
        {
            const auto handle = exact_path(portable, false) ? open_pinned(portable) : Handle(nullptr);
            const auto bytes = handle ? read(handle.get(), 8192) : std::nullopt;
            PortableBoot defaults;
            if (!bytes || glz::read_json(defaults, *bytes) || defaults.schema != 1) return std::nullopt;
            const auto fixture = [](const std::string& name) {
                return name == "SILVERFISH_COOP_HEADLESS" || name.ends_with("_FIXTURES");
            };
            for (const auto& [name, value] : defaults.features)
                if (fixture(name)) return std::nullopt;
            for (const auto& [name, value] : defaults.isolated_features)
                if (!fixture(name) || value != "1") return std::nullopt;
            if (has_descriptor && config.shipping_sha256 != defaults.shipping_sha256) return std::nullopt;
            if (!has_descriptor)
            {
                const auto work = portable_workspace(config.isolated);
                if (!work) return std::nullopt;
                const auto encoded = work->u8string();
                config.work.assign(encoded.begin(), encoded.end());
                config.schema = 1;
                config.shipping_sha256 = defaults.shipping_sha256;
                config.seat = "local";
                config.campaign = "default";
                config.bind = config.isolated ? "127.0.0.1" : "0.0.0.0";
                config.port = 7777;
            }
            config.features = std::move(defaults.features);
            if (config.isolated) config.features.merge(defaults.isolated_features);
        }
        return config.schema == 1 ? std::optional(std::move(config)) : std::nullopt;
    }

    std::optional<Handle> boot_installed_title(HMODULE module)
    {
        const auto image = module_image(nullptr);
        const auto mod = module_image(module).parent_path().parent_path();
        const auto binaries = image.parent_path();
        const auto game = binaries.parent_path().parent_path().parent_path();
        if (!exact_path(image, false) ||
            !windows_path_equal(mod, binaries / L"ue4ss" / L"Mods" / mod_name) ||
            !windows_path_equal(image, game / L"SilverFish/Binaries/Win64/SilverFish-Win64-Shipping.exe") ||
            !exact_path(stage_manifest(game), false)) return std::nullopt;
        auto configured = load_boot_config(mod);
        if (!configured) return std::nullopt;
        const auto& config = *configured;
        if (!lower_hex(std::string_view(config.shipping_sha256), 64) ||
            config.port < 1 || config.port > 65535 || config.features.size() > 64 ||
            config.features.contains("SILVERFISH_COOP_INSTALLED_TITLE") ||
            config.features.contains("SILVERFISH_COOP_EMBEDDED_STARTUP") ||
            config.features.contains("SILVERFISH_COOP_BRIDGE") ||
            config.features.contains("SILVERFISH_COOP_HEADLESS") != config.isolated) return std::nullopt;
        for (auto name : {"SILVERFISH_COOP_EMBEDDED_ENGINE", "SILVERFISH_COOP_RESIDENT_TITLE",
                          "SILVERFISH_COOP_UNIFIED_TITLE", "SILVERFISH_COOP_TITLE_DIRECT_JOIN"})
            if (!config.features.contains(name) || config.features.at(name) != "1") return std::nullopt;
        if (!std::all_of(config.bind.begin(), config.bind.end(), [](char ch) {
                return ch == '.' || ch == ':' || (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
            }) || config.bind.empty() || config.bind.size() > 45) return std::nullopt;
        const std::wstring seat(config.seat.begin(), config.seat.end());
        const std::wstring campaign(config.campaign.begin(), config.campaign.end());
        if (!profile_name(seat) || !profile_name(campaign)) return std::nullopt;
        const auto work = fs::path(std::u8string(config.work.begin(), config.work.end()));
        const auto title = work / L"titles" / seat;
        const auto run = title / L"run";
        const auto mailbox = run / L"game-bridge";
        if (!work.is_absolute() || work.lexically_normal() != work ||
            under(work, game) || under(game, work) ||
            !matches_command_line(image, title / L"game-user", work, false, false, !config.isolated) ||
            !matches_shipping_image(image, config.shipping_sha256))
            return std::nullopt;

        std::map<std::wstring, std::wstring> updates;
        for (const auto& [key, value] : config.features)
        {
            if (key.size() > 96 || !key.starts_with("SILVERFISH_COOP_") ||
                !std::ranges::all_of(key, [](char ch) { return ch == '_' || (ch >= 'A' && ch <= 'Z') ||
                                                                 (ch >= '0' && ch <= '9'); }) ||
                (value != "1" && value != "cpp" && value != "notdeer")) return std::nullopt;
            updates.emplace(std::wstring(key.begin(), key.end()), std::wstring(value.begin(), value.end()));
        }
        const auto runtime_manifest = open_pinned(runtime_path(mod) / L"runtime-manifest.json");
        const auto manifest = runtime_manifest ? read(runtime_manifest.get(), max_manifest) : std::nullopt;
        if (!manifest || !Sha256{}.matches(*manifest, manifest_digest)) return std::nullopt;
        if (exists_on_disk(mod / portable_config))
        {
            if (!ensure_directory(mailbox)) return std::nullopt;
        }
        else
        {
            for (const auto& path : {work, title.parent_path(), title, run, mailbox})
                if (!exact_path(path, true)) return std::nullopt;
        }

        Handle lease(CreateFileW((run / L"installed-title.lock").c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                                 OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        BY_HANDLE_FILE_INFORMATION info{};
        if (!lease || !GetFileInformationByHandle(lease.get(), &info) ||
            info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) return std::nullopt;
        const auto nonce = make_nonce();
        const auto bridge = mailbox / nonce;
        if (nonce.empty() || !fs::create_directory(bridge) || !exact_path(bridge, true)) return std::nullopt;
        const auto request = "{\"schema\":1,\"campaign\":\"" + config.campaign + "\",\"bind\":\"" + config.bind
            + "\",\"port\":" + std::to_string(config.port) + "}\n";
        Handle selection(CreateFileW((bridge / L"resident-title.json").c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                                     CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
        DWORD written{};
        if (!selection || !WriteFile(selection.get(), request.data(), static_cast<DWORD>(request.size()), &written, nullptr)
            || written != request.size() || !FlushFileBuffers(selection.get())) return std::nullopt;

        updates.emplace(bridge_env, bridge.native());
        updates.emplace(installed_env, L"1");
        for (const auto& [key, value] : updates)
            if (!environment(key.c_str()).empty()) return std::nullopt;
        for (const auto& [key, value] : updates)
        {
            if (_wputenv_s(key.c_str(), value.c_str()) == 0 && environment(key.c_str()) == value) continue;
            for (const auto& [applied, ignored] : updates) _wputenv_s(applied.c_str(), L"");
            return std::nullopt;
        }
        if (_wputenv_s(startup_env, nonce.c_str()) != 0 || startup_nonce() != nonce)
        {
            for (const auto& [key, ignored] : updates) _wputenv_s(key.c_str(), L"");
            _wputenv_s(startup_env, L"");
            return std::nullopt;
        }
        return lease;
    }

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
        std::optional<Handle> installed_lease;

    public:
        EmbeddedRuntime()
        {
            ModName = mod_name;
            ModVersion = STR("0.1");
            ModIntendedSDKVersion = STR("4.0.0");
            if (!lower_hex(manifest_digest, 64) || !environment(startup_env).empty()) return;
            HMODULE module{};
            if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                    reinterpret_cast<LPCWSTR>(&module_anchor), &module)) return;
            try
            {
                if (auto lease = boot_installed_title(module)) installed_lease.emplace(std::move(*lease));
            }
            catch (...) {}
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
