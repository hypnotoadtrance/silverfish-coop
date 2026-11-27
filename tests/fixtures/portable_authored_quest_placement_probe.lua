    if fields[2] == "fixture-stock-quest-placement" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
        local world_path = map .. ".Shop_Smuggler_P"
        local source = world_path .. ":PersistentLevel.Case_HandOff_01_C_1"
        local class = "/Game/Classes/Items/Key_Items/Case_HandOff_01.Case_HandOff_01_C"
        local quest = "Clct_HandOff_01"
        local operation = fields[3]
        local inventory = operation == "guest-item"
        assert(#fields == (inventory and 4 or 3) and state.listening and state.map == map and valid(state.world)
            and state.world:GetFullName() == "World " .. world_path, "expected joined Smuggler Shop")
        local pawn = state.local_pawn and state.local_pawn.pawn
        assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), state.world))
        assert(type(SilverfishReadNativeAuthoredQuestPlacements) == "function", "native placement observer unavailable")
        local client = phase == "connected" and state.server == true
        if client then
            assert(SilverfishReadNativeAuthoredQuestPlacements(pawn:GetAddress()) == nil)
            if operation == "guest" then return "guest scope rejected" end
            if inventory then
                local id = fields[4]
                assert(#id == 32 and id:match("^[0-9A-F]+$"))
                local stack = state.controller.MenuStack
                assert(stack:GetArrayNum() == 1, "stock guest inventory is not open")
                local screen = stack[1]
                assert(valid(screen) and same(screen:GetClass(),
                    StaticFindObject("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")))
                local cards, found = screen.BackpackPanel.Cards, 0
                local item_type = StaticFindObject(class)
                for index = 1, cards:GetArrayNum() do
                    local card = cards[index]
                    if card and type(card.type) == "function" and card:type() == "RemoteUnrealParam" then
                        card = card:get()
                    end
                    if valid(card) and same(card.Item.Item_Class_3_2813641E42AA35FC0693029E4EE49F81, item_type) then
                        local guid = card.Item.GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A
                        assert(("%08X%08X%08X%08X"):format(
                            guid.A & 0xffffffff, guid.B & 0xffffffff, guid.C & 0xffffffff, guid.D & 0xffffffff) == id)
                        assert(math.max(1, card.Item.Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788) == 1)
                        found = found + 1
                    end
                end
                assert(found == 1, "stock guest backpack does not show exactly one sealed briefcase")
                return "stock backpack shows the exact sealed case"
            end
            assert(operation == "stock-state")
        else
            assert(phase == "hosting" and state.server == false and pawn:HasAuthority())
        end

        local function actor_state(actor)
            local at, angle, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
            return {at.X, at.Y, at.Z, angle.Pitch, angle.Yaw, angle.Roll, scale.X, scale.Y, scale.Z}
        end

        local function format_state(hidden, collision, pose)
            return ("%s|%s|%.4f|%.4f|%.4f|%.4f|%.4f|%.4f|%.6f|%.6f|%.6f"):format(
                tostring(hidden), tostring(collision), table.unpack(pose))
        end

        local key = FName(quest, EFindName.FNAME_Find)
        assert(key:GetComparisonIndex() ~= 0)
        local instance, active, step
        if not client and operation ~= "stock-state" then
            instance = state.world.OwningGameInstance
            assert(valid(instance) and valid(pawn.Save) and same(instance.SaveSlot_Ref, pawn.Save))
            active = pawn.Save.ActiveQuests
            step = active:Contains(key) and active:Find(key):get().Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A or 0
            if operation == "quest-step" then return tostring(step) end
        end

        local actor = StaticFindObject(source)
        if operation == "stock-state" and not valid(actor) then return "absent" end
        assert(valid(actor) and same(actor:GetClass(), StaticFindObject(class))
            and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
            and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) and not actor:IsActorBeingDestroyed()
            and same(actor.Box, actor.RootComponent), "exact placed Metal Briefcase unavailable")
        assert(actor.Quest_ID:ToString() == quest)
        if operation == "stock-state" then
            return format_state(actor.bHidden, actor:GetActorEnableCollision(), actor_state(actor))
        end
        if operation == "activate" then
            local library = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C")
            assert(valid(library) and step == 0, "activation requires the dormant stock quest")
            instance:AddQuest_ByName(key)
            assert(active:Contains(key)
                and active:Find(key):get().Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A == 1)
            library:GlobalPing("Ping_NewQuest", pawn, instance)
            return "stock quest activated"
        end

        assert(operation == "snapshot")
        local observed = actor_state(actor)
        local hidden, collision = actor.bHidden, actor:GetActorEnableCollision()
        local rows = SilverfishReadNativeAuthoredQuestPlacements(pawn:GetAddress())
        if rows == nil then return "census unavailable" end
        assert(type(rows) == "string")
        local matched = false
        for line in rows:gmatch("[^\n]+") do
            local row = {}
            for value in (line .. "\t"):gmatch("(.-)\t") do row[#row + 1] = value end
            if row[3] == source or row[4] == quest then
                assert(not matched and #row == 14 and row[1] == "native-authored-quest-placement"
                    and row[2] == world_path and row[3] == source and row[4] == quest and row[5] == class)
                assert(step == 1 and not hidden and collision, "inactive Metal Briefcase appeared in native census")
                for index = 1, #observed do
                    assert(math.abs(observed[index] - assert(tonumber(row[index + 5]))) <= 0.001,
                        "native placement differs from the stock actor")
                end
                assert((observed[1] + 2698.7365)^2 + (observed[2] - 6589.5794)^2 + (observed[3] + 325.2872)^2 <= 1,
                    "stock case did not return to its authored location")
                matched = true
            end
        end
        local after = actor:K2_GetActorLocation()
        assert(hidden == actor.bHidden and collision == actor:GetActorEnableCollision()
            and observed[1] == after.X and observed[2] == after.Y and observed[3] == after.Z,
            "readback changed the stock case")
        return ("%d|%s|%s"):format(step, tostring(matched), format_state(hidden, collision, observed))
    end
