    if fields[2] == "fixture-stock-quest-placement" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local map = "/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P"
        local world_path = map .. ".Shop_Smuggler_P"
        local source = world_path .. ":PersistentLevel.Case_HandOff_01_C_1"
        local class = "/Game/Classes/Items/Key_Items/Case_HandOff_01.Case_HandOff_01_C"
        local quest = "Clct_HandOff_01"
        local operation = fields[3]
        assert(#fields == 3 and state.listening and state.map == map and valid(state.world)
            and state.world:GetFullName() == "World " .. world_path, "expected joined Smuggler Shop")
        local pawn = state.local_pawn and state.local_pawn.pawn
        assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), state.world))
        assert(type(SilverfishReadNativeAuthoredQuestPlacements) == "function", "native placement observer unavailable")
        if operation == "guest" then
            assert(phase == "connected" and state.server == true)
            assert(SilverfishReadNativeAuthoredQuestPlacements(pawn:GetAddress()) == nil)
            return "guest scope rejected"
        end

        assert(phase == "hosting" and state.server == false and pawn:HasAuthority())
        local actor = StaticFindObject(source)
        assert(valid(actor) and same(actor:GetClass(), StaticFindObject(class))
            and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
            and actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) and not actor:IsActorBeingDestroyed()
            and same(actor.Box, actor.RootComponent), "exact placed Metal Briefcase unavailable")
        local instance = state.world.OwningGameInstance
        assert(valid(instance) and valid(pawn.Save) and same(instance.SaveSlot_Ref, pawn.Save))
        local key = FName(quest, EFindName.FNAME_Find)
        assert(key:GetComparisonIndex() ~= 0 and actor.Quest_ID:ToString() == quest)
        local active = pawn.Save.ActiveQuests
        local step = active:Contains(key) and active:Find(key):get().Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A or 0
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
        local at, angle, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
        local observed = {at.X, at.Y, at.Z, angle.Pitch, angle.Yaw, angle.Roll, scale.X, scale.Y, scale.Z}
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
                assert((at.X + 2698.7365)^2 + (at.Y - 6589.5794)^2 + (at.Z + 325.2872)^2 <= 1,
                    "stock case did not return to its authored location")
                matched = true
            end
        end
        local after = actor:K2_GetActorLocation()
        assert(hidden == actor.bHidden and collision == actor:GetActorEnableCollision()
            and at.X == after.X and at.Y == after.Y and at.Z == after.Z, "readback changed the stock case")
        return ("%d|%s|%s|%s|%.4f|%.4f|%.4f"):format(step, tostring(matched), tostring(hidden), tostring(collision),
            at.X, at.Y, at.Z)
    end
