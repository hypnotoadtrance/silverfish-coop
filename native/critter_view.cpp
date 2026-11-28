#include "critter_view.hpp"
#include "interaction_input.hpp"
#include "zone_exit.hpp"

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::intptr_t;

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::FObjectProperty* property, U::UObject* owner)
    {
        return property && owner ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    struct StockLayout
    {
        static constexpr std::size_t setter_capacity = 32;
        U::FWeakObjectPtr critter{}, marker{}, movement{}, mesh{}, asset{}, falling{}, set_mesh{};
        U::FObjectProperty *owner{}, *persistent{}, *current_mesh{}, *new_mesh{};
        U::FByteProperty* role{};
        U::FBoolProperty *destroyed{}, *falling_result{};

        bool bind(U::UWorld* world)
        {
            auto* base = find<U::UClass>(STR("/Game/Classes/Ambiant_FX/Critter_Master_BP.Critter_Master_BP_C"));
            auto* actor = find<U::UClass>(STR("/Script/Engine.Actor"));
            auto* target = find<U::UClass>(STR("/Script/Engine.TargetPoint"));
            auto* mover = find<U::UClass>(STR("/Script/Engine.CharacterMovementComponent"));
            auto* component = find<U::UClass>(STR("/Script/Engine.StaticMeshComponent"));
            auto* mesh_asset = find<U::UClass>(STR("/Script/Engine.StaticMesh"));
            if (!base || !actor || !target || !mover || !component || !mesh_asset) return false;
            auto* query = mover->GetFunctionByNameInChain(U::FName{STR("IsFalling"), U::FNAME_Find});
            auto* setter = find<U::UFunction>(STR("/Script/Engine.StaticMeshComponent:SetStaticMesh"));
            falling_result = query ? U::CastField<U::FBoolProperty>(query->GetReturnProperty()) : nullptr;
            auto* set_result = setter ? U::CastField<U::FBoolProperty>(setter->GetReturnProperty()) : nullptr;
            new_mesh = field<U::FObjectProperty>(setter, STR("NewMesh"));
            if (!falling_result || !falling_result->HasAnyPropertyFlags(U::CPF_ReturnParm) ||
                falling_result->GetArrayDim() != 1 || falling_result->GetOffset_Internal() != 0 ||
                falling_result->GetByteOffset() != 0 || !query->HasAnyFunctionFlags(U::FUNC_Native) ||
                query->GetNumParms() != 1 || query->GetParmsSize() != 1 ||
                !new_mesh || !set_result || setter->GetOuterPrivate() != component ||
                !setter->HasAnyFunctionFlags(U::FUNC_Native) || setter->GetNumParms() != 2 ||
                setter->GetParmsSize() > setter_capacity || setter->GetParmsSize() <= sizeof(void*) ||
                !new_mesh->HasAnyPropertyFlags(U::CPF_Parm) || new_mesh->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) ||
                new_mesh->GetPropertyClass().Get() != mesh_asset || new_mesh->GetOffset_Internal() != 0 ||
                new_mesh->GetSize() != sizeof(void*) || set_result->GetOffset_Internal() != sizeof(void*) ||
                set_result->GetByteOffset() != 0) return false;
            owner = field<U::FObjectProperty>(actor, STR("Owner"));
            persistent = field<U::FObjectProperty>(world, STR("PersistentLevel"));
            current_mesh = field<U::FObjectProperty>(component, STR("StaticMesh"));
            role = field<U::FByteProperty>(actor, STR("Role"));
            destroyed = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
            if (!owner || !persistent || !current_mesh || !role || !destroyed) return false;
            critter = base;
            marker = target;
            movement = mover;
            mesh = component;
            asset = mesh_asset;
            falling = query;
            set_mesh = setter;
            return true;
        }

        bool ready() const
        {
            return critter.Get() && marker.Get() && movement.Get() && mesh.Get() && asset.Get() &&
                falling.Get() && set_mesh.Get();
        }
    };

    struct Binding
    {
        U::FWeakObjectPtr marker{}, actor{}, level{}, type{};
        U::FObjectProperty *mesh{}, *movement{}, *idle{}, *hop{}, *owning_world{};
        U::FBoolProperty* visible{};
        U::FNumericProperty* health{};
        std::string path;
        bool streamed{};

        bool bind(U::UObject* source, U::UObject* identity, U::UObject* source_level, U::UWorld* world,
                  U::UObject* persistent, std::string_view marker_path)
        {
            auto* actor_class = source->GetClassPrivate();
            health = field<U::FNumericProperty>(actor_class, STR("Health"));
            mesh = field<U::FObjectProperty>(actor_class, STR("StaticMesh"));
            movement = field<U::FObjectProperty>(actor_class, STR("CharacterMovement"));
            idle = field<U::FObjectProperty>(actor_class, STR("Mesh_Normal"));
            hop = field<U::FObjectProperty>(actor_class, STR("Mesh_Hopping"));
            if (!health || (!health->IsFloatingPoint() && !health->IsInteger()) || !mesh || !movement || !idle || !hop)
                return false;
            streamed = source_level != persistent;
            if (streamed)
            {
                visible = field<U::FBoolProperty>(source_level, STR("bIsVisible"));
                owning_world = field<U::FObjectProperty>(source_level, STR("OwningWorld"));
                if (!visible || !owning_world || object(owning_world, source_level) != world ||
                    !silverfish::native_zone::visible_actor_level(static_cast<U::AActor*>(source), world)) return false;
            }
            else if (static_cast<U::AActor*>(source)->GetWorld() != world) return false;
            marker = identity;
            actor = source;
            level = source_level;
            type = actor_class;
            path = marker_path;
            return true;
        }
    };

    int update_mesh(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 4 || !lua_isinteger(stack, 1) || !lua_isinteger(stack, 2) ||
            lua_type(stack, 3) != LUA_TSTRING || !lua_isinteger(stack, 4)) return 0;
        auto* world = silverfish::native_interaction_input::current_listen_world();
        if (!world || address(world) != lua_tointeger(stack, 1)) return 0;
        auto* driver = object(field<U::FObjectProperty>(world, STR("NetDriver")), world);
        if (!live(driver) || !live(object(field<U::FObjectProperty>(driver, STR("ServerConnection")), driver))) return 0;
        const auto expected_actor = lua_tointeger(stack, 2);
        const auto expected_level = lua_tointeger(stack, 4);
        std::size_t path_length{};
        const auto* path_chars = lua_tolstring(stack, 3, &path_length);
        const std::string_view path{path_chars, path_length};
        if (expected_actor <= 0 || expected_level <= 0 || path.empty() || path.size() > 512 ||
            path.find('\0') != std::string_view::npos) return 0;

        static U::FWeakObjectPtr bound_world;
        static StockLayout stock;
        static std::unordered_map<Address, Binding> bindings;
        if (world != bound_world.Get())
        {
            bindings.clear();
            bound_world = world;
            stock = {};
            stock.bind(world);
        }
        if (!stock.ready()) return 0;
        auto* persistent = object(stock.persistent, world);
        if (!live(persistent)) return 0;
        auto found = bindings.find(expected_actor);
        auto* marker = found != bindings.end() && found->second.path == path ? found->second.marker.Get() : nullptr;
        if (!marker)
        {
            auto* candidate = find<U::UObject>(RC::to_wstring(path).c_str());
            if (!live(candidate) || candidate->GetClassPrivate() != stock.marker.Get() ||
                static_cast<U::AActor*>(candidate)->GetWorld() != world) return 0;
            const auto point = static_cast<U::AActor*>(candidate)->K2_GetActorLocation();
            const double offset = point.Z() - 327680.0; // replicated_actor_reference.lua
            const double id = std::round(offset);
            if (!std::isfinite(offset) || id < 1 || id > 65535 || std::abs(offset - id) >= .25) return 0;
            marker = candidate;
        }
        if (!live(marker) || marker->GetClassPrivate() != stock.marker.Get() ||
            static_cast<U::AActor*>(marker)->GetWorld() != world ||
            stock.destroyed->GetPropertyValueInContainer(marker) ||
            stock.role->GetPropertyValueInContainer(marker) != 1) return 0;
        auto* actor = object(stock.owner, marker);
        if (address(actor) != expected_actor || !live(actor) ||
            !actor->IsA(static_cast<U::UClass*>(stock.critter.Get()))) return 0;
        const auto respond = [&](bool remains, bool changed = false) {
            lua_pushboolean(stack, remains);
            lua_pushboolean(stack, changed);
            return 2;
        };
        auto* level = actor->GetOuterPrivate();
        auto role = stock.role->GetPropertyValueInContainer(actor);
        if (stock.destroyed->GetPropertyValueInContainer(actor) || role == 0) return respond(false);
        if (role != 1 || address(level) != expected_level || !live(level)) return 0;
        if (found == bindings.end() || found->second.marker.Get() != marker || found->second.actor.Get() != actor ||
            found->second.level.Get() != level || found->second.type.Get() != actor->GetClassPrivate())
        {
            Binding binding;
            if (!binding.bind(actor, marker, level, world, persistent, path)) return 0;
            if (bindings.size() >= 1024) bindings.clear();
            found = bindings.insert_or_assign(expected_actor, std::move(binding)).first;
        }
        const auto& binding = found->second;
        auto* health = binding.health->ContainerPtrToValuePtr<void>(actor);
        const bool alive = binding.health->IsFloatingPoint()
            ? binding.health->GetFloatingPointPropertyValue(health) > 0
            : binding.health->GetSignedIntPropertyValue(health) > 0;
        if (!alive) return respond(false);
        if (binding.streamed)
        {
            if (object(binding.owning_world, level) != world) return 0;
            if (!binding.visible->GetPropertyValueInContainer(level)) return respond(true);
        }
        else if (level != persistent) return 0;
        auto* component = object(binding.mesh, actor);
        auto* movement = object(binding.movement, actor);
        if (!live(component) || !live(movement) || component->GetClassPrivate() != stock.mesh.Get() ||
            movement->GetClassPrivate() != stock.movement.Get() || component->GetOuterPrivate() != actor ||
            movement->GetOuterPrivate() != actor) return 0;

        std::uint8_t falling{};
        movement->ProcessEvent(static_cast<U::UFunction*>(stock.falling.Get()), &falling);
        auto* desired = object(stock.falling_result->GetPropertyValueInContainer(&falling) ? binding.hop : binding.idle, actor);
        if (!live(desired) || !desired->IsA(static_cast<U::UClass*>(stock.asset.Get()))) return 0;
        if (object(stock.current_mesh, component) == desired) return respond(true);
        alignas(void*) std::array<std::byte, StockLayout::setter_capacity> params{};
        stock.new_mesh->SetObjectPropertyValue(stock.new_mesh->ContainerPtrToValuePtr<void>(params.data()), desired);
        component->ProcessEvent(static_cast<U::UFunction*>(stock.set_mesh.Get()), params.data());
        return respond(true, object(stock.current_mesh, component) == desired);
    }
}

void silverfish::native_critter_view::register_lua(Lua& lua)
{
    lua.register_function("SilverfishUpdateNativeCritterMesh", update_mesh);
}
