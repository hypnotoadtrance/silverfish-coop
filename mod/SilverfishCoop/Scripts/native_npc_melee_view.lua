local melee = {}

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local strike_path = npc_path .. ":Slot_Anim"
local marker_path = "/Script/Engine.AmbientSound"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local byte_angle = 360 / 256
local maximum, marker_lifetime, cue_lifetime = 128, 15, .6
local discovery_interval = .15
local current, hooked, notifications, npc_class, point_class, statics

local function valid(value)
    return value and value:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function byte(angle)
    return math.floor(angle / byte_angle + .5) % 256
end

local function stock_clip(path)
    if type(path) ~= "string" or #path > 192 then return false end
    local package, clip = path:match("^(/Game/[%w_/%-]+)%.([%w_%-]+)$")
    return package and package:match("/([%w_%-]+)$") == clip
end

local function actor_ready(actor, authority)
    return valid(actor) and valid(npc_class) and actor:IsA(npc_class) and actor.Alive == true
        and same(actor:GetWorld(), current.world) and not actor:IsActorBeingDestroyed()
        and actor.bReplicates == true and actor:GetLocalRole() == authority
end

local function host_marker(actor, now)
    local address = actor:GetAddress()
    local entry = current.markers[address]
    if entry and valid(entry.marker) and same(entry.marker:GetOwner(), actor) then
        entry.expires = now + marker_lifetime
        return entry.id
    end
    if not valid(point_class) or current.count >= maximum or current.serial >= 65535 then return end
    local id = current.serial + 1
    -- A native actor with no sound asset carries only the owner and ID. Keep
    -- its class separate from the TargetPoint actors used for deaths.
    local marker = current.world:SpawnActor(point_class, actor:K2_GetActorLocation(), {
        Pitch = 0, Yaw = (id % 256) * byte_angle, Roll = (id // 256) * byte_angle,
    })
    if not valid(marker) then return end
    marker:SetOwner(actor)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    current.serial, current.count = id, current.count + (entry and 0 or 1)
    current.markers[address] = {marker = marker, id = id, expires = now + marker_lifetime}
    return id
end

local function capture(context, animation, rate, blend_in, blend_out, duration)
    if not current or not current.active then return end
    local actor, clip = context:get(), animation:get()
    if not actor_ready(actor, 3) or not valid(clip) then return end
    local path = clip:GetFullName():match("^AnimSequence (%S+)$")
    local speed, fade_in, fade_out = rate:get(), blend_in:get(), blend_out:get()
    if not stock_clip(path) or duration:get() <= 0 or not (speed >= .1 and speed <= 8
        and fade_in >= 0 and fade_in <= 1 and fade_out >= 0 and fade_out <= 1) then return end
    local swings = actor.Anim_Melee
    for i = 1, swings:GetArrayNum() do
        if same(clip, swings[i]) then
            local now = statics:GetRealTimeSeconds(current.world)
            local id = host_marker(actor, now)
            if id then current.capture(now, id, path, speed, fade_in, fade_out) end
            return
        end
    end
end

local function remember_marker(marker)
    if not valid(marker) or marker:HasAnyFlags(EObjectFlags.RF_WasLoaded) then return true end
    local world = marker:GetWorld()
    if not valid(world) then return false end
    if not same(world, current.world) or not same(marker:GetClass(), point_class) then return true end
    local audio = marker.AudioComponent
    if valid(audio) and valid(audio.Sound) then return true end
    if marker:GetLocalRole() ~= 1 or not valid(audio) or not actor_ready(marker:GetOwner(), 1) then return false end
    local rotation = marker:K2_GetActorRotation()
    local id = byte(rotation.Roll) * 256 + byte(rotation.Yaw)
    if id == 0 then return false end
    current.markers[id] = marker
    return true
end

local function created(marker)
    local scope = current
    if not scope or scope.capture or not valid(marker) or scope.candidate_count >= maximum then return end
    local address = marker:GetAddress()
    if not scope.candidates[address] then
        scope.candidates[address] = marker
        scope.candidate_count = scope.candidate_count + 1
    end
end

local function discover_markers(now)
    for address, marker in pairs(current.candidates) do
        if remember_marker(marker) then
            current.candidates[address] = nil
            current.candidate_count = current.candidate_count - 1
        end
    end
    for id in pairs(current.pending) do
        if not valid(current.markers[id]) then
            if now >= current.next_discovery then
                -- Owner and rotation can arrive after construction, or a
                -- marker can already exist when a guest enters this world.
                current.next_discovery = now + (notifications and discovery_interval or .05)
                for _, marker in ipairs(FindAllOf("AmbientSound") or {}) do remember_marker(marker) end
            end
            return
        end
    end
end

local function play(marker, cue, now)
    local actor = valid(marker) and marker:GetOwner()
    if not actor_ready(actor, 1) then return end
    local mesh = actor.Mesh
    local body = valid(mesh) and mesh.SkinnedAsset
    local animator = valid(body) and mesh:GetAnimInstance()
    if not valid(animator) or not same(mesh:GetOwner(), actor) then return end
    local clip = StaticFindObject(cue.path)
    if not valid(clip) then
        LoadAsset(cue.path)
        clip = StaticFindObject(cue.path)
    end
    if not valid(clip) or clip:GetFullName() ~= "AnimSequence " .. cue.path
        or not same(clip.Skeleton, body.Skeleton) then return false end
    local age = (now - cue.received) * cue.rate
    if age >= clip:GetPlayLength() then return false end
    -- The stock slot plays the same clip without re-running NPC combat or AI.
    local montage = animator:PlaySlotAnimationAsDynamicMontage(clip, FName("DefaultSlot"),
        cue.blend_in, cue.blend_out, cue.rate, 1, -1, math.max(0, age))
    return valid(montage) == true
end

function melee.offer(world, id, path, rate, blend_in, blend_out)
    if not current or current.capture or not same(current.world, world)
        or not id or id < 1 or id > 65535 or not stock_clip(path)
        or not rate or rate < .1 or rate > 8 or not blend_in or blend_in < 0 or blend_in > 1
        or not blend_out or blend_out < 0 or blend_out > 1 then return false end
    local now = statics:GetRealTimeSeconds(world)
    local cue = {path = path, rate = rate, blend_in = blend_in, blend_out = blend_out, received = now}
    local played = play(current.markers[id], cue, now)
    if played ~= nil then return played end
    -- The cue and its Unreal owner reference use separate channels. Keep the
    -- newest swing for this actor until the reference arrives.
    if current.pending[id] or current.count < maximum then
        if not current.pending[id] then current.count = current.count + 1 end
        current.pending[id] = cue
        return true
    end
end

function melee.sync(state, phase, on_capture)
    local hosting = phase == "hosting" and state.listening and not state.server
    local watching = phase == "connected" and state.server
    local world = (hosting or watching) and state.world
    if not valid(world) then current = nil; return end
    if not same(current and current.world, world) or current.capture ~= (hosting and on_capture or nil) then
        current = {world = world, capture = hosting and on_capture or nil,
            markers = {}, pending = {}, candidates = {}, serial = 0, next_scan = 0, next_discovery = 0,
            count = 0, candidate_count = 0}
    end
    current.active = hosting and state.clients > 0 and type(SilverfishPublishLocalWeaponCue) == "function"
    if not valid(npc_class) then npc_class = StaticFindObject(npc_path) end
    if not valid(point_class) then point_class = StaticFindObject(marker_path) end
    if not valid(statics) then statics = StaticFindObject(statics_path) end
    if not valid(npc_class) or not valid(statics) then return end
    if watching and notifications == nil and valid(point_class) then
        notifications = type(NotifyOnNewObject) == "function" and pcall(NotifyOnNewObject, marker_path, created)
    end
    if hosting and not hooked and valid(StaticFindObject(strike_path)) then
        RegisterHook(strike_path, function(context, animation, rate, blend_in, blend_out, duration)
            pcall(capture, context, animation, rate, blend_in, blend_out, duration)
        end)
        hooked = true
    end
end

function melee.update(get_world)
    if not current or current.count == 0 or not valid(statics) then return end
    local world = get_world()
    if not same(current.world, world) then return end
    local hosting = current.capture ~= nil
    local now = statics:GetRealTimeSeconds(world)
    if now < current.next_scan then return end
    current.next_scan = now + (hosting and .5 or .05)
    if hosting then
        for address, entry in pairs(current.markers) do
            local marker = entry.marker
            if now >= entry.expires or not valid(marker) or not actor_ready(marker:GetOwner(), 3) then
                if valid(marker) then marker:K2_DestroyActor() end
                current.markers[address] = nil
                current.count = current.count - 1
            end
        end
    else
        for id, cue in pairs(current.pending) do
            if now - cue.received >= cue_lifetime then
                current.pending[id] = nil
                current.count = current.count - 1
            end
        end
        discover_markers(now)
        for id, cue in pairs(current.pending) do
            if play(current.markers[id], cue, now) ~= nil then
                current.pending[id] = nil
                current.count = current.count - 1
            end
        end
        for id, marker in pairs(current.markers) do
            if not valid(marker) then current.markers[id] = nil end
        end
    end
end

return melee
