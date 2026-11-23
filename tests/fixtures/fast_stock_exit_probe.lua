-- Test-only approach and actual capsule sweep for the exact Observatory exit.
local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"
    or not box or not box:match("authored%-route%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local wall = require("zone_wall")
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local actor_name = "Level_Exit_BP_C_1"
local prefix = box .. "/fast-stock-exit"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local trigger_class = "/Game/Classes/ScriptingActors/Level_Exit_BP.Level_Exit_BP_C"
local color = {R = 0, G = 0, B = 0, A = 0}
local ticket_seen

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function distance(a, b) return math.sqrt((a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2) end

local function fixture(action, name)
    local exit = StaticFindObject(world_path .. ":PersistentLevel." .. actor_name)
    local world = valid(exit) and exit:GetWorld()
    local driver = valid(world) and world.NetDriver
    local trigger = valid(exit) and exit.Box
    assert(same(valid(exit) and exit:GetClass(), StaticFindObject(trigger_class)) and valid(driver)
        and not valid(driver.ServerConnection) and #driver.ClientConnections == 1
        and same(exit:GetOuter(), world.PersistentLevel) and exit:HasAuthority()
        and exit:HasAnyFlags(EObjectFlags.RF_WasLoaded) and exit["Entrance Tag"]:ToString() == "Obsv_Etr"
        and valid(trigger) and same(trigger:GetOwner(), exit) and trigger:GetGenerateOverlapEvents(),
        "The exact stock Observatory overlap is unavailable")
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local host = api:GetPlayerPawn(world, 0)
    local guest_path = name:match("^Player_FP_C (.+)$")
    local guest = guest_path and StaticFindObject(guest_path)
    local class = StaticFindObject(pawn_class)
    assert(valid(host) and valid(guest) and same(host:GetClass(), class) and same(guest:GetClass(), class)
        and same(host:GetWorld(), world) and same(guest:GetWorld(), world) and guest:GetFullName() == name
        and host:IsLocallyControlled() and not guest:IsLocallyControlled() and host:HasAuthority() and guest:HasAuthority()
        and not host.Dead and not guest.Dead, "The exact standing host and guest are required")
    assert(not trigger:IsOverlappingActor(host), "The host was already in the overlap")
    local start = host:K2_GetActorLocation()
    local center, normal = trigger:K2_GetComponentLocation(), exit:GetActorForwardVector()
    local sign = ((start.X - center.X)*normal.X + (start.Y - center.Y)*normal.Y) > 0 and 1 or -1
    local x, y = normal.X * sign, normal.Y * sign
    if action == "place" then
        local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local height = guest.CapsuleComponent:GetScaledCapsuleHalfHeight()
        local old, facing = guest:K2_GetActorLocation(), guest:K2_GetActorRotation()
        local moved
        local function ground(dx, dy, span)
            local px, py = start.X + dx*span, start.Y + dy*span
            local hit = {}
            if trace:LineTraceSingle(host, {X=px,Y=py,Z=start.Z+180}, {X=px,Y=py,Z=start.Z-285}, 0, false,
                {host, guest, exit}, 0, hit, true, color, color, 0) then
                hit = hit.OutHit or hit
                local component = hit.Component and hit.Component:Get()
                if valid(component) and component:GetCollisionObjectType() == 0 and hit.ImpactNormal.Z >= .7 then
                    return {X=px,Y=py,Z=hit.ImpactPoint.Z+height+3}
                end
            end
        end
        for _, angle in ipairs({0, 20, -20, 35, -35}) do
            local radians = math.rad(angle)
            local dx = x * math.cos(radians) - y * math.sin(radians)
            local dy = x * math.sin(radians) + y * math.cos(radians)
            local first = ground(dx, dy, 1480)
            if first then
                local point = ground(dx, dy, math.sqrt(1480^2 - (first.Z-start.Z)^2))
                if point and guest:K2_TeleportTo(point, facing) then
                    moved = true
                    local at = distance(start, guest:K2_GetActorLocation())
                    if at >= 1470 and at <= 1490 and not trigger:IsOverlappingActor(guest) then
                        guest:ForceNetUpdate()
                        return ("%.2f"):format(at)
                    end
                end
            end
        end
        if moved then assert(guest:K2_TeleportTo(old, facing), "Fixture could not restore the guest") end
        error("No safe stock terrain at the admitted 14.8 m approach")
    end
    assert(action == "cross", "Unknown boundary operation")
    local admitted = wall.rows(function(...) return {...} end)[1]
    assert(admitted[1] == "native-zone-wall" and admitted[2] == "ready" and admitted[3] == 1,
        "The native authority did not open the exact approach wall")
    local peer = guest:K2_GetActorLocation()
    local before = distance(start, peer)
    assert(before >= 1465 and before < 1500, ("Unexpected admission distance %.2f"):format(before))
    host:K2_SetActorLocation({X=start.X-x*80,Y=start.Y-y*80,Z=start.Z}, true, {}, false)
    local step = host:K2_GetActorLocation()
    local beyond = distance(step, peer)
    assert(distance(start, step) >= 50 and beyond > 1500 and not trigger:IsOverlappingActor(host),
        ("Fixture needed a distinct step outside the volume (before %.2f, after %.2f, moved %.2f, overlap %s)")
        :format(before, beyond, distance(start, step), tostring(trigger:IsOverlappingActor(host))))
    local result = wall.command({"fixture", "native-exit-wall-probe", "local", "Obsv_Etr", actor_name},
        {world=world,map=map,server=false,listening=true,pawns={{pawn=host,slot="local",kind="local"}}}, "hosting")
    local final = distance(host:K2_GetActorLocation(), guest:K2_GetActorLocation())
    assert(result == "native exit sweep clear at stock " .. actor_name and final > 1500, result)
    return ("%.2f,%.2f,%.2f,%s"):format(before, beyond, final, result)
end

LoopInGameThreadWithDelay(50, function()
    local input = io.open(prefix .. "-command.tsv", "rb")
    if not input then return end
    local record = input:read(1025)
    input:close()
    if not record or #record > 1024 then return end
    local ticket, action, guest = record:match("^(%x+)\t([a-z]+)\t([^\r\n]+)")
    if not ticket or #ticket ~= 32 or ticket == ticket_seen then return end
    ticket_seen = ticket
    local ok, result = pcall(fixture, action, guest)
    local out = assert(io.open(prefix .. "-reply.tsv", "wb"))
    out:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(result):gsub("[\r\n\t]", " ")))
    out:close()
end)
