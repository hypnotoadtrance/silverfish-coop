local box = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not box or not box:find("weather%-proof%-%x+") then return end

local function valid(object)
    return object and object:IsValid()
end
local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end
local function find(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path) object = StaticFindObject(path) end
    return object
end
local function spool(name, value)
    local target = assert(io.open(box .. "/" .. name, "ab"))
    target:write(value, "\n")
    target:close()
end

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local fog_class = "/Game/Classes/Weather/Storms/TheFog_BP.TheFog_BP_C"
local fog_cue = "/Game/Assets/Sounds/CUEs/Alarm_Cues/StormSiren_03_Cue.StormSiren_03_Cue"
local rain, installed, previous, fog_demo

local function start_fog(world, statics, pawn)
    assert(not fog_demo and world:GetFullName() == "World " .. crossroads)
    local day = StaticFindObject(crossroads .. ":PersistentLevel.DayCycle_SunControler_BP_C_1")
    local horn = StaticFindObject(crossroads .. ":PersistentLevel.AlarmSiren_BP_C_1")
    assert(valid(day) and same(day:GetWorld(), world) and not valid(day.LocalStorm) and day:HasAuthority())
    assert(valid(horn) and same(horn:GetWorld(), world) and horn.bReplicates == false)
    local class = find(fog_class)
    assert(valid(class))
    local transform = {Translation = {X = 0, Y = 0, Z = 0},
        Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
    local storm = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
    assert(valid(storm) and same(storm:GetClass(), class) and same(storm:GetWorld(), world))
    assert(math.abs(storm.FogDensity - 1.2) < .0001 and storm.FogDensityBlend == 1
        and storm.SirenTime == 44 and valid(storm.Siren) and storm.Siren:GetFullName() == "SoundCue " .. fog_cue)
    storm.FirstActivation = true
    -- NoHazards keeps the stock fog and warning without spawning a hunting squad.
    storm.StormLevel = 1
    local realtime = day.UpdateInRealTime
    day.UpdateInRealTime = false
    storm = statics:FinishSpawningActor(storm, transform, 0)
    assert(valid(storm))
    day.LocalStorm = storm
    fog_demo = {day = day, storm = storm, realtime = realtime}
    return tostring(storm.FogDensity) .. "|" .. horn:GetFullName() .. "|" .. tostring(storm.SirenTime)
end

local function clear_fog(world)
    assert(fog_demo and valid(fog_demo.day) and same(fog_demo.day:GetWorld(), world))
    local demo = fog_demo
    assert(valid(demo.storm) and same(demo.day.LocalStorm, demo.storm))
    demo.storm:Start_Stop(false)
    demo.storm:K2_DestroyActor()
    demo.day.LocalStorm = nil
    fog_demo = nil
    demo.day:UpdateFog()
    local density = demo.day.HightFog.Component.FogDensity
    demo.day.UpdateInRealTime = demo.realtime
    return tostring(density)
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    if not valid(driver) then return end
    local guest = valid(driver.ServerConnection)
    if fog_demo and valid(fog_demo.storm) and same(fog_demo.storm:GetWorld(), world) then
        fog_demo.day:UpdateFog()
    end
    if guest and not installed then
        RegisterHook("/Script/Niagara.NiagaraComponent:SetVariableFloat", function(context, parameter, amount)
            local particle = context:get()
            if valid(particle) and valid(particle.Asset) and parameter:get():ToString() == "User.Intensity"
                and particle.Asset:GetFullName() == "NiagaraSystem /Game/Assets/PFX/NI_Rain_01.NI_Rain_01" then
                spool("guest-rain", tostring(amount:get()))
            end
        end)
        if valid(StaticFindObject("/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren")) then
            RegisterHook("/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren", function(context, cue, duration)
                local actor, sound = context:get(), cue:get()
                if valid(actor) and valid(sound) and same(actor:GetWorld(), world) then
                    spool("guest-siren", actor:GetFullName() .. "|" .. sound:GetFullName() .. "|" .. tostring(duration:get()))
                end
            end)
        end
        installed = true
    end
    local source = io.open(box .. "/weather-probe", "rb")
    if not source then return end
    local command = source:read("*l")
    source:close()
    if previous == command then return end
    previous = command
    local ok, result = pcall(function()
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        local pawn = statics:GetPlayerPawn(world, 0)
        if guest then
            assert(command == "check" or command == "check-dry")
            local stock, projected = 0, 0
            for _, actor in ipairs(FindAllOf("Rain_BP_C") or {}) do
                if same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then stock = stock + 1 end
            end
            for _, particle in ipairs(FindAllOf("NiagaraComponent") or {}) do
                if valid(particle) and valid(particle.Asset) and same(particle:GetAttachParent(), pawn.RootComponent)
                    and particle.Asset:GetFullName() == "NiagaraSystem /Game/Assets/PFX/NI_Rain_01.NI_Rain_01"
                    and particle:IsActive() then projected = projected + 1 end
            end
            return tostring(stock) .. "|" .. tostring(projected)
        end
        assert(os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")
        if command == "the-fog" then return start_fog(world, statics, pawn) end
        if command == "clear-fog" then return clear_fog(world) end
        if command == "dry" then
            assert(valid(rain))
            rain:UpdateIntensity(0, false)
            return tostring(rain.Intensity)
        end
        assert(command == "wet")
        local day = FindFirstOf("DayCycle_SunControler_BP_C")
        assert(valid(day) and same(day:GetWorld(), world))
        day.UpdateInRealTime = false
        rain = day.RainController
        if not valid(rain) then
            local class = find("/Game/Classes/Actors/Rain_BP.Rain_BP_C")
            local transform = {Translation = pawn:K2_GetActorLocation(),
                Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
            local created = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, nil, 0)
            assert(valid(created), "stock rain spawn unavailable")
            created.Max_LightningChance, created.LightningMod = 0, 0
            created.Intensity, created.Rain_Mod = 0.55, 1.0
            rain = statics:FinishSpawningActor(created, transform, 0)
            day.RainController = rain
        end
        assert(valid(rain))
        rain.Max_LightningChance, rain.LightningMod = 0, 0
        rain:UpdateIntensity(0.55, false)
        local horn
        for _, candidate in ipairs(FindAllOf("AlarmSiren_BP_C") or {}) do
            if same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then horn = candidate break end
        end
        assert(valid(horn), "no authored storm siren")
        local cue = find("/Game/Assets/Sounds/CUEs/Alarm_Cues/StormSiren_03_Cue.StormSiren_03_Cue")
        assert(valid(cue))
        horn:StormSiren(cue, 15)
        return tostring(rain.Intensity) .. "|" .. horn:GetFullName() .. "|" .. tostring(horn.bReplicates)
    end)
    spool("weather-probe-reply", command .. "|" .. (ok and "ok" or "failed") .. "|" .. tostring(result))
end)
