    if args[2] == "fixture-warehouse-board" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" then
        assert(state.map == "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P", "wrong map")
        local current = assert(census(state, phase), "stock destructible census unavailable")
        local entry = assert(current.actors.Dest_WoodPlank_BP_C_1, "stock warehouse plank missing on " .. phase)
        local actor = entry.actor
        local mode = args[3]
        if mode == "inspect" then
            if entry.ended or not live(actor) then return "removed" end
            return ("%s,%s,%.17g"):format(tostring(actor.bHidden), tostring(actor:GetActorEnableCollision()), actor.Health)
        end
        if mode == "damage" and phase == "connected" then
            assert(live(actor) and not actor.bHidden and actor:GetActorEnableCollision(), "warehouse board was already broken")
            -- Enter the same stock break event the game's damage calculation invokes on a lethal hit.
            local point = actor.StaticMesh:K2_GetComponentLocation()
            actor:Damage_Feedback(true, {Location = point, ImpactPoint = point})
            return "warehouse board stock break requested"
        elseif mode == "contact" and phase == "hosting" then
            return SilverfishReadNativeGuestMeleeContactStatus()
        end

        assert(live(actor) and actor.Health > 0, "stock warehouse plank is unavailable")
        local path = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_HeavyPipe.MeleeWep_HeavyPipe_C"
        local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
        local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
        local payload = "DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"
        local function guid(row)
            local id = row[item_guid]
            return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
        end
        local pawn, save
        if phase == "hosting" then
            pawn, save = require("guest_save").exact_item_context(state, phase, args[4])
        else
            pawn = state.local_pawn and state.local_pawn.pawn
            save = valid(pawn) and pawn.Save
        end
        assert(valid(pawn) and valid(save), "guest equipment is unavailable")
        if mode == "seed" and phase == "hosting" then
            local class = StaticFindObject(path)
            if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
            local factory = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C")
            assert(valid(class) and valid(factory), "stock Heavy Pipe factory unavailable")
            local defaults, output, slots = class:GetCDO(), {}, save.PlayerEquipment
            for index = 1, slots:GetArrayNum() do
                if same(slots[index][item_class], class) then return guid(slots[index]) end
            end
            factory:ItemActorToStruct(defaults, pawn, output)
            local row = output.Item or output
            assert(same(row[item_class], class), "stock factory returned the wrong melee item")
            for index = 1, slots:GetArrayNum() do
                if not valid(slots[index][item_class]) then
                    slots[index] = row
                    slots[index][payload] = defaults.DataStruct
                    return guid(row)
                end
            end
            error("warehouse board fixture needs an empty guest utility slot")
        end
        if mode == "select" and phase == "connected" then
            local slots = save.PlayerEquipment
            for index = 1, slots:GetArrayNum() do
                local row = slots[index]
                if valid(row[item_class]) and guid(row) == args[4] then
                    pawn:Equip_NextItem(index - 1, false)
                    return "selected"
                end
            end
            return "waiting"
        end

        local weapon = pawn.EquipedItem
        if not valid(weapon) or not same(weapon:GetClass(), StaticFindObject(path)) then return "waiting for stock pipe" end
        local attack = weapon.ComboAttacks[1]
        local range = attack.Range_7_1ABB464F45C04D4C4BDA07A41F83FF24
        local radius = attack.Radius_9_4CAB95D84DE379A3C49DFD9CE1A93643
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local math_api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        local color = {R=0, G=0, B=0, A=0}
        local bounds = {}
        trace:GetComponentBounds(actor.StaticMesh, bounds, {}, {})
        local center = bounds.Origin or bounds
        assert(center.X, "stock plank mesh bounds unavailable")

        local function aim()
            local eye = pawn.Camera:K2_GetComponentLocation()
            local angle = math_api:FindLookAtRotation(eye, center)
            local direction = math_api:GetForwardVector(angle)
            local function step(origin, distance)
                return {X=origin.X + direction.X * distance, Y=origin.Y + direction.Y * distance,
                    Z=origin.Z + direction.Z * distance}
            end
            local endpoint, line, sphere = step(eye, range), {}, {}
            if trace:LineTraceSingleForObjects(weapon, eye, endpoint, {0, 4}, true, {pawn}, 0,
                line, true, color, color, 0) then
                local hit = line.OutHit or line
                if hit.bStartPenetrating or hit.Distance <= radius then return false end
                endpoint = hit.ImpactPoint
            end
            trace:SphereTraceMulti(weapon, eye, step(endpoint, -radius), radius, 6, false, {pawn}, 0,
                sphere, true, color, color, 0)
            local hits = 0
            for _, raw in ipairs(sphere.OutHits or sphere) do
                local wrapped, hit = pcall(function() return raw:get() end)
                hit = wrapped and hit or raw
                local component = hit.Component and hit.Component:Get()
                if not same(component, actor.StaticMesh) or hit.bStartPenetrating or hit.Distance <= 1 then return false end
                hits = hits + 1
            end
            if hits == 0 then return false end
            pawn.Controller:SetControlRotation(angle)
            pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
            return true
        end
        if mode == "aim" then
            return aim() and "ready" or "stock sweep misses plank"
        end
        if mode == "approach" and phase == "hosting" then
            local original, facing = pawn:K2_GetActorLocation(), pawn.Controller:GetControlRotation()
            local half = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
            for _, distance in ipairs({range * .7, range * .85, range * .55}) do
                for offset = 0, 15 do
                    local angle = offset * math.pi / 8
                    local x, y = center.X + distance * math.cos(angle), center.Y + distance * math.sin(angle)
                    local floor = {}
                    if trace:LineTraceSingle(pawn, {X=x, Y=y, Z=center.Z+75}, {X=x, Y=y, Z=center.Z-225},
                        0, false, {pawn, actor}, 0, floor, true, color, color, 0) then
                        local hit = floor.OutHit or floor
                        local at = {X=x, Y=y, Z=hit.ImpactPoint.Z + half + 3}
                        if hit.ImpactNormal.Z >= .75 and pawn:K2_TeleportTo(at, facing) and aim() then
                            local point = pawn:K2_GetActorLocation()
                            return ("%f,%f,%f"):format(point.X, point.Y, point.Z)
                        end
                    end
                end
            end
            pawn:K2_TeleportTo(original, facing)
            error("could not align a clear stock Heavy Pipe sweep on the warehouse plank")
        end
        error("invalid warehouse board fixture")
    end
