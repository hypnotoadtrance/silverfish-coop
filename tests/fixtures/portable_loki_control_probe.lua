    if fields[2] == "fixture-loki-guest" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "connected" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P")
        local pawn = assert(state.local_pawn.pawn)
        local guid, kind = equipped_guid(pawn, pawn.Save)
        if guid ~= fields[3] then return "waiting" end
        local held = pawn.EquipedItem
        local child = loki_child(held, pawn, kind)
        if not child then return "waiting" end
        if fields[4] == "toggle" then
            held:ToggleMode(true)
            held:ToggleMode(false)
        end
        return ("%d,%d,%s"):format(held.Ammo, child.Ammo, same(child, held.InputIntercept) and "loki" or "primary")
    end
    if fields[2] == "fixture-loki-reserve" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "hosting" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P")
        local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
        assert(pawn and save and not same(save, state.world.OwningGameInstance.SaveSlot_Ref))
        local path = weapon_profiles.ammo["bolt-action-shotgun-loki"]
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path); class = StaticFindObject(path) end
        local converter = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
        local converted = {}
        converter:ItemActorToStruct(class:GetCDO(), pawn, converted)
        local row = converted.Item or converted
        assert(same(row[class_field], class))
        row[ammo_field] = 1
        save.PlayerBackpack[save.PlayerBackpack:GetArrayNum() + 1] = row
        return "seeded"
    end
    if fields[2] == "fixture-loki-stock" and headless
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(phase == "hosting" and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P")
        local pawn = assert(state.local_pawn.pawn)
        local actions = {Wep_AR_08 = "UnderBarrel_Loki_BP", Wep_HCR_Loki = "UnderBarrel_Loki_BP",
            Wep_BoltAction_Shotgun_Loki = "ChildActor1"}
        local name, verb = fields[3], fields[4]
        local child_field = assert(actions[name])
        local path = "/Game/Classes/Weapons/" .. name .. "." .. name .. "_C"
        if verb == "equip" then
            local class = StaticFindObject(path)
            if not class or not class:IsValid() then LoadAsset(path); class = StaticFindObject(path) end
            local converter = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
            assert(class:IsValid() and converter:IsValid())
            local defaults, converted = class:GetCDO(), {}
            converter:ItemActorToStruct(defaults, pawn, converted)
            local row, save = converted.Item or converted, pawn.Save
            assert(same(row[class_field], class))
            local slot = save.PlayerEquipIndex == 3 and 4 or 3
            save.PlayerEquipment[slot + 1] = row
            save.PlayerEquipment[slot + 1]["DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"] = defaults.DataStruct
            local facing = pawn.Controller:GetControlRotation()
            pawn.Controller:SetControlRotation({Pitch = 70, Yaw = facing.Yaw, Roll = 0})
            pawn:Equip_NextItem(slot, false)
            return "equipping"
        end
        local receiver = pawn.EquipedItem
        if not receiver or not receiver:IsValid() or not receiver:GetClass():GetFullName():find(path, 1, true) then
            return "waiting"
        end
        local mount = receiver[child_field]
        local child = mount and mount.ChildActor
        if not child or not child:IsValid() or not same(child.ParentWeapon, receiver) or not same(child.User, pawn) then
            return "waiting"
        end
        if verb == "toggle" then receiver:ToggleMode(true) end
        if verb == "press" then receiver:Use_Item_Primary(true, 0) end
        if verb == "release" then receiver:Use_Item_Primary(false, 0) end
        if verb == "reload" then
            local reserve_path = "/Game/Classes/Items/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C"
            local reserve = StaticFindObject(reserve_path)
            if not reserve or not reserve:IsValid() then LoadAsset(reserve_path); reserve = StaticFindObject(reserve_path) end
            local converter = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
            local converted = {}
            converter:ItemActorToStruct(reserve:GetCDO(), pawn, converted)
            local reserve_row = converted.Item or converted
            reserve_row[ammo_field] = 1
            local bag = pawn.Save.PlayerBackpack
            bag[bag:GetArrayNum() + 1] = reserve_row
            receiver:Reload()
        end
        local save = pawn.Save
        local row = save.PlayerEquipment[save.PlayerEquipIndex + 1]
        local item = "Ammo_Item_9_DC1291D94AB26C3244B369B14FA38582"
        local function ammo_name(actor)
            local class = actor.AmmoType[item]
            return class and class:IsValid() and (class:GetFullName():match("[^%.]+$")) or "-"
        end
        local fields_out = {receiver.MagSize, receiver.BurstLength, receiver.FireRate, receiver.Action_OnRelease,
            receiver.Ammo, row[ammo_field], ammo_name(receiver), child.MagSize, child.BurstLength, child.FireRate,
            child.Action_OnRelease, child.SingleReload, child.Ammo, ammo_name(child), receiver.Tertiary_Mode,
            same(receiver.InputIntercept, child) or false, row["DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"]:ToString(),
            child.Reloading, child:GetClass():GetFullName():match("[^%.]+$")}
        for i, value in ipairs(fields_out) do fields_out[i] = tostring(value) end
        return table.concat(fields_out, "|")
    end
