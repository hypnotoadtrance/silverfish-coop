local effects = {}

local assets = {
    charge = {"/Game/Assets/PFX/NI_Starburst_Charge.NI_Starburst_Charge",
        "/Game/Assets/Sounds/CUEs/Anomally_Cue/Starburst_Charge_Cue.Starburst_Charge_Cue"},
    blast = {"/Game/Assets/PFX/FX_Blast_FragGrenade_Converted.FX_Blast_FragGrenade_Converted",
        "/Game/Assets/Sounds/MetaSounds/MS_Grenade01.MS_Grenade01",
        "/Game/Assets/Sounds/SoundClasses/Blast_Attenuation.Blast_Attenuation"},
}
local audio_range = {charge = 4000, blast = 500100}
effects.blast_class = "BlueprintGeneratedClass /Game/Classes/Actors/ExplosionActor_BP.ExplosionActor_BP_C"
local history, pending, serial, seen, visit = {}, {}, 0, {}, nil
local captured = {charge = 0, blast = 0}
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

local function record(kind, point, scale)
    serial = serial + 1
    captured[kind] = captured[kind] + 1
    local now = os.clock()
    pending[#pending + 1] = {serial, kind, point.X, point.Y, point.Z, scale, expires = now + 1.25}
    if #pending > 12 then table.remove(pending, 1) end
end

function effects.clear()
    for index = #charging, 1, -1 do remove_charge(index) end
    history, pending, seen, visit = {}, {}, {}, nil
    captured = {charge = 0, blast = 0}
end

function effects.observe_charge(entry)
    local trap = entry.actor
    local charged = trap.Rads == 2000
    if charged and not entry.charged and valid(trap.Sphere) then
        record("charge", trap.Sphere:K2_GetComponentLocation(), 1)
    end
    entry.charged = charged
end

function effects.observe_blast(actor, active)
    if not actor:HasAuthority() then return end
    local ignored, pfx, sfx = actor["Actors to Ignore"], actor.PFX, actor.SFX
    local count = ignored and ignored:GetArrayNum() or 0
    local scale = actor["PFX Scale"]
    if count < 1 or count > 8 or not valid(pfx) or not valid(sfx)
        or pfx:GetFullName() ~= "NiagaraSystem " .. assets.blast[1]
        or sfx:GetFullName():match("^%S+ (.+)$") ~= assets.blast[2]
        or type(scale) ~= "number" or not (scale > 0 and scale <= 10) then return end
    for index = 1, count do
        local source = ignored[index]
        local entry = valid(source) and active[source:GetAddress()]
        if entry and entry.kind == "starburst" and entry.actor:GetAddress() == source:GetAddress() then
            entry.charged = true
            record("blast", actor:K2_GetActorLocation(), scale)
            return
        end
    end
end

function effects.rows(row)
    local now = os.clock()
    while pending[1] and pending[1].expires <= now do table.remove(pending, 1) end
    local rows = {row("native-starburst-capture", captured.charge, captured.blast)}
    for _, entry in ipairs(pending) do
        rows[#rows + 1] = row("native-starburst-cue", table.unpack(entry))
    end
    return rows
end

local function play(pawn, origin, kind, point, scale)
    local profile = assets[kind]
    local particle, sound, attenuation = asset(profile[1]), asset(profile[2]), asset(profile[3])
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(niagara) or not valid(statics) or not valid(particle) or not valid(sound)
        or kind == "blast" and not valid(attenuation) then return false end
    effects.retire_charges(point)
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
        local audio = statics:SpawnSoundAtLocation(pawn, sound, point, rotation, 1, 1, 0, attenuation, nil, true)
        if valid(audio) then
            audio:SetFloatParameter(FName("Distance"), math.sqrt(distance_squared(origin, point)))
        end
    end
    return true, valid(component) == true
end

function effects.command(args, state, phase)
    if args[2] ~= "native-starburst-play-cues" then return end
    local pawn, epoch = state.local_pawn and state.local_pawn.pawn, args[4]
    if phase ~= "connected" or state.map ~= args[3] or not valid(pawn) or not pawn:IsLocallyControlled()
        or not epoch or #epoch ~= 32 or not epoch:match("^[0-9a-f]+$") or #args < 5 or #args > 16 then
        error("Starburst cue is outside the current guest visit", 0)
    end
    if visit ~= epoch then
        effects.clear()
        visit = epoch
    end
    local origin, played = pawn:K2_GetActorLocation(), {}
    for index = 5, #args do
        local id, kind, x, y, z, scale = args[index]:match("^(%d+)|(%l+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        x, y, z, scale = tonumber(x), tonumber(y), tonumber(z), tonumber(scale)
        if id and #id <= 15 and not seen[id] and assets[kind] and x and y and z and scale and scale > 0 and scale <= 10
            and (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 <= audio_range[kind]^2 then
            local accepted, particles = play(pawn, origin, kind, {X = x, Y = y, Z = z}, scale)
            if accepted then
                played[#played + 1] = id .. ":" .. (particles and "1" or "0")
                history[#history + 1], seen[id] = id, true
                if #history > 48 then seen[table.remove(history, 1)] = nil end
            end
        end
    end
    return table.concat(played, ",")
end

return effects
