    if (fields[2] == "fixture-spade-aim" or fields[2] == "fixture-spade-holes" or fields[2] == "fixture-spade-loot"
        or fields[2] == "fixture-spade-props") and
        os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(state.map == map and (phase == "hosting" or phase == "connected"), "Crossroads dig fixtures require a co-op session")
        if fields[2] == "fixture-spade-props" then
            local props = {}
            for _, actor in ipairs(FindAllOf("Prop_TinCan_C") or {}) do
                if valid(actor) and same(actor:GetWorld(), state.world) and not actor:IsActorBeingDestroyed()
                    and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                    props[#props + 1] = string.format("%d,%s", actor:GetLocalRole(), actor:GetFName():ToString())
                end
            end
            return table.concat(props, ";")
        end
        if fields[2] == "fixture-spade-loot" then
            assert(phase == "hosting" and #fields == 3)
            local choices = {
                item = "/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C",
                prop = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_TinCan.Prop_TinCan_C",
            }
            local wanted = assert(choices[fields[3]])
            local target = StaticFindObject(wanted)
            if not valid(target) then LoadAsset(wanted) target = StaticFindObject(wanted) end
            assert(valid(target), "The stock dig fixture class is unavailable")
            local target_world = state.world
            assert(SilverfishArmStockDigRollFixture(target_world:GetAddress()), "The stock dig roll fixture did not arm")
            local armed = true
            RegisterBeginPlayPreHook(function(context)
                if not armed then return end
                local actor = context:get()
                if not valid(actor) or not same(actor:GetWorld(), target_world) or not actor:HasAuthority()
                    or actor:GetClass():GetFName():ToString() ~= "DigHole_BP_C" then return end
                armed = false
                local weights = actor.UnearthedItems
                assert(weights:Contains(target), "The fixture choice is absent from the stock roll")
                weights:Empty()
                weights:Add(target, 1)
            end)
            return "stock shovel weighted loot narrowed"
        end
        if fields[2] == "fixture-spade-holes" then
            local holes = {}
            for _, hole in ipairs(FindAllOf("DigHole_BP_C") or {}) do
                if valid(hole) and same(hole:GetWorld(), state.world) and not hole:IsActorBeingDestroyed() then
                    local at = hole:K2_GetActorLocation()
                    holes[#holes+1] = string.format("%d,%0.3f,%0.3f,%0.3f", hole:GetLocalRole(), at.X, at.Y, at.Z)
                end
            end
            table.sort(holes)
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
            if fields[4] == "find" or fields[4] == "next" then
                local radii = fields[4] == "find" and {150, 250, 400, 550, 800, 1100} or {400, 550, 800, 1100}
                for _, radius in ipairs(radii) do
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
