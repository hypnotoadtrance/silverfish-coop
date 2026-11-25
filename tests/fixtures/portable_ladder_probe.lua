local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-ladder-"
local world_name = "World /Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local ladder_class = "BlueprintGeneratedClass /Game/Classes/Actors/Ladder_BP.Ladder_BP_C"
local ladder_path = "/Game/Classes/Actors/Ladder_BP.Ladder_BP_C:"
local pawn_tick = "/Game/Classes/Player/Player_FP.Player_FP_C:ReceiveTick"
local stock_press = "/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact"
local track, last, installed_hooks
local clear = {R = 0, G = 0, B = 0, A = 0}

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function stage()
    local view = FindFirstOf("GameViewportClient")
    local world = valid(view) and view.World
    assert(valid(world) and world:GetFullName() == world_name, "stock Tutorial Dock is unavailable")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "local pawn is unavailable")
    local ladders, peer = {}, nil
    for _, candidate in ipairs(FindAllOf("Ladder_BP_C") or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and candidate:GetClass():GetFullName() == ladder_class
            and valid(candidate.Box) and same(candidate.Box:GetOwner(), candidate) and valid(candidate.Arrow_Bottom) then
            ladders[#ladders + 1] = candidate
        end
    end
    assert(#ladders > 0, "the dock has no loaded stock ladder")
    table.sort(ladders, function(a, b) return a:GetFullName() < b:GetFullName() end)
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and not same(candidate, pawn)
            and not candidate:IsActorBeingDestroyed() then
            assert(not peer, "ladder fixture requires one remote pawn")
            peer = candidate
        end
    end
    assert(valid(peer), "remote pawn is unavailable")
    return world, statics, pawn, peer, ladders
end

local function aim(pawn, target)
    local eye = pawn.Camera:K2_GetComponentLocation()
    local x, y, z = target.X - eye.X, target.Y - eye.Y, target.Z - eye.Z
    pawn.Camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, math.sqrt(x*x+y*y))),
        Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
    pawn:LookTrace()
end

local function approach(pawn, ladders)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local half = pawn.CapsuleComponent:GetScaledCapsuleHalfHeight()
    for _, ladder in ipairs(ladders) do
        local box, foot = ladder.Box, ladder.Arrow_Bottom:K2_GetComponentLocation()
        local point = box:K2_GetComponentLocation()
        local facing = ladder:K2_GetActorRotation()
        for _, distance in ipairs({85, 125, 165}) do
            for _, angle in ipairs({0, 90, 180, 270, 45, 135, 225, 315}) do
                local heading = math.rad(facing.Yaw + angle)
                local x, y = point.X + distance * math.cos(heading), point.Y + distance * math.sin(heading)
                local result = {}
                local hit = trace:LineTraceSingle(pawn, {X=x,Y=y,Z=foot.Z+76}, {X=x,Y=y,Z=foot.Z-183}, 0, false,
                    {pawn, ladder}, 0, result, true, clear, clear, 0)
                local floor = result.OutHit or result
                if hit and floor.ImpactNormal.Z >= .7 and pawn:K2_TeleportTo(
                    {X=x,Y=y,Z=floor.ImpactPoint.Z + half + 3}, {Yaw=facing.Yaw,Pitch=0,Roll=0}) then
                    aim(pawn, point)
                    if pawn.CanInteract and same(pawn.InteractionTarget_Component, box) then return ladder end
                end
            end
        end
    end
    error("could not place the player on a clear floor facing a stock ladder")
end

local function tap(pawn, ladder)
    aim(pawn, ladder.Box:K2_GetComponentLocation())
    assert(pawn.CanInteract and same(pawn.InteractionTarget_Component, ladder.Box), "stock F is aimed away from the ladder")
    local library = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
    local action = StaticFindObject(stock_press)
    pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(library:MakeInputActionValueOfType(1,0,0,0), 0,0,action)
    pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(library:MakeInputActionValueOfType(0,0,0,0), .05,.05,action)
end

local function read(pawn)
    local point = pawn:K2_GetActorLocation()
    return ("%s,%d,%.2f,%.2f,%.2f"):format(tostring(pawn.OnLadder), pawn.CharacterMovement.MovementMode,
        point.X, point.Y, point.Z)
end

local function sample()
    if not track or not track.pressed or not valid(track.pawn) or not same(track.pawn:GetWorld(), track.world) then return end
    local now = track.statics:GetRealTimeSeconds(track.world)
    local age = now - track.pressed
    if age <= 1.5 and #track.points < 64 then
        track.points[#track.points + 1] = ("%.3f,%s"):format(age, read(track.pawn))
    end
end

local function run(action, payload)
    if action == "arm" then
        local world, statics, pawn, peer, ladders = stage()
        local ladder = not pawn:HasAuthority() and approach(pawn, ladders) or nil
        track = {world=world,statics=statics,pawn=pawn,peer=peer,ladder=ladder,ladders=ladders,points={},events={}}
        if ladder and not installed_hooks then
            RegisterHook(pawn_tick, function(context)
                if track and track.climb_until and same(context:get(), track.pawn)
                    and track.pawn.OnLadder and same(track.world, track.pawn:GetWorld())
                    and track.statics:GetRealTimeSeconds(track.world) < track.climb_until then
                    track.pawn:Add_Movement_Input({X=0,Y=1})
                end
            end)
            for _, name in ipairs({"Start/Stop_Climbing",
                    "BndEvt__Box1_K2Node_ComponentBoundEvent_3_ComponentBeginOverlapSignature__DelegateSignature",
                    "BndEvt__Dismount_Top_K2Node_ComponentBoundEvent_2_ComponentBeginOverlapSignature__DelegateSignature"}) do
                RegisterHook(ladder_path .. name, function(context, value)
                    if track and track.pressed and #track.events < 12 and same(context:get(),track.ladder) then
                        local elapsed = track.statics:GetRealTimeSeconds(track.world) - track.pressed
                        local detail = name == "Start/Stop_Climbing" and tostring(value:get()) or "overlap"
                        track.events[#track.events + 1] = ("%.3f,%s,%s"):format(elapsed,name,detail)
                    end
                end)
            end
            installed_hooks = true
        end
        return ladder and (ladder:GetFullName() .. ";" .. read(pawn)) or "armed"
    end
    assert(track and valid(track.pawn) and same(track.pawn:GetWorld(),track.world), "stale ladder fixture")
    if action == "local" then return read(track.pawn) end
    if action == "place" then
        assert(track.pawn:HasAuthority(), "the host must position the remote pawn")
        local x,y,z = payload:match("^([-%d.]+),([-%d.]+),([-%d.]+)$")
        assert(x and y and z, "fixture pose is missing")
        local point = {X=tonumber(x),Y=tonumber(y),Z=tonumber(z)}
        local near = false
        for _, ladder in ipairs(track.ladders) do
            local pos = ladder.Box:K2_GetComponentLocation()
            if (pos.X-point.X)^2 + (pos.Y-point.Y)^2 + (pos.Z-point.Z)^2 < 230^2 then near = true end
        end
        assert(near and track.peer:K2_TeleportTo(point, track.peer:K2_GetActorRotation()),
            "the host could not place the guest at the chosen ladder")
        track.peer:ForceNetUpdate()
        return read(track.peer)
    end
    if action == "press" then
        assert(valid(track.ladder) and not track.pawn:HasAuthority(), "guest ladder is unavailable")
        track.pressed = track.statics:GetRealTimeSeconds(track.world)
        tap(track.pawn, track.ladder)
        return read(track.pawn)
    end
    if action == "climb" then
        assert(track.pawn.OnLadder, "guest has not mounted the ladder")
        track.climb_until = track.statics:GetRealTimeSeconds(track.world) + .75
        return read(track.pawn)
    end
    if action == "remote" then return read(track.peer) end
    if action == "report" then return table.concat(track.points,"|") .. ";" .. table.concat(track.events,"|") end
    error("unknown ladder fixture command")
end

LoopInGameThreadWithDelay(25, function()
    sample()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local line = file:read(100)
    file:close()
    local ticket, action, payload = line:match("^(%x+)\t([a-z%-]+)\t?(.*)$")
    if not ticket or #ticket ~= 32 or last == ticket then return end
    last = ticket
    local ok, result = pcall(run, action, payload)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(result):gsub("[\r\n\t]"," ")
    reply:write(ticket,"\t",ok and "ok" or "error","\t",message)
    reply:close()
end)

