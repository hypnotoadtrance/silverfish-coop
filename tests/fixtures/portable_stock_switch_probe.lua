    if fields[2] == "fixture-warehouse-switch" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        local name = "CircutBox_BP_C " .. dock .. ".Tut_Dock_P:PersistentLevel.CircutBox_BP_C_1"
        assert(state.map == dock)
        local entry = assert(census(state, phase)[name], "the stock warehouse switch is missing")
        local mode = fields[3]
        if mode == "host" and phase == "hosting" then
            entry.actor:SendInteractionToActor(state.local_pawn.pawn, entry.box)
            return "host pressed"
        elseif mode == "guest" and phase == "connected" then
            stock_focus(state.local_pawn.pawn, entry.box)
            return "guest pressed"
        elseif mode == "stage" and phase == "hosting" then
            local pawn = assert(guest_save.exact_item_context(state, phase, fields[4]))
            local center = entry.box:K2_GetComponentLocation()
            local origin = pawn:K2_GetActorLocation()
            for _, axis in ipairs({entry.box:GetForwardVector(), entry.box:GetRightVector()}) do
                for _, offset in ipairs({90, -90, 130, -130}) do
                    local point = {X = center.X + offset * axis.X, Y = center.Y + offset * axis.Y, Z = center.Z - 42}
                    if pawn:K2_SetActorLocation(point, false, {}, true)
                        and pcall(stock_focus, pawn, entry.box, function() return true end) then
                        return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
                    end
                end
            end
            pawn:K2_SetActorLocation(origin, false, {}, true)
            error("could not focus the warehouse circuit box from a nearby placement", 0)
        end
        error("invalid warehouse switch fixture", 0)
    end
