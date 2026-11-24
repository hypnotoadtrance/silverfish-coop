local lightning = {}

local strike_path = "/Game/Assets/PFX/NI_Lightning_Strike.NI_Lightning_Strike"
local blast_path = "/Game/Assets/PFX/FX_Blast_PulseGrenade_Converted.FX_Blast_PulseGrenade_Converted"
local sound_path = "/Game/Assets/Sounds/MetaSounds/MS_LightningStrike.MS_LightningStrike"
local niagara_path = "/Script/Niagara.Default__NiagaraFunctionLibrary"
local light_path = "/Script/Engine.DirectionalLightComponent"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local zero_rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local normal_scale = {X = 1, Y = 1, Z = 1}
local identity = {X = 0, Y = 0, Z = 0, W = 1}
local active, receipt = {}, nil

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function asset(path, class)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    if valid(object) and object:GetFullName() == class .. " " .. path then return object end
end

local function destroy_component(component, owner)
    if valid(component) and valid(owner) and not owner:IsActorBeingDestroyed() then
        pcall(component.K2_DestroyComponent, component, owner)
    end
end

local function finish(entry)
    destroy_component(entry.light, entry.pawn)
    for _, component in pairs(entry.particles) do
        if valid(component) then
            pcall(component.Deactivate, component)
            local owner = component:GetOwner()
            if valid(owner) then destroy_component(component, owner) end
        end
    end
    if valid(entry.audio) then pcall(entry.audio.Stop, entry.audio) end
end

function lightning.clear()
    for _, entry in ipairs(active) do finish(entry) end
    active = {}
end

local function add_flash(entry, position, rotation, class)
    local pawn = entry.pawn
    local point = {X = position.X, Y = position.Y, Z = position.Z + 5000}
    local transform = {Rotation = identity, Translation = point, Scale3D = normal_scale}
    local light = pawn:AddComponentByClass(class, false, transform, true)
    if not valid(light) then return false end
    entry.light = light
    light:SetVisibility(false, false)
    light:SetMobility(2)
    light:SetAbsolute(true, true, true)
    -- Cooked LightningBolt_BP DirectionalLight template. Keep the other
    -- properties at the engine component defaults, as the stock template does.
    light.ForwardShadingPriority = 100
    light:SetAtmosphereSunLight(false)
    light:SetUseTemperature(true)
    light:SetTemperature(8065.5996)
    light:SetIntensity(6)
    light:SetCastShadows(false)
    pawn:FinishAddComponent(light, false, transform)
    light:K2_SetWorldLocationAndRotation(point, rotation, false, {}, true)
    light:SetVisibility(true, false)
    return same(light:GetOwner(), pawn) and light:IsVisible()
end

function lightning.play(current, epoch, serial, position, rotation)
    if not receipt or not same(receipt.world, current.world) or receipt.epoch ~= epoch then
        lightning.clear()
        receipt = {world = current.world, epoch = epoch, serial = 0}
    end
    if serial <= receipt.serial then return "duplicate" end

    local strike, blast = asset(strike_path, "NiagaraSystem"), asset(blast_path, "NiagaraSystem")
    local sound = asset(sound_path, "MetaSoundSource")
    local niagara, light_class = StaticFindObject(niagara_path), StaticFindObject(light_path)
    if not strike or not blast or not sound or not valid(niagara) or not valid(light_class) then
        error("guest_weather_lightning_assets_unavailable", 0)
    end

    local pawn, api = current.pawn, current.statics
    local now = api:GetRealTimeSeconds(current.world)
    local entry = {world = current.world, pawn = pawn, particles = {}, flash_until = now + .125, until_time = now + 8}
    if #active >= 8 then finish(table.remove(active, 1)) end
    active[#active + 1] = entry

    -- Each cue is attempted once per authoritative serial, including when
    -- NullRHI or a muted audio device cannot create a corresponding component.
    receipt.serial, receipt.position, receipt.rotation = serial, position, rotation
    receipt.flags = {0, 0, 0, 0}
    local flash_ok, flash = pcall(add_flash, entry, position, rotation, light_class)
    if flash_ok and flash then
        receipt.flags[1] = 1
    else
        destroy_component(entry.light, pawn)
        entry.light = nil
    end
    for index, particle in ipairs({strike, blast}) do
        local ok, component = pcall(niagara.SpawnSystemAtLocation, niagara, pawn, particle, position, zero_rotation,
            normal_scale, true, true, 0, true)
        if ok and valid(component) then
            entry.particles[index] = component
            receipt.flags[index + 1] = 1
        end
    end
    local sound_ok, audio = pcall(api.SpawnSoundAtLocation, api, pawn, sound, position, zero_rotation, 1, 1, 0, nil, nil, true)
    if sound_ok and valid(audio) then
        entry.audio = audio
        receipt.flags[4] = 1
        pcall(function()
            local from = pawn:K2_GetActorLocation()
            local distance = math.sqrt((from.X - position.X)^2 + (from.Y - position.Y)^2 + (from.Z - position.Z)^2)
            audio:SetFloatParameter(FName("Distance"), distance)
        end)
    end
    return "played"
end

function lightning.animate(world)
    if #active == 0 then return end
    if not valid(world) then lightning.clear(); return end
    local api = StaticFindObject(statics_path)
    if not valid(api) then lightning.clear(); return end
    local now = api:GetRealTimeSeconds(world)
    for index = #active, 1, -1 do
        local entry = active[index]
        if not same(entry.world, world) or now >= entry.until_time or not valid(entry.pawn) then
            finish(entry)
            table.remove(active, index)
        elseif entry.light and now >= entry.flash_until then
            destroy_component(entry.light, entry.pawn)
            entry.light = nil
        end
    end
end

function lightning.rows(current, epoch, row)
    if not receipt or not receipt.flags or not same(receipt.world, current.world) or receipt.epoch ~= epoch then return {} end
    local point, rotation = receipt.position, receipt.rotation
    return {row("native-weather-lightning-played", epoch, receipt.serial, point.X, point.Y, point.Z,
        rotation.Pitch, rotation.Yaw, rotation.Roll, table.unpack(receipt.flags))}
end

return lightning
