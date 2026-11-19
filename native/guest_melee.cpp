#include "guest_melee.hpp"
#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
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
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = H::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Guid = std::array<std::uint32_t, 4>;

    constexpr auto map_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto pipe_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C");
    constexpr auto master_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Master_BP.MeleeWep_Master_BP_C");
    constexpr auto animator_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleeWep_AnimBP.MeleeWep_AnimBP_C");
    constexpr auto sequence_path = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleWep_Anim/Melee_Slash_01.Melee_Slash_01");
    constexpr auto attack_path = STR("/Game/Classes/Libraries/Structs/MeleeAttack_Struct.MeleeAttack_Struct");
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
    constexpr int max_rows = 4096;
    constexpr int max_effects = 256;

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") && env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") &&
            env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
    }

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

    bool selected_pipe(U::UObject* save, const Guid& wanted, const Passives& passives)
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
            return ++matches == 1 && selected && item == find<U::UClass>(pipe_path);
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
        double host_health{}, guest_health{};
        bool pending{}, settled{}, passive_free{};
    };

    Swing swing;
    std::int64_t serial{};
    std::array callbacks{H::ERROR_ID, H::ERROR_ID};

    bool owner_current(const Swing& expected)
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
            boolean(pawn, STR("Dead"), false) && boolean(pawn, STR("UsingItem"), false) &&
            boolean(pawn, STR("IsWeaponBlocked"), false) && boolean(pawn, STR("HeadUnderWater"), false) &&
            silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, expected.canonical.Get());
    }

    double health(U::UObject* save)
    {
        auto* value = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
        return value ? value->GetPropertyValueInContainer(save) : -1.0;
    }

    bool stock_attack(const Swing& current, bool active)
    {
        auto* weapon = current.pipe.Get();
        auto* motor = current.animation.Get();
        auto* combo = field<U::FArrayProperty>(weapon, STR("ComboAttacks"));
        auto* combo_row = combo ? U::CastField<U::FStructProperty>(combo->GetInner()) : nullptr;
        auto* attack = field<U::FStructProperty>(motor, STR("ActiveAttack"));
        auto* parameter = field<U::FStructProperty>(current.contact.Get(), STR("Attack"));
        auto* charge = field<U::FFloatProperty>(weapon, STR("ChargeTime"));
        auto* type = find<U::UScriptStruct>(attack_path);
        if (!combo_row || !attack || !type || !parameter || !charge ||
            charge->GetPropertyValueInContainer(weapon) != 0.6f || attack->GetStruct().Get() != type ||
            combo_row->GetStruct().Get() != type || parameter->GetStruct().Get() != type) return false;
        U::FScriptArrayHelper rows(combo, combo->ContainerPtrToValuePtr<void>(weapon));
        if (rows.Num() != 2) return false;
        auto* name = field<U::FObjectProperty>(type, animation_name);
        auto* range = field<U::FDoubleProperty>(type, range_name);
        auto* radius = field<U::FDoubleProperty>(type, radius_name);
        auto* breach = field<U::FBoolProperty>(type, breach_name);
        auto* damage = field<U::FStructProperty>(type, damage_name);
        auto* damage_type = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/Damage_Struct.Damage_Struct"));
        auto* blunt = field<U::FDoubleProperty>(damage_type, blunt_name);
        auto* first = rows.GetRawPtr(0);
        auto* sequence = name ? name->GetObjectPropertyValue(name->ContainerPtrToValuePtr<void>(first)) : nullptr;
        return live(sequence) && sequence == find<U::UObject>(sequence_path) && range && radius && breach && damage &&
            damage_type && damage->GetStruct().Get() == damage_type && blunt &&
            range->GetPropertyValueInContainer(first) == 230.0 && radius->GetPropertyValueInContainer(first) == 65.0 &&
            !breach->GetPropertyValueInContainer(first) &&
            blunt->GetPropertyValueInContainer(damage->ContainerPtrToValuePtr<void>(first)) == 30.0 &&
            (!active || attack->Identical(attack->ContainerPtrToValuePtr<void>(motor), first));
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

    void tick()
    {
        if (!swing.pending) return;
        if (!owner_current(swing)) return finish("mutation_unverified_guest_melee_owner");
        if (Clock::now() >= swing.deadline) return finish("mutation_unverified_guest_melee_timeout");
        if (swing.contacts != 1 || swing.qualified != 1 || !idle(swing)) return;
        Passives passives;
        std::vector<U::FName> cache;
        swing.passive_free = passives.canonical(swing.game.Get(), cache) && cache == swing.cache &&
            passives.character(swing.save.Get()) && selected_pipe(swing.save.Get(), swing.guid, passives);
        if (!swing.passive_free) return finish("mutation_unverified_guest_melee_passive_readback");
        if (health(swing.canonical.Get()) != swing.host_health || health(swing.save.Get()) != swing.guest_health)
            return finish("mutation_unverified_guest_melee_health_readback");
        finish("surface_verified", true);
    }

    void fence_contact(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!U::IsInGameThreadRaw() || !receiver || receiver != swing.pipe.Get() || frame.Node() != swing.contact.Get()) return;
        // Retain the weak receiver fence after every terminal outcome. An animation
        // can notify after loss of ownership, timeout, or an explicit abort.
        const bool already_vetoed = call.OriginalFunctionCallPrevented();
        if (!already_vetoed) call.PreventOriginalFunctionCall();
        ++swing.contacts;
        auto* graph = frame.PreviousFrame();
        auto* notify = graph ? graph->PreviousFrame() : nullptr;
        if (!swing.pending || already_vetoed || Clock::now() >= swing.deadline || swing.contacts != 1)
            return finish("mutation_unverified_guest_melee_unexpected_contact");
        if (frame.Object() != receiver || !graph || graph->Node() != swing.graph.Get() ||
            graph->Object() != swing.animation.Get() || !notify || notify->Node() != swing.hit.Get() ||
            notify->Object() != swing.animation.Get())
            return finish("mutation_unverified_guest_melee_contact_path");
        if (!owner_current(swing) || !stock_attack(swing, true))
            return finish("mutation_unverified_guest_melee_contact_owner_attack");
        ++swing.qualified;
    }

    bool hooks()
    {
        if (callbacks[0] != H::ERROR_ID && callbacks[1] != H::ERROR_ID) return true;
        constexpr auto owner = STR("SilverfishCoopNative");
        callbacks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(
            fence_contact, {false, false, owner, STR("Fence the one Old Pipe animation contact before its sweep")});
        callbacks[1] = H::RegisterEngineTickPreCallback(
            [](Iteration&, U::UEngine*, float, bool) { if (U::IsInGameThreadRaw()) tick(); },
            {false, false, owner, STR("Bound the isolated guest Old Pipe animation receipt")});
        if (callbacks[0] != H::ERROR_ID && callbacks[1] != H::ERROR_ID) return true;
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
        return 9;
    }

    int report(const Lua& lua)
    {
        return proof(lua, !swing.status.starts_with("mutation_unverified_"), swing.status, &swing);
    }

    int start(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_string(3)) return proof(lua, false, "native_guest_melee_arguments_unqualified");
        if (swing.pending || (swing.serial && swing.pipe.Get()))
            return proof(lua, false, "native_guest_melee_fixture_already_bound");
        const auto pawn_address = lua.get_integer();
        const auto save_address = lua.get_integer();
        const auto guid = lua.get_string();
        Swing candidate;
        if (pawn_address <= 0 || save_address <= 0 || !guid_from(guid, candidate.guid))
            return proof(lua, false, "native_guest_melee_arguments_unqualified");
        U::UObject* pawn{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object, int, int) {
            if (reinterpret_cast<std::uintptr_t>(object) != static_cast<std::uintptr_t>(pawn_address))
                return RC::LoopAction::Continue;
            pawn = object;
            return RC::LoopAction::Break;
        });
        auto* save = object(pawn, STR("Save"));
        auto* game = object(pawn, STR("As Game Instance FP"));
        auto* canonical = object(game, STR("SaveSlot_Ref"));
        auto* world = find<U::UWorld>(map_path);
        if (reinterpret_cast<std::uintptr_t>(save) != static_cast<std::uintptr_t>(save_address) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, canonical) || !live(world) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world)
            return proof(lua, false, "native_guest_melee_exact_owner_unqualified");
        auto* pipe = object(pawn, STR("EquipedItem"));
        auto* mesh = object(pipe, STR("Wep_Skeli"));
        auto* anim = object(pipe, STR("As Melee Wep Anim BP"));
        auto* old_pipe = find<U::UClass>(pipe_path);
        auto* animator = find<U::UClass>(animator_path);
        auto* master = find<U::UClass>(master_path);
        if (!live(pipe) || !old_pipe || pipe->GetClassPrivate() != old_pipe || !live(anim) ||
            !animator || anim->GetClassPrivate() != animator)
            return proof(lua, false, "native_guest_melee_stock_receiver_unqualified");
        auto* primary = local_function(pipe, master, STR("Use_Item_Primary"), 2);
        auto* contact = local_function(pipe, master, STR("Contact"), 1);
        auto* hit = local_function(anim, animator, STR("AnimNotify_Hit"), 0);
        auto* graph = local_function(anim, animator, STR("ExecuteUbergraph_MeleeWep_AnimBP"), 1);
        auto* held = primary_input<U::FBoolProperty>(primary, STR("Held"), offsetof(Primary, held));
        if (!primary || primary->GetParmsSize() != sizeof(Primary) || !held ||
            !primary_input<U::FDoubleProperty>(primary, STR("VarianceScaler"), offsetof(Primary, variance)) ||
            !contact || !hit || !graph || !field<U::FObjectProperty>(pipe, STR("InputIntercept")) ||
            object(pipe, STR("InputIntercept")))
            return proof(lua, false, "native_guest_melee_stock_calls_unqualified");
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
        if (!stock_attack(candidate, false)) return proof(lua, false, "native_guest_melee_stock_swing_unqualified");
        candidate.host_health = health(canonical);
        candidate.guest_health = health(save);
        if (!std::isfinite(candidate.host_health) || candidate.host_health <= 1.0 ||
            !std::isfinite(candidate.guest_health) || candidate.guest_health <= 1.0)
            return proof(lua, false, "native_guest_melee_stock_health_unqualified");
        Passives passives;
        candidate.passive_free = passives.canonical(game, candidate.cache) && passives.character(save) &&
            selected_pipe(save, candidate.guid, passives);
        if (!candidate.passive_free) return proof(lua, false, "native_guest_melee_guid_or_passives_unqualified");
        if (!hooks() || serial >= (std::int64_t{1} << 53) - 1)
            return proof(lua, false, "native_guest_melee_hook_unavailable");

        candidate.serial = ++serial;
        candidate.deadline = Clock::now() + attack_timeout;
        candidate.status = "pending";
        candidate.pending = true;
        swing = std::move(candidate);
        Primary press{};
        held->SetPropertyValueInContainer(&press, true);
        ++swing.presses;
        pipe->ProcessEvent(primary, &press);
        if (!owner_current(swing)) finish("mutation_unverified_guest_melee_press_owner");
        else
        {
            const auto pressed = boolean(pipe, STR("Holding_Primary"), true) &&
                boolean(anim, STR("IsMidSwing"), true) && live(object(anim, STR("ActiveMontage")));
            Primary release{};
            held->SetPropertyValueInContainer(&release, false);
            ++swing.releases;
            pipe->ProcessEvent(primary, &release);
            if (!pressed || !owner_current(swing) || !boolean(pipe, STR("Holding_Primary"), false))
                finish("mutation_unverified_guest_melee_press_release");
        }
        return report(lua);
    }

    template <bool abort> int observe(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            !swing.serial || swing.serial != lua.get_integer())
            return proof(lua, false, "native_guest_melee_serial_unqualified");
        if constexpr (abort)
        {
            if (swing.pending) finish("aborted_contact_fenced");
        }
        else tick();
        return report(lua);
    }
}

void silverfish::native_guest_melee::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishStartNativeGuestMeleeCanary", start);
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
}
