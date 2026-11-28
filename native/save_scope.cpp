#include "save_scope.hpp"
#include "title_entry.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <LuaType/LuaUObject.hpp>
#include <LuaType/LuaUWorld.hpp>
#include <Unreal/Core/Containers/FString.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

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

    struct GuestView
    {
        U::FWeakObjectPtr instance, save, previous_save;
        std::wstring slot, previous_slot;
        bool previous_autosave{};
        std::optional<double> previous_travel_step;
    };

    std::optional<GuestView> guest;
    bool creating_guest{}, guest_restored{};

    struct GuestReference
    {
        U::FWeakObjectPtr instance, save;
    };

    std::optional<GuestReference> retired_guest;

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

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::UClass* loaded_class(U::FWeakObjectPtr& cache, const U::TCHAR* path)
    {
        if (auto* value = cache.Get()) return static_cast<U::UClass*>(value);
        auto* value = U::Cast<U::UClass>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
        cache = value;
        return value;
    }

    template <typename Property>
    Property* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<Property>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    std::wstring_view view(const U::FString& text)
    {
        return {*text, static_cast<std::size_t>(text.Len())};
    }

    struct GuestFields
    {
        U::UObject* instance{};
        U::UClass* save_class{};
        U::FObjectProperty* save{};
        U::FStrProperty* slot{};
        U::FBoolProperty* autosave{};
        U::FDoubleProperty* travel_step{};
        bool at_title{};

        U::UObject* current_save() const
        {
            return save->GetObjectPropertyValue(save->ContainerPtrToValuePtr<void>(instance));
        }

        void set_save(U::UObject* object) const
        {
            save->SetObjectPropertyValue(save->ContainerPtrToValuePtr<void>(instance), object);
        }

        std::wstring_view current_slot() const { return view(slot->GetPropertyValueInContainer(instance)); }
        bool current_autosave() const { return autosave->GetPropertyValueInContainer(instance); }
        bool installed(U::UObject* new_save, std::wstring_view new_slot) const
        {
            return current_save() == new_save && current_slot() == new_slot && !current_autosave();
        }

        void install(U::UObject* new_save, const std::wstring& new_slot) const
        {
            if (current_slot() != new_slot) slot->SetPropertyValueInContainer(instance, U::FString{new_slot});
            if (current_save() != new_save) set_save(new_save);
            if (current_autosave()) autosave->SetPropertyValueInContainer(instance, false);
        }
    };

    std::optional<GuestFields> guest_fields(U::UWorld* world)
    {
        if (!U::UnrealInitializer::ShouldUseHashTables() || !live(world)) return {};
        static U::FWeakObjectPtr world_type, instance_type, save_type, local_type, viewport_type;
        if (world->GetClassPrivate() != loaded_class(world_type, STR("/Script/Engine.World"))) return {};
        auto* instance = object(world, STR("OwningGameInstance"));
        if (!live(instance) || instance->GetClassPrivate() != loaded_class(instance_type,
            STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"))) return {};

        auto* local_players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = local_players ? U::CastField<U::FObjectProperty>(local_players->GetInner()) : nullptr;
        if (!entry || entry->GetArrayDim() != 1) return {};
        U::FScriptArrayHelper players(local_players, local_players->ContainerPtrToValuePtr<void>(instance));
        if (players.Num() != 1) return {};
        auto* local = entry->GetObjectPropertyValue(players.GetRawPtr(0));
        auto* local_class = loaded_class(local_type, STR("/Script/Engine.LocalPlayer"));
        auto* viewport = live(local) && local_class && local->IsA(local_class) ? object(local, STR("ViewportClient")) : nullptr;
        if (!live(viewport) || viewport->GetClassPrivate() != loaded_class(viewport_type, STR("/Script/Engine.GameViewportClient")) ||
            viewport->GetOuterPrivate() != instance->GetOuterPrivate() || object(viewport, STR("World")) != world ||
            object(viewport, STR("GameInstance")) != instance) return {};

        auto* driver_field = field<U::FObjectProperty>(world, STR("NetDriver"));
        if (!driver_field) return {};
        auto* driver = driver_field->GetObjectPropertyValue(driver_field->ContainerPtrToValuePtr<void>(world));
        const bool at_title = world->GetPathName() == STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P");
        if (at_title ? driver != nullptr : !live(driver) || object(driver, STR("World")) != world ||
            !live(object(driver, STR("ServerConnection")))) return {};

        auto* save_class = loaded_class(save_type, STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"));
        auto* save = field<U::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* slot = field<U::FStrProperty>(instance, STR("CurrentSaveSlot"));
        auto* autosave = field<U::FBoolProperty>(instance, STR("SaveAfterTransition"));
        auto* step = field<U::FDoubleProperty>(instance, STR("Travel_TimeStep"));
        if (!save_class || !save || save->GetPropertyClass().Get() != save_class || !slot || !autosave) return {};
        return GuestFields{instance, save_class, save, slot, autosave, step, at_title};
    }

    U::UObject* create_guest_save(U::UClass* save_class)
    {
        static U::FWeakObjectPtr receiver_cache, function_cache, save_type;
        const auto resolve = [](U::FWeakObjectPtr& cache, const U::TCHAR* path) {
            if (auto* value = cache.Get()) return value;
            auto* value = U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true);
            cache = value;
            return value;
        };
        auto* receiver = resolve(receiver_cache, STR("/Script/Engine.Default__GameplayStatics"));
        auto* function = U::Cast<U::UFunction>(resolve(function_cache, STR("/Script/Engine.GameplayStatics:CreateSaveGameObject")));
        auto* base = loaded_class(save_type, STR("/Script/Engine.SaveGame"));
        auto* input = field<U::FClassProperty>(function, STR("SaveGameClass"));
        auto* output = field<U::FObjectProperty>(function, STR("ReturnValue"));
        struct Arguments { U::UClass* type; U::UObject* result{}; } args{save_class};
        if (!receiver || !receiver->HasAnyFlags(U::RF_ClassDefaultObject) || !function || !base || !save_class->IsChildOf(base) ||
            function->GetOuterPrivate() != receiver->GetClassPrivate() ||
            !function->HasAllFunctionFlags(U::FUNC_Native | U::FUNC_Static) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != sizeof(Arguments) || !input || !input->HasAnyPropertyFlags(U::CPF_Parm) ||
            input->GetSize() != sizeof(args.type) || input->GetOffset_Internal() != offsetof(Arguments, type) ||
            input->GetMetaClass().Get() != base || !output || output != function->GetReturnProperty() ||
            output->GetSize() != sizeof(args.result) || output->GetOffset_Internal() != offsetof(Arguments, result) ||
            output->GetPropertyClass().Get() != base) return nullptr;
        receiver->ProcessEvent(function, &args);
        auto* outer = live(args.result) ? args.result->GetOuterPrivate() : nullptr;
        return live(outer) && outer->GetPathName() == STR("/Engine/Transient") &&
            args.result->GetClassPrivate() == save_class ? args.result : nullptr;
    }

    U::FArrayProperty* save_references(U::UObject* instance)
    {
        auto* references = field<U::FArrayProperty>(instance, STR("ReferencedObjects"));
        auto* entry = references ? U::CastField<U::FObjectProperty>(references->GetInner()) : nullptr;
        return entry && entry->GetArrayDim() == 1 ? references : nullptr;
    }

    // GameInstance's reflected references keep the one private Save alive if
    // stock swaps SaveSlot_Ref between a client visit and a return to title.
    bool reference_guest(U::UObject* instance, U::UObject* save, bool retain)
    {
        if (!live(instance) || !live(save)) return !retain;
        auto* references = save_references(instance);
        if (!references) return false;
        auto* entry = U::CastField<U::FObjectProperty>(references->GetInner());
        U::FScriptArrayHelper objects(references, references->ContainerPtrToValuePtr<void>(instance));
        for (int index = 0; index < objects.Num(); ++index)
        {
            if (entry->GetObjectPropertyValue(objects.GetRawPtr(index)) != save) continue;
            if (retain) return false;
            objects.RemoveValues(index, 1);
            return true;
        }
        if (!retain) return true;
        const auto index = objects.AddValue();
        if (index < 0 || index >= objects.Num()) return false;
        auto* storage = objects.GetRawPtr(index);
        entry->SetObjectPropertyValue(storage, save);
        if (entry->GetObjectPropertyValue(storage) == save) return true;
        objects.RemoveValues(index, 1);
        return false;
    }

    bool release_retired_guest()
    {
        if (!retired_guest) return true;
        if (!U::IsInGameThreadRaw() || !reference_guest(retired_guest->instance.Get(), retired_guest->save.Get(), false))
            return false;
        retired_guest.reset();
        return true;
    }

    void retire_guest()
    {
        if (guest)
        {
            retired_guest = GuestReference{guest->instance, guest->save};
            guest.reset();
        }
        release_retired_guest();
    }

    U::UObject* install_guest(const GuestFields& fields)
    {
        auto* save = guest->save.Get();
        if (guest->instance.Get() != fields.instance || !live(save) || save->GetClassPrivate() != fields.save_class) return nullptr;
        fields.install(save, guest->slot);
        return fields.installed(save, guest->slot) ? save : nullptr;
    }

    U::UObject* prepare_guest(U::UWorld* world, std::string_view id)
    {
        const auto fields = guest_fields(world);
        if (!fields) return nullptr;
        std::shared_ptr<const Route> lease;
        {
            std::scoped_lock lock{lifecycle};
            if (!selected || selected->id != id || creating_guest || guest_restored) return nullptr;
            if (guest) return install_guest(*fields);
            if (!fields->at_title || !save_references(fields->instance)) return nullptr;
            lease = selected;
            creating_guest = true;
        }
        struct FinishCreation
        {
            ~FinishCreation()
            {
                std::scoped_lock lock{lifecycle};
                creating_guest = false;
            }
        } finish_creation;

        // Release the lifecycle lock while the engine executes the reflected
        // constructor. Recheck the route and live objects before installing.
        U::FWeakObjectPtr owning_world{world}, owning_instance{fields->instance};
        auto* save = create_guest_save(fields->save_class);
        const auto current = guest_fields(U::Cast<U::UWorld>(owning_world.Get()));
        if (!save || !current || !current->at_title || current->instance != owning_instance.Get() ||
            current->save_class != fields->save_class) return nullptr;

        std::scoped_lock lock{lifecycle};
        if (selected != lease || guest || !live(save) || current->current_save() == save) return nullptr;
        auto* previous = current->current_save();
        GuestView snapshot{current->instance, save, live(previous) ? previous : nullptr,
            L"SFCoopGuest_" + RC::to_wstring(id), std::wstring{current->current_slot()}, current->current_autosave()};
        if (!reference_guest(current->instance, save, true)) return nullptr;
        guest.emplace(std::move(snapshot));
        return install_guest(*current);
    }

    bool remember_travel_step(const GuestFields& fields, U::UObject* save, std::string_view id, double step)
    {
        if (!fields.at_title || !fields.travel_step || !std::isfinite(step)) return false;
        std::scoped_lock lock{lifecycle};
        if (!selected || selected->id != id || !guest || guest->instance.Get() != fields.instance ||
            guest->save.Get() != save || !fields.installed(save, guest->slot) ||
            fields.travel_step->GetPropertyValueInContainer(fields.instance) != 0) return false;
        if (!guest->previous_travel_step) guest->previous_travel_step = step;
        return true;
    }

    bool restore_guest(const GuestFields& fields, std::string_view id)
    {
        if (!fields.at_title) return false;
        std::scoped_lock lock{lifecycle};
        if (!selected || selected->id != id) return false;
        if (!guest) return true;
        if (guest->instance.Get() != fields.instance || (guest->previous_travel_step && !fields.travel_step)) return false;

        auto* save = guest->save.Get();
        auto* previous = guest->previous_save.Get();
        if (!live(previous) || !previous->IsA(fields.save_class)) previous = nullptr;
        const bool restore_slot = fields.current_slot() == guest->slot;
        const bool restore_save = save && fields.current_save() == save;
        const bool restore_travel = guest->previous_travel_step &&
            fields.travel_step->GetPropertyValueInContainer(fields.instance) == 0;
        if (restore_slot) fields.slot->SetPropertyValueInContainer(fields.instance, U::FString{guest->previous_slot});
        if (restore_save) fields.set_save(previous);
        fields.autosave->SetPropertyValueInContainer(fields.instance, guest->previous_autosave);
        if (restore_travel) fields.travel_step->SetPropertyValueInContainer(fields.instance, *guest->previous_travel_step);

        if ((restore_slot && fields.current_slot() != guest->previous_slot) ||
            (restore_save && fields.current_save() != previous) || fields.current_autosave() != guest->previous_autosave ||
            (restore_travel && fields.travel_step->GetPropertyValueInContainer(fields.instance) != *guest->previous_travel_step))
            return false;
        if (!reference_guest(fields.instance, save, false)) return false;
        guest.reset();
        guest_restored = true;
        return true;
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

    std::string_view stack_id(lua_State* stack, int index, int count)
    {
        if (lua_gettop(stack) != count || lua_type(stack, index) != LUA_TSTRING) return {};
        std::size_t length{};
        const auto* data = lua_tolstring(stack, index, &length);
        return {data, length};
    }

    template <typename Wrapper>
    auto wrapped(const Lua& lua, int index, const char* type)
    {
        using Object = decltype(lua.get_userdata<Wrapper>(index, true).get_remote_cpp_object());
        auto* stack = lua.get_lua_state();
        if (!luaL_testudata(stack, index, type)) return Object{};
        lua_getfield(stack, index, "IsValid");
        if (!lua_iscfunction(stack, -1))
        {
            lua_pop(stack, 1);
            return Object{};
        }
        lua_pushvalue(stack, index);
        const bool valid = lua_pcall(stack, 1, 1, 0) == LUA_OK && lua_toboolean(stack, -1);
        lua_pop(stack, 1);
        return valid ? lua.get_userdata<Wrapper>(index, true).get_remote_cpp_object() : Object{};
    }

    int lua_prepare_guest(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack, 2, 2);
        auto* save = scope_id(id) ? prepare_guest(wrapped<RC::LuaType::UWorld>(lua, 1, "UWorld"), id) : nullptr;
        if (!save) return 0;
        RC::LuaType::auto_construct_object(lua, save);
        return 1;
    }

    int lua_rebind_guest(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack, 2, 2);
        const auto fields = scope_id(id) ? guest_fields(wrapped<RC::LuaType::UWorld>(lua, 1, "UWorld")) : std::nullopt;
        bool accepted{};
        if (fields)
        {
            std::scoped_lock lock{lifecycle};
            accepted = selected && selected->id == id && guest && install_guest(*fields);
        }
        lua_pushboolean(stack, accepted);
        return 1;
    }

    int lua_remember_travel_step(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack, 4, 5);
        bool accepted{};
        if (scope_id(id) && lua_type(stack, 5) == LUA_TNUMBER)
        {
            auto* world = wrapped<RC::LuaType::UWorld>(lua, 1, "UWorld");
            auto* instance = wrapped<RC::LuaType::UObject>(lua, 2, "UObject");
            auto* save = wrapped<RC::LuaType::UObject>(lua, 3, "UObject");
            const auto fields = guest_fields(world);
            if (fields && instance == fields->instance && save)
                accepted = remember_travel_step(*fields, save, id, lua_tonumber(stack, 5));
        }
        lua_pushboolean(stack, accepted);
        return 1;
    }

    int lua_restore_guest(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack, 2, 2);
        const auto fields = scope_id(id) ? guest_fields(wrapped<RC::LuaType::UWorld>(lua, 1, "UWorld")) : std::nullopt;
        lua_pushboolean(stack, fields && restore_guest(*fields, id));
        return 1;
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
            if (!release_retired_guest()) return "The previous co-op guest save could not be released";
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
        guest_restored = false;
        return "selected";
    }

    int lua_select(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        return push_text(stack, select(stack_id(stack, 1, 1)));
    }

    int lua_release(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw()) return 0;
        auto* stack = lua.get_lua_state();
        const auto id = stack_id(stack, 1, 1);
        bool released{};
        {
            std::scoped_lock lock{lifecycle};
            if (selected && selected->id == id && !creating_guest && !guest)
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
    lua.register_function("SilverfishPrepareNativeGuestSaveView", lua_prepare_guest);
    lua.register_function("SilverfishRebindNativeGuestSaveView", lua_rebind_guest);
    lua.register_function("SilverfishRememberNativeGuestTravelStep", lua_remember_travel_step);
    lua.register_function("SilverfishRestoreNativeGuestSaveView", lua_restore_guest);
}

bool silverfish::native_save_scope::active()
{
    return static_cast<bool>(current_route());
}

void silverfish::native_save_scope::clear()
{
    std::scoped_lock lock{lifecycle};
    selected.reset();
    retire_guest();
    ++generation;
}

void silverfish::native_save_scope::uninstall_hooks()
{
    std::scoped_lock lock{lifecycle};
    selected.reset();
    retire_guest();
    ++generation;
    remove_hooks();
}
