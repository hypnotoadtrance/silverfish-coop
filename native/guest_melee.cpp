#include "guest_melee.hpp"
#include "enemy_loot_registry.hpp"
#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Script.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    namespace cqc = silverfish::enemy_loot_profiles;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = H::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto map_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto damage_map = cqc::actors.front().world;
    constexpr auto damage_target_path = cqc::actors.front().path;
    constexpr auto damage_target_class = STR("/Game/Classes/NPCs/NPC_State_Infantry_CQC.NPC_State_Infantry_CQC_C");
    constexpr auto npc_class = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    constexpr auto effects_class = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C");
    constexpr auto effects_default = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.Default__FL_GameEffects_C");
    constexpr auto effects_sweep = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:MeleeSweep");
    constexpr auto pipe_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C");
    constexpr auto master_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Master_BP.MeleeWep_Master_BP_C");
    constexpr auto equipment_path = STR("/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C");
    constexpr auto animator_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleeWep_AnimBP.MeleeWep_AnimBP_C");
    constexpr auto sequence_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleWep_Anim/Melee_Slash_01.Melee_Slash_01");
    constexpr auto attack_path = STR("/Game/Classes/Libraries/Structs/MeleeAttack_Struct.MeleeAttack_Struct");
    constexpr auto damage_type_path = STR("/Game/Classes/Libraries/Structs/Damage_Struct.Damage_Struct");
    constexpr auto row_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto character_path = STR("/Game/Classes/Libraries/Structs/PlayerCharacter_Struct.PlayerCharacter_Struct");
    constexpr auto item_base = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto class_name = STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81");
    constexpr auto guid_name = STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A");
    constexpr auto animation_name = STR("Animation_2_C8CAEADF40C9F2C76A885BA8E5E9AA27");
    constexpr auto range_name = STR("Range_7_1ABB464F45C04D4C4BDA07A41F83FF24");
    constexpr auto radius_name = STR("Radius_9_4CAB95D84DE379A3C49DFD9CE1A93643");
    constexpr auto damage_name = STR("Damage_14_3B344B294DE0247300DB6F9513CDE617");
    constexpr auto blunt_name = STR("BluntForce_6_6923B3964740DE281BD773BCCAAC2772");
    constexpr auto breach_name = STR("Breach_21_E0B1E3A34ACBB341494DE885CECBF89B");
    constexpr auto attack_timeout = std::chrono::milliseconds{2500};
    constexpr auto hold_timeout = std::chrono::seconds{9};
    constexpr int max_rows = 4096;
    constexpr int max_effects = 256;
    constexpr std::size_t max_fenced_pipes = 32;
    constexpr double sweep_range = 230.0;
    constexpr float sweep_radius = 65.0f;
    constexpr int max_stock_sweep_contacts = 128;

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_ZONE");
    }

    bool fixture_enabled()
    {
        return enabled() && env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
    }

    enum class SwingMode { surface_fixture, damage_fixture, ordinary };

    template <typename T> T* find(const U::TCHAR* name)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, name);
    }

    template <typename T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* value = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* value = field<U::FObjectProperty>(owner, name);
        return value ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    U::UObject* read_object(U::FObjectProperty* value, const void* owner)
    {
        return value && owner ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* object)
    {
        constexpr auto invalid = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->IsUnreachable() && !object->HasAnyFlags(invalid);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* value = field<U::FBoolProperty>(owner, name);
        return value && value->GetPropertyValueInContainer(owner) == expected;
    }

    using Vector = std::array<double, 3>;

    template <class T> T* parameter(U::UFunction* function, const U::TCHAR* name)
    {
        auto* prop = field<T>(function, name);
        return prop && prop->HasAnyPropertyFlags(U::CPF_Parm) && prop->GetOffset_Internal() >= 0 &&
            prop->GetOffset_Internal() + prop->GetSize() <= function->GetParmsSize() ? prop : nullptr;
    }

    constexpr auto vector_path = STR("/Script/CoreUObject.Vector");
    constexpr auto rotator_path = STR("/Script/CoreUObject.Rotator");
    constexpr auto impact_vector_path = STR("/Script/Engine.Vector_NetQuantize");

    bool vector_field(U::FStructProperty* value, const U::TCHAR* type = vector_path)
    {
        auto* structure = find<U::UScriptStruct>(type);
        return value && value->GetSize() == sizeof(Vector) &&
            structure && value->GetStruct().Get() == structure;
    }

    std::optional<Vector> read_vector(U::FStructProperty* value, const void* buffer, const U::TCHAR* type = vector_path)
    {
        if (!buffer || !vector_field(value, type)) return {};
        Vector point{};
        std::memcpy(point.data(), value->ContainerPtrToValuePtr<void>(buffer), sizeof(point));
        return std::ranges::all_of(point, [](double axis) { return std::isfinite(axis); })
            ? std::optional{point} : std::nullopt;
    }

    Vector ahead(const Vector& origin, const Vector& direction, double distance)
    {
        return {origin[0] + distance * direction[0], origin[1] + distance * direction[1],
                origin[2] + distance * direction[2]};
    }

    std::optional<Vector> component_vector(U::UObject* component, const U::TCHAR* path, const U::TCHAR* type = vector_path)
    {
        auto* fn = find<U::UFunction>(path);
        auto* value = fn ? U::CastField<U::FStructProperty>(fn->GetReturnProperty()) : nullptr;
        if (!vector_field(value, type) || fn->GetNumParms() != 1 || fn->GetParmsSize() != sizeof(Vector) ||
            value->GetOffset_Internal() != 0 || !fn->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        Vector returned{};
        component->ProcessEvent(fn, returned.data());
        return read_vector(value, returned.data(), type);
    }

    struct TraceBuffer
    {
        alignas(16) std::array<std::byte, 512> values{};
        std::array<U::FProperty*, 3> constructed{};
        std::size_t count{};

        void initialize(U::FProperty* property)
        {
            property->InitializeValue_InContainer(values.data());
            constructed[count++] = property;
        }

        ~TraceBuffer()
        {
            while (count) constructed[--count]->DestroyValue_InContainer(values.data());
        }
    };

    std::optional<Vector> relative_rotation(U::UObject* camera)
    {
        auto* value = field<U::FStructProperty>(camera, STR("RelativeRotation"));
        return value && value->GetOffset_Internal() == 0x140 ? read_vector(value, camera, rotator_path) : std::nullopt;
    }

    bool set_camera_rotation(U::UObject* camera, const Vector& rotation, bool world)
    {
        const auto* path = world ? STR("/Script/Engine.SceneComponent:K2_SetWorldRotation")
                                 : STR("/Script/Engine.SceneComponent:K2_SetRelativeRotation");
        auto* function = find<U::UFunction>(path);
        auto* value = parameter<U::FStructProperty>(function, STR("NewRotation"));
        auto* hit = parameter<U::FStructProperty>(function, STR("SweepHitResult"));
        auto* sweep = parameter<U::FBoolProperty>(function, STR("bSweep"));
        auto* teleport = parameter<U::FBoolProperty>(function, STR("bTeleport"));
        if (!function || function->GetOuterPrivate() != find<U::UClass>(STR("/Script/Engine.SceneComponent")) ||
            !function->HasAnyFunctionFlags(U::FUNC_Native) || function->GetNumParms() != 4 ||
            function->GetReturnProperty() || function->GetParmsSize() > sizeof(TraceBuffer::values) ||
            !vector_field(value, rotator_path) || value->GetOffset_Internal() != 0 || !hit ||
            hit->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/Engine.HitResult")) ||
            !hit->HasAnyPropertyFlags(U::CPF_OutParm) || hit->GetOffset_Internal() != 0x20 ||
            !sweep || sweep->GetOffset_Internal() != 0x18 || !teleport || teleport->GetOffset_Internal() != 0x118) return false;
        TraceBuffer buffer;
        buffer.initialize(hit);
        auto* storage = buffer.values.data();
        std::memcpy(value->ContainerPtrToValuePtr<void>(storage), rotation.data(), sizeof(rotation));
        sweep->SetPropertyValueInContainer(storage, false);
        teleport->SetPropertyValueInContainer(storage, true);
        camera->ProcessEvent(function, storage);
        return true;
    }

    struct TraceCall
    {
        U::UObject* library{};
        U::UFunction* fn{};
        U::FStructProperty *start{}, *end{};
        U::FArrayProperty* ignore{};
        U::FObjectProperty *context{}, *ignored_actor{};
        U::FBoolProperty* returned{};
        U::FBoolProperty *ignore_self{}, *complex{};

        bool bind(const U::TCHAR* path, int count)
        {
            library = find<U::UObject>(STR("/Script/Engine.Default__KismetSystemLibrary"));
            fn = find<U::UFunction>(path);
            if (!library || !fn || !library->HasAnyFlags(U::RF_ClassDefaultObject) ||
                fn->GetOuterPrivate() != library->GetClassPrivate() || !fn->HasAnyFunctionFlags(U::FUNC_Native) ||
                fn->GetNumParms() != count || fn->GetParmsSize() > sizeof(TraceBuffer::values)) return false;
            context = parameter<U::FObjectProperty>(fn, STR("WorldContextObject"));
            start = parameter<U::FStructProperty>(fn, STR("Start"));
            end = parameter<U::FStructProperty>(fn, STR("End"));
            ignore = parameter<U::FArrayProperty>(fn, STR("ActorsToIgnore"));
            ignored_actor = ignore ? U::CastField<U::FObjectProperty>(ignore->GetInner()) : nullptr;
            returned = parameter<U::FBoolProperty>(fn, STR("ReturnValue"));
            ignore_self = parameter<U::FBoolProperty>(fn, STR("bIgnoreSelf"));
            complex = parameter<U::FBoolProperty>(fn, STR("bTraceComplex"));
            return context && vector_field(start) && vector_field(end) && ignored_actor &&
                ignored_actor->GetPropertyClass().Get() == find<U::UClass>(STR("/Script/Engine.Actor")) &&
                ignored_actor->GetOffset_Internal() == 0 && complex && ignore_self && returned &&
                returned == fn->GetReturnProperty();
        }

        void inputs(TraceBuffer& args, U::UObject* pipe, U::UObject* pawn, const Vector& origin, const Vector& destination,
                    bool trace_complex) const
        {
            auto* data = args.values.data();
            context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(data), pipe);
            std::memcpy(start->ContainerPtrToValuePtr<void>(data), origin.data(), sizeof(origin));
            std::memcpy(end->ContainerPtrToValuePtr<void>(data), destination.data(), sizeof(destination));
            complex->SetPropertyValueInContainer(data, trace_complex);
            ignore_self->SetPropertyValueInContainer(data, true);
            args.initialize(ignore);
            U::FScriptArrayHelper list(ignore, ignore->ContainerPtrToValuePtr<void>(data));
            ignored_actor->SetObjectPropertyValue(list.GetRawPtr(list.AddValue()), nullptr);
            ignored_actor->SetObjectPropertyValue(list.GetRawPtr(list.AddValue()), pawn);
        }
    };

    struct SweepHit
    {
        U::UScriptStruct* type = find<U::UScriptStruct>(STR("/Script/Engine.HitResult"));
        U::FWeakObjectProperty* component = field<U::FWeakObjectProperty>(type, STR("Component"));
        U::FStructProperty* impact = field<U::FStructProperty>(type, STR("ImpactPoint"));
        U::FBoolProperty* blocking = field<U::FBoolProperty>(type, STR("bBlockingHit"));
        U::FBoolProperty* penetrating = field<U::FBoolProperty>(type, STR("bStartPenetrating"));
        U::FFloatProperty* time = field<U::FFloatProperty>(type, STR("Time"));
        U::FFloatProperty* distance = field<U::FFloatProperty>(type, STR("Distance"));
        U::FFloatProperty* depth = field<U::FFloatProperty>(type, STR("PenetrationDepth"));

        bool bound() const
        {
            return type && component && vector_field(impact, impact_vector_path) &&
                blocking && penetrating && time && distance && depth;
        }

        U::UObject* contact_component(const void* row, double range) const
        {
            if (!row || penetrating->GetPropertyValueInContainer(row)) return nullptr;
            const float reached = distance->GetPropertyValueInContainer(row);
            const float fraction = time->GetPropertyValueInContainer(row);
            if (!std::isfinite(reached) || reached <= 1 || reached > range || !std::isfinite(fraction) ||
                fraction <= 0 || fraction > 1 || depth->GetPropertyValueInContainer(row) != 0.0f) return nullptr;
            auto* value = component->GetPropertyValueInContainer(row).Get();
            return live(value) ? value : nullptr;
        }

        U::UObject* owned_component(const void* row, U::UObject* enemy, double range) const
        {
            auto* value = contact_component(row, range);
            return value && value->GetOuterPrivate() == enemy ? value : nullptr;
        }
    };

    struct TraceProof
    {
        Vector origin{}, direction{};
        U::UObject* first{};
    };

    struct TraceDiagnostic
    {
        std::optional<Vector> origin, direction;
        U::FWeakObjectPtr component;
        std::string_view reason{"awaiting_contact"};
    };

    std::optional<TraceProof> stock_trace(U::UObject* pipe, U::UObject* pawn, U::UObject* enemy,
                                          std::string_view* refusal = nullptr, TraceDiagnostic* diagnostic = nullptr,
                                          double reach = sweep_range, float sweep_width = sweep_radius)
    {
        const auto reject = [&](std::string_view reason) -> std::optional<TraceProof> {
            if (refusal) *refusal = reason;
            if (diagnostic) diagnostic->reason = reason;
            return {};
        };
        auto* camera = object(pawn, STR("Camera"));
        if (!live(camera) || camera->GetOuterPrivate() != pawn ||
            camera->GetClassPrivate() != find<U::UClass>(STR("/Script/Engine.CameraComponent")))
            return reject("camera_identity");
        const auto origin = component_vector(camera, STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"));
        const auto direction = component_vector(camera, STR("/Script/Engine.SceneComponent:GetForwardVector"));
        if (!origin || !direction || std::abs(std::hypot((*direction)[0], (*direction)[1], (*direction)[2]) - 1.0) > 0.001)
            return reject("camera_vector_abi");
        if (diagnostic)
        {
            diagnostic->origin = origin;
            diagnostic->direction = direction;
        }
        const SweepHit hit;
        TraceCall line, sphere;
        if (!hit.bound()) return reject("hit_result_abi");
        if (!line.bind(STR("/Script/Engine.KismetSystemLibrary:LineTraceSingleForObjects"), 13))
            return reject("line_call_abi");
        if (!sphere.bind(STR("/Script/Engine.KismetSystemLibrary:SphereTraceMulti"), 14))
            return reject("sphere_call_abi");
        auto* objects = parameter<U::FArrayProperty>(line.fn, STR("ObjectTypes"));
        auto* kind = objects ? U::CastField<U::FByteProperty>(objects->GetInner()) : nullptr;
        auto* line_hit = parameter<U::FStructProperty>(line.fn, STR("OutHit"));
        auto* radius = parameter<U::FFloatProperty>(sphere.fn, STR("Radius"));
        auto* channel = parameter<U::FByteProperty>(sphere.fn, STR("TraceChannel"));
        auto* hits = parameter<U::FArrayProperty>(sphere.fn, STR("OutHits"));
        auto* hit_entry = hits ? U::CastField<U::FStructProperty>(hits->GetInner()) : nullptr;
        if (!kind || kind->GetOffset_Internal() != 0 || !line_hit || line_hit->GetStruct().Get() != hit.type ||
            !line_hit->HasAnyPropertyFlags(U::CPF_OutParm) || !radius || !channel || !hit_entry ||
            hit_entry->GetStruct().Get() != hit.type || !hits->HasAnyPropertyFlags(U::CPF_OutParm))
            return reject("trace_parameters_abi");

        TraceBuffer single;
        const auto destination = ahead(*origin, *direction, reach);
        line.inputs(single, pipe, pawn, *origin, destination, true);
        single.initialize(objects);
        single.initialize(line_hit);
        auto* data = single.values.data();
        U::FScriptArrayHelper types(objects, objects->ContainerPtrToValuePtr<void>(data));
        kind->SetPropertyValue(types.GetRawPtr(types.AddValue()), 0);
        kind->SetPropertyValue(types.GetRawPtr(types.AddValue()), 4);
        line.library->ProcessEvent(line.fn, data);
        Vector contact = destination;
        if (line.returned->GetPropertyValueInContainer(data))
        {
            const auto* row = line_hit->ContainerPtrToValuePtr<void>(data);
            if (diagnostic) diagnostic->component = hit.component->GetPropertyValueInContainer(row).Get();
            const auto impact = read_vector(hit.impact, row, impact_vector_path);
            if (!impact || !hit.blocking->GetPropertyValueInContainer(row) || !hit.contact_component(row, reach))
                return reject("line_contact");
            // Stock clips the sphere's sweep at this surface. The sphere contacts below
            // still have to belong to the exact target, even when the ray hits a wall.
            contact = *impact;
        }
        if (std::hypot(contact[0] - (*origin)[0], contact[1] - (*origin)[1], contact[2] - (*origin)[2]) <= sweep_width)
            return reject("line_too_near");

        TraceBuffer area;
        sphere.inputs(area, pipe, pawn, *origin, ahead(contact, *direction, -sweep_width), false);
        area.initialize(hits);
        data = area.values.data();
        radius->SetPropertyValueInContainer(data, sweep_width);
        channel->SetPropertyValueInContainer(data, 6);
        sphere.library->ProcessEvent(sphere.fn, data);
        U::FScriptArrayHelper contacted(hits, hits->ContainerPtrToValuePtr<void>(data));
        if (contacted.Num() < 1) return reject("sphere_empty");
        // Several hit components can belong to the same stock NPC, especially with armor and weapons equipped.
        if (contacted.Num() > (enemy ? 16 : max_stock_sweep_contacts)) return reject("sphere_bounds");
        U::UObject* first{};
        for (int index = 0; index < contacted.Num(); ++index)
        {
            const auto* row = contacted.GetRawPtr(index);
            if (diagnostic) diagnostic->component = hit.component->GetPropertyValueInContainer(row).Get();
            auto* component = hit.contact_component(row, reach);
            if (!component) return reject("sphere_contact");
            auto* owner = component->GetOuterPrivate();
            if (!live(owner) || (enemy && owner != enemy)) return reject("sphere_other_actor");
            if (!first)
            {
                first = component;
                enemy = owner;
            }
        }
        if (refusal) *refusal = "ready";
        if (diagnostic) diagnostic->reason = "ready";
        return TraceProof{*origin, *direction, first};
    }

    bool guid_from(std::string_view text, Guid& guid)
    {
        if (text.size() != 32 || !std::ranges::all_of(text, [](char ch) {
            return (ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F');
        })) return false;
        for (std::size_t index = 0; index < guid.size(); ++index)
        {
            const auto* group = text.data() + index * 8;
            if (std::from_chars(group, group + 8, guid[index], 16).ec != std::errc{}) return false;
        }
        return guid != Guid{};
    }

    struct Passives
    {
        U::FName brawler{STR("Brawler"), U::FNAME_Find};
        U::FName firestarter{STR("FireStarter"), U::FNAME_Find};

        bool allowed(U::FName name) const { return name != brawler && name != firestarter; }

        bool names(U::FArrayProperty* property, void* storage, std::vector<U::FName>* copy = nullptr) const
        {
            auto* element = property ? U::CastField<U::FNameProperty>(property->GetInner()) : nullptr;
            if (!element || element->GetArrayDim() != 1 || element->GetOffset_Internal() != 0 ||
                brawler.IsNone() || firestarter.IsNone()) return false;
            U::FScriptArrayHelper rows(property, storage);
            if (rows.Num() < 0 || rows.Num() > max_effects) return false;
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto name = element->GetPropertyValue(rows.GetRawPtr(index));
                if (!allowed(name)) return false;
                if (copy) copy->push_back(name);
            }
            return true;
        }

        bool canonical(U::UObject* game, std::vector<U::FName>& copy) const
        {
            auto* effects = field<U::FArrayProperty>(game, STR("PassiveEffects"));
            return effects && names(effects, effects->ContainerPtrToValuePtr<void>(game), &copy);
        }

        bool character(U::UObject* save) const
        {
            auto* character = field<U::FStructProperty>(save, STR("Character"));
            auto* type = find<U::UScriptStruct>(character_path);
            auto* effects = field<U::FArrayProperty>(type, STR("PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"));
            if (!character || !type || character->GetStruct().Get() != type || !effects ||
                !names(effects, effects->ContainerPtrToValuePtr<void>(character->ContainerPtrToValuePtr<void>(save)))) return false;

            auto* temporary = field<U::FMapProperty>(save, STR("PlayerTempEffects"));
            auto* key = temporary ? U::CastField<U::FNameProperty>(temporary->GetKeyProp()) : nullptr;
            auto* value = temporary ? U::CastField<U::FStructProperty>(temporary->GetValueProp()) : nullptr;
            auto* time = find<U::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
            if (!key || key->GetSize() != sizeof(U::FName) || key->GetArrayDim() != 1 || key->GetOffset_Internal() != 0 ||
                !value || !time || value->GetStruct().Get() != time) return false;
            const auto layout = U::FScriptMap::GetScriptLayout(
                key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
            if (value->GetOffset_Internal() != layout.ValueOffset) return false;
            const auto* map = temporary->ContainerPtrToValuePtr<U::FScriptMap>(save);
            if (map->Num() < 0 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > max_effects) return false;
            for (int index = 0; index < map->GetMaxIndex(); ++index)
            {
                if (!map->IsValidIndex(index)) continue;
                const auto* row = map->GetData(index, layout);
                if (!row || !allowed(key->GetPropertyValue(row))) return false;
            }
            return true;
        }

        bool gear(U::UObject* value) const
        {
            if (!value) return true;
            auto* type = U::Cast<U::UClass>(value);
            auto* base = find<U::UClass>(item_base);
            if (!live(type) || !base || !type->IsChildOf(base)) return false;
            auto* defaults = type->GetClassDefaultObject().Get();
            auto* effects = field<U::FArrayProperty>(defaults, STR("PassiveEffects"));
            auto* entry = effects ? U::CastField<U::FStrProperty>(effects->GetInner()) : nullptr;
            if (!defaults || defaults->GetClassPrivate() != type || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
                !entry || entry->GetArrayDim() != 1 || entry->GetOffset_Internal() != 0) return false;
            U::FScriptArrayHelper rows(effects, effects->ContainerPtrToValuePtr<void>(defaults));
            if (rows.Num() < 0 || rows.Num() > max_effects) return false;
            for (int index = 0; index < rows.Num(); ++index)
            {
                const auto& text = entry->GetPropertyValue(rows.GetRawPtr(index));
                if (text.Len() < 0 || text.Len() > 256 || (!text.IsEmpty() && !allowed(U::FName{*text, U::FNAME_Find})))
                    return false;
            }
            return true;
        }
    };

    bool selected_melee_item(U::UObject* save, U::UClass* weapon_class, const Guid& wanted, const Passives& passives)
    {
        auto* row = find<U::UScriptStruct>(row_path);
        auto* type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
        auto* klass = field<U::FClassProperty>(row, class_name);
        auto* id = field<U::FStructProperty>(row, guid_name);
        auto* slot = field<U::FIntProperty>(save, STR("PlayerEquipIndex"));
        if (!row || !type || !klass || !id || !slot || id->GetStruct().Get() != type) return false;
        const auto equipped = slot->GetPropertyValueInContainer(save);
        if (equipped < 0 || equipped >= max_rows) return false;

        std::array<U::FIntProperty*, 4> words{
            field<U::FIntProperty>(type, STR("A")), field<U::FIntProperty>(type, STR("B")),
            field<U::FIntProperty>(type, STR("C")), field<U::FIntProperty>(type, STR("D"))
        };
        if (std::ranges::find(words, nullptr) != words.end()) return false;
        int matches = 0;
        const auto check_row = [&](const void* record, bool selected) {
            auto* item = klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(record));
            if (!passives.gear(item)) return false;
            Guid identity{};
            const auto* canary_guid = id->ContainerPtrToValuePtr<void>(record);
            for (std::size_t index = 0; index < words.size(); ++index)
                identity[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(canary_guid));
            if (identity != wanted) return !selected;
            return ++matches == 1 && selected && item == weapon_class;
        };
        // Stock compiles the backpack, equipment, armor, and helmet. Checking every
        // typed row also refuses relevant effects from currently dormant items.
        for (const bool equipment : {false, true})
        {
            auto* property = field<U::FArrayProperty>(save, equipment ? STR("PlayerEquipment") : STR("PlayerBackpack"));
            auto* inner = property ? U::CastField<U::FStructProperty>(property->GetInner()) : nullptr;
            if (!inner || inner->GetStruct().Get() != row) return false;
            U::FScriptArrayHelper entries(property, property->ContainerPtrToValuePtr<void>(save));
            if (entries.Num() < 0 || entries.Num() > max_rows || (equipment && equipped >= entries.Num())) return false;
            for (int index = 0; index < entries.Num(); ++index)
                if (!check_row(entries.GetRawPtr(index), equipment && index == equipped)) return false;
        }
        for (auto* name : {STR("PlayerArmor"), STR("PlayerHelmet")})
        {
            auto* property = field<U::FStructProperty>(save, name);
            if (!property || property->GetStruct().Get() != row || !check_row(property->ContainerPtrToValuePtr<void>(save), false))
                return false;
        }
        return matches == 1;
    }

    U::UFunction* local_function(U::UObject* receiver, U::UClass* owner, const U::TCHAR* name, int parameters)
    {
        const U::FName exact{name, U::FNAME_Find};
        auto* function = receiver && !exact.IsNone() ? receiver->GetFunctionByNameInChain(exact) : nullptr;
        return function && owner && function->GetOuterPrivate() == owner && function->GetNumParms() == parameters &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) && !function->GetReturnProperty() ? function : nullptr;
    }

    struct Primary { bool held{}; double variance{}; };

    template <typename T> T* primary_input(U::UFunction* function, const U::TCHAR* name, std::size_t offset)
    {
        auto* property = field<T>(function, name);
        return property && property->GetOffset_Internal() == offset ? property : nullptr;
    }

    struct FixtureContact
    {
        U::FWeakObjectPtr target, camera;
        U::FFrame* restore_frame{};
        std::string name, component{"-"};
        std::string_view stage{"awaiting_contact"};
        std::string_view pose{"unchanged"};
        Vector eye{}, direction{}, target_origin{}, qualified_rotation{}, saved_relative{};
        double initial_health{-1}, contact_health{-1}, selected_health{-1};
        double eye_drift{-1}, direction_dot{-2}, target_drift{-1};
    };

    struct Swing
    {
        U::FWeakObjectPtr pawn{}, save{}, canonical{}, game{}, world{}, pipe{}, mesh{}, animation{};
        U::FWeakObjectPtr primary{}, contact{}, hit{}, graph{};
        Guid guid{};
        std::vector<U::FName> cache;
        Clock::time_point deadline{};
        std::int64_t serial{};
        std::string_view status{"inactive"};
        int presses{}, releases{}, contacts{}, qualified{};
        double host_health{}, guest_health{}, range{sweep_range};
        float radius{sweep_radius};
        bool pending{}, settled{}, passive_free{};
        SwingMode mode{SwingMode::surface_fixture};
        struct Input
        {
            int contacts{}, hits{}, charged{}, combo{};
            bool active{}, held{}, charged_attack{}, combo_attack{};
        } input;
        std::optional<FixtureContact> fixture;
        struct Damage
        {
            U::FWeakObjectPtr target{}, component{}, sweep{}, deal{}, library{}, graph{}, contact_candidate{};
            std::string target_name{"-"};
            std::string_view contact_status{"awaiting_contact"};
            U::FFrame *contact_frame{}, *sweep_frame{}, *deal_frame{};
            Vector origin{}, direction{};
            int sweeps{}, calls{};
            double before{-1}, after{-1}, total{-1};
            bool requested{};
            bool fenced_miss{};
            bool destructible{};
            bool blocked_by_obstacle{};
        } damage;
    };

    Swing swing;
    struct PipeFence { U::FWeakObjectPtr pipe, contact; };
    std::vector<PipeFence> fenced_pipes;
    std::int64_t serial{};
    std::array callbacks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};

    bool owner_current(const Swing& expected, bool require_ready = true)
    {
        auto* pawn = expected.pawn.Get();
        auto* save = expected.save.Get();
        auto* old_pipe = expected.pipe.Get();
        auto* mesh = expected.mesh.Get();
        auto* anim = expected.animation.Get();
        auto* world = expected.world.Get();
        auto* role = field<U::FByteProperty>(old_pipe, STR("Role"));
        return live(pawn) && live(old_pipe) && live(anim) && live(mesh) && live(world) &&
            object(pawn, STR("EquipedItem")) == old_pipe && role && role->GetPropertyValueInContainer(old_pipe) == 3 &&
            live(expected.game.Get()) && static_cast<U::AActor*>(old_pipe)->GetWorld() == world &&
            object(old_pipe, STR("Owner")) == pawn && object(old_pipe, STR("Instigator")) == pawn &&
            object(old_pipe, STR("User")) == pawn && object(old_pipe, STR("Player_Ref")) == pawn &&
            object(old_pipe, STR("As Game Instance FP")) == expected.game.Get() &&
            object(pawn, STR("As Game Instance FP")) == expected.game.Get() &&
            object(old_pipe, STR("Wep_Skeli")) == mesh && mesh->GetOuterPrivate() == old_pipe &&
            object(old_pipe, STR("As Melee Wep Anim BP")) == anim && object(mesh, STR("AnimScriptInstance")) == anim &&
            anim->GetOuterPrivate() == mesh && object(anim, STR("Wep_Object")) == old_pipe &&
            boolean(old_pipe, STR("HeldByPlayer"), true) && boolean(old_pipe, STR("bActorIsBeingDestroyed"), false) &&
            (!require_ready || (boolean(pawn, STR("Dead"), false) && boolean(pawn, STR("UsingItem"), false) &&
                boolean(pawn, STR("IsWeaponBlocked"), false) && boolean(pawn, STR("HeadUnderWater"), false))) &&
            silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, expected.canonical.Get());
    }

    double health(U::UObject* save)
    {
        auto* value = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
        return value ? value->GetPropertyValueInContainer(save) : -1.0;
    }

    double enemy_health(U::UObject* actor)
    {
        auto* value = field<U::FDoubleProperty>(actor, STR("Health"));
        return value ? value->GetPropertyValueInContainer(actor) : -1.0;
    }

    constexpr auto destructible_class = STR("/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP.Static_DestructableObject_Master_BP_C");
    constexpr auto destructible_damage = STR("/Game/Assets/StaticMeshes/Destructable/Static_DestructableObject_Master_BP.Static_DestructableObject_Master_BP_C:DealDamage");

    bool placed_destructible(U::UObject* candidate, U::UWorld* world)
    {
        auto* base = find<U::UClass>(destructible_class);
        if (!live(candidate) || !base || !candidate->GetClassPrivate()->IsChildOf(base)) return false;
        auto* actor = static_cast<U::AActor*>(candidate);
        return actor->GetWorld() == world && actor->HasAnyFlags(U::RF_WasLoaded) &&
            actor->GetLevel() == object(world, STR("PersistentLevel")) &&
            boolean(actor, STR("bReplicates"), false) && boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    bool qualified_enemy(U::UObject* candidate, U::UWorld* world)
    {
        if (!live(world) || !live(candidate) || candidate->GetClassPrivate() != find<U::UClass>(damage_target_class)) return false;
        const auto world_name = world->GetPathName();
        const auto actor_name = candidate->GetPathName();
        const auto profile = std::ranges::find_if(cqc::actors, [&](const auto& value) {
            return world_name == value.world && actor_name == value.path;
        });
        if (profile == cqc::actors.end() || find<U::AActor>(profile->path) != candidate) return false;
        auto* actor = static_cast<U::AActor*>(candidate);
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        auto* faction = field<U::FByteProperty>(actor, STR("Faction"));
        auto* maximum = field<U::FDoubleProperty>(actor, STR("Max_Health"));
        if (actor->GetWorld() != world || !actor->HasAnyFlags(U::RF_WasLoaded) ||
            actor->GetLevel() != object(world, STR("PersistentLevel")) || !role || role->GetPropertyValueInContainer(actor) != 3 ||
            !faction || faction->GetPropertyValueInContainer(actor) != 4 || !maximum ||
            maximum->GetPropertyValueInContainer(actor) != 160.0 || !boolean(actor, STR("Alive"), true) ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return false;
        return true;
    }

    bool combat_target(U::UObject* candidate, U::UWorld* world, bool ordinary, std::string_view* status = nullptr)
    {
        if (!ordinary) return qualified_enemy(candidate, world);
        const auto reject = [&](std::string_view cause) {
            if (status) *status = cause;
            return false;
        };
        if (!live(world) || !live(candidate)) return reject("target_class_unqualified");
        const auto* base = find<U::UClass>(npc_class);
        const bool npc = base && candidate->GetClassPrivate()->IsChildOf(base);
        if (!npc && !placed_destructible(candidate, world)) return reject("target_class_unqualified");
        auto* actor = static_cast<U::AActor*>(candidate);
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        if (actor->GetWorld() != world || !role || role->GetPropertyValueInContainer(actor) != 3)
            return reject("target_authority_unqualified");
        if ((npc && !boolean(actor, STR("Alive"), true)) || !boolean(actor, STR("bActorIsBeingDestroyed"), false))
            return reject("target_life_unqualified");
        const double hp = enemy_health(actor);
        return std::isfinite(hp) && hp > 0.0 ? true : reject("target_health_unqualified");
    }

    // Ordinary attacks can reach Blueprint children that override the stock damage graph.
    U::UFunction* combat_damage(U::UObject* actor, bool destructible)
    {
        const U::FName name{STR("DealDamage")};
        auto* definition = find<U::UFunction>(destructible ? destructible_damage
            : STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C:DealDamage"));
        auto* call = actor ? actor->GetFunctionByNameInChain(name) : nullptr;
        if (!definition || !call || call->GetNumParms() != 7 || call->GetReturnProperty() ||
            call->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return nullptr;
        if (destructible) return call == definition ? call : nullptr;
        for (auto* inherited = static_cast<U::UStruct*>(call); inherited; inherited = inherited->GetSuperStruct())
            if (inherited == definition) return call;
        return nullptr;
    }

    U::UObject* placed_enemy(U::UWorld* world)
    {
        auto* actor = find<U::AActor>(damage_target_path);
        return qualified_enemy(actor, world) ? actor : nullptr;
    }

    U::UObject* fixture_target(U::UWorld* world, std::int64_t address)
    {
        if (address <= 0 || !silverfish::native_guest_melee::ordinary_world(world)) return nullptr;
        for (const auto& row : cqc::actors)
        {
            if (world->GetPathName() != row.world) continue;
            auto* actor = find<U::AActor>(row.path);
            if (reinterpret_cast<std::uintptr_t>(actor) == static_cast<std::uintptr_t>(address) && qualified_enemy(actor, world))
                return actor;
        }
        return nullptr;
    }

    bool nonlethal_health(double value) { return std::isfinite(value) && value >= 100.0 && value <= 160.0; }

    std::optional<Vector> enemy_origin(U::UObject* actor)
    {
        return live(actor) ? component_vector(actor, STR("/Script/Engine.Actor:K2_GetActorLocation")) : std::nullopt;
    }

    double displacement(const Vector& first, const Vector& second)
    {
        return std::hypot(first[0] - second[0], first[1] - second[1], first[2] - second[2]);
    }

    double direction_dot(const Vector& first, const Vector& second)
    {
        return std::clamp(first[0] * second[0] + first[1] * second[1] + first[2] * second[2], -1.0, 1.0);
    }

    bool restore_fixture_camera(FixtureContact& fixture, U::UObject* pawn)
    {
        if (!fixture.restore_frame) return true;
        fixture.restore_frame = nullptr;
        auto* camera = fixture.camera.Get();
        const auto restored = live(camera) && camera->GetOuterPrivate() == pawn && object(pawn, STR("Camera")) == camera &&
            set_camera_rotation(camera, fixture.saved_relative, false) ? relative_rotation(camera) : std::nullopt;
        bool matches = restored.has_value();
        if (restored)
            for (std::size_t axis = 0; axis < restored->size(); ++axis)
                matches = matches && std::abs(std::remainder((*restored)[axis] - fixture.saved_relative[axis], 360.0)) <= 0.15;
        fixture.pose = matches ? "restored" : "restore_unverified";
        return matches;
    }

    void* output(U::FFrame& frame, U::FProperty* property)
    {
        auto* current = frame.OutParms();
        void* value{};
        for (int remaining = frame.Node()->GetNumParms(); current && remaining > 0; current = current->NextOutParm, --remaining)
        {
            if (current->Property != property) continue;
            if (value) return nullptr;
            value = current->PropAddr;
        }
        return current ? nullptr : value;
    }

    bool stock_attack(Swing& current, void* contact_args = nullptr)
    {
        auto* weapon = current.pipe.Get();
        auto* motor = current.animation.Get();
        auto* combo = field<U::FArrayProperty>(weapon, STR("ComboAttacks"));
        auto* combo_row = combo ? U::CastField<U::FStructProperty>(combo->GetInner()) : nullptr;
        auto* attack = field<U::FStructProperty>(motor, STR("ActiveAttack"));
        auto* parameter = field<U::FStructProperty>(current.contact.Get(), STR("Attack"));
        auto* charge = field<U::FFloatProperty>(weapon, STR("ChargeTime"));
        auto* type = find<U::UScriptStruct>(attack_path);
        if (!combo_row || !attack || !type || !parameter || !charge || attack->GetStruct().Get() != type ||
            combo_row->GetStruct().Get() != type || parameter->GetStruct().Get() != type ||
            !parameter->HasAnyPropertyFlags(U::CPF_Parm) ||
            parameter->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReferenceParm) ||
            parameter->GetOffset_Internal() != 0) return false;
        U::FScriptArrayHelper rows(combo, combo->ContainerPtrToValuePtr<void>(weapon));
        const bool ordinary = current.mode == SwingMode::ordinary;
        if (rows.Num() < 1 || rows.Num() > 16 || (!ordinary && (rows.Num() != 2 ||
            charge->GetPropertyValueInContainer(weapon) != 0.6f))) return false;
        auto* name = field<U::FObjectProperty>(type, animation_name);
        auto* range = field<U::FDoubleProperty>(type, range_name);
        auto* radius = field<U::FDoubleProperty>(type, radius_name);
        auto* breach = field<U::FBoolProperty>(type, breach_name);
        auto* damage = field<U::FStructProperty>(type, damage_name);
        auto* damage_type = find<U::UScriptStruct>(damage_type_path);
        auto* blunt = field<U::FDoubleProperty>(damage_type, blunt_name);
        if (!name || !range || !radius || !breach || !damage || !damage_type ||
            damage->GetStruct().Get() != damage_type || !blunt) return false;
        const auto matches = [&](const void* row) {
            return attack->Identical(attack->ContainerPtrToValuePtr<void>(motor), row) &&
                parameter->Identical(parameter->ContainerPtrToValuePtr<void>(contact_args), row);
        };
        const void* selected = rows.GetRawPtr(0);
        if (contact_args && current.input.active)
        {
            // The stock animation selects the charge or combo row. Validate that
            // exact authored struct at contact rather than choosing an attack here.
            selected = nullptr;
            auto* charged = field<U::FStructProperty>(weapon, STR("ChargedAttack"));
            if (charged && charged->GetStruct().Get() == type)
            {
                auto* row = charged->ContainerPtrToValuePtr<void>(weapon);
                if (matches(row))
                {
                    selected = row;
                    current.input.charged_attack = true;
                }
            }
            for (int index = 0; index < rows.Num() && !selected; ++index)
            {
                auto* row = rows.GetRawPtr(index);
                if (matches(row))
                {
                    selected = row;
                    current.input.combo_attack = index > 0;
                }
            }
        }
        if (!selected) return false;
        auto* sequence = name->GetObjectPropertyValue(name->ContainerPtrToValuePtr<void>(selected));
        const double reach = range->GetPropertyValueInContainer(selected);
        const double width = radius->GetPropertyValueInContainer(selected);
        if (!live(sequence)) return false;
        if (!(width > 0.0 && width < reach && reach <= 1000.0)) return false;
        if (!ordinary && (sequence != find<U::UObject>(sequence_path) || reach != sweep_range || width != sweep_radius ||
            breach->GetPropertyValueInContainer(selected) ||
            blunt->GetPropertyValueInContainer(damage->ContainerPtrToValuePtr<void>(selected)) != 30.0)) return false;
        if (!contact_args || current.input.active)
        {
            current.range = reach;
            current.radius = static_cast<float>(width);
            return true;
        }
        return reach == current.range && static_cast<float>(width) == current.radius && matches(selected);
    }

    bool idle(const Swing& current)
    {
        auto* anim = current.animation.Get();
        return boolean(anim, STR("IsMidSwing"), false) && boolean(anim, STR("Charging"), false) &&
            boolean(anim, STR("Charged"), false) && boolean(anim, STR("PrimaryInput"), false) &&
            boolean(anim, STR("AttackQueued"), false) && boolean(anim, STR("PerformingSpecialAction"), false) &&
            boolean(anim, STR("HoldingBlock"), false) && boolean(current.pipe.Get(), STR("Holding_Primary"), false);
    }

    void finish(std::string_view status, bool settled = false)
    {
        swing.pending = false;
        swing.settled = settled;
        swing.status = status;
    }

    bool apply_primary_edge(Swing& current, bool down)
    {
        auto* primary = static_cast<U::UFunction*>(current.primary.Get());
        auto* held = primary_input<U::FBoolProperty>(primary, STR("Held"), offsetof(Primary, held));
        if (!held || !owner_current(current, down)) return false;
        Primary parameters{};
        held->SetPropertyValueInContainer(&parameters, down);
        current.pipe.Get()->ProcessEvent(primary, &parameters);
        return owner_current(current, down) && boolean(current.pipe.Get(), STR("Holding_Primary"), down) &&
            boolean(current.animation.Get(), STR("PrimaryInput"), down);
    }

    bool harvest_input_contact()
    {
        if (!swing.contacts) return true;
        auto& damage = swing.damage;
        if (swing.contacts != 1 || swing.qualified != 1 || damage.contact_frame || damage.sweep_frame || damage.deal_frame ||
            (!damage.fenced_miss && (damage.sweeps != 1 || damage.calls != 1 ||
                                    (damage.total <= 0 && !damage.blocked_by_obstacle)))) return false;
        ++swing.input.contacts;
        if (damage.total > 0) ++swing.input.hits;
        if (swing.input.charged_attack) ++swing.input.charged;
        if (swing.input.combo_attack) ++swing.input.combo;
        swing.input.charged_attack = swing.input.combo_attack = false;
        swing.contacts = swing.qualified = 0;
        Swing::Damage next;
        next.requested = damage.requested;
        next.sweep = damage.sweep;
        next.library = damage.library;
        next.graph = damage.graph;
        damage = std::move(next);
        return true;
    }

    void cancel_input()
    {
        const bool verified = harvest_input_contact();
        finish(verified ? "cancelled" : "mutation_unverified_guest_melee_cancel_contact");
        if (!swing.input.held) return;
        swing.input.held = false;
        ++swing.releases;
        // Dead, blocked and loading guests may still own the equipped receiver.
        // Retire that exact stock input with Contact already fenced.
        if (owner_current(swing, false) && !apply_primary_edge(swing, false))
            finish("mutation_unverified_guest_melee_cancel_release");
    }

    bool passives_unchanged(const Swing& current)
    {
        Passives passives;
        std::vector<U::FName> cache;
        auto* weapon = current.pipe.Get();
        return weapon && passives.canonical(current.game.Get(), cache) && cache == current.cache &&
            passives.character(current.save.Get()) &&
            selected_melee_item(current.save.Get(), weapon->GetClassPrivate(), current.guid, passives);
    }

    void tick()
    {
        if (swing.fixture && swing.fixture->restore_frame)
        {
            const bool restored = restore_fixture_camera(*swing.fixture, swing.pawn.Get());
            return finish(restored ? "mutation_unverified_guest_melee_fixture_contact_post" :
                "mutation_unverified_guest_melee_fixture_camera_restore");
        }
        if (!swing.pending) return;
        if (swing.input.active)
        {
            if (!owner_current(swing) || Clock::now() >= swing.deadline) return cancel_input();
            if (!harvest_input_contact()) return finish("mutation_unverified_guest_melee_contact_readback");
            if (!swing.input.held && idle(swing))
            {
                swing.passive_free = passives_unchanged(swing);
                finish(swing.passive_free ? "settled" : "mutation_unverified_guest_melee_passive_readback", swing.passive_free);
            }
            return;
        }
        const bool ordinary = swing.mode == SwingMode::ordinary;
        const bool cancellable = ordinary && !swing.damage.calls && !swing.damage.sweeps && swing.damage.target_name == "-";
        if (!owner_current(swing)) return finish(cancellable ? "stock_swing_cancelled" : "mutation_unverified_guest_melee_owner");
        if (Clock::now() >= swing.deadline) return finish(cancellable ? "stock_swing_cancelled" : "mutation_unverified_guest_melee_timeout");
        if (swing.contacts != 1 || swing.qualified != 1 || !idle(swing)) return;
        swing.passive_free = passives_unchanged(swing);
        if (!swing.passive_free) return finish("mutation_unverified_guest_melee_passive_readback");
        if (!ordinary && (health(swing.canonical.Get()) != swing.host_health || health(swing.save.Get()) != swing.guest_health))
            return finish("mutation_unverified_guest_melee_health_readback");
        if (ordinary && swing.damage.blocked_by_obstacle)
        {
            // The ordinary receipt still includes its damage fields for a stock no-op.
            swing.damage = {};
            swing.damage.requested = true;
            swing.damage.contact_status = "obstacle_damage_blocked";
            return finish("stock_miss_verified", true);
        }
        if (ordinary && swing.damage.fenced_miss && !swing.damage.sweeps && !swing.damage.calls)
            return finish("stock_miss_verified", true);
        if (swing.damage.requested && (swing.damage.sweeps != 1 || swing.damage.calls != 1 || swing.damage.total <= 0))
            return finish("mutation_unverified_guest_melee_stock_damage_missing");
        finish(swing.damage.requested ? "stock_damage_verified" : "surface_verified", true);
    }

    void fence_contact(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!U::IsInGameThreadRaw() || !receiver || receiver != swing.pipe.Get() || frame.Node() != swing.contact.Get()) return;
        // Retain the weak receiver fence after every terminal outcome. An animation
        // can notify after loss of ownership, timeout, or an explicit abort.
        const bool already_vetoed = call.OriginalFunctionCallPrevented();
        if (swing.pending && swing.input.active && !harvest_input_contact())
        {
            call.PreventOriginalFunctionCall();
            return finish("mutation_unverified_guest_melee_contact_readback");
        }
        ++swing.contacts;
        const auto refuse = [&](std::string_view why) {
            if (!call.OriginalFunctionCallPrevented()) call.PreventOriginalFunctionCall();
            finish(why);
        };
        auto* graph = frame.PreviousFrame();
        auto* notify = graph ? graph->PreviousFrame() : nullptr;
        if (!swing.pending)
        {
            // A terminal receipt stays stable if a later stock notification is fenced.
            if (!already_vetoed) call.PreventOriginalFunctionCall();
            --swing.contacts;
            return;
        }
        if (already_vetoed || Clock::now() >= swing.deadline || swing.contacts != 1 ||
            (swing.input.active && swing.input.contacts >= swing.presses))
            return refuse("mutation_unverified_guest_melee_unexpected_contact");
        if (frame.Object() != receiver || !graph || graph->Node() != swing.graph.Get() ||
            graph->Object() != swing.animation.Get() || !notify || notify->Node() != swing.hit.Get() ||
            notify->Object() != swing.animation.Get())
            return refuse("mutation_unverified_guest_melee_contact_path");
        if (!owner_current(swing)) return refuse("mutation_unverified_guest_melee_contact_owner");
        if (!frame.Locals() || !stock_attack(swing, frame.Locals()))
            return refuse("mutation_unverified_guest_melee_contact_attack");
        if (!swing.damage.requested)
        {
            call.PreventOriginalFunctionCall();
            ++swing.qualified;
            return;
        }
        const bool ordinary = swing.mode == SwingMode::ordinary;
        auto* world = static_cast<U::UWorld*>(swing.world.Get());
        auto* enemy = swing.damage.target.Get();
        if (!passives_unchanged(swing) || (!ordinary && (!qualified_enemy(enemy, world) ||
                !nonlethal_health(enemy_health(enemy)) ||
                health(swing.canonical.Get()) != swing.host_health || health(swing.save.Get()) != swing.guest_health)))
            return refuse("mutation_unverified_guest_melee_damage_scope");
        TraceDiagnostic diagnostic;
        auto trace = stock_trace(receiver, swing.pawn.Get(), ordinary ? nullptr : enemy, &swing.damage.contact_status,
            swing.fixture ? &diagnostic : nullptr, swing.range, swing.radius);
        if (ordinary && trace) enemy = trace->first->GetOuterPrivate();
        if (trace) swing.damage.contact_candidate = enemy;
        bool allowed = trace && (!ordinary || combat_target(enemy, world, true, &swing.damage.contact_status));
        if (swing.fixture)
        {
            auto& staged = *swing.fixture;
            const auto eligible = [world](U::UObject* actor) {
                return qualified_enemy(actor, world) && nonlethal_health(enemy_health(actor));
            };
            auto* expected = staged.target.Get();
            const bool registered = trace && qualified_enemy(enemy, world);
            const auto hp = enemy_health(expected);
            const auto selected_hp = registered ? enemy_health(enemy) : -1;
            staged.contact_health = std::isfinite(hp) ? hp : -1;
            staged.selected_health = std::isfinite(selected_hp) ? selected_hp : -1;
            staged.stage = !trace ? diagnostic.reason : allowed ? "selected_contact" :
                registered ? "target_health" : "target_identity";
            auto* component = trace ? trace->first : diagnostic.component.Get();
            if (live(component)) staged.component = RC::to_utf8_string(component->GetFullName());
            if (diagnostic.origin) staged.eye_drift = displacement(staged.eye, *diagnostic.origin);
            if (diagnostic.direction) staged.direction_dot = direction_dot(staged.direction, *diagnostic.direction);
            if (const auto origin = enemy_origin(expected)) staged.target_drift = displacement(staged.target_origin, *origin);
            allowed = allowed && enemy == expected && eligible(expected);
            // The idle headless client can overwrite look rotation during the stock montage. Only replay a verified
            // fixture camera angle if the two real actors have barely moved, then run the same stock sweep qualification.
            if (!allowed && diagnostic.direction && staged.direction_dot < 0.99 && staged.eye_drift >= 0 &&
                staged.eye_drift <= 18 && staged.target_drift >= 0 && staged.target_drift <= 22 && eligible(expected))
            {
                auto* pawn = swing.pawn.Get();
                auto* camera = staged.camera.Get();
                if (live(camera) && camera->GetOuterPrivate() == pawn && object(pawn, STR("Camera")) == camera)
                {
                    const auto saved = relative_rotation(camera);
                    if (saved && set_camera_rotation(camera, staged.qualified_rotation, true))
                    {
                        staged.saved_relative = *saved;
                        staged.restore_frame = &frame;
                        trace = stock_trace(receiver, pawn, expected, nullptr, nullptr, swing.range, swing.radius);
                        allowed = trace && direction_dot(staged.direction, trace->direction) >= 0.9999 &&
                            displacement(staged.eye, trace->origin) <= 18 && eligible(expected);
                        if (allowed)
                        {
                            enemy = expected;
                            staged.stage = "selected_contact";
                            staged.selected_health = enemy_health(expected);
                            staged.component = RC::to_utf8_string(trace->first->GetFullName());
                        }
                        else if (!restore_fixture_camera(staged, pawn))
                            return refuse("mutation_unverified_guest_melee_fixture_camera_restore");
                    }
                }
            }
        }
        if (!allowed)
        {
            if (!ordinary) return refuse("stock_damage_aim_fenced");
            call.PreventOriginalFunctionCall();
            ++swing.qualified;
            swing.damage.fenced_miss = true;
            return;
        }
        if (ordinary)
        {
            const bool destructible = placed_destructible(enemy, world);
            auto* deal = combat_damage(enemy, destructible);
            if (!deal)
            {
                swing.damage.contact_status = "damage_function_unqualified";
                if (swing.fixture && !restore_fixture_camera(*swing.fixture, swing.pawn.Get()))
                    return refuse("mutation_unverified_guest_melee_fixture_camera_restore");
                call.PreventOriginalFunctionCall();
                ++swing.qualified;
                swing.damage.fenced_miss = true;
                return;
            }
            swing.damage.target = enemy;
            swing.damage.deal = deal;
            swing.damage.destructible = destructible;
            swing.damage.target_name = RC::to_utf8_string(enemy->GetFullName());
        }
        swing.damage.contact_frame = &frame;
        swing.damage.component = trace->first;
        swing.damage.origin = trace->origin;
        swing.damage.direction = trace->direction;
        ++swing.qualified;
    }

    void observe_pre(Iteration& call, U::UObject* receiver, U::FFrame& frame, void* result)
    {
        if (!U::IsInGameThreadRaw() || !frame.Node()) return;
        if (receiver && receiver == swing.pipe.Get() && frame.Node() == swing.contact.Get())
            return fence_contact(call, receiver, frame, result);
        static const U::FName contact_name{STR("Contact")};
        if (receiver && frame.Node()->GetFName() == contact_name && std::ranges::any_of(fenced_pipes, [&](const PipeFence& guard) {
                return guard.pipe.Get() == receiver && guard.contact.Get() == frame.Node();
            }))
        {
            if (!call.OriginalFunctionCallPrevented()) call.PreventOriginalFunctionCall();
            return;
        }
        auto& proof = swing.damage;
        if (!proof.contact_frame) return;
        const auto refuse = [&](std::string_view status) {
            if (!call.OriginalFunctionCallPrevented()) call.PreventOriginalFunctionCall();
            finish(status);
        };
        if (frame.Node() == proof.sweep.Get())
        {
            auto* graph = frame.PreviousFrame();
            auto* fn = frame.Node();
            auto* attack = parameter<U::FStructProperty>(fn, STR("Attack"));
            auto* contact_attack = parameter<U::FStructProperty>(static_cast<U::UFunction*>(swing.contact.Get()), STR("Attack"));
            auto* args = frame.Locals();
            if (call.OriginalFunctionCallPrevented() || !swing.pending || proof.sweeps || receiver != proof.library.Get() ||
                !graph || graph->Node() != proof.graph.Get() || graph->Object() != swing.pipe.Get() ||
                graph->PreviousFrame() != proof.contact_frame || !args || !attack || !contact_attack ||
                attack->GetStruct().Get() != contact_attack->GetStruct().Get() ||
                !attack->Identical(attack->ContainerPtrToValuePtr<void>(args),
                    contact_attack->ContainerPtrToValuePtr<void>(proof.contact_frame->Locals())) ||
                read_object(parameter<U::FObjectProperty>(fn, STR("Instigator")), args) != swing.pawn.Get() ||
                read_object(parameter<U::FObjectProperty>(fn, STR("__WorldContext")), args) != swing.pipe.Get() ||
                read_vector(parameter<U::FStructProperty>(fn, STR("Origin")), args) != std::optional{proof.origin} ||
                read_vector(parameter<U::FStructProperty>(fn, STR("Direction")), args) != std::optional{proof.direction})
                return refuse("mutation_unverified_guest_melee_stock_sweep_path");
            proof.sweep_frame = &frame;
            ++proof.sweeps;
            return;
        }
        static const U::FName deal_name{STR("DealDamage")};
        if (!proof.sweep_frame || frame.Node()->GetFName() != deal_name) return;
        auto* ancestor = frame.PreviousFrame();
        for (int remaining = 8; ancestor && ancestor != proof.sweep_frame && remaining > 0; --remaining)
            ancestor = ancestor->PreviousFrame();
        if (ancestor != proof.sweep_frame) return;
        const bool ordinary = swing.mode == SwingMode::ordinary;
        if (ordinary && proof.deal_frame && receiver == proof.target.Get() &&
            frame.PreviousFrame() != proof.sweep_frame) return;
        if (ordinary && (proof.calls || receiver != proof.target.Get()))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        auto* fn = frame.Node();
        auto* args = frame.Locals();
        const SweepHit hit;
        auto* hit_value = parameter<U::FStructProperty>(fn, STR("Hit"));
        auto* damage = parameter<U::FStructProperty>(fn, STR("Damage"));
        auto* damage_type = find<U::UScriptStruct>(damage_type_path);
        auto* blunt = field<U::FDoubleProperty>(damage_type, blunt_name);
        if (call.OriginalFunctionCallPrevented() || !swing.pending || proof.calls ||
            frame.PreviousFrame() != proof.sweep_frame || receiver != proof.target.Get() ||
            !combat_target(receiver, static_cast<U::UWorld*>(swing.world.Get()), ordinary) ||
            fn != proof.deal.Get() || !args ||
            !hit.bound() || !hit_value || hit_value->GetStruct().Get() != hit.type ||
            hit.owned_component(hit_value->ContainerPtrToValuePtr<void>(args), receiver, swing.range) != proof.component.Get() ||
            !damage || !damage_type || damage->GetStruct().Get() != damage_type || !blunt ||
            (!ordinary && blunt->GetPropertyValueInContainer(damage->ContainerPtrToValuePtr<void>(args)) != 30.0) ||
            read_object(parameter<U::FObjectProperty>(fn, STR("Instigator")), args) != swing.pawn.Get())
            return refuse("mutation_unverified_guest_melee_stock_damage_path");
        proof.before = enemy_health(receiver);
        if (!std::isfinite(proof.before) || proof.before <= 0.0 || (!ordinary && !nonlethal_health(proof.before)))
            return refuse("mutation_unverified_guest_melee_stock_damage_health");
        proof.deal_frame = &frame;
        ++proof.calls;
    }

    void observe_post(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!U::IsInGameThreadRaw()) return;
        auto& proof = swing.damage;
        if (swing.fixture && swing.fixture->restore_frame == &frame &&
            !restore_fixture_camera(*swing.fixture, swing.pawn.Get()))
        {
            proof.contact_frame = nullptr;
            return finish("mutation_unverified_guest_melee_fixture_camera_restore");
        }
        if (proof.deal_frame == &frame)
        {
            proof.deal_frame = nullptr;
            auto* total = parameter<U::FDoubleProperty>(frame.Node(), STR("TotalDamage"));
            auto* lethal = parameter<U::FBoolProperty>(frame.Node(), STR("IsLethal"));
            auto* amount = total ? output(frame, total) : nullptr;
            auto* dead = lethal ? output(frame, lethal) : nullptr;
            proof.after = enemy_health(receiver);
            if (proof.destructible)
            {
                // The stock obstacle graph reports remaining Health and may destroy its actor
                // during Damage_Feedback. The current frame still owns the exact receiver.
                if (call.OriginalFunctionCallPrevented() || receiver != frame.Object() || !amount || !dead ||
                    lethal->GetPropertyValue(dead) || !std::isfinite(proof.after) || proof.after > proof.before ||
                    total->GetPropertyValue(amount) != proof.after)
                    return finish("mutation_unverified_guest_melee_stock_destructible_readback");
                proof.total = proof.before - proof.after;
                proof.blocked_by_obstacle = proof.total == 0.0;
                return;
            }
            const bool killed = dead && lethal->GetPropertyValue(dead);
            const bool ordinary = swing.mode == SwingMode::ordinary;
            if (call.OriginalFunctionCallPrevented() || !amount || !dead || (killed && !ordinary) ||
                receiver != proof.target.Get() || !boolean(receiver, STR("Alive"), !killed) ||
                object(receiver, STR("LastDamagedMe")) != swing.pawn.Get() || !std::isfinite(proof.after) ||
                killed != (proof.after <= 0.0) || proof.after >= proof.before)
                return finish("mutation_unverified_guest_melee_stock_damage_readback");
            proof.total = total->GetPropertyValue(amount);
            if (!std::isfinite(proof.total) || proof.total <= 0.0 || (!ordinary && proof.total > 90.0) ||
                (killed && proof.after == 0.0 ? proof.total + 1e-6 < proof.before :
                                                std::abs((proof.before - proof.after) - proof.total) > 1e-6))
                return finish("mutation_unverified_guest_melee_stock_damage_amount");
        }
        if (proof.sweep_frame == &frame) proof.sweep_frame = nullptr;
        if (proof.contact_frame == &frame)
        {
            proof.contact_frame = nullptr;
            if (swing.pending && (proof.sweeps != 1 || proof.calls != 1 ||
                (proof.total <= 0.0 && !proof.blocked_by_obstacle)))
                finish("mutation_unverified_guest_melee_stock_damage_missing");
        }
    }

    bool hooks()
    {
        const auto ready = [] { return std::ranges::none_of(callbacks, [](auto item) { return item == H::ERROR_ID; }); };
        if (ready()) return true;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(
            observe_pre, {false, false, owner, STR("Qualify one exact Old Pipe animation contact")});
        callbacks[1] = H::RegisterEngineTickPreCallback(
            [](Iteration&, U::UEngine*, float, bool) { if (U::IsInGameThreadRaw()) tick(); },
            {false, false, owner, STR("Bound the isolated guest Old Pipe animation receipt")});
        callbacks[2] = H::RegisterProcessLocalScriptFunctionPostCallback(
            observe_post, {false, false, owner, STR("Read back the qualified stock melee damage")});
        if (ready()) return true;
        silverfish::native_guest_melee::uninstall_hooks();
        return false;
    }

    int proof(const Lua& lua, bool ok, std::string_view status, const Swing* current = nullptr)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        lua.set_integer(current ? current->serial : 0);
        lua.set_integer(current ? current->presses : 0);
        lua.set_integer(current ? current->releases : 0);
        lua.set_integer(current ? current->contacts : 0);
        lua.set_integer(current ? current->qualified : 0);
        lua.set_bool(current && current->settled);
        lua.set_bool(current && current->passive_free);
        if (current && current->damage.requested)
        {
            const auto& hit = current->damage;
            lua.set_integer(hit.sweeps);
            lua.set_integer(hit.calls);
            lua.set_number(hit.before);
            lua.set_number(hit.after);
            lua.set_number(hit.total);
            lua.set_string(hit.target_name);
            return 15;
        }
        return 9;
    }

    int report(const Lua& lua)
    {
        return proof(lua, !swing.status.starts_with("mutation_unverified_"), swing.status, &swing);
    }

    int input_report(const Lua& lua)
    {
        lua.set_bool(!swing.status.starts_with("mutation_unverified_"));
        lua.set_string(swing.status);
        lua.set_integer(swing.serial);
        lua.set_integer(swing.presses);
        lua.set_integer(swing.releases);
        lua.set_integer(swing.input.contacts);
        lua.set_integer(swing.input.hits);
        lua.set_integer(swing.input.charged);
        lua.set_integer(swing.input.combo);
        return 9;
    }

    U::UObject* pawn_at(std::int64_t address)
    {
        U::UObject* pawn{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object, int, int) {
            if (reinterpret_cast<std::uintptr_t>(object) != static_cast<std::uintptr_t>(address))
                return RC::LoopAction::Continue;
            if (live(object) && object->GetClassPrivate() ==
                find<U::UClass>(STR("/Game/Classes/Player/Player_FP.Player_FP_C"))) pawn = object;
            return RC::LoopAction::Break;
        });
        return pawn;
    }

    int damage_pose(const Lua& lua)
    {
        bool clear = false;
        std::string_view status = "arguments";
        double observed_health{-1};
        const auto argc = lua.get_stack_size();
        if (fixture_enabled() && U::IsInGameThreadRaw() && (argc == 1 || (argc == 2 && lua.is_integer(2))) && lua.is_integer(1))
        {
            auto* pawn = pawn_at(lua.get_integer());
            auto* save = object(pawn, STR("Save"));
            auto* canonical = object(object(pawn, STR("As Game Instance FP")), STR("SaveSlot_Ref"));
            auto* pipe = object(pawn, STR("EquipedItem"));
            auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
            auto* target = argc == 1 ? placed_enemy(world) : fixture_target(world, lua.get_integer());
            const auto hp = enemy_health(target);
            if (std::isfinite(hp)) observed_health = hp;
            if (!target) status = "placed_target";
            else if (!pawn || static_cast<U::AActor*>(pawn)->GetWorld() != world ||
                !silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, canonical))
                status = "detached_owner";
            else if (!live(pipe) || pipe->GetClassPrivate() != find<U::UClass>(pipe_path) ||
                object(pipe, STR("Player_Ref")) != pawn || object(pipe, STR("Owner")) != pawn)
                status = "equipped_pipe";
            else if (!nonlethal_health(observed_health)) status = "target_health";
            else clear = stock_trace(pipe, pawn, target, &status).has_value();
        }
        lua.set_bool(clear);
        lua.set_string(status);
        lua.set_number(observed_health);
        return 3;
    }

    template <SwingMode mode, bool input = false> int start(const Lua& lua)
    {
        constexpr bool damage = mode != SwingMode::surface_fixture;
        constexpr bool ordinary = mode == SwingMode::ordinary;
        const auto argc = lua.get_stack_size();
        const bool fixture = ordinary && !input && argc == 4 && fixture_enabled() && lua.is_integer(4);
        if (!(ordinary ? enabled() : fixture_enabled()) || !U::IsInGameThreadRaw() || (argc != 3 && !fixture) ||
            !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_string(3)) return proof(lua, false, "native_guest_melee_arguments_unqualified");
        if (swing.pending || (swing.input.active && swing.status.starts_with("mutation_unverified_")) ||
            (!ordinary && swing.serial && swing.pipe.Get()))
            return proof(lua, false, "native_guest_melee_fixture_already_bound");
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        const auto guid = lua.get_string();
        const auto target_address = fixture ? lua.get_integer() : 0;
        Swing candidate;
        candidate.mode = mode;
        candidate.input.active = input;
        if (pawn_address <= 0 || save_address <= 0 || !guid_from(guid, candidate.guid))
            return proof(lua, false, "native_guest_melee_arguments_unqualified");
        auto* pawn = pawn_at(pawn_address);
        auto* save = object(pawn, STR("Save"));
        auto* game = object(pawn, STR("As Game Instance FP"));
        auto* canonical = object(game, STR("SaveSlot_Ref"));
        auto* world = ordinary && pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : find<U::UWorld>(damage ? damage_map : map_path);
        if (!pawn || reinterpret_cast<std::uintptr_t>(save) != static_cast<std::uintptr_t>(save_address) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, canonical) || !live(world) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || (ordinary && !silverfish::native_guest_melee::ordinary_world(world)))
            return proof(lua, false, "native_guest_melee_exact_owner_unqualified");
        auto* pipe = object(pawn, STR("EquipedItem"));
        auto* mesh = object(pipe, STR("Wep_Skeli"));
        auto* anim = object(pipe, STR("As Melee Wep Anim BP"));
        auto* old_pipe = find<U::UClass>(pipe_path);
        auto* animator = find<U::UClass>(animator_path);
        auto* master = find<U::UClass>(master_path);
        auto* equipment = find<U::UClass>(equipment_path);
        if (!live(pipe) || (ordinary ? !silverfish::native_guest_melee::ordinary_weapon(pipe) :
             !old_pipe || pipe->GetClassPrivate() != old_pipe) || !live(anim) ||
            !animator || anim->GetClassPrivate() != animator)
            return proof(lua, false, "native_guest_melee_stock_receiver_unqualified");
        if (!master || !equipment || master->GetSuperClass() != equipment)
            return proof(lua, false, "native_guest_melee_stock_inheritance_unqualified");
        // The stock melee primary calls Equipment_Master_BP. InputIntercept is
        // declared on the separate firearm base and is absent from this chain.
        if (pipe->GetPropertyByNameInChain(STR("InputIntercept")))
            return proof(lua, false, "native_guest_melee_stock_input_intercept_unexpected");
        auto* primary = local_function(pipe, master, STR("Use_Item_Primary"), 2);
        if (!primary) return proof(lua, false, "native_guest_melee_stock_primary_unqualified");
        auto* held = primary_input<U::FBoolProperty>(primary, STR("Held"), offsetof(Primary, held));
        if (primary->GetParmsSize() != sizeof(Primary) || !held ||
            !primary_input<U::FDoubleProperty>(primary, STR("VarianceScaler"), offsetof(Primary, variance)))
            return proof(lua, false, "native_guest_melee_stock_primary_parameters_unqualified");
        auto* contact = local_function(pipe, master, STR("Contact"), 1);
        if (!contact) return proof(lua, false, "native_guest_melee_stock_contact_unqualified");
        auto* hit = local_function(anim, animator, STR("AnimNotify_Hit"), 0);
        if (!hit) return proof(lua, false, "native_guest_melee_stock_hit_notify_unqualified");
        auto* graph = local_function(anim, animator, STR("ExecuteUbergraph_MeleeWep_AnimBP"), 1);
        if (!graph) return proof(lua, false, "native_guest_melee_stock_animation_graph_unqualified");
        candidate.pawn = pawn;
        candidate.save = save;
        candidate.canonical = canonical;
        candidate.game = game;
        candidate.world = world;
        candidate.pipe = pipe;
        candidate.mesh = mesh;
        candidate.animation = anim;
        candidate.primary = primary;
        candidate.contact = contact;
        candidate.hit = hit;
        candidate.graph = graph;
        if (!owner_current(candidate) || !idle(candidate))
            return proof(lua, false, "native_guest_melee_stock_pose_unqualified");
        if constexpr (ordinary && !input)
        {
            auto* combo_step = field<U::FIntProperty>(anim, STR("ComboStep"));
            if (!combo_step || combo_step->GetPropertyValueInContainer(anim) != 0)
                return proof(lua, false, "native_guest_melee_stock_combo_busy");
        }
        if (!stock_attack(candidate)) return proof(lua, false, "native_guest_melee_stock_swing_unqualified");
        candidate.host_health = health(canonical);
        candidate.guest_health = health(save);
        if (!std::isfinite(candidate.host_health) || candidate.host_health <= 1.0 ||
            !std::isfinite(candidate.guest_health) || candidate.guest_health <= 1.0)
            return proof(lua, false, "native_guest_melee_stock_health_unqualified");
        Passives passives;
        candidate.passive_free = passives.canonical(game, candidate.cache) && passives.character(save) &&
            selected_melee_item(save, pipe->GetClassPrivate(), candidate.guid, passives);
        if (!candidate.passive_free) return proof(lua, false, "native_guest_melee_guid_or_passives_unqualified");
        if constexpr (damage)
        {
            auto* enemy = ordinary ? nullptr : placed_enemy(world);
            auto* sweep = find<U::UFunction>(effects_sweep);
            auto* library = find<U::UObject>(effects_default);
            auto* deal = local_function(enemy, find<U::UClass>(npc_class), STR("DealDamage"), 7);
            auto* contact_graph = local_function(pipe, master, STR("ExecuteUbergraph_MeleeWep_Master_BP"), 1);
            const auto hp = enemy_health(enemy);
            if ((!ordinary && (!enemy || !nonlethal_health(hp) || !deal || !stock_trace(pipe, pawn, enemy))) ||
                (enemy && !deal) || !contact_graph || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) ||
                library->GetClassPrivate() != find<U::UClass>(effects_class) || !sweep ||
                sweep->GetOuterPrivate() != library->GetClassPrivate() || sweep->GetNumParms() != 7 ||
                sweep->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || sweep->GetReturnProperty())
                return proof(lua, false, "native_guest_melee_stock_damage_scope_unqualified");
            candidate.damage.requested = true;
            candidate.damage.target = enemy;
            candidate.damage.sweep = sweep;
            candidate.damage.deal = deal;
            if (enemy) candidate.damage.target_name = RC::to_utf8_string(enemy->GetFullName());
            candidate.damage.library = library;
            candidate.damage.graph = contact_graph;
        }
        if (fixture)
        {
            auto* target = fixture_target(world, target_address);
            const auto hp = enemy_health(target);
            const auto origin = enemy_origin(target);
            const auto lane = nonlethal_health(hp)
                ? stock_trace(pipe, pawn, target, nullptr, nullptr, candidate.range, candidate.radius) : std::nullopt;
            auto* camera = object(pawn, STR("Camera"));
            const auto facing = lane ? component_vector(camera, STR("/Script/Engine.SceneComponent:K2_GetComponentRotation"),
                rotator_path) : std::nullopt;
            if (!lane || !origin || !facing) return proof(lua, false, "native_guest_melee_stock_fixture_contact_baseline_unqualified");
            auto& staged = candidate.fixture.emplace();
            staged.target = target;
            staged.camera = camera;
            staged.name = RC::to_utf8_string(target->GetFullName());
            staged.eye = lane->origin;
            staged.direction = lane->direction;
            staged.target_origin = *origin;
            staged.qualified_rotation = *facing;
            staged.initial_health = hp;
        }
        std::erase_if(fenced_pipes, [](const PipeFence& fence) { return !fence.pipe.Get() || !fence.contact.Get(); });
        const bool new_pipe = std::ranges::none_of(fenced_pipes, [&](const PipeFence& fence) { return fence.pipe.Get() == pipe; });
        if (new_pipe && fenced_pipes.size() == max_fenced_pipes)
            return proof(lua, false, "native_guest_melee_late_contact_budget_unavailable");
        if (!hooks() || serial >= (std::int64_t{1} << 53) - 1)
            return proof(lua, false, "native_guest_melee_hook_unavailable");
        if (new_pipe) fenced_pipes.push_back({candidate.pipe, candidate.contact});

        candidate.serial = ++serial;
        candidate.deadline = Clock::now() + (input ? hold_timeout : attack_timeout);
        candidate.status = "pending";
        candidate.pending = true;
        swing = std::move(candidate);
        if constexpr (input)
        {
            swing.input.held = true;
            ++swing.presses;
            if (!apply_primary_edge(swing, true)) finish("mutation_unverified_guest_melee_press_readback");
            return input_report(lua);
        }
        Primary press{};
        held->SetPropertyValueInContainer(&press, true);
        ++swing.presses;
        pipe->ProcessEvent(primary, &press);
        if (!owner_current(swing)) finish("mutation_unverified_guest_melee_press_owner");
        else
        {
            // The stock animation sets IsMidSwing from its later swing-start notify.
            const auto pressed = boolean(pipe, STR("Holding_Primary"), true) &&
                boolean(anim, STR("PrimaryInput"), true);
            Primary release{};
            held->SetPropertyValueInContainer(&release, false);
            ++swing.releases;
            pipe->ProcessEvent(primary, &release);
            if (!pressed) finish("mutation_unverified_guest_melee_press_readback");
            else if (!owner_current(swing) || !boolean(pipe, STR("Holding_Primary"), false) ||
                     !boolean(anim, STR("PrimaryInput"), false))
                finish("mutation_unverified_guest_melee_release_readback");
        }
        return report(lua);
    }

    int input_edge(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 2 ||
            !lua.is_integer(1) || !lua.is_string(2) || !swing.input.active || swing.serial != lua.get_integer())
            return proof(lua, false, "native_guest_melee_exact_serial_required");
        const auto edge = lua.get_string();
        if (edge != "press" && edge != "release" && edge != "cancel")
            return proof(lua, false, "native_guest_melee_exact_edge_required");
        if (!swing.pending || swing.input.held == (edge == "press"))
            return proof(lua, false, "native_guest_melee_stock_edge_inactive");
        if (edge == "cancel" || (edge == "release" && !owner_current(swing)))
        {
            cancel_input();
            return input_report(lua);
        }
        const bool down = edge == "press";
        if (down && !owner_current(swing)) return proof(lua, false, "native_guest_melee_exact_owner_unqualified");
        swing.input.held = down;
        if (down) ++swing.presses;
        else ++swing.releases;
        swing.deadline = Clock::now() + (down ? hold_timeout : attack_timeout);
        if (!apply_primary_edge(swing, down)) finish(down ? "mutation_unverified_guest_melee_press_readback" :
            "mutation_unverified_guest_melee_release_readback");
        return input_report(lua);
    }

    int read_input(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !swing.input.active || swing.serial != lua.get_integer()) return 0;
        return input_report(lua);
    }

    template <bool abort> int observe(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !swing.serial || swing.serial != lua.get_integer())
            return proof(lua, false, "native_guest_melee_serial_unqualified");
        if constexpr (abort)
        {
            if (swing.pending)
            {
                if (swing.mode == SwingMode::ordinary)
                    finish(swing.damage.calls || swing.damage.sweeps || swing.damage.target_name != "-" ?
                        "mutation_unverified_guest_melee_abort_after_contact" : "stock_swing_cancelled");
                else finish("aborted_contact_fenced");
            }
        }
        else tick();
        return report(lua);
    }

    int observe_fixture(const Lua& lua)
    {
        if (!fixture_enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !swing.fixture || swing.serial != lua.get_integer()) return 0;
        const auto& staged = *swing.fixture;
        lua.set_string(staged.name);
        lua.set_string(staged.stage);
        lua.set_string(staged.component);
        lua.set_number(staged.initial_health);
        lua.set_number(staged.contact_health);
        lua.set_number(staged.selected_health);
        lua.set_number(staged.eye_drift);
        lua.set_number(staged.direction_dot);
        lua.set_number(staged.target_drift);
        lua.set_string(staged.pose);
        return 10;
    }
}

bool silverfish::native_guest_melee::apply_stock_control_rotation(RC::Unreal::UObject* camera,
                                                                 RC::Unreal::UObject* controller,
                                                                 std::array<double, 3>& previous)
{
    if (!live(camera) || !live(controller)) return false;
    const auto relative = relative_rotation(camera);
    const auto control = component_vector(controller, STR("/Script/Engine.Controller:GetControlRotation"), rotator_path);
    if (!relative || !control || !set_camera_rotation(camera, *control, true)) return false;
    previous = *relative;
    return true;
}

bool silverfish::native_guest_melee::restore_stock_camera_rotation(RC::Unreal::UObject* camera,
                                                                   const std::array<double, 3>& previous)
{
    return live(camera) && set_camera_rotation(camera, previous, false);
}

bool silverfish::native_guest_melee::ordinary_world(RC::Unreal::UWorld* world)
{
    return live(world) && world->GetClassPrivate() == find<U::UClass>(STR("/Script/Engine.World")) &&
        world->GetPathName().starts_with(STR("/Game/Classes/Maps/"));
}

bool silverfish::native_guest_melee::ordinary_weapon(RC::Unreal::UObject* weapon)
{
    if (!live(weapon)) return false;
    auto* master = find<U::UClass>(master_path);
    auto* type = weapon->GetClassPrivate();
    if (!master || !type || type == master || !type->IsChildOf(master)) return false;
    const auto name = type->GetPathName();
    return name.starts_with(STR("/Game/Classes/Weapons/MeleeWeapons/")) &&
        name != STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C") &&
        local_function(weapon, master, STR("Use_Item_Primary"), 2) &&
        local_function(weapon, master, STR("Contact"), 1);
}

void silverfish::native_guest_melee::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishStartNativeGuestMeleeSwing", start<SwingMode::ordinary>);
    lua.register_function("SilverfishStartNativeGuestMeleeInput", start<SwingMode::ordinary, true>);
    lua.register_function("SilverfishApplyNativeGuestMeleeInputEdge", input_edge);
    lua.register_function("SilverfishReadNativeGuestMeleeInput", read_input);
    if (env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_TITLE_FIXTURES"))
        lua.register_function("SilverfishReadNativeGuestMeleeContactStatus", [](const Lua& state) {
            auto cause = std::string{swing.damage.contact_status};
            auto* candidate = swing.damage.contact_candidate.Get();
            if (live(candidate)) cause += " " + RC::to_utf8_string(candidate->GetFullName());
            state.set_string(cause);
            return 1;
        });
    if (fixture_enabled())
    {
        lua.register_function("SilverfishStartNativeGuestMeleeCanary", start<SwingMode::surface_fixture>);
        lua.register_function("SilverfishStartNativeGuestMeleeDamageCanary", start<SwingMode::damage_fixture>);
        lua.register_function("SilverfishCheckNativeGuestMeleeDamagePose", damage_pose);
        lua.register_function("SilverfishReadNativeGuestMeleeContactFixture", observe_fixture);
    }
    lua.register_function("SilverfishReadNativeGuestMeleeCanary", observe<false>);
    lua.register_function("SilverfishAbortNativeGuestMeleeCanary", observe<true>);
}

void silverfish::native_guest_melee::uninstall_hooks()
{
    for (auto& callback : callbacks)
    {
        if (callback != H::ERROR_ID) H::UnregisterCallback(callback);
        callback = H::ERROR_ID;
    }
    swing = {};
    fenced_pipes.clear();
}
