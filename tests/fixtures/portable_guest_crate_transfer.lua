    if fields[2] == "fixture-streamed-crate" and title_fixtures and os.getenv("SILVERFISH_COOP_HEADLESS") == "1" then
        local mode, world = fields[3], state.world
        local stream = actions.crate_stream
        assert(state.listening and state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P")
        if mode == "load" then
            assert(not valid_object(stream))
            local loader = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
            stream = loader:LoadLevelInstance(world, "/Game/Classes/Maps/Maps_Dungeons/Cistern_P",
                {X = 45000, Y = 45000, Z = 2000}, {Pitch = 0, Yaw = 0, Roll = 0}, {}, "CoopCrateSublevel", nil, false)
            assert(valid_object(stream))
            actions.crate_stream = stream
            return "loading"
        end
        assert(valid_object(stream))
        local level = stream:GetLoadedLevel()
        if mode == "unload" then
            stream:SetShouldBeVisible(false)
            stream:SetShouldBeLoaded(false)
            return "unloading"
        elseif mode == "reload" then
            assert(not valid_object(level))
            stream:SetShouldBeLoaded(true)
            stream:SetShouldBeVisible(true)
            return "loading"
        elseif mode == "loaded" then
            return tostring(not not valid_object(level))
        end
        if not stream:IsLevelVisible() or not valid_object(level) then return "absent" end
        local path = assert(level:GetFullName():match("^%S+ (.+)$")) .. ".LootCrate_Small_BP_C_1"
        local crate = StaticFindObject(path)
        if not valid_object(crate) then return "absent" end
        if mode == "observe" then
            return table.concat({"LootCrate_Small_BP_C " .. state.map .. "|" .. path, tostring(crate.Looted)}, ";")
        end
        assert(mode == "interact" and phase == "connected" and crate.Looted == false)
        assert(require("native_loot_crate_input").stock_focus(state.local_pawn.pawn, crate.Box))
        return "pressed"
    end
    if fields[2] == "fixture-stock-crate-transfer" and title_fixtures then
        local mode, requested = fields[3], fields[4]
        local profile = assert(loot.profile(state.map, requested), "fixture requires an exact stock crate")
        local crate = StaticFindObject(profile.path)
        local api, class = StaticFindObject(statics), StaticFindObject(save)
        local function same(left, right)
            return valid_object(left) and valid_object(right) and left:GetAddress() == right:GetAddress()
        end
        assert(valid_object(crate) and same(crate:GetWorld(), state.world) and valid_object(api) and valid_object(class))
        if mode == "state" then
            return table.concat({crate:GetFullName(), tostring(crate:HasAuthority()), tostring(crate.bReplicates),
                tostring(crate.SpawnSalt), tostring(crate.Looted), tostring(crate.SpawnedValue)}, "|")
        end
        if mode == "observe" and phase == "hosting" then
            assert(crate:HasAuthority() and not crate.Looted and crate.SpawnedValue == 0)
            assert(not loot.lucky_looter(state.local_pawn.pawn.Save))
            return "host crate unopened"
        end
        if mode == "give-trait" and phase == "connected" then
            local local_save = assert(require("native_save_scope").bind_guest(state, phase))
            local character = local_save.Character
            character.PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00 = {FName("LuckyLooter", EFindName.FNAME_Find)}
            local_save.Character = character
            local lucky, source = loot.lucky_looter(local_save)
            assert(lucky and source == "permanent", "the guest's local trait did not apply")
            return source
        end
        if mode == "roll" and phase == "connected" then
            local local_save, pawn = require("native_save_scope").bind_guest(state, phase)
            assert(valid_object(local_save) and valid_object(pawn) and crate.Looted == false)
            local lucky, source = loot.lucky_looter(local_save)
            local reply = table.pack(SilverfishOpenNativeGuestLootCrate(profile.path, crate:GetAddress(), pawn:GetAddress(),
                crate.Box:GetAddress(), local_save:GetAddress(), state.world.OwningGameInstance.SaveSlot_Ref:GetAddress(), lucky))
            assert(reply[1] == true, tostring(reply[2]))
            assert(reply[3] and reply[7] and crate.Looted and same(crate.Player, pawn))
            local outputs = {}
            for slot = 1, profile.capacity do outputs[slot] = reply[slot <= 2 and slot + 3 or slot + 5] end
            local spawned = loot.results({crate = crate, world = state.world}, outputs)
            local pool = api:CreateSaveGameObject(class)
            local receipt = table.pack(SilverfishCaptureNativeLootEscrow(crate:GetAddress(), pool:GetAddress(), table.unpack(outputs)))
            assert(receipt[1] == true, tostring(receipt[2]))
            local ok, reason, rows = SilverfishExportNativeLootEscrow(pool:GetAddress())
            assert(ok and #rows == #spawned and #rows == receipt[3], tostring(reason))
            for index, item in ipairs(spawned) do
                assert(rows[index].class_path == item.class_path and item.object:GetActorEnableCollision())
            end
            actions.transfer_fixture = { world = state.world, crate = requested, rows = rows }
            return table.concat({#rows, lucky and "1" or "0", source, tostring(reply[6])}, "|")
        elseif mode == "read" and phase == "connected" then
            local transfer = actions.transfer_fixture
            assert(transfer and same(transfer.world, state.world) and transfer.crate == requested)
            local row = assert(transfer.rows[tonumber(fields[5])], "fixture row unavailable")
            local delimiter = "@@fixture-escrow@@"
            assert(not row.text:find(delimiter, 1, true))
            return table.concat({row.guid, row.class_path, row.text}, delimiter)
        elseif mode == "import" and phase == "hosting" then
            assert(not crate.Looted and crate.SpawnedValue == 0 and #fields <= 4 + 3 * profile.capacity)
            local pool = api:CreateSaveGameObject(class)
            local ok, reason = SilverfishImportNativeLootEscrow(pool:GetAddress(), profile.capacity, table.unpack(fields, 5))
            if not ok then
                assert(#pool.PlayerBackpack == 0, "failed import left a partial escrow")
                error(reason, 0)
            end
            local exported, detail, rows = SilverfishExportNativeLootEscrow(pool:GetAddress())
            assert(exported, tostring(detail))
            for index, row in ipairs(rows) do
                local argument = 5 + 3 * (index - 1)
                assert(row.guid == fields[argument] and row.class_path == fields[argument + 1] and row.text == fields[argument + 2])
            end
            return "host imported " .. #rows .. " typed rows"
        end
        error("unsupported crate transfer fixture", 0)
    end
