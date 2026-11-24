    if args[2] == "fixture-warehouse-board" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(state.map == "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P", "wrong map")
        local current = assert(census(state, phase), "stock destructible census unavailable")
        local entry = assert(current.actors.Dest_WoodPlank_BP_C_1, "stock warehouse plank missing on " .. phase)
        local actor = entry.actor
        if args[3] == "damage" then
            assert(live(actor) and not actor.bHidden and actor:GetActorEnableCollision(), "warehouse board was already broken")
            -- Enter the same stock break event the game's damage calculation invokes on a lethal hit.
            local point = actor.StaticMesh:K2_GetComponentLocation()
            actor:Damage_Feedback(true, {Location = point, ImpactPoint = point})
            return "warehouse board stock break requested"
        elseif args[3] == "impact" and phase == "hosting" and live(actor) then
            local pawn = assert(state.local_pawn.pawn)
            local point = actor.StaticMesh:K2_GetComponentLocation()
            local total, lethal = {}, {}
            actor:DealDamage({BluntForce_6_6923B3964740DE281BD773BCCAAC2772 = 500},
                {Location = point, ImpactPoint = point}, pawn, {X = 0, Y = 0, Z = 0}, true, total, lethal)
            return "warehouse board stock impact dispatched"
        elseif args[3] == "inspect" then
            if entry.ended or not live(actor) then return "removed" end
            return ("%s,%s,%d"):format(tostring(actor.bHidden), tostring(actor:GetActorEnableCollision()), actor.Health)
        end
        error("invalid warehouse board fixture")
    end
