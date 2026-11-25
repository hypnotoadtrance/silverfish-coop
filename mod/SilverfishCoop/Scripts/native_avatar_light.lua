local light_view = {}

local source_class = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local light_class = "/Script/Engine.SpotLightComponent"
local light_template = source_class .. ":SpotLight_GEN_VARIABLE"
local cookie_path = "/Game/Assets/Materials/Mat_Misc/M_LightCookie.M_LightCookie"
local click_path = "/Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local click_range = 4000 -- Switch_Reset_Cue's stock attenuation maximum in centimeters.
local statics = "/Script/Engine.Default__GameplayStatics"
local transform = {Translation = {X = 0, Y = 0, Z = 0}, Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    return object
end

function light_view.create(pawn, mount, profile, values)
    local class, cookie = StaticFindObject(light_class), asset(cookie_path)
    if not valid(class) or not valid(cookie) or not valid(asset(source_class)) then return end
    local stock = StaticFindObject(light_template)
    if not valid(stock) or not same(stock:GetClass(), class) then return end
    local spot = pawn:AddComponentByClass(class, true, transform, true)
    if not valid(spot) then return end
    local ok = pcall(function()
        spot:SetVisibility(false, false)
        spot:SetMobility(2)
        pawn:FinishAddComponent(spot, true, transform)
        local none = FName("None", EFindName.FNAME_Find)
        if not spot:K2_AttachToComponent(mount, none, 2, 2, 0, false) then error("flashlight view attachment refused") end
        spot:K2_SetRelativeLocationAndRotation({X = values[1], Y = values[2], Z = values[3]},
            {Pitch = values[4], Yaw = values[5], Roll = values[6]}, false, {}, true)
        -- A bare component uses engine defaults. Match the stock emitter's units,
        -- falloff and cookie even when the remote first-person actor is hidden.
        spot:SetUseInverseSquaredFalloff(stock.bUseInverseSquaredFalloff)
        spot:SetIntensityUnits(stock.IntensityUnits)
        spot:SetLightFalloffExponent(stock.LightFalloffExponent)
        spot:SetCastShadows(stock.CastShadows)
        spot:SetUseTemperature(stock.bUseTemperature)
        spot:SetTemperature(stock.Temperature)
        spot:SetLightFunctionFadeDistance(stock.LightFunctionFadeDistance)
        spot:SetLightFunctionDisabledBrightness(stock.DisabledBrightness)
        spot:SetVolumetricScatteringIntensity(stock.VolumetricScatteringIntensity)
        spot:SetHiddenInGame(false, false)
        spot:SetIntensity(profile.intensity)
        spot:SetAttenuationRadius(profile.radius)
        spot:SetInnerConeAngle(profile.inner)
        spot:SetOuterConeAngle(profile.outer)
        spot:SetLightColor({R = values[7], G = values[8], B = values[9], A = 1}, true)
        spot:SetLightFunctionMaterial(cookie)
    end)
    if ok then return spot end
    spot:K2_DestroyComponent(pawn)
end

function light_view.click(listener, mount)
    local api, sound = StaticFindObject(statics), asset(click_path)
    if not valid(api) or not valid(sound) then return false end
    local point, ear = mount:K2_GetComponentLocation(), listener:K2_GetActorLocation()
    if (point.X - ear.X)^2 + (point.Y - ear.Y)^2 + (point.Z - ear.Z)^2 > click_range^2 then return false end
    api:PlaySoundAtLocation(listener, sound, point, {Pitch = 0, Yaw = 0, Roll = 0}, 1, 1, 0, nil, nil, nil, nil)
    return true
end

return light_view
