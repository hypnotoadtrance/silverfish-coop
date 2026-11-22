local box = os.getenv("SILVERFISH_COOP_BRIDGE")
local pyrogeist = box and box:find("pyrogeist%-proof%-%x+") ~= nil
local shadow = box and box:find("shadow%-sphere%-proof%-%x+") ~= nil
local angler = box and box:find("angler%-lamp%-proof%-%x+") ~= nil
local observer = box and box:find("observer%-proof%-%x+") ~= nil
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box
    or not (pyrogeist or shadow or angler or observer or box:find("gravity%-proof%-%x+")) then return end

local level = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local director_path = level .. ":PersistentLevel.DynaimicSpawnControler_BP_C_1"
local class = pyrogeist and "Pyrogeist_BP_C" or shadow and "ShadowSphere_BP_C"
    or angler and "AnglerLamp_BP_C" or observer and "Observers_BP_C" or "GravityAnomaly_BP_C"
local particle_name = pyrogeist and "NI_Pyrogeist_Center" or "NI_GravityDistortion"
local particle = "NiagaraSystem /Game/Assets/PFX/" .. particle_name .. "." .. particle_name
local jet_particle = "NiagaraSystem /Game/Assets/PFX/NI_FireJet_Continuous_2.NI_FireJet_Continuous_2"
local contact = "BndEvt__Pyrogeist_BP_CapsuleComponent_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"
local panic = "BndEvt__Pyrogeist_BP_Sphere_Panic_K2Node_ComponentBoundEvent_2_ComponentBeginOverlapSignature__DelegateSignature"
local shadow_enter = "BndEvt__Starburst_BP_Sphere_K2Node_ComponentBoundEvent_0_ComponentBeginOverlapSignature"
    .. "__DelegateSignature"
local weights = "Anomalies_WtWeights_41_BDF2F049400F5C73E9FF86B6B2B4E19F"
local test_lane = {X = 13850, Y = -200, Z = 132}
local previous, lift, hooked, selected
local watched, damage_hooked
local gaze_hooked
local reward_camera

local function valid(object)
    return object and object:IsValid()
end

local function clear_offering_lane(observer_, start, target, ignore)
    local kismet = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local color = {R = 0, G = 0, B = 0, A = 0}
    return not kismet:SphereTraceSingleForObjects(observer_, start, target, 70, {0, 1, 2, 3, 4, 5},
        false, ignore, 0, {}, true, color, color, 0)
end

local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function write(kind, value, mode)
    local file = assert(io.open(box .. "/" .. kind, mode or "wb"))
    file:write(value, "\n")
    file:close()
end

local function wells(world)
    local result = {}
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then result[#result + 1] = actor end
    end
    return result
end

local function stock_roll(world)
    local director = StaticFindObject(director_path)
    assert(same(director:GetWorld(), world))
    local map = director.Spawns_Struct[weights]
    local original = {}
    map:ForEach(function(key, value)
        original[#original + 1] = {key:get(), value:get()}
    end)
    assert(#original == 10, "Crossroads anomaly weights did not match stock")
    local cap = director.Max_AnomalyFields
    local ok, issue = pcall(function()
        map:Empty()
        map:Add(FName(pyrogeist and "Pyrogiest" or shadow and "ShadowSwarm" or angler and "AnglerLamp"
            or observer and "Observers" or "GravityWell"), 1)
        director.Max_AnomalyFields = 2
        director:SpawnAnomalies()
    end)
    director.Max_AnomalyFields = cap
    map:Empty()
    for _, entry in ipairs(original) do map:Add(entry[1], entry[2]) end
    assert(ok, tostring(issue))
end

local function jets(world)
    local result = {}
    for _, jet in ipairs(FindAllOf("Fire_Jet_Directed_BP_C") or {}) do
        local body = jet.Instigator
        local role = jet:GetLocalRole()
        if (role == 1 or role == 3) and same(jet:GetWorld(), world) and not jet:IsActorBeingDestroyed()
            and valid(body) and body:GetClass():GetFullName():match("%.Pyrogeist_BP_C$") then
            local point = body:K2_GetActorLocation()
            if (point.X - test_lane.X)^2 + (point.Y - test_lane.Y)^2 < 30^2 then result[#result + 1] = jet end
        end
    end
    return result
end

local function watch(world, host_pawn, guest_pawn)
    watched = {world = world, host = host_pawn, guest = guest_pawn}
    if damage_hooked then return end
    RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage", function(context, damage, _, instigator)
        local pawn, body = context:get(), instigator:get()
        if not watched or not valid(body) or not same(body:GetWorld(), watched.world)
            or not body:GetClass():GetFullName():match("%.Pyrogeist_BP_C$") then return end
        local seat = same(pawn, watched.host) and "host" or same(pawn, watched.guest) and "guest"
        if not seat then return end
        local fire = damage:get().Fire_12_2B415A724C533C075B6BB390B76BF247
        if seat == "guest" then watched.hit = true end
        write("pyrogeist-damage", ("%s|%s|%d|%.3f|%.4f"):format(
            seat, pawn:GetFullName(), pawn:GetLocalRole(), fire, pawn.Save.PlayerHealth), "ab")
    end)
    damage_hooked = true
end

local function floor_position(pawn, near, x, y, players)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local out = {}
    local color = {R = 0, G = 0, B = 0, A = 0}
    local hit = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = near + 350}, {X = x, Y = y, Z = near - 350},
        0, true, players, 0, out, true, color, color, 0)
    local impact = (out.OutHit or out).ImpactPoint
    if hit and impact and math.abs(impact.Z - near) < 260 then
        return {X = impact.X, Y = impact.Y, Z = impact.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() + 8}
    end
end

local function command(operation, world, driver, host, local_pawn, argument)
    local current = wells(world)
    if operation == "roll" then
        if host then assert(os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1") end
        stock_roll(world)
        return tostring(#current) .. "|" .. tostring(#wells(world))
    elseif operation == "view" then
        local rows = {}
        for _, well in ipairs(current) do
            local at = well:K2_GetActorLocation()
            local fx = pyrogeist and well.NI_StarburstHint or shadow and well.PostProcess_CreepingShadows
                or angler and well.PostProcessLure or observer and well.Sphere1 or well.NI_GravityDistortion
            local post = shadow or angler
            local available = valid(fx) and same(fx:GetOwner(), well)
                and (post or observer or valid(fx.Asset) and fx.Asset:GetFullName() == particle)
            assert(pyrogeist or post or observer or available)
            if available then
                rows[#rows + 1] = ("%.2f|%.2f|%.2f|%d|%s|%s|%s"):format(at.X, at.Y, at.Z, well:GetLocalRole(),
                    tostring(well:GetActorEnableCollision()), tostring(well:IsActorTickEnabled()),
                    tostring(not observer and (post and fx.bEnabled or not post and fx:IsActive())))
            end
        end
        return table.concat(rows, ";")
    elseif operation == "guestguard" then
        assert(not host and #current > 0)
        local well = current[1]
        assert(well:GetLocalRole() == 1 and not well:GetActorEnableCollision() and not well:IsActorTickEnabled())
        if observer then
            local opacity = well.OpacityTarget
            well:ReceiveBeginPlay()
            well:CheckPlayerLooking()
            well:Rage()
            well:TryCatchItem(local_pawn, {X = 1, Y = 0, Z = 0})
            well:AbsorbItem(local_pawn)
            assert(well.OpacityTarget == opacity)
            return "0"
        end
        if angler then
            well:ReceiveBeginPlay()
            well:ReceiveTick(.2)
            well:AddLook()
            well:SubtractLook()
            well:Attract_NPCs(local_pawn)
            assert(well.LookAlpha == 0)
            return "0"
        end
        if shadow then
            local count = #well.OverlappingCharacters
            well:ReceiveBeginPlay()
            well:ReceiveTick(.2)
            well[shadow_enter](well, well.Sphere, local_pawn, local_pawn.CapsuleComponent, 0, false, {})
            well:Try_Attack()
            assert(#well.OverlappingCharacters == count and not well.PlayerOverlap)
            return tostring(count)
        end
        if pyrogeist then
            watch(world, nil, local_pawn)
            local save = local_pawn.Save
            local scan, health = well.ScanIntensity, valid(save) and save.PlayerHealth
            well:ReceiveBeginPlay()
            well:MoveAround()
            well[contact](well, well.CapsuleComponent, local_pawn, local_pawn.CapsuleComponent, 0, false, {})
            well[panic](well, well.Sphere_Panic, local_pawn, local_pawn.CapsuleComponent, 0, false, {})
            assert(well.ScanIntensity == scan and (valid(save) and save.PlayerHealth) == health)
            return tostring(#jets(world))
        end
        local characters, bodies = #well.Characters, #well.PhysActors
        well:ReceiveBeginPlay()
        well:AddActors(local_pawn, {})
        well:ReceiveTick(.2)
        assert(#well.Characters == characters and #well.PhysActors == bodies)
        return tostring(characters) .. "|" .. tostring(bodies)
    elseif (shadow or angler or observer) and operation == "prepare" then
        assert(host and #current > 0 and #driver.ClientConnections == 0)
        local body = current[1]
        assert(body:HasAuthority() and body.bReplicates and body:GetActorEnableCollision())
        local anchor = {X = test_lane.X, Y = test_lane.Y, Z = test_lane.Z + 3500}
        -- Scope this lane above the roaming NPCs so unrelated attacks cannot alter either save during the proof.
        local_pawn.CharacterMovement:StopMovementImmediately()
        local_pawn.CharacterMovement:SetMovementMode(0, 0)
        assert(local_pawn:K2_SetActorLocation({X = anchor.X + (observer and 2800 or 1050), Y = anchor.Y, Z = anchor.Z}, false, {}, true))
        assert(body:K2_SetActorLocation(anchor, false, {}, true))
        if observer then
            body.FloatingPawnMovement:StopMovementImmediately()
            body.FloatingPawnMovement:SetComponentTickEnabled(false)
        end
        body:ForceNetUpdate()
        local_pawn:ForceNetUpdate()
        selected = {well = body, entry = {X = anchor.X + 120, Y = anchor.Y, Z = anchor.Z}, anchor = anchor}
        return ("%.2f|%.2f|%.2f"):format(anchor.X, anchor.Y, anchor.Z)
    elseif pyrogeist and operation == "prepare" then
        assert(host and #current > 0 and #driver.ClientConnections == 0)
        local body = current[1]
        assert(body:HasAuthority() and body.bReplicates and body:GetActorEnableCollision())
        local start = test_lane
        local ignored = {local_pawn, body}
        local anchor = floor_position(body, start.Z, start.X, start.Y, ignored)
        assert(anchor, "No floor at the Crossroads spawn")
        for _, offset in ipairs({{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) do
            local dx, dy = offset[1], offset[2]
            local entry = floor_position(local_pawn, start.Z, start.X + 420 * dx, start.Y + 420 * dy, ignored)
            local parking = floor_position(local_pawn, start.Z, start.X + 1050 * dx, start.Y + 1050 * dy, ignored)
            if entry and parking and local_pawn:K2_TeleportTo(parking, local_pawn:K2_GetActorRotation()) then
                -- Exercise the stock hazard on a reachable lane even if the field rolled in a building.
                body.CharacterMovement:StopMovementImmediately()
                body.CharacterMovement:SetMovementMode(0, 0)
                assert(body:K2_SetActorLocation(anchor, false, {}, true), "Could not relocate chosen Pyrogeist")
                body:FlushNetDormancy()
                body:ForceNetUpdate()
                local_pawn:ForceNetUpdate()
                selected = {well = body, entry = entry}
                write("hazard-anchor", ("%s|role=%d:%d|rep=%s|dormancy=%s|relevant=%s|cull=%s"):format(
                    body:GetFullName(), body:GetLocalRole(), body:GetRemoteRole(), tostring(body.bReplicates),
                    tostring(body.NetDormancy), tostring(body.bAlwaysRelevant), tostring(body.NetCullDistanceSquared)))
                return ("%.2f|%.2f|%.2f"):format(anchor.X, anchor.Y, anchor.Z)
            end
        end
        error("No floor for the Crossroads hazard test lane")
    elseif operation == "prepare" then
        assert(host and #current > 0 and #driver.ClientConnections == 0)
        local players = {local_pawn}
        for _, well in ipairs(current) do
            local point = well.Sphere:K2_GetComponentLocation()
            local entry = floor_position(local_pawn, point.Z, point.X, point.Y, players)
            if entry then
                local radius = well.Sphere:GetScaledSphereRadius() + 240
                for _, offset in ipairs({{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) do
                    local x, y = point.X + radius * offset[1], point.Y + radius * offset[2]
                    local placement = floor_position(local_pawn, point.Z, x, y, players)
                    if placement and local_pawn:K2_TeleportTo(placement, local_pawn:K2_GetActorRotation()) then
                        selected = {well = well, entry = entry}
                        local_pawn:ForceNetUpdate()
                        return ("%.2f|%.2f|%.2f"):format(point.X, point.Y, point.Z)
                    end
                end
            end
        end
        error("No safe position near GravityWell")
    elseif operation == "position" then
        local subject = host and #driver.ClientConnections == 1 and driver.ClientConnections[1].PlayerController.Pawn
            or not host and local_pawn
        assert(valid(subject) and same(subject:GetWorld(), world))
        local point = subject:K2_GetActorLocation()
        return ("%.2f|%.2f|%.2f"):format(point.X, point.Y, point.Z)
    elseif shadow and operation == "shadowinside" then
        assert(host and selected and valid(selected.well) and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
        selected.retreat = {X = selected.entry.X + 930, Y = selected.entry.Y, Z = selected.entry.Z}
        guest.CharacterMovement:StopMovementImmediately()
        guest.CharacterMovement:SetMovementMode(0, 0)
        assert(guest:K2_SetActorLocation(selected.entry, false, {}, true))
        guest:ForceNetUpdate()
        return guest:GetFullName()
    elseif shadow and operation == "shadowleave" then
        assert(host and selected and selected.retreat and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:K2_TeleportTo(selected.retreat, guest:K2_GetActorRotation()))
        return guest:GetFullName()
    elseif angler and operation == "anglerholdguest" then
        assert(host and selected and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
        local parking = {X = selected.entry.X + 1050, Y = selected.entry.Y, Z = selected.entry.Z}
        guest.CharacterMovement:StopMovementImmediately()
        guest.CharacterMovement:SetMovementMode(0, 0)
        assert(guest:K2_SetActorLocation(parking, false, {}, true))
        guest:ForceNetUpdate()
        return ("%.2f|%.2f|%.2f"):format(parking.X, parking.Y, parking.Z)
    elseif angler and operation == "anglerviewpoint" then
        assert(not host and valid(local_pawn.Camera))
        local camera = local_pawn.Camera
        local point, ahead = camera:K2_GetComponentLocation(), camera:GetForwardVector()
        return ("%.3f|%.3f|%.3f|%.5f|%.5f|%.5f"):format(point.X, point.Y, point.Z, ahead.X, ahead.Y, ahead.Z)
    elseif angler and operation == "anglerpresent" then
        assert(host and selected and valid(selected.well) and not selected.home)
        local x, y, z, ax, ay, az = argument:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z, ax, ay, az = tonumber(x), tonumber(y), tonumber(z), tonumber(ax), tonumber(ay), tonumber(az)
        assert(x and y and z and ax and ay and az and math.abs(x - test_lane.X) < 8000 and math.abs(y - test_lane.Y) < 8000
            and math.abs(z) < 10000 and math.abs(ax) <= 1 and math.abs(ay) <= 1 and math.abs(az) <= 1)
        local body = selected.well
        selected.home = body:K2_GetActorLocation()
        body:SetActorTickEnabled(false)
        local kismet = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        kismet:MoveComponentTo(body.RootComponent, body.RootComponent.RelativeLocation, body.RootComponent.RelativeRotation,
            false, false, 0, true, 1, {Linkage = -1, UUID = -436090044,
                ExecutionFunction = FName("ExecuteUbergraph_AnglerLamp_BP"), CallbackTarget = body})
        assert(body:K2_SetActorLocation({X = x + 325 * ax - 60 * ay, Y = y + 325 * ay + 60 * ax,
            Z = z + 325 * az}, false, {}, true))
        body.PointLight:SetLightColor({R = .8, G = .2, B = .45, A = 1}, true)
        body.PointLight1:SetLightColor({R = .4, G = .6, B = .12, A = 1}, true)
        body:ForceNetUpdate()
        return body:GetFullName()
    elseif angler and operation == "anglervisual" then
        local rows = {}
        for _, actor in ipairs(current) do
            local post = actor.PostProcessLure
            local first, second = actor.PointLight:GetLightColor(), actor.PointLight1:GetLightColor()
            local list = post.Settings.WeightedBlendables.Array
            local material = #list > 0 and list[1].Object
            local vfx = valid(material) and material:GetFullName():match("^MaterialInstanceDynamic ")
            local light = vfx and material:K2_GetVectorParameterValue(FName("Origin"))
            local point, rotation = actor:K2_GetActorLocation(), local_pawn.Controller:GetControlRotation()
            rows[#rows + 1] = ("%.3f|%.3f|%.3f|%.5f|%.5f|%.5f|%.5f|%.5f|%.5f|%s|%.4f|%.3f|%.3f"):format(
                point.X, point.Y, point.Z, first.R, first.G, first.B, second.R, second.G, second.B,
                tostring(post.bEnabled), light and light.A or 0, rotation.Yaw, rotation.Pitch)
        end
        return table.concat(rows, ";")
    elseif (shadow or angler) and operation == "viewpoint" then
        assert(not host and valid(local_pawn.Camera))
        local point = local_pawn.Camera:K2_GetComponentLocation()
        return ("%.3f|%.3f|%.3f"):format(point.X, point.Y, point.Z)
    elseif shadow and operation == "viewinside" then
        assert(host and selected and valid(selected.well) and not selected.home)
        local x, y, z = argument:match("^([^|]+)|([^|]+)|([^|]+)$")
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        assert(x and y and z and math.abs(x - test_lane.X) < 5000 and math.abs(y - test_lane.Y) < 5000 and math.abs(z) < 10000)
        local body = selected.well
        local at, center = body:K2_GetActorLocation(), body.Sphere:K2_GetComponentLocation()
        selected.home = at
        assert(body:K2_SetActorLocation({X = x + at.X - center.X + 400, Y = y + at.Y - center.Y, Z = z + at.Z - center.Z},
            false, {}, true))
        body:ForceNetUpdate()
        return body:GetFullName()
    elseif (shadow or angler) and operation == "viewrestore" then
        assert(host and selected and valid(selected.well) and selected.home)
        assert(selected.well:K2_SetActorLocation(selected.home, false, {}, true))
        if angler then selected.well:SetActorTickEnabled(true) end
        selected.well:ForceNetUpdate()
        selected.home = nil
        return selected.well:GetFullName()
    elseif shadow and operation == "shadowvisual" then
        assert(not host and valid(local_pawn.Camera))
        local rows = {}
        local camera = local_pawn.Camera:K2_GetComponentLocation()
        local manager = local_pawn.Controller.PlayerCameraManager
        local rendered = valid(manager) and manager:GetCameraLocation() or {X = 0, Y = 0, Z = 0}
        local pawn = local_pawn:K2_GetActorLocation()
        write("shadow-camera", ("camera=%.2f,%.2f,%.2f|view=%.2f,%.2f,%.2f|pawn=%.2f,%.2f,%.2f"):format(
            camera.X, camera.Y, camera.Z, rendered.X, rendered.Y, rendered.Z, pawn.X, pawn.Y, pawn.Z))
        for _, actor in ipairs(current) do
            local post, sphere, mesh = actor.PostProcess_CreepingShadows, actor.Sphere, actor.FX_Sphere_01
            if actor:GetLocalRole() == 1 and valid(post) and valid(sphere) and valid(mesh) then
                local list = post.Settings.WeightedBlendables.Array
                local material = #list > 0 and list[1].Object
                local point = sphere:K2_GetComponentLocation()
                local d = math.sqrt((point.X - camera.X)^2 + (point.Y - camera.Y)^2 + (point.Z - camera.Z)^2)
                local strength = valid(material) and material:GetFullName():match("^MaterialInstanceDynamic ")
                    and material:K2_GetScalarParameterValue(FName("Intensity")) or 0
                rows[#rows + 1] = ("%s|%.5f|%.4f|%.3f|%.3f|%.3f"):format(
                    tostring(post.bEnabled), strength, mesh.RelativeScale3D.X, d, sphere.SphereRadius, actor.BlendRadius)
            end
        end
        return table.concat(rows, ";")
    elseif (shadow or observer) and operation == "shadowstate" then
        assert(host and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        local name = FName("Player_Fatigue")
        local function fatigue(pawn)
            local flags = pawn.Save.Flags
            return flags:Contains(name) and flags:Find(name):get() or 0
        end
        return ("%d|%.3f|%d|%.3f"):format(fatigue(local_pawn), local_pawn.Save.PlayerHealth,
            fatigue(guest), guest.Save.PlayerHealth)
    elseif observer and operation == "observerpose" then
        assert(host and selected and valid(selected.well) and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
        local use_host = argument == "host"
        local distance = argument == "far" and 2500 or 600
        local away = argument == "away"
        assert(use_host or argument == "far" or argument == "away" or argument == "guest")
        local point = selected.anchor
        local target, other = use_host and local_pawn or guest, use_host and guest or local_pawn
        local function place(pawn, x, y, rotation)
            pawn.CharacterMovement:StopMovementImmediately()
            pawn.CharacterMovement:SetMovementMode(0, 0)
            assert(pawn:K2_SetActorLocation({X = x, Y = y, Z = point.Z}, false, {}, true))
            pawn.Controller:SetControlRotation(rotation)
            pawn.Camera:K2_SetWorldRotation(rotation, false, {}, true)
            pawn:ForceNetUpdate()
        end
        place(other, point.X + 2800, point.Y + 320, {Pitch = 0, Yaw = 0, Roll = 0})
        place(target, point.X + distance, point.Y, {Pitch = 0, Yaw = away and 0 or 180, Roll = 0})
        if not gaze_hooked then
            RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:PsyAttack", function(context, amount)
                local pawn = context:get()
                if (same(pawn, local_pawn) or same(pawn, guest)) and amount:get() == 4 then
                    write("observer-gaze", (same(pawn, local_pawn) and "host" or "guest") .. "|" .. pawn:GetFullName(),
                        "ab")
                end
            end)
            gaze_hooked = true
        end
        local eye, look = target.Camera:K2_GetComponentLocation(), target.Camera:GetForwardVector()
        return ("%.1f|%.1f|%.1f|%.3f|%.3f|%.3f"):format(eye.X, eye.Y, eye.Z, look.X, look.Y, look.Z)
    elseif observer and operation == "observercheck" then
        assert(host and selected and valid(selected.well) and same(selected.well["As Player FP"], local_pawn))
        selected.well:CheckPlayerLooking()
        assert(same(selected.well["As Player FP"], local_pawn), "Observer retained the temporary target")
        return local_pawn:GetFullName()
    elseif observer and operation == "observeroffering" then
        assert(host and selected and valid(selected.well) and not selected.offering and #driver.ClientConnections == 1)
        local path = "/Game/Classes/Items/Item_Jewely.Item_Jewely_C"
        local kind = StaticFindObject(path)
        if not valid(kind) then LoadAsset(path); kind = StaticFindObject(path) end
        local body, guest = selected.well, driver.ClientConnections[1].PlayerController.Pawn
        local point = body:K2_GetActorLocation()
        local start = {X = point.X + 265, Y = point.Y, Z = point.Z}
        if argument == "flight" or argument == "loot" or argument == "input" then
            local clear
            local heights = argument == "flight" and {3000, 2500, 2000, 1500, 1000, 700} or {700, 1000, 1500}
            for _, height in ipairs(heights) do
                local target = {X = point.X, Y = point.Y, Z = test_lane.Z + height}
                start = {X = target.X + 265, Y = target.Y, Z = target.Z}
                local ignore = {body, local_pawn, guest}
                local guest_eye = {X = target.X + 385, Y = target.Y, Z = target.Z + 30}
                if clear_offering_lane(body, start, target, ignore)
                    and (argument ~= "input" or clear_offering_lane(body, guest_eye, start, ignore)) then
                    assert(body:K2_SetActorLocation(target, false, {}, true))
                    if argument == "loot" or argument == "input" then
                        for index, pawn in ipairs({local_pawn, guest}) do
                            local guest_throw = argument == "input" and index == 2
                            local parked = {X = target.X + (guest_throw and 385 or 600),
                                Y = target.Y + (guest_throw and 0 or 100 * index), Z = target.Z}
                            assert(pawn:K2_SetActorLocation(parked, false, {}, true))
                            if guest_throw then
                                pawn.Camera:K2_SetWorldLocation({X = parked.X, Y = parked.Y, Z = parked.Z + 30}, false, {}, true)
                            end
                            pawn:ForceNetUpdate()
                        end
                    end
                    clear = true
                    break
                end
            end
            assert(clear, "No clear stock throw lane")
        end
        local item = world:SpawnActor(kind, start, {Pitch = 0, Yaw = 0, Roll = 0})
        assert(valid(item) and item:HasAuthority() and valid(item.Box))
        -- A high-value item guarantees an ordinary stock reward without choosing its tier.
        item.Value = 2500
        item.Instigator = guest
        item.Box:SetSimulatePhysics(true)
        item.Box:SetEnableGravity(false)
        item:SetReplicates(true)
        item:SetReplicateMovement(true)
        item:ForceNetUpdate()
        selected.offering = item
        selected.absorptions = 0
        RegisterHook("/Game/Classes/Anomalies/Observers_BP.Observers_BP_C:AbsorbItem", function(context, input)
            local observer_ = context:get()
            if not same(observer_, selected.well) then return end
            local offering = input:get()
            if same(offering, item) and observer_.ResultIndex > 0 then selected.absorptions = selected.absorptions + 1 end
            write("observer-stock-absorb", ("%s|%s"):format(
                valid(offering) and offering:GetFullName() or "-", tostring(observer_.ResultIndex)), "ab")
        end)
        return item:GetFullName()
    elseif observer and operation == "observerimpact" then
        assert(host and selected and valid(selected.offering) and valid(selected.well))
        local item = selected.offering
        local point, target = item:K2_GetActorLocation(), selected.well:K2_GetActorLocation()
        assert(math.abs(point.Y - target.Y) < 35 and math.abs(point.Z - target.Z) < 35 and point.X > target.X)
        item.Value = 2500
        assert(item:K2_SetActorLocation(target, false, {}, true))
        return "entered stock capsule"
    elseif observer and operation == "observerlaunch" then
        assert(host and selected and valid(selected.offering) and valid(selected.well) and #driver.ClientConnections == 1)
        local item, body = selected.offering, selected.well
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(same(item.Instigator, guest) and valid(guest.Camera) and valid(item.RootComponent))
        local source, target = item:K2_GetActorLocation(), body:K2_GetActorLocation()
        local offset = {X = target.X - source.X, Y = target.Y - source.Y, Z = target.Z - source.Z}
        local length = math.sqrt(offset.X^2 + offset.Y^2 + offset.Z^2)
        local direction = {X = offset.X / length, Y = offset.Y / length, Z = offset.Z / length}
        local angle = {Pitch = math.deg(math.asin(direction.Z)), Yaw = math.deg(math.atan(direction.Y, direction.X)), Roll = 0}
        guest.Controller:SetControlRotation(angle)
        guest.Camera:K2_SetWorldRotation(angle, false, {}, true)
        direction = guest.Camera:GetForwardVector()
        local dot = (offset.X * direction.X + offset.Y * direction.Y + offset.Z * direction.Z) / length
        assert(dot > .95, ("guest aim does not align with throw %.2f"):format(dot))
        item.Value = 2500
        assert(clear_offering_lane(body, source, target, {body, item, guest, local_pawn}),
            "The fixture throw path became obstructed")
        body:TryCatchItem(item, direction)
        local grabbed = same(body.GrabbedComponent, item.RootComponent)
        item.Box:AddImpulse({X = 600 * direction.X, Y = 600 * direction.Y, Z = 600 * direction.Z},
            FName("None"), true)
        return ("%s|%.3f|%.2f"):format(tostring(grabbed), dot, length)
    elseif observer and operation == "observerabsorptions" then
        assert(host and selected and selected.absorptions)
        return tostring(selected.absorptions)
    elseif observer and operation == "observerofferpoint" then
        assert(host and selected and valid(selected.offering))
        local at = selected.offering:K2_GetActorLocation()
        return ("%.3f|%.3f|%.3f"):format(at.X, at.Y, at.Z)
    elseif observer and operation == "observerofferidentity" then
        assert(not host)
        local candidates = FindAllOf("Item_Jewely_C") or {}
        for _, item in ipairs(candidates) do
            if valid(item) and same(item:GetWorld(), world) and item:GetLocalRole() == 1 and not item:IsActorBeingDestroyed() then
                local at = item:K2_GetActorLocation()
                if (at.X - test_lane.X)^2 + (at.Y - test_lane.Y)^2 < 450^2 then
                    local x, y, z = argument:match("^([^,]+),([^,]+),([^,]+)$")
                    x, y, z = tonumber(x), tonumber(y), tonumber(z)
                    assert(x and y and z and math.abs(x - at.X) < 60 and math.abs(y - at.Y) < 60 and math.abs(z - at.Z) < 220)
                    item.Box:SetEnableGravity(false)
                    item.Box:SetPhysicsLinearVelocity({X = 0, Y = 0, Z = 0}, false, FName("None"))
                    assert(item:K2_SetActorLocation({X = x, Y = y, Z = z}, false, {}, true))
                    return ("%d|%s"):format(#candidates, item:GetFullName())
                end
            end
        end
        return ""
    elseif observer and operation == "observerthrowaim" then
        if host then
            assert(selected and valid(selected.offering) and #driver.ClientConnections == 1
                and same(driver.ClientConnections[1].PlayerController.Pawn.HeldObject, selected.offering))
            selected.offering.Box:SetEnableGravity(true)
            return "ready"
        end
        assert(valid(local_pawn.HeldObject) and local_pawn:IsLocallyControlled())
        local_pawn.HeldObject.Box:SetEnableGravity(true)
        local angle = {Pitch = -8, Yaw = 180, Roll = 0}
        local_pawn.Controller:SetControlRotation(angle)
        local_pawn.Camera:K2_SetWorldRotation(angle, false, {}, true)
        return "aimed"
    elseif observer and operation == "observerlootcamera" then
        assert(not host and local_pawn:IsLocallyControlled() and valid(local_pawn.Camera))
        local camera = local_pawn.Camera
        if argument == "reset" then
            assert(reward_camera and same(reward_camera.camera, camera))
            camera:K2_SetRelativeLocation(reward_camera.location, false, {}, true)
            reward_camera = nil
            return "restored"
        end
        assert((argument == "position" or argument:match("^position:%d+$")) and not reward_camera)
        local at, relative = local_pawn:K2_GetActorLocation(), camera.RelativeLocation
        reward_camera = {camera = camera, location = {X = relative.X, Y = relative.Y, Z = relative.Z}}
        -- The suspended test pawn's normal camera remains at the pre-teleport point.
        local height = tonumber(argument:match("^position:(%d+)$")) or 52
        assert(height <= 70)
        camera:K2_SetWorldLocation({X = at.X, Y = at.Y, Z = at.Z + height}, false, {}, true)
        local eye = camera:K2_GetComponentLocation()
        return ("%.3f|%.3f|%.3f"):format(eye.X, eye.Y, eye.Z)
    elseif observer and operation == "observerofferingprobe" then
        assert(host and selected and selected.absorptions)
        local record = io.open(box .. "/observer-native-reward", "r")
        local native = record and record:read("*l") or "unavailable"
        if record then record:close() end
        local found = {}
        for _, tier in ipairs({"Red", "Green", "Blue", "Purple", "Gold"}) do
            local stem = "Art_Tesseract_" .. tier
            local type_ = StaticFindObject("/Game/Classes/Items/Artifacts/" .. stem .. "." .. stem .. "_C")
            local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
            if valid(type_) then
                local result = {}
                api:GetAllActorsOfClass(world, type_, result)
                for _, actor in ipairs(result.OutActors or result) do
                    if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                    if valid(actor) and same(actor:GetWorld(), world) then
                        local at = actor:K2_GetActorLocation()
                        found[#found + 1] = ("%s:%.1f/%.1f/%.1f"):format(tier, at.X, at.Y, at.Z)
                    end
                end
            end
        end
        return ("%d:%s|%s"):format(selected.absorptions, native, table.concat(found, ";"))
    elseif observer and operation == "observerrewards" then
        local function near(actor)
            if not valid(actor) or not same(actor:GetWorld(), world) or actor:IsActorBeingDestroyed() then return false end
            local point = actor:K2_GetActorLocation()
            return (point.X - test_lane.X)^2 + (point.Y - test_lane.Y)^2 < 450^2
        end
        local offers, rewards = 0, {}
        for _, actor in ipairs(FindAllOf("Item_Jewely_C") or {}) do
            if near(actor) then offers = offers + 1 end
        end
        for _, tier in ipairs({"Red", "Green", "Blue", "Purple", "Gold"}) do
            for _, actor in ipairs(FindAllOf("Art_Tesseract_" .. tier .. "_C") or {}) do
                if near(actor) then
                    rewards[#rewards + 1] = ("%s,%d,%s"):format(tier, actor:GetLocalRole(), tostring(actor.bReplicateMovement))
                end
            end
        end
        return tostring(offers) .. "|" .. table.concat(rewards, ";")
    elseif observer and operation == "observervisual" then
        for _, actor in ipairs(current) do
            local point = actor:K2_GetActorLocation()
            local material = actor.DynamicMat
            if (point.X - test_lane.X)^2 + (point.Y - test_lane.Y)^2 < 30^2 and valid(material) then
                local meshes = {actor.Sphere1, actor.Sphere2, actor.Sphere3}
                local matching = 0
                for _, mesh in ipairs(meshes) do
                    if valid(mesh) and same(mesh:GetOwner(), actor) and same(mesh:GetMaterial(0), material)
                        and mesh:IsVisible() then matching = matching + 1 end
                end
                return ("%.5f|%d"):format(material:K2_GetScalarParameterValue(FName("Opacity")), matching)
            end
        end
        return ""
    elseif shadow and operation == "shadowhit" then
        assert(host and selected and valid(selected.well) and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
        local body = selected.well
        body.AttackTarget = guest
        body[shadow_enter](body, body.Sphere, guest, guest.CapsuleComponent, 0, false, {})
        body:Try_Attack()
        return guest:GetFullName()
    elseif pyrogeist and operation == "enter" then
        assert(host and selected and valid(selected.well) and #driver.ClientConnections == 1)
        local body, guest = selected.well, driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and not guest:IsLocallyControlled())
        local health = guest.Save.PlayerHealth
        watch(world, local_pawn, guest)
        watched.retreat = guest:K2_GetActorLocation()
        assert(guest:K2_TeleportTo(selected.entry, guest:K2_GetActorRotation()),
            "Could not enter the stock Pyrogeist panic radius")
        return tostring(health)
    elseif pyrogeist and operation == "jets" then
        local rows = {}
        for _, jet in ipairs(jets(world)) do
            local body, root, fx = jet.Instigator, jet.RootComponent, jet.NI_FireMassive
            local asset = valid(fx) and valid(fx.Asset) and fx.Asset:GetFullName() or "-"
            local effect = same(valid(fx) and fx:GetOwner(), jet) and asset == jet_particle and fx:IsActive()
            local at, rotation = jet:K2_GetActorLocation(), jet:K2_GetActorRotation()
            rows[#rows + 1] = ("%.2f|%.2f|%.2f|%d|%s|%s|%s|%s|%.2f|%s|%s"):format(at.X, at.Y, at.Z, jet:GetLocalRole(),
                tostring(jet:GetActorEnableCollision()), tostring(jet:IsActorTickEnabled()), tostring(effect),
                tostring(same(root:GetAttachParent(), body.Sphere)), rotation.Yaw, asset, tostring(jet.bReplicates))
        end
        return table.concat(rows, ";")
    elseif pyrogeist and operation == "jetguard" then
        assert(not host)
        local save = local_pawn.Save
        local health, count = valid(save) and save.PlayerHealth, 0
        for _, jet in ipairs(jets(world)) do
            assert(jet:GetLocalRole() == 1 and not jet:GetActorEnableCollision() and not jet:IsActorTickEnabled())
            jet:FireDmg_Actor(local_pawn)
            count = count + 1
        end
        assert(count > 0 and (valid(save) and save.PlayerHealth) == health)
        return tostring(count)
    elseif operation == "lift" then
        assert(host and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and #current > 0 and #driver.ClientConnections == 1)
        local well = selected and selected.well
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(well) and same(well:GetWorld(), world) and well:HasAuthority() and well.bReplicates and well:GetActorEnableCollision() and well:IsActorTickEnabled())
        assert(valid(guest) and same(guest:GetWorld(), world) and guest:HasAuthority() and not guest:IsLocallyControlled())
        local entry = selected.entry
        lift = {guest = guest, host = local_pawn, sent = false}
        if not hooked then
            RegisterHook("/Script/Engine.Character:LaunchCharacter", function(context, speed)
                local who = context:get()
                if not lift then return end
                local velocity = speed:get()
                if velocity.Z <= 0 then return end
                if same(who, lift.host) then write("gravity-host-launch", tostring(velocity.Z)) end
                if lift.sent or not same(who, lift.guest) then return end
                lift.sent = true
                write("gravity-launch", who:GetFullName() .. "|" .. tostring(velocity.Z))
            end)
            hooked = true
        end
        assert(guest:K2_TeleportTo(entry, guest:K2_GetActorRotation()), "could not enter GravityWell")
        return ("%.2f|%.2f|%.2f"):format(entry.X, entry.Y, guest:K2_GetActorLocation().Z)
    end
    error("unknown gravity fixture probe")
end

LoopInGameThreadWithDelay(60, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. level then return end
    local driver = world.NetDriver
    if not valid(driver) then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    if not valid(pawn) or not pawn:IsLocallyControlled() then return end
    if watched and watched.hit and watched.retreat and valid(watched.guest) then
        local guest = watched.guest
        if guest:K2_TeleportTo(watched.retreat, guest:K2_GetActorRotation()) then watched.retreat = nil end
    end
    local file = io.open(box .. "/gravity-probe", "rb")
    if not file then return end
    local request = file:read("*l")
    file:close()
    if not request or previous == request then return end
    local operation, argument = request:match("^([a-z]+)|[a-f0-9]+|?(.*)$")
    if not operation then return end
    previous = request
    local ok, result = pcall(command, operation, world, driver, not valid(driver.ServerConnection), pawn, argument)
    write("gravity-reply", (ok and "ok|" or "failed|") .. tostring(result))
end)
