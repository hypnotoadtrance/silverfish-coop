    if fields[2] == "fixture-streamed-switch" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local mode = fields[3]
        local fixture = switches.stream_fixture
        assert(state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P" and state.listening)
        if mode == "load" then
            assert(not fixture or not same(fixture.world, state.world))
            local loading = StaticFindObject("/Script/Engine.Default__LevelStreamingDynamic")
            local stream = loading:LoadLevelInstance(state.world, "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P",
                {X = 45000, Y = 45000, Z = 2000}, {Pitch = 0, Yaw = 0, Roll = 0}, {}, "CoopSwitchSublevel", nil, false)
            assert(valid(stream))
            switches.stream_fixture = {world = state.world, stream = stream}
            return "loading"
        end
        assert(fixture and same(fixture.world, state.world))
        if mode == "reader" then
            local read = assert(SilverfishReadNativeSwitchPower, "native switch reader unavailable")
            local world_address = state.world:GetAddress()
            if valid(fixture.stream) and fixture.stream:IsLevelVisible() then
                local entry = assert(find_switch(state, phase, fields[5]), "streamed switch missing from the census")
                assert(same(entry.level, fixture.stream:GetLoadedLevel()))
                fixture.reader_address, fixture.reader_path = entry.address, entry.path
                local powered = read(world_address, entry.address, entry.path, "Powered", false)
                assert(type(powered) == "boolean" and powered == entry.actor.Powered,
                    "native streamed switch disagrees with the stock power state")
                assert(read(world_address, entry.address, entry.path .. "_missing", "Powered", false) == nil,
                    "native streamed switch accepted a different path for the cached address")
                return bit(powered)
            end
            assert(fixture.reader_address and fixture.reader_path)
            assert(read(world_address, fixture.reader_address, fixture.reader_path, "Powered", false) == nil,
                "native streamed switch accepted a hidden or unloaded actor")
            return "refused"
        end
        assert(valid(fixture.stream))
        if mode == "hide" or mode == "show" or mode == "unload" then
            fixture.stream:SetShouldBeVisible(mode == "show")
            if mode == "unload" then fixture.stream:SetShouldBeLoaded(false) end
            return mode
        end
        if not fixture.stream:IsLevelVisible() then return "waiting" end
        if mode == "discover" or mode == "diagnose" then
            local statics, class = StaticFindObject(statics_path), StaticFindObject(profiles.CircutBox_BP_C.path)
            local found = {}
            statics:GetAllActorsOfClass(state.world, class, found)
            local scope = fixture.stream:GetLoadedLevel()
            for _, actor in ipairs(found.OutActors or found) do
                if type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                if valid(actor) and same(actor:GetOuter(), scope)
                    and actor:GetFullName():match(":PersistentLevel%.CircutBox_BP_C_1$") then
                    if mode == "diagnose" then
                        return table.concat({actor:GetFullName(), "world=" .. tostring(same(actor:GetWorld(), state.world)),
                            "authority=" .. tostring(actor:HasAuthority()), "visible=" .. tostring(scope.bIsVisible),
                            "qualified=" .. tostring(qualified(actor, state.world) ~= nil),
                            "visible-in-census=" .. tostring(visible_streams and visible_streams[scope:GetAddress()] ~= nil),
                            "cached=" .. tostring(entries[actor:GetFullName()] ~= nil)}, ";")
                    end
                    return actor:GetFullName()
                end
            end
            error("the streamed level did not contain the stock circuit box", 0)
        end
        local entry = assert(find_switch(state, phase, fields[5]), "the streamed switch has not entered the co-op census")
        assert(same(entry.level, fixture.stream:GetLoadedLevel()))
        local stock_focus = require("native_loot_crate_input").stock_focus
        if mode == "host" and phase == "hosting" then
            entry.actor:SendInteractionToActor(state.local_pawn.pawn, entry.box)
            return "host pressed"
        elseif mode == "stage" and phase == "hosting" then
            local pawn = assert(guest_save.exact_item_context(state, phase, fields[4]))
            local center, origin = entry.box:K2_GetComponentLocation(), pawn:K2_GetActorLocation()
            fixture.guest_origin = origin
            for _, axis in ipairs({entry.box:GetForwardVector(), entry.box:GetRightVector()}) do
                for _, offset in ipairs({90, -90, 130, -130}) do
                    local point = {X = center.X + offset * axis.X, Y = center.Y + offset * axis.Y, Z = center.Z - 42}
                    if pawn:K2_SetActorLocation(point, false, {}, true)
                        and pcall(stock_focus, pawn, entry.box, function() return true end) then
                        pawn:ForceNetUpdate()
                        return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
                    end
                end
            end
            pawn:K2_SetActorLocation(origin, false, {}, true)
            error("could not focus the streamed switch", 0)
        elseif mode == "return-guest" and phase == "hosting" then
            local pawn = assert(guest_save.exact_item_context(state, phase, fields[4]))
            assert(fixture.guest_origin and pawn:K2_SetActorLocation(fixture.guest_origin, false, {}, true))
            pawn:ForceNetUpdate()
            return "guest returned"
        elseif mode == "guest" and phase == "connected" then
            local before = entry.actor.Powered
            stock_focus(state.local_pawn.pawn, entry.box)
            return entry.actor.Powered == before and "guest pressed" or "guest changed power locally"
        end
        error("invalid streamed switch fixture", 0)
    end
    if (fields[2] == "fixture-warehouse-switch" or fields[2] == "fixture-warehouse-radio"
        or fields[2] == "fixture-observatory-tv" or fields[2] == "fixture-warehouse-candle")
        and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local stock_focus = require("native_loot_crate_input").stock_focus
        local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        local radio = fields[2] == "fixture-warehouse-radio"
        local candle = fields[2] == "fixture-warehouse-candle"
        local television = fields[2] == "fixture-observatory-tv"
        local class, number = "CircutBox_BP_C", 1
        if radio then class, number = "Prop_Radio01_C", 3 end
        if candle then class = "Prop_Candle_01_C" end
        local name = class .. " " .. dock .. ".Tut_Dock_P:PersistentLevel." .. class .. "_" .. number
        local mode = fields[3]
        if television then
            assert(state.map == "/Game/Classes/Maps/Maps_OverWorld/Observitory_P")
            if mode == "discover" then
                local names = {}
                for id, candidate in pairs(census(state, phase)) do
                    if candidate.power == "Power" then
                        local entry = find_switch(state, phase, id)
                        if entry and entry.actor.Working then names[#names + 1] = id end
                    end
                end
                table.sort(names)
                return assert(names[1], "no working stock television in Observatory")
            end
            name = assert(fields[5], "missing exact television identity")
        else
            assert(state.map == dock)
        end
        local entry = assert(find_switch(state, phase, name), "the stock warehouse device is missing")
        if candle and mode == "state" then
            local material = entry.mesh:GetMaterial(0)
            return table.concat({bit(entry.actor.Lit), bit(entry.light:IsVisible()), bit(entry.flame:IsActive()),
                bit(entry.box:GetGenerateOverlapEvents()), valid(material) and material:GetFullName() or "-"}, "|")
        elseif candle and mode == "ignite" and phase == "hosting" then
            entry.actor:SetLitUnlit(true, false)
            return "host lit the candle"
        elseif candle and mode == "local-extinguish" and phase == "connected" then
            entry.actor:Extinguish(1)
            return bit(entry.actor.Lit)
        elseif television and mode == "screen" then
            local material = assert(entry.actor.PropMesh:GetMaterial(entry.actor.ScreenIndex))
            return (entry.actor.Power and "1|" or "0|") .. material:GetFullName()
        elseif radio and mode == "playback" then
            assert(type(SilverfishProbeNativeRadioPlayback) == "function", "the native media probe is missing")
            local playing, song = SilverfishProbeNativeRadioPlayback(name:match("^%S+ (.+)$"))
            if type(playing) ~= "boolean" then return "no media player" end
            return (playing and "playing" or "silent") .. "|" .. (song or "")
        elseif mode == "host" and phase == "hosting" then
            entry.actor:SendInteractionToActor(state.local_pawn.pawn, entry.box)
            return "host pressed"
        elseif mode == "guest" and phase == "connected" then
            stock_focus(state.local_pawn.pawn, entry.box)
            return "guest pressed"
        elseif mode == "stage" and phase == "hosting" or candle and mode == "guest-near" and phase == "connected" then
            local local_press = mode == "guest-near"
            local pawn = local_press and state.local_pawn.pawn
                or assert(guest_save.exact_item_context(state, phase, fields[4]))
            local center = entry.box:K2_GetComponentLocation()
            local origin = pawn:K2_GetActorLocation()
            for _, axis in ipairs({entry.box:GetForwardVector(), entry.box:GetRightVector()}) do
                for _, offset in ipairs({90, -90, 130, -130}) do
                    local point = {X = center.X + offset * axis.X, Y = center.Y + offset * axis.Y, Z = center.Z - 42}
                    if pawn:K2_SetActorLocation(point, false, {}, true)
                        and pcall(stock_focus, pawn, entry.box, function() return true end) then
                        if local_press then
                            stock_focus(pawn, entry.box)
                            return "guest pressed"
                        end
                        return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
                    end
                end
            end
            pawn:K2_SetActorLocation(origin, false, {}, true)
            error("could not focus the warehouse device from a nearby placement", 0)
        end
        error("invalid warehouse switch fixture", 0)
    end
