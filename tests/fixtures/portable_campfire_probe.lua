local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-campfire-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local camp = map .. ":PersistentLevel.CampFire_02_BP_C_1"
local flame_class = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Camp_BP.Fire_Camp_BP_C"
local overlay_path = "/Game/Classes/Player/PlayerAssets/PPActor_Damage_BP.PPActor_Damage_BP_C"
local hit_sound = "SoundCue /Game/Assets/Sounds/CUEs/PhysImpact_Cues/BurnDmg_Cue.BurnDmg_Cue"
local blunt_sound = "SoundCue /Game/Assets/Sounds/CUEs/PhysImpact_Cues/Melee_Blunt_Cue.Melee_Blunt_Cue"
local fire_property = "Fire_12_2B415A724C533C075B6BB390B76BF247"
local blunt_property = "BluntForce_6_6923B3964740DE281BD773BCCAAC2772"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C:"
local local_pawn, peer, flame, last, armed, failure
local previous_position = {}
local overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices = 0, 0, 0, 0, 0, 0
local blunt_hits, muted_blunt = 0, 0
local remote_offset = 0
local voice_attenuation = "SoundAttenuation /Game/Assets/Sounds/SoundClasses/Vocal_Attenuation.Vocal_Attenuation"
local voice_replays, voice_origin = {}, nil

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
        elseif valid(cue) and cue:GetFullName() == blunt_sound then
            if same(owner, local_pawn) and volume:get() > 0 then blunt_hits = blunt_hits + 1 end
            if same(owner, peer) and volume:get() <= .001 then muted_blunt = muted_blunt + 1 end
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
    RegisterHook("/Script/Engine.GameplayStatics:PlaySoundAtLocation", watch(function(_, context, sound, point, _, gain, pitch, _, radius)
        local settings, cue = radius:get(), sound:get()
        if not same(context:get(), local_pawn) or not valid(settings) or settings:GetFullName() ~= voice_attenuation then return end
        assert(valid(cue), "the voice relay needs a stock sound")
        local where = point:get()
        voice_replays[#voice_replays + 1] = ("%s,%.6f,%.6f,%.3f,%.3f,%.3f"):format(
            cue:GetFullName(), gain:get(), pitch:get(), where.X, where.Y, where.Z)
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
    pawn:K2_SetActorLocation(destination, false, {}, true)
    local reached = pawn:K2_GetActorLocation()
    local error_squared = (reached.X - destination.X)^2 + (reached.Y - destination.Y)^2 + (reached.Z - destination.Z)^2
    assert(error_squared <= 1, "stock pawn move did not reach the requested location")
    if not entering then previous_position[address] = nil end
end

local function run(action)
    if action == "arm" then return arm() end
    if action == "clear" then
        overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices, failure = 0, 0, 0, 0, 0, 0, nil
        remote_offset, blunt_hits, muted_blunt = 0, 0, 0
        return "cleared"
    end
    if action == "burst-audio" then
        assert(not failure, failure)
        return ("%d,%d"):format(blunt_hits, muted_blunt)
    end
    if action == "burst-peer" then
        assert(local_pawn:HasAuthority() and peer:HasAuthority() and valid(peer.Save) and not same(peer.Save, local_pawn.Save),
            "detached guest unavailable on the host")
        assert(not flame.Capsule:IsOverlappingActor(peer) and peer.Save.PlayerHealth > 66,
            "the guest must be healthy and outside the campfire")
        local point = flame:K2_GetActorLocation()
        local hit = {Location = point, ImpactPoint = point}
        local force = {X = 0, Y = 0, Z = 0}
        local before = peer.Save.PlayerHealth
        peer:DealDamage({[fire_property] = 54}, hit, flame, force, false, {}, {})
        local burned = peer.Save.PlayerHealth
        peer:DealDamage({[blunt_property] = 8}, hit, flame, force, false, {}, {})
        local hit_twice = peer.Save.PlayerHealth
        assert(before - burned >= 40 and burned > hit_twice and hit_twice > 0,
            ("unexpected stock burst health %.4f,%.4f,%.4f"):format(before, burned, hit_twice))
        return ("%.6f,%.6f,%.6f"):format(before, burned, hit_twice)
    end
    if action == "voice-character" then
        local authority = local_pawn:HasAuthority()
        local guest = authority and peer or local_pawn
        local save = guest.Save
        assert(valid(save) and (not authority or not same(save, local_pawn.Save)), "detached guest voice save unavailable")
        local character = save.Character
        character["VoxRef_97_E223354141E070B2E1F41F9E2CB7BD58"] = FName("Fem_02", EFindName.FNAME_Find)
        character["Vox_Pitch_100_3439A01A4F568098E2FC93ADB75FE9D7"] = .94
        guest:Compile_VoxSetup()
        return "voice selected"
    end
    if action == "voice-read" then
        assert(not failure, failure)
        return table.concat(voice_replays, "|")
    end
    if action == "voice-clear" then
        voice_replays = {}
        return "cleared"
    end
    if action == "voice-near" or action == "voice-far" or action == "voice-restore" then
        local authority = local_pawn:HasAuthority()
        local guest, host = authority and peer or local_pawn, authority and local_pawn or peer
        local movement = guest.CharacterMovement
        movement:StopMovementImmediately()
        if action == "voice-restore" then
            assert(voice_origin)
            guest:K2_SetActorLocation(voice_origin, false, {}, true)
            movement:SetMovementMode(1, 0)
            voice_origin = nil
        else
            if not voice_origin then voice_origin = copy(guest:K2_GetActorLocation()) end
            local position = copy(host:K2_GetActorLocation())
            position.Z = position.Z + (action == "voice-near" and 3000 or 6000)
            movement:SetMovementMode(5, 0)
            guest:K2_SetActorLocation(position, false, {}, true)
        end
        return "moved"
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
        local authority = local_pawn:HasAuthority()
        local guest_save = authority and peer.Save
        return ("%.6f,%.6f,%d,%d,%d,%d,%d,%d,%.6f,%s,%s,%.6f,%.6f"):format(
            local_pawn.Save.PlayerHealth, guest_save and guest_save.PlayerHealth or -1,
            overlays, hit_sounds, muted_hits, local_fire, peer_fire, hurt_voices, remote_offset,
            tostring(flame.Capsule:IsOverlappingActor(local_pawn)), tostring(flame.Capsule:IsOverlappingActor(peer)),
            local_pawn.Save.PlayerRegen, guest_save and guest_save.PlayerRegen or -1)
    end
    if action == "regen-policy" then
        assert(local_pawn:HasAuthority() and valid(peer.Save), "authority is required to read stock guest regeneration")
        return ("%.8f,%.8f,%.8f,%.8f"):format(peer.Sickness, peer.RegenDmg_Coeff, peer.Min_RegenRatio, peer.MaxHealth)
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
