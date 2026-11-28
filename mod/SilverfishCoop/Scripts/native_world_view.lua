local view = {}
local lightning_guest = require("native_weather_lightning_guest")
local ambience_guest = require("native_storm_ambience_guest")
local lightning_context, local_lightning_gate

local day_class = "/Game/Classes/Actors/DayCycle_SunControler_BP.DayCycle_SunControler_BP_C"
local sky_class = "/Game/Assets/StaticMeshes/Mesh_SkyBox/BP_Sky_Sphere_Custom.BP_Sky_Sphere_Custom_C"
local fog_class = "/Script/Engine.ExponentialHeightFog"
local fog_component = "/Script/Engine.ExponentialHeightFogComponent"
local cloud_class = "/Script/Engine.VolumetricCloud"
local cloud_component = "/Script/Engine.VolumetricCloudComponent"
local cloud_material = "/Game/Assets/Materials/Mat_Misc/m_SimpleVolumetricCloud_Stormy.m_SimpleVolumetricCloud_Stormy"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local math_path = "/Script/Engine.Default__KismetMathLibrary"
local arrow_class = "/Script/Engine.ArrowComponent"
local light_class = "/Script/Engine.DirectionalLightComponent"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local mid_class = "/Script/Engine.MaterialInstanceDynamic"
local max_seconds = 922337203685
local tolerance = 0.025
local weather_tolerance = 0.0001
local lease_seconds = 3
local status_recheck_seconds = 0.5
local lease, receipt, warning_receipts
local weather_fields = {
    -- TheFog_BP blends to a stock fog density of 1.2.
    { "fog_density", 0, 1.2 + weather_tolerance }, { "fog_falloff", 0, 2 }, { "cloud_bottom_km", 1, 10 },
    { "cloud_height_km", 3, 20 }, { "cloud_density_offset", 0, 1 },
}

local profiles = require("authored_world_view")
local weather_view = require("native_weather_view")
local lightning_host = require("native_weather_lightning_host")
local storm_ambience = require("native_storm_ambience_host")

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
        and not object:GetFullName():find("Default__", 1, true)
end

local function live_actor(actor, world)
    return valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
end

local function bounded(number, low, high)
    return type(number) == "number" and number == number and number >= low and number <= high
end

local function resolve_day(world, instance, profile)
    local path = profile.day:match("^%S+ (.+)$")
    local found = path and StaticFindObject(path)
    if not live_actor(found, world) or not exact(found, day_class) or found:GetFullName() ~= profile.day
        or found.Ready ~= true or found.OnlyTrackTime ~= false or found.bReplicates ~= false
        or type(found.UpdateInRealTime) ~= "boolean" or not same(found["Game Instance"], instance) then
        error("native_world_day_cycle_unavailable", 0)
    end
    local north, sun, light = found.Arrow_North, found.Arrow_Sun, found.DLight_Sun
    if not exact(north, arrow_class) or not exact(sun, arrow_class) or not exact(light, light_class)
        or not same(north:GetOwner(), found) or not same(sun:GetOwner(), found) or not same(light:GetOwner(), found)
        or not same(north:GetAttachParent(), found.RootComponent) or not same(sun:GetAttachParent(), north)
        or not same(light:GetAttachParent(), sun) then
        error("native_world_sun_attachments_unavailable", 0)
    end
    return found, north, sun, light
end

local function context(state, phase)
    local hosting = phase == "hosting"
    local world = state.world
    local profile = profiles[state.map]
    local detail = state.local_pawn
    local pawn, controller = detail and detail.pawn, detail and detail.controller_object
    if (not hosting and phase ~= "connected") or not profile or not state.listening
        or state.server ~= (not hosting) or not valid(world) or world:GetFullName() ~= profile.world
        or not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or not live_actor(pawn, world) or not live_actor(controller, world)
        or pawn:HasAuthority() ~= hosting or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller) then
        error("native_world_exact_local_context_unavailable", 0)
    end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection or nil
    if not valid(driver) or not same(driver.World, world) or valid(connection) == hosting then
        error("native_world_transport_unavailable", 0)
    end
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(pawn) or nil
    local save = valid(instance) and instance.SaveSlot_Ref or nil
    if not exact(instance, instance_class) or not same(world.OwningGameInstance, instance)
        or not exact(save, save_class) or not same(pawn.Save, save) then
        error("native_world_canonical_context_unavailable", 0)
    end
    local day, north, sun, light = resolve_day(world, instance, profile)
    return { profile = profile, world = world, pawn = pawn, driver = driver, connection = connection, statics = statics,
        instance = instance, save = save, day = day, north = north, sun = sun, light = light }
end

function view.read_game_seconds_lua(save)
    local maths = StaticFindObject(math_path)
    if not valid(maths) then error("native_world_clock_unavailable", 0) end
    local parts = {}
    maths:BreakTimespan(save.GameTime, parts, parts, parts, parts, parts)
    for _, field in ipairs({ "Days", "Hours", "Minutes", "Seconds", "Milliseconds" }) do
        if type(parts[field]) ~= "number" or not math.tointeger(parts[field]) then
            error("native_world_clock_unavailable", 0)
        end
    end
    if not bounded(parts.Days, 0, 10675200) or not bounded(parts.Hours, 0, 23)
        or not bounded(parts.Minutes, 0, 59) or not bounded(parts.Seconds, 0, 59)
        or not bounded(parts.Milliseconds, 0, 999) then error("native_world_clock_out_of_range", 0) end
    local seconds = parts.Days * 86400 + parts.Hours * 3600 + parts.Minutes * 60
        + parts.Seconds + parts.Milliseconds / 1000
    if not bounded(seconds, 0, max_seconds) then error("native_world_clock_out_of_range", 0) end
    return seconds
end

local function game_seconds(save)
    if type(SilverfishReadNativeSavedGameSeconds) == "function" then
        local seconds = SilverfishReadNativeSavedGameSeconds(save)
        if seconds ~= nil then return seconds end
    end
    return view.read_game_seconds_lua(save)
end

local function clock_text(seconds)
    return string.format("%d:%02d", math.floor(seconds / 3600) % 24, math.floor(seconds / 60) % 60)
end

local function sky_material(current)
    local sky = current.day.SkySphere
    if not live_actor(sky, current.world) or not exact(sky, sky_class) or sky:GetFullName() ~= current.profile.sky
        or sky.bReplicates ~= false or not same(sky["Sun_Component"], current.light) then return nil end
    local mesh, material = sky.SkySphereMesh, sky["Sky material"]
    if not exact(mesh, mesh_class) or not same(mesh:GetOwner(), sky) or not exact(material, mid_class)
        or not same(mesh:GetMaterial(0), material) then return nil end
    return sky, material
end

local function sky_direction(material)
    local name = FName("Light direction", EFindName.FNAME_Find)
    if name:GetComparisonIndex() == 0 then return nil end
    local color = material:K2_GetVectorParameterValue(name)
    if color and bounded(color.R, -1.01, 1.01) and bounded(color.G, -1.01, 1.01)
        and bounded(color.B, -1.01, 1.01) then return color end
end

local function weather_context(current)
    local fog, cloud = current.day.HightFog, current.day.Clouds_Ref
    if not live_actor(fog, current.world) or not exact(fog, fog_class) or fog:GetFullName() ~= current.profile.fog
        or fog.bReplicates ~= false or not live_actor(cloud, current.world) or not exact(cloud, cloud_class)
        or cloud:GetFullName() ~= current.profile.cloud or cloud.bReplicates ~= false then return nil end
    local height_fog, volume, material = fog.Component, cloud.VolumetricCloudComponent, current.day.ClodMat_Inst
    if not exact(height_fog, fog_component) or not same(height_fog, fog.RootComponent)
        or not same(height_fog:GetOwner(), fog) or not exact(volume, cloud_component)
        or not same(volume, cloud.RootComponent) or not same(volume:GetOwner(), cloud)
        or not exact(material, mid_class) or not same(volume.Material, material)
        or not same(material.Parent, StaticFindObject(cloud_material)) then return nil end
    local density = FName("DensityOffset", EFindName.FNAME_Find)
    if density:GetComparisonIndex() == 0 then return nil end
    return { fog = fog, cloud = cloud, height_fog = height_fog, volume = volume, material = material, density = density }
end

local function weather_fields_valid(report)
    for _, field in ipairs(weather_fields) do
        if not bounded(report[field[1]], field[2], field[3]) then return false end
    end
    return true
end

local function has_weather(report)
    for _, field in ipairs(weather_fields) do
        if report[field[1]] ~= nil then return true end
    end
    return false
end

local function read_weather(weather)
    local report = {
        fog_density = weather.height_fog.FogDensity, fog_falloff = weather.height_fog.FogHeightFalloff,
        cloud_bottom_km = weather.volume.LayerBottomAltitude, cloud_height_km = weather.volume.LayerHeight,
        cloud_density_offset = weather.material:K2_GetScalarParameterValue(weather.density),
    }
    return weather_fields_valid(report) and report or nil
end

local function copy_weather(target, source)
    for _, field in ipairs(weather_fields) do target[field[1]] = source[field[1]] end
end

local function observed_weather(current)
    local weather = weather_context(current)
    return weather and read_weather(weather) or nil
end

function view.observe_weather(state, phase)
    if phase ~= "hosting" or not profiles[state.map] then return nil end
    return observed_weather(context(state, phase))
end

local function capture(current)
    local rotation = current.north.RelativeRotation
    local sun = current.light:GetForwardVector()
    local visible, intensity = current.light:IsVisible(), current.light.Intensity
    if not rotation or not sun or not bounded(rotation.Pitch, -360, 360) or not bounded(rotation.Yaw, -360, 360)
        or not bounded(rotation.Roll, -360, 360) or not bounded(sun.X, -1.01, 1.01)
        or not bounded(sun.Y, -1.01, 1.01) or not bounded(sun.Z, -1.01, 1.01)
        or type(visible) ~= "boolean" or not bounded(intensity, 0, 100000) then
        error("native_world_sun_readback_unavailable", 0)
    end
    local report = { map = current.profile.map, actor = current.profile.day,
        pitch = rotation.Pitch, yaw = rotation.Yaw, roll = rotation.Roll,
        sun_x = sun.X, sun_y = sun.Y, sun_z = sun.Z, sun_visible = visible, sun_intensity = intensity }
    local ok, sky = pcall(function()
        local _, material = sky_material(current)
        if material then return sky_direction(material) end
    end)
    if ok and sky then report.sky_r, report.sky_g, report.sky_b = sky.R, sky.G, sky.B end
    return report
end

local function append_storm(report, current)
    local ok, storm = pcall(storm_ambience.capture, current)
    if ok then report.storm_ambience = storm end
    return report
end

local function observe_lua(state, phase)
    local current = context(state, phase)
    local report = capture(current)
    report.game_seconds = game_seconds(current.save)
    local ok, weather = pcall(observed_weather, current)
    if ok and weather then copy_weather(report, weather) end
    local rain_ok, rain = pcall(weather_view.read_rain, current)
    if rain_ok and rain then weather_view.copy_rain(report, rain) end
    return append_storm(report, current)
end

function view.observe(state, phase)
    local profile = profiles[state.map]
    if phase ~= "hosting" or not profile then return nil end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if state.listening and state.server == false and type(SilverfishReadNativeHostWeather) == "function"
        and valid(state.world) and valid(pawn) then
        local report, day = SilverfishReadNativeHostWeather(state.world:GetAddress(), pawn:GetAddress(), profile)
        if report then return append_storm(report, { world = state.world, day = day }) end
    end
    return observe_lua(state, phase)
end

local function real_time(current)
    local seconds = current.statics:GetRealTimeSeconds(current.pawn)
    if not bounded(seconds, 0, max_seconds) then error("native_world_lease_clock_unavailable", 0) end
    return seconds
end

local function valid_frame(frame)
    local profile = type(frame) == "table" and profiles[frame.map]
    if not profile or frame.actor ~= profile.day
        or type(frame.epoch) ~= "string" or #frame.epoch > 128 or not frame.epoch:match("^[%w_%-]+$")
        or not bounded(frame.sequence, 1, 9007199254740991) or not math.tointeger(frame.sequence)
        or not bounded(frame.game_seconds, 0, max_seconds) or type(frame.sun_visible) ~= "boolean"
        or not bounded(frame.sun_intensity, 0, 100000) then return false end
    for _, key in ipairs({ "pitch", "yaw", "roll" }) do
        if not bounded(frame[key], -360, 360) then return false end
    end
    for _, key in ipairs({ "sun_x", "sun_y", "sun_z" }) do
        if not bounded(frame[key], -1.01, 1.01) then return false end
    end
    local length = frame.sun_x * frame.sun_x + frame.sun_y * frame.sun_y + frame.sun_z * frame.sun_z
    if not bounded(length, 0.95, 1.05) then return false end
    if (frame.sky_r ~= nil or frame.sky_g ~= nil or frame.sky_b ~= nil)
        and not (bounded(frame.sky_r, -1.01, 1.01) and bounded(frame.sky_g, -1.01, 1.01)
            and bounded(frame.sky_b, -1.01, 1.01)) then
        return false
    end
    return (not has_weather(frame) or weather_fields_valid(frame))
        and (not weather_view.has_rain(frame) or weather_view.valid_rain(frame))
end

local function project_visuals(current, report)
    local rotation = { Pitch = report.pitch, Yaw = report.yaw, Roll = report.roll }
    current.north:K2_SetRelativeRotation(rotation, false, {}, false)
    current.light:SetVisibility(report.sun_visible, false)
    current.light:SetIntensity(report.sun_intensity)
    local qualified, sky = pcall(sky_material, current)
    if qualified and sky then sky:UpdateSunDirection() end
end

local function matches_vector(first, second, x, y, z)
    local dx, dy, dz = first[x] - second[x], first[y] - second[y], first[z] - second[z]
    return dx * dx + dy * dy + dz * dz <= tolerance * tolerance
end

local function matches_sun(actual, expected)
    return actual.sun_visible == expected.sun_visible
        and math.abs(actual.sun_intensity - expected.sun_intensity) <= tolerance
        and matches_vector(actual, expected, "sun_x", "sun_y", "sun_z")
end

local function project_fog(weather, report)
    weather.height_fog:SetFogDensity(report.fog_density)
    weather.height_fog:SetFogHeightFalloff(report.fog_falloff)
end

local function project_cloud_layer(weather, report)
    weather.volume:SetLayerBottomAltitude(report.cloud_bottom_km)
    weather.volume:SetLayerHeight(report.cloud_height_km)
end

local function project_weather(weather, report)
    project_fog(weather, report)
    project_cloud_layer(weather, report)
    weather.material:SetScalarParameterValue(weather.density, report.cloud_density_offset)
end

local function matches_weather(actual, expected)
    if not actual then return false end
    for _, field in ipairs(weather_fields) do
        if math.abs(actual[field[1]] - expected[field[1]]) > weather_tolerance then return false end
    end
    return true
end

local function fog_target(current, weather)
    return live_actor(weather.fog, current.world) and weather.fog:GetFullName() == current.profile.fog
        and weather.fog.bReplicates == false
        and same(weather.fog.Component, weather.height_fog) and same(weather.fog.RootComponent, weather.height_fog)
        and same(weather.height_fog:GetOwner(), weather.fog)
end

local function cloud_target(current, weather)
    return live_actor(weather.cloud, current.world) and weather.cloud:GetFullName() == current.profile.cloud
        and weather.cloud.bReplicates == false
        and same(weather.cloud.VolumetricCloudComponent, weather.volume)
        and same(weather.cloud.RootComponent, weather.volume) and same(weather.volume:GetOwner(), weather.cloud)
end

local function weather_current(current, weather)
    return fog_target(current, weather) and cloud_target(current, weather)
        and same(current.day.HightFog, weather.fog) and same(current.day.Clouds_Ref, weather.cloud)
        and same(current.day.ClodMat_Inst, weather.material) and same(weather.volume.Material, weather.material)
end

local function restore_weather(previous)
    local weather, original = previous.weather, previous.original_weather
    if not weather then return end
    local restored_fog = pcall(function()
        if fog_target(previous.context, weather) then
            project_fog(weather, original)
            if not (math.abs(weather.height_fog.FogDensity - original.fog_density) <= weather_tolerance
                and math.abs(weather.height_fog.FogHeightFalloff - original.fog_falloff) <= weather_tolerance) then
                error("native_world_original_fog_unverified", 0)
            end
        end
    end)
    local restored_cloud = pcall(function()
        if cloud_target(previous.context, weather) then
            project_cloud_layer(weather, original)
            if not (math.abs(weather.volume.LayerBottomAltitude - original.cloud_bottom_km) <= weather_tolerance
                and math.abs(weather.volume.LayerHeight - original.cloud_height_km) <= weather_tolerance) then
                error("native_world_original_cloud_unverified", 0)
            end
            if same(weather.volume.Material, weather.material) then
                weather.material:SetScalarParameterValue(weather.density, original.cloud_density_offset)
                if not (math.abs(weather.material:K2_GetScalarParameterValue(weather.density)
                    - original.cloud_density_offset) <= weather_tolerance) then
                    error("native_world_original_cloud_density_unverified", 0)
                end
            end
        end
    end)
    if not restored_fog or not restored_cloud then error("native_world_original_weather_unverified", 0) end
end

local function day_current(current)
    local day = current.day
    return live_actor(day, current.world) and day:GetFullName() == current.profile.day
        and day.bReplicates == false and same(day["Game Instance"], current.instance)
end

local function components_current(current)
    local day = current.day
    return day_current(current)
        and same(day.Arrow_North, current.north) and same(day.Arrow_Sun, current.sun)
        and same(day.DLight_Sun, current.light) and same(current.north:GetOwner(), day)
        and same(current.light:GetAttachParent(), current.sun) and same(current.sun:GetAttachParent(), current.north)
end

function view.clear()
    lightning_guest.clear()
    ambience_guest.clear()
    local previous = lease
    lease = nil
    if not previous then return true end
    local current = previous.context
    local rain_ok, rain_failure = pcall(weather_view.destroy_rain, previous.rain)
    local stock_rain_ok, stock_rain_failure = pcall(weather_view.restore_local_rain, current, previous.local_rain)
    local weather_ok, weather_failure = pcall(restore_weather, previous)
    local ok, failure = pcall(function()
        if not day_current(current) then return end
        current.day.UpdateInRealTime = previous.realtime
        if current.day.UpdateInRealTime ~= previous.realtime then
            error("native_world_original_clock_gate_unverified", 0)
        end
        if not components_current(current) then return end
        project_visuals(current, previous.baseline)
        if not matches_sun(capture(current), previous.baseline) then
            error("native_world_original_render_unverified", 0)
        end
    end)
    if not rain_ok then return false, rain_failure end
    if not stock_rain_ok then return false, stock_rain_failure end
    if not weather_ok then return false, weather_failure end
    return ok, failure
end

local function lease_time_lua(current)
    if same(current.world.NetDriver, current.driver) and same(current.driver.ServerConnection, current.connection)
        and same(current.instance.SaveSlot_Ref, current.save) and same(current.pawn.Save, current.save) then
        return real_time(current)
    end
end

local function lease_time(state, phase)
    local current = lease.context
    if phase ~= "connected" or not state.server or state.map ~= current.profile.map or not same(state.world, current.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, current.pawn) then return end
    local now
    if lease.native_clock_args and type(SilverfishReadNativeWeatherLeaseTime) == "function" then
        local ok, seconds = pcall(SilverfishReadNativeWeatherLeaseTime, table.unpack(lease.native_clock_args))
        if ok and bounded(seconds, 0, max_seconds) then now = seconds end
    end
    if not now then now = lease_time_lua(current) end
    if now and now < lease.expires then return now end
end

local function lease_components_current_lua()
    return components_current(lease.context) and (not lease.weather or weather_current(lease.context, lease.weather))
end

local function lease_components_current()
    if lease.native_component_args and type(SilverfishVerifyNativeGuestWeatherComponents) == "function"
        and SilverfishVerifyNativeGuestWeatherComponents(table.unpack(lease.native_component_args)) == true then
        return true
    end
    return lease_components_current_lua()
end

local function lease_current(state, phase)
    return lease_time(state, phase) ~= nil and lease_components_current()
end

function view.sync(state, phase)
    pcall(weather_view.sync, state, phase)
    pcall(lightning_host.sync, state, phase)
    if not lease then return true end
    local ok, current = pcall(lease_current, state, phase)
    if not ok or not current then return view.clear() end
    ambience_guest.sync(lease.context, lease.frame.epoch)
    return true
end

local function readback(current, frame, saved_seconds)
    local report = capture(current)
    report.game_seconds, report.clock_text = frame.game_seconds, clock_text(frame.game_seconds)
    report.epoch, report.sequence = frame.epoch, frame.sequence
    report.save_unchanged = same(current.instance.SaveSlot_Ref, current.save)
        and game_seconds(current.save) == saved_seconds
    report.sky_verified = frame.sky_r ~= nil and report.sky_r ~= nil
        and matches_vector(report, frame, "sky_r", "sky_g", "sky_b")
    report.weather_verified = false
    if lease.weather then
        local weather = read_weather(lease.weather)
        if weather then copy_weather(report, weather) end
        report.weather_verified = matches_weather(weather, frame)
    end
    if weather_view.has_rain(frame) then
        weather_view.copy_rain(report, frame)
        report.rain_skipped_nullrhi = lease.rain_skipped_nullrhi
        report.rain_verified = not report.rain_skipped_nullrhi and weather_view.rain_verified(lease.rain, frame)
    end
    return report
end

function view.apply(state, phase, frame)
    if phase ~= "connected" or not valid_frame(frame) or frame.map ~= state.map then
        error("native_world_authenticated_snapshot_required", 0)
    end
    local cleared, clear_error = view.sync(state, phase)
    if not cleared then error(clear_error, 0) end
    local current = context(state, phase)
    if receipt and same(receipt.world, current.world) and same(receipt.connection, current.connection) then
        if receipt.epoch ~= frame.epoch then error("native_world_snapshot_wrong_epoch", 0) end
        if same(receipt.pawn, current.pawn) and frame.sequence <= receipt.sequence then
            error("native_world_snapshot_already_observed", 0)
        end
    end
    local saved_seconds = game_seconds(current.save)
    local now = real_time(current)
    local weather, original_weather
    if has_weather(frame) and not (lease and lease.weather) then
        weather = weather_context(current)
        original_weather = weather and read_weather(weather)
        if not original_weather then error("native_world_weather_components_unavailable", 0) end
    end
    if not lease then
        lease = { baseline = capture(current), realtime = current.day.UpdateInRealTime }
    end
    lease.context, lease.frame, lease.expires, lease.saved_seconds = current, frame, now + lease_seconds, saved_seconds
    local ok, report = pcall(function()
        lease.native_clock_args = type(SilverfishReadNativeWeatherLeaseTime) == "function" and {
            current.world:GetAddress(), current.pawn:GetAddress(), current.driver:GetAddress(),
            current.connection:GetAddress(), current.instance:GetAddress(), current.save:GetAddress(),
        } or nil
        current.day.UpdateInRealTime = false
        project_visuals(current, frame)
        lease.local_rain = weather_view.bind_local_rain(current, lease.local_rain)
        weather_view.suppress_local_rain(lease.local_rain)
        if has_weather(frame) then
            lease.weather = lease.weather or weather
            lease.original_weather = lease.original_weather or original_weather
            project_weather(lease.weather, frame)
        elseif lease.weather then
            restore_weather(lease)
            lease.weather, lease.original_weather = nil, nil
        end
        if type(SilverfishVerifyNativeGuestWeatherComponents) == "function" then
            local args = {current.world:GetAddress(), current.instance:GetAddress(), current.profile,
                current.day:GetAddress(), current.north:GetAddress(), current.sun:GetAddress(), current.light:GetAddress()}
            local targets = lease.weather
            if targets then
                args[8], args[9] = targets.fog:GetAddress(), targets.height_fog:GetAddress()
                args[10], args[11] = targets.cloud:GetAddress(), targets.volume:GetAddress()
                args[12] = targets.material:GetAddress()
            end
            lease.native_component_args = args
        else
            lease.native_component_args = nil
        end
        lease.rain_skipped_nullrhi = weather_view.skip_wet_rain(frame)
        if weather_view.has_rain(frame) and not lease.rain_skipped_nullrhi then
            lease.rain = weather_view.apply_rain(current, frame, lease.rain)
        elseif lease.rain then
            weather_view.destroy_rain(lease.rain)
            lease.rain = nil
        end
        local actual = readback(current, frame, saved_seconds)
        if current.day.UpdateInRealTime ~= false or not actual.save_unchanged or not matches_sun(actual, frame)
            or frame.sky_r ~= nil and actual.sky_r ~= nil and not actual.sky_verified
            or has_weather(frame) and not actual.weather_verified
            or weather_view.has_rain(frame) and not (actual.rain_verified or actual.rain_skipped_nullrhi) then
            error("native_world_projection_readback_mismatch", 0)
        end
        return actual
    end)
    if not ok then
        local restored = view.clear()
        error(restored and report or "native_world_projection_restore_unverified", 0)
    end
    lease.status_report, lease.status_recheck = report, now + status_recheck_seconds
    receipt = {world = current.world, connection = current.connection, pawn = current.pawn,
        epoch = frame.epoch, sequence = frame.sequence}
    return report
end

function view.status(state, phase)
    if not lease then return nil end
    local ok, report = pcall(function()
        local now = lease_time(state, phase)
        if not now or lease.context.day.UpdateInRealTime ~= false then return nil end
        -- Snapshot application verifies the actual scene immediately. Between snapshots,
        -- reuse that report and periodically check for changes in the local scene.
        if now < lease.status_recheck then return lease.status_report end
        if not lease_components_current() then return nil end
        local current, frame = lease.context, lease.frame
        local actual = readback(current, frame, lease.saved_seconds)
        lease.status_report = actual.save_unchanged and matches_sun(actual, frame) and actual or nil
        lease.status_recheck = now + status_recheck_seconds
        return lease.status_report
    end)
    return ok and report or nil
end

function view.weather_cue_rows(state, phase, row, host_report)
    local rows = weather_view.siren_rows(state, phase, row)
    for _, strike in ipairs(lightning_host.rows(state, phase, row)) do rows[#rows + 1] = strike end
    if host_report then storm_ambience.append(rows, host_report.storm_ambience, row) end
    return rows
end

function view.play_siren(state, phase, epoch, sequence, source, cue, remaining)
    if not lease or lease.frame.epoch ~= epoch or not lease_current(state, phase)
        or not math.tointeger(sequence) or sequence < 1 or sequence >= 2^53 then
        error("guest_weather_siren_scope_unavailable", 0)
    end
    local world = lease.context.world
    if not warning_receipts or not same(warning_receipts.world, world) or warning_receipts.epoch ~= epoch then
        warning_receipts = {world = world, epoch = epoch, sources = {}}
    end
    if sequence <= (warning_receipts.sources[source] or 0) then return "weather siren already played" end
    if not weather_view.play_siren(lease.context, source, cue, remaining) then
        error("guest_weather_siren_source_unavailable", 0)
    end
    warning_receipts.sources[source] = sequence
    return "stock weather siren played"
end

function view.sync_lightning(state, phase)
    local current = lease and lease.context or lightning_context
    if phase ~= "connected" or state.server ~= true or not state.listening then
        current = nil
    elseif not current or state.map ~= current.profile.map or not same(state.world, current.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, current.pawn)
        or not same(current.world.NetDriver, current.driver) or not same(current.driver.ServerConnection, current.connection) then
        local ok, scope = pcall(context, state, phase)
        current = ok and scope or nil
    end
    lightning_context = current
    local_lightning_gate = weather_view.gate_local_lightning(current, local_lightning_gate)
end

function view.play_lightning(state, phase, epoch, serial, position, rotation)
    if not lease or lease.frame.epoch ~= epoch or not lease_current(state, phase)
        or not math.tointeger(serial) or serial < 1 or serial >= 2^53
        or not bounded(position.X, -10000000, 10000000) or not bounded(position.Y, -10000000, 10000000)
        or not bounded(position.Z, -10000000, 10000000) or not bounded(rotation.Pitch, -360, 360)
        or not bounded(rotation.Yaw, -360, 360) or not bounded(rotation.Roll, -360, 360) then
        error("guest_weather_lightning_scope_unavailable", 0)
    end
    return lightning_guest.play(lease.context, epoch, serial, position, rotation)
end

function view.lightning_rows(row)
    if not lease then return {} end
    return lightning_guest.rows(lease.context, lease.frame.epoch, row)
end

function view.apply_ambience(state, phase, epoch, report)
    if not lease or lease.frame.epoch ~= epoch or not lease_current(state, phase) then
        error("guest_weather_ambience_scope_unavailable", 0)
    end
    return ambience_guest.apply(lease.context, epoch, report)
end

view.ambience_rows = ambience_guest.rows

view.animate_lightning = lightning_guest.animate

return view
