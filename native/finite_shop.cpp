#include "finite_shop.hpp"
#include "stock_wallet.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <string_view>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/Property/FTextProperty.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    struct MerchantProfile
    {
        const Unreal::TCHAR* world;
        const Unreal::TCHAR* actor;
        const Unreal::TCHAR* data;
        const Unreal::TCHAR* cash_barter_replacement{};
        bool camera_only{};
        const Unreal::TCHAR* reception{};
        bool smuggler{};
    };

    constexpr std::array merchants{
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Shop_BP_C_2"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/Observitory_ShopData.Observitory_ShopData"),
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P:PersistentLevel.Shop_BP_C_2"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/ScavBar_ShopData.ScavBar_ShopData"),
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01:PersistentLevel.Shop_BP_C_1"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/BlacMarket_ShopData.BlacMarket_ShopData"),
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.Shop_BP_C_2"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/Science_ShopData.Science_ShopData"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.Barter_BP_C_107"),
            true,
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P.Shop_Gunsmith_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P.Shop_Gunsmith_P:PersistentLevel.Shop_BP_C_1"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/AmmoShopData.AmmoShopData"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P.Shop_Gunsmith_P:PersistentLevel.Barter_BP_C_1"),
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P:PersistentLevel.Shop_BP_C_1"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/StrangeJunkShopData.StrangeJunkShopData"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P:PersistentLevel.Barter_BP_C_1"),
            false,
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P:PersistentLevel.Generic_Interact_BP_C_1"),
        },
        MerchantProfile{
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P.Shop_Smuggler_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P.Shop_Smuggler_P:PersistentLevel.Shop_BP_C_1"),
            STR("/Game/Classes/Libraries/DataAssets/Merchants/SmugglerShopData.SmugglerShopData"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P.Shop_Smuggler_P:PersistentLevel.Barter_BP_C_2"),
            false, nullptr, true,
        },
    };
    constexpr auto shop_class = STR("/Game/Classes/Actors/Shop_BP.Shop_BP_C");
    constexpr auto panel_class = STR("/Game/Classes/GUI/New/Components/ShopPanel.ShopPanel_C");
    constexpr auto calc_markup = STR("/Game/Classes/GUI/New/Components/ShopPanel.ShopPanel_C:Calc_ShopMarkup");
    constexpr auto calc_item = STR("/Game/Classes/GUI/New/Components/ShopPanel.ShopPanel_C:CalcItemMarkup");
    constexpr auto save_inventory = STR("/Game/Classes/Actors/Shop_BP.Shop_BP_C:SaveInventory");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto item_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto camera_class = STR("/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C");
    constexpr auto blank_tape_class = STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C");
    constexpr auto item_struct = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto saved_shop_struct = STR("/Game/Classes/Libraries/Structs/ShopInventory_SaveStruct.ShopInventory_SaveStruct");
    constexpr auto payload_struct = STR("/Script/StructUtils.InstancedStruct");
    constexpr std::array guid_words{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr int max_offers = 128;
    constexpr int max_items = 4096;
    constexpr int max_clients = 4;
    constexpr int max_money = std::numeric_limits<std::int32_t>::max();

    bool flag(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 &&
            size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return flag("SILVERFISH_COOP_NATIVE_ZONE") && flag("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* value = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    Unreal::UObject* read_object(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FObjectProperty>(owner, name);
        return value ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool read_bool(Unreal::UObject* owner, const Unreal::TCHAR* name, bool expected)
    {
        auto* value = field<Unreal::FBoolProperty>(owner, name);
        return value && value->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(Unreal::UObject* object, Unreal::UClass* type)
    {
        return type && live(object) && object->GetClassPrivate() == type;
    }

    bool authority(Unreal::UObject* actor, Unreal::UWorld* world)
    {
        auto* role = field<Unreal::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            read_bool(actor, STR("bActorIsBeingDestroyed"), false) &&
            static_cast<Unreal::AActor*>(actor)->GetWorld() == world;
    }

    Address address(Unreal::UObject* object) { return reinterpret_cast<Address>(object); }

    struct Context
    {
        Unreal::UWorld* world{};
        Unreal::UObject* shop{};
        Unreal::UObject* panel{};
        Unreal::UObject* pawn{};
        Unreal::UObject* guest{};
        Unreal::UObject* stock{};
        Unreal::UObject* instance{};
        Unreal::UObject* reception{};
        bool camera_only{}, guards_neutral_purchase{}, smuggler{};
        bool host{};
    };

    bool qualified_host_connection(const Context& context)
    {
        auto* controller = read_object(context.pawn, STR("Controller"));
        auto* driver = read_object(context.world, STR("NetDriver"));
        auto* player = read_object(controller, STR("Player"));
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* players = field<Unreal::FArrayProperty>(context.instance, STR("LocalPlayers"));
        auto* local_field = players ? Unreal::CastField<Unreal::FObjectProperty>(players->GetInner()) : nullptr;
        if (!context.camera_only || !authority(context.pawn, context.world) ||
            !exact(controller, find<Unreal::UClass>(controller_class)) || !authority(controller, context.world) ||
            !read_bool(controller, STR("Loading"), false) || !live(player) || !local_type || !player->IsA(local_type) ||
            !live(driver) || !local_field || !field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) ||
            read_object(driver, STR("ServerConnection")) || read_object(driver, STR("World")) != context.world ||
            read_object(player, STR("PlayerController")) != controller || read_object(controller, STR("Pawn")) != context.pawn ||
            read_object(context.world, STR("OwningGameInstance")) != context.instance ||
            read_object(context.instance, STR("As Player FP")) != context.pawn ||
            read_object(context.pawn, STR("As Game Instance FP")) != context.instance ||
            read_object(context.instance, STR("SaveSlot_Ref")) != context.stock ||
            read_object(context.pawn, STR("Save")) != context.stock || context.guest != context.stock) return false;
        Unreal::FScriptArrayHelper local_players(players, players->ContainerPtrToValuePtr<void>(context.instance));
        return local_players.Num() == 1 && local_field->GetObjectPropertyValue(local_players.GetRawPtr()) == player;
    }

    bool qualified_connection(const Context& context)
    {
        if (context.host) return qualified_host_connection(context);
        auto* controller_type = find<Unreal::UClass>(controller_class);
        auto* local_type = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* net_type = find<Unreal::UClass>(STR("/Script/Engine.NetConnection"));
        auto* controller = read_object(context.pawn, STR("Controller"));
        auto* connection = read_object(controller, STR("Player"));
        auto* driver = read_object(context.world, STR("NetDriver"));
        auto* clients = field<Unreal::FArrayProperty>(driver, STR("ClientConnections"));
        auto* remote_type = clients ? Unreal::CastField<Unreal::FObjectProperty>(clients->GetInner()) : nullptr;
        auto* players = field<Unreal::FArrayProperty>(context.instance, STR("LocalPlayers"));
        auto* local_field = players ? Unreal::CastField<Unreal::FObjectProperty>(players->GetInner()) : nullptr;
        // Loading is a nonreplicated stock UI flag, initialized true. The
        // authority copy of a connected guest can retain the CDO value.
        if (!controller_type || !local_type || !net_type || !exact(controller, controller_type) ||
            !authority(controller, context.world) ||
            !live(connection) || !connection->IsA(net_type) || !live(driver) || !remote_type || !local_field ||
            !field<Unreal::FObjectProperty>(driver, STR("ServerConnection")) || read_object(driver, STR("ServerConnection")) ||
            read_object(driver, STR("World")) != context.world || read_object(connection, STR("Driver")) != driver ||
            read_object(connection, STR("OwningActor")) != controller || read_object(connection, STR("PlayerController")) != controller ||
            read_object(controller, STR("Pawn")) != context.pawn) return false;
        Unreal::FScriptArrayHelper local_players(players, players->ContainerPtrToValuePtr<void>(context.instance));
        if (local_players.Num() != 1) return false;
        auto* player = local_field->GetObjectPropertyValue(local_players.GetRawPtr());
        auto* host_controller = read_object(player, STR("PlayerController"));
        auto* host_pawn = read_object(host_controller, STR("Pawn"));
        if (!live(player) || !player->IsA(local_type) || !exact(host_controller, controller_type) ||
            !authority(host_controller, context.world) || read_object(host_controller, STR("Player")) != player ||
            !exact(host_pawn, context.pawn->GetClassPrivate()) || !authority(host_pawn, context.world) ||
            read_object(host_pawn, STR("Controller")) != host_controller ||
            read_object(host_pawn, STR("Save")) != context.stock) return false;

        Unreal::FScriptArrayHelper remotes(clients, clients->ContainerPtrToValuePtr<void>(driver));
        if (remotes.Num() < 1 || remotes.Num() > max_clients) return false;
        int matches = 0;
        for (int index = 0; index < remotes.Num(); ++index)
        {
            auto* entry = remote_type->GetObjectPropertyValue(remotes.GetRawPtr(index));
            if (!live(entry) || !entry->IsA(net_type)) return false;
            if (entry == connection)
            {
                ++matches;
                continue;
            }
            auto* other = read_object(read_object(entry, STR("PlayerController")), STR("Pawn"));
            if (read_object(other, STR("Save")) == context.guest) return false;
        }
        return matches == 1;
    }

    using Location = std::array<double, 3>;
    bool location(Unreal::UObject* object, const Unreal::TCHAR* path, Location& output)
    {
        auto* method = find<Unreal::UFunction>(path);
        auto* result = field<Unreal::FStructProperty>(method, STR("ReturnValue"));
        auto* vector = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        if (!method || !result || !vector || result->GetStruct().Get() != vector ||
            result != method->GetReturnProperty() || result->GetOffset_Internal() != 0 ||
            method->GetNumParms() != 1 || method->GetParmsSize() != sizeof(output) ||
            !method->HasAnyFunctionFlags(Unreal::FUNC_Native)) return false;
        object->ProcessEvent(method, &output);
        return std::ranges::all_of(output, [](double value) { return std::isfinite(value) && std::abs(value) < 1e8; });
    }

    bool qualified_reach(const Context& context)
    {
        auto* counter = context.reception ? context.reception : context.shop;
        auto* box = read_object(counter, STR("Box"));
        auto* range = field<Unreal::FDoubleProperty>(context.pawn, STR("InteractionRange"));
        if (!exact(box, find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"))) ||
            box->GetOuterPrivate() != counter || !range) return false;
        const auto distance = range->GetPropertyValueInContainer(context.pawn);
        Location guest{}, merchant{};
        if (!std::isfinite(distance) || distance <= 0 || distance > 1000 ||
            !location(context.pawn, STR("/Script/Engine.Actor:K2_GetActorLocation"), guest) ||
            !location(box, STR("/Script/Engine.SceneComponent:K2_GetComponentLocation"), merchant))
            return false;
        const auto x = guest[0] - merchant[0], y = guest[1] - merchant[1], z = guest[2] - merchant[2];
        return (x * x + y * y + z * z) < distance * distance;
    }

    bool cash_counter_open(Unreal::UObject* shop)
    {
        auto* box = read_object(shop, STR("Box"));
        auto* function = find<Unreal::UFunction>(STR("/Script/Engine.PrimitiveComponent:GetCollisionEnabled"));
        auto* output = field<Unreal::FByteProperty>(function, STR("ReturnValue"));
        std::uint8_t mode{};
        if (!exact(box, find<Unreal::UClass>(STR("/Script/Engine.BoxComponent"))) ||
            !function || !output || output != function->GetReturnProperty() || output->GetOffset_Internal() != 0 ||
            function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(mode) ||
            !function->HasAnyFunctionFlags(Unreal::FUNC_Native)) return false;
        box->ProcessEvent(function, &mode);
        return mode == 1 || mode == 3;
    }

    bool smuggler_cash_stock(Unreal::UObject* shop, Unreal::UObject* data)
    {
        auto* markup = field<Unreal::FStructProperty>(data, STR("MarkupMin/Max"));
        auto* vector_type = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Vector2D"));
        auto* x = field<Unreal::FDoubleProperty>(vector_type, STR("X"));
        auto* y = field<Unreal::FDoubleProperty>(vector_type, STR("Y"));
        if (!markup || !vector_type || markup->GetStruct().Get() != vector_type || !x || !y)
            return false;
        const auto* bounds = markup->ContainerPtrToValuePtr<void>(data);
        return x->GetPropertyValueInContainer(bounds) == 5.0 && y->GetPropertyValueInContainer(bounds) == 5.0 &&
            field<Unreal::FBoolProperty>(shop, STR("NoInfiniteStock")) && read_bool(shop, STR("CanSellTo"), true) &&
            read_bool(shop, STR("LimitedCash"), true);
    }

    std::string_view bind_context(const std::array<Address, 5>& input, Context& context, bool allow_host = false)
    {
        const MerchantProfile* profile = nullptr;
        for (const auto& listed : merchants)
        {
            auto* actor = find<Unreal::UObject>(listed.actor);
            if (!actor || address(actor) != input[3]) continue;
            context.shop = actor;
            profile = &listed;
            break;
        }
        if (!profile) return "shop_exact_merchant_unavailable";
        context.camera_only = profile->camera_only;
        context.smuggler = profile->smuggler;
        context.guards_neutral_purchase = profile->cash_barter_replacement && !profile->camera_only;
        context.world = find<Unreal::UWorld>(profile->world);
        auto* data = find<Unreal::UObject>(profile->data);
        if (!live(context.world) || !exact(context.shop, find<Unreal::UClass>(shop_class)) ||
            !authority(context.shop, context.world) || !live(data) ||
            static_cast<Unreal::AActor*>(context.shop)->GetLevel() != read_object(context.world, STR("PersistentLevel")) ||
            read_object(context.shop, STR("MerchantData")) != data || !read_bool(context.shop, STR("IsCheatShop"), false))
            return "shop_exact_merchant_unavailable";

        auto* pawn_type = find<Unreal::UClass>(pawn_class);
        auto* panel_type = find<Unreal::UClass>(panel_class);
        if (!pawn_type || !panel_type) return "shop_class_unavailable";
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            if (address(object) == input[0] && exact(object, pawn_type)) context.pawn = object;
            if (address(object) == input[4] && exact(object, panel_type)) context.panel = object;
            return context.pawn && context.panel ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        context.instance = read_object(context.world, STR("OwningGameInstance"));
        context.stock = read_object(context.instance, STR("SaveSlot_Ref"));
        context.guest = read_object(context.pawn, STR("Save"));
        context.host = allow_host && context.camera_only && context.guest == context.stock;
        auto* save_type = find<Unreal::UClass>(save_class);
        if (!authority(context.pawn, context.world) || !read_bool(context.pawn, STR("Dead"), false))
            return context.host ? "shop_exact_standing_host_unavailable" : "shop_exact_standing_guest_unavailable";
        if (!exact(context.instance, find<Unreal::UClass>(instance_class)) || !exact(context.stock, save_type) ||
            !exact(context.guest, save_type) || (!context.host && context.stock == context.guest) ||
            address(context.stock) != input[2] || address(context.guest) != input[1] ||
            read_object(context.shop, STR("As Game Instance FP")) != context.instance)
            return "shop_exact_guest_and_canonical_saves_unavailable";
        if (!read_bool(context.stock, STR("BarterOnly"), false))
            return "shop_stock_cash_trade_unavailable";
        if (context.smuggler && !smuggler_cash_stock(context.shop, data))
            return "shop_smuggler_cash_stock_unverified";
        if (profile->reception)
        {
            context.reception = find<Unreal::UObject>(profile->reception);
            auto* reception_type = find<Unreal::UClass>(
                STR("/Game/Classes/ScriptingActors/Generic_Interact_BP.Generic_Interact_BP_C"));
            if (!exact(context.reception, reception_type) || !authority(context.reception, context.world) ||
                static_cast<Unreal::AActor*>(context.reception)->GetLevel() != read_object(context.world, STR("PersistentLevel")) ||
                !cash_counter_open(context.reception))
                return "shop_shopkeep_cash_reception_unavailable";
        }
        if (profile->cash_barter_replacement)
        {
            auto* conversion = find<Unreal::UObject>(profile->cash_barter_replacement);
            if (!exact(conversion, find<Unreal::UClass>(STR("/Game/Classes/Actors/Barter_BP.Barter_BP_C"))) ||
                !authority(conversion, context.world) || read_object(context.shop, STR("BarterReplacement")) != conversion ||
                read_object(conversion, STR("MerchantData")) != data || (!context.reception && !cash_counter_open(context.shop)))
                return context.reception ? "shop_shopkeep_cash_reception_unavailable" :
                    context.smuggler ? "shop_smuggler_cash_counter_unavailable" :
                    context.camera_only ? "shop_scientist_cash_counter_unavailable" : "shop_gunsmith_cash_counter_unavailable";
            if (context.guards_neutral_purchase)
            {
                auto* faction = field<Unreal::FByteProperty>(context.shop, STR("Rep Faction"));
                if (static_cast<Unreal::AActor*>(conversion)->GetLevel() != read_object(context.world, STR("PersistentLevel")) ||
                    !read_bool(conversion, STR("AcceptCash"), true) || !faction || faction->GetPropertyValueInContainer(context.shop) != 0)
                    return context.reception ? "shop_shopkeep_neutral_profile_unverified" :
                        context.smuggler ? "shop_smuggler_neutral_profile_unverified" : "shop_gunsmith_neutral_profile_unverified";
            }
        }
        if (!qualified_connection(context))
            return context.host ? "shop_exact_host_connection_unavailable" : "shop_exact_guest_connection_unavailable";
        if (!context.panel || read_object(context.panel, STR("Target")) != context.shop ||
            read_object(context.panel, STR("GameInstanceFP")) != context.instance)
            return "shop_exact_offscreen_quote_panel_unavailable";
        auto* health = field<Unreal::FDoubleProperty>(context.guest, STR("PlayerHealth"));
        const auto hp = health ? health->GetPropertyValueInContainer(context.guest) : 0.0;
        if (!std::isfinite(hp) || hp <= 0.0)
            return context.host ? "shop_standing_host_health_unavailable" : "shop_standing_guest_health_unavailable";
        if (!qualified_reach(context))
            return context.host ? "shop_host_outside_stock_reach" : "shop_guest_outside_stock_reach";
        return {};
    }

    struct DestroyValue
    {
        Unreal::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, DestroyValue>;

    Value copy(Unreal::FProperty* property, const void* current)
    {
        Value value(property->AllocateAndInitializeValue(), {property});
        if (value && current) property->CopyCompleteValue(value.get(), current);
        return value;
    }

    struct Layout
    {
        Unreal::UScriptStruct* item = find<Unreal::UScriptStruct>(item_struct);
        Unreal::UClass* item_base = find<Unreal::UClass>(item_class);
        Unreal::FArrayProperty* finite{};
        Unreal::FArrayProperty* buyback{};
        Unreal::FArrayProperty* backpack{};
        Unreal::FArrayProperty* equipment{};
        Unreal::FStructProperty* armor{};
        Unreal::FStructProperty* helmet{};
        Unreal::FStructProperty* row{};
        Unreal::FStructProperty* guid{};
        Unreal::FStructProperty* payload{};
        Unreal::FClassProperty* type{};
        Unreal::FIntProperty* ammo{};
        Unreal::FIntProperty* value{};
        Unreal::FDoubleProperty* weight{};
        Unreal::FBoolProperty* never_drop{};
        Unreal::FIntProperty* merchant_cash{};
        Unreal::FIntProperty* shop_index{};
        Unreal::FTextProperty* shop_name{};
        Unreal::FMapProperty* flags{};
        Unreal::FArrayProperty* saved_shops{};
        Unreal::FArrayProperty* saved_finite{};
        Unreal::FArrayProperty* saved_buyback{};
        Unreal::FTextProperty* saved_name{};
        Unreal::FIntProperty* saved_cash{};
        Unreal::UFunction* save{};
        Unreal::UFunction* quote_refresh{};
        Unreal::UFunction* quote{};
        Unreal::FStructProperty* quote_item{};
        Unreal::FDoubleProperty* quote_result{};
        std::array<Unreal::FIntProperty*, 4> words{};

        bool is_row(Unreal::FProperty* property) const
        {
            auto* structure = Unreal::CastField<Unreal::FStructProperty>(property);
            return structure && structure->GetStruct().Get() == item && structure->GetArrayDim() == 1;
        }

        bool is_array(Unreal::FArrayProperty* property) const { return property && is_row(property->GetInner()); }

        bool bind(const Context& context)
        {
            auto* saved_type = find<Unreal::UScriptStruct>(saved_shop_struct);
            auto* item_guid = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            auto* instanced = find<Unreal::UScriptStruct>(payload_struct);
            finite = field<Unreal::FArrayProperty>(context.shop, STR("FiniteInventory"));
            buyback = field<Unreal::FArrayProperty>(context.shop, STR("BuyBackInventory"));
            backpack = field<Unreal::FArrayProperty>(context.guest, STR("PlayerBackpack"));
            equipment = field<Unreal::FArrayProperty>(context.guest, STR("PlayerEquipment"));
            armor = field<Unreal::FStructProperty>(context.guest, STR("PlayerArmor"));
            helmet = field<Unreal::FStructProperty>(context.guest, STR("PlayerHelmet"));
            row = finite ? Unreal::CastField<Unreal::FStructProperty>(finite->GetInner()) : nullptr;
            guid = field<Unreal::FStructProperty>(item, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
            payload = field<Unreal::FStructProperty>(item, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
            type = field<Unreal::FClassProperty>(item, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
            ammo = field<Unreal::FIntProperty>(item, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
            value = field<Unreal::FIntProperty>(item, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"));
            weight = field<Unreal::FDoubleProperty>(item, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"));
            never_drop = field<Unreal::FBoolProperty>(item, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
            merchant_cash = field<Unreal::FIntProperty>(context.shop, STR("Cash"));
            shop_index = field<Unreal::FIntProperty>(context.shop, STR("ShopSave_Index"));
            shop_name = field<Unreal::FTextProperty>(context.shop, STR("ShopName"));
            flags = field<Unreal::FMapProperty>(context.stock, STR("Flags"));
            saved_shops = field<Unreal::FArrayProperty>(context.stock, STR("ShopInventories"));
            auto* saved_row = saved_shops ? Unreal::CastField<Unreal::FStructProperty>(saved_shops->GetInner()) : nullptr;
            saved_finite = field<Unreal::FArrayProperty>(saved_type, STR("Inventory_6_7BF795C6494DCDA07EF9378A112D2E58"));
            saved_buyback = field<Unreal::FArrayProperty>(saved_type, STR("BuyBack_13_017BDDE54F3600CA201C59AFB7BD02D6"));
            saved_name = field<Unreal::FTextProperty>(saved_type, STR("ShopName_2_6A241A6F4C7374F1D56D938276CB7FA4"));
            saved_cash = field<Unreal::FIntProperty>(saved_type, STR("Cash_19_B82762B84E6C5BD86986A5A92BD748E3"));
            save = find<Unreal::UFunction>(save_inventory);
            quote_refresh = find<Unreal::UFunction>(calc_markup);
            quote = find<Unreal::UFunction>(calc_item);
            quote_item = field<Unreal::FStructProperty>(quote, STR("InventoryItem_Struct"));
            quote_result = field<Unreal::FDoubleProperty>(quote, STR("ReturnValue"));
            auto* changed = field<Unreal::FBoolProperty>(quote_refresh, STR("Changed"));
            if (!item || !item_base || !item_guid || !instanced || !saved_type || !is_array(finite) || !is_array(buyback) ||
                !is_array(backpack) || !is_array(equipment) || !is_row(armor) || !is_row(helmet) || !is_row(row) ||
                !is_array(saved_finite) || !is_array(saved_buyback) || !saved_row ||
                saved_row->GetStruct().Get() != saved_type || !saved_name || !saved_cash || !flags ||
                !guid || guid->GetStruct().Get() != item_guid || !payload || payload->GetStruct().Get() != instanced ||
                !type || type->GetMetaClass().Get() != item_base || !ammo || !value || !weight || !never_drop ||
                !merchant_cash || !shop_index || !shop_name ||
                !save || save->GetOuterPrivate() != context.shop->GetClassPrivate() || save->GetNumParms() != 0 ||
                !quote_refresh || quote_refresh->GetOuterPrivate() != context.panel->GetClassPrivate() ||
                !changed || changed->GetOffset_Internal() != 0 || !changed->HasAnyPropertyFlags(Unreal::CPF_OutParm) ||
                quote_refresh->GetNumParms() != 1 || quote_refresh->GetParmsSize() != 1 ||
                !quote || quote->GetOuterPrivate() != context.panel->GetClassPrivate() ||
                !is_row(quote_item) || quote_item->GetOffset_Internal() != 0 || quote_item->GetSize() != 80 ||
                !quote_item->HasAnyPropertyFlags(Unreal::CPF_ConstParm) || !quote_result ||
                quote_result != quote->GetReturnProperty() || quote_result->GetOffset_Internal() != 80 ||
                quote->GetNumParms() != 2 || quote->GetParmsSize() != 88) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                words[index] = field<Unreal::FIntProperty>(item_guid, guid_words[index]);
                if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * sizeof(std::int32_t)))
                    return false;
            }
            return true;
        }

        Guid identity(const void* value) const
        {
            const auto* data = guid->ContainerPtrToValuePtr<void>(value);
            Guid result{};
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return result;
        }

        void replace_identity(void* value, const Guid& key) const
        {
            auto* data = guid->ContainerPtrToValuePtr<void>(value);
            for (std::size_t index = 0; index < words.size(); ++index)
                words[index]->SetPropertyValueInContainer(data, std::bit_cast<std::int32_t>(key[index]));
        }

        Unreal::UClass* item_type(const void* value) const
        {
            return Unreal::Cast<Unreal::UClass>(type->GetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(value)));
        }

        bool ident(const void* first, const void* second) const
        {
            return row->Identical(first, second) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(first), payload->ContainerPtrToValuePtr<void>(second));
        }

        bool equal(Unreal::FArrayProperty* property, const void* first, Unreal::FArrayProperty* other,
                   const void* second, int maximum) const
        {
            Unreal::FScriptArrayHelper left(property, first), right(other, second);
            if (left.Num() != right.Num() || left.Num() < 0 || left.Num() > maximum) return false;
            for (int index = 0; index < left.Num(); ++index)
                if (!ident(left.GetRawPtr(index), right.GetRawPtr(index))) return false;
            return true;
        }

        bool verify_saved(const Context& context, bool current) const
        {
            auto* storage = saved_shops->ContainerPtrToValuePtr<void>(context.stock);
            Unreal::FScriptArrayHelper saved(saved_shops, storage);
            const auto index = shop_index->GetPropertyValueInContainer(context.shop);
            if (saved.Num() < 1 || saved.Num() > max_items || index < 0 || index >= saved.Num()) return false;
            const auto* entry = saved.GetRawPtr(index);
            const auto* name = shop_name->ContainerPtrToValuePtr<void>(context.shop);
            if (!shop_name->Identical(name, saved_name->ContainerPtrToValuePtr<void>(entry))) return false;
            for (int other = 0; other < saved.Num(); ++other)
                if (other != index && shop_name->Identical(name, saved_name->ContainerPtrToValuePtr<void>(saved.GetRawPtr(other))))
                    return false;
            return !current ||
                (saved_cash->GetPropertyValueInContainer(entry) == merchant_cash->GetPropertyValueInContainer(context.shop) &&
                 equal(finite, finite->ContainerPtrToValuePtr<void>(context.shop), saved_finite,
                       saved_finite->ContainerPtrToValuePtr<void>(entry), max_offers) &&
                 equal(buyback, buyback->ContainerPtrToValuePtr<void>(context.shop), saved_buyback,
                       saved_buyback->ContainerPtrToValuePtr<void>(entry), max_offers));
        }

        bool owner_has(Unreal::UObject* owner, const Guid& key) const
        {
            for (auto* property : {backpack, equipment})
            {
                Unreal::FScriptArrayHelper items(property, property->ContainerPtrToValuePtr<void>(owner));
                if (items.Num() < 0 || items.Num() > max_items) return true;
                for (int index = 0; index < items.Num(); ++index)
                    if (identity(items.GetRawPtr(index)) == key) return true;
            }
            return identity(armor->ContainerPtrToValuePtr<void>(owner)) == key ||
                identity(helmet->ContainerPtrToValuePtr<void>(owner)) == key;
        }
    };

    using silverfish::stock_wallet::Wallet;

    struct Offer
    {
        Guid guid{};
        Unreal::UClass* klass{};
        std::string path;
        int quantity{};
        int value{};
        int price{};
        int ammo{};
        double weight{};
        double markup{1.0};
        bool never_drop{};
        bool stackable{};
        std::string_view source{"backpack"};
    };

    Guid new_stock_guid(const Layout& layout)
    {
        auto* library = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetGuidLibrary"));
        auto* function = find<Unreal::UFunction>(STR("/Script/Engine.KismetGuidLibrary:NewGuid"));
        auto* result = field<Unreal::FStructProperty>(function, STR("ReturnValue"));
        Guid guid{};
        if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function || !result ||
            function->GetOuterPrivate() != library->GetClassPrivate() || result != function->GetReturnProperty() ||
            result->GetStruct().Get() != layout.guid->GetStruct().Get() || result->GetOffset_Internal() != 0 ||
            function->GetNumParms() != 1 || function->GetParmsSize() != sizeof(guid) ||
            !function->HasAnyFunctionFlags(Unreal::FUNC_Native)) return {};
        library->ProcessEvent(function, &guid);
        return guid;
    }

    template<typename T>
    T* parameter(Unreal::UFunction* function, const Unreal::TCHAR* name, int offset, bool output)
    {
        auto* value = field<T>(function, name);
        return value && value->GetOffset_Internal() == offset && offset + value->GetSize() <= function->GetParmsSize() &&
            value->HasAnyPropertyFlags(Unreal::CPF_Parm) &&
            value->HasAnyPropertyFlags(Unreal::CPF_OutParm) == output ? value : nullptr;
    }

    struct PassiveEffect
    {
        Unreal::FName name;
        bool count{}, active{};
        std::int32_t instances{};
    };

    bool read_purchase_effect(const Context& context, PassiveEffect& effect)
    {
        auto* method = find<Unreal::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:FindPassiveEffect"));
        auto* count = parameter<Unreal::FBoolProperty>(method, STR("CountInstances"), 8, false);
        auto* active = parameter<Unreal::FBoolProperty>(method, STR("EffectActive"), 9, true);
        auto* instances = parameter<Unreal::FIntProperty>(method, STR("Instances"), 12, true);
        static_assert(sizeof(PassiveEffect) == 16 && offsetof(PassiveEffect, instances) == 12);
        if (!method || method->GetOuterPrivate() != context.instance->GetClassPrivate() ||
            method->GetNumParms() != 4 || method->GetParmsSize() != sizeof(PassiveEffect) ||
            !parameter<Unreal::FNameProperty>(method, STR("EffectName"), 0, false) || !count || !active || !instances)
            return false;
        count->SetPropertyValueInContainer(&effect, true);
        context.instance->ProcessEvent(method, &effect);
        effect.active = active->GetPropertyValueInContainer(&effect);
        effect.instances = instances->GetPropertyValueInContainer(&effect);
        return effect.instances >= 0;
    }

    struct ScientistReputation
    {
        static constexpr std::uint8_t commune = 2;
        Unreal::UFunction* apply{};
        int reward{}, expected{};

        bool bind(const Context& context, const Wallet& wallet, int price)
        {
            if (!context.camera_only) return true;
            auto* faction = field<Unreal::FByteProperty>(context.shop, STR("Rep Faction"));
            // The qualified Scientist shelf quotes stay below the stock Skinflint threshold of 1000.
            if (!faction || faction->GetPropertyValueInContainer(context.shop) != commune || price > 313) return false;
            apply = find<Unreal::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddPlayerRep"));
            if (!apply || apply->GetOuterPrivate() != context.instance->GetClassPrivate() ||
                apply->GetNumParms() != 2 || apply->GetParmsSize() != 8 ||
                !parameter<Unreal::FByteProperty>(apply, STR("Faction"), 0, false) ||
                !parameter<Unreal::FIntProperty>(apply, STR("RepDifference"), 4, false)) return false;
            PassiveEffect trait{Unreal::FName{STR("IntensePersonality")}};
            if (!read_purchase_effect(context, trait)) return false;
            reward = price / 100;
            const auto [before, found] = wallet.flag_value(context.instance, STR("Rep_Commune"));
            const auto change = static_cast<std::int64_t>(reward) * (found ? 1LL + trait.instances : 1LL);
            const auto after = before + change;
            if (change > max_money || after > max_money) return false;
            expected = static_cast<int>(after);
            return true;
        }

        void commit(const Context& context) const
        {
            if (!apply) return;
            struct Params { std::uint8_t faction; std::int32_t amount; } values{commune, reward};
            static_assert(sizeof(Params) == 8 && offsetof(Params, amount) == 4);
            context.instance->ProcessEvent(apply, &values);
        }

        bool verified(const Context& context, const Wallet& wallet) const
        {
            return !apply || wallet.flag_value(context.instance, STR("Rep_Commune")).first == expected;
        }
    };

    std::string_view item_offer(const Layout& layout, const void* row, Offer& result)
    {
        result.guid = layout.identity(row);
        auto* klass = layout.item_type(row);
        if (result.guid == Guid{} || !live(klass) || !klass->IsChildOf(layout.item_base))
            return "shop_offer_identity_invalid";
        result.path = RC::to_string(klass->GetPathName());
        if (!result.path.starts_with("/Game/") || result.path.size() > 512 ||
            !std::ranges::all_of(result.path, [](char c) {
                return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                    c == '/' || c == '.' || c == '_' || c == '-';
            })) return "shop_offer_class_invalid";
        auto* cdo = klass->GetClassDefaultObject().Get();
        auto* stacks = field<Unreal::FBoolProperty>(cdo, STR("Stacks"));
        if (!cdo || cdo->GetClassPrivate() != klass || !cdo->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !stacks)
            return "shop_offer_defaults_invalid";
        result.klass = klass;
        result.stackable = stacks->GetPropertyValueInContainer(cdo);
        result.ammo = layout.ammo->GetPropertyValueInContainer(row);
        result.quantity = result.stackable ? result.ammo : 1;
        result.value = layout.value->GetPropertyValueInContainer(row);
        result.weight = layout.weight->GetPropertyValueInContainer(row);
        result.never_drop = layout.never_drop->GetPropertyValueInContainer(row);
        if (result.quantity < 1 || result.value < 0) return "shop_offer_amount_invalid";
        if (!std::isfinite(result.weight) || result.weight < 0 || result.weight > 100000) return "shop_offer_weight_invalid";
        return {};
    }

    std::string_view offer(const Context& context, const Layout& layout, const void* row, Offer& result)
    {
        if (const auto failure = item_offer(layout, row, result); !failure.empty()) return failure;
        // Stock ShopPanel.MakeItemCard applies a markup of one to its Buy Back panel.
        if (result.source == "buyback")
        {
            result.price = result.value;
            return {};
        }
        alignas(16) std::array<std::byte, 88> parameters{};
        layout.quote_item->InitializeValue(parameters.data());
        layout.quote_item->CopyCompleteValue(parameters.data(), row);
        context.panel->ProcessEvent(layout.quote, parameters.data());
        result.markup = layout.quote_result->GetPropertyValueInContainer(parameters.data());
        layout.quote_item->DestroyValue(parameters.data());
        const auto price = std::ceil(result.markup * result.value);
        if (!std::isfinite(result.markup) || result.markup < 0 || !std::isfinite(price) || price > max_money)
            return "shop_native_quote_invalid";
        result.price = static_cast<int>(price);
        return {};
    }

    std::string encode(const Offer& row)
    {
        return std::format("{:08X}{:08X}{:08X}{:08X}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{:d}\t{:d}\t{}\n",
                           row.guid[0], row.guid[1], row.guid[2], row.guid[3], row.path, row.quantity, row.value, row.price,
                           row.source, row.ammo, row.weight, row.never_drop, row.stackable, row.markup);
    }

    Guid parse_guid(std::string_view text)
    {
        Guid result{};
        if (text.size() != 32 || !std::ranges::all_of(text, [](char c) {
                return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F');
            })) return result;
        for (std::size_t index = 0; index < result.size(); ++index)
        {
            const auto* start = text.data() + index * 8;
            if (std::from_chars(start, start + 8, result[index], 16).ec != std::errc{}) return {};
        }
        return result;
    }

    int response(const Lua& lua, bool ok, std::string_view state, std::string_view rows = {},
                 int wallet_before = -1, int wallet_after = -1, int cash_before = -1, int cash_after = -1)
    {
        lua.set_bool(ok);
        lua.set_string(state);
        lua.set_string(rows);
        lua.set_integer(wallet_before);
        lua.set_integer(wallet_after);
        lua.set_integer(cash_before);
        lua.set_integer(cash_after);
        return 7;
    }

    bool stock_portion(const Context& context, const Layout& layout, const Offer& selected, const void* source,
                       int quantity, bool shelf, Value& taken, Value& remainder)
    {
        auto* library = find<Unreal::UObject>(STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
        auto* function = find<Unreal::UFunction>(shelf ?
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeItemStack") :
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:SplitStack"));
        if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function ||
            function->GetOuterPrivate() != library->GetClassPrivate() || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native | Unreal::FUNC_Net)) return false;
        alignas(16) std::array<std::byte, 0x108> bytes{};
        if (shelf)
        {
            auto* defaults = selected.klass->GetClassDefaultObject().Get();
            auto* base_units = field<Unreal::FIntProperty>(defaults, STR("Ammo"));
            auto* type = parameter<Unreal::FClassProperty>(function, STR("Item_Class"), 0, false);
            auto* count = parameter<Unreal::FIntProperty>(function, STR("Count"), 8, false);
            auto* owner = parameter<Unreal::FObjectProperty>(function, STR("__WorldContext"), 16, false);
            auto* out = parameter<Unreal::FStructProperty>(function, STR("Item"), 24, true);
            if (!base_units || quantity > 10LL * base_units->GetPropertyValueInContainer(defaults) || !type ||
                type->GetMetaClass().Get() != layout.item_base || !count || !owner || !layout.is_row(out) ||
                function->GetNumParms() != 4 || function->GetParmsSize() != 104) return false;
            out->InitializeValue_InContainer(bytes.data());
            type->SetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(bytes.data()), selected.klass);
            count->SetPropertyValueInContainer(bytes.data(), quantity);
            owner->SetObjectPropertyValue(owner->ContainerPtrToValuePtr<void>(bytes.data()), context.pawn);
            library->ProcessEvent(function, bytes.data());
            taken = copy(layout.row, out->ContainerPtrToValuePtr<void>(bytes.data()));
            out->DestroyValue_InContainer(bytes.data());
            return taken != nullptr;
        }
        auto* item = parameter<Unreal::FStructProperty>(function, STR("Item"), 0, false);
        auto* count = parameter<Unreal::FIntProperty>(function, STR("AmountToTake"), 0x50, false);
        auto* owner = parameter<Unreal::FObjectProperty>(function, STR("__WorldContext"), 0x58, false);
        auto* out = parameter<Unreal::FStructProperty>(function, STR("NewItem"), 0x60, true);
        auto* left = parameter<Unreal::FStructProperty>(function, STR("RemainingStack"), 0xB0, true);
        auto* entire = parameter<Unreal::FBoolProperty>(function, STR("EntireStack"), 0x100, true);
        auto* success = parameter<Unreal::FBoolProperty>(function, STR("Success"), 0x101, true);
        if (!layout.is_row(item) || !count || !owner || !layout.is_row(out) || !layout.is_row(left) || !entire || !success ||
            function->GetNumParms() != 7 || function->GetParmsSize() > bytes.size()) return false;
        for (auto* row : {item, out, left}) row->InitializeValue_InContainer(bytes.data());
        item->CopyCompleteValue(item->ContainerPtrToValuePtr<void>(bytes.data()), source);
        count->SetPropertyValueInContainer(bytes.data(), quantity);
        owner->SetObjectPropertyValue(owner->ContainerPtrToValuePtr<void>(bytes.data()), context.pawn);
        library->ProcessEvent(function, bytes.data());
        if (success->GetPropertyValueInContainer(bytes.data()) && !entire->GetPropertyValueInContainer(bytes.data()))
        {
            taken = copy(layout.row, out->ContainerPtrToValuePtr<void>(bytes.data()));
            remainder = copy(layout.row, left->ContainerPtrToValuePtr<void>(bytes.data()));
        }
        for (auto* row : {item, out, left}) row->DestroyValue_InContainer(bytes.data());
        return taken && remainder;
    }

    template <bool Buying, bool ExplicitQuantity = false>
    int transact(const Lua& lua)
    {
        constexpr int size = Buying ? 9 : 5;
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != size)
            return response(lua, false, "shop_scope_unavailable");
        std::array<Address, 5> input{};
        for (auto& target : input)
        {
            if (!lua.is_integer(1)) return response(lua, false, "shop_exact_addresses_required");
            const auto value = lua.get_integer();
            if (value <= 0) return response(lua, false, "shop_exact_addresses_required");
            target = static_cast<Address>(value);
        }
        Guid chosen{};
        int quantity{}, expected_price{}, expected_wallet{};
        if constexpr (Buying)
        {
            if (!lua.is_string(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_integer(4))
                return response(lua, false, "shop_exact_quote_required");
            chosen = parse_guid(lua.get_string());
            const auto count = lua.get_integer(), price = lua.get_integer(), balance = lua.get_integer();
            if (chosen == Guid{} || count < 1 || count > max_money || price < 0 || price > max_money ||
                balance < 0 || balance > max_money) return response(lua, false, "shop_exact_quote_required");
            quantity = static_cast<int>(count);
            expected_price = static_cast<int>(price);
            expected_wallet = static_cast<int>(balance);
        }

        Context context;
        if (const auto state = bind_context(input, context, true); !state.empty()) return response(lua, false, state);
        Layout layout;
        if (!layout.bind(context)) return response(lua, false, "shop_reflected_layout_unavailable");
        if (!layout.verify_saved(context, false)) return response(lua, false, "shop_exact_stock_save_record_unavailable");
        Wallet wallet;
        if (!wallet.bind(context.instance)) return response(lua, false, "shop_stock_wallet_schema_unavailable");
        const int money = wallet.amount(context.instance);
        const int cash = layout.merchant_cash->GetPropertyValueInContainer(context.shop);
        if (money < 0 || cash < 0) return response(lua, false, "shop_wallet_unavailable");
        std::uint8_t changed{};
        context.panel->ProcessEvent(layout.quote_refresh, &changed);
        auto* finite = layout.finite;
        Unreal::FArrayProperty* shelf{};
        const auto* camera = context.camera_only ? find<Unreal::UClass>(camera_class) : nullptr;
        const auto* blank_tape = context.camera_only && !context.host ? find<Unreal::UClass>(blank_tape_class) : nullptr;
        auto* limited = field<Unreal::FBoolProperty>(context.shop, STR("NoInfiniteStock"));
        if (!limited) return response(lua, false, "shop_stock_mode_unavailable");
        if (!limited->GetPropertyValueInContainer(context.shop))
        {
            shelf = field<Unreal::FArrayProperty>(context.shop, STR("StockInventory"));
            if (!layout.is_array(shelf)) return response(lua, false, "shop_stock_shelf_unavailable");
        }
        Unreal::FArrayProperty* source_property{};
        std::set<Guid> seen;
        std::string rows;
        Offer selected;
        int index = -1, listed = 0;
        auto* buyback = layout.buyback;
        for (auto* inventory : {shelf, finite, buyback})
        {
            if (!inventory) continue;
            Unreal::FScriptArrayHelper items(inventory, inventory->ContainerPtrToValuePtr<void>(context.shop));
            if (items.Num() < 0 || items.Num() > max_offers) return response(lua, false, "shop_offer_count_unavailable");
            for (int i = 0; i < items.Num(); ++i)
            {
                const auto* item = items.GetRawPtr(i);
                const auto key = layout.identity(item);
                if (key == Guid{} || !seen.insert(key).second) return response(lua, false, "shop_offer_guid_ambiguous");
                if constexpr (Buying)
                    if (key != chosen) continue;
                if (++listed > max_offers) return response(lua, false, "shop_offer_count_unavailable");
                Offer row;
                row.source = inventory == shelf ? "shelf" : inventory == buyback ? "buyback" : "limited";
                if (const auto state = offer(context, layout, item, row); !state.empty()) return response(lua, false, state);
                if constexpr (Buying)
                {
                    const bool tape_offer = blank_tape && row.klass == blank_tape && inventory == shelf;
                    if (context.camera_only && !tape_offer && (!camera || row.klass != camera || inventory == buyback ||
                            row.stackable || row.quantity != 1 || row.value != 125 || row.price > 313))
                        return response(lua, false, "shop_scientist_purchase_profile_unavailable");
                    if (tape_offer && (row.stackable || row.quantity != 1 || row.value != 30 || row.price > 75 || row.ammo != 0))
                        return response(lua, false, "shop_scientist_exact_blank_tape_offer_unavailable");
                }
                rows += encode(row);
                if constexpr (Buying)
                {
                    selected = std::move(row);
                    index = i;
                    source_property = inventory;
                }
            }
        }
        if constexpr (!Buying)
            return response(lua, true, "listed", rows, money, money, cash, cash);
        if (!source_property || index < 0) return response(lua, false, "shop_offer_no_longer_available");
        const bool reusable = source_property == shelf;
        auto* source = source_property->ContainerPtrToValuePtr<void>(context.shop);
        Unreal::FScriptArrayHelper stock_rows(source_property, source);
        Value taken, remainder;
        if constexpr (ExplicitQuantity)
        {
            if (context.host || !selected.stackable || (!reusable && quantity > selected.quantity))
                return response(lua, false, "shop_quantity_unavailable");
            if (reusable || selected.quantity != quantity)
            {
                if (!stock_portion(context, layout, selected, stock_rows.GetRawPtr(index), quantity, reusable,
                                   taken, remainder)) return response(lua, false, "shop_quantity_unavailable");
                Offer portion;
                if (!item_offer(layout, taken.get(), portion).empty() || !portion.stackable || portion.quantity != quantity ||
                    portion.klass != selected.klass || seen.contains(portion.guid) ||
                    layout.owner_has(context.guest, portion.guid) || layout.owner_has(context.stock, portion.guid))
                    return response(lua, false, "shop_quantity_row_unverified");
                if (remainder && (layout.identity(remainder.get()) != chosen ||
                    layout.item_type(remainder.get()) != selected.klass ||
                    layout.ammo->GetPropertyValueInContainer(remainder.get()) != selected.quantity - quantity))
                    return response(lua, false, "shop_quantity_remainder_unverified");
                portion.source = selected.source;
                portion.markup = selected.markup;
                const double price = std::ceil(portion.markup * portion.value);
                if (!std::isfinite(price) || price < 0 || price > max_money)
                    return response(lua, false, "shop_native_quote_invalid");
                portion.price = static_cast<int>(price);
                selected = std::move(portion);
                rows = encode(selected);
            }
        }
        if (expected_wallet != money || selected.quantity != quantity || selected.price != expected_price)
            return response(lua, false, "shop_quote_changed");
        if (money < selected.price || cash > max_money - selected.price)
            return response(lua, false, "shop_funds_unavailable");
        if (layout.owner_has(context.guest, chosen) || layout.owner_has(context.stock, chosen))
            return response(lua, false, "shop_inventory_guid_conflict");
        if (context.guards_neutral_purchase && selected.price > 1000)
        {
            PassiveEffect skinflint{Unreal::FName{STR("Skinflint")}};
            if (!read_purchase_effect(context, skinflint) || skinflint.active)
                return response(lua, false, context.reception ? "shop_shopkeep_skinflint_effect_unverified" :
                    context.smuggler ? "shop_smuggler_skinflint_effect_unverified" :
                    "shop_gunsmith_skinflint_effect_unverified");
        }
        ScientistReputation reputation;
        if (!reputation.bind(context, wallet, selected.price))
            return response(lua, false, "shop_camera_reputation_unqualified");

        const bool remove_offer = !reusable && !remainder;
        auto* target = layout.backpack->ContainerPtrToValuePtr<void>(context.guest);
        auto* saved = layout.saved_shops->ContainerPtrToValuePtr<void>(context.stock);
        auto* flags = layout.flags->ContainerPtrToValuePtr<void>(context.stock);
        Unreal::FScriptArrayHelper backpack(layout.backpack, target);
        const int old_size = backpack.Num();
        const int old_stock = stock_rows.Num();
        if (old_size < 0 || old_size >= max_items || (old_size && backpack.GetRawPtr() == stock_rows.GetRawPtr()))
            return response(lua, false, "shop_guest_backpack_unavailable");
        auto source_before = copy(source_property, source);
        auto target_before = copy(layout.backpack, target);
        auto saved_before = copy(layout.saved_shops, saved);
        auto flags_before = copy(layout.flags, flags);
        auto transferred = taken ? std::move(taken) : copy(layout.row, stock_rows.GetRawPtr(index));
        if (!source_before || !target_before || !saved_before || !flags_before || !transferred)
            return response(lua, false, "shop_before_image_unavailable");
        if (reusable && !ExplicitQuantity)
        {
            const auto minted = new_stock_guid(layout);
            if (minted == Guid{} || seen.contains(minted) || layout.owner_has(context.guest, minted) ||
                layout.owner_has(context.stock, minted))
                return response(lua, false, "shop_shelf_transfer_guid_unavailable");
            layout.replace_identity(transferred.get(), minted);
            selected.guid = minted;
            rows = encode(selected);
        }

        const auto add = backpack.AddValue();
        if (add == old_size && backpack.Num() == old_size + 1)
        {
            layout.row->CopyCompleteValue(backpack.GetRawPtr(add), transferred.get());
            if (remove_offer) stock_rows.RemoveValues(index);
            else if (remainder) layout.row->CopyCompleteValue(stock_rows.GetRawPtr(index), remainder.get());
            wallet.change(context.instance, selected.price, true);
            layout.merchant_cash->SetPropertyValueInContainer(context.shop, cash + selected.price);
            reputation.commit(context);
            context.shop->ProcessEvent(layout.save, nullptr);
            bool verified = stock_rows.Num() == old_stock - (remove_offer ? 1 : 0) && backpack.Num() == old_size + 1 &&
                layout.ident(backpack.GetRawPtr(add), transferred.get()) &&
                wallet.amount(context.instance) == money - selected.price && reputation.verified(context, wallet) && qualified_connection(context) &&
                read_object(context.pawn, STR("Save")) == context.guest &&
                read_object(context.instance, STR("SaveSlot_Ref")) == context.stock &&
                layout.verify_saved(context, true);
            Unreal::FScriptArrayHelper previous_stock(source_property, source_before.get());
            for (int i = 0; verified && i < stock_rows.Num(); ++i)
            {
                const auto* expected = remainder && i == index ? remainder.get() :
                    previous_stock.GetRawPtr(i + (remove_offer && i >= index ? 1 : 0));
                verified = layout.ident(stock_rows.GetRawPtr(i), expected);
            }
            Unreal::FScriptArrayHelper previous_bag(layout.backpack, target_before.get());
            for (int i = 0; verified && i < old_size; ++i)
                verified = layout.ident(backpack.GetRawPtr(i), previous_bag.GetRawPtr(i));
            if (verified)
                return response(lua, true, "purchased", rows, money, money - selected.price, cash, cash + selected.price);
        }
        source_property->CopyCompleteValue(source, source_before.get());
        layout.backpack->CopyCompleteValue(target, target_before.get());
        layout.saved_shops->CopyCompleteValue(saved, saved_before.get());
        layout.flags->CopyCompleteValue(flags, flags_before.get());
        layout.merchant_cash->SetPropertyValueInContainer(context.shop, cash);
        if (!layout.equal(source_property, source, source_property, source_before.get(), max_offers) ||
            !layout.equal(layout.backpack, target, layout.backpack, target_before.get(), max_items) ||
            !layout.flags->Identical(flags, flags_before.get()) || !layout.saved_shops->Identical(saved, saved_before.get()) ||
            wallet.amount(context.instance) != money || layout.merchant_cash->GetPropertyValueInContainer(context.shop) != cash)
            return response(lua, false, "mutation_unverified_shop_restore");
        return response(lua, false, "shop_commit_readback_failed_rolled_back");
    }

    struct Sale
    {
        Unreal::UFunction* add = find<Unreal::UFunction>(STR("/Game/Classes/Actors/Shop_BP.Shop_BP_C:AddItem"));
        Unreal::UFunction* ammo_check = find<Unreal::UFunction>(
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:WeaponHasAmmo"));
        Unreal::UObject* library = find<Unreal::UObject>(
            STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"));
        Unreal::FStructProperty* add_item = parameter<Unreal::FStructProperty>(add, STR("Item"), 0, false);
        Unreal::FIntProperty* destination = parameter<Unreal::FIntProperty>(add, STR("Inventory"), 80, false);
        Unreal::FIntProperty* preferred = parameter<Unreal::FIntProperty>(add, STR("PreferredIndex"), 84, false);
        Unreal::FBoolProperty* success = parameter<Unreal::FBoolProperty>(add, STR("Success"), 88, true);
        Unreal::FIntProperty* added_index = parameter<Unreal::FIntProperty>(add, STR("Index"), 92, true);
        Unreal::FBoolProperty* stacked = parameter<Unreal::FBoolProperty>(add, STR("ItemWasStacked"), 96, true);
        Unreal::FStructProperty* ammo_item = parameter<Unreal::FStructProperty>(ammo_check, STR("Item"), 0, true);
        Unreal::FObjectProperty* world_arg = parameter<Unreal::FObjectProperty>(ammo_check, STR("__WorldContext"), 80, false);
        Unreal::FBoolProperty* has_ammo = parameter<Unreal::FBoolProperty>(ammo_check, STR("AmmoTrue"), 88, true);
        Unreal::FNameProperty* tag{};
        Unreal::FArrayProperty* infinite{};

        bool bind(const Context& context, const Layout& layout)
        {
            tag = field<Unreal::FNameProperty>(layout.item, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"));
            infinite = field<Unreal::FArrayProperty>(context.shop, STR("StockInventory"));
            return tag && layout.is_array(infinite) && add && add->GetOuterPrivate() == context.shop->GetClassPrivate() &&
                add->GetNumParms() == 6 && add->GetParmsSize() >= 97 && add->GetParmsSize() <= 104 &&
                !add->GetReturnProperty() && layout.is_row(add_item) && add_item->GetSize() == 80 &&
                destination && preferred && success && added_index && stacked &&
                library && library->HasAnyFlags(Unreal::RF_ClassDefaultObject) && ammo_check &&
                ammo_check->GetOuterPrivate() == library->GetClassPrivate() && ammo_check->GetNumParms() == 3 &&
                ammo_check->GetParmsSize() >= 89 && ammo_check->GetParmsSize() <= 96 &&
                !ammo_check->GetReturnProperty() && layout.is_row(ammo_item) && ammo_item->GetSize() == 80 &&
                ammo_item->HasAnyPropertyFlags(Unreal::CPF_ConstParm) && world_arg && has_ammo;
        }

        bool loaded(const Context& context, const void* item) const
        {
            alignas(16) std::array<std::byte, 96> params{};
            ammo_item->InitializeValue_InContainer(params.data());
            ammo_item->CopyCompleteValue(params.data(), item);
            world_arg->SetObjectPropertyValue(world_arg->ContainerPtrToValuePtr<void>(params.data()), context.shop);
            library->ProcessEvent(ammo_check, params.data());
            const bool loaded = has_ammo->GetPropertyValueInContainer(params.data());
            ammo_item->DestroyValue_InContainer(params.data());
            return loaded;
        }

        bool preserves_reputation(const Context& context) const
        {
            auto* faction = field<Unreal::FByteProperty>(context.shop, STR("Rep Faction"));
            if (!faction) return false;
            if (faction->GetPropertyValueInContainer(context.shop) == 4) return true;
            auto* query = find<Unreal::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Find_PlayerReputation"));
            auto* target = parameter<Unreal::FByteProperty>(query, STR("Faction"), 0, false);
            auto* points = parameter<Unreal::FIntProperty>(query, STR("Points"), 4, true);
            if (!target || !points || query->GetOuterPrivate() != context.instance->GetClassPrivate() ||
                query->GetNumParms() != 4 || query->GetParmsSize() != 8 ||
                !parameter<Unreal::FByteProperty>(query, STR("Relationship"), 1, true) ||
                !parameter<Unreal::FByteProperty>(query, STR("Reputation"), 2, true)) return false;
            alignas(8) std::array<std::byte, 8> params{};
            target->SetPropertyValueInContainer(params.data(), 4);
            context.instance->ProcessEvent(query, params.data());
            return points->GetPropertyValueInContainer(params.data()) < 300;
        }

        bool day(const Context& context, int& days) const
        {
            auto* library = find<Unreal::UObject>(STR("/Script/Engine.Default__KismetMathLibrary"));
            auto* function = find<Unreal::UFunction>(STR("/Script/Engine.KismetMathLibrary:GetDays"));
            auto* time = field<Unreal::FStructProperty>(context.stock, STR("GameTime"));
            auto* input = parameter<Unreal::FStructProperty>(function, STR("A"), 0, false);
            auto* output = parameter<Unreal::FIntProperty>(function, STR("ReturnValue"), 8, true);
            auto* timespan = find<Unreal::UScriptStruct>(STR("/Script/CoreUObject.Timespan"));
            if (!library || !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || !function ||
                function->GetOuterPrivate() != library->GetClassPrivate() || function->GetNumParms() != 2 ||
                function->GetParmsSize() < 12 || function->GetParmsSize() > 16 || !time || !input || !timespan ||
                time->GetStruct().Get() != timespan || input->GetStruct().Get() != timespan ||
                input->GetSize() != 8 || !output || output != function->GetReturnProperty()) return false;
            alignas(8) std::array<std::byte, 16> params{};
            input->CopyCompleteValue(params.data(), time->ContainerPtrToValuePtr<void>(context.stock));
            library->ProcessEvent(function, params.data());
            days = output->GetPropertyValueInContainer(params.data());
            return days >= 0;
        }

        int add_buyback(const Context& context, const void* item, int index) const
        {
            alignas(16) std::array<std::byte, 104> params{};
            add_item->InitializeValue_InContainer(params.data());
            add_item->CopyCompleteValue(params.data(), item);
            destination->SetPropertyValueInContainer(params.data(), 2);
            preferred->SetPropertyValueInContainer(params.data(), index);
            context.shop->ProcessEvent(add, params.data());
            const int result = success->GetPropertyValueInContainer(params.data()) &&
                !stacked->GetPropertyValueInContainer(params.data())
                ? added_index->GetPropertyValueInContainer(params.data()) : -1;
            add_item->DestroyValue_InContainer(params.data());
            return result;
        }

        bool matches_buyback(const Layout& layout, const void* original, const void* accepted, int day) const
        {
            if (RC::to_string(tag->GetPropertyValueInContainer(accepted).ToString()) != std::to_string(day))
                return false;
            auto normalized = copy(layout.row, accepted);
            if (!normalized) return false;
            tag->SetPropertyValueInContainer(normalized.get(), tag->GetPropertyValueInContainer(original));
            return layout.ident(original, normalized.get());
        }
    };

    bool exact_removal(const Layout& layout, Unreal::FScriptArrayHelper& after, Unreal::FScriptArrayHelper& before, int removed)
    {
        if (after.Num() + 1 != before.Num()) return false;
        for (int i = 0; i < after.Num(); ++i)
            if (!layout.ident(after.GetRawPtr(i), before.GetRawPtr(i + (i >= removed ? 1 : 0)))) return false;
        return true;
    }

    template<bool Commit>
    int sell(const Lua& lua)
    {
        if (!enabled() || !Unreal::IsInGameThreadRaw() || lua.get_stack_size() != (Commit ? 9 : 6))
            return response(lua, false, "shop_sale_scope_unavailable");
        std::array<Address, 5> input{};
        for (auto& address : input)
        {
            if (!lua.is_integer(1)) return response(lua, false, "shop_sale_exact_addresses_required");
            const auto value = lua.get_integer();
            if (value <= 0) return response(lua, false, "shop_sale_exact_addresses_required");
            address = static_cast<Address>(value);
        }
        if (!lua.is_string(1)) return response(lua, false, "shop_sale_exact_guid_required");
        const auto chosen = parse_guid(lua.get_string());
        if (chosen == Guid{}) return response(lua, false, "shop_sale_exact_guid_required");
        int units{}, quoted_value{}, wallet_before{};
        if constexpr (Commit)
        {
            if (!lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3))
                return response(lua, false, "shop_sale_exact_quote_required");
            const auto quantity = lua.get_integer(), price = lua.get_integer(), before = lua.get_integer();
            if (quantity < 1 || quantity > max_money || price < 0 || price > max_money || before < 0 || before > max_money)
                return response(lua, false, "shop_sale_exact_quote_required");
            units = static_cast<int>(quantity);
            quoted_value = static_cast<int>(price);
            wallet_before = static_cast<int>(before);
        }

        Context context;
        if (const auto state = bind_context(input, context); !state.empty()) return response(lua, false, state);
        if (context.camera_only) return response(lua, false, "shop_stock_sales_unavailable");
        Layout layout;
        Sale sale;
        Wallet wallet;
        if (!layout.bind(context) || !layout.verify_saved(context, false) || !sale.bind(context, layout) || !wallet.bind(context.instance))
            return response(lua, false, "shop_sale_stock_schema_unavailable");
        if (!read_bool(context.shop, STR("CanSellTo"), true) || !read_bool(context.shop, STR("LimitedCash"), true))
            return response(lua, false, "shop_stock_sales_unavailable");
        if (!sale.preserves_reputation(context)) return response(lua, false, "shop_sale_stock_reputation_path_unavailable");
        const int money = wallet.amount(context.instance), cash = layout.merchant_cash->GetPropertyValueInContainer(context.shop);
        if (money < 0 || cash < 0) return response(lua, false, "shop_sale_wallet_unavailable");

        auto* source = layout.backpack->ContainerPtrToValuePtr<void>(context.guest);
        auto* target = layout.buyback->ContainerPtrToValuePtr<void>(context.shop);
        auto* finite = layout.finite->ContainerPtrToValuePtr<void>(context.shop);
        Unreal::FScriptArrayHelper backpack(layout.backpack, source), buyback(layout.buyback, target), finite_rows(layout.finite, finite);
        const int old_size = backpack.Num(), old_buyback = buyback.Num();
        if (old_size < 1 || old_size > max_items || old_buyback < 0 || old_buyback >= max_offers ||
            finite_rows.Num() < 0 || finite_rows.Num() > max_offers ||
            (old_buyback && buyback.GetRawPtr() == backpack.GetRawPtr()))
            return response(lua, false, "shop_sale_stock_or_guest_capacity_unavailable");

        int removed = -1;
        for (int i = 0; i < old_size; ++i)
        {
            if (layout.identity(backpack.GetRawPtr(i)) != chosen) continue;
            if (removed >= 0) return response(lua, false, "shop_sale_guest_guid_ambiguous");
            removed = i;
        }
        if (removed < 0) return response(lua, false, "shop_sale_guest_backpack_item_unavailable");
        if (layout.owner_has(context.stock, chosen)) return response(lua, false, "shop_sale_canonical_inventory_guid_conflict");
        Unreal::FScriptArrayHelper equipment(layout.equipment, layout.equipment->ContainerPtrToValuePtr<void>(context.guest));
        if (equipment.Num() < 0 || equipment.Num() > max_items ||
            layout.identity(layout.armor->ContainerPtrToValuePtr<void>(context.guest)) == chosen ||
            layout.identity(layout.helmet->ContainerPtrToValuePtr<void>(context.guest)) == chosen)
            return response(lua, false, "shop_sale_guest_equipment_guid_conflict");
        for (int i = 0; i < equipment.Num(); ++i)
            if (layout.identity(equipment.GetRawPtr(i)) == chosen) return response(lua, false, "shop_sale_guest_equipment_guid_conflict");

        const auto* source_row = backpack.GetRawPtr(removed);
        Offer selected;
        if (const auto state = item_offer(layout, source_row, selected); !state.empty()) return response(lua, false, state);
        selected.price = selected.value;
        if (selected.klass->GetName() == STR("Art_BlackStone_C") || sale.loaded(context, source_row))
            return response(lua, false, "shop_sale_stock_item_requires_special_handling");
        if (selected.price > cash || selected.price > max_money - money)
            return response(lua, false, "shop_sale_merchant_funds_unavailable");

        Unreal::FScriptArrayHelper infinite(sale.infinite, sale.infinite->ContainerPtrToValuePtr<void>(context.shop));
        if (infinite.Num() < 0 || infinite.Num() > max_items)
            return response(lua, false, "shop_sale_stock_inventory_unavailable");
        for (auto* stock : {&finite_rows, &infinite, &buyback})
            for (int i = 0; i < stock->Num(); ++i)
                if (layout.identity(stock->GetRawPtr(i)) == chosen)
                    return response(lua, false, "shop_sale_merchant_guid_conflict");
        if (selected.stackable)
        {
            for (int i = 0; i < old_buyback; ++i)
            {
                auto* row = buyback.GetRawPtr(i);
                if (layout.type->GetObjectPropertyValue(layout.type->ContainerPtrToValuePtr<void>(row)) == selected.klass)
                    return response(lua, false, "shop_sale_buyback_merge_unavailable");
            }
        }

        if constexpr (!Commit) return response(lua, true, "sale-quote", encode(selected), money, money, cash, cash);
        if (chosen != selected.guid || units != selected.quantity || quoted_value != selected.price || wallet_before != money)
            return response(lua, false, "shop_sale_quote_changed");
        int day{};
        if (!sale.day(context, day)) return response(lua, false, "shop_sale_stock_day_unavailable");
        auto* saved = layout.saved_shops->ContainerPtrToValuePtr<void>(context.stock);
        auto* flags = layout.flags->ContainerPtrToValuePtr<void>(context.stock);
        auto source_before = copy(layout.backpack, source);
        auto target_before = copy(layout.buyback, target);
        auto finite_before = copy(layout.finite, finite);
        auto saved_before = copy(layout.saved_shops, saved);
        auto flags_before = copy(layout.flags, flags);
        auto transferred = copy(layout.row, source_row);
        if (!source_before || !target_before || !finite_before || !saved_before || !flags_before || !transferred)
            return response(lua, false, "shop_sale_before_image_unavailable");

        const auto inserted = sale.add_buyback(context, transferred.get(), old_buyback);
        bool verified = buyback.Num() == old_buyback + 1 && inserted >= 0 && inserted < buyback.Num() &&
            sale.matches_buyback(layout, transferred.get(), buyback.GetRawPtr(inserted), day) &&
            layout.equal(layout.finite, finite, layout.finite, finite_before.get(), max_offers) &&
            layout.equal(layout.backpack, source, layout.backpack, source_before.get(), max_items);
        Unreal::FScriptArrayHelper previous_buyback(layout.buyback, target_before.get());
        for (int i = 0; verified && i < old_buyback; ++i)
            verified = layout.ident(previous_buyback.GetRawPtr(i), buyback.GetRawPtr(i + (i >= inserted ? 1 : 0)));
        if (verified)
        {
            backpack.RemoveValues(removed);
            wallet.change(context.instance, selected.price, false);
            layout.merchant_cash->SetPropertyValueInContainer(context.shop, cash - selected.price);
            context.shop->ProcessEvent(layout.save, nullptr);
            Unreal::FScriptArrayHelper previous_bag(layout.backpack, source_before.get());
            if (exact_removal(layout, backpack, previous_bag, removed) && !layout.owner_has(context.guest, chosen) &&
                wallet.amount(context.instance) == money + selected.price && qualified_connection(context) &&
                read_object(context.pawn, STR("Save")) == context.guest &&
                read_object(context.instance, STR("SaveSlot_Ref")) == context.stock && layout.verify_saved(context, true))
                return response(lua, true, "sold", encode(selected), money, money + selected.price, cash, cash - selected.price);
        }

        layout.backpack->CopyCompleteValue(source, source_before.get());
        layout.buyback->CopyCompleteValue(target, target_before.get());
        layout.finite->CopyCompleteValue(finite, finite_before.get());
        layout.saved_shops->CopyCompleteValue(saved, saved_before.get());
        layout.flags->CopyCompleteValue(flags, flags_before.get());
        layout.merchant_cash->SetPropertyValueInContainer(context.shop, cash);
        if (!layout.equal(layout.backpack, source, layout.backpack, source_before.get(), max_items) ||
            !layout.equal(layout.buyback, target, layout.buyback, target_before.get(), max_offers) ||
            !layout.equal(layout.finite, finite, layout.finite, finite_before.get(), max_offers) ||
            !layout.flags->Identical(flags, flags_before.get()) || !layout.saved_shops->Identical(saved, saved_before.get()) ||
            wallet.amount(context.instance) != money || layout.merchant_cash->GetPropertyValueInContainer(context.shop) != cash)
            return response(lua, false, "mutation_unverified_shop_sale_restore");
        return response(lua, false, "shop_sale_readback_failed_rolled_back");
    }
}

namespace silverfish::finite_shop
{
    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishReadNativeFiniteShop", transact<false>);
        lua.register_function("SilverfishBuyNativeFiniteShop", transact<true>);
        lua.register_function("SilverfishBuyNativeShopQuantity", transact<true, true>);
        lua.register_function("SilverfishQuoteNativeFiniteShopSale", sell<false>);
        lua.register_function("SilverfishSellNativeFiniteShop", sell<true>);
    }
}
