local effects = {}

local assets = {
    charge = {"/Game/Assets/PFX/NI_Starburst_Charge.NI_Starburst_Charge",
        "/Game/Assets/Sounds/CUEs/Anomally_Cue/Starburst_Charge_Cue.Starburst_Charge_Cue"},
    blast = {"/Game/Assets/PFX/FX_Blast_FragGrenade_Converted.FX_Blast_FragGrenade_Converted",
        "/Game/Assets/Sounds/MetaSounds/MS_Grenade01.MS_Grenade01",
        "/Game/Assets/Sounds/SoundClasses/Blast_Attenuation.Blast_Attenuation"},
    vortex = {"/Game/Assets/PFX/NI_Singularity_01.NI_Singularity_01",
        "/Game/Assets/Sounds/CUEs/Blast_Cues/ObserverAbsorb_Cue.ObserverAbsorb_Cue",
        "/Game/Assets/Sounds/SoundClasses/Blast_Attenuation.Blast_Attenuation"},
    electrode = {"/Game/Assets/PFX/FX_Blast_PulseGrenade_Converted.FX_Blast_PulseGrenade_Converted",
        "/Game/Assets/Sounds/MetaSounds/MS_ElectricBlast.MS_ElectricBlast",
        "/Game/Assets/Sounds/SoundClasses/Weapon_ShortRange_Attenuation.Weapon_ShortRange_Attenuation"},
    canister = {"/Game/Assets/PFX/FX_Blast_FragGrenade_Converted.FX_Blast_FragGrenade_Converted",
        "/Game/Assets/Sounds/MetaSounds/MS_Blast_Pop.MS_Blast_Pop",
        "/Game/Assets/Sounds/SoundClasses/Blast_Attenuation.Blast_Attenuation"},
    wandering = {"/Game/Assets/PFX/NI_Singularity_01.NI_Singularity_01",
        "/Game/Assets/Sounds/CUEs/Blast_Cues/Teleport_Cue.Teleport_Cue"},
    bottle = {"/Game/Assets/PFX/NI_BrokenBottle_WtLiquid.NI_BrokenBottle_WtLiquid",
        "/Game/Assets/Sounds/CUEs/BulletHit_Cues/Impact_Glass_Liquid_Cue.Impact_Glass_Liquid_Cue",
        "/Game/Assets/Sounds/SoundClasses/Default_Attenuation.Default_Attenuation"},
}
assets.pipebomb = assets.blast
assets.fueldrum = assets.blast
assets.fixedtank = assets.blast
assets.powerbox = {assets.electrode[1], assets.electrode[2], assets.blast[3]}
assets.fixedfire = {assets.blast[1], "/Game/Assets/Sounds/MetaSounds/MS_Blast_Firey.MS_Blast_Firey", assets.blast[3]}
local audio_range = {charge = 4000, blast = 500100, vortex = 500100, electrode = 8100, canister = 500100, wandering = 500100,
    powerbox = 500100, fixedtank = 500100, fixedfire = 500100, fueldrum = 500100, bottle = 8000}
local canister_sounds = {[assets.canister[2]] = "canister", [assets.fueldrum[2]] = "fueldrum"}
local observer_blasts = {
    [0] = {kind = "observersparks", particle = "NI_Sparks"},
    {kind = "observerred", particle = "FX_Blast_Pulse_Red"},
    {kind = "observergreen", particle = "FX_Blast_Pulse_Green"},
    {kind = "observerblue", particle = "FX_Blast_PulseGrenade_Converted"},
    {kind = "observerpurple", particle = "FX_Blast_Pulse_Purple"},
    {kind = "observergold", particle = "FX_Blast_Pulse_Gold"},
}
for _, spec in pairs(observer_blasts) do
    assets[spec.kind] = {"/Game/Assets/PFX/" .. spec.particle .. "." .. spec.particle,
        assets.vortex[2], assets.blast[3]}
    audio_range[spec.kind] = audio_range.blast
end
local pipe_bomb = "BlueprintGeneratedClass /Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/PipeBomb_Projectile_BP.PipeBomb_Projectile_BP_C"
audio_range.pipebomb = audio_range.blast
local blasts = {starburst = "blast", vortex = "vortex", electrode = "electrode"}
effects.blast_class = "BlueprintGeneratedClass /Game/Classes/Actors/ExplosionActor_BP.ExplosionActor_BP_C"
local history, pending, serial, seen, visit = {}, {}, 0, {}, nil
local captured = {charge = 0, blast = 0, vortex = 0, electrode = 0, canister = 0}
local charging = {}

local function valid(object) return object and object:IsValid() end
local function asset(path)
    if not path then return end
    local value = StaticFindObject(path)
    if not valid(value) then LoadAsset(path); value = StaticFindObject(path) end
    return value
end

local function distance_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function remove_charge(index)
    local component = table.remove(charging, index).component
    if valid(component) then
        local owner = component:GetOwner()
        component:K2_DestroyComponent(valid(owner) and owner or nil)
    end
end

function effects.retire_charges(point)
    local now = os.clock()
    for index = #charging, 1, -1 do
        local active = charging[index]
        if not valid(active.component) or active.expires <= now or (point and distance_squared(active.point, point) < 9) then
            remove_charge(index)
        end
    end
end

local function record(kind, point, scale, volume)
    serial = serial + 1
    captured[kind] = (captured[kind] or 0) + 1
    local now = os.clock()
    pending[#pending + 1] = {serial, kind, point.X, point.Y, point.Z, scale, volume or 1, expires = now + 1.25}
    if #pending > 12 then table.remove(pending, 1) end
end

function effects.clear()
    for index = #charging, 1, -1 do remove_charge(index) end
    history, pending, seen, visit = {}, {}, {}, nil
    captured = {charge = 0, blast = 0, vortex = 0, electrode = 0, canister = 0}
end

function effects.observe_charge(entry)
    local trap = entry.actor
    local charged = trap.Rads == 2000
    if charged and not entry.charged and valid(trap.Sphere) then
        record("charge", trap.Sphere:K2_GetComponentLocation(), 1)
    end
    entry.charged = charged
end

function effects.observe_bottle_breaks(world)
    if type(SilverfishTakeNativeBottleBreakCue) ~= "function" then return end
    local address = world:GetAddress()
    for _ = 1, 8 do
        local x, y, z = SilverfishTakeNativeBottleBreakCue(address)
        if not x then break end
        record("bottle", {X = x, Y = y, Z = z}, 1)
    end
end

function effects.observe_wandering_teleport(point)
    if point and type(point.X) == "number" and type(point.Y) == "number" and type(point.Z) == "number"
        and math.abs(point.X) <= 1e7 and math.abs(point.Y) <= 1e7 and math.abs(point.Z) <= 1e7 then
        record("wandering", point, 1, .6)
    end
end

local function stock_blast(cue, point, pfx, sfx, scale, volume)
    if not valid(pfx) or not valid(sfx)
        or type(scale) ~= "number" or not (scale > 0 and scale <= 10)
        or type(volume) ~= "number" or not (volume >= 0 and volume <= 10) then return end
    if pfx:GetFullName() == "NiagaraSystem " .. assets[cue][1]
        and sfx:GetFullName():match("^%S+ (.+)$") == assets[cue][2] then
        record(cue, point, scale, volume)
        return true
    end
end

function effects.observe_fixed_blast(cue, point, pfx, sfx, scale, volume, attenuation)
    if valid(attenuation) and attenuation:GetFullName() == "SoundAttenuation " .. assets[cue][3] then
        stock_blast(cue, point, pfx, sfx, scale, volume)
    end
end

function effects.observe_pipe_bomb_blast(actor)
    if not actor:HasAuthority() then return end
    local sources = actor["Actors to Ignore"]
    local count = sources and sources:GetArrayNum() or 0
    if count < 1 or count > 8 then return end
    for index = 1, count do
        local source = sources[index]
        if valid(source) and source:HasAuthority() and source:GetClass():GetFullName() == pipe_bomb
            and valid(source:GetWorld()) and source:GetWorld():GetAddress() == actor:GetWorld():GetAddress() then
            effects.observe_fixed_blast("pipebomb", actor:K2_GetActorLocation(), actor.PFX, actor.SFX, actor["PFX Scale"],
                actor["SFX Vol"], actor["Attenuation Settings"])
            return source
        end
    end
end

local function observe_canister(actor, registered_source)
    local sound = actor.SFX
    local cue = valid(sound) and canister_sounds[sound:GetFullName():match("^%S+ (.+)$")]
    if not cue then return end
    -- Stock drums set their impulse explicitly. Other blasts also use the default grenade assets.
    if cue == "fueldrum" and actor.ImpulseStrength ~= 800 then return end
    local attenuation = actor["Attenuation Settings"]
    local point = actor:K2_GetActorLocation()
    if valid(attenuation) and attenuation:GetFullName() == "SoundAttenuation " .. assets[cue][3]
        and registered_source(point, cue) then
        return stock_blast(cue, point, actor.PFX, sound, actor["PFX Scale"], actor["SFX Vol"])
    end
end

function effects.observe_blast(actor, active, registered_canister)
    if not actor:HasAuthority() then return end
    if registered_canister and observe_canister(actor, registered_canister) then return end
    local ignored = actor["Actors to Ignore"]
    local count = ignored and ignored:GetArrayNum() or 0
    if count < 1 or count > 8 then return end
    for index = 1, count do
        local source = ignored[index]
        local entry = valid(source) and active[source:GetAddress()]
        local cue = entry and blasts[entry.kind]
        if entry and entry.kind == "observer" then
            local spec = observer_blasts[source.ResultIndex]
            local attenuation = actor["Attenuation Settings"]
            if spec and valid(attenuation) and attenuation:GetFullName() == "SoundAttenuation " .. assets[spec.kind][3] then
                cue = spec.kind
            end
        end
        if cue and entry.actor:GetAddress() == source:GetAddress()
            and stock_blast(cue, actor:K2_GetActorLocation(), actor.PFX, actor.SFX, actor["PFX Scale"], actor["SFX Vol"]) then
            if cue == "blast" then entry.charged = true end
            return
        end
    end
end

function effects.rows(row)
    local now = os.clock()
    while pending[1] and pending[1].expires <= now do table.remove(pending, 1) end
    local rows = {
        row("native-anomaly-capture", captured.charge, captured.blast, captured.vortex, captured.electrode, captured.canister,
            captured.fueldrum or 0),
        row("native-fixed-explosive-capture", (captured.powerbox or 0) + (captured.fixedtank or 0) + (captured.fixedfire or 0)),
    }
    for _, entry in ipairs(pending) do
        rows[#rows + 1] = row("native-anomaly-cue", table.unpack(entry))
    end
    return rows
end

local function play(pawn, origin, kind, point, scale, volume)
    local profile = assets[kind]
    -- The stock bottle loads these assets with its class. Avoid loading a package during a throw.
    local lookup = kind == "bottle" and StaticFindObject or asset
    local particle, sound = lookup(profile[1]), lookup(profile[2])
    local attenuation = profile[3] and lookup(profile[3])
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(niagara) or not valid(statics) or not valid(particle) or not valid(sound)
        or profile[3] and not valid(attenuation) then return false end
    if kind == "charge" or kind == "blast" then effects.retire_charges(point) end
    local rotation = {Pitch = 0, Yaw = 0, Roll = 0}
    local component = niagara:SpawnSystemAtLocation(pawn, particle, point, rotation,
        {X = scale, Y = scale, Z = scale}, true, true, 0, true)
    if kind == "charge" then
        if valid(component) then
            if #charging >= 12 then remove_charge(1) end
            charging[#charging + 1] = {component = component, point = point, expires = os.clock() + 3}
        end
    end
    if not profile[3] or kind == "bottle" then
        statics:PlaySoundAtLocation(pawn, sound, point, rotation, volume, 1, 0, attenuation, nil, nil, nil)
    else
        local audio = statics:SpawnSoundAtLocation(pawn, sound, point, rotation, volume, 1, 0, attenuation, nil, true)
        if valid(audio) then
            audio:SetFloatParameter(FName("Distance"), math.sqrt(distance_squared(origin, point)))
        end
    end
    return true, valid(component) == true
end

function effects.play_turret_blast(pawn, point)
    local explosion = StaticFindObject("/Game/Classes/Actors/ExplosionActor_BP.ExplosionActor_BP_C")
    local defaults = valid(explosion) and explosion:GetCDO()
    if not valid(defaults) then return false end
    local scale, volume = defaults["PFX Scale"], defaults["SFX Vol"]
    if not (type(scale) == "number" and scale > 0 and scale <= 10
        and type(volume) == "number" and volume >= 0 and volume <= 10) then return false end
    -- The stock turret explosion uses the same particles, sound and attenuation as the canister.
    return play(pawn, pawn:K2_GetActorLocation(), "canister", point, scale, volume)
end

function effects.command(args, state, phase, animate_vortex)
    if args[2] ~= "native-anomaly-play-cues" then return end
    local pawn, epoch = state.local_pawn and state.local_pawn.pawn, args[4]
    if phase ~= "connected" or state.map ~= args[3] or not valid(pawn) or not pawn:IsLocallyControlled()
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") or #args < 5 or #args > 16 then
        error("Anomaly cue is outside the current guest visit", 0)
    end
    if visit ~= epoch then
        effects.clear()
        visit = epoch
    end
    local origin, played = pawn:K2_GetActorLocation(), {}
    for index = 5, #args do
        local id, kind, x, y, z, scale, volume = args[index]:match("^(%d+)|(%l+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z, scale, volume = tonumber(x), tonumber(y), tonumber(z), tonumber(scale), tonumber(volume)
        if id and #id <= 15 and seen[id] then
            played[#played + 1] = id .. ":already"
        elseif id and #id <= 15 and assets[kind] and x and y and z and scale and scale > 0 and scale <= 10
            and volume and volume >= 0 and volume <= 10 and (kind ~= "bottle" or scale == 1 and volume == 1)
            and (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 <= audio_range[kind]^2 then
            local point = {X = x, Y = y, Z = z}
            local accepted, particles = play(pawn, origin, kind, point, scale, volume)
            if accepted then
                local animated = false
                if kind == "vortex" then
                    local ok, playing = pcall(animate_vortex, point)
                    animated = ok and playing
                end
                played[#played + 1] = id .. ":" .. (particles and "1" or "0") .. ":" .. (animated and "1" or "0")
                history[#history + 1], seen[id] = id, true
                if #history > 48 then seen[table.remove(history, 1)] = nil end
            end
        end
    end
    return table.concat(played, ",")
end

return effects
