    if (fields[2] == "fixture-spade-aim" or fields[2] == "fixture-spade-holes") and
        os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(state.map == map and (phase == "hosting" or phase == "connected"), "Crossroads dig fixtures require a co-op session")
        if fields[2] == "fixture-spade-holes" then
            local holes = {}
            for _, hole in ipairs(FindAllOf("DigHole_BP_C") or {}) do
                if valid(hole) and same(hole:GetWorld(), state.world) and not hole:IsActorBeingDestroyed() then
                    local at = hole:K2_GetActorLocation()
                    holes[#holes+1] = string.format("%d,%0.3f,%0.3f,%0.3f", hole:GetLocalRole(), at.X, at.Y, at.Z)
                end
            end
            return table.concat(holes, ";") ~= "" and table.concat(holes, ";") or "none"
        end
        local pawn = phase == "connected" and state.local_pawn.pawn or guest_save.exact_item_context(state, phase, fields[3])
        local controller = pawn.Controller
        local origin = pawn:K2_GetActorLocation()
        local facing = pawn:K2_GetActorRotation()
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local color = {R=0, G=0, B=0, A=0}
        local spade = pawn.EquipedItem
        local ignore = valid(spade) and {pawn,spade} or {pawn}
        local function soil(a, b)
            local res = {}
            if trace:LineTraceSingle(pawn, a, b, 0, true, ignore, 0, res, true, color, color, 0) then
                local hit = res.OutHit or res
                local pm = hit.PhysMaterial and hit.PhysMaterial:Get()
                if valid(pm) and allowed_material[pm:GetFullName()] then return hit end
            end
        end
        local aim = {Pitch=-86,Yaw=0,Roll=0}
        if phase == "hosting" then
            if fields[4] == "find" then
                for _, radius in ipairs({150, 250, 400, 550, 800, 1100}) do
                    for bearing = 0, 315, 45 do
                        local dir = math.rad(bearing)
                        local x,y = origin.X+radius*math.cos(dir),origin.Y+radius*math.sin(dir)
                        local floor = soil({X=x,Y=y,Z=origin.Z+250},{X=x,Y=y,Z=origin.Z-550})
                        if floor and floor.ImpactNormal.Z >= .7 then
                            local stand = {X=x,Y=y,Z=floor.ImpactPoint.Z+pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()+1}
                            if pawn:K2_TeleportTo(stand, facing) then
                                controller:SetControlRotation(aim)
                                pawn.Camera:K2_SetWorldRotation(aim,false,{},true)
                                pawn:ForceNetUpdate()
                                local at = pawn:K2_GetActorLocation()
                                return string.format("%0.3f,%0.3f,%0.3f", at.X, at.Y, at.Z)
                            end
                        end
                    end
                end
                error("No stock diggable terrain close to the spade", 0)
            end
        end
        controller:SetControlRotation(aim)
        pawn.Camera:K2_SetWorldRotation(aim,false,{},true)
        local eye, forward = pawn.Camera:K2_GetComponentLocation(), pawn.Camera:GetForwardVector()
        local hit = soil(eye, {X=eye.X+forward.X*300,Y=eye.Y+forward.Y*300,Z=eye.Z+forward.Z*300})
        assert(hit, "Stock shovel camera cannot see diggable terrain")
        local at = hit.ImpactPoint
        return string.format("%0.3f,%0.3f,%0.3f", at.X, at.Y, at.Z)
    end
