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
}
local audio_range = {charge = 4000, blast = 500100, vortex = 500100, electrode = 8100, canister = 500100}
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
    captured[kind] = captured[kind] + 1
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

local function stock_blast(actor, cue)
    local pfx, sfx = actor.PFX, actor.SFX
    local scale, volume = actor["PFX Scale"], actor["SFX Vol"]
    if not valid(pfx) or not valid(sfx)
        or type(scale) ~= "number" or not (scale > 0 and scale <= 10)
        or type(volume) ~= "number" or not (volume >= 0 and volume <= 10) then return end
    if pfx:GetFullName() == "NiagaraSystem " .. assets[cue][1]
        and sfx:GetFullName():match("^%S+ (.+)$") == assets[cue][2] then
        record(cue, actor:K2_GetActorLocation(), scale, volume)
        return true
    end
end

local function observe_canister(actor, registered_source)
    local sound = actor.SFX
    if not valid(sound) or sound:GetFullName():match("^%S+ (.+)$") ~= assets.canister[2] then return end
    local attenuation = actor["Attenuation Settings"]
    if valid(attenuation) and attenuation:GetFullName() == "SoundAttenuation " .. assets.canister[3]
        and registered_source(actor:K2_GetActorLocation()) then
        return stock_blast(actor, "canister")
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
        if cue and entry.actor:GetAddress() == source:GetAddress() and stock_blast(actor, cue) then
            if cue == "blast" then entry.charged = true end
            return
        end
    end
end

function effects.rows(row)
    local now = os.clock()
    while pending[1] and pending[1].expires <= now do table.remove(pending, 1) end
    local rows = {row("native-anomaly-capture", captured.charge, captured.blast, captured.vortex, captured.electrode, captured.canister)}
    for _, entry in ipairs(pending) do
        rows[#rows + 1] = row("native-anomaly-cue", table.unpack(entry))
    end
    return rows
end

local function play(pawn, origin, kind, point, scale, volume)
    local profile = assets[kind]
    local particle, sound, attenuation = asset(profile[1]), asset(profile[2]), asset(profile[3])
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(niagara) or not valid(statics) or not valid(particle) or not valid(sound)
        or kind ~= "charge" and not valid(attenuation) then return false end
    if kind == "charge" or kind == "blast" then effects.retire_charges(point) end
    local rotation = {Pitch = 0, Yaw = 0, Roll = 0}
    local component = niagara:SpawnSystemAtLocation(pawn, particle, point, rotation,
        {X = scale, Y = scale, Z = scale}, true, true, 0, true)
    if kind == "charge" then
        if valid(component) then
            if #charging >= 12 then remove_charge(1) end
            charging[#charging + 1] = {component = component, point = point, expires = os.clock() + 3}
        end
        statics:PlaySoundAtLocation(pawn, sound, point, rotation, 1, 1, 0, nil, nil, nil, nil)
    else
        local audio = statics:SpawnSoundAtLocation(pawn, sound, point, rotation, volume, 1, 0, attenuation, nil, true)
        if valid(audio) then
            audio:SetFloatParameter(FName("Distance"), math.sqrt(distance_squared(origin, point)))
        end
    end
    return true, valid(component) == true
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
        if id and #id <= 15 and not seen[id] and assets[kind] and x and y and z and scale and scale > 0 and scale <= 10
            and volume and volume >= 0 and volume <= 10
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
