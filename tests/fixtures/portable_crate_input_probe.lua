local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or not mailbox:match("[/\\]portable%-title%-%x+[\\/]second[\\/]")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local path = map .. ":PersistentLevel.LootCrate_Small_BP_C_1"
local command, reply = mailbox .. "/portable-crate-command.tsv", mailbox .. "/portable-crate-reply.tsv"
local last, motion

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function unwrap(value)
    return type(value.type) == "function" and value:type() == "RemoteUnrealParam" and value:get() or value
end

local function sample_motion()
    local track = motion
    if not track or track.done then return end
    local panel, box, crate = track.panel, track.box, track.crate
    if not valid(panel) or not valid(crate) then return end
    local duplicate
    for _, value in ipairs(crate:K2_GetComponentsByClass(track.mesh_class)) do
        local part = unwrap(value)
        if valid(part) and not track.components[part:GetAddress()] and same(part.StaticMesh, panel.StaticMesh) then
            duplicate = part
            break
        end
    end
    if crate.Looted == true then
        track.done = not duplicate and panel:IsVisible()
        return
    end
    if not duplicate or track.elapsed then return end
    local point = duplicate.RelativeRotation
    local before = track.rotation
    local function difference(value, previous) return math.abs((value - previous + 180) % 360 - 180) end
    if math.max(difference(point.Pitch, before.Pitch), difference(point.Yaw, before.Yaw),
        difference(point.Roll, before.Roll)) <= 1 then return end
    local original = panel.RelativeRotation
    track.safe = difference(original.Pitch, before.Pitch) < .05 and difference(original.Yaw, before.Yaw) < .05
        and difference(original.Roll, before.Roll) < .05 and panel:GetCollisionEnabled() == track.panel_collision
        and valid(box) and box:GetCollisionEnabled() == track.box_collision and not panel:IsVisible()
        and duplicate:IsVisible() and duplicate:GetCollisionEnabled() == 0
    track.elapsed = (track.statics:GetRealTimeSeconds(track.world) - track.pressed) * 1000
end

local function press(mode)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = valid(statics) and valid(world) and statics:GetPlayerPawn(world, 0)
    local crate = StaticFindObject(path)
    local box = valid(crate) and crate.Box
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(driver) and valid(driver.ServerConnection)
        and valid(pawn) and pawn:IsLocallyControlled() and not pawn:HasAuthority() and pawn.Dead == false
        and valid(crate) and not crate:HasAuthority() and same(crate:GetWorld(), world) and crate.Looted == false
        and valid(box) and same(box:GetOwner(), crate), "unqualified Observatory crate or guest")
    local camera = pawn.Camera
    local eye, point = camera:K2_GetComponentLocation(), box:K2_GetComponentLocation()
    local original = camera.RelativeRotation
    local restore = {Pitch = original.Pitch, Yaw = original.Yaw, Roll = original.Roll}
    local x, y, z = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
    local ok, detail = pcall(function()
        camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))),
            Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
        pawn:LookTrace()
        assert(pawn.CanInteract == true and same(pawn.InteractionTarget_Component, box)
            and pawn.Holding_Interact == false, "stock F is not focused on the authored crate")
        local input = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
        local action = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact")
        assert(valid(input) and valid(action), "stock Interact is unavailable")
        local started = input:MakeInputActionValueOfType(1, 0, 0, 0)
        local completed = input:MakeInputActionValueOfType(0, 0, 0, 0)
        if mode == "coldpress" then SilverfishClearNativeLootCrateInput() end
        local panel = crate.SM_Prop_Cabinet_Small_Door_01
        local mesh_class = StaticFindObject("/Script/Engine.StaticMeshComponent")
        assert(valid(panel) and valid(mesh_class), "stock crate lid probe unavailable")
        local components = {}
        for _, value in ipairs(crate:K2_GetComponentsByClass(mesh_class)) do
            local part = unwrap(value)
            if valid(part) then components[part:GetAddress()] = true end
        end
        local angles = panel.RelativeRotation
        motion = {crate = crate, box = box, panel = panel, mesh_class = mesh_class, statics = statics, world = world,
            components = components, panel_collision = panel:GetCollisionEnabled(), box_collision = box:GetCollisionEnabled(),
            rotation = {Pitch = angles.Pitch, Yaw = angles.Yaw, Roll = angles.Roll}, pressed = statics:GetRealTimeSeconds(world)}
        pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(started, 0, 0, action)
        pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(completed, .05, .05, action)
        return crate:GetFullName()
    end)
    camera:K2_SetRelativeRotation(restore, false, {}, true)
    pawn:LookTrace()
    assert(ok, detail)
    return detail
end

LoopInGameThreadWithDelay(25, function()
    pcall(sample_motion)
    local request = io.open(command, "rb")
    if not request then return end
    local line = request:read(64)
    request:close()
    local ticket, mode
    if line then ticket, mode = line:match("^(%x+)\t(%a+)$") end
    if mode ~= "press" and mode ~= "coldpress" and mode ~= "motion" then return end
    if not ticket or #ticket ~= 32 or last == ticket then return end
    last = ticket
    local ok, result
    if mode == "motion" then
        ok, result = true, motion and motion.done and ("%.2f,%s"):format(motion.elapsed or -1, tostring(motion.safe)) or "pending"
    else
        ok, result = pcall(press, mode)
    end
    local output = io.open(reply, "wb")
    if output then
        output:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(result):gsub("[\r\n\t]", " "))
        output:close()
    end
end)
