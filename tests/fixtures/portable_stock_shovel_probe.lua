    if fields[2]:match("^fixture%-spade%-") and
        os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        if fields[2] == "fixture-spade-contact-diagnosis" then
            assert(phase == "connected")
            return type(SilverfishInspectNativeGuestShovel) == "function"
                and SilverfishInspectNativeGuestShovel(state.local_pawn.pawn:GetFullName():match("^Player_FP_C (%S+)$")) or "shovel hooks unavailable"
        end
        if fields[2] == "fixture-spade-hold-report" then
            assert(phase == "connected" and (fields[3] == "hold" or fields[3] == "release"))
            _G.SilverfishShovelFixtureHoldReport = fields[3] == "hold"
            return fields[3]
        end
        if fields[2] == "fixture-spade-authored-buried" then
            assert(phase == "hosting" and state.listening == true and (#fields == 3 or #fields == 4))
            local sources = {}
            for _, source in ipairs(FindAllOf("BurriedItem_BP_C") or {}) do
                if valid(source) and not source:IsActorBeingDestroyed() and source:HasAuthority()
                    and same(source:GetWorld(), state.world) and source:HasAnyFlags(EObjectFlags.RF_WasLoaded)
                    and valid(source.BurriedActor) then
                    local name = source:GetFullName()
                    local path = assert(name:match("^%S+ (%S+)$"))
                    local reward = assert(source.BurriedActor:GetFullName():match("^%S+ (%S+)$"))
                    local at = source:K2_GetActorLocation()
                    if fields[3] == path then
                        local host = state.local_pawn.pawn
                        local guest
                        for _, member in ipairs(state.pawns) do
                            if member.name == fields[4] then guest = member.pawn break end
                        end
                        assert(valid(guest) and same(guest:GetWorld(), state.world))
                        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
                        local no_color = {R=0,G=0,B=0,A=0}
                        local function ground(x, y)
                            local result = {}
                            if trace:LineTraceSingle(host, {X=x,Y=y,Z=at.Z+400}, {X=x,Y=y,Z=at.Z-450},
                                0, true, {host, guest, source}, 0, result, true, no_color, no_color, 0) then
                                local hit = result.OutHit or result
                                if hit.ImpactNormal.Z >= .7 then return hit.ImpactPoint end
                            end
                        end
                        local function approach(pawn, first_bearing)
                            for bearing = first_bearing, first_bearing + 315, 45 do
                                local angle = math.rad(bearing)
                                local pos = ground(at.X+350*math.cos(angle), at.Y+350*math.sin(angle))
                                if pos then
                                    pos.Z = pos.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() + 2
                                    if pawn:K2_TeleportTo(pos, pawn:K2_GetActorRotation()) then
                                        pawn:ForceNetUpdate()
                                        return true
                                    end
                                end
                            end
                        end
                        local spot = assert(ground(at.X, at.Y), "No ground at the authored dig")
                        assert(approach(host, 0) and approach(guest, 180), "No approach near the authored dig")
                        -- Exercise the stock spawn using the contact point found on the authored terrain.
                        source:DigForItem(spot)
                        return name .. "|" .. reward
                    end
                    sources[#sources+1] = ("%s|%s|%.3f,%.3f,%.3f"):format(path, reward, at.X, at.Y, at.Z)
                end
            end
            assert(fields[3] == "list", "The exact authored buried source is unavailable")
            table.sort(sources)
            return table.concat(sources, ";")
        end
        assert(state.map == map and (phase == "hosting" or phase == "connected"), "Crossroads dig fixtures require a co-op session")
        if fields[2] == "fixture-spade-quest-activate" then
            assert(phase == "hosting" and #fields == 2)
            local instance = state.world.OwningGameInstance
            local pawn, key = state.local_pawn.pawn, FName("Note_Farmhouse")
            assert(valid(instance) and same(instance.SaveSlot_Ref, pawn.Save) and pawn:HasAuthority())
            local quests = pawn.Save.ActiveQuests
            assert(not quests:Contains(key), "The Missing Contact setup needs a fresh campaign")
            instance:AddQuest_ByName(key)
            local active = quests:Find(key):get()
            assert(active.Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A == 1)
            return "staged the stock Missing Contact quest"
        end
        if fields[2] == "fixture-spade-sight" then
            assert(phase == "connected" and #fields == 3)
            local pawn = state.local_pawn.pawn
            local actor = StaticFindObject(fields[3])
            assert(valid(actor) and same(actor:GetWorld(), state.world))
            local box = actor.Box
            local eye, center = pawn.Camera:K2_GetComponentLocation(), box:K2_GetComponentLocation()
            local x, y, z = center.X - eye.X, center.Y - eye.Y, center.Z - eye.Z
            local distance = math.sqrt(x * x + y * y + z * z)
            local scale = pawn.InteractionRange / distance
            local ray_end = {X=eye.X+x*scale, Y=eye.Y+y*scale, Z=eye.Z+z*scale}
            local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            local color = {R=0, G=0, B=0, A=0}
            local skip = valid(pawn.EquipedItem) and {pawn, pawn.EquipedItem} or {pawn}
            local function scan(channel, complex)
                local ray = {}
                if not trace:LineTraceSingle(pawn, eye, ray_end, channel, complex, skip, 0, ray, true, color, color, 0) then
                    return "none"
                end
                local hit = ray.OutHit or ray
                local component = hit.Component and hit.Component:Get()
                return valid(component) and (component:GetFullName() .. " at " .. tostring(hit.Distance)) or "none"
            end
            return ("%.2f/%.2f, interaction=%s, visibility=%s, box=%d, channel=%d"):format(distance,
                pawn.InteractionRange, scan(4, false), scan(1, true), box:GetCollisionEnabled(),
                box:GetCollisionResponseToChannel(16))
        end
        if fields[2] == "fixture-spade-props" then
            local props = {}
            for _, actor in ipairs(FindAllOf("Prop_TinCan_C") or {}) do
                if valid(actor) and same(actor:GetWorld(), state.world) and not actor:IsActorBeingDestroyed()
                    and not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then
                    local at = actor:K2_GetActorLocation()
                    props[#props + 1] = string.format("%d,%s,%s,%.3f,%.3f,%.3f", actor:GetLocalRole(),
                        actor:GetFName():ToString(), tostring(actor.bReplicateMovement), at.X, at.Y, at.Z)
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
        if fields[2] == "fixture-spade-quest-packages" then
            local packages = {}
            for _, actor in ipairs(FindAllOf("Package_Item_FarmHouse_C") or {}) do
                if valid(actor) and same(actor:GetWorld(), state.world) and not actor:IsActorBeingDestroyed() then
                    packages[#packages + 1] = actor:GetFullName()
                end
            end
            return table.concat(packages, ";") ~= "" and table.concat(packages, ";") or "none"
        end
        local pawn = (phase == "connected" or fields[3] == "local") and state.local_pawn.pawn
            or guest_save.exact_item_context(state, phase, fields[3])
        if fields[2] == "fixture-spade-host-equip" or fields[2] == "fixture-spade-host-press"
            or fields[2] == "fixture-spade-host-release" then
            local ctx = assert(scope(state, phase))
            assert(fields[3] == "local" and same(ctx.pawn, pawn) and pawn:IsLocallyControlled())
            local path = "/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C"
            local class = StaticFindObject(path)
            assert(valid(class))
            if fields[2] == "fixture-spade-host-equip" then
                local inventory = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
                local output = {}
                -- Match the stock placed shovel row, which carries no ammunition.
                inventory:MakeItemStack(class, -1, pawn, output)
                local row, slots = output.Item or output, ctx.save.PlayerEquipment
                local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
                assert(same(row[item_class], class) and row.Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788 == -1)
                for index = 1, slots:GetArrayNum() do
                    if not valid(slots[index][item_class]) then
                        slots[index] = row
                        pawn:Equip_NextItem(index - 1, false)
                        assert(same(pawn.EquipedItem:GetClass(), class))
                        return "equipped"
                    end
                end
                error("Stock shovel fixture needs a free host equipment slot")
            end
            assert(same(pawn.EquipedItem:GetClass(), class))
            local input = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Use_Primary.IA_Use_Primary")
            local library = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
            assert(valid(input) and valid(library))
            local function release()
                pawn:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_37(
                    library:MakeInputActionValueOfType(0, 0, 0, 0), .05, .05, input)
            end
            if fields[2] == "fixture-spade-host-release" then
                release()
                return "released"
            end
            local press_path = path .. ":PrimaryPress"
            local spade, presses = pawn.EquipedItem, 0
            local pre, post = RegisterHook(press_path, function(context)
                if same(context:get(), spade) then presses = presses + 1 end
            end)
            local aim = {Pitch=-86, Yaw=0, Roll=0}
            if fields[4] == "keep-aim" then
                local x, y, z = fields[5]:match("^([%d%.%-]+),([%d%.%-]+),([%d%.%-]+)$")
                local ground = {X=tonumber(x), Y=tonumber(y), Z=tonumber(z)}
                local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
                local started = ctx.statics:GetRealTimeSeconds(ctx.world)
                local pending
                local function keep_camera()
                    if not valid(pawn) or not same(pawn:GetWorld(), ctx.world)
                        or not same(pawn.EquipedItem, spade) or ctx.statics:GetRealTimeSeconds(ctx.world) - started > 1.1 then
                        if pending then CancelDelayedAction(pending) end
                        return
                    end
                    local rotation = math_api:FindLookAtRotation(pawn.Camera:K2_GetComponentLocation(), ground)
                    pawn.Controller:SetControlRotation(rotation)
                    pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
                end
                keep_camera()
                pending = LoopInGameThreadWithDelay(16, keep_camera)
            else
                pawn.Controller:SetControlRotation(aim)
                pawn.Camera:K2_SetWorldRotation(aim, false, {}, true)
            end
            pawn:InpActEvt_IA_Use_Primary_K2Node_EnhancedInputActionEvent_36(library:MakeInputActionValueOfType(1, 0, 0, 0), 0, 0, input)
            UnregisterHook(press_path, pre, post)
            if fields[4] ~= "keep-aim" then release() end
            assert(presses == 1, ("Stock host input reached PrimaryPress %d times, blocked=%s, using=%s")
                :format(presses, tostring(pawn.IsWeaponBlocked), tostring(pawn.UsingItem)))
            return fields[4] == "keep-aim" and "pressed" or "pressed and released"
        end
        local controller = pawn.Controller
        local observing = phase == "hosting" and fields[4] == "watch-host"
        local origin = (observing and state.local_pawn.pawn or pawn):K2_GetActorLocation()
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
        local function point(location)
            return ("%.3f,%.3f,%.3f"):format(location.X, location.Y, location.Z)
        end
        if fields[2] == "fixture-spade-buried-item" then
            assert(phase == "hosting")
            local subject = StaticFindObject("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C")
            local reward = StaticFindObject("/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C")
            local floor = assert(soil({X=origin.X+115,Y=origin.Y,Z=origin.Z+55},
                {X=origin.X+115,Y=origin.Y,Z=origin.Z-180}), "Buried item fixture needs stock ground")
            local ground = floor.ImpactPoint
            local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
            local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            local transform = math_api:MakeTransform(ground, {Pitch=0,Yaw=0,Roll=0}, {X=1,Y=1,Z=1})
            local source = statics:BeginDeferredActorSpawnFromClass(pawn, subject, transform, 1, nil, 0)
            assert(valid(source))
            source.BurriedActor = reward
            assert(same(statics:FinishSpawningActor(source, transform, 0), source))
            local qualified = source:GetFullName()
            source:DigForItem(ground)
            return qualified
        elseif fields[2] == "fixture-spade-quest-approach" then
            assert(phase == "hosting")
            local x, y, z = geometry.X, geometry.Y, geometry.Z
            local target = assert(soil({X=x, Y=y, Z=z+55}, {X=x, Y=y, Z=z-46}), "Missing Contact stock soil was unavailable")
            local first_bearing = fields[4] == "observer" and 180 or 0
            for bearing = first_bearing, first_bearing + 315, 45 do
                local angle = math.rad(bearing)
                local distance = fields[4] == "observer" and 110 or 75
                local sx, sy = x + distance * math.cos(angle), y + distance * math.sin(angle)
                local floor = soil({X=sx, Y=sy, Z=z+150}, {X=sx, Y=sy, Z=z-170})
                if floor and floor.ImpactNormal.Z >= .7 then
                    local stand = {X=sx, Y=sy, Z=floor.ImpactPoint.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()+2}
                    if pawn:K2_TeleportTo(stand, facing) then
                        pawn:ForceNetUpdate()
                        return point(pawn:K2_GetActorLocation()) .. ";" .. point(target.ImpactPoint)
                    end
                end
            end
            error("No walkable approach to the Missing Contact dig site", 0)
        elseif fields[2] == "fixture-spade-quest-collect" then
            local ctx = assert(scope(state, phase))
            assert(fields[3] == "local" and same(pawn, ctx.pawn))
            local report = assert(read(ctx))
            local path = report.actor:match("^Package_Item_FarmHouse_C (%S+)$")
            local actor = assert(path and StaticFindObject(path))
            assert(valid(actor) and same(actor:GetWorld(), state.world) and report.phase == "escrowed")
            local box = actor.Box
            local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            aim = math_api:FindLookAtRotation(pawn.Camera:K2_GetComponentLocation(), box:K2_GetComponentLocation())
            controller:SetControlRotation(aim)
            pawn.Camera:K2_SetWorldRotation(aim, false, {}, true)
            pawn:LookTrace()
            assert(pawn.CanInteract and same(pawn.InteractionTarget_Component, box), "The host could not focus the stock package")
            local accepted, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), false)
            assert(accepted and status == "stock_started", status)
            return status
        elseif fields[2] == "fixture-spade-quest-aim" then
            local encoded = phase == "hosting" and fields[4] or fields[3]
            local x, y, z = encoded:match("^([%d%.%-]+),([%d%.%-]+),([%d%.%-]+)$")
            local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            aim = math_api:FindLookAtRotation(pawn.Camera:K2_GetComponentLocation(), {X=tonumber(x), Y=tonumber(y), Z=tonumber(z)})
        elseif fields[2] == "fixture-spade-aim-away" then
            assert(phase == "hosting" and fields[3] ~= "local")
            local away = {Pitch=75, Yaw=0, Roll=0}
            controller:SetControlRotation(away)
            pawn.Camera:K2_SetWorldRotation(away, false, {}, true)
            local eye, forward = pawn.Camera:K2_GetComponentLocation(), pawn.Camera:GetForwardVector()
            assert(not soil(eye, {X=eye.X+forward.X*300,Y=eye.Y+forward.Y*300,Z=eye.Z+forward.Z*300}))
            return "aimed away from soil"
        else
            assert(fields[2] == "fixture-spade-aim", "Unknown stock shovel fixture command")
        end
        if phase == "hosting" then
            if fields[4] == "find" or fields[4] == "next" or observing then
                local radii = fields[4] == "next" and {400, 550, 800, 1100} or {150, 250, 400, 550, 800, 1100}
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
                                return point(at)
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
        return point(hit.ImpactPoint)
    end
