local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-lightning-"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local bolt_path = "/Game/Classes/Actors/LightningBolt_BP.LightningBolt_BP_C"
local explosion = "/Game/Classes/Libraries/Fuctions_Lib/FL_GameEffects.FL_GameEffects_C:SpawnExplosion"
local statics_path = "/Script/Engine.GameplayStatics"
local thunder = "/Game/Assets/Sounds/MetaSounds/MS_LightningStrike.MS_LightningStrike"
local strike = "/Game/Assets/PFX/NI_Lightning_Strike.NI_Lightning_Strike"
local blast = "/Game/Assets/PFX/FX_Blast_PulseGrenade_Converted.FX_Blast_PulseGrenade_Converted"
local fire = "Fire_12_2B415A724C533C075B6BB390B76BF247"
local electric = "Electric_16_8F945E8C491C5825EC19E3837E266D48"
local installed, sample, last

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function path(object) return valid(object) and object:GetFullName():match(" (%S+)$") end
local function find(name)
    local object = StaticFindObject(name)
    if not valid(object) then LoadAsset(name); object = StaticFindObject(name) end
    assert(valid(object), name)
    return object
end

local function observing(object)
    if not sample or not valid(object) then return false end
    local world = object:GetWorld()
    return valid(world) and world:GetAddress() == sample.world and world:GetFullName() == "World " .. crossroads
end

local function point(value)
    return ("%.7f,%.7f,%.7f"):format(value.X, value.Y, value.Z)
end

local function rotation(value)
    return ("%.7f,%.7f,%.7f"):format(value.Pitch, value.Yaw, value.Roll)
end

local function same_point(first, second)
    return math.abs(first.X - second.X) < .02 and math.abs(first.Y - second.Y) < .02
        and math.abs(first.Z - second.Z) < .02
end

local function stock_bolt(object)
    return valid(object) and path(object:GetClass()) == bolt_path
end

local function watch(callback)
    return function(...)
        if not sample then return end
        local ok, reason = pcall(callback, ...)
        if not ok then sample.error = tostring(reason) end
    end
end

local function install_hooks()
    if installed then return end
    find(bolt_path)
    find(explosion)
    RegisterBeginPlayPostHook(watch(function(context)
        local actor = context:get()
        if stock_bolt(actor) and observing(actor) then sample.stock_bolts = sample.stock_bolts + 1 end
    end))
    RegisterHook(explosion, watch(function(_, location, radius, maximum, particles, _, sound, _, _, origin)
        local source, cue = origin:get(), sound:get()
        if not observing(source) or not stock_bolt(source) or path(cue) ~= thunder then return end
        local impact, component, bolt_fx = location:get(), source.DirectionalLight, source.NI_Lightning_Strike
        assert(valid(component) and valid(bolt_fx) and path(bolt_fx.Asset) == strike
            and path(particles:get()) == blast and source:HasAuthority())
        assert(same_point(source["Impact Point"], impact)
            and same_point(bolt_fx:K2_GetComponentLocation(), impact), "lightning explosion lost its traced impact")
        local reach, peak = radius:get(), maximum:get()
        sample.explosions = sample.explosions + 1
        sample.explosion_position, sample.explosion_rotation = point(impact), rotation(component:K2_GetComponentRotation())
        sample.damage_schema = ("%s,%s,%s,%s"):format(reach[fire], reach[electric], peak[fire], peak[electric])
    end))
    RegisterHook("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage", watch(function(context, damage, _, origin)
        local victim, source = context:get(), origin:get()
        if observing(victim) and stock_bolt(source) then
            local amount = damage:get()
            if amount[fire] > 0 or amount[electric] > 0 then sample.damage = sample.damage + 1 end
        end
    end))
    RegisterHook(statics_path .. ":SpawnSoundAtLocation", watch(function(_, context, sound, location, _, volume)
        local source, cue = context:get(), sound:get()
        if observing(source) and path(cue) == thunder then
            sample.thunder = sample.thunder + 1
            sample.thunder_position = point(location:get())
            sample.thunder_volume = volume:get()
        end
    end))
    RegisterHook("/Script/Niagara.NiagaraFunctionLibrary:SpawnSystemAtLocation", watch(function(_, context, asset, location)
        if not observing(context:get()) then return end
        local identity = path(asset:get())
        if identity == strike then
            sample.strike_fx = sample.strike_fx + 1
            sample.strike_position = point(location:get())
        elseif identity == blast then
            sample.blast_fx = sample.blast_fx + 1
            sample.blast_position = point(location:get())
        end
    end))
    installed = true
end

local function authored_impact(pawn, peer)
    local center, other = pawn:K2_GetActorLocation(), peer:K2_GetActorLocation()
    local trace = find("/Script/Engine.Default__KismetSystemLibrary")
    local color = {R=0,G=0,B=0,A=0}
    for step = 0, 7 do
        local angle = step * math.pi / 4
        local x, y = center.X + math.cos(angle) * 1800, center.Y + math.sin(angle) * 1800
        local hit = {}
        if (x - other.X)^2 + (y - other.Y)^2 > 1000^2 and trace:LineTraceSingle(pawn,
            {X=x,Y=y,Z=center.Z+10000}, {X=x,Y=y,Z=center.Z-10000}, 2, false, {pawn, peer}, 0,
            hit, true, color, color, 0) then
            hit = hit.OutHit or hit
            local floor = hit.Component and hit.Component:Get()
            if valid(floor) and floor:GetCollisionObjectType() == 0 and hit.ImpactNormal.Z > .7 then
                return {X=x,Y=y,Z=hit.ImpactPoint.Z+80}, point(hit.ImpactPoint)
            end
        end
    end
    error("no qualified stock lightning impact near the private pair")
end

local function command(action)
    if action == "disarm" then sample = nil return "cleared" end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. crossroads and valid(world.NetDriver))
    local statics = find("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world))
    if action == "arm" then
        install_hooks()
        sample = {world=world:GetAddress(), stock_bolts=0, explosions=0, damage=0, thunder=0, strike_fx=0, blast_fx=0}
        return "armed"
    end
    assert(observing(pawn) and not sample.error, sample and sample.error)
    if action == "read" then
        return table.concat({sample.stock_bolts, sample.explosions, sample.damage, sample.thunder,
            sample.strike_fx, sample.blast_fx, sample.explosion_position or "-", sample.explosion_rotation or "-",
            sample.damage_schema or "-", sample.thunder_position or "-", sample.thunder_volume or "-",
            sample.strike_position or "-", sample.blast_position or "-"}, ";")
    end
    assert(action == "strike" and pawn:HasAuthority() and not valid(world.NetDriver.ServerConnection)
        and not sample.issued and world.NetDriver.ClientConnections:GetArrayNum() == 1)
    local connection = world.NetDriver.ClientConnections[1]
    local remote = connection.PlayerController.Pawn
    assert(valid(remote) and remote:HasAuthority() and not remote:IsLocallyControlled() and same(remote:GetWorld(), world))
    local location, expected = authored_impact(pawn, remote)
    local transform = {Translation=location, Rotation={X=0,Y=0,Z=0,W=1}, Scale3D={X=1,Y=1,Z=1}}
    -- Match the cooked Rain_BP deferred spawn and leave LightningBolt's BeginPlay untouched.
    local actor = statics:BeginDeferredActorSpawnFromClass(pawn, find(bolt_path), transform, 2, nil, 1)
    assert(stock_bolt(actor) and same(actor:GetWorld(), world))
    sample.issued = true
    local finished = statics:FinishSpawningActor(actor, transform, 1)
    assert(same(finished, actor), "the stock LightningBolt did not finish spawning")
    return expected
end

LoopInGameThreadWithDelay(40, function()
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
