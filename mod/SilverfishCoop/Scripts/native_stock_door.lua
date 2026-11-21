local doors = {}
local guest_save = require("guest_save")

local base_path = "/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local guest_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local active_world, active_phase, scanned_at, actors, door_parts
local sampler
local trace_report
local tool_report

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end
local function available()
    return type(SilverfishStockDoorGuardAvailable) == "function" and SilverfishStockDoorGuardAvailable() == true
end
local function finite(value, limit)
    return type(value) == "number" and value == value and math.abs(value) <= limit
end

local function identity(actor, world)
    local base = StaticFindObject(base_path)
    if not valid(base) or not valid(actor) or not actor:IsA(base)
        or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), world)
        or not same(actor:GetOuter(), world.PersistentLevel) then return end
    local name = actor:GetFullName()
    if #name <= 384 and name:match("^[%w_]+ /Game/[%w_/]+%.[%w_]+:PersistentLevel%.[%w_]+$") then
        return name
    end
end

local function components(actor)
    local root, arrow, panel, knob = actor.RootComponent, actor.Arrow, actor.DoorMesh, actor.Box_Knob
    if not exact(arrow, "/Script/Engine.ArrowComponent") or not exact(panel, "/Script/Engine.StaticMeshComponent")
        or not exact(knob, "/Script/Engine.BoxComponent") or not same(arrow:GetAttachParent(), root)
        or not same(panel:GetAttachParent(), arrow) or not same(knob:GetAttachParent(), panel)
        or not same(arrow:GetOwner(), actor) or not same(panel:GetOwner(), actor)
        or not same(knob:GetOwner(), actor) then return end
    return arrow, panel, knob
end

local function census(state, phase)
    if (phase ~= "hosting" and phase ~= "connected") or not state.listening or not valid(state.world)
        or state.server ~= (phase == "connected") or not available() then
        active_world, active_phase, scanned_at, actors, door_parts, sampler = nil, nil, nil, nil, nil, nil
        return {}
    end
    local now = os.time()
    if not same(active_world, state.world) or active_phase ~= phase or scanned_at ~= now then
        active_world, active_phase, scanned_at, actors, door_parts = state.world, phase, now, {}, {}
        local base, statics = StaticFindObject(base_path), StaticFindObject(statics_path)
        if not valid(base) or not valid(statics) then return actors end
        local found = {}
        statics:GetAllActorsOfClass(state.world, base, found)
        for _, actor in ipairs(found.OutActors or found) do
            if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
            local name = identity(actor, state.world)
            if name and (phase ~= "hosting" or actor:HasAuthority()) then
                local arrow, panel, knob = components(actor)
                if arrow then
                    actors[name] = actors[name] == nil and actor or false
                    door_parts[name] = actors[name] and {arrow, panel, knob} or nil
                end
            end
        end
    end
    return actors
end

local function pose(actor, arrow)
    local rot, location = arrow.RelativeRotation, arrow.RelativeLocation
    if type(actor.Locked) ~= "boolean" or type(actor.LockBroken) ~= "boolean" or type(actor.Open) ~= "boolean"
        or not rot or not finite(rot.Pitch, 360) or not finite(rot.Yaw, 360) or not finite(rot.Roll, 360)
        or not location or not finite(location.X, 10000) or not finite(location.Y, 10000) or not finite(location.Z, 10000) then return end
    return {actor.Locked, actor.LockBroken, actor.Open, rot.Pitch, rot.Yaw, rot.Roll, location.X, location.Y, location.Z}
end

local function matches(a, b)
    if not a or not b then return false end
    for index = 1, 3 do if a[index] ~= b[index] then return false end end
    for index = 4, 6 do
        if math.abs((a[index] - b[index] + 180) % 360 - 180) > .05 then return false end
    end
    for index = 7, 9 do if math.abs(a[index] - b[index]) > .05 then return false end end
    return true
end

local function apply(actor, arrow, expected)
    arrow:K2_SetRelativeLocationAndRotation({X = expected[7], Y = expected[8], Z = expected[9]},
        {Pitch = expected[4], Yaw = expected[5], Roll = expected[6]}, false, {}, true)
    actor.Locked, actor.LockBroken, actor.Open = expected[1], expected[2], expected[3]
end

local function project(fields, state, phase)
    if phase ~= "connected" or fields[3] ~= state.map or #fields < 4 or #fields > 27 then
        error("stock_door_projection_scope_unavailable", 0)
    end
    local known, updates = census(state, phase), {}
    for index = 4, #fields do
        local name, locked, broken, open, pitch, yaw, roll, x, y, z =
            fields[index]:match("^([^|]+)|([01])|([01])|([01])|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        local actor = known[name]
        local arrow = valid(actor) and components(actor)
        pitch, yaw, roll = tonumber(pitch), tonumber(yaw), tonumber(roll)
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local before = arrow and pose(actor, arrow)
        if not before or updates[name] or not finite(pitch, 360) or not finite(yaw, 360) or not finite(roll, 360)
            or not finite(x, 10000) or not finite(y, 10000) or not finite(z, 10000) then
            error("stock_door_projection_pose_unavailable", 0)
        end
        updates[name] = {actor = actor, arrow = arrow, before = before,
            expected = {locked == "1", broken == "1", open == "1", pitch, yaw, roll, x, y, z}}
    end
    local applied = pcall(function()
        for _, row in pairs(updates) do
            apply(row.actor, row.arrow, row.expected)
            if not matches(pose(row.actor, row.arrow), row.expected) then error("stock_door_projection_readback") end
        end
    end)
    if not applied then
        for _, row in pairs(updates) do
            pcall(apply, row.actor, row.arrow, row.before)
        end
        error("stock_door_projection_readback_unavailable", 0)
    end
    return "stock door flags and hinge transform synchronized"
end

local function sample_door(scan, name)
    local actor, parts = scan.actors[name], door_parts[name]
    if not valid(actor) or actor:IsActorBeingDestroyed() or not parts
        or not valid(parts[1]) or not valid(parts[2]) or not valid(parts[3]) then return end
    local arrow, panel, knob = table.unpack(parts)
    local value = pose(actor, arrow)
    if not value then return end
    local center, grip = panel:K2_GetComponentLocation(), knob:K2_GetComponentLocation()
    local collision = math.tointeger(panel:GetCollisionEnabled())
    if center and grip and collision and collision >= 0 and collision <= 5 then
        return scan.row("native-stock-door", scan.map, name,
            value[1] and 1 or 0, value[2] and 1 or 0, value[3] and 1 or 0,
            string.format("%.3f", value[4]), string.format("%.3f", value[5]), string.format("%.3f", value[6]),
            string.format("%.3f", value[7]), string.format("%.3f", value[8]), string.format("%.3f", value[9]),
            string.format("%.3f", center.X), string.format("%.3f", center.Y), string.format("%.3f", center.Z),
            string.format("%.3f", grip.X), string.format("%.3f", grip.Y), string.format("%.3f", grip.Z), collision)
    end
end

local function sample_slice(scan)
    local started = os.clock()
    for _ = 1, math.ceil(#scan.names / 12) do
        local name = scan.names[scan.next]
        scan.readings[name] = sample_door(scan, name)
        scan.next = scan.next % #scan.names + 1
        if os.clock() - started >= .002 then break end
    end
end

function doors.start()
    if type(SilverfishStockDoorGuardAvailable) ~= "function" then return end
    LoopInGameThreadWithDelay(16, function()
        local scan = sampler
        if scan and #scan.names > 12 and scan.actors == actors and same(scan.world, active_world)
            and not pcall(sample_slice, scan) then sampler = nil end
    end)
end

local function sample_command(scan, fields)
    if not fields then return end
    local action = fields[2]
    if action == "native-stock-door-project" then
        for index = 4, math.min(#fields, 27) do
            local name = fields[index]:match("^([^|]+)|")
            if name and scan.actors[name] then scan.readings[name] = sample_door(scan, name) end
        end
    elseif action and action:sub(1, 18) == "native-stock-door-" then
        local name = fields[action == "native-stock-door-fixture" and 4 or 3]
        if name and scan.actors[name] then scan.readings[name] = sample_door(scan, name) end
    end
end

function doors.rows(state, phase, status_row, fields)
    local known = census(state, phase)
    local result = {}
    if not active_world then return result end
    if not sampler or sampler.actors ~= known or sampler.phase ~= phase then
        local previous = sampler
        sampler = {actors = known, world = state.world, phase = phase, map = state.map, row = status_row,
            names = {}, readings = {}, next = 1}
        for name, actor in pairs(known) do
            if #sampler.names >= 240 then break end
            if actor then
                sampler.names[#sampler.names + 1] = name
                if previous and previous.phase == phase and same(previous.world, state.world)
                    and same(previous.actors[name], actor) then
                    sampler.readings[name] = previous.readings[name]
                end
            end
        end
    end
    local scan = sampler
    if #scan.names <= 12 then
        for _, name in ipairs(scan.names) do scan.readings[name] = sample_door(scan, name) end
    else
        -- Read a command's exact doors before acknowledging it to the coordinator.
        sample_command(scan, fields)
    end
    for _, name in ipairs(scan.names) do
        if scan.readings[name] then result[#result + 1] = scan.readings[name] end
    end
    if trace_report and active_world and same(trace_report.world, state.world) and trace_report.phase == phase then
        result[#result + 1] = status_row("native-stock-door-trace", state.map, trace_report.actor,
            trace_report.query, trace_report.panel and 1 or 0)
    end
    if tool_report and phase == "hosting" and same(tool_report.world, state.world) then
        result[#result + 1] = status_row("native-stock-door-pry", state.map, tool_report.actor, tool_report.pawn,
            tool_report.guid, tool_report.kind, tool_report.before, tool_report.after)
    end
    return result
end

function doors.focused_target(pawn)
    if not available() or not valid(pawn) or pawn:HasAuthority() or pawn.CanInteract ~= true then return end
    local knob = pawn.InteractionTarget_Component
    local actor = valid(knob) and knob:GetOwner()
    local world = pawn:GetWorld()
    local name = valid(world) and identity(actor, world)
    if name and components(actor) and same(knob, actor.Box_Knob) then return "stock-door:" .. name end
end

function doors.pry_target(pawn, kind)
    if not available() or not valid(pawn) or (kind == "crowbar" and pawn.CanInteract ~= true) then return end
    local actor, world = pawn.ActorBeingLookedAt, pawn:GetWorld()
    local name = valid(world) and identity(actor, world)
    if name and actor.Can_PryOpen == true and actor.Locked == true and actor.LockBroken == false and components(actor) then
        return name
    end
end

function doors.input_fixture(state, phase, target, pawn, dispatch)
    local actor = census(state, phase)[target]
    if not guest_fixture or phase ~= "connected" or not valid(actor) or not components(actor) then
        error("stock_door_guest_input_fixture_unavailable", 0)
    end
    require("native_loot_crate_input").stock_focus(pawn, actor.Box_Knob, function()
        if doors.focused_target(pawn) ~= "stock-door:" .. target then error("stock_door_fixture_missing_focus", 0) end
        dispatch()
    end)
    return "stock door guest input dispatched"
end

local function fixture(fields, state, phase)
    local mode = fields[3]
    local actor = census(state, phase)[fields[4]]
    if #fields ~= 4 or not valid(actor) or not components(actor) then error("stock_door_fixture_scope_unavailable", 0) end
    if mode == "pry" and phase == "hosting" and host_fixture and actor.Locked == true then
        actor:PriedOpen_Lock()
    elseif mode == "guard" and phase == "connected" and guest_fixture and actor.Locked == true then
        actor:PriedOpen_Lock()
    else
        error("stock_door_fixture_scope_unavailable", 0)
    end
    return "stock hinged door fixture dispatched"
end

local function interact(fields, state, phase)
    local actor = census(state, phase)[fields[3]]
    if #fields ~= 4 or phase ~= "hosting" or not valid(actor) or not components(actor)
        or actor.Locked ~= false then error("stock_door_guest_locked_or_unavailable", 0) end
    local qualified, pawn = pcall(guest_save.exact_item_context, state, phase, fields[4])
    if not qualified or not valid(pawn) or pawn.Dead ~= false then error("stock_door_guest_unavailable", 0) end
    local knob = actor.Box_Knob
    require("native_loot_crate_input").stock_focus(pawn, knob, function()
        if not same(pawn.ActorBeingLookedAt, actor) or actor.Locked ~= false then
            error("stock_door_guest_out_of_reach", 0)
        end
        actor:SendInteractionToActor(pawn, knob)
    end, "stock_door_guest_out_of_reach")
    return "stock hinged door interaction dispatched"
end

local function tool_pry(fields, state, phase)
    tool_report = nil
    local target, name, guid, kind = fields[3], fields[4], fields[5], fields[6]
    local actor = census(state, phase)[target]
    local knob = valid(actor) and select(3, components(actor))
    if #fields ~= 6 or phase ~= "hosting" or not knob or (kind ~= "blowtorch" and kind ~= "crowbar")
        or actor.Locked ~= true or actor.LockBroken ~= false or not guid:match("^[0-9A-F]+$") or #guid ~= 32
        or type(SilverfishBeginNativeGuestDoorPry) ~= "function" or type(SilverfishFinishNativeGuestDoorPry) ~= "function" then
        error("stock_door_pry_scope_unavailable", 0)
    end
    local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    local instance = state.world.OwningGameInstance
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not qualified or not valid(pawn) or pawn.Dead ~= false or not valid(save) or not valid(stock) or same(save, stock) then
        error("stock_door_pry_guest_unavailable", 0)
    end
    local tools = require("native_loot_crate_input")
    local scope = {pawn = pawn, crate = actor, save = save, stock = stock, box = knob}
    local actor_address = actor:GetAddress()
    local armed = false
    local function arm()
        if not SilverfishBeginNativeGuestDoorPry(actor_address, pawn:GetAddress(), save:GetAddress()) then
            error("stock_door_pry_busy", 0)
        end
        armed = true
    end
    local ok, before, after = pcall(function()
        if kind == "blowtorch" then return tools.fire_blowtorch(scope, guid, arm) end
        return tools.swing_crowbar(scope, actor.LockPick_Chance, guid, arm)
    end)
    local observed = armed and SilverfishFinishNativeGuestDoorPry(actor_address)
    if not ok then
        if armed then error("mutation_unverified_stock_door_pry:" .. tostring(before), 0) end
        error(before, 0)
    end
    if not observed and before == after and valid(actor) and actor.Locked == true then
        error("stock_door_pry_stock_action_busy", 0)
    end
    if not observed or (kind == "blowtorch" and after ~= before - 1) or not valid(actor) then
        error("mutation_unverified_stock_door_pry", 0)
    end
    tool_report = {world = state.world, actor = target, pawn = name, guid = guid, kind = kind, before = before, after = after}
    return "stock hinged door pry was invoked for the guest"
end

local function trace(fields, state, phase)
    local permitted = (phase == "hosting" and host_fixture) or (phase == "connected" and guest_fixture)
    local actor = permitted and census(state, phase)[fields[3]]
    local _, panel
    if valid(actor) then _, panel = components(actor) end
    local api = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local query = math.tointeger(fields[4])
    if #fields ~= 10 or not permitted or not valid(panel) or not valid(api) or (query ~= 0 and query ~= 2) then
        error("stock_door_trace_fixture_unavailable", 0)
    end
    local hinge = actor:K2_GetActorLocation()
    local function endpoint(index)
        local x, y, z = tonumber(fields[index]), tonumber(fields[index + 1]), tonumber(fields[index + 2])
        if not finite(x, 1e7) or not finite(y, 1e7) or not finite(z, 1e7)
            or (x - hinge.X)^2 + (y - hinge.Y)^2 + (z - hinge.Z)^2 > 300^2 then
            error("stock_door_trace_fixture_range", 0)
        end
        return {X = x, Y = y, Z = z}
    end
    local hit, color = {}, {R = 0, G = 0, B = 0, A = 1}
    local ignored = {}
    for _, details in ipairs(state.pawns) do
        if valid(details.pawn) then ignored[#ignored + 1] = details.pawn end
    end
    local found = api:LineTraceSingle(actor, endpoint(5), endpoint(8), query, true, ignored, 0, hit, false, color, color, 0)
    hit = hit.OutHit or hit
    local component = hit.Component and hit.Component:Get()
    trace_report = {world = state.world, phase = phase, actor = fields[3], query = query,
        panel = found == true and same(component, panel)}
    return "stock door collision trace completed"
end

function doors.command(fields, state, phase)
    if fields[2] == "native-stock-door-project" then return project(fields, state, phase) end
    if fields[2] == "native-stock-door-fixture" then return fixture(fields, state, phase) end
    if fields[2] == "native-stock-door-guest-interact" then return interact(fields, state, phase) end
    if fields[2] == "native-stock-door-guest-pry" then return tool_pry(fields, state, phase) end
    if fields[2] == "native-stock-door-trace" then return trace(fields, state, phase) end
end

return doors
