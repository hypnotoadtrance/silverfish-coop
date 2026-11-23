local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or not mailbox:match("[/\\]portable%-title%-%x+[\\/]second[\\/]")
    or not mailbox:match("[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local path = map .. ":PersistentLevel.LootCrate_Small_BP_C_1"
local command, reply = mailbox .. "/portable-crate-command.tsv", mailbox .. "/portable-crate-reply.tsv"
local last

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function press()
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
    local request = io.open(command, "rb")
    if not request then return end
    local line = request:read(64)
    request:close()
    local ticket = line and line:match("^(%x+)\tpress$")
    if not ticket or #ticket ~= 32 or last == ticket then return end
    last = ticket
    local ok, result = pcall(press)
    local output = io.open(reply, "wb")
    if output then
        output:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(result):gsub("[\r\n\t]", " "))
        output:close()
    end
end)
