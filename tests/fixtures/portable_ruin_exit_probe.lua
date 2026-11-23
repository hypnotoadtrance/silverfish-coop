-- Place the private host pair at Crossroads' exact stock Ruin Fields overlap.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" or not mailbox
    or not mailbox:match("[/\\]portable%-title%-%x+[/\\]first[/\\]")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local world_path = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local exit_path = world_path .. ":PersistentLevel.Level_Exit_BP_C_273"
local arrival_path = world_path .. ":PersistentLevel.LevelExit_Spawn_BP_C_273"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local exit_class = "/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C"
local color = {R = 0, G = 0, B = 0, A = 0}
local prefix = mailbox .. "/portable-ruin-exit"
local last

local function valid(o) return o and o:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(o, class) return valid(o) and same(o:GetClass(), StaticFindObject(class)) end

local function offset(point, center, axis)
    return (point.X - center.X) * axis.X + (point.Y - center.Y) * axis.Y + (point.Z - center.Z) * axis.Z
end

local function distance_squared(a, b)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2
end

local function place(action, guest_name)
    assert(action == "guest-near" or action == "host-near" or action == "host-inside", "Unknown exit placement")
    local exit = StaticFindObject(exit_path)
    local world = valid(exit) and exit:GetWorld()
    local driver = valid(world) and world.NetDriver
    local box = valid(exit) and exit.Box
    assert(valid(world) and world:GetFullName() == "World " .. world_path and valid(driver)
        and not valid(driver.ServerConnection) and #driver.ClientConnections == 1
        and exact(exit, exit_class) and same(exit:GetOuter(), world.PersistentLevel) and exit:HasAuthority()
        and exit:HasAnyFlags(EObjectFlags.RF_WasLoaded) and not exit:IsActorBeingDestroyed()
        and exit["Entrance Tag"]:ToString() == "CR_RF" and exact(box, "/Script/Engine.BoxComponent")
        and same(box:GetOwner(), exit) and exit:GetActorEnableCollision() and box:GetGenerateOverlapEvents()
        and box:GetCollisionResponseToChannel(2) == 1, "Unqualified stock Crossroads exit")
    local anchor = StaticFindObject(arrival_path)
    assert(valid(anchor) and same(anchor:GetWorld(), world) and anchor.EntranceTag:ToString() == "CR_RF"
        and valid(anchor.Arrow) and same(anchor.Arrow:GetOwner(), anchor), "Missing stock CR_RF approach")

    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local controller = statics:GetPlayerController(world, 0)
    local host = valid(controller) and controller.Pawn
    local guest_path = guest_name:match("^Player_FP_C (.+)$")
    local guest = guest_path and StaticFindObject(guest_path)
    assert(exact(host, pawn_class) and same(host:GetWorld(), world) and host:HasAuthority() and host:IsLocallyControlled()
        and exact(guest, pawn_class) and guest:GetFullName() == guest_name and same(guest:GetWorld(), world)
        and guest:HasAuthority() and not guest:IsLocallyControlled()
        and not host.Dead and not guest.Dead, "Unqualified authority pair")

    local pawn = action == "guest-near" and guest or host
    local inside = action == "host-inside"
    local c, e = box:K2_GetComponentLocation(), box:GetScaledBoxExtent()
    local f, r, up = box:GetForwardVector(), box:GetRightVector(), box:GetUpVector()
    local n, tangent, width, length = f, r, e.X, e.Y
    if e.Y < e.X then n, tangent, width, length = r, f, e.Y, e.X end
    local reference = anchor.Arrow:K2_GetComponentLocation()
    local direction = offset(reference, c, n) < 0 and -1 or 1
    local capsule = pawn.CapsuleComponent
    assert(valid(capsule) and same(capsule:GetOwner(), pawn) and math.abs(up.Z) > .7)
    local radius, height = capsule:GetScaledCapsuleRadius(), capsule:GetScaledCapsuleHalfHeight()
    local lane_limit = math.max(0, length - radius - 35)
    local lane = math.max(-lane_limit, math.min(lane_limit, offset(reference, c, tangent)))
    local depth = inside and math.max(0, width - radius - 45) or width + radius + 130
    local assigned = action == "guest-near" and 180 or 0
    local top = math.max(reference.Z, c.Z - math.max(0, e.Z - height)) + 325
    local bottom = math.min(reference.Z, c.Z - e.Z) - 400
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local original, facing = pawn:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local moved
    for _, shift in ipairs({assigned, assigned + 125, assigned - 125, 0, 250, -250}) do
        local side = math.max(-lane_limit, math.min(lane_limit, lane + shift))
        local x, y = c.X + depth * direction * n.X + side * tangent.X, c.Y + depth * direction * n.Y + side * tangent.Y
        local result = {}
        local hit = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = top}, {X = x, Y = y, Z = bottom}, 0, false,
            {exit, host, guest}, 0, result, true, color, color, 0)
        result = result.OutHit or result
        local surface = hit and result.Component and result.Component:Get()
        if valid(surface) and surface:GetCollisionObjectType() == 0 and result.ImpactNormal.Z >= .7 then
            local point = {X = x, Y = y, Z = result.ImpactPoint.Z + height + 3}
            local in_box = math.abs(offset(point, c, f)) <= e.X and math.abs(offset(point, c, r)) <= e.Y
                and math.abs(offset(point, c, up)) <= e.Z
            if in_box == inside and (not inside or distance_squared(point, guest:K2_GetActorLocation()) <= 1450^2)
                and pawn:K2_TeleportTo(point, facing) then
                moved = true
                if distance_squared(point, pawn:K2_GetActorLocation()) <= 70^2 and box:IsOverlappingActor(pawn) == inside then
                    pawn:ForceNetUpdate()
                    return exit:GetFullName()
                end
            end
        end
    end
    if moved then assert(pawn:K2_TeleportTo(original, facing), "Could not restore the staged pawn") end
    error("No verified stock overlap position for " .. action)
end

LoopInGameThreadWithDelay(50, function()
    local input = io.open(prefix .. "-command.tsv", "rb")
    if not input then return end
    local request = input:read(1025)
    input:close()
    if not request or #request > 1024 then return end
    local ticket, action, guest = request:match("^(%x+)\t([a-z%-]+)\t([^\r\n]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, result = pcall(place, action, guest)
    local output = assert(io.open(prefix .. "-reply.tsv", "wb"))
    output:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(result):gsub("[\r\n\t]", " "))
    output:close()
end)
