local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-npc-override-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local class_path = "/Game/Classes/NPCs/NPC_Drones/NPC_Drone.NPC_Drone_C"
local damage_key = "Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5"
local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
local last, authority_drone

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function run(action, actor_path)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. map and valid(world.NetDriver),
        "the test requires Observatory in an isolated co-op pair")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "local stock pawn unavailable")
    local host = pawn:HasAuthority()
    local class = StaticFindObject(class_path)
    local drone
    if not host or action == "census" or action == "spawn" then
        for _, candidate in ipairs(FindAllOf("NPC_Drone_C") or {}) do
            if valid(candidate) and not candidate:IsActorBeingDestroyed() and same(candidate:GetWorld(), world) then
                assert(not drone, "unexpected stock drone already in the test world")
                drone = candidate
            end
        end
    end
    if action == "census" then return drone and drone:GetFullName() or "empty" end
    if action == "spawn" then
        assert(host and not drone and not valid(authority_drone), "only the isolated host may spawn one fixture drone")
        if not valid(class) then LoadAsset(class_path); class = StaticFindObject(class_path) end
        assert(valid(class), "stock drone class unavailable")
        local origin = pawn:K2_GetActorLocation()
        local transform = {Translation={X=origin.X+180,Y=origin.Y,Z=origin.Z+180},
            Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
        local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
        assert(valid(actor) and same(actor:GetClass(), class) and same(actor:GetWorld(), world), "stock drone spawn unavailable")
        actor.bAlwaysRelevant = true
        assert(same(actor, statics:FinishSpawningActor(actor, transform, 0)) and actor:GetLocalRole() == 3,
            "stock drone did not finish on the authority")
        actor:SetReplicates(true)
        actor:SetReplicateMovement(true)
        actor:ForceNetUpdate()
        assert(actor.bReplicates and actor.Health > 0, "fixture drone is not alive and replicated")
        authority_drone = actor
        return actor:GetFullName():match("^NPC_Drone_C (.+)$")
    end

    assert(actor_path and actor_path:match("^" .. map:gsub("%.", "%%.") .. ":PersistentLevel%.NPC_Drone_C_%d+$"),
        "expected the exact test drone's path")
    -- Unreal may give a dynamically replicated actor a different name on the guest.
    local actor = host and StaticFindObject(actor_path) or drone
    if not valid(actor) or actor:IsActorBeingDestroyed() then return "absent" end
    assert(same(actor:GetClass(), class) and same(actor:GetWorld(), world) and actor.bReplicates
        and actor:GetLocalRole() == (host and 3 or 1), "expected the enrolled stock drone")
    if host then assert(same(actor, authority_drone), "the target is outside the fixture") end
    local before = actor.Health
    if action == "observe" then return ("%.6f"):format(before) end
    if action == "death" then
        local mesh = actor.Mesh
        assert(valid(mesh) and same(mesh:GetOwner(), actor), "stock drone mesh unavailable")
        local markers = 0
        for _, marker in ipairs(FindAllOf("TargetPoint") or {}) do
            if valid(marker) and same(marker:GetOwner(), actor) and marker:GetLocalRole() == (host and 3 or 1) then
                markers = markers + 1
            end
        end
        return ("%s,%s,%d"):format(tostring(actor.Alive), tostring(mesh:IsAnySimulatingPhysics()), markers)
    end

    assert((action == "guest-damage" and not host) or (action == "host-damage" and host), "wrong damage test seat")
    assert(before > 0, "stock drone was already dead")
    local total, lethal = {}, {}
    actor:DealDamage({[damage_key]=1000}, {}, pawn, {X=0,Y=0,Z=0}, false, total, lethal)
    if not host then
        assert(total.TotalDamage == 0 and lethal.IsLethal == false and actor.Health == before and actor.Alive,
            ("stock drone override dealt damage locally: %s %s (%.2f to %.2f)"):format(
                tostring(total.TotalDamage), tostring(lethal.IsLethal), before, actor.Health))
        return "damage denied"
    end
    assert(total.TotalDamage > 0 and lethal.IsLethal == true, "authority was unable to resolve a stock drone kill")
    return ("%.6f"):format(total.TotalDamage)
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(400)
    request:close()
    local ticket, action, actor_path = (data or ""):match("^(%x+)\t([a-z%-]+)\t?([^\r\n]*)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action, actor_path)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
