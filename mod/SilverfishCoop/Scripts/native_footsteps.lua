local footsteps = {}

local sounds = "/Game/Assets/Sounds/"
local attenuation_path = sounds .. "SoundClasses/Player_FootStep_Attenuation.Player_FootStep_Attenuation"
local concurrency_path = sounds .. "SoundClasses/SoundCon_FootSteps.SoundCon_FootSteps"
local spawn_path = "/Script/Engine.GameplayStatics:SpawnSoundAttached"
local cues = {Trash = sounds .. "CUEs/PhysImpact_Cues/Impact_Trash_Cue.Impact_Trash_Cue"}
for _, surface in ipairs({"Brush", "Carpet", "ChainLink", "Default", "Dirt", "Glass", "Goop", "Grass",
    "Metal", "MetalLadder", "Sand", "Stone", "Water_Deep", "Water_Shallow", "Wood"}) do
    local name = "FootStep_" .. surface .. "_Cue"
    cues[surface] = sounds .. "CUEs/FootStep_Cues/" .. name .. "." .. name
end
local surfaces = {}
for surface, path in pairs(cues) do surfaces["SoundCue " .. path] = surface end

local hooked, current, sequence = false, nil, 0

local function valid(object)
    return object and object:IsValid()
end

local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function live_pawn(pawn, world, role)
    return valid(pawn) and pawn:GetLocalRole() == role and not pawn:IsActorBeingDestroyed()
        and same(pawn:GetWorld(), world)
end

local function capture(_, sound, component, _, _, _, _, _stop_with_owner, volume, pitch, _, attenuation, concurrency)
    local scope = current
    if not scope or not same(concurrency:get(), scope.concurrency) then return end
    if not same(component:get(), scope.capsule) or not live_pawn(scope.pawn, scope.world, scope.local_role)
        or not scope.pawn:IsLocallyControlled() or not same(attenuation:get(), scope.attenuation) then return end
    local asset = sound:get()
    local surface = valid(asset) and surfaces[asset:GetFullName()]
    if not surface then return end
    local level, rate = volume:get(), pitch:get()
    if type(level) ~= "number" or type(rate) ~= "number" or not (level > 0 and level <= 4 and rate > 0 and rate <= 1) then return end
    local gain, note = math.floor(level * 1000 + .5), math.floor(rate * 1000 + .5)
    if gain == 0 or note == 0 then return end
    sequence = sequence % 4294967295 + 1
    SilverfishPublishLocalWeaponCue(scope.map, table.concat({"step", sequence, surface, gain, note, scope.name}, "|"), 160)
end

function footsteps.sync(state, phase, player_id)
    if type(SilverfishPublishLocalWeaponCue) ~= "function" or type(SilverfishTakeRemoteFootsteps) ~= "function" then return end
    local local_role = phase == "hosting" and 3 or phase == "connected" and 2
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not local_role or not live_pawn(pawn, state.world, local_role) or not pawn:IsLocallyControlled() then
        current = nil
        return
    end
    local capsule = pawn.CapsuleComponent
    local attenuation, concurrency = StaticFindObject(attenuation_path), StaticFindObject(concurrency_path)
    if not valid(capsule) or not valid(attenuation) or not valid(concurrency) then
        current = nil
        return
    end
    local peers, remote_role = {}, phase == "hosting" and 3 or 1
    for _, details in ipairs(state.pawns) do
        local peer = details.pawn
        if not same(peer, pawn) and live_pawn(peer, state.world, remote_role) and not peer:IsLocallyControlled() then
            local id = player_id(details)
            if id then
                if peers[id] == nil then peers[id] = peer else peers[id] = false end
            end
        end
    end
    current = {name = state.local_pawn.name, pawn = pawn, capsule = capsule, map = state.map, world = state.world,
        local_role = local_role, remote_role = remote_role, peers = peers, attenuation = attenuation, concurrency = concurrency}
    if not hooked and valid(StaticFindObject(spawn_path)) then
        RegisterHook(spawn_path, function(...) pcall(capture, ...) end)
        hooked = true
    end
end

local function play(scope, api, origin, peer, cue, volume, pitch)
    if not live_pawn(peer, scope.world, scope.remote_role) or peer:IsLocallyControlled() then return end
    local capsule = peer.CapsuleComponent
    if not valid(capsule) or not same(capsule:GetOwner(), peer) then return end
    local point, half = capsule:K2_GetComponentLocation(), capsule:GetScaledCapsuleHalfHeight()
    if type(half) ~= "number" or not (half > 0 and half <= 300) then return end
    local foot = {X = point.X, Y = point.Y, Z = point.Z - half}
    -- Cooked player attenuation is a 500 cm sphere with 2500 cm falloff.
    if (foot.X - origin.X)^2 + (foot.Y - origin.Y)^2 + (foot.Z - origin.Z)^2 > 3000^2 then return end
    local asset = StaticFindObject(cue)
    if not valid(asset) then
        LoadAsset(cue)
        asset = StaticFindObject(cue)
    end
    if not valid(asset) or asset:GetFullName() ~= "SoundCue " .. cue then return end
    api:PlaySoundAtLocation(scope.pawn, asset, foot, {Pitch = 0, Yaw = 0, Roll = 0},
        volume / 1000, pitch / 1000, 0, scope.attenuation, scope.concurrency, peer, nil)
end

function footsteps.update(get_world)
    if not current then return end
    local scope = current
    local frame = SilverfishTakeRemoteFootsteps()
    if type(frame) ~= "string" or #frame > 2048 or not same(scope.world, get_world())
        or not live_pawn(scope.pawn, scope.world, scope.local_role) or not scope.pawn:IsLocallyControlled() then return end
    local area, listener, body = frame:match("^([^\n]+)\n([^\n]+)\n(.+)\n$")
    if area ~= scope.map or listener ~= scope.name or listener ~= scope.pawn:GetFullName() then return end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(api) then return end
    local origin = scope.pawn:K2_GetActorLocation()
    local count = 0
    for line in body:gmatch("[^\n]+") do
        count = count + 1
        if count > 16 then break end
        local id, cue, volume, pitch = line:match("^(%d+)|([%a_]+)|(%d+)|(%d+)$")
        local peer, gain, note = id and scope.peers[tonumber(id)], tonumber(volume), tonumber(pitch)
        if peer and cues[cue] and gain > 0 and gain <= 4000 and note > 0 and note <= 1000 then
            play(scope, api, origin, peer, cues[cue], gain, note)
        end
    end
end

return footsteps
