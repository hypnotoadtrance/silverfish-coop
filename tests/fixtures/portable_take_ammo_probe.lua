    if fields[2] == "fixture-stock-take-ammo-seed" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "hosting" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P" and #fields == 4)
        local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
        assert(pawn and save and not same(save, state.world.OwningGameInstance.SaveSlot_Ref))
        local kind = fields[4]
        assert(kind == "revolver-9mm" or kind == "break-action-50")
        local path = weapon_profiles.paths[kind]
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path) class = StaticFindObject(path) end
        local converter = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
        assert(class:IsValid() and converter:IsValid())
        local defaults, converted = class:GetCDO(), {}
        converter:ItemActorToStruct(defaults, pawn, converted)
        local row = converted.Item or converted
        assert(same(row[class_field], class))
        row[ammo_field] = 1
        local index = save.PlayerBackpack:GetArrayNum() + 1
        save.PlayerBackpack[index] = row
        save.PlayerBackpack[index]["DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"] = defaults.DataStruct
        return assert(item_identity(save.PlayerBackpack[index]))
    end
