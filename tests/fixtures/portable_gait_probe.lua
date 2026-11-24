local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-gait-"
local dock = "World /Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local pistol = "/Game/Classes/Weapons/Wep_RustyPistol.Wep_RustyPistol_C"
local blend = "BlendSpace1D /Game/Classes/NPCs/NPC_Assets/Animations/HandGun_Lowered_BlendSpace.HandGun_Lowered_BlendSpace"
local anim_class = "Class /Script/Engine.AnimSingleNodeInstance"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local clock_path = "/Script/Engine.Default__GameplayStatics"
local trace_path = "/Script/Engine.Default__KismetSystemLibrary"
local color = {R=0,G=0,B=0,A=0}
local installed, observer, lane, last

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function bit(value) return value and 1 or 0 end

local function scope()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == dock and valid(world.NetDriver), "expected the private Dock")
    local statics = StaticFindObject(clock_path)
    assert(valid(statics))
    local driver = world.NetDriver
    local guest = valid(driver.ServerConnection) == true
    local pawn = statics:GetPlayerPawn(world, 0)
    if not guest then
        local peers = driver.ClientConnections
        assert(peers:GetArrayNum() == 1 and valid(peers[1].PlayerController), "expected one admitted guest")
        pawn = peers[1].PlayerController.Pawn
    end
    if not valid(pawn) or pawn:IsActorBeingDestroyed() then return end
    assert(same(pawn:GetWorld(), world) and pawn:IsLocallyControlled() == guest and pawn:HasAuthority() ~= guest)
    local weapon = pawn.EquipedItem
    if not valid(weapon) or weapon:IsActorBeingDestroyed() or not same(weapon:GetClass(), StaticFindObject(pistol)) then return end
    assert(same(weapon:GetWorld(), world) and same(weapon:GetAttachParentActor(), pawn)
        and (same(weapon:GetOwner(), pawn) or same(weapon.User, pawn)), "expected the guest's selected stock pistol")
    return world, pawn, weapon, guest
end

local function hand_body(node)
    for _ = 1, 4 do
        local parent = node:GetAttachParent()
        if not valid(parent) then return end
        local socket = node:GetAttachSocketName()
        if socket:ToString() == "Weapon_Socket01" and parent:DoesSocketExist(socket) then return parent, socket end
        node = parent
    end
end

local function hand_copy(pawn, weapon)
    local original = weapon.Mesh_Frame
    if not valid(original) or not valid(original.StaticMesh) or not original.bOnlyOwnerSee then return end
    local hand, body, socket
    for _, raw in ipairs(pawn:K2_GetComponentsByClass(StaticFindObject(mesh_class)) or {}) do
        local copy = raw
        if type(copy) == "userdata" and type(copy.type) == "function" and copy:type() == "RemoteUnrealParam" then
            copy = copy:get()
        end
        if valid(copy) and same(copy:GetOwner(), pawn) and same(copy.StaticMesh, original.StaticMesh)
            and copy:IsVisible() and not copy.bHiddenInGame and not copy.bOnlyOwnerSee then
            local parent, slot = hand_body(copy)
            if parent then
                assert(not hand and same(parent:GetOwner(), pawn), "the stock pistol has an ambiguous displayed hand")
                hand, body, socket = copy, parent, slot
            end
        end
    end
    return hand, body, socket
end

local function record_blend()
    if installed then return end
    RegisterHook("/Script/Engine.AnimSingleNodeInstance:SetBlendSpacePosition", function(context, position)
        if not observer then return end
        local instance = context:get()
        if not valid(instance) or instance:GetAddress() ~= observer.instance then return end
        local body = instance:GetOwningComponent()
        if not valid(body) or body:GetAddress() ~= observer.body then return end
        local world = body:GetWorld()
        if not valid(world) or world:GetAddress() ~= observer.world then return end
        local asset = instance.CurrentAsset
        if not valid(asset) or asset:GetAddress() ~= observer.asset then return end
        local axis = position:get()
        local inputs = observer.inputs
        if axis.Y == 0 and axis.Z == 0 and #inputs < 12 and axis.X ~= inputs[#inputs] then
            inputs[#inputs + 1] = axis.X
        end
    end)
    installed = true
end

local function position_row(pawn)
    local point, velocity, movement = pawn:K2_GetActorLocation(), pawn:GetVelocity(), pawn.CharacterMovement
    return table.concat({point.X, point.Y, point.Z, math.sqrt(velocity.X^2 + velocity.Y^2),
        bit(pawn.Sprinting), bit(pawn.bIsCrouched), bit(pawn.Aiming), bit(movement:IsMovingOnGround()),
        movement.MaxWalkSpeed, pawn.Speed_Walking, pawn.Speed_Sprinting, pawn.MovementSpeedMod,
        pawn.Save.PlayerFood}, ";")
end

local function observe(action, world, pawn, weapon)
    assert(action == "arm" or action == "read")
    local hand, body, socket = hand_copy(pawn, weapon)
    local instance = valid(body) and body:GetAnimInstance()
    local asset = valid(instance) and instance.CurrentAsset
    if not valid(asset) or asset:GetFullName() ~= blend then return "pending" end
    assert(body.AnimationMode == 1 and instance:GetClass():GetFullName() == anim_class
        and same(instance:GetOwningComponent(), body), "expected one stock handgun animation driver")
    if action == "arm" then
        record_blend()
        observer = {world=world:GetAddress(), body=body:GetAddress(), instance=instance:GetAddress(),
            asset=asset:GetAddress(), inputs={}}
    end
    assert(observer)
    local height = body:GetSocketTransform(socket, 2).Translation.Z
    return table.concat({pawn:GetAddress(), weapon:GetAddress(), hand:GetAddress(), body:GetAddress(),
        instance:GetAddress(), asset:GetAddress(), body:GetPosition(), height,
        #observer.inputs > 0 and table.concat(observer.inputs, ",") or "-", position_row(pawn)}, ";")
end

local function open_lane(pawn)
    local eye = pawn:K2_GetActorLocation()
    local start = {X=eye.X,Y=eye.Y,Z=eye.Z+8}
    local capsule = pawn.CapsuleComponent
    local radius, height = capsule:GetScaledCapsuleRadius(), capsule:GetScaledCapsuleHalfHeight()
    local trace = StaticFindObject(trace_path)
    assert(valid(trace) and radius > 10 and height > radius)
    local function supported(x, y)
        local result = {}
        if not trace:LineTraceSingle(pawn, {X=x,Y=y,Z=start.Z+80}, {X=x,Y=y,Z=start.Z-height-120},
            0, false, {pawn}, 0, result, true, color, color, 0) then return false end
        result = result.OutHit or result
        return result.ImpactNormal.Z > .65 and math.abs(result.ImpactPoint.Z - (eye.Z - height)) < 60
    end
    for _, length in ipairs({1750, 1350, 950}) do
        for yaw = 0, 337.5, 22.5 do
            local angle = math.rad(yaw)
            local x, y = math.cos(angle), math.sin(angle)
            local destination = {X=start.X+x*length,Y=start.Y+y*length,Z=start.Z}
            if not trace:CapsuleTraceSingle(pawn, start, destination, radius, height-8, 0, false, {pawn}, 0,
                {}, true, color, color, 0) and supported(destination.X, destination.Y)
                and supported(start.X+x*length*.5, start.Y+y*length*.5) then
                return x, y, yaw, length
            end
        end
    end
    error("the exact guest has no safe grounded Dock lane", 0)
end

local function drive(action, world, pawn, weapon)
    if action == "owner" then
        local frame = weapon.Mesh_Frame
        assert(valid(frame) and same(frame:GetOwner(), weapon) and valid(frame.StaticMesh))
        return table.concat({position_row(pawn), weapon:GetAddress(), frame:GetAddress(), frame.StaticMesh:GetAddress()}, ";")
    end
    if action == "finish" then
        assert(lane and lane.world == world:GetAddress() and lane.pawn == pawn:GetAddress() and valid(pawn.Controller))
        pawn:Action_Sprint(false)
        pawn.MovementInput = lane.inputs
        pawn.Controller:SetControlRotation(lane.control)
        lane = nil
        return "restored"
    end
    assert(valid(pawn.Controller) and pawn.MoveInputs_Enabled and not pawn.Dead
        and not pawn.bIsCrouched and not pawn.Aiming and pawn.CharacterMovement:IsMovingOnGround(),
        "gait witness requires a standing, un-aimed local guest")
    if action == "prepare" then
        assert(not lane and not pawn.Sprinting)
        local x, y, yaw, length = open_lane(pawn)
        local control, inputs = pawn.Controller:GetControlRotation(), pawn.MovementInput
        lane = {world=world:GetAddress(), pawn=pawn:GetAddress(), control={Pitch=control.Pitch,Yaw=control.Yaw,Roll=control.Roll},
            inputs={X=inputs.X,Y=inputs.Y}}
        pawn.Controller:SetControlRotation({Pitch=0,Yaw=yaw,Roll=0})
        return ("%.7f;%.7f;%s"):format(x, y, length)
    end
    assert(lane and lane.world == world:GetAddress() and lane.pawn == pawn:GetAddress())
    assert(action == "walk" or action == "sprint")
    pawn.MovementInput = {X=0,Y=1}
    pawn:Action_Sprint(action == "sprint")
    assert(pawn.Sprinting == (action == "sprint"))
    return position_row(pawn)
end

local function run(action)
    if action == "disarm" then observer = nil return "disarmed" end
    local world, pawn, weapon, guest = scope()
    if not world then return "pending" end
    if guest then return drive(action, world, pawn, weapon) end
    if action == "hostspeed" then
        local owner = StaticFindObject(clock_path):GetPlayerPawn(world, 0)
        assert(valid(owner) and owner:IsLocallyControlled() and owner:HasAuthority(), "expected the stock host player")
        return position_row(owner)
    end
    return observe(action, world, pawn, weapon)
end

LoopInGameThreadWithDelay(40, function()
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(128)
    file:close()
    if not request or request == last then return end
    last = request
    local ticket, action = request:match("^([a-f0-9]+)\t(%a+)$")
    if not ticket or #ticket ~= 32 then return end
    local ok, result = pcall(run, action)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, ok and "\tok\t" or "\tfailed\t", tostring(result), "\n")
    reply:close()
end)
