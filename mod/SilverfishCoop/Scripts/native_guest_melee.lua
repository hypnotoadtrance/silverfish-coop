local melee = {}
local guest_save = require("guest_save")
local cqc = require("native_enemy_loot_registry")

local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local npc_name = cqc[dead_bridge].names[1]
local damage_start = "native-guest-melee-damage-canary-start"
local swing_start = "native-guest-melee-swing-start"
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
local latest, equipment, staged_aim

local function qualified_host(state, phase, testing)
    return enabled and phase == "hosting" and state.listening and state.server == false
        and (cqc[state.map] ~= nil or testing and state.map == crossroads)
        and (testing and fixtures and type(SilverfishStartNativeGuestMeleeCanary) == "function"
            and type(SilverfishEquipNativeGuestOldPipe) == "function"
            or not testing and type(SilverfishStartNativeGuestOldPipeSwing) == "function")
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

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
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
    local profile = cqc[area]
    local target_ok = ordinary and (target == "-" or profile and profile.paths[target] ~= nil)
        or not ordinary and target == npc_name
    if damage and (not target_ok or ((miss or cancelled) and target ~= "-") or (settled and not miss and target == "-")
        or (sweeps ~= 0 and sweeps ~= 1) or (calls ~= 0 and calls ~= 1) or calls > sweeps
        or not finite(before) or not finite(after) or not finite(total)
        or ((miss or cancelled) and (sweeps ~= 0 or calls ~= 0 or before ~= -1 or after ~= -1 or total ~= -1))
        or (settled and not miss and (sweeps ~= 1 or calls ~= 1 or before < 100 or before > 160 or after <= 0 or total <= 0
            or total > 90 or math.abs(before - after - total) > 1e-6))) then
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
        if #fields ~= (action == fixture_aim and 5 or 4) or type(guid) ~= "string" or #guid ~= 32
            or guid:find("[^0-9A-F]") or not guid:find("[^0]") then
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
        local invoke = ordinary and SilverfishStartNativeGuestOldPipeSwing
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
    if not latest or latest.map ~= state.map then return rows end
    local pawn = exact_guest(state, phase, latest.pawn, standing)
    local invalid = not pawn or pawn:GetAddress() ~= latest.address
    read(latest, invalid and latest.proof.status == "pending")
    local proof = latest.proof
    local fields = {latest.ordinary and "native-guest-melee-swing" or "native-guest-melee-canary", state.map,
        latest.pawn, latest.guid, proof.serial, proof.status,
        proof.presses, proof.releases, proof.contacts, proof.qualified, proof.settled and 1 or 0, proof.passives and 1 or 0}
    if latest.damage then
        fields[13], fields[14], fields[15] = proof.target, proof.sweeps, proof.calls
        fields[16], fields[17], fields[18] = proof.before, proof.after, proof.total
    end
    rows[#rows + 1] = status_row(table.unpack(fields))
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
