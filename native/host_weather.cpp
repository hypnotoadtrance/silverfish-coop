#include "host_weather.hpp"

#include "interaction_input.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <optional>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <LuaType/LuaUObject.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Vector = std::array<double, 3>;
    using Color = std::array<float, 4>;

    constexpr auto day_class = STR("/Game/Classes/Actors/DayCycle_SunControler_BP.DayCycle_SunControler_BP_C");
    constexpr auto sky_class = STR("/Game/Assets/StaticMeshes/Mesh_SkyBox/BP_Sky_Sphere_Custom.BP_Sky_Sphere_Custom_C");
    constexpr auto fog_class = STR("/Script/Engine.ExponentialHeightFog");
    constexpr auto cloud_class = STR("/Script/Engine.VolumetricCloud");
    constexpr auto mid_class = STR("/Script/Engine.MaterialInstanceDynamic");
    constexpr auto cloud_material = STR("/Game/Assets/Materials/Mat_Misc/m_SimpleVolumetricCloud_Stormy.m_SimpleVolumetricCloud_Stormy");
    constexpr auto rain_class = STR("/Game/Classes/Actors/Rain_BP.Rain_BP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");

    constexpr auto vector_path = STR("/Script/CoreUObject.Vector");
    constexpr auto rotator_path = STR("/Script/CoreUObject.Rotator");
    constexpr auto color_path = STR("/Script/CoreUObject.LinearColor");
    constexpr std::array vector_axes{STR("X"), STR("Y"), STR("Z")};
    constexpr std::array rotator_axes{STR("Pitch"), STR("Yaw"), STR("Roll")};
    constexpr std::array color_axes{STR("R"), STR("G"), STR("B"), STR("A")};

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::Cast<T>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
    }

    template <typename T>
    T* field(U::UStruct* type, const U::TCHAR* name)
    {
        auto* property = type ? U::CastField<T>(type->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        return object ? field<T>(object->GetClassPrivate(), name) : nullptr;
    }

    bool live(U::UObject* object)
    {
        constexpr auto discarded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(discarded) && !object->IsUnreachable();
    }

    bool exact(U::UObject* object, const U::TCHAR* path)
    {
        if (!live(object)) return false;
        auto* type = find<U::UClass>(path);
        return type && object->GetClassPrivate() == type;
    }

    U::UObject* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    std::optional<bool> boolean(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property ? std::optional{property->GetPropertyValueInContainer(object)} : std::nullopt;
    }

    std::optional<double> bounded(double value, double low, double high)
    {
        return std::isfinite(value) && value >= low && value <= high ? std::optional{value} : std::nullopt;
    }

    std::optional<double> scalar(U::UObject* object, const U::TCHAR* name, double low, double high)
    {
        if (auto* property = field<U::FFloatProperty>(object, name))
            return bounded(property->GetPropertyValueInContainer(object), low, high);
        if (auto* property = field<U::FDoubleProperty>(object, name))
            return bounded(property->GetPropertyValueInContainer(object), low, high);
        return {};
    }

    template <typename Element, typename Property, std::size_t Size>
    std::optional<std::array<Element, Size>> struct_value(U::FStructProperty* property, const void* data,
        const U::TCHAR* path, const std::array<const U::TCHAR*, Size>& names)
    {
        std::array<Element, Size> result{};
        auto* type = property ? property->GetStruct().Get() : nullptr;
        if (!type || type != find<U::UScriptStruct>(path) || property->GetSize() != sizeof(result)) return {};
        for (std::size_t index = 0; index < names.size(); ++index)
        {
            auto* axis = field<Property>(type, names[index]);
            if (!axis || axis->GetOffset_Internal() != index * sizeof(Element)) return {};
        }
        std::memcpy(result.data(), property->ContainerPtrToValuePtr<void>(data), sizeof(result));
        return result;
    }

    struct NativeRead
    {
        U::UFunction* function;
        alignas(16) std::array<std::byte, 48> arguments{};

        NativeRead(U::UObject* object, const U::TCHAR* name, int count) : function(nullptr)
        {
            // Every caller has already certified the exact stock target class.
            const U::FName function_name{name, U::FNAME_Find};
            auto* stock = function_name.IsNone() ? nullptr : object->GetClassPrivate()->GetFunctionByNameInChain(function_name);
            if (live(stock) && stock->HasAnyFunctionFlags(U::FUNC_Native) && !stock->HasAnyFunctionFlags(U::FUNC_Static) &&
                stock->GetNumParms() == count && stock->GetParmsSize() <= arguments.size())
                function = stock;
        }

        template <typename T>
        T* parameter(const U::TCHAR* name = STR("ReturnValue")) const
        {
            auto* property = field<T>(function, name);
            if (!property || !property->HasAnyPropertyFlags(U::CPF_Parm) || property->GetOffset_Internal() < 0 ||
                property->GetOffset_Internal() + property->GetSize() > function->GetParmsSize()) return nullptr;
            return property;
        }

        template <typename T>
        T* result() const
        {
            auto* property = parameter<T>();
            return property && property == function->GetReturnProperty() ? property : nullptr;
        }

        void invoke(U::UObject* object) { object->ProcessEvent(function, arguments.data()); }
    };

    U::UObject* owner(U::UObject* component)
    {
        NativeRead read(component, STR("GetOwner"), 1);
        auto* result = read.result<U::FObjectProperty>();
        if (!result) return nullptr;
        read.invoke(component);
        return result->GetObjectPropertyValue(result->ContainerPtrToValuePtr<void>(read.arguments.data()));
    }

    bool owned(U::UObject* component, U::UObject* actor, const U::TCHAR* path)
    {
        return exact(component, path) && owner(component) == actor;
    }

    U::UObject* mesh_material(U::UObject* mesh)
    {
        NativeRead read(mesh, STR("GetMaterial"), 2);
        auto* index = read.parameter<U::FIntProperty>(STR("ElementIndex"));
        auto* result = read.result<U::FObjectProperty>();
        if (!index || !result) return nullptr;
        index->SetPropertyValueInContainer(read.arguments.data(), 0);
        read.invoke(mesh);
        return result->GetObjectPropertyValue(result->ContainerPtrToValuePtr<void>(read.arguments.data()));
    }

    template <typename Output, typename ReadResult>
    std::optional<Output> material_parameter(U::UObject* material, const U::TCHAR* method,
        const U::TCHAR* name, ReadResult read_result)
    {
        NativeRead read(material, method, 2);
        auto* parameter = read.parameter<U::FNameProperty>(STR("ParameterName"));
        const U::FName value{name, U::FNAME_Find};
        if (!parameter || parameter->GetSize() != sizeof(value) || value.IsNone()) return {};
        // The reflected input is an FName with no owned storage.
        parameter->SetPropertyValueInContainer(read.arguments.data(), value);
        return read_result(read, material);
    }

    std::optional<std::string_view> profile_string(lua_State* stack, const char* key)
    {
        lua_pushstring(stack, key);
        lua_rawget(stack, 3);
        std::optional<std::string_view> result;
        if (lua_type(stack, -1) == LUA_TSTRING)
        {
            std::size_t size{};
            const char* text = lua_tolstring(stack, -1, &size);
            if (size > 0 && size <= 320 && std::string_view{text, size}.find('\0') == std::string_view::npos)
                result = std::string_view{text, size};
        }
        lua_pop(stack, 1);
        return result;
    }

    bool live_actor(U::UObject* object, U::UWorld* world)
    {
        return live(object) && boolean(object, STR("bActorIsBeingDestroyed")) == false &&
            static_cast<U::AActor*>(object)->GetWorld() == world;
    }

    U::UObject* attached_actor(U::UObject* actor, U::UWorld* world, const U::TCHAR* type, std::string_view name)
    {
        return exact(actor, type) && live_actor(actor, world) && boolean(actor, STR("bReplicates")) == false &&
            actor->GetFullName() == RC::to_wstring(name) ? actor : nullptr;
    }

    U::UObject* authored_day(U::UWorld* world, U::UObject* instance, std::string_view name)
    {
        const auto space = name.find(' ');
        const auto separator = name.find(':', space);
        if (space == name.npos || separator == name.npos ||
            !silverfish::world_path::object(name.substr(space + 1, separator - space - 1))) return nullptr;
        auto* day = attached_actor(find<U::AActor>(RC::to_wstring(name.substr(space + 1)).c_str()), world, day_class, name);
        return day && boolean(day, STR("Ready")) == true && boolean(day, STR("OnlyTrackTime")) == false &&
            boolean(day, STR("UpdateInRealTime")).has_value() && object_field(day, STR("Game Instance")) == instance
            ? day : nullptr;
    }

    std::optional<double> saved_seconds(U::UObject* save)
    {
        auto* time = field<U::FStructProperty>(save, STR("GameTime"));
        if (!time || time->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/CoreUObject.Timespan")) ||
            time->GetSize() != sizeof(std::int64_t)) return {};
        std::int64_t ticks{};
        std::memcpy(&ticks, time->ContainerPtrToValuePtr<void>(save), sizeof(ticks));
        if (ticks < 0) return {};
        const auto milliseconds = ticks / 10000;
        return bounded(static_cast<double>(milliseconds / 1000) + static_cast<double>(milliseconds % 1000) / 1000,
            0, 922337203685.0);
    }

    struct View
    {
        Vector north{}, sun{};
        bool visible{};
        double intensity{}, seconds{};
        Color sky{};
        std::array<double, 5> weather{};
        double rain_intensity{};
        Vector rain_direction{0, 0, 1};
    };

    struct SunTargets
    {
        U::UObject* north;
        U::UObject* sun;
        U::UObject* light;
    };

    std::optional<SunTargets> sun_targets(U::UObject* day)
    {
        auto* north = object_field(day, STR("Arrow_North"));
        auto* sun = object_field(day, STR("Arrow_Sun"));
        auto* light = object_field(day, STR("DLight_Sun"));
        auto* root = object_field(day, STR("RootComponent"));
        if (!live(root) || !owned(north, day, STR("/Script/Engine.ArrowComponent")) ||
            !owned(sun, day, STR("/Script/Engine.ArrowComponent")) ||
            !owned(light, day, STR("/Script/Engine.DirectionalLightComponent")) ||
            object_field(north, STR("AttachParent")) != root || object_field(sun, STR("AttachParent")) != north ||
            object_field(light, STR("AttachParent")) != sun) return {};
        return SunTargets{north, sun, light};
    }

    U::UObject* read_sun(View& report, U::UObject* day)
    {
        const auto targets = sun_targets(day);
        if (!targets) return nullptr;
        auto* north = targets->north;
        auto* light = targets->light;
        const auto rotation = struct_value<double, U::FDoubleProperty>(
            field<U::FStructProperty>(north, STR("RelativeRotation")), north, rotator_path, rotator_axes);
        const auto intensity = scalar(light, STR("Intensity"), 0, 100000);
        if (!rotation || !intensity ||
            !std::ranges::all_of(*rotation, [](double angle) { return bounded(angle, -360, 360).has_value(); }))
            return nullptr;

        NativeRead forward(light, STR("GetForwardVector"), 1);
        NativeRead visibility(light, STR("IsVisible"), 1);
        auto* direction = forward.result<U::FStructProperty>();
        auto* visible = visibility.result<U::FBoolProperty>();
        if (!direction || !visible) return nullptr;
        forward.invoke(light);
        const auto facing = struct_value<double, U::FDoubleProperty>(direction, forward.arguments.data(), vector_path, vector_axes);
        if (!facing || !std::ranges::all_of(*facing, [](double axis) { return bounded(axis, -1.01, 1.01).has_value(); }))
            return nullptr;
        visibility.invoke(light);
        report.north = *rotation;
        report.sun = *facing;
        report.visible = visible->GetPropertyValueInContainer(visibility.arguments.data());
        report.intensity = *intensity;
        return light;
    }

    bool read_sky(View& report, U::UWorld* world, U::UObject* day, U::UObject* light, std::string_view name)
    {
        auto* sky = attached_actor(object_field(day, STR("SkySphere")), world, sky_class, name);
        if (!sky || object_field(sky, STR("Sun_Component")) != light) return false;
        auto* mesh = object_field(sky, STR("SkySphereMesh"));
        auto* material = object_field(sky, STR("Sky material"));
        if (!owned(mesh, sky, STR("/Script/Engine.StaticMeshComponent")) || !exact(material, mid_class) ||
            mesh_material(mesh) != material) return false;
        const auto color = material_parameter<Color>(material, STR("K2_GetVectorParameterValue"), STR("Light direction"),
            [](NativeRead& read, U::UObject* target) -> std::optional<Color> {
                auto* result = read.result<U::FStructProperty>();
                if (!result) return {};
                read.invoke(target);
                return struct_value<float, U::FFloatProperty>(result, read.arguments.data(), color_path, color_axes);
            });
        if (!color || !std::all_of(color->begin(), color->begin() + 3,
            [](float axis) { return bounded(axis, -1.01, 1.01).has_value(); })) return false;
        report.sky = *color;
        return true;
    }

    struct WeatherTargets
    {
        U::UObject* fog;
        U::UObject* height;
        U::UObject* cloud;
        U::UObject* volume;
        U::UObject* material;
    };

    std::optional<WeatherTargets> weather_targets(U::UWorld* world, U::UObject* day,
        std::string_view fog_name, std::string_view cloud_name)
    {
        auto* fog = attached_actor(object_field(day, STR("HightFog")), world, fog_class, fog_name);
        auto* cloud = attached_actor(object_field(day, STR("Clouds_Ref")), world, cloud_class, cloud_name);
        auto* height = object_field(fog, STR("Component"));
        auto* volume = object_field(cloud, STR("VolumetricCloudComponent"));
        auto* material = object_field(day, STR("ClodMat_Inst"));
        if (!owned(height, fog, STR("/Script/Engine.ExponentialHeightFogComponent")) ||
            !owned(volume, cloud, STR("/Script/Engine.VolumetricCloudComponent")) ||
            object_field(fog, STR("RootComponent")) != height || object_field(cloud, STR("RootComponent")) != volume ||
            !exact(material, mid_class) || object_field(volume, STR("Material")) != material) return {};
        return WeatherTargets{fog, height, cloud, volume, material};
    }

    bool read_weather(View& report, U::UWorld* world, U::UObject* day, std::string_view fog_name, std::string_view cloud_name)
    {
        const auto targets = weather_targets(world, day, fog_name, cloud_name);
        if (!targets) return false;
        auto* height = targets->height;
        auto* volume = targets->volume;
        auto* material = targets->material;
        auto* parent = object_field(material, STR("Parent"));
        if (!live(parent) || parent != find<U::UObject>(cloud_material)) return false;

        const auto density = scalar(height, STR("FogDensity"), 0, 1.2001);
        const auto falloff = scalar(height, STR("FogHeightFalloff"), 0, 2);
        const auto bottom = scalar(volume, STR("LayerBottomAltitude"), 1, 10);
        const auto depth = scalar(volume, STR("LayerHeight"), 3, 20);
        const auto offset = material_parameter<double>(material, STR("K2_GetScalarParameterValue"), STR("DensityOffset"),
            [](NativeRead& read, U::UObject* target) -> std::optional<double> {
                auto* result = read.result<U::FFloatProperty>();
                if (!result) return {};
                read.invoke(target);
                return bounded(result->GetPropertyValueInContainer(read.arguments.data()), 0, 1);
            });
        if (!density || !falloff || !bottom || !depth || !offset) return false;
        report.weather = {*density, *falloff, *bottom, *depth, *offset};
        return true;
    }

    bool read_rain(View& report, U::UWorld* world, U::UObject* day)
    {
        auto* controller = field<U::FObjectProperty>(day, STR("RainController"));
        if (!controller) return false;
        auto* rain = controller->GetObjectPropertyValue(controller->ContainerPtrToValuePtr<void>(day));
        // The stock observer reports a fixed dry snapshot without a live stock rain controller.
        if (!exact(rain, rain_class)) return true;
        const auto destroyed = boolean(rain, STR("bActorIsBeingDestroyed"));
        if (!destroyed) return false;
        if (*destroyed || static_cast<U::AActor*>(rain)->GetWorld() != world) return true;
        const auto intensity = scalar(rain, STR("Intensity"), 0, 20);
        if (!intensity) return false;
        if (*intensity == 0) return true;
        const auto vector = struct_value<double, U::FDoubleProperty>(
            field<U::FStructProperty>(rain, STR("RainVector")), rain, vector_path, vector_axes);
        if (!vector || !bounded((*vector)[0], -1.01, 1.01) || !bounded((*vector)[1], -1.01, 1.01) ||
            !bounded((*vector)[2], .99, 1.01)) return false;
        report.rain_intensity = *intensity;
        report.rain_direction = *vector;
        return true;
    }

    int refuse(lua_State* stack, const char* reason)
    {
        static const bool fixture = [] {
            std::array<char, 2> value{};
            std::size_t size{};
            return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_HEADLESS") == 0 &&
                size == value.size() && value[0] == '1';
        }();
        if (!fixture) return 0;
        lua_pushnil(stack);
        lua_pushstring(stack, reason);
        return 2;
    }

    int verify_guest_components(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        const int count = lua_gettop(stack);
        const bool weather = count == 12;
        if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables() ||
            (count != 7 && !weather) || !lua_istable(stack, 3)) return 0;
        for (int index = 1; index <= count; ++index)
            if (index != 3 && (!lua_isinteger(stack, index) || lua_tointeger(stack, index) <= 0)) return 0;
        const auto matches = [stack](int index, U::UObject* object) {
            return live(object) && reinterpret_cast<std::uintptr_t>(object) ==
                static_cast<std::uintptr_t>(lua_tointeger(stack, index));
        };

        // The address arguments identify the applied lease. Resolve every pointer
        // from the current game-thread world and its stock actor bindings.
        auto* world = silverfish::native_interaction_input::current_listen_world();
        if (!matches(1, world)) return 0;
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* driver = object_field(world, STR("NetDriver"));
        if (!matches(2, instance) || !live(object_field(driver, STR("ServerConnection")))) return 0;

        const auto map = profile_string(stack, "map");
        const auto world_name = profile_string(stack, "world");
        const auto day_name = profile_string(stack, "day");
        if (!map || !world_name || !day_name || !silverfish::world_path::package(*map) ||
            world->GetFullName() != RC::to_wstring(*world_name)) return 0;
        const auto path = world->GetPathName();
        if (std::wstring_view{path}.substr(0, path.find('.')) != RC::to_wstring(*map)) return 0;
        auto* day = authored_day(world, instance, *day_name);
        if (!matches(4, day)) return 0;

        const auto sun = sun_targets(day);
        if (!sun || !matches(5, sun->north) || !matches(6, sun->sun) || !matches(7, sun->light)) return 0;
        if (weather)
        {
            const auto fog_name = profile_string(stack, "fog");
            const auto cloud_name = profile_string(stack, "cloud");
            if (!fog_name || !cloud_name) return 0;
            const auto targets = weather_targets(world, day, *fog_name, *cloud_name);
            if (!targets || !matches(8, targets->fog) || !matches(9, targets->height) || !matches(10, targets->cloud) ||
                !matches(11, targets->volume) || !matches(12, targets->material)) return 0;
        }
        lua_pushboolean(stack, true);
        return 1;
    }

    int read_host_weather(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables() || lua_gettop(stack) != 3 ||
            !lua_isinteger(stack, 1) || !lua_isinteger(stack, 2) || !lua_istable(stack, 3)) return 0;
        const auto world_address = lua_tointeger(stack, 1);
        const auto pawn_address = lua_tointeger(stack, 2);
        if (world_address <= 0 || pawn_address <= 0) return 0;
        auto* world = silverfish::native_interaction_input::current_listen_world();
        if (reinterpret_cast<std::uintptr_t>(world) != static_cast<std::uintptr_t>(world_address))
            return refuse(stack, "world_scope");
        auto* pawn = silverfish::native_interaction_input::current_local_pawn(pawn_address);
        auto* role = field<U::FByteProperty>(pawn, STR("Role"));
        if (!role || role->GetPropertyValueInContainer(pawn) != 3) return refuse(stack, "host_pawn_scope");
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* save = object_field(instance, STR("SaveSlot_Ref"));
        if (!exact(instance, instance_class) || !exact(save, save_class) || object_field(pawn, STR("Save")) != save)
            return refuse(stack, "canonical_save");

        const auto map = profile_string(stack, "map");
        const auto world_name = profile_string(stack, "world");
        const auto day_name = profile_string(stack, "day");
        const auto sky_name = profile_string(stack, "sky");
        const auto fog_name = profile_string(stack, "fog");
        const auto cloud_name = profile_string(stack, "cloud");
        if (!map || !world_name || !day_name || !sky_name || !fog_name || !cloud_name ||
            !silverfish::world_path::package(*map) || world->GetFullName() != RC::to_wstring(*world_name))
            return refuse(stack, "authored_world");
        const auto path = world->GetPathName();
        if (std::wstring_view{path}.substr(0, path.find('.')) != RC::to_wstring(*map))
            return refuse(stack, "authored_map");
        auto* day = authored_day(world, instance, *day_name);
        if (!day) return refuse(stack, "authored_day");

        View report;
        const auto seconds = saved_seconds(save);
        auto* light = read_sun(report, day);
        if (!seconds) return refuse(stack, "saved_clock");
        if (!light) return refuse(stack, "sun");
        if (!read_sky(report, world, day, light, *sky_name)) return refuse(stack, "sky");
        if (!read_weather(report, world, day, *fog_name, *cloud_name)) return refuse(stack, "fog_cloud");
        if (!read_rain(report, world, day)) return refuse(stack, "rain");
        report.seconds = *seconds;

        lua_createtable(stack, 0, 23);
        const auto number = [stack](const char* key, double value) {
            lua_pushnumber(stack, value);
            lua_setfield(stack, -2, key);
        };
        lua_pushlstring(stack, map->data(), map->size());
        lua_setfield(stack, -2, "map");
        lua_pushlstring(stack, day_name->data(), day_name->size());
        lua_setfield(stack, -2, "actor");
        lua_pushboolean(stack, report.visible);
        lua_setfield(stack, -2, "sun_visible");
        number("pitch", report.north[0]); number("yaw", report.north[1]); number("roll", report.north[2]);
        number("sun_x", report.sun[0]); number("sun_y", report.sun[1]); number("sun_z", report.sun[2]);
        number("sun_intensity", report.intensity);
        number("game_seconds", report.seconds);
        number("sky_r", report.sky[0]); number("sky_g", report.sky[1]); number("sky_b", report.sky[2]);
        number("fog_density", report.weather[0]); number("fog_falloff", report.weather[1]);
        number("cloud_bottom_km", report.weather[2]); number("cloud_height_km", report.weather[3]);
        number("cloud_density_offset", report.weather[4]);
        if (report.rain_intensity == 0)
        {
            constexpr std::array rain_keys{"rain_intensity", "rain_x", "rain_y", "rain_z"};
            for (std::size_t index = 0; index < rain_keys.size(); ++index)
            {
                lua_pushinteger(stack, index == 3 ? 1 : 0);
                lua_setfield(stack, -2, rain_keys[index]);
            }
        }
        else
        {
            number("rain_intensity", report.rain_intensity);
            number("rain_x", report.rain_direction[0]);
            number("rain_y", report.rain_direction[1]);
            number("rain_z", report.rain_direction[2]);
        }
        RC::LuaType::auto_construct_object(lua, day);
        return 2;
    }
}

void silverfish::native_host_weather::register_lua(Lua& lua)
{
    lua.register_function("SilverfishReadNativeHostWeather", read_host_weather);
    lua.register_function("SilverfishVerifyNativeGuestWeatherComponents", verify_guest_components);
}
