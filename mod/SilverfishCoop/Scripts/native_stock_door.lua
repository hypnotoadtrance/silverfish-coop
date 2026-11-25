local doors = {}
local guest_save = require("guest_save")

local base_path = "/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local guest_fixture = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local active_world, active_phase, scanned_at, actors, door_parts
local sampler, pending_census, periodic_sampling
local trace_report
local tool_report
local motions = {}
local move_component = "/Script/Engine.KismetSystemLibrary:MoveComponentTo"
local clock, math_library
local motion_probe

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local stock_classes = {}
local function stock_class(path)
    if not valid(stock_classes[path]) then stock_classes[path] = StaticFindObject(path) end
    return stock_classes[path]
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), stock_class(path))
end
local function available()
    return type(SilverfishStockDoorGuardAvailable) == "function" and SilverfishStockDoorGuardAvailable() == true
end
local function finite(value, limit)
    return type(value) == "number" and value == value and math.abs(value) <= limit
end

local function identity(actor, world)
    local base = stock_class(base_path)
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

local function census_slice(scan, deadline)
    for _ = 1, 16 do
        local candidate = scan.candidates[scan.next]
        if not candidate then break end
        scan.next = scan.next + 1
        local actor = candidate
        if type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        local name = identity(actor, scan.world)
        if name and (scan.phase ~= "hosting" or actor:HasAuthority()) then
            local arrow, panel, knob = components(actor)
            if arrow then
                scan.actors[name] = scan.actors[name] == nil and actor or false
                scan.parts[name] = scan.actors[name] and {arrow, panel, knob} or nil
            end
        end
        if os.clock() >= deadline then break end
    end
    if not scan.candidates[scan.next] then
        actors, door_parts, pending_census = scan.actors, scan.parts, nil
    end
end

local function census(state, phase)
    if (phase ~= "hosting" and phase ~= "connected") or not state.listening or not valid(state.world)
        or state.server ~= (phase == "connected") or not available() then
        active_world, active_phase, scanned_at, actors, door_parts, sampler, pending_census = nil, nil, nil, nil, nil, nil, nil
        return {}
    end
    if not same(active_world, state.world) or active_phase ~= phase then
        active_world, active_phase, scanned_at = state.world, phase, nil
        actors, door_parts, sampler, pending_census = {}, {}, nil, nil
    end
    local now = os.time()
    if not pending_census and scanned_at ~= now then
        scanned_at = now
        local base, statics = stock_class(base_path), stock_class(statics_path)
        if not valid(base) or not valid(statics) then return actors end
        local found = {}
        statics:GetAllActorsOfClass(state.world, base, found)
        local candidates = found.OutActors or found
        local scan = {candidates = candidates, next = 1, world = state.world, phase = phase, actors = {}, parts = {}}
        pending_census = scan
        if not periodic_sampling or #candidates <= 12 then
            while pending_census do census_slice(scan, math.huge) end
        end
    end
    return actors
end

local function target_door(state, phase, name, known)
    known = known or census(state, phase)
    local actor = known[name]
    if actor ~= nil then
        if valid(actor) and not actor:IsActorBeingDestroyed() and same(actor:GetWorld(), state.world) then return actor end
        return
    end
    if not pending_census or type(name) ~= "string" or #name > 384 then return end
    -- Exact commands can still resolve a door while the background census is in progress.
    local path = name:match("^[%w_]+ (/Game/[%w_/]+%.[%w_]+:PersistentLevel%.[%w_]+)$")
    actor = path and StaticFindObject(path)
    if identity(actor, state.world) ~= name or (phase == "hosting" and not actor:HasAuthority()) then return end
    local arrow, panel, knob = components(actor)
    if not arrow then return end
    known[name], door_parts[name] = actor, {arrow, panel, knob}
    if sampler and sampler.actors == known and #sampler.names < 240 then
        sampler.names[#sampler.names + 1] = name
    end
    return actor
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

local function animate(name, motion, now)
    if not valid(motion.arrow) or not valid(motion.actor) or motion.actor:IsActorBeingDestroyed()
        or not same(motion.actor:GetWorld(), active_world) then
        motions[name] = nil
        return
    end
    local alpha = math.min(1, math.max(0, (now - motion.start) / motion.duration))
    if alpha < 1 then
        if motion.ease == 3 then
            alpha = alpha < .5 and 2 * alpha * alpha or 1 - 2 * (1 - alpha)^2
        elseif motion.ease == 2 then
            alpha = 1 - (1 - alpha)^2
        elseif motion.ease == 1 then
            alpha = alpha^2
        end
    end
    local from, goal = motion.from, motion.goal
    local angles = math_library:RLerp(from.rotation, goal.rotation, alpha, motion.shortest)
    local vector = {X = from.location.X + (goal.location.X - from.location.X) * alpha,
        Y = from.location.Y + (goal.location.Y - from.location.Y) * alpha,
        Z = from.location.Z + (goal.location.Z - from.location.Z) * alpha}
    motion.arrow:K2_SetRelativeLocationAndRotation(vector, angles, false, {}, true)
    if motion_probe and name == motion_probe.name and not motion_probe.first and motion.actor.Open ~= motion.opens
        and (math.abs((angles.Yaw - from.rotation.Yaw + 180) % 360 - 180) > .5
            or math.abs(vector.Z - from.location.Z) > .5) then
        motion_probe.first = (now - motion_probe.pressed) * 1000
    end
    if now >= motion.start + motion.duration + .15 then motions[name] = nil end
end

function doors.play_motion(state, pawn, fields)
    if not state.server or not same(state.world, active_world) or active_phase ~= "connected" then return false end
    local name, opens, duration, ease, shortest = fields[3], tonumber(fields[4]), tonumber(fields[5]),
        math.tointeger(tonumber(fields[6])), fields[7]
    local x, y, z, pitch, yaw, roll = tonumber(fields[8]), tonumber(fields[9]), tonumber(fields[10]),
        tonumber(fields[11]), tonumber(fields[12]), tonumber(fields[13])
    if #fields ~= 13 or (opens ~= 0 and opens ~= 1) or not finite(duration, 3) or duration < .06
        or not ease or ease < 0 or ease > 3 or (shortest ~= "1" and shortest ~= "0")
        or not finite(x, 10000) or not finite(y, 10000) or not finite(z, 10000)
        or not finite(pitch, 360) or not finite(yaw, 360) or not finite(roll, 360) then return false end
    local actor = target_door(state, "connected", name)
    local arrow = valid(actor) and components(actor)
    opens = opens == 1
    if not arrow or actor.Open == opens or actor.Locked or not valid(pawn)
        or not valid(clock) or not valid(math_library) then return false end
    local listener, hinge = pawn:K2_GetActorLocation(), arrow:K2_GetComponentLocation()
    if (listener.X - hinge.X)^2 + (listener.Y - hinge.Y)^2 + (listener.Z - hinge.Z)^2 > 12000^2 then return false end
    local before = pose(actor, arrow)
    if not before then return false end
    motions[name] = {actor = actor, arrow = arrow, opens = opens, duration = duration, ease = ease,
        shortest = shortest == "1", start = clock:GetRealTimeSeconds(state.world), from = {
            location = {X = before[7], Y = before[8], Z = before[9]},
            rotation = {Pitch = before[4], Yaw = before[5], Roll = before[6]}},
        goal = {location = {X = x, Y = y, Z = z}, rotation = {Pitch = pitch, Yaw = yaw, Roll = roll}}}
    return true
end

local function project(fields, state, phase)
    if phase ~= "connected" or fields[3] ~= state.map or #fields < 4 or #fields > 27 then
        error("stock_door_projection_scope_unavailable", 0)
    end
    local known, updates, deferred = census(state, phase), {}, {}
    for index = 4, #fields do
        local name, locked, broken, open, pitch, yaw, roll, x, y, z =
            fields[index]:match("^([^|]+)|([01])|([01])|([01])|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
        local actor = target_door(state, phase, name, known)
        local arrow = valid(actor) and components(actor)
        pitch, yaw, roll = tonumber(pitch), tonumber(yaw), tonumber(roll)
        x, y, z = tonumber(x), tonumber(y), tonumber(z)
        local before = arrow and pose(actor, arrow)
        if not before or updates[name] or not finite(pitch, 360) or not finite(yaw, 360) or not finite(roll, 360)
            or not finite(x, 10000) or not finite(y, 10000) or not finite(z, 10000) then
            error("stock_door_projection_pose_unavailable", 0)
        end
        local expected = {locked == "1", broken == "1", open == "1", pitch, yaw, roll, x, y, z}
        local motion = motions[name]
        if motion and before[1] == expected[1] and before[2] == expected[2] and expected[3] ~= motion.opens then
            -- A snapshot made during the stock movement still has the old Open flag.
            deferred[#deferred + 1] = tostring(index - 3)
        else
            updates[name] = {actor = actor, arrow = arrow, before = before, expected = expected}
        end
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
    for name in pairs(updates) do motions[name] = nil end
    return "stock door flags and hinge transform synchronized" .. (#deferred > 0 and "|" .. table.concat(deferred, "|") or "")
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

local function sample_slice(scan, deadline)
    for _ = 1, math.ceil(#scan.names / 12) do
        local name = scan.names[scan.next]
        scan.readings[name] = sample_door(scan, name)
        scan.next = scan.next % #scan.names + 1
        if os.clock() >= deadline then break end
    end
end

function doors.start(report_motion)
    if type(SilverfishStockDoorGuardAvailable) ~= "function" then return end
    clock = StaticFindObject(statics_path)
    math_library = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    if type(report_motion) == "function" and valid(StaticFindObject(move_component)) then
        RegisterHook(move_component, function(_, part, location, rotation, out_ease, in_ease, seconds, shortest, action, latent)
            if active_phase ~= "hosting" then return end
            pcall(function()
                local arrow = part:get()
                local actor = valid(arrow) and arrow:GetOwner()
                local name = identity(actor, active_world)
                if not name or not actor:HasAuthority() or not same(arrow, actor.Arrow) or action:get() ~= 0 then return end
                local call = latent:get()
                -- These are the stock callbacks for the open and close MoveComponentTo calls.
                local opens = call.Linkage == 163
                if not (opens or call.Linkage == 15) or not same(call.CallbackTarget, actor) then return end
                local duration, p, r = seconds:get(), location:get(), rotation:get()
                if not finite(duration, 3) or duration < .06 or not p or not r then return end
                local ease = (in_ease:get() and 1 or 0) + (out_ease:get() and 2 or 0)
                report_motion(active_world, name, opens and 1 or 0, duration, ease, shortest:get() and 1 or 0,
                    p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll)
            end)
        end)
    end
    LoopInGameThreadWithDelay(16, function()
        if next(motions) then
            if active_phase ~= "connected" or not valid(active_world) then
                motions = {}
            else
                local now = clock:GetRealTimeSeconds(active_world)
                for name, motion in pairs(motions) do
                    if not pcall(animate, name, motion, now) then motions[name] = nil end
                end
            end
        end
        local deadline = os.clock() + .002
        if pending_census and not pcall(census_slice, pending_census, deadline) then pending_census = nil end
        local scan = sampler
        if os.clock() < deadline and scan and #scan.names > 12 and scan.actors == actors
            and same(scan.world, active_world) and not pcall(sample_slice, scan, deadline) then sampler = nil end
    end)
    periodic_sampling = true
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

function doors.focused_target(pawn, pressed_component)
    if not available() or not valid(pawn) or pawn:HasAuthority() then return end
    local knob
    if pressed_component == nil then
        if pawn.CanInteract ~= true then return end
        knob = pawn.InteractionTarget_Component
    elseif type(pressed_component) == "string" and #pressed_component > 0 and #pressed_component <= 512 then
        knob = StaticFindObject(pressed_component)
    end
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
    local actor = target_door(state, phase, target)
    if not guest_fixture or phase ~= "connected" or not valid(actor) or not components(actor) then
        error("stock_door_guest_input_fixture_unavailable", 0)
    end
    require("native_loot_crate_input").stock_focus(pawn, actor.Box_Knob, function()
        if doors.focused_target(pawn) ~= "stock-door:" .. target then error("stock_door_fixture_missing_focus", 0) end
        motion_probe = {name = target, pressed = clock:GetRealTimeSeconds(state.world)}
        dispatch()
    end)
    return "stock door guest input dispatched"
end

local function fixture(fields, state, phase)
    local mode = fields[3]
    local actor = target_door(state, phase, fields[4])
    if #fields ~= 4 or not valid(actor) or not components(actor) then error("stock_door_fixture_scope_unavailable", 0) end
    if mode == "pry" and phase == "hosting" and host_fixture and actor.Locked == true then
        actor:PriedOpen_Lock()
    elseif mode == "guard" and phase == "connected" and guest_fixture and actor.Locked == true then
        actor:PriedOpen_Lock()
    elseif mode == "motion" and phase == "connected" and guest_fixture and motion_probe and motion_probe.name == fields[4] then
        return motion_probe.first and ("%.1f"):format(motion_probe.first) or "no movement before the final flag"
    else
        error("stock_door_fixture_scope_unavailable", 0)
    end
    return "stock hinged door fixture dispatched"
end

local function interact(fields, state, phase)
    local actor = target_door(state, phase, fields[3])
    if #fields ~= 4 or phase ~= "hosting" or not valid(actor) or not components(actor)
        or type(actor.Locked) ~= "boolean" then error("stock_door_guest_unavailable", 0) end
    if actor.Locked then error("stock_door_locked", 0) end
    local qualified, pawn = pcall(guest_save.exact_item_context, state, phase, fields[4])
    if not qualified or not valid(pawn) or pawn.Dead ~= false then error("stock_door_guest_unavailable", 0) end
    local knob = actor.Box_Knob
    local _, was_open = require("native_loot_crate_input").stock_focus(pawn, knob, function()
        -- Stock F uses the interaction trace. The separate visibility ray can
        -- hit the wall behind a valid knob when the door is open.
        if not same(actor.Box_Knob, knob) or actor.Locked ~= false or type(actor.Open) ~= "boolean" then
            error("stock_door_guest_out_of_reach", 0)
        end
        local before = actor.Open
        actor:SendInteractionToActor(pawn, knob)
        return before
    end, "stock_door_guest_out_of_reach")
    return "stock hinged door interaction dispatched " .. (was_open and "open" or "closed")
end

local function tool_pry(fields, state, phase)
    tool_report = nil
    local target, name, guid, kind = fields[3], fields[4], fields[5], fields[6]
    local actor = target_door(state, phase, target)
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
    local actor = permitted and target_door(state, phase, fields[3])
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
