local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local seat = os.getenv("SILVERFISH_COOP_ROOK_MIXED_FIELDS")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or (seat ~= "host" and seat ~= "guest")
    or not mailbox or not mailbox:match("[/\\]rook%-mixed%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local level = "/Game/Classes/Maps/Dreams/TheRook_P.TheRook_P"
local field_type = "BlueprintGeneratedClass /Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C"
local gravity = "BlueprintGeneratedClass /Game/Classes/Anomalies/GravityAnomaly_BP.GravityAnomaly_BP_C"
local vortex = "BlueprintGeneratedClass /Game/Classes/Anomalies/VortexTrap_BP.VortexTrap_BP_C"
local last

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end

local function scan(world, driver)
    local server = driver.ServerConnection
    assert(same(driver.World, world) and (seat == "guest" and valid(server) and same(server.Driver, driver)
        or seat == "host" and not valid(server)), "Rook field probe has the wrong network role")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and same(pawn:GetWorld(), world) and pawn:IsLocallyControlled()
        and pawn:GetLocalRole() == (seat == "host" and 3 or 2), "Rook field probe has no qualified local pawn")
    local position = pawn:K2_GetActorLocation()
    local rows = {table.concat({"seat", seat, pawn:GetFullName(), position.X, position.Y, position.Z}, "\t")}
    for _, index in ipairs({1, 5, 6, 7, 8}) do
        local path = level .. ":PersistentLevel.AnomalyField_BP_C_" .. index
        local field = StaticFindObject(path)
        assert(valid(field) and same(field:GetWorld(), world) and same(field:GetOuter(), world.PersistentLevel)
            and field:GetClass():GetFullName() == field_type, "Rook authored field missing " .. path)
        local classes = field.Anomalies
        assert(classes:GetArrayNum() == 2 and valid(classes[1]) and valid(classes[2])
            and classes[1]:GetFullName() == gravity and classes[2]:GetFullName() == vortex
            and field.ForceSpawn == true and field.Min_Spawn == 6 and field.Max_Spawn == 12
            and math.abs(field.SpawnRadius - 11082.602) < 1, "Rook authored field changed " .. path)
        local point = field:K2_GetActorLocation()
        rows[#rows + 1] = table.concat({"field", path, point.X, point.Y, point.Z, field.SpawnRadius,
            field.Min_Spawn, field.Max_Spawn, tostring(field.ForceSpawn), gravity, vortex}, "\t")
    end
    local count = 0
    for _, family in ipairs({{"gravity", "GravityAnomaly_BP_C", gravity}, {"vortex", "VortexTrap_BP_C", vortex}}) do
        for _, actor in ipairs(FindAllOf(family[2]) or {}) do
            if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
                and actor:GetClass():GetFullName() == family[3] and valid(actor.RootComponent) then
                count = count + 1
                assert(count <= 128, "Rook anomaly census exceeded its fixture bound")
                local point = actor:K2_GetActorLocation()
                rows[#rows + 1] = table.concat({"anomaly", family[1], actor:GetFullName(), actor:GetLocalRole(),
                    tostring(actor:ActorHasTag(FName("Anomaly"))), tostring(actor.bReplicates),
                    tostring(actor:GetActorEnableCollision()), tostring(actor:IsActorTickEnabled()),
                    point.X, point.Y, point.Z}, "\t")
            end
        end
    end
    return table.concat(rows, "\n")
end

LoopInGameThreadWithDelay(100, function()
    local input = io.open(mailbox .. "/rook-mixed-field-request", "rb")
    if not input then return end
    local nonce = input:read(34)
    input:close()
    if not nonce or not nonce:match("^[a-f0-9]+\n$") or #nonce ~= 33 or nonce == last then return end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. level or not valid(world.NetDriver) then return end
    last = nonce
    local ok, result = pcall(scan, world, world.NetDriver)
    local reply = assert(io.open(mailbox .. "/rook-mixed-field-reply", "wb"))
    reply:write(nonce, ok and "ok\n" or "error\n", result, "\nend\n")
    reply:close()
end)
