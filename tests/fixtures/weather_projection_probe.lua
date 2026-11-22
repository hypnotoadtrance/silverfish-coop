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

local rain, installed, previous
LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    if not valid(driver) then return end
    local guest = valid(driver.ServerConnection)
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
