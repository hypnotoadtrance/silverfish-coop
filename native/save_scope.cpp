#include "save_scope.hpp"
#include "title_entry.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/Core/Containers/FString.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace title = silverfish::native_title_entry;
    using Lua = RC::LuaMadeSimple::Lua;

    struct Route
    {
        std::string id;
        std::wstring prefix, directory, raw_root, absolute_root;
    };

    // The stock async actions take their slot at construction. The hook holds
    // the same immutable route for the duration of one argument rewrite.
    std::mutex lifecycle;
    std::shared_ptr<const Route> selected;
    std::uint64_t generation{};

    struct Target
    {
        const U::TCHAR* path;
        int parameter;
        bool directory{};
    };

    constexpr std::array targets{
        Target{STR("/Script/Engine.GameplayStatics:SaveGameToSlot"), 1},
        Target{STR("/Script/Engine.GameplayStatics:LoadGameFromSlot"), 0},
        Target{STR("/Script/Engine.GameplayStatics:DoesSaveGameExist"), 0},
        Target{STR("/Script/Engine.GameplayStatics:DeleteGameInSlot"), 0},
        Target{STR("/Script/Engine.AsyncActionHandleSaveGame:AsyncLoadGameFromSlot"), 1},
        Target{STR("/Script/Engine.AsyncActionHandleSaveGame:AsyncSaveGameToSlot"), 2},
        Target{STR("/Script/BlueprintFileUtils.BlueprintFileUtilsBPLibrary:FindFiles"), 0, true},
    };

    struct Hook
    {
        U::FWeakObjectPtr function;
        U::CallbackId callback{U::InvalidCallbackId};
    };

    std::array<Hook, targets.size()> hooks;
    bool installed{};

    std::shared_ptr<const Route> current_route()
    {
        std::scoped_lock lock{lifecycle};
        return selected;
    }

    bool scope_id(std::string_view id)
    {
        return id.size() == 32 && id.find_first_not_of('0') != id.npos &&
            std::ranges::all_of(id, [](char letter) { return (letter >= '0' && letter <= '9') || (letter >= 'a' && letter <= 'f'); });
    }

    wchar_t lower_ascii(wchar_t letter)
    {
        return letter >= L'A' && letter <= L'Z' ? letter + (L'a' - L'A') : letter;
    }

    std::wstring normalize(std::wstring_view path)
    {
        std::wstring result;
        result.reserve(path.size());
        for (auto letter : path)
        {
            if (letter == L'\\') letter = L'/';
            if (letter == L'/' && !result.empty() && result.back() == L'/') continue;
            result.push_back(lower_ascii(letter));
        }
        if (!result.empty() && result.back() == L'/') result.pop_back();
        return result;
    }

    bool invalid_slot(std::wstring_view value)
    {
        return value.empty() || value == L"." || value == L".." ||
            value.find_first_of(L"/\\:<>|?*\"") != value.npos ||
            std::ranges::any_of(value, [](wchar_t letter) { return letter < 32 || letter == 127; });
    }

    U::FStrProperty* input(U::UFunction* function, int index)
    {
        for (auto* candidate : U::TFieldRange<U::FProperty>(function, U::EFieldIterationFlags::IncludeDeprecated))
        {
            if (!candidate->HasAnyPropertyFlags(U::CPF_Parm) || candidate->HasAnyPropertyFlags(U::CPF_ReturnParm)) continue;
            if (index-- != 0) continue;

            const auto offset = candidate->GetOffset_Internal();
            if (candidate->GetArrayDim() != 1 || candidate->GetSize() != sizeof(U::FString) ||
                offset < 0 || offset % alignof(U::FString) != 0 || offset + sizeof(U::FString) > function->GetParmsSize()) return nullptr;
            return U::CastField<U::FStrProperty>(candidate);
        }
        return nullptr;
    }

    void route(U::UnrealScriptFunctionCallableContext& context, U::FStrProperty* property, bool directory)
    {
        const auto current = current_route();
        if (!current) return;

        auto& frame = context.TheStack;
        auto* value = property->HasAnyPropertyFlags(U::CPF_OutParm)
            ? (frame.OutParms() ? U::FindOutParamValueAddress(frame, property) : nullptr)
            : (frame.Locals() ? property->ContainerPtrToValuePtr<void>(frame.Locals()) : nullptr);
        if (!value) return;

        const auto& field = property->GetPropertyValue(value);
        const std::wstring_view text{*field, static_cast<std::size_t>(field.Len())};
        if (directory)
        {
            const auto path = normalize(text);
            if (path == current->raw_root || path == current->absolute_root)
                property->SetPropertyValue(value, U::FString{current->directory});
            return;
        }

        // Config is the stock global settings slot. Keep it outside a campaign.
        if (std::ranges::equal(text, std::wstring_view{L"config"}, {}, lower_ascii)) return;
        auto result = current->prefix;
        result.append(invalid_slot(text) ? std::wstring_view{L"|InvalidSaveSlot"} : text);
        property->SetPropertyValue(value, U::FString{result});
    }

    void remove_hooks()
    {
        for (auto& hook : hooks)
        {
            if (auto* function = U::Cast<U::UFunction>(hook.function.Get()); function && hook.callback != U::InvalidCallbackId)
                function->UnregisterHook(hook.callback);
            hook = {};
        }
        installed = false;
    }

    bool install_hooks()
    {
        if (installed) return true;
        if (!U::UnrealInitializer::ShouldUseHashTables()) return false;

        std::array<U::UFunction*, targets.size()> functions{};
        std::array<U::FStrProperty*, targets.size()> inputs{};
        // Qualify every route before attaching any callback or allowing a scope
        // to become active. A partly routed save surface can touch solo files.
        for (std::size_t index = 0; index < targets.size(); ++index)
        {
            const auto& target = targets[index];
            auto* function = U::Cast<U::UFunction>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, target.path, false, true));
            if (!function || function->IsUnreachable() || !function->HasAllFunctionFlags(U::FUNC_Native | U::FUNC_Static) ||
                !function->GetFunc() || function->GetFunc() == U::UObject::ProcessInternalInternal.get_function_address()) return false;
            auto* property = input(function, target.parameter);
            if (!property || (!target.directory && property->GetFName() != U::FName{STR("SlotName"), U::FNAME_Find})) return false;
            functions[index] = function;
            inputs[index] = property;
        }
        try
        {
            for (std::size_t index = 0; index < targets.size(); ++index)
            {
                const auto callback = functions[index]->RegisterPreHook(
                    [property = inputs[index], directory = targets[index].directory](U::UnrealScriptFunctionCallableContext& context, void*) {
                        route(context, property, directory);
                    });
                if (callback == U::InvalidCallbackId)
                {
                    remove_hooks();
                    return false;
                }
                hooks[index] = {functions[index], callback};
            }
        }
        catch (...)
        {
            remove_hooks();
            return false;
        }
        installed = true;
        return true;
    }

    std::string_view stack_id(lua_State* stack)
    {
        if (lua_gettop(stack) != 1 || lua_type(stack, 1) != LUA_TSTRING) return {};
        std::size_t length{};
        const auto* data = lua_tolstring(stack, 1, &length);
        return {data, length};
    }

    int push_text(lua_State* stack, std::string_view result)
    {
        if (result.empty()) return 0;
        lua_pushlstring(stack, result.data(), result.size());
        return 1;
    }

    std::string_view select(std::string_view id)
    {
        if (!scope_id(id)) return "Expected a co-op save scope ID";
        std::uint64_t before{};
        {
            std::scoped_lock lock{lifecycle};
            if (selected) return "Release the current co-op save scope first";
            before = generation;
        }

        std::string_view error;
        const auto paths = title::resolve_save_routing_paths(id, error);
        if (!paths) return error;
        auto route = std::make_shared<const Route>(Route{std::string{id}, L"SilverfishCoop/" + RC::to_wstring(id) + L"/",
            RC::to_wstring(paths->directory) + L"/", normalize(RC::to_wstring(paths->raw_root)),
            normalize(RC::to_wstring(paths->absolute_root))});

        std::scoped_lock lock{lifecycle};
        if (generation != before) return "The co-op save scope changed during selection";
        if (selected) return "Release the current co-op save scope first";
        if (!install_hooks()) return {};
        selected = std::move(route);
        return "selected";
    }

    int lua_select(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        return push_text(stack, select(stack_id(stack)));
    }

    int lua_release(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack);
        bool released{};
        {
            std::scoped_lock lock{lifecycle};
            if (selected && selected->id == id)
            {
                selected.reset();
                ++generation;
                released = true;
            }
        }
        lua_pushboolean(stack, released);
        return 1;
    }

    int lua_selected(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        const auto route = current_route();
        return route ? push_text(lua.get_lua_state(), route->id) : 0;
    }
}

void silverfish::native_save_scope::register_lua(Lua& lua)
{
    lua.register_function("SilverfishSelectNativeSaveScope", lua_select);
    lua.register_function("SilverfishReleaseNativeSaveScope", lua_release);
    lua.register_function("SilverfishSelectedNativeSaveScope", lua_selected);
}

void silverfish::native_save_scope::clear()
{
    std::scoped_lock lock{lifecycle};
    selected.reset();
    ++generation;
}

void silverfish::native_save_scope::uninstall_hooks()
{
    std::scoped_lock lock{lifecycle};
    selected.reset();
    ++generation;
    remove_hooks();
}
