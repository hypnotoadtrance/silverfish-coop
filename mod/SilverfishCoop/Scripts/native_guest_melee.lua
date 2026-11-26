local melee = {}
local guest_save = require("guest_save")
local cqc = require("native_enemy_loot_registry")

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local npc_name = cqc[dead_bridge].names[1]
local damage_start = "native-guest-melee-damage-canary-start"
local swing_start = "native-guest-melee-swing-start"
local input_start = "native-guest-melee-input-start"
local input_edge = "native-guest-melee-input-edge"
local input_state = "native-guest-melee-input-state"
local fixture_aim = "native-guest-melee-aim-fixture"
local fixture_contact = "native-guest-melee-contact-fixture"
local actions = {
    ["native-guest-melee-canary-equip"] = true,
    ["native-guest-melee-canary-start"] = true,
    [damage_start] = true,
    [swing_start] = true,
    [fixture_aim] = true,
    ["native-guest-melee-canary-observe"] = true,
    ["native-guest-melee-canary-abort"] = true,
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local latest, equipment, staged_aim, input_scope

local function qualified_host(state, phase, testing)
    return enabled and phase == "hosting" and state.listening and state.server == false
        and (not testing or cqc[state.map] ~= nil or state.map == crossroads)
        and (testing and fixtures and type(SilverfishStartNativeGuestMeleeCanary) == "function"
            and type(SilverfishEquipNativeGuestOldPipe) == "function"
            or not testing and type(SilverfishStartNativeGuestMeleeSwing) == "function")
        and type(SilverfishReadNativeGuestMeleeCanary) == "function"
        and type(SilverfishAbortNativeGuestMeleeCanary) == "function"
end

local function exact_guest(state, phase, name, standing)
    if type(standing) ~= "function" or type(name) ~= "string" then return end
    local called, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not called or not pawn or not pawn:IsValid() or not save or not save:IsValid() then return end
    local projected, ready = pcall(standing, pawn, name)
    if projected and ready == true and pawn:GetFullName() == name then return pawn, save end
end

local function valid_guid(guid)
    return type(guid) == "string" and #guid == 32 and not guid:find("[^0-9A-F]") and guid:find("[^0]") ~= nil
end

local function melee_input_proof(ok, status, serial, presses, releases, contacts, hits, charged, combo)
    if ok == false and serial == 0 and type(status) == "string" and status:match("^native_guest_melee_") then
        error(status, 0)
    end
    local dirty = type(status) == "string" and status:match("^mutation_unverified_guest_melee_")
    if (dirty and ok ~= false) or (not dirty and (ok ~= true or
        (status ~= "pending" and status ~= "settled" and status ~= "cancelled")))
        or not math.tointeger(serial) or serial < 1 or serial >= 2 ^ 53
        or not math.tointeger(presses) or not math.tointeger(releases) or not math.tointeger(contacts)
        or not math.tointeger(hits) or not math.tointeger(charged) or not math.tointeger(combo)
        or presses < 1 or releases < 0 or presses - releases < 0 or presses - releases > 1
        or hits < 0 or contacts < hits or contacts > presses or charged < 0 or combo < 0 or charged + combo > contacts
        or ((status == "settled" or status == "cancelled") and releases ~= presses) then
        error("mutation_unverified_guest_melee_input_abi", 0)
    end
    return {serial = serial, status = status, presses = presses, releases = releases, contacts = contacts,
        hits = hits, charged = charged, combo = combo}
end

local function command_input(fields, state, phase, standing)
    if not qualified_host(state, phase, false) or type(SilverfishStartNativeGuestMeleeInput) ~= "function"
        or type(SilverfishApplyNativeGuestMeleeInputEdge) ~= "function"
        or type(SilverfishReadNativeGuestMeleeInput) ~= "function" then
        error("native_guest_melee_swing_unavailable", 0)
    end
    if fields[2] == input_start then
        local name, guid = fields[3], fields[4]
        local tap = #fields == 5 and fields[5] == "tap"
        if (#fields ~= 4 and not tap) or not valid_guid(guid) then error("native_guest_melee_exact_guid_required", 0) end
        local pawn, save = exact_guest(state, phase, name, standing)
        if not pawn then error("native_guest_melee_exact_owner_required", 0) end
        local proof
        if tap then
            proof = melee_input_proof(SilverfishStartNativeGuestMeleeInput(pawn:GetAddress(), save:GetAddress(), guid, "tap"))
        else
            proof = melee_input_proof(SilverfishStartNativeGuestMeleeInput(pawn:GetAddress(), save:GetAddress(), guid))
        end
        latest = nil
        input_scope = {map = state.map, pawn = name, guid = guid, proof = proof}
    else
        local serial, edge = math.tointeger(fields[3]), fields[4]
        if #fields ~= 4 or not input_scope or input_scope.map ~= state.map or serial ~= input_scope.proof.serial
            or (edge ~= "press" and edge ~= "release" and edge ~= "cancel" and edge ~= "tap") then
            error("native_guest_melee_exact_edge_required", 0)
        end
        if (edge == "press" or edge == "tap") and not exact_guest(state, phase, input_scope.pawn, standing) then
            error("native_guest_melee_exact_owner_required", 0)
        end
        local proof = melee_input_proof(SilverfishApplyNativeGuestMeleeInputEdge(serial, edge))
        if proof.serial ~= serial then error("mutation_unverified_guest_melee_input_owner", 0) end
        input_scope.proof = proof
    end
    return "stock guest melee input observed"
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function ordinary_target(area, target)
    if type(target) ~= "string" or #target > 512 then return false end
    local path = target:match("^[%w_]+ (.+)$")
    local level = area:match("([^/]+)$")
    if not path or not level then return false end
    local prefix = area .. "." .. level .. ":PersistentLevel."
    return path:sub(1, #prefix) == prefix and path:sub(#prefix + 1):match("^[%w_]+$") ~= nil
end

local function observation(damage, ordinary, area, ok, status, serial, presses, releases, contacts, qualified, settled, passives,
                           sweeps, calls, before, after, total, target)
    local dirty = type(status) == "string" and status:match("^mutation_unverified_guest_melee_") ~= nil
    local settled_status = damage and "stock_damage_verified" or "surface_verified"
    local miss = ordinary and status == "stock_miss_verified"
    local cancelled = ordinary and status == "stock_swing_cancelled"
    if (ok ~= true and not dirty) or (dirty and ok ~= false)
        or math.type(serial) ~= "integer" or serial < 1 or serial >= 2 ^ 53
        or presses ~= 1 or (releases ~= 0 and releases ~= 1)
        or math.type(contacts) ~= "integer" or contacts < 0 or contacts > 64
        or (qualified ~= 0 and qualified ~= 1) or qualified > contacts
        or type(settled) ~= "boolean" or type(passives) ~= "boolean"
        or settled ~= (status == settled_status or miss)
        or (settled and (releases ~= 1 or contacts ~= 1 or qualified ~= 1 or not passives))
        or (not dirty and status ~= "pending" and status ~= settled_status and status ~= "aborted_contact_fenced"
            and not (damage and status == "stock_damage_aim_fenced") and not miss and not cancelled) then
        error("mutation_unverified_guest_melee_abi", 0)
    end
    local target_ok = ordinary and (target == "-" or ordinary_target(area, target))
        or not ordinary and target == npc_name
    local numbers_ok = finite(before) and finite(after) and finite(total)
    local valid_damage = numbers_ok and before > 0 and after < before and total > 0
        and (math.abs(before - after - total) <= 1e-6 or ordinary and after == 0 and total >= before - 1e-6)
        and (ordinary or before >= 100 and before <= 160 and after > 0 and total <= 90)
    if damage and (not target_ok or ((miss or cancelled) and target ~= "-") or (settled and not miss and target == "-")
        or (sweeps ~= 0 and sweeps ~= 1) or (calls ~= 0 and calls ~= 1) or calls > sweeps
        or not numbers_ok
        or ((miss or cancelled) and (sweeps ~= 0 or calls ~= 0 or before ~= -1 or after ~= -1 or total ~= -1))
        or (settled and not miss and (sweeps ~= 1 or calls ~= 1 or not valid_damage))) then
        error("mutation_unverified_guest_melee_damage_abi", 0)
    end
    return {status = status, serial = serial, presses = presses, releases = releases,
        contacts = contacts, qualified = qualified, settled = settled, passives = passives,
        sweeps = sweeps, calls = calls, before = before, after = after, total = total, target = target}
end

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function point(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return {X = value.X, Y = value.Y, Z = value.Z}
    end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return {Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll}
    end
end

local function distance(first, second)
    return math.sqrt((first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2)
end

local function near_rotation(actual, expected)
    if not actual then return false end
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((actual[axis] - expected[axis] + 180) % 360 - 180) > 0.15 then return false end
    end
    return true
end

local function restore_pose(pose)
    if not pose then return end
    local pawn, controller, camera = pose.pawn, pose.controller, pose.camera
    if not valid(pawn) or not valid(camera) or not same(pawn.Controller, controller)
        or not same(pawn.Camera, camera) or not same(pawn:GetWorld(), pose.world)
        or not pawn:K2_TeleportTo(pose.origin, pose.facing) then
        error("mutation_unverified_guest_melee_damage_pose_restore", 0)
    end
    controller:SetControlRotation(pose.control)
    camera:K2_SetRelativeRotation(pose.camera_rotation, false, {}, true)
    pawn:ForceNetUpdate()
    local origin = point(pawn:K2_GetActorLocation())
    if not origin or distance(origin, pose.origin) > 18
        or not near_rotation(rotation(pawn:K2_GetActorRotation()), pose.facing)
        or not near_rotation(rotation(controller:GetControlRotation()), pose.control)
        or not near_rotation(rotation(camera.RelativeRotation), pose.camera_rotation) then
        error("mutation_unverified_guest_melee_damage_pose_readback", 0)
    end
end

local function damage_pose(state, pawn, name)
    local profile = cqc[state.map]
    local path = profile and profile.paths[name]
    local enemy = path and StaticFindObject(path)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local host = state.local_pawn and state.local_pawn.pawn
    local camera, capsule, controller = pawn.Camera, pawn.CapsuleComponent, pawn.Controller
    local check = SilverfishCheckNativeGuestMeleeDamagePose
    if type(check) ~= "function" or not valid(enemy) or enemy:GetFullName() ~= name
        or not enemy:HasAuthority() or not same(enemy:GetWorld(), state.world) or enemy.Alive ~= true
        or not valid(host) or not same(host:GetWorld(), state.world)
        or not valid(camera) or not same(camera:GetOwner(), pawn)
        or not valid(capsule) or not same(capsule:GetOwner(), pawn) or not valid(controller)
        or not same(controller.Pawn, pawn) or not valid(trace) then
        error("native_guest_melee_stock_damage_placement_unqualified", 0)
    end
    local original = {pawn = pawn, world = state.world, camera = camera, controller = controller,
        origin = point(pawn:K2_GetActorLocation()), facing = rotation(pawn:K2_GetActorRotation()),
        control = rotation(controller:GetControlRotation()), camera_rotation = rotation(camera.RelativeRotation)}
    local anchor, host_position = point(enemy:K2_GetActorLocation()), point(host:K2_GetActorLocation())
    local half = capsule:GetScaledCapsuleHalfHeight()
    local forward = point(enemy:GetActorForwardVector())
    if not original.origin or not original.facing or not original.control or not original.camera_rotation
        or not anchor or not forward or not host_position or distance(host_position, anchor) < 2500
        or not finite(half) or half < 25 or half > 150 then
        error("native_guest_melee_stock_damage_distance_unqualified", 0)
    end
    local tint = {R = 0, G = 0, B = 0, A = 0}
    local bearing = math.atan(forward.Y, forward.X) + math.pi
    local moved = false
    local floor_hits, floors, teleports, eyes, probes = 0, 0, 0, 0, 0
    local refusals, observed_health = {}, -1
    local ran, clear = pcall(function()
        for _, offset in ipairs({0, 30, -30, 60, -60, 90, -90, 180}) do
            local heading = bearing + math.rad(offset)
            for _, range in ipairs({155, 125, 185}) do
                local x, y = anchor.X + range * math.cos(heading), anchor.Y + range * math.sin(heading)
                local hit = {}
                local floor = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = anchor.Z + 275},
                    {X = x, Y = y, Z = anchor.Z - 245}, 0, false, {pawn}, 0, hit, true, tint, tint, 0)
                hit = hit.OutHit or hit
                if floor == true then floor_hits = floor_hits + 1 end
                local base, normal = floor == true and point(hit.ImpactPoint), point(hit.ImpactNormal)
                local component = hit.Component and hit.Component:Get()
                if base and normal and normal.Z >= 0.75 and finite(hit.Time) and hit.Time > 0 and hit.Time <= 1
                    and finite(hit.Distance) and hit.Distance > 1 and hit.PenetrationDepth == 0
                    and valid(component) and component:GetCollisionObjectType() == 0 then
                    local position = {X = x, Y = y, Z = base.Z + half + 3}
                    local yaw = math.deg(math.atan(anchor.Y - y, anchor.X - x))
                    if math.abs(position.Z - anchor.Z) <= 75 then floors = floors + 1 end
                    if math.abs(position.Z - anchor.Z) <= 75 and
                        pawn:K2_TeleportTo(position, {Pitch = 0, Yaw = yaw, Roll = 0}) then
                        moved = true
                        teleports = teleports + 1
                        local actual, eye = point(pawn:K2_GetActorLocation()), point(camera:K2_GetComponentLocation())
                        if actual and eye and distance(actual, position) <= 18 and distance(eye, actual) <= 150 then
                            eyes = eyes + 1
                            for _, height in ipairs({10, 25, -5}) do
                                local pitch = math.deg(math.atan(anchor.Z + height - eye.Z,
                                    math.sqrt((anchor.X - eye.X)^2 + (anchor.Y - eye.Y)^2)))
                                local aim = {Pitch = pitch,
                                    Yaw = math.deg(math.atan(anchor.Y - eye.Y, anchor.X - eye.X)), Roll = 0}
                                if math.abs(aim.Pitch) <= 35 then
                                    controller:SetControlRotation(aim)
                                    camera:K2_SetWorldRotation(aim, false, {}, true)
                                    probes = probes + 1
                                    local ready, reason, health = check(pawn:GetAddress(), enemy:GetAddress())
                                    if finite(health) then observed_health = health end
                                    if ready == true then
                                        pawn:ForceNetUpdate()
                                        original.target = enemy:GetAddress()
                                        return true
                                    end
                                    if type(reason) ~= "string" or #reason > 32 or not reason:match("^[a-z_]+$") then
                                        reason = "pose_probe_abi"
                                    end
                                    refusals[reason] = (refusals[reason] or 0) + 1
                                    if reason == "target_health" then return false end
                                end
                            end
                        end
                    end
                end
            end
        end
        return false
    end)
    if ran and clear then return original end
    if moved then restore_pose(original) end
    if not ran then error(clear, 0) end
    local diagnostic = {}
    for reason, count in pairs(refusals) do diagnostic[#diagnostic + 1] = reason .. "=" .. count end
    table.sort(diagnostic)
    error(("native_guest_melee_stock_damage_sight_unavailable:hits=%d:floor=%d:teleport=%d:eyes=%d:probes=%d:health=%.3f:%s")
        :format(floor_hits, floors, teleports, eyes, probes, observed_health, table.concat(diagnostic, ",")), 0)
end

local function read(scope, abort)
    local native = abort and SilverfishAbortNativeGuestMeleeCanary or SilverfishReadNativeGuestMeleeCanary
    local checked, proof = pcall(function() return observation(scope.damage, scope.ordinary, scope.map, native(scope.proof.serial)) end)
    if not checked then
        if scope.pose then
            pcall(SilverfishAbortNativeGuestMeleeCanary, scope.proof.serial)
            restore_pose(scope.pose)
            scope.pose = nil
        end
        error(proof, 0)
    end
    if proof.serial ~= scope.proof.serial then error("mutation_unverified_guest_melee_owner", 0) end
    scope.proof = proof
    if proof.status ~= "pending" and scope.pose then
        restore_pose(scope.pose)
        scope.pose = nil
    end
end

function melee.command(fields, state, phase, standing)
    local action = fields[2]
    if action == input_start or action == input_edge then return command_input(fields, state, phase, standing) end
    if not actions[action] then return end
    local ordinary = action == swing_start
    if not qualified_host(state, phase, not ordinary) then error("native_guest_melee_fixture_unavailable", 0) end
    local damage = action == damage_start or ordinary
    if damage or action == fixture_aim or action == "native-guest-melee-canary-start" or action == "native-guest-melee-canary-equip" then
        if (action == damage_start and state.map ~= dead_bridge) or (action == "native-guest-melee-canary-start" and state.map ~= crossroads)
            or (action == "native-guest-melee-canary-equip" and state.map ~= crossroads and state.map ~= dead_bridge)
            or (action == damage_start and type(SilverfishStartNativeGuestMeleeDamageCanary) ~= "function") then
            error("native_guest_melee_fixture_unavailable", 0)
        end
        local name, guid = fields[3], fields[4]
        if #fields ~= (action == fixture_aim and 5 or 4) or not valid_guid(guid) then
            error("native_guest_melee_exact_guid_required", 0)
        end
        local pawn, save = exact_guest(state, phase, name, standing)
        if not pawn then error("native_guest_melee_exact_owner_required", 0) end
        if action == fixture_aim then
            local target, profile = fields[5], cqc[state.map]
            if not profile or not profile.paths[target] or type(SilverfishCheckNativeGuestMeleeDamagePose) ~= "function" then
                error("native_guest_melee_fixture_unavailable", 0)
            end
            staged_aim = {map = state.map, pawn = name, address = pawn:GetAddress(), guid = guid, target = target, expires = os.time() + 10}
            return "stock Old Pipe aim fixture armed"
        end
        if action == "native-guest-melee-canary-equip" then
            if latest and latest.proof.status == "pending" then error("native_guest_melee_canary_pending", 0) end
            local ok, status = SilverfishEquipNativeGuestOldPipe(pawn:GetAddress(), save:GetAddress(), guid)
            if type(ok) ~= "boolean" or type(status) ~= "string" or (ok and status ~= "old_pipe_equipped") then
                error("mutation_unverified_guest_melee_equip_abi", 0)
            end
            equipment = {map = state.map, pawn = name, guid = guid, ok = ok, status = status}
            return "stock guest Old Pipe equip observed"
        end
        local fixture_bound = ordinary and staged_aim and staged_aim.map == state.map and staged_aim.pawn == name
            and staged_aim.address == pawn:GetAddress() and staged_aim.guid == guid and os.time() <= staged_aim.expires
        local target = action == damage_start and npc_name or fixture_bound and staged_aim.target
        if fixture_bound then staged_aim = nil end
        local pose = target and damage_pose(state, pawn, target)
        local invoke = ordinary and SilverfishStartNativeGuestMeleeSwing
            or damage and SilverfishStartNativeGuestMeleeDamageCanary or SilverfishStartNativeGuestMeleeCanary
        local called, result = pcall(function()
            if fixture_bound then return table.pack(invoke(pawn:GetAddress(), save:GetAddress(), guid, pose.target)) end
            return table.pack(invoke(pawn:GetAddress(), save:GetAddress(), guid))
        end)
        if not called then
            restore_pose(pose)
            error("mutation_unverified_guest_melee_start", 0)
        end
        local ok, status, serial = table.unpack(result, 1, 3)
        if ok == false and serial == 0 and type(status) == "string" and status:match("^native_guest_melee_") then
            restore_pose(pose)
            error(status, 0)
        end
        local checked, proof = pcall(observation, damage, ordinary, state.map, table.unpack(result, 1, result.n))
        if not checked then
            if pose and math.type(serial) == "integer" and serial > 0 then
                pcall(SilverfishAbortNativeGuestMeleeCanary, serial)
            end
            restore_pose(pose)
            error(proof, 0)
        end
        latest = {map = state.map, pawn = name, guid = guid, address = pawn:GetAddress(), damage = damage,
            ordinary = ordinary, pose = pose, proof = proof, fixture_target = fixture_bound and target}
        input_scope = nil
        if latest.proof.status ~= "pending" and latest.pose then
            restore_pose(latest.pose)
            latest.pose = nil
        end
    else
        local serial = math.tointeger(fields[3])
        if #fields ~= 3 or not latest or latest.map ~= state.map or serial ~= latest.proof.serial then
            error("native_guest_melee_exact_serial_required", 0)
        end
        read(latest, action == "native-guest-melee-canary-abort")
    end
    return "stock guest Old Pipe animation observed"
end

local function input_row(scope, status_row)
    local proof = scope.proof
    return status_row(input_state, scope.map, scope.pawn, scope.guid, proof.serial, proof.status,
        proof.presses, proof.releases, proof.contacts, proof.hits, proof.charged, proof.combo)
end

local function swing_row(swing, status_row)
    local proof = swing.proof
    local fields = {swing.ordinary and "native-guest-melee-swing" or "native-guest-melee-canary", swing.map,
        swing.pawn, swing.guid, proof.serial, proof.status,
        proof.presses, proof.releases, proof.contacts, proof.qualified, proof.settled and 1 or 0, proof.passives and 1 or 0}
    if swing.damage then
        fields[13], fields[14], fields[15] = proof.target, proof.sweeps, proof.calls
        fields[16], fields[17], fields[18] = proof.before, proof.after, proof.total
    end
    return status_row(table.unpack(fields))
end

function melee.local_reply(fields, state, phase, sequence, status_row, standing)
    local accepted, message = pcall(melee.command, fields, state, phase, standing)
    local report
    if accepted then
        if fields[2] == swing_start then
            if latest and latest.map == state.map then report = swing_row(latest, status_row) end
        elseif input_scope and input_scope.map == state.map then
            report = input_row(input_scope, status_row)
        end
        if not report then accepted, message = false, "mutation_unverified_guest_melee_readback" end
    end
    local rows = {"native-guest-melee-reply\t1", status_row("schema", 1), status_row("seq", sequence),
        status_row("ack", fields[1], accepted and "accepted" or "error", message),
        status_row("session", "host", phase, state.map, state.clients, state.server and 1 or 0)}
    if report then rows[#rows + 1] = report end
    return table.concat(rows, "\n") .. "\n"
end

function melee.rows(state, phase, status_row, standing)
    local rows = {}
    if staged_aim then
        if staged_aim.map ~= state.map or os.time() > staged_aim.expires then staged_aim = nil
        elseif qualified_host(state, phase, true) then
            rows[#rows + 1] = status_row(fixture_aim, state.map, staged_aim.pawn, staged_aim.guid, staged_aim.target)
        end
    end
    if not qualified_host(state, phase, false) and not qualified_host(state, phase, true) then return rows end
    if equipment and equipment.map == state.map then
        rows[#rows + 1] = status_row("native-guest-melee-equip", state.map, equipment.pawn, equipment.guid,
            equipment.ok and 1 or 0, equipment.status)
    end
    if input_scope and input_scope.map == state.map then
        local scope = input_scope
        local proof = scope.proof
        if proof.status == "pending" then
            proof = melee_input_proof(SilverfishReadNativeGuestMeleeInput(proof.serial))
            if proof.serial ~= scope.proof.serial then error("mutation_unverified_guest_melee_input_owner", 0) end
            scope.proof = proof
        end
        rows[#rows + 1] = input_row(scope, status_row)
    end
    if not latest or latest.map ~= state.map then return rows end
    local pawn = exact_guest(state, phase, latest.pawn, standing)
    local invalid = not pawn or pawn:GetAddress() ~= latest.address
    read(latest, invalid and latest.proof.status == "pending")
    local proof = latest.proof
    rows[#rows + 1] = swing_row(latest, status_row)
    if latest.fixture_target and type(SilverfishReadNativeGuestMeleeContactFixture) == "function" then
        local ok, detail = pcall(function() return table.pack(SilverfishReadNativeGuestMeleeContactFixture(proof.serial)) end)
        if ok and (detail.n == 9 or detail.n == 10) and detail[1] == latest.fixture_target then
            rows[#rows + 1] = status_row(fixture_contact, state.map, latest.pawn, latest.guid, proof.serial,
                table.unpack(detail, 1, detail.n))
        end
    end
    return rows
end

return melee
