    if fields[2] == "fixture-stock-split-bandage" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" and phase == "hosting" then
        local guest = assert(state.guests[1]).pawn
        local save = guest.Save
        assert(guest:HasAuthority() and not guest:IsLocallyControlled() and save:IsValid()
            and save:GetAddress() ~= state.world.OwningGameInstance.SaveSlot_Ref:GetAddress())
        local path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
        local class = StaticFindObject(path)
        if not class:IsValid() then LoadAsset(path); class = StaticFindObject(path) end
        local factory = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
        local output = {}
        factory:MakeItemStack(class, 9, guest, output)
        local item = output.Item or output
        local id = item.GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A
        assert(item.Item_Class_3_2813641E42AA35FC0693029E4EE49F81:GetAddress() == class:GetAddress()
            and item.Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788 == 9)
        local pack = save.PlayerBackpack
        pack[pack:GetArrayNum() + 1] = item
        return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
    end
