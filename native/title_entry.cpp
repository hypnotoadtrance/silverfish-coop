#include "title_entry.hpp"
#include "local_bridge.hpp"

#include <algorithm>
#include <array>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <LuaType/LuaAActor.hpp>
#include <LuaType/LuaUObject.hpp>
#include <LuaType/LuaUWorld.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/Core/Containers/FString.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace title = silverfish::native_title_entry;
    using Lua = RC::LuaMadeSimple::Lua;

    constexpr auto map = "/Game/Classes/Maps/MainMenu_P";
    constexpr auto world_path = STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto mode_path = STR("/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C");
    constexpr auto widget_path = STR("/Game/Classes/GUI/MainMenu_Widget.MainMenu_Widget_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto viewport_path = STR("/Script/Engine.GameViewportClient");
    constexpr std::uint64_t sequence_limit = std::uint64_t{1} << 53;

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    template <typename T>
    T* loaded(U::FWeakObjectPtr& cache, const U::TCHAR* path)
    {
        if (auto* value = cache.Get()) return static_cast<T*>(value);
        auto* value = U::Cast<T>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
        cache = value;
        return value;
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    std::optional<U::UObject*> object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        if (!property) return {};
        return property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object));
    }

    struct ObjectArray
    {
        U::FObjectProperty* element{};
        std::optional<U::FScriptArrayHelper> array;

        ObjectArray(U::UObject* owner, const U::TCHAR* name)
        {
            auto* property = field<U::FArrayProperty>(owner, name);
            element = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
            if (element && element->GetArrayDim() == 1)
                array.emplace(property, property->ContainerPtrToValuePtr<void>(owner));
        }

        U::UObject* at(int index)
        {
            return element->GetObjectPropertyValue(array->GetRawPtr(index));
        }
    };

    std::optional<bool> widget_bit(U::UObject* widget, U::FWeakObjectPtr& cache, const U::TCHAR* path)
    {
        auto* function = loaded<U::UFunction>(cache, path);
        auto* result = function ? U::CastField<U::FBoolProperty>(function->GetReturnProperty()) : nullptr;
        if (!result || result->GetOffset_Internal() != 0 || result->GetArrayDim() != 1 ||
            function->GetNumParms() != 1 || function->GetParmsSize() != 1 ||
            !function->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        std::uint8_t parameters{};
        widget->ProcessEvent(function, &parameters);
        return result->GetPropertyValueInContainer(&parameters);
    }

    std::optional<U::UObject*> widget_player(U::UObject* widget)
    {
        static U::FWeakObjectPtr cache;
        auto* function = loaded<U::UFunction>(cache, STR("/Script/UMG.Widget:GetOwningPlayer"));
        auto* result = function ? U::CastField<U::FObjectProperty>(function->GetReturnProperty()) : nullptr;
        if (!result || result->GetOffset_Internal() != 0 || result->GetArrayDim() != 1 ||
            function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(U::UObject*) ||
            !function->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        U::UObject* controller{};
        widget->ProcessEvent(function, &controller);
        return controller;
    }

    bool text_path(std::string_view path)
    {
        return !path.empty() && path.size() <= 4096 && std::ranges::none_of(path, [](unsigned char byte) {
            return byte < 0x20 || byte == 0x7f;
        });
    }

    bool path_slash(char byte) { return byte == '/' || byte == '\\'; }

    bool drive_absolute_path(std::string_view path)
    {
        return path.size() >= 3 && ((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) &&
            path[1] == ':' && path_slash(path[2]);
    }

    bool absolute_path(std::string_view path)
    {
        return drive_absolute_path(path) || (path.size() >= 2 && path_slash(path[0]) && path_slash(path[1]));
    }

    std::string path_text(const U::FString& path)
    {
        if (path.Len() <= 0 || path.Len() > 4096) return {};
        return RC::to_string(std::wstring_view{*path, static_cast<std::size_t>(path.Len())});
    }

    std::optional<U::FString> absolute_saved_path(U::FString path)
    {
        static U::FWeakObjectPtr function_cache, receiver_cache;
        auto* function = loaded<U::UFunction>(function_cache,
            STR("/Script/Engine.BlueprintPathsLibrary:ConvertRelativePathToFull"));
        auto* receiver = loaded<U::UObject>(receiver_cache, STR("/Script/Engine.Default__BlueprintPathsLibrary"));
        // Three native FString parameters in their declared order: path, base, return.
        struct Parameters { U::FString path, base, result; };
        if (!function || !receiver || function->GetOuterPrivate() != receiver->GetClassPrivate() ||
            !function->HasAllFunctionFlags(U::FUNC_Native | U::FUNC_Static) ||
            function->GetNumParms() != 3 || function->GetParmsSize() != sizeof(Parameters)) return {};
        std::array<bool, 3> qualified{};
        for (auto* parameter : U::TFieldRange<U::FProperty>(function, U::EFieldIterationFlags::None))
        {
            if (!parameter->HasAnyPropertyFlags(U::CPF_Parm)) continue;
            const auto offset = parameter->GetOffset_Internal();
            if (offset < 0 || offset % sizeof(U::FString) != 0 || offset >= sizeof(Parameters) ||
                !U::CastField<U::FStrProperty>(parameter) || parameter->GetArrayDim() != 1 ||
                parameter->GetSize() != sizeof(U::FString)) return {};
            const auto index = offset / sizeof(U::FString);
            if (qualified[index] || parameter->HasAnyPropertyFlags(U::CPF_ReturnParm) != (index == 2)) return {};
            qualified[index] = true;
        }
        if (!std::ranges::all_of(qualified, [](bool check) { return check; })) return {};
        Parameters parameters{std::move(path), {}, {}};
        receiver->ProcessEvent(function, &parameters);
        return std::move(parameters.result);
    }

    std::optional<U::FString> raw_saved_directory()
    {
        static U::FWeakObjectPtr function_cache, receiver_cache;
        auto* function = loaded<U::UFunction>(function_cache, STR("/Script/Engine.KismetSystemLibrary:GetProjectSavedDirectory"));
        auto* receiver = loaded<U::UObject>(receiver_cache, STR("/Script/Engine.Default__KismetSystemLibrary"));
        auto* result = function ? U::CastField<U::FStrProperty>(function->GetReturnProperty()) : nullptr;
        if (!receiver || !result || function->GetOuterPrivate() != receiver->GetClassPrivate() ||
            !function->HasAllFunctionFlags(U::FUNC_Native | U::FUNC_Static) ||
            function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(U::FString) ||
            result->GetArrayDim() != 1 || result->GetOffset_Internal() != 0) return {};
        U::FString path;
        receiver->ProcessEvent(function, &path);
        if (!text_path(path_text(path))) return {};
        return path;
    }

    std::optional<std::string> saved_directory()
    {
        auto path = raw_saved_directory();
        if (!path) return {};
        auto text = path_text(*path);
        if (!absolute_path(text))
        {
            const auto full = absolute_saved_path(std::move(*path));
            if (!full) return {};
            text = path_text(*full);
        }
        if (!text_path(text) || !absolute_path(text)) return {};
        return text;
    }

    bool directory_exists(std::string_view path)
    {
        static U::FWeakObjectPtr function_cache, receiver_cache;
        auto* function = loaded<U::UFunction>(function_cache, STR("/Script/Engine.BlueprintPathsLibrary:DirectoryExists"));
        auto* receiver = loaded<U::UObject>(receiver_cache, STR("/Script/Engine.Default__BlueprintPathsLibrary"));
        struct Parameters { U::FString path; std::uint8_t result{}; };
        if (!function || !receiver || function->GetOuterPrivate() != receiver->GetClassPrivate() ||
            !function->HasAllFunctionFlags(U::FUNC_Native | U::FUNC_Static) || function->GetNumParms() != 2 ||
            function->GetParmsSize() < sizeof(U::FString) + 1 || function->GetParmsSize() > sizeof(Parameters)) return false;
        auto* result = U::CastField<U::FBoolProperty>(function->GetReturnProperty());
        if (!result || result->GetArrayDim() != 1 || result->GetSize() != 1 ||
            result->GetOffset_Internal() != sizeof(U::FString)) return false;
        for (auto* parameter : U::TFieldRange<U::FProperty>(function, U::EFieldIterationFlags::None))
        {
            if (!parameter->HasAnyPropertyFlags(U::CPF_Parm) || parameter == result) continue;
            if (!U::CastField<U::FStrProperty>(parameter) || parameter->GetOffset_Internal() != 0 ||
                parameter->GetArrayDim() != 1 || parameter->GetSize() != sizeof(U::FString)) return false;
            Parameters parameters{U::FString{RC::to_wstring(path)}};
            receiver->ProcessEvent(function, &parameters);
            return result->GetPropertyValueInContainer(&parameters);
        }
        return false;
    }

    bool valid_wrapper(lua_State* stack, int index, const char* metatable)
    {
        if (!luaL_testudata(stack, index, metatable)) return false;
        lua_getfield(stack, index, "IsValid");
        if (!lua_iscfunction(stack, -1))
        {
            lua_pop(stack, 1);
            return false;
        }
        lua_pushvalue(stack, index);
        const bool valid = lua_pcall(stack, 1, 1, 0) == LUA_OK && lua_toboolean(stack, -1);
        lua_pop(stack, 1);
        return valid;
    }

    std::optional<title::Input> lua_input(const Lua& lua, int count)
    {
        auto* stack = lua.get_lua_state();
        if (!U::IsInGameThreadRaw() || lua_gettop(stack) != count || lua_type(stack, 3) != LUA_TSTRING ||
            !lua_isboolean(stack, 4) || !lua_isboolean(stack, 5) || !lua_isinteger(stack, 6) ||
            !lua_isboolean(stack, 7) || !valid_wrapper(stack, 1, "UWorld")) return {};
        std::size_t length{};
        const auto* raw = lua_tolstring(stack, 3, &length);
        const std::string_view phase{raw, length};
        if (phase != "opening" && phase != "ready") return {};
        U::UObject* controller{};
        if (!lua_isnil(stack, 2))
        {
            if (!valid_wrapper(stack, 2, "AActor")) return {};
            controller = lua.get_userdata<RC::LuaType::AActor>(2, true).get_remote_cpp_object();
        }
        return title::Input{lua.get_userdata<RC::LuaType::UWorld>(1, true).get_remote_cpp_object(), controller,
            phase == "ready" ? title::Phase::ready : title::Phase::opening, lua_toboolean(stack, 4) != 0,
            lua_toboolean(stack, 5) != 0, lua_toboolean(stack, 7) != 0, lua_tointeger(stack, 6)};
    }

    std::string name(U::UObject* object)
    {
        return object ? RC::to_string(object->GetFullName()) : "-";
    }

    std::string_view bit(std::optional<bool> value)
    {
        return value ? (*value ? "1" : "0") : "-";
    }

    void push_bool(lua_State* stack, std::optional<bool> value)
    {
        if (value) lua_pushboolean(stack, *value);
        else lua_pushnil(stack);
    }

    int lua_observe(const Lua& lua)
    {
        const auto input = lua_input(lua, 7);
        const auto observation = input ? title::observe(*input) : std::nullopt;
        if (!observation) return 0;
        auto* stack = lua.get_lua_state();
        lua.set_string(observation->ready() ? "ready" : "waiting");
        if (observation->ready()) lua_pushnil(stack);
        else lua.set_string(observation->reason);
        if (observation->menu) RC::LuaType::auto_construct_object(lua, observation->menu);
        else lua_pushnil(stack);
        if (observation->viewport) RC::LuaType::auto_construct_object(lua, observation->viewport);
        else lua_pushnil(stack);
        if (observation->count) lua_pushinteger(stack, *observation->count);
        else lua_pushnil(stack);
        push_bool(stack, observation->top);
        push_bool(stack, observation->mounted);
        push_bool(stack, observation->visible);
        return 8;
    }

    int lua_rows(const Lua& lua)
    {
        const auto input = lua_input(lua, 8);
        if (!input) return 0;
        auto* stack = lua.get_lua_state();
        std::optional<std::uint64_t> sequence;
        if (!lua_isnil(stack, 8))
        {
            if (!lua_isinteger(stack, 8)) return 0;
            const auto value = lua_tointeger(stack, 8);
            if (value <= 0 || static_cast<std::uint64_t>(value) >= sequence_limit) return 0;
            sequence = static_cast<std::uint64_t>(value);
        }
        const auto rows = title::rows(*input, sequence);
        if (!rows) return 0;
        lua.set_string(rows->title);
        if (!rows->startup) return 1;
        lua.set_string(*rows->startup);
        return 2;
    }
}

std::optional<title::SaveRoutingPaths> title::resolve_save_routing_paths(std::string_view id, std::string_view& error)
{
    error = "The game save directory is unavailable";
    if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables()) return {};
    const auto saved = raw_saved_directory();
    if (!saved) return {};
    auto root = path_text(*saved);
    while (!root.empty() && path_slash(root.back())) root.pop_back();
    root.append("/SaveGames");
    const auto full = absolute_saved_path(U::FString{RC::to_wstring(root)});
    if (!full) return {};
    auto absolute_root = path_text(*full);
    if (!text_path(absolute_root) || !drive_absolute_path(absolute_root)) return {};
    auto directory = absolute_root + "/SilverfishCoop/";
    directory.append(id);
    if (!directory_exists(directory))
    {
        error = "The private co-op save directory is unavailable";
        return {};
    }
    error = {};
    return SaveRoutingPaths{std::move(root), std::move(absolute_root), std::move(directory)};
}

std::optional<title::Observation> title::observe(const Input& input)
{
    if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables() ||
        !live(input.world) || input.world->GetPathName() != world_path) return {};

    Observation report;
    const auto waiting = [&report](std::string_view reason) -> Observation {
        report.reason = reason;
        return report;
    };
    if (input.listening || input.server || input.clients != 0 || input.has_local_pawn)
        return waiting("standalone_title_required");
    const auto driver = object_field(input.world, STR("NetDriver"));
    if (!driver) return {};
    if (live(*driver)) return waiting("stock_world_unavailable");

    static U::FWeakObjectPtr controller_cache, mode_cache, game_cache, local_cache, viewport_cache, widget_cache;
    const auto* controller_class = loaded<U::UClass>(controller_cache, controller_path);
    auto* controller = input.controller;
    if (!controller_class) return {};
    if (!live(controller) || controller->GetClassPrivate() != controller_class ||
        static_cast<U::AActor*>(controller)->GetWorld() != input.world)
        return waiting("stock_controller_unavailable");
    auto* role = field<U::FByteProperty>(controller, STR("Role"));
    auto* destroyed = field<U::FBoolProperty>(controller, STR("bActorIsBeingDestroyed"));
    const auto* local_type = loaded<U::UClass>(local_cache, STR("/Script/Engine.LocalPlayer"));
    const auto player = object_field(controller, STR("Player"));
    const auto pawn = object_field(controller, STR("Pawn"));
    if (!role || !destroyed || !local_type || !player || !pawn) return {};
    if (live(*pawn)) return waiting("standalone_title_required");
    if (role->GetPropertyValueInContainer(controller) != 3 || destroyed->GetPropertyValueInContainer(controller) ||
        !live(*player) || !(*player)->IsA(local_type)) return waiting("stock_controller_unavailable");
    report.controller = controller;

    const auto mode = object_field(input.world, STR("AuthorityGameMode"));
    const auto* mode_class = loaded<U::UClass>(mode_cache, mode_path);
    if (!mode || !mode_class) return {};
    if (!live(*mode) || (*mode)->GetClassPrivate() != mode_class ||
        static_cast<U::AActor*>(*mode)->GetWorld() != input.world)
        return waiting("stock_menu_mode_unavailable");

    const auto instance = object_field(input.world, STR("OwningGameInstance"));
    const auto* game_class = loaded<U::UClass>(game_cache, instance_path);
    if (!instance || !game_class) return {};
    if (!live(*instance) || (*instance)->GetClassPrivate() != game_class)
        return waiting("stock_game_instance_unavailable");
    ObjectArray locals{*instance, STR("LocalPlayers")};
    const auto possessing = object_field(*player, STR("PlayerController"));
    if (!locals.array || !possessing) return {};
    if (locals.array->Num() != 1 || locals.at(0) != *player || *possessing != controller) return {};

    const auto viewport = object_field(*player, STR("ViewportClient"));
    const auto* viewport_class = loaded<U::UClass>(viewport_cache, viewport_path);
    if (!viewport || !viewport_class) return {};
    if (!live(*viewport) || (*viewport)->GetClassPrivate() != viewport_class ||
        (*viewport)->GetOuterPrivate() != (*instance)->GetOuterPrivate())
        return waiting("stock_viewport_unavailable");
    if (object_field(*viewport, STR("World")) != input.world || object_field(*viewport, STR("GameInstance")) != *instance)
        return waiting("stock_viewport_unavailable");
    report.viewport = *viewport;

    ObjectArray stack{controller, STR("MenuStack")};
    if (!stack.array) return {};
    const auto count = stack.array->Num();
    if (count < 0 || count > 32) return waiting("stock_menu_stack_unqualified");
    report.count = count;
    const auto* widget_class = loaded<U::UClass>(widget_cache, widget_path);
    if (!widget_class) return waiting("stock_menu_class_unavailable");
    U::UObject* menu{};
    int index{};
    for (int position = 0; position < count; ++position)
    {
        auto* entry = stack.at(position);
        if (!live(entry) || entry->GetClassPrivate() != widget_class) continue;
        if (menu) return waiting("stock_menu_ambiguous");
        menu = entry;
        index = position;
    }
    if (!menu) return waiting("stock_menu_not_created");
    const auto owner = widget_player(menu);
    if (!owner) return {};
    if (*owner != controller || menu->GetOuterPrivate() != *instance)
        return waiting("stock_menu_owner_unqualified");

    report.menu = menu;
    report.top = index + 1 == count;
    static U::FWeakObjectPtr mounted_cache, visible_cache;
    report.mounted = widget_bit(menu, mounted_cache, STR("/Script/UMG.Widget:IsInViewport"));
    report.visible = widget_bit(menu, visible_cache, STR("/Script/UMG.Widget:IsVisible"));
    if (!report.mounted || !report.visible) return {};
    if (!*report.top) return waiting("stock_menu_below_dialog");
    if (!*report.mounted) return waiting("stock_menu_not_mounted");
    if (!*report.visible) return waiting("stock_menu_hidden");
    if (input.phase != Phase::ready) return waiting("stock_title_opening");
    return report;
}

std::optional<title::Rows> title::rows(const Input& input, std::optional<std::uint64_t> startup_sequence)
{
    const auto report = observe(input);
    if (!report) return {};
    Rows rows;
    rows.title = "native-title-entry\t";
    rows.title.append(map);
    const auto controller = name(report->controller), menu = name(report->menu), viewport = name(report->viewport);
    const auto count = report->count ? std::to_string(*report->count) : "-";
    using namespace std::string_view_literals;
    for (const auto field : {report->ready() ? "ready"sv : "waiting"sv, std::string_view{controller},
        std::string_view{menu}, std::string_view{viewport}, std::string_view{count}, bit(report->top),
        bit(report->mounted), bit(report->visible), report->ready() ? "-"sv : report->reason})
    {
        rows.title.push_back('\t');
        silverfish::local_bridge::append_status_text(rows.title, field);
    }
    if (report->ready() && startup_sequence)
    {
        if (!*startup_sequence || *startup_sequence >= sequence_limit) return {};
        const auto path = saved_directory();
        if (!path) return {}; // Let the existing producer qualify the full pair.
        rows.startup = "embedded-engine-startup\t";
        rows.startup->append(map).append("\t").append(std::to_string(*startup_sequence)).append("\t");
        silverfish::local_bridge::append_status_text(*rows.startup, *path);
    }
    return rows;
}

void title::register_lua(Lua& lua)
{
    lua.register_function("SilverfishObserveNativeTitle", lua_observe);
    lua.register_function("SilverfishNativeTitleRows", lua_rows);
}
