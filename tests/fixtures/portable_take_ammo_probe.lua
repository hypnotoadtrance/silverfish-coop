    if fields[2] == "fixture-stock-take-ammo-loadout" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "connected" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P" and #fields == 5)
        local pawn = state.local_pawn.pawn
        local save, slot = pawn.Save, math.tointeger(tonumber(fields[3]))
        assert(slot and slot >= 0 and slot < 8)
        local row = save.PlayerEquipment[slot + 1]
        if item_identity(row) ~= fields[4] then return "waiting" end
        if fields[5] == "equip" then
            pawn:Equip_NextItem(slot, false)
            local facing = pawn.Controller:GetControlRotation()
            pawn.Controller:SetControlRotation({Pitch = 70, Yaw = facing.Yaw, Roll = 0})
        end
        local guid = equipped_guid(pawn, save)
        if fields[5] == "ammo" then
            if guid ~= fields[4] then return "waiting" end
            local class = pawn.EquipedItem.AmmoType["Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582"]
            return assert(class:GetFullName():match("^%S+ (%S+)$"))
        end
        return ("%d,%s"):format(row[ammo_field], guid == fields[4] and tostring(pawn.EquipedItem.Ammo) or "stored")
    end
    if fields[2] == "fixture-stock-take-ammo-seed" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "hosting" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P" and #fields == 4)
        local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
        assert(pawn and save and not same(save, state.world.OwningGameInstance.SaveSlot_Ref))
        local path = weapon_profiles.paths[fields[4]]
        assert(path and weapon_profiles.magazine_capacity[path])
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
    if fields[2] == "fixture-stock-take-ammo-select-round" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "hosting" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P" and #fields == 5)
        local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
        local guid, kind = equipped_guid(pawn, save)
        local path = fields[5]
        assert(guid == fields[4] and weapon_profiles.ammo_options[kind] and weapon_profiles.ammo_options[kind][path])
        local ammo = StaticFindObject(path)
        if not ammo or not ammo:IsValid() then LoadAsset(path) ammo = StaticFindObject(path) end
        assert(ammo:IsValid())
        local held = pawn.EquipedItem
        local rounds = held.Ammo
        assert(rounds > 0)
        held:ChangeAmmoType(ammo:GetCDO().AmmoType)
        held.Ammo = rounds
        held:Update_Mag()
        return "stock round loaded"
    end
