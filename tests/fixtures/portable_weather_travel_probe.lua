local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not bridge or not bridge:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local rain_class = "/Game/Classes/Actors/Rain_BP.Rain_BP_C"
local rain_asset = "NiagaraSystem /Game/Assets/PFX/NI_Rain_01.NI_Rain_01"
local siren_hook = "/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren"
local cue_path = "/Game/Assets/Sounds/CUEs/Alarm_Cues/StormSiren_03_Cue.StormSiren_03_Cue"
local last, monitored, heard

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function find(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), path)
    return object
end

local function observe_siren(world)
    if monitored then return end
    if not valid(StaticFindObject(siren_hook)) then return end
    monitored, heard = world, 0
    RegisterHook(siren_hook, function(context, cue)
        local actor, sound = context:get(), cue:get()
        if valid(actor) and same(actor:GetWorld(), monitored) and valid(sound)
            and sound:GetFullName() == "SoundCue " .. cue_path then heard = heard + 1 end
    end)
end

local function rain_state(day)
    local rain = day.RainController
    if not valid(rain) then return "absent" end
    local particles, audio, loop = rain.NI_Rain_01, rain.MS_Rain_SFX, rain.Rain_Storm_Loop_3
    assert(valid(particles) and valid(audio) and valid(loop))
    return ("%s;%s;%s;%s;%s"):format(rain.Intensity, tostring(rain:IsActorTickEnabled()),
        tostring(particles:IsVisible()), audio.VolumeMultiplier, loop.VolumeMultiplier)
end

local function stock_rain(world, statics, pawn, day, amount)
    local rain = day.RainController
    if not valid(rain) or rain:IsActorBeingDestroyed() then
        if amount == 0 then return "absent" end
        local transform = {Translation = pawn:K2_GetActorLocation(),
            Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
        local created = statics:BeginDeferredActorSpawnFromClass(pawn, find(rain_class), transform, 1, nil, 0)
        assert(valid(created))
        created.Intensity, created.Rain_Mod = amount, 1
        created.Max_LightningChance, created.LightningMod = 0, 0
        rain = statics:FinishSpawningActor(created, transform, 0)
        day.RainController = rain
    end
    assert(same(rain:GetWorld(), world) and rain:HasAuthority(), "The stock rain actor is not locally owned")
    rain:UpdateIntensity(amount, false)
    return rain_state(day)
end

LoopInGameThreadWithDelay(100, function()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. crossroads or not valid(world.NetDriver) then return end
    local guest = valid(world.NetDriver.ServerConnection)
    if guest then observe_siren(world) end
    local file = io.open(bridge .. "/weather-travel-command.tsv", "rb")
    if not file then return end
    local input = file:read("*l")
    file:close()
    if not input or input == last then return end
    local id, action = input:match("^([a-f0-9]+)\t(%a+)$")
    if not id or #id ~= 32 then return end
    last = input
    local ok, result = pcall(function()
        local statics = find("/Script/Engine.Default__GameplayStatics")
        local pawn = statics:GetPlayerPawn(world, 0)
        local day = find(crossroads .. ":PersistentLevel.DayCycle_SunControler_BP_C_1")
        assert(valid(pawn) and pawn:IsLocallyControlled() and same(day:GetWorld(), world))
        if action == "read" then return rain_state(day) end
        if action == "siren" and guest then return tostring(heard or 0) end
        if action == "particles" and guest then
            local count = 0
            for _, particle in ipairs(FindAllOf("NiagaraComponent") or {}) do
                if valid(particle) and valid(particle.Asset) and particle.Asset:GetFullName() == rain_asset
                    and same(particle:GetAttachParent(), pawn.RootComponent) and particle:IsActive() then count = count + 1 end
            end
            return tostring(count)
        end
        if action == "localrain" and guest then
            assert(day.UpdateInRealTime == false)
            return stock_rain(world, statics, pawn, day, .85)
        end
        assert(not guest and valid(world.NetDriver.ClientConnections[1]))
        day.UpdateInRealTime = false
        if action == "dry" then return stock_rain(world, statics, pawn, day, 0) end
        assert(action == "wet")
        local report = stock_rain(world, statics, pawn, day, .55)
        for _, horn in ipairs(FindAllOf("AlarmSiren_BP_C") or {}) do
            if same(horn:GetWorld(), world) and horn:HasAuthority() and not horn:IsActorBeingDestroyed() then
                horn:StormSiren(find(cue_path), 15)
                return report
            end
        end
        error("The Crossroads stock siren is unavailable")
    end)
    local reply = assert(io.open(bridge .. "/weather-travel-reply.tsv", "wb"))
    reply:write(id, "\t", ok and "ok" or "failed", "\t", tostring(result), "\n")
    reply:close()
end)
