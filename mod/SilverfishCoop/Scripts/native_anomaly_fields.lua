local fields = {}

local classes = {
    starburst = "/Game/Classes/Anomalies/Starburst_BP.Starburst_BP_C",
    burner = "/Game/Classes/Anomalies/BurnerTrap_BP.BurnerTrap_BP_C",
}
local kinds = {}
for kind, path in pairs(classes) do kinds["BlueprintGeneratedClass " .. path] = kind end
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local pending, active = {}, {}
local world

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function in_world(actor, current)
    return valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), current)
end

local function candidate(context, role)
    local actor = context:get()
    if not valid(actor) or actor:IsActorBeingDestroyed() then return end
    local kind = kinds[actor:GetClass():GetFullName()]
    if not kind then return end
    if role == "host" and actor:HasAuthority() then return actor, kind end
    if role == "client" and actor:GetLocalRole() == 1 then
        local current = actor:GetWorld()
        local driver = valid(current) and current.NetDriver
        local server = valid(driver) and driver.ServerConnection
        if valid(server) and same(driver.World, current) and same(server.Driver, driver) then return actor, kind end
    end
end

function fields.start(role)
    if type(SilverfishSetNativeAnomalyGuest) ~= "function" or type(RegisterBeginPlayPreHook) ~= "function"
        or type(RegisterBeginPlayPostHook) ~= "function" then return end
    RegisterBeginPlayPreHook(function(context)
        if role() ~= "client" then return end
        local actor = candidate(context, "client")
        if actor then actor:SetActorEnableCollision(false) end
    end)
    RegisterBeginPlayPostHook(function(context)
        local seat = role()
        if seat ~= "host" and seat ~= "client" then return end
        local actor, kind = candidate(context, seat)
        if not actor then return end
        if seat == "client" then actor:SetActorEnableCollision(false) end
        pending[actor:GetAddress()] = {actor = actor, kind = kind}
    end)
end

function fields.guest(enabled)
    if type(SilverfishSetNativeAnomalyGuest) == "function" then SilverfishSetNativeAnomalyGuest(enabled) end
end

function fields.command(args, state, phase)
    if args[2] ~= "native-anomaly-field-fixture" then return end
    local path = classes[args[3]]
    if #args ~= 3 or not path or state.map ~= crossroads or not valid(state.world) or not headless
        or (phase ~= "connected" and (phase ~= "hosting" or not host_fixture)) then
        error("The stock trap probe requires a muted Crossroads session", 0)
    end
    local class = StaticFindObject("/Game/Classes/Actors/AnomalyField_BP.AnomalyField_BP_C")
    local kind = StaticFindObject(path)
    if not valid(kind) then LoadAsset(path); kind = StaticFindObject(path) end
    local anchor = StaticFindObject(crossroads .. ".CrossRoads_P:PersistentLevel.AnomalyField_BP_C_6")
    if not valid(class) or not valid(kind) or not in_world(anchor, state.world) or not same(anchor:GetClass(), class) then
        error("The Crossroads field template is unavailable", 0)
    end
    local field = state.world:SpawnActor(class, anchor:K2_GetActorLocation(), {Pitch = 0, Yaw = 0, Roll = 0})
    if not in_world(field, state.world) or field.ForceSpawn then error("The stock probe field did not initialize", 0) end
    field.Anomalies = {kind}
    field.Min_Spawn, field.Max_Spawn, field.SpawnRadius = 1, 1, 500
    if phase == "connected" then
        -- Exercise the stock Blueprint call through the same ForceSpawn branch used by placed fields.
        field.ForceSpawn = true
        field:ReceiveBeginPlay()
    else
        field:SpawnField()
    end
    field:K2_DestroyActor()
    return "Stock anomaly field invoked"
end

function fields.rows(state, phase, row)
    if phase ~= "hosting" and phase ~= "connected" or not valid(state.world) then return {} end
    if not same(world, state.world) then active, world = {}, state.world end
    for address, entry in pairs(pending) do
        local actor = entry.actor
        if in_world(actor, world) then
            if phase == "connected" then
                if actor:GetActorEnableCollision() == false then active[address] = entry end
            elseif actor:ActorHasTag(FName("Anomaly")) then
                -- SpawnField applies its stock tag after finishing the spawned actor's BeginPlay.
                actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 5)
                actor:SetReplicates(true)
                actor:SetReplicateMovement(true)
                if actor.bReplicates and actor.bReplicateMovement then
                    actor:ForceNetUpdate()
                    active[address] = entry
                end
            end
        end
        pending[address] = nil
    end
    local rows, live = {}, 0
    for address, entry in pairs(active) do
        local actor = entry.actor
        if valid(actor) and not actor:IsActorBeingDestroyed() then
            live = live + 1
            local position = actor:K2_GetActorLocation()
            rows[#rows + 1] = row("native-anomaly-position", entry.kind, position.X, position.Y, position.Z,
                actor:GetActorEnableCollision() and 1 or 0)
        else
            active[address] = nil
        end
    end
    local skipped = phase == "connected" and type(SilverfishReadNativeAnomalySkips) == "function"
        and SilverfishReadNativeAnomalySkips(world:GetAddress()) or 0
    rows[#rows + 1] = row("native-anomaly-field", live, skipped)
    return rows
end

return fields
