local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-storm-"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"
local classes = "/Game/Classes/Weather/Storms/"
local lightning = classes .. "LightningStorm_BP.LightningStorm_BP_C"
local fog = classes .. "TheFog_BP.TheFog_BP_C"
local pestilence = classes .. "PestilenceStorm_BP.PestilenceStorm_BP_C"
local data = "/Game/Classes/Weather/Data/"
local day_path = map .. ":PersistentLevel.DayCycle_SunControler_BP_C_0"
local siren_class = "/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C"
local wind = "Wind_Strong_01.Wind_Strong_01"
local weather_particle = "NI_LightningStorm.NI_LightningStorm"
local field_class = "Storm_7_1FF2FAC343119542F0A40FAB3A93D49F"
local field_curve = "Curve_4_01ED66844315D5CE3688EEA5ABE10B40"
local field_time = "TimeZero_10_E5159B6049F6FE6711AEBFB70381881E"
local field_rate = "PlayRate_13_BE5598FD40BAEA467B961581B19E0DF6"
local field_active = "Activated_19_0B381CD94413F01EAE590297A57BD87B"
local last, armed, installed, conflict

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function path(object) return valid(object) and object:GetFullName():match(" (%S+)$") or "-" end
local function find(name)
    local object = StaticFindObject(name)
    if not valid(object) then LoadAsset(name); object = StaticFindObject(name) end
    assert(valid(object), name)
    return object
end

local function current_world()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if valid(world) and world:GetFullName() == "World " .. map and valid(world.NetDriver) then return world end
end

local function in_world(actor)
    return armed and valid(actor) and same(actor:GetWorld(), current_world())
end

local function install_hooks()
    if installed then return end
    find(siren_class)
    RegisterHook(siren_class .. ":StormSiren", function(context, cue)
        if not in_world(context:get()) then return end
        local sound = path(cue:get())
        if sound:find("StormSiren_02_Cue", 1, true) then armed.lightning_warning = armed.lightning_warning + 1 end
        if sound:find("StormSiren_03_Cue", 1, true) then armed.fog_warning = armed.fog_warning + 1 end
    end)
    RegisterBeginPlayPostHook(function(context)
        local actor = context:get()
        if not in_world(actor) then return end
        local identity = path(actor:GetClass())
        if identity == lightning then armed.lightning_spawns = armed.lightning_spawns + 1 end
        if identity == fog then armed.fog_spawns = armed.fog_spawns + 1 end
        if identity == pestilence then armed.pestilence_spawns = armed.pestilence_spawns + 1 end
    end)
    RegisterHook("/Script/Niagara.NiagaraFunctionLibrary:SpawnSystemAtLocation", function(_, context, asset)
        if in_world(context:get()) and path(asset:get()):find(weather_particle, 1, true) then
            armed.lightning_particles = armed.lightning_particles + 1
        end
    end)
    RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAttached", function(_, sound, component)
        if not armed then return end
        local target = component:get()
        if valid(target) and in_world(target:GetOwner()) and path(sound:get()):find(wind, 1, true) then
            armed.lightning_audio = armed.lightning_audio + 1
        end
    end)
    installed = true
end

local function current()
    local world = assert(current_world(), "the exact private Observatory world is unavailable")
    local statics = find("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world))
    local day = StaticFindObject(day_path)
    assert(valid(day) and same(day:GetWorld(), world))
    local instance = day["Game Instance"]
    assert(valid(instance) and valid(instance.SaveSlot_Ref))
    return world, statics, pawn, day, instance
end

local function stock_active_track(save, variant, operation)
    local authored = find(data .. variant .. "." .. variant)
    local curve, storm_class = authored.Curve, authored.Storm
    assert(valid(curve) and valid(storm_class), "unqualified stock storm data")
    local alpha
    for quarter = 1, 64 do
        local time = quarter / 4
        if curve:GetVectorValue(time).Z > .5 and curve:GetVectorValue(time + .25).Z > .5 then
            alpha = time
            break
        end
    end
    assert(alpha, "the stock storm curve has no continuously active sample")
    local maths = find("/Script/Engine.Default__KismetMathLibrary")
    local track = save.StormTrack
    local old_class, old_curve, old_rate, old_active = track[field_class], track[field_curve], track[field_rate], track[field_active]
    local old_time = maths:Add_TimespanTimespan(track[field_time], maths:FromHours(0))
    track[field_class], track[field_curve], track[field_rate], track[field_active] = storm_class, curve, 1, false
    track[field_time] = maths:Subtract_TimespanTimespan(save.GameTime, maths:FromHours(alpha))
    assert(same(track[field_class], storm_class) and same(track[field_curve], curve)
        and track[field_rate] == 1 and track[field_active] == false)
    local ok, report = pcall(operation, track, storm_class)
    track[field_class], track[field_curve], track[field_rate] = old_class, old_curve, old_rate
    track[field_time], track[field_active] = old_time, old_active
    assert(ok, report)
    return report
end

local function start_host(world, statics, pawn, day, instance, action)
    assert(not valid(world.NetDriver.ServerConnection) and day:HasAuthority() and pawn:HasAuthority())
    if not valid(statics:GetActorOfClass(world, find(siren_class))) then
        local transform = {Translation=pawn:K2_GetActorLocation(), Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
        local horn = statics:BeginDeferredActorSpawnFromClass(pawn, find(siren_class), transform, 1, nil, 1)
        assert(valid(horn) and same(statics:FinishSpawningActor(horn, transform, 1), horn))
    end
    local previous = day.LocalStorm
    if valid(previous) then previous:K2_DestroyActor(); day.LocalStorm = nil end
    -- Keep this private fixture on the stock atmospheric-only branch in Observatory.
    day.UpdateInRealTime, day.StormLevel, day.Storming = false, 1, false
    local variant = action == "start" and "LightningStorm_Data" or "Pestilence_Data"
    return stock_active_track(instance.SaveSlot_Ref, variant, function(_, expected)
        day:UpdateStormTrack()
        local active = day.LocalStorm
        assert(valid(active) and same(active:GetClass(), expected) and same(active.DayControler_Ref, day)
            and active.StormLevel == 1 and active.FirstActivation and day.Storming, "stock storm activation failed")
        return path(active:GetClass()) .. ";" .. tostring(active:GetAddress())
    end)
end

local function read_conflict(world, day, instance)
    assert(valid(world.NetDriver.ServerConnection) and type(SilverfishReadNativeGuestStormGuard) == "function")
    return stock_active_track(instance.SaveSlot_Ref, "TheFog_Data", function(track, expected)
        local prior_world, prior_count = SilverfishReadNativeGuestStormGuard()
        local prior, ongoing = day.LocalStorm, instance.OngoingStorm
        local prior_address = valid(prior) and prior:GetAddress() or 0
        day:UpdateStormTrack()
        local guard_world, guard_count = SilverfishReadNativeGuestStormGuard()
        local active = day.LocalStorm
        assert(guard_world == world:GetAddress() and type(guard_count) == "number"
            and guard_count == (prior_world == guard_world and prior_count or 0) + 1,
            "the exact guest world did not deny the stock storm scheduler")
        assert((valid(active) and active:GetAddress() or 0) == prior_address and instance.OngoingStorm == ongoing
            and same(track[field_class], expected) and track[field_active] == false,
            "denied weather scheduling changed the guest's storm track, actor or danger state")
        return ("%s;%s;%s;%s"):format(path(expected), guard_count, prior_address, tostring(ongoing))
    end)
end

local function command(action)
    if action == "arm" then
        install_hooks()
        armed = {lightning_warning=0, fog_warning=0, lightning_spawns=0, fog_spawns=0,
            pestilence_spawns=0, lightning_particles=0, lightning_audio=0}
        return "armed"
    end
    assert(armed, "the private weather witness was not armed")
    if action == "awaitconflict" then
        if not conflict then return "pending" end
        assert(conflict.ok, conflict.report)
        return conflict.report
    end
    if action == "queueconflict" then conflict = nil; armed.queue_conflict = true return "queued" end
    local world, statics, pawn, day, instance = current()
    if action == "conflict" then return read_conflict(world, day, instance) end
    if action == "start" or action == "fog" then return start_host(world, statics, pawn, day, instance, action) end
    if action == "clear" then
        local active = day.LocalStorm
        assert(not valid(world.NetDriver.ServerConnection) and day:HasAuthority() and valid(active)
            and path(active:GetClass()) == pestilence)
        active:Start_Stop(false)
        assert(active.Fading and valid(active), "the stock storm did not begin fading")
        return "fading"
    end
    assert(action == "read", action)
    local active = day.LocalStorm
    return table.concat({path(valid(active) and active:GetClass()), valid(active) and active:GetAddress() or 0,
        armed.lightning_warning, armed.fog_warning, armed.lightning_spawns, armed.fog_spawns, armed.pestilence_spawns,
        armed.lightning_particles, armed.lightning_audio}, ";")
end

LoopInGameThreadWithDelay(45, function()
    if armed and armed.queue_conflict then
        local world = current_world()
        if valid(world) and valid(world.NetDriver.ServerConnection) then
            local ready, exact, _, _, day, instance = pcall(current)
            if ready then
                armed.queue_conflict = nil
                local ok, report = pcall(read_conflict, exact, day, instance)
                conflict = {ok=ok, report=report}
            end
        end
    end
    local file = io.open(prefix .. "command.tsv", "rb")
    if not file then return end
    local request = file:read(128)
    file:close()
    if not request or request == last then return end
    last = request
    local ticket, action = request:match("^([a-f0-9]+)\t(%a+)$")
    if not ticket or #ticket ~= 32 then return end
    local ok, result = pcall(command, action)
    local reply = assert(io.open(prefix .. "reply.tsv", "wb"))
    reply:write(ticket, ok and "\tok\t" or "\tfailed\t", tostring(result), "\n")
    reply:close()
end)
