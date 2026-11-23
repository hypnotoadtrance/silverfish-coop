local fire = {}

local class_path = "/Game/Classes/Anomalies/Fire_Camp_BP.Fire_Camp_BP_C"
local base_path = "/Game/Classes/Anomalies/Fire_BP.Fire_BP_C"
local zero = {X = 0, Y = 0, Z = 0}
local identity = {Translation = zero, Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}

local function valid(object)
    return object and object:IsValid()
end

local function templates()
    local class = StaticFindObject(class_path)
    if not valid(class) then LoadAsset(class_path); class = StaticFindObject(class_path) end
    if not valid(class) then return end
    local flames = StaticFindObject(class_path .. ":NI_FireMassive_GEN_VARIABLE")
    local audio = StaticFindObject(class_path .. ":Fireplace_Low_02_Loop_GEN_VARIABLE")
    local light = StaticFindObject(class_path .. ":PointLight_GEN_VARIABLE")
    if valid(flames) and valid(flames.Asset) and valid(audio) and valid(audio.Sound) and valid(light) then
        return flames, audio, light, class:GetCDO()
    end
end

local function add_light(turret, root, template, defaults, view)
    local class = StaticFindObject("/Script/Engine.PointLightComponent")
    if not valid(class) then return end
    local light = turret:AddComponentByClass(class, true, identity, true)
    if not valid(light) then return end
    view.fire_light = light
    light:SetMobility(2)
    light:SetCastShadows(template.CastShadows)
    turret:FinishAddComponent(light, true, identity)
    if not light:K2_AttachToComponent(root, FName("None"), 2, 2, 0, false) then
        light:K2_DestroyComponent(turret)
        view.fire_light = nil
        return
    end
    light:K2_SetRelativeLocationAndRotation(template.RelativeLocation, template.RelativeRotation, false, {}, true)
    light:SetIntensity(defaults.LightIntensity)
    light:SetLightColor(template:GetLightColor(), true)
    light:SetAttenuationRadius(template.AttenuationRadius)
    light:SetSourceRadius(template.SourceRadius)
    if valid(template.LightFunctionMaterial) then light:SetLightFunctionMaterial(template.LightFunctionMaterial) end
end

function fire.attached(turret)
    local output = {}
    turret:GetAttachedActors(output, true, false)
    for _, item in ipairs(output.OutActors or output) do
        local actor = type(item.type) == "function" and item:type() == "RemoteUnrealParam" and item:get() or item
        if valid(actor) and actor:GetClass():GetFullName() == "BlueprintGeneratedClass " .. class_path
            and actor:HasAuthority() and not actor:IsActorBeingDestroyed() then return actor end
    end
end

function fire.hook_extinguish(callback)
    local path = base_path .. ":FizzleOut"
    if not valid(StaticFindObject(path)) then return false end
    RegisterHook(path, function(context) callback(context:get()) end)
    return true
end

function fire.start(turret, view)
    local root = turret.RootComponent
    local niagara = StaticFindObject("/Script/Niagara.Default__NiagaraFunctionLibrary")
    local sound_api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local flames, audio, light, defaults = templates()
    if not valid(root) or not valid(niagara) or not valid(sound_api) or not valid(defaults) then return end
    view.fire_started = true

    -- Follow the host's fire with sound, light and particles only.
    view.flames = niagara:SpawnSystemAttached(flames.Asset, root, FName("None"), flames.RelativeLocation,
        flames.RelativeRotation, 0, true, true, 0, false)
    if valid(view.flames) then view.flames:SetRelativeScale3D(flames.RelativeScale3D) end
    view.fire_audio = sound_api:SpawnSoundAttached(audio.Sound, root, FName("None"), audio.RelativeLocation,
        audio.RelativeRotation, 0, true, audio.VolumeMultiplier, audio.PitchMultiplier, 0,
        audio.AttenuationSettings, nil, true)
    if valid(view.fire_audio) and audio.bOverrideAttenuation then
        view.fire_audio:AdjustAttenuation(audio.AttenuationOverrides)
    end
    add_light(turret, root, light, defaults, view)
end

function fire.stop(view)
    if not view.fire_started then return end
    if valid(view.flames) then view.flames:Deactivate() end
    if valid(view.fire_audio) then view.fire_audio:FadeOut(1, 0, 0) end
    if valid(view.fire_light) then view.fire_light:K2_DestroyComponent(view.fire_light:GetOwner()) end
    view.fire_started, view.flames, view.fire_audio, view.fire_light = nil, nil, nil, nil
end

return fire
