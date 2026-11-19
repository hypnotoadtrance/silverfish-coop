local hazard = {}
local guest_save = require("guest_save")
local native_death = require("native_death")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map = "/Game/Classes/Maps/PlaytestMap_01"
local tank_path = "PoisonGasTank_01_BP_C " .. map .. ".PlaytestMap_01:PersistentLevel.PoisonGasTank_01_BP_C_1"
local gas_class = "/Game/Classes/Anomalies/PoisonGass_Sphere_BP.PoisonGass_Sphere_BP_C"
local maximum_seconds = 30
local clear_radius = 400
local stop_delay = 2.25
local run

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < 1000000000
end

local function health(pawn)
    local save = valid(pawn) and pawn.Save or nil
    local amount = valid(save) and save.PlayerHealth or nil
    return finite(amount) and amount or nil
end

local function participants(state, phase, guest_name)
    if phase ~= "hosting" or state.map ~= map or state.clients ~= 1 or #state.pawns ~= 2
        or not state.local_pawn or not valid(state.world) then return end
    local host = state.local_pawn.pawn
    for _, details in ipairs(state.pawns) do
        local guest = details.pawn
        if details.kind == "guest" and details.name == guest_name and not same(host, guest)
            and guest_save.matches(guest) and type(native_death.has_pair) == "function"
            and native_death.has_pair(host, guest) then
            return host, guest
        end
    end
end

local function live_source(record)
    return valid(record.tank) and record.tank:GetFullName() == tank_path and valid(record.gas)
        and valid(record.sphere) and not record.tank:IsActorBeingDestroyed()
        and not record.gas:IsActorBeingDestroyed() and same(record.tank:GetWorld(), record.world)
        and same(record.gas:GetWorld(), record.world) and same(record.gas.Sphere, record.sphere)
        and same(record.gas:GetParentComponent(), record.tank.PoisonGass_Sphere_BP)
        and same(record.tank.PoisonGass_Sphere_BP.ChildActor, record.gas)
end

local function find_source(world)
    local tank
    for _, actor in ipairs(FindAllOf("PoisonGasTank_01_BP_C") or {}) do
        if valid(actor) and actor:GetFullName() == tank_path and actor:HasAuthority()
            and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            if tank then error("The staged poison gas tank is ambiguous", 0) end
            tank = actor
        end
    end
    local attachment = tank and tank.PoisonGass_Sphere_BP
    local gas = valid(attachment) and attachment.ChildActor or nil
    local class = valid(gas) and gas:GetClass() or nil
    if not valid(class) or class:GetFullName():match("^%S+ (%S+)$") ~= gas_class or not gas:HasAuthority() then
        error("The exact stock gas child is unavailable", 0)
    end
    local record = { world = world, tank = tank, gas = gas, sphere = gas.Sphere, gas_name = gas:GetFullName() }
    if not live_source(record) or gas.Powered ~= false or gas.GasActive ~= false then
        error("The authored poison gas sphere needs to be live and inactive", 0)
    end
    return record
end

local function distance_squared(first, second)
    local x, y, z = first.X - second.X, first.Y - second.Y, first.Z - second.Z
    return x * x + y * y + z * z
end

local function host_is_clear(record, host)
    local radius = record.sphere:GetScaledSphereRadius()
    if not finite(radius) or radius < 500 or radius > 700 then return false end
    local center = record.sphere:K2_GetComponentLocation()
    return distance_squared(host:K2_GetActorLocation(), center) > (radius + clear_radius) ^ 2
end

local function overlapping_characters(record)
    local actors = {}
    record.sphere:GetOverlappingActors(actors, record.character_class)
    return actors
end

local function isolated(record, host, guest)
    if not host_is_clear(record, host) or not record.sphere:IsOverlappingActor(guest) then return false end
    local actors = overlapping_characters(record)
    if #actors ~= 1 then return false end
    local entry = actors[1]
    local checked, direct = pcall(valid, entry)
    if checked and direct then return same(entry, guest) end
    -- The normal UE4SS reflected array path gives a RemoteUnrealParam.
    local read, actor = pcall(function() return entry:get() end)
    return read and same(actor, guest)
end

local function capture(record)
    record.current = health(record.guest) or record.current
    record.host_current = health(record.host) or record.host_current
end

local function release(record)
    if not live_source(record) then
        if not valid(record.gas) or record.gas:IsActorBeingDestroyed() then
            record.state = record.outcome
            return true
        end
        error("Could not verify the original poison gas sphere for safe cleanup", 0)
    end
    local sphere, gas = record.sphere, record.gas
    if sphere:GetCollisionEnabled() ~= 0 then sphere:SetCollisionEnabled(0) end
    if sphere:GetCollisionEnabled() ~= 0 then
        error("Could not disable poison gas collisions before teardown", 0)
    end
    -- Power-off updates GasActive after a native delay. Disable overlap damage
    -- immediately and only restore collision after the stock timer goes quiet.
    if gas.Powered ~= false then gas:Send_PowerSignal(false, record.tank) end
    if gas.Powered ~= false then error("Could not clear the stock poison gas power signal", 0) end
    if #overlapping_characters(record) ~= 0 then
        error("Stock poison gas overlaps did not clear before teardown", 0)
    end
    if gas.GasActive == false then
        local now = record.clock:GetRealTimeSeconds(record.world)
        if not finite(now) then error("Could not verify poison gas cleanup time", 0) end
        record.quiet_since = record.quiet_since or now
        -- A Damage tick may already be waiting on its 1.8–2.0 s latent delay.
        if now - record.quiet_since < stop_delay then return true end
        sphere:SetCollisionEnabled(record.collision)
        if sphere:GetCollisionEnabled() ~= record.collision then
            error("Could not restore the stock poison gas sphere collision", 0)
        end
        record.state = record.outcome
    else
        record.quiet_since = nil
    end
    return true
end

local function stop(outcome, detail)
    if not run or not (run.state == "active" or run.state == "stopping") then return true end
    if run.state == "active" then
        capture(run)
        run.state, run.outcome, run.detail = "stopping", outcome, detail
    end
    return release(run)
end

function hazard.clear(reason)
    return stop("stopped", reason or "requested stop")
end

local function position(state, phase, guest_name)
    if run and (run.state == "active" or run.state == "stopping") then
        error("Stop the stock poison gas test before positioning the guest", 0)
    end
    local host, guest = participants(state, phase, guest_name)
    if not host then error("The exact guest needs an active paired native death scope", 0) end
    local record = find_source(state.world)
    if not host_is_clear(record, host) or guest.Dead ~= false or not ((health(guest) or 0) > 0) then
        error("The host must stay outside the gas margin and the guest must be alive", 0)
    end
    local center = record.sphere:K2_GetComponentLocation()
    local original = guest:K2_GetActorLocation()
    local rotation = guest:K2_GetActorRotation()
    for _, offset in ipairs({ { 180, 0 }, { -180, 0 }, { 0, 180 }, { 0, -180 } }) do
        local point = { X = center.X + offset[1], Y = center.Y + offset[2], Z = center.Z + 105 }
        if guest:K2_TeleportTo(point, rotation) and record.sphere:IsOverlappingActor(guest) then
            guest:ForceNetUpdate()
            return "bound guest positioned in the inactive stock gas sphere"
        end
    end
    if not guest:K2_TeleportTo(original, rotation) then error("Could not restore the guest after gas positioning", 0) end
    guest:ForceNetUpdate()
    error("No unobstructed guest position overlapped the stock gas sphere", 0)
end

local function start(state, phase, guest_name)
    if run and (run.state == "active" or run.state == "stopping") then
        error("A stock poison gas test is already active or clearing", 0)
    end
    local host, guest = participants(state, phase, guest_name)
    if not host then error("The exact guest needs an active paired native death scope", 0) end
    local record = find_source(state.world)
    record.character_class = StaticFindObject("/Script/Engine.Character")
    record.clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(record.character_class) or not valid(record.clock) then
        error("Native overlap or elapsed time support is unavailable", 0)
    end
    if not isolated(record, host, guest) then
        error("Only the guest may overlap the stock gas sphere and the host must be outside its margin", 0)
    end
    local before, host_before = health(guest), health(host)
    local now = record.clock:GetRealTimeSeconds(state.world)
    local collision = math.tointeger(record.sphere:GetCollisionEnabled())
    if not before or before <= 0 or guest.Dead ~= false or not host_before or host_before <= 0
        or host.Dead ~= false or not finite(now) or not collision or collision < 1 or collision > 6 then
        error("Living pawns and the stock sphere collision are required", 0)
    end
    record.host, record.guest, record.guest_name = host, guest, guest_name
    record.before, record.current = before, before
    record.host_before, record.host_current = host_before, host_before
    record.collision, record.expires, record.state = collision, now + maximum_seconds, "active"
    run = record
    local powered = pcall(function() record.gas:Send_PowerSignal(true, record.tank) end)
    if not powered or record.gas.Powered ~= true then
        stop("failed", "stock power-on signal failed")
        error("Could not start the stock poison gas sphere", 0)
    end
    return "stock poison gas hazard powered"
end

function hazard.sync(state, phase)
    if not run then return false end
    if run.state == "stopping" then release(run); return false end
    if run.state ~= "active" then return false end
    if not live_source(run) then return stop("stopped", "the stock gas source changed") end
    local host, guest = participants(state, phase, run.guest_name)
    if not same(host, run.host) or not same(guest, run.guest) then
        return stop("stopped", "the paired co-op scope changed")
    end
    capture(run)
    if run.host_current ~= run.host_before or host.Dead ~= false then
        return stop("failed", "host health or stock death state changed")
    end
    if guest.Dead ~= false then return stop("failed", "the guest entered stock death") end
    if run.current <= 0 then return stop("downed", "stock poison damaged the exact guest") end
    if not isolated(run, host, guest) then return stop("stopped", "overlap isolation changed") end
    local now = run.clock:GetRealTimeSeconds(state.world)
    if not finite(now) or now >= run.expires then return stop("timeout", "stock gas test time elapsed") end
    if run.gas.Powered ~= true then return stop("stopped", "stock gas power ended") end
    return false
end

function hazard.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-hazard-position" and action ~= "native-hazard-start" and action ~= "native-hazard-clear" then return end
    if not enabled then error("The staged native hazard is unavailable", 0) end
    if action == "native-hazard-position" and #fields == 3 then return position(state, phase, fields[3]) end
    if action == "native-hazard-start" and #fields == 3 then return start(state, phase, fields[3]) end
    if action == "native-hazard-clear" and #fields == 2 then
        hazard.clear()
        return "stock poison gas test stopped"
    end
    error("Invalid native hazard command arguments", 0)
end

function hazard.rows(state, phase, status_row)
    if not run or phase ~= "hosting" or not same(state.world, run.world) then return {} end
    return { status_row("native-hazard", run.state, tank_path, run.gas_name, run.guest_name,
        run.before, run.current, run.host_before, run.host_current, run.detail or "") }
end

return hazard
