local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("gravity%-proof%-%x+") then return end

local level = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local director_path = level .. ":PersistentLevel.DynaimicSpawnControler_BP_C_1"
local particle = "NiagaraSystem /Game/Assets/PFX/NI_GravityDistortion.NI_GravityDistortion"
local weights = "Anomalies_WtWeights_41_BDF2F049400F5C73E9FF86B6B2B4E19F"
local previous, lift, hooked, selected

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
    for _, actor in ipairs(FindAllOf("GravityAnomaly_BP_C") or {}) do
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
        map:Add(FName("GravityWell"), 1)
        director.Max_AnomalyFields = 2
        director:SpawnAnomalies()
    end)
    director.Max_AnomalyFields = cap
    map:Empty()
    for _, entry in ipairs(original) do map:Add(entry[1], entry[2]) end
    assert(ok, tostring(issue))
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
            local at, fx = well:K2_GetActorLocation(), well.NI_GravityDistortion
            assert(valid(fx) and same(fx:GetOwner(), well) and valid(fx.Asset) and fx.Asset:GetFullName() == particle)
            rows[#rows + 1] = ("%.2f|%.2f|%.2f|%d|%s|%s|%s"):format(at.X, at.Y, at.Z, well:GetLocalRole(),
                tostring(well:GetActorEnableCollision()), tostring(well:IsActorTickEnabled()), tostring(fx:IsActive()))
        end
        return table.concat(rows, ";")
    elseif operation == "guestguard" then
        assert(not host and #current > 0)
        local well = current[1]
        assert(well:GetLocalRole() == 1 and not well:GetActorEnableCollision() and not well:IsActorTickEnabled())
        local characters, bodies = #well.Characters, #well.PhysActors
        well:ReceiveBeginPlay()
        well:AddActors(local_pawn, {})
        well:ReceiveTick(.2)
        assert(#well.Characters == characters and #well.PhysActors == bodies)
        return tostring(characters) .. "|" .. tostring(bodies)
    elseif operation == "approach" then
        assert(host and #current > 0 and #driver.ClientConnections == 1)
        local guest = driver.ClientConnections[1].PlayerController.Pawn
        assert(valid(guest) and guest:HasAuthority() and same(guest:GetWorld(), world))
        local players = {local_pawn, guest}
        for _, well in ipairs(current) do
            local point = well.Sphere:K2_GetComponentLocation()
            local entry = floor_position(guest, point.Z, point.X, point.Y, players)
            if entry then
                local radius = well.Sphere:GetScaledSphereRadius() + 140
                for _, offset in ipairs({{1, 0}, {-1, 0}, {0, 1}, {0, -1}}) do
                    local x, y = point.X + radius * offset[1], point.Y + radius * offset[2]
                    local placement = floor_position(local_pawn, point.Z, x, y, players)
                    local remote = floor_position(guest, point.Z, x - 110 * offset[2], y + 110 * offset[1], players)
                    if placement and remote and local_pawn:K2_TeleportTo(placement, local_pawn:K2_GetActorRotation())
                        and guest:K2_TeleportTo(remote, guest:K2_GetActorRotation()) then
                        selected = {well = well, entry = entry}
                        local_pawn:ForceNetUpdate()
                        guest:ForceNetUpdate()
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
