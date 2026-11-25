    local dust_fixture = fields[2]:match("^fixture%-stock%-stardust%-(%l+)$")
    if dust_fixture and os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local function valid(object)
            return object and object:IsValid()
        end
        local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        local dust = "/Game/Classes/Items/Artifacts/Art_DreamDust.Art_DreamDust_C"
        local inventory = StaticFindObject("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")
        local owner = state.local_pawn and state.local_pawn.pawn
        assert(state.map == map and valid(owner) and owner:IsLocallyControlled(), "fixture requires the Crossroads player")
        if dust_fixture == "seed" then
            assert(phase == "hosting" and owner:HasAuthority() and #fields == 3)
            local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
            assert(valid(pawn) and valid(save) and not same(save, owner.Save))
            local class = StaticFindObject(dust)
            if not valid(class) then LoadAsset(dust); class = StaticFindObject(dust) end
            assert(valid(class))
            local stock = class:GetCDO()
            local converter = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
            local out = {}
            converter:ItemActorToStruct(stock, pawn, out)
            local row = out.Item or out
            assert(same(row[class_field], class) and stock.Stacks and row[ammo_field] == 1)
            row[ammo_field] = 2
            row[weight_field] = 2 * stock.Weight
            row["Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"] = 2 * stock.Value
            local index = save.PlayerBackpack:GetArrayNum() + 1
            save.PlayerBackpack[index] = row
            save.PlayerBackpack[index]["DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"] = stock.DataStruct
            return assert(item_identity(save.PlayerBackpack[index]))
        end
        assert(phase == "hosting" or phase == "connected")
        local controller = owner.Controller
        assert(valid(controller) and same(controller.Pawn, owner) and controller:IsLocalController())
        if dust_fixture == "projected" then
            assert(phase == "connected" and #fields == 3 and valid(owner.Save))
            local backpack = owner.Save.PlayerBackpack
            for index = 1, backpack:GetArrayNum() do
                if item_identity(backpack[index]) == fields[3] then return "present" end
            end
            return "waiting"
        end
        local stack = controller.MenuStack
        if dust_fixture == "close" then
            assert(#fields == 2 and phase == "hosting")
            if stack:GetArrayNum() > 0 then controller:CloseMenu({}) end
            assert(stack:GetArrayNum() == 0)
            return "closed"
        end
        assert(#fields == 3 and fields[3]:match("^[0-9A-F]+$") and #fields[3] == 32)
        if stack:GetArrayNum() == 0 and phase == "hosting" then
            local input = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Inventory.IA_Inventory")
            controller:InpActEvt_IA_Inventory_K2Node_EnhancedInputActionEvent_0({}, 0, 0, input)
        end
        assert(stack:GetArrayNum() == 1, "fixture requires the stock inventory")
        local screen = stack[1]
        if type(screen.type) == "function" and screen:type() == "RemoteUnrealParam" then screen = screen:get() end
        assert(same(screen:GetClass(), inventory) and same(screen:GetOwningPlayer(), controller))
        local manager, cards = screen.ItemManager, screen.BackpackPanel.Cards
        local card
        for index = 1, cards:GetArrayNum() do
            local object = cards[index]
            if type(object.type) == "function" and object:type() == "RemoteUnrealParam" then object = object:get() end
            if valid(object) and item_identity(object.Item) == fields[3] then
                assert(not card)
                card = object
            end
        end
        if not card then return "waiting" end
        assert(card.Item[class_field]:GetFullName() == "BlueprintGeneratedClass " .. dust and card.Item[ammo_field] == 2)
        local menu = manager.ItemContextMenu
        if menu:IsVisible() then manager:CloseContextMenu() end
        manager:OpenContextMenu(card)
        assert(same(menu["Item Card"], card))
        if dust_fixture == "drop" then
            assert(phase == "connected" and menu.Button_Drop:IsVisible())
            manager:ActionDrop()
        else
            assert(dust_fixture == "use")
            manager:ActionUse()
        end
        return "pressed"
    end
