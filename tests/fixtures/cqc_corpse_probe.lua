-- Read the exact stock Dead Bridge CQC body. This fixture never mutates actors.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_NPC_TEST") ~= "1"
    or not mailbox then return end
local profile = mailbox:match("[/\\](dead%-bridge%-cqc%-%x+[^/\\]*)[/\\]run[/\\]game%-bridge[/\\]%x+$")
if not profile or not (profile:match("^dead%-bridge%-cqc%-%x+$") or profile:match("^dead%-bridge%-cqc%-%x+%-late$")) then return end

local world_path = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"
local actor_path = world_path .. ":PersistentLevel.NPC_State_Infantry_CQC_C_0"
local npc_class = "/Game/Classes/NPCs/NPC_State_Infantry_CQC.NPC_State_Infantry_CQC_C"
local request, reply = mailbox .. "/cqc-corpse-probe", mailbox .. "/cqc-corpse-reply"
local last

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function byte(angle) return math.floor(angle / (360 / 256) + .5) % 256 end

local function marker_for(world, actor)
    local class = StaticFindObject("/Script/Engine.TargetPoint")
    local marker
    for _, candidate in ipairs(FindAllOf("TargetPoint") or {}) do
        if valid(candidate) and same(candidate:GetClass(), class) and same(candidate:GetWorld(), world)
            and same(candidate:GetOwner(), actor) and candidate:GetLocalRole() == actor:GetLocalRole()
            and not candidate:IsActorBeingDestroyed() then
            assert(not marker, "Conflicting markers own the exact placed CQC")
            marker = candidate
        end
    end
    return marker
end

local function sample()
    local actor = StaticFindObject(actor_path)
    if not valid(actor) then return "missing_actor" end
    local world = actor:GetWorld()
    local driver = valid(world) and world.NetDriver
    local guest = valid(driver) and valid(driver.ServerConnection)
    assert(valid(world) and world:GetFullName() == "World " .. world_path and valid(driver) and same(driver.World, world)
        and actor:GetFullName() == "NPC_State_Infantry_CQC_C " .. actor_path
        and same(actor:GetClass(), StaticFindObject(npc_class)) and same(actor:GetOuter(), world.PersistentLevel)
        and (guest or actor:GetLocalRole() == 3 and actor:HasAuthority())
        and not actor:IsActorBeingDestroyed() and type(actor.Alive) == "boolean", "Unqualified stock CQC")
    local mesh, capsule = actor.Mesh, actor.CapsuleComponent
    assert(valid(mesh) and same(mesh:GetOwner(), actor) and valid(capsule) and same(capsule:GetOwner(), actor),
        "The exact CQC has no stock mesh or capsule")
    local marker = actor.Alive == false and marker_for(world, actor)
    local number, anchor, bone, simulated_bone = 0, -1, "-", false
    local point = mesh:K2_GetComponentLocation()
    if valid(marker) then
        -- The standard corpse marker packs its ID into roll/yaw and its bone index into pitch.
        local rotation = marker:K2_GetActorRotation()
        number = byte(rotation.Roll) * 256 + byte(rotation.Yaw)
        local code = byte(rotation.Pitch)
        anchor = code <= 60 and code - 1 or code >= 196 and code - 136 or -1
        if anchor >= 0 and anchor < math.min(120, mesh:GetNumBones()) then
            local name = mesh:GetBoneName(anchor)
            bone, simulated_bone = name:ToString(), mesh:IsSimulatingPhysics(name)
            point = mesh:GetSocketLocation(name)
        end
    end
    return table.concat({actor:GetFullName(), ("%x"):format(actor:GetAddress()), ("%x"):format(mesh:GetAddress()),
        actor:GetLocalRole(), actor.Alive and 1 or 0, actor.bHidden and 1 or 0, valid(actor.Controller) and 1 or 0,
        actor:GetActorEnableCollision() and 1 or 0, capsule:GetCollisionEnabled(), mesh:IsVisible() and 1 or 0,
        mesh:GetCollisionEnabled(), mesh:IsAnySimulatingPhysics() and 1 or 0, number, anchor, bone,
        simulated_bone and 1 or 0, ("%.3f,%.3f,%.3f"):format(point.X, point.Y, point.Z)}, "\t")
end

LoopInGameThreadWithDelay(75, function()
    local file = io.open(request, "rb")
    if not file then return end
    local line = file:read(35)
    file:close()
    local ticket = line and line:match("^([0-9a-f]+)\n$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, value = pcall(sample)
    local output = assert(io.open(reply, "wb"))
    output:write(ticket, "\t", ok and "ok" or "error", "\t", tostring(value):gsub("[\r\n]", " "), "\n")
    output:close()
end)
