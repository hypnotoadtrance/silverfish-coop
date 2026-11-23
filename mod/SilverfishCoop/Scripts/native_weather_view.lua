local view = {}

local rain_class = "BlueprintGeneratedClass /Game/Classes/Actors/Rain_BP.Rain_BP_C"
local particle_path = "/Game/Assets/PFX/NI_Rain_01.NI_Rain_01"
local rain_sound = "/Game/Assets/Sounds/MetaSounds/MS_Rain_SFX.MS_Rain_SFX"
local niagara_path = "/Script/Niagara.Default__NiagaraFunctionLibrary"
local niagara_class = "Class /Script/Niagara.NiagaraComponent"
local audio_class = "Class /Script/Engine.AudioComponent"
local system_library = "/Script/Engine.Default__KismetSystemLibrary"
local headless_fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local nullrhi
local siren_class = "BlueprintGeneratedClass /Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C"
local siren_function = "/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren"
local siren_cues = {}
for index = 1, 4 do
    local name = ("StormSiren_%02d_Cue"):format(index)
    siren_cues[index] = "/Game/Assets/Sounds/CUEs/Alarm_Cues/" .. name .. "." .. name
end

local zero = {X = 0, Y = 0, Z = 0}
local rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local current, installed, retry_at, serial, sirens = nil, false, 0, 0, {}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and object:GetClass():GetFullName() == class
end

local function bounded(value, low, high)
    return type(value) == "number" and value >= low and value <= high
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

function view.has_rain(report)
    return report.rain_intensity ~= nil or report.rain_x ~= nil or report.rain_y ~= nil or report.rain_z ~= nil
end

function view.valid_rain(report)
    return bounded(report.rain_intensity, 0, 20) and bounded(report.rain_x, -1.01, 1.01)
        and bounded(report.rain_y, -1.01, 1.01) and bounded(report.rain_z, 0.99, 1.01)
end

function view.skip_wet_rain(frame)
    if not headless_fixture or not frame.rain_intensity or frame.rain_intensity <= 0 then return false end
    if nullrhi == nil then
        -- Some muted fixtures use a real renderer for particle readback.
        local ok, result = pcall(function()
            local system = StaticFindObject(system_library)
            if not valid(system) then return nil end
            local command_line = system:GetCommandLine()
            if type(command_line) == "userdata" then command_line = command_line:ToString() end
            if type(command_line) ~= "string" then return nil end
            return system:ParseParam(command_line, "nullrhi")
        end)
        if ok and type(result) == "boolean" then nullrhi = result end
    end
    return nullrhi == true
end

local function stock_rain_actor(current_world)
    local rain = current_world.day.RainController
    if exact(rain, rain_class) and same(rain:GetWorld(), current_world.world)
        and not rain:IsActorBeingDestroyed() then return rain end
end

function view.read_rain(current_world)
    local rain = stock_rain_actor(current_world)
    if not rain or rain.Intensity == 0 then return {rain_intensity = 0, rain_x = 0, rain_y = 0, rain_z = 1} end
    local particles, direction, amount = rain.NI_Rain_01, rain.RainVector, rain.Intensity
    if not exact(particles, niagara_class) or not same(particles:GetOwner(), rain)
        or not same(particles.Asset, StaticFindObject(particle_path)) or not bounded(amount, 0, 20) then return nil end
    local report = {rain_intensity = amount, rain_x = direction.X, rain_y = direction.Y, rain_z = direction.Z}
    return view.valid_rain(report) and report or nil
end

function view.copy_rain(dst, src)
    dst.rain_intensity, dst.rain_x, dst.rain_y, dst.rain_z = src.rain_intensity, src.rain_x, src.rain_y, src.rain_z
end

local function local_rain_actor(current_world)
    local rain = stock_rain_actor(current_world)
    if rain and rain:HasAuthority() then return rain end
end

function view.restore_local_rain(current_world, original)
    if not original or not same(local_rain_actor(current_world), original.actor) then return end
    local rain, particles = original.actor, original.particles
    if same(particles, rain.NI_Rain_01) then particles:SetVisibility(original.visible, false) end
    for _, source in ipairs(original.audio) do
        if same(source.component, rain[source.field]) then source.component:SetVolumeMultiplier(source.volume) end
    end
    rain:SetActorTickEnabled(original.tick)
end

function view.bind_local_rain(current_world, original)
    local rain = local_rain_actor(current_world)
    if not rain then return nil end
    local particles = rain.NI_Rain_01
    if not exact(particles, niagara_class) or not same(particles:GetOwner(), rain)
        or not same(particles.Asset, StaticFindObject(particle_path)) then return nil end
    if original and same(original.actor, rain) and same(original.particles, particles) then return original end
    original = {actor = rain, particles = particles, visible = particles:IsVisible(),
        tick = rain:IsActorTickEnabled(), audio = {}}
    for _, field in ipairs({"MS_Rain_SFX", "Rain_Storm_Loop_3"}) do
        local audio = rain[field]
        if exact(audio, audio_class) and same(audio:GetOwner(), rain) then
            original.audio[#original.audio + 1] = {field = field, component = audio, volume = audio.VolumeMultiplier}
        end
    end
    return original
end

function view.suppress_local_rain(original)
    if not original then return end
    local rain, particles = original.actor, original.particles
    -- The locally spawned stock actor can outlive startup on a guest. The
    -- authoritative rain emitter handles visuals and sound for this visit.
    if rain:IsActorTickEnabled() then rain:SetActorTickEnabled(false) end
    if particles:IsVisible() then particles:SetVisibility(false, false) end
    for _, source in ipairs(original.audio) do
        if valid(source.component) and source.component.VolumeMultiplier ~= 0 then source.component:SetVolumeMultiplier(0) end
    end
end

function view.destroy_rain(lease)
    if not lease then return end
    if valid(lease.audio) then
        pcall(lease.audio.Stop, lease.audio)
    end
    if valid(lease.particles) then
        local owner = lease.particles:GetOwner()
        lease.particles:Deactivate()
        lease.particles:K2_DestroyComponent(valid(owner) and owner or nil)
    end
end

function view.apply_rain(current_world, frame, lease)
    if frame.rain_intensity == 0 then
        view.destroy_rain(lease)
        return nil
    end
    local pawn, root = current_world.pawn, current_world.pawn.RootComponent
    if not valid(root) or not same(root:GetOwner(), pawn) then error("guest_rain_attachment_unavailable", 0) end
    if lease and (not same(lease.root, root) or not valid(lease.particles)) then
        view.destroy_rain(lease)
        lease = nil
    end
    if not lease then
        local niagara, particle = StaticFindObject(niagara_path), asset(particle_path)
        if not valid(niagara) or not valid(particle) then error("guest_rain_asset_unavailable", 0) end
        local particles = niagara:SpawnSystemAttached(particle, root, FName("None"), zero, rotation, 0,
            false, false, 0, false)
        if not exact(particles, niagara_class) or not same(particles:GetAttachParent(), root)
            or not same(particles.Asset, particle) then
            view.destroy_rain({particles = particles})
            error("guest_rain_particle_unavailable", 0)
        end
        lease = {root = root, particles = particles}
    end
    local changed_intensity = lease.rain_intensity ~= frame.rain_intensity
    local changed_direction = lease.rain_x ~= frame.rain_x or lease.rain_y ~= frame.rain_y or lease.rain_z ~= frame.rain_z
    local ok, issue = pcall(function()
        if changed_intensity then
            lease.particles:SetVariableFloat(FName("User.Intensity"), frame.rain_intensity)
        end
        if changed_direction then
            local direction = {X = frame.rain_x, Y = frame.rain_y, Z = frame.rain_z}
            lease.particles:SetVariableVec3(FName("User.RainDirection"), direction)
        end
        if not lease.particles:IsActive() then lease.particles:Activate(true) end
        if not lease.particles:IsActive() then error("guest_rain_particle_inactive", 0) end
    end)
    if not ok then
        view.destroy_rain(lease)
        error(issue, 0)
    end
    local audio_started = false
    if not valid(lease.audio) then
        local now = current_world.statics:GetRealTimeSeconds(pawn)
        if now >= (lease.next_audio_attempt or 0) then
            lease.next_audio_attempt = now + 1
            pcall(function()
                local sound = asset(rain_sound)
                if not valid(sound) then return end
                lease.audio = current_world.statics:SpawnSoundAttached(sound, root, FName("None"), zero, rotation,
                    0, true, 1, 1, 0, nil, nil, true)
                audio_started = valid(lease.audio)
            end)
        end
    end
    if (changed_intensity or audio_started) and exact(lease.audio, audio_class) then
        pcall(function() lease.audio:SetFloatParameter(FName("StormIntensity"), frame.rain_intensity) end)
    end
    view.copy_rain(lease, frame)
    return lease
end

function view.rain_verified(lease, frame)
    if frame.rain_intensity == 0 then return not lease end
    return lease ~= nil and exact(lease.particles, niagara_class)
        and same(lease.particles:GetAttachParent(), lease.root) and lease.particles:IsActive() == true
        and lease.rain_intensity == frame.rain_intensity and lease.rain_x == frame.rain_x
        and lease.rain_y == frame.rain_y and lease.rain_z == frame.rain_z
end

local function siren_actor(actor, world)
    if not exact(actor, siren_class) or actor.bReplicates ~= false or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), world) then return nil end
    if not exact(actor.Audio, audio_class) or not same(actor.Audio:GetOwner(), actor) then return nil end
    return actor:GetFullName():match("^AlarmSiren_BP_C (/Game/[A-Za-z0-9_/.:]+)$")
end

local function capture(actor, sound, duration)
    if not current or not valid(actor) or not valid(sound) or not bounded(duration, 0.25, 120) or not actor:HasAuthority() then return end
    local source = siren_actor(actor, current.world)
    if not source then return end
    for cue, path in ipairs(siren_cues) do
        if sound:GetFullName() == "SoundCue " .. path then
            serial = serial + 1
            for index, previous in ipairs(sirens) do
                if previous.source == source then
                    table.remove(sirens, index)
                    break
                end
            end
            sirens[#sirens + 1] = {id = serial, source = source, cue = cue, until_time = os.time() + duration}
            if #sirens > 4 then table.remove(sirens, 1) end
            return
        end
    end
end

function view.sync(state, phase)
    local world = phase == "hosting" and valid(state.world) and state.world or nil
    if world == nil and current == nil then return end
    if not same(world, current and current.world) then
        current, sirens, retry_at = world and {world = world} or nil, {}, 0
    end
    local now = os.time()
    if not current or installed or now < retry_at then return end
    retry_at = now + 1
    if not valid(StaticFindObject(siren_function)) then return end
    RegisterHook(siren_function, function(context, sound, duration)
        pcall(function() capture(context:get(), sound:get(), duration:get()) end)
    end)
    installed = true
end

function view.siren_rows(state, phase, row)
    if phase ~= "hosting" or not current or not same(current.world, state.world) then return {} end
    local result, now = {}, os.time()
    for _, siren in ipairs(sirens) do
        if siren.until_time > now then
            result[#result + 1] = row("native-weather-siren", siren.id, siren.source, siren.cue, siren.until_time)
        end
    end
    return result
end

function view.play_siren(current_world, source, cue, remaining)
    if not siren_cues[cue] or not bounded(remaining, 0.2, 120) or type(source) ~= "string"
        or #source > 384 or not source:match("^/Game/[A-Za-z0-9_/.:]+$") then return false end
    local actor = StaticFindObject(source)
    if siren_actor(actor, current_world.world) ~= source then return false end
    local sound = asset(siren_cues[cue])
    if not valid(sound) then return false end
    actor:StormSiren(sound, remaining)
    return same(actor.Audio.Sound, sound)
end

return view
