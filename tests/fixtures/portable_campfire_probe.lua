local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-campfire-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local camp = map .. ":PersistentLevel.CampFire_02_BP_C_1"
local flame_class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Camp_BP.Fire_Camp_BP_C"
local overlay_path = "/Game/Classes/Player/PlayerAssets/PPActor_Damage_BP.PPActor_Damage_BP_C"
local hit_sound = "SoundCue /Game/Assets/Sounds/CUEs/PhysImpact_Cues/BurnDmg_Cue.BurnDmg_Cue"
local fire_property = "Fire_12_2B415A724C533C075B6BB390B76BF247"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C:"
local local_pawn, peer, flame, last, armed, failure
local previous_position = {}
local overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices = 0, 0, 0, 0, 0, 0
local remote_offset = 0

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function copy(point) return {X = point.X, Y = point.Y, Z = point.Z} end

local function watch(callback)
    return function(...)
        local ok, error_message = pcall(callback, ...)
        if not ok then failure = error_message end
    end
end

local function arm()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. map, "stock Observatory unavailable")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local_pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(local_pawn) and local_pawn:IsLocallyControlled() and same(local_pawn:GetWorld(), world), "local player unavailable")
    peer = nil
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() and not candidate:IsLocallyControlled() then
            assert(not peer, "multiple remote players in the campfire fixture")
            peer = candidate
        end
    end
    assert(valid(peer), "remote player unavailable")
    local firepit = StaticFindObject(camp)
    flame = valid(firepit) and firepit.FireRef
    assert(valid(flame) and same(flame:GetWorld(), world) and flame:GetClass():GetFullName() == flame_class
        and valid(flame.Capsule) and same(flame.Capsule:GetOwner(), flame), "the placed Observatory campfire is not burning")
    if armed then return "armed" end
    armed = true

    local function read_overlay(effect)
        if valid(effect) and effect:GetClass():GetFullName() == "BlueprintGeneratedClass " .. overlay_path
            and same(effect.Player, local_pawn) and valid(effect.PostProcess_FX) and effect.PostProcess_FX.bEnabled == true then
            overlays = overlays + 1
        end
    end
    RegisterBeginPlayPostHook(watch(function(context) read_overlay(context:get()) end))
    if not valid(StaticFindObject(overlay_path)) then LoadAsset(overlay_path) end
    RegisterHook(overlay_path .. ":Reset", watch(function(context) read_overlay(context:get()) end))
    RegisterHook("/Script/Engine.GameplayStatics:PlaySound2D", watch(function(_, source, sound, volume)
        local owner, cue = source:get(), sound:get()
        if valid(cue) and cue:GetFullName() == hit_sound then
            if same(owner, local_pawn) and volume:get() > 0 then hit_sounds = hit_sounds + 1 end
            if same(owner, peer) and volume:get() <= .001 then muted_hits = muted_hits + 1 end
        end
    end))
    RegisterHook(pawn_path .. "Pulse_Dmg_FX", watch(function(context, _, direction, _, damage)
        local victim = context:get()
        if not same(victim, local_pawn) and not same(victim, peer) then return end
        local payload = damage:get()
        if payload and (tonumber(payload[fire_property]) or 0) > 0 then
            if same(victim, local_pawn) then
                local_fire = local_fire + 1
            else
                peer_fire = peer_fire + 1
                local offset = direction:get()
                remote_offset = math.max(math.abs(offset.X), math.abs(offset.Y), math.abs(offset.Z))
            end
        end
    end))
    RegisterHook(pawn_path .. "Speak", watch(function(context, cue)
        if same(context:get(), local_pawn) and same(cue:get(), local_pawn.Vox_Hurt) then hurt_voices = hurt_voices + 1 end
    end))
    return "armed"
end

local function move(pawn, entering)
    local address = pawn:GetAddress()
    local destination = previous_position[address]
    if entering then
        assert(not destination and valid(pawn.CapsuleComponent), "victim is already in the fire")
        local point = copy(flame.Capsule:K2_GetComponentLocation())
        point.Z = point.Z + pawn.CapsuleComponent:GetScaledCapsuleHalfHeight() * .75
        previous_position[address], destination = copy(pawn:K2_GetActorLocation()), point
    else
        assert(destination, "no position saved for the victim")
    end
    pawn.CharacterMovement:StopMovementImmediately()
    assert(pawn:K2_SetActorLocation(destination, false, {}, true), "stock pawn move was refused")
    if not entering then previous_position[address] = nil end
end

local function run(action)
    if action == "arm" then return arm() end
    if action == "clear" then
        overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices, failure = 0, 0, 0, 0, 0, 0, nil
        remote_offset = 0
        return "cleared"
    end
    if action == "enter-peer" or action == "leave-peer" then
        assert(local_pawn:HasAuthority() and peer:HasAuthority() and valid(peer.Save) and not same(peer.Save, local_pawn.Save),
            "detached guest unavailable on the host")
        move(peer, action == "enter-peer")
        return "moved"
    end
    if action == "enter" or action == "leave" then
        move(local_pawn, action == "enter")
        return "moved"
    end
    if action == "read" then
        assert(not failure, failure)
        local guest_health = local_pawn:HasAuthority() and peer.Save.PlayerHealth or -1
        return ("%.6f,%.6f,%d,%d,%d,%d,%d,%d,%.6f,%s,%s"):format(local_pawn.Save.PlayerHealth, guest_health,
            overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices, remote_offset,
            tostring(flame.Capsule:IsOverlappingActor(local_pawn)), tostring(flame.Capsule:IsOverlappingActor(peer)))
    end
    error("unknown campfire probe action")
end

LoopInGameThreadWithDelay(40, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(96)
    request:close()
    if not data then return end
    local ticket, action = data:match("^(%x+)\t([a-z%-]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
