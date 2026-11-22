local box = os.getenv("SILVERFISH_COOP_BRIDGE")
local pyrogeist = box and box:find("pyrogeist%-proof%-%x+") ~= nil
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box
    or not (pyrogeist or box:find("gravity%-proof%-%x+")) then return end

local level = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local director_path = level .. ":PersistentLevel.DynaimicSpawnControler_BP_C_1"
local class = pyrogeist and "Pyrogeist_BP_C" or "GravityAnomaly_BP_C"
local particle_name = pyrogeist and "NI_Pyrogeist_Center" or "NI_GravityDistortion"
local particle = "NiagaraSystem /Game/Assets/PFX/" .. particle_name .. "." .. particle_name
local jet_particle = "NiagaraSystem /Game/Assets/PFX/NI_FireJet_Continuous_2.NI_FireJet_Continuous_2"
local contact = "BndEvt__Pyrogeist_BP_CapsuleComponent_K2Node_ComponentBoundEvent_1_ComponentBeginOverlapSignature__DelegateSignature"
local panic = "BndEvt__Pyrogeist_BP_Sphere_Panic_K2Node_ComponentBoundEvent_2_ComponentBeginOverlapSignature__DelegateSignature"
local weights = "Anomalies_WtWeights_41_BDF2F049400F5C73E9FF86B6B2B4E19F"
local test_lane = {X = 13850, Y = -200, Z = 132}
local previous, lift, hooked, selected
local watched, damage_hooked

local function valid(object)
    return object and object:IsValid()
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
        map:Add(FName(pyrogeist and "Pyrogiest" or "GravityWell"), 1)
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

local function command(operation, world, driver, host, local_pawn)
    local current = wells(world)
    if operation == "roll" then
        if host then assert(os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1") end
        stock_roll(world)
        return tostring(#current) .. "|" .. tostring(#wells(world))
    elseif operation == "view" then
        local rows = {}
        for _, well in ipairs(current) do
            local at = well:K2_GetActorLocation()
            local fx = pyrogeist and well.NI_StarburstHint or well.NI_GravityDistortion
            local available = valid(fx) and same(fx:GetOwner(), well) and valid(fx.Asset) and fx.Asset:GetFullName() == particle
            assert(pyrogeist or available)
            if available then
                rows[#rows + 1] = ("%.2f|%.2f|%.2f|%d|%s|%s|%s"):format(at.X, at.Y, at.Z, well:GetLocalRole(),
                    tostring(well:GetActorEnableCollision()), tostring(well:IsActorTickEnabled()), tostring(fx:IsActive()))
            end
        end
        return table.concat(rows, ";")
    elseif operation == "guestguard" then
        assert(not host and #current > 0)
        local well = current[1]
        assert(well:GetLocalRole() == 1 and not well:GetActorEnableCollision() and not well:IsActorTickEnabled())
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
                write("pyrogeist-anchor", ("%s|role=%d:%d|rep=%s|dormancy=%s|relevant=%s|cull=%s"):format(
                    body:GetFullName(), body:GetLocalRole(), body:GetRemoteRole(), tostring(body.bReplicates),
                    tostring(body.NetDormancy), tostring(body.bAlwaysRelevant), tostring(body.NetCullDistanceSquared)))
                return ("%.2f|%.2f|%.2f"):format(anchor.X, anchor.Y, anchor.Z)
            end
        end
        error("No floor for the Crossroads Pyrogeist test lane")
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
    local operation = request:match("^([a-z]+)|")
    if not operation then return end
    previous = request
    local ok, result = pcall(command, operation, world, driver, not valid(driver.ServerConnection), pawn)
    write("gravity-reply", (ok and "ok|" or "failed|") .. tostring(result))
end)
