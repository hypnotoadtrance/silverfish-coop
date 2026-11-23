local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
local lane = mailbox and mailbox:match("[/\\]portable%-title%-%x+[/\\]([a-z]+)[/\\]")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or (lane ~= "first" and lane ~= "second")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local path = map .. ":PersistentLevel.Prop_Lantern_01_C_1"
local command, reply = mailbox .. "/portable-lantern-command.tsv", mailbox .. "/portable-lantern-reply.tsv"
local color = {R = 0, G = 0, B = 0, A = 0}
local last

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function sweep_lantern(world, driver, pawn, lantern, root)
    local controller, grip = pawn.Controller, pawn.PhysicsHandle
    assert(valid(driver.ServerConnection) and pawn:IsLocallyControlled() and not pawn:HasAuthority()
        and valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn)
        and valid(grip) and same(grip:GetGrabbedComponent(), root) and same(pawn.HeldObject, lantern),
        "unqualified local guest lantern grip")
    local rotation = controller:GetControlRotation()
    local start_yaw, pitch, roll = rotation.Yaw, rotation.Pitch, rotation.Roll
    local began = os.clock()
    LoopInGameThreadWithDelay(25, function()
        if not same(world.NetDriver, driver) or not same(controller.Pawn, pawn) or pawn.Dead ~= false
            or not same(pawn.HeldObject, lantern) then return true end
        local fraction = math.min((os.clock() - began) / 1.2, 1)
        controller:SetControlRotation({Pitch = pitch, Yaw = start_yaw + 65 * fraction, Roll = roll})
        return fraction >= 1
    end)
    return "sweeping"
end

local function verify(action, pawn_name)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    local lantern = StaticFindObject(path)
    local root = valid(lantern) and lantern.PropMesh
    local mesh = StaticFindObject("/Script/Engine.StaticMeshComponent")
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver)
        and valid(lantern) and same(lantern:GetWorld(), world) and same(lantern:GetOuter(), world.PersistentLevel)
        and lantern:HasAnyFlags(EObjectFlags.RF_WasLoaded)
        and not lantern:IsActorBeingDestroyed() and lantern.bReplicateMovement == true
        and same(root, lantern.RootComponent) and valid(mesh) and root:IsA(mesh),
        "unqualified authored Observatory lantern")
    local pawn_path = pawn_name:match("^Player_FP_C (.+)$")
    local pawn = pawn_path and StaticFindObject(pawn_path)
    assert(valid(pawn) and pawn:GetFullName() == pawn_name and same(pawn:GetWorld(), world) and pawn.Dead == false,
        "unqualified Observatory guest pawn")
    if lane == "second" then
        assert(action == "sweep" and not lantern:HasAuthority(), "unexpected guest lantern probe action")
        return sweep_lantern(world, driver, pawn, lantern, root)
    end
    assert(not valid(driver.ServerConnection) and #driver.ClientConnections == 1
        and lantern:HasAuthority() and pawn:HasAuthority() and not pawn:IsLocallyControlled(),
        "unqualified authority guest")
    if action == "approach" then
        assert(not valid(pawn.HeldObject), "the guest is not empty-handed")
        local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local origin, eye = pawn:K2_GetActorLocation(), pawn.Camera:K2_GetComponentLocation()
        for _, heading in ipairs({180, 270, 90, 0}) do
            local bearing = math.rad(heading)
            local target = {X = origin.X + 110 * math.cos(bearing), Y = origin.Y + 110 * math.sin(bearing), Z = origin.Z - 25}
            if api:LineTraceSingle(pawn, eye, target, 1, true, {pawn, lantern}, 0, {}, true, color, color, 0) == false
                and lantern:K2_SetActorLocation(target, false, {}, true) then
                lantern:ForceNetUpdate()
                return string.format("%.3f %.3f %.3f", origin.X, origin.Y, origin.Z)
            end
        end
        error("no clear lantern approach")
    end
    assert(action == "sample", "unexpected lantern probe action")
    local point, grip = root:K2_GetComponentLocation(), pawn.PhysicsHandle
    local held = same(pawn.HeldObject, lantern) and valid(grip) and same(grip:GetGrabbedComponent(), root)
    return string.format("%d %.3f %.3f %.3f", held and 1 or 0, point.X, point.Y, point.Z)
end

LoopInGameThreadWithDelay(50, function()
    local input = io.open(command, "rb")
    if not input then return end
    local request = input:read(513)
    input:close()
    if not request or #request > 512 then return end
    local ticket, action, pawn = request:match("^(%x+)\t([a-z]+)\t([^\r\n]+)$")
    if not ticket or #ticket ~= 32 or last == ticket then return end
    last = ticket
    local ok, result = pcall(verify, action, pawn)
    local output = io.open(reply, "wb")
    if output then
        output:write(ticket, "\t", ok and "ok" or "error", "\t", (tostring(result):gsub("[\r\n\t]", " ")))
        output:close()
    end
end)
