    if (fields[2] == "fixture-warehouse-switch" or fields[2] == "fixture-warehouse-radio")
        and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        local stock_focus = require("native_loot_crate_input").stock_focus
        local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        local radio = fields[2] == "fixture-warehouse-radio"
        local class, number = "CircutBox_BP_C", 1
        if radio then class, number = "Prop_Radio01_C", 3 end
        local name = class .. " " .. dock .. ".Tut_Dock_P:PersistentLevel." .. class .. "_" .. number
        assert(state.map == dock)
        local entry = assert(census(state, phase)[name], "the stock warehouse device is missing")
        local mode = fields[3]
        if radio and mode == "playback" then
            return entry.actor.MediaPlayer:IsPlaying() and "playing" or "silent"
        elseif mode == "host" and phase == "hosting" then
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
            error("could not focus the warehouse device from a nearby placement", 0)
        end
        error("invalid warehouse switch fixture", 0)
    end
