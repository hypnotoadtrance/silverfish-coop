local campaign_npc = {}
local guest_save = require("guest_save")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC") == "1"
local not_deer_enabled = enabled and os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_NOTDEER") == "1"
local combat_enabled = os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_COMBAT") == "1"
local production_roster = os.getenv("SILVERFISH_COOP_NATIVE_CROSSROADS_ROSTER") == "1"
local headless_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixture = enabled and (os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REPLICATE") == "1"
    or (not_deer_enabled and combat_enabled)) and headless_fixtures
local combat_fixture = fixture and combat_enabled
local detached_sickness = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SICKNESS") == "1"
    or os.getenv("SILVERFISH_COOP_NATIVE_CROSSROADS_ROSTER") == "1"
local refresh_fixture = fixture and os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_REFRESH") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local ambient_worlds = {
    [dead_bridge] = true,
    ["/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"] = true,
}
local level = map .. ".CrossRoads_P:PersistentLevel."
local types = {
    husk = { name = "NPC_Zomb_01_C", path = "/Game/Classes/NPCs/NPC_Zomb_01.NPC_Zomb_01_C", limit = 5,
        stages = { stock_initial = true, stock_refresh = true }, combat_radii = { 550, 680, 820 } },
    flayer = { name = "NPC_Flayer_C", path = "/Game/Classes/NPCs/NPC_Flayer.NPC_Flayer_C", limit = 2,
        stages = { stock_antfarm = true }, combat_radii = { 235, 275, 295 }, trace_sweep = 350 },
    notdeer = { name = "NPC_NotDeer_C", path = "/Game/Classes/NPCs/NPC_NotDeer.NPC_NotDeer_C", limit = 1,
        stages = { stock_antfarm = true }, combat_radii = { 205, 230, 250 }, trace_sweep = 300 },
}
local requested_view = os.getenv("SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW")
local view = "husk"
if requested_view == "notdeer" and not_deer_enabled then view = "notdeer" end
if requested_view == "flayer" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1" then view = "flayer" end

local function read_family(pawn, host, family, current_map)
    if current_map and current_map ~= map then
        return SilverfishReadCampaignNpc(pawn:GetAddress(), host, family, current_map)
    end
    if family ~= "husk" then return SilverfishReadCampaignNpc(pawn:GetAddress(), host, family) end
    return SilverfishReadCampaignNpc(pawn:GetAddress(), host)
end
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local trace_color = { R = 0, G = 0, B = 0, A = 0 }
local approach_radius, readback_tolerance = 4000, 60
local sweep_margin = 25
local frontal_angles = { 0, 30, -30, 50, -50 }
local active, approach, refresh, shot_aim, shot_ticket

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

function campaign_npc.clear()
    active, approach, refresh, shot_aim, shot_ticket = nil, nil, nil, nil, nil
    if enabled and type(SilverfishClearCampaignNpc) == "function" then SilverfishClearCampaignNpc() end
end

local function controlled(detail, world, local_player)
    local pawn, controller = detail and detail.pawn, detail and detail.controller_object
    return exact(pawn, pawn_path) and exact(controller, controller_path)
        and pawn:GetFullName() == detail.name and not pawn:IsActorBeingDestroyed()
        and not controller:IsActorBeingDestroyed() and pawn:HasAuthority() and controller:HasAuthority()
        and pawn:IsLocallyControlled() == local_player and controller:IsLocalController() == local_player
        and same(pawn.Controller, controller) and same(controller.Pawn, pawn)
        and same(pawn:GetWorld(), world) and same(controller:GetWorld(), world)
end

local function paired(state, phase, ambient)
    local host = state.local_pawn
    local guest = state.guests and #state.guests == 1 and state.guests[1]
    if phase ~= "hosting" or (state.map ~= map and not ambient) or not state.listening or state.server ~= false or state.clients ~= 1
        or not valid(state.world) then return nil, "listen_world" end
    if not controlled(host, state.world, true) or not same(host.controller_object, state.controller) then
        return nil, "host_authority"
    end
    if not controlled(guest, state.world, false) or same(host.pawn, guest.pawn) then return nil, "guest_authority" end
    if players.is_downed(host.pawn) ~= false then return nil, "host_not_standing" end
    if not players.is_standing_guest(guest.pawn, guest.name) then return nil, "guest_not_standing" end
    local driver, remote = state.world.NetDriver, guest.controller_object
    local connection = remote.Player
    local connection_class = StaticFindObject("/Script/Engine.NetConnection")
    if not valid(driver) or not same(driver.World, state.world) or valid(driver.ServerConnection)
        or not valid(connection) or not valid(connection_class) or not connection:IsA(connection_class)
        or not same(connection.Driver, driver) or not same(connection.PlayerController, remote)
        or not same(connection.OwningActor, remote) or (valid(remote.NetConnection) and not same(remote.NetConnection, connection))
        or driver.ClientConnections:GetArrayNum() ~= 1 or not same(driver.ClientConnections[1], connection) then
        return nil, "guest_connection"
    end
    local verified, enrolled, saved = pcall(guest_save.exact_item_context, state, phase, guest.name)
    if verified and same(enrolled, guest.pawn) and same(saved, guest.pawn.Save) and not same(saved, host.pawn.Save) then
        return host, guest, saved
    end
    return nil, "guest_detached_save"
end

local function stock_member(world, local_pawn, name, current_map)
    local family = current_map and current_map ~= map and "husk" or view
    local profile = types[family]
    local current_level = family == "husk" and current_map and current_map ~= map
        and current_map .. "." .. current_map:match("[^/]+$") .. ":PersistentLevel." or level
    local prefix = profile.name .. " " .. current_level
    if type(SilverfishReadCampaignNpc) ~= "function" or type(name) ~= "string" or #name > 512
        or name:sub(1, #prefix) ~= prefix
        or not name:sub(#prefix + 1):match("^" .. profile.name .. "_%d+$") then return end
    local receipts, stage, spawner, data, size, replicated, _, _, natural_calls, _, _, _, _, _, roster =
        read_family(local_pawn, true, family, current_map)
    if type(receipts) ~= "number" or receipts < 1 or not profile.stages[stage]
        or type(spawner) ~= "string" or spawner == "" or type(data) ~= "string" or data == ""
        or type(size) ~= "number" or size < 1 or size > profile.limit or replicated ~= size
        or type(natural_calls) ~= "number" or natural_calls < receipts or type(roster) ~= "string" then return end
    local names, count = {}, 0
    for fullname in roster:gmatch("[^\n]+") do
        if names[fullname] then return end
        names[fullname] = true
        count = count + 1
    end
    if not names[name] or count ~= size then return end
    local cohort, target = {}, nil
    for _, actor in ipairs(FindAllOf(profile.name) or {}) do
        if exact(actor, profile.path) and same(actor:GetWorld(), world) and actor:HasAuthority()
            and not actor:IsActorBeingDestroyed() and names[actor:GetFullName()] then
            names[actor:GetFullName()] = nil
            cohort[#cohort + 1] = actor
            if actor:GetFullName() == name then target = actor end
        end
    end
    if target and #cohort == count then return target, cohort end
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge
end

local function point(value)
    if value and finite(value.X) and finite(value.Y) and finite(value.Z) then
        return { X = value.X, Y = value.Y, Z = value.Z }
    end
end

local function rotation(value)
    if value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) then
        return { Pitch = value.Pitch, Yaw = value.Yaw, Roll = value.Roll }
    end
end

local function same_rotation(actual, expected)
    if not actual then return false end
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((actual[axis] - expected[axis] + 180) % 360 - 180) > 0.15 then return false end
    end
    return true
end

local function distance(first, second)
    return math.sqrt((first.X - second.X) ^ 2 + (first.Y - second.Y) ^ 2 + (first.Z - second.Z) ^ 2)
end

local function placement(pawn)
    local capsule = pawn.CapsuleComponent
    local half_height = valid(capsule) and same(capsule:GetOwner(), pawn) and capsule:GetScaledCapsuleHalfHeight()
    local origin, facing = point(pawn:K2_GetActorLocation()), rotation(pawn:K2_GetActorRotation())
    if origin and facing and finite(half_height) and half_height >= 20 and half_height <= 120 then
        return { pawn = pawn, origin = origin, facing = facing, half_height = half_height }
    end
end

local function floor_at(trace, player, anchor, x, y, ignored)
    local top, bottom = anchor.Z + 420, anchor.Z - 450
    local hit = {}
    local found = trace:LineTraceSingle(player.pawn, { X = x, Y = y, Z = top }, { X = x, Y = y, Z = bottom },
        0, false, ignored, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    if found ~= true then return end
    local impact, normal = point(hit.ImpactPoint), point(hit.ImpactNormal)
    local component = hit.Component and hit.Component:Get()
    if impact and normal and normal.Z >= 0.75 and impact.Z < top - 1 and impact.Z >= bottom
        and valid(component) and component:GetCollisionObjectType() == 0 then
        return { X = x, Y = y, Z = impact.Z + player.half_height + 3 }
    end
end

local function at(pawn, target, tolerance)
    local current = point(pawn:K2_GetActorLocation())
    return current and distance(current, target) <= tolerance and current
end

local function restore_position(player)
    return at(player.pawn, player.origin, 1) or
        (player.pawn:K2_TeleportTo(player.origin, player.facing) == true
            and at(player.pawn, player.origin, readback_tolerance))
end

local function safe_melee_placement(trace, host, guest, target, cohort, anchor, x, y, angle, ignored, sweep)
    local covered = floor_at(trace, host, anchor, anchor.X - math.cos(angle) * 3500,
        anchor.Y - math.sin(angle) * 3500, ignored)
    local exposed = floor_at(trace, guest, anchor, x, y, ignored)
    if not covered or not exposed or math.abs(exposed.Z - anchor.Z) > 120 then return end
    for _, actor in ipairs(cohort) do
        local location = point(actor:K2_GetActorLocation())
        if not location or distance(covered, location) < 2500 then return end
    end
    if sweep then
        if distance(exposed, anchor) <= sweep - sweep_margin then return covered, exposed end
        return
    end
    local hit = {}
    local blocked = trace:LineTraceSingle(guest.pawn, exposed, anchor, 0, false,
        { host.pawn, guest.pawn }, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    local component = hit.Component and hit.Component:Get()
    if blocked == false or (blocked == true and valid(component) and same(component:GetOwner(), target)) then
        return covered, exposed
    end
end

local function forward_melee_trace(trace, target, guest, location, sweep)
    local start, facing = point(target:K2_GetActorLocation()), point(target:GetActorForwardVector())
    if not start or not facing then return "sweep_range" end
    local reach = distance(start, location)
    if reach > sweep - sweep_margin then return "sweep_range" end
    local dx, dy = location.X - start.X, location.Y - start.Y
    local length = math.sqrt(dx ^ 2 + dy ^ 2)
    local facing_length = math.sqrt(facing.X ^ 2 + facing.Y ^ 2)
    if length < 1 or facing_length < 0.5 or dx * facing.X + dy * facing.Y < length * facing_length / 2 then
        return "front_cone"
    end
    -- ResolveMelee uses a forward Visibility line between these actor locations and an empty ignore list.
    local hit = {}
    local found = trace:LineTraceSingle(target, start, location, 0, false, {}, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    if found ~= true then return "clear" end
    local component = hit.Component and hit.Component:Get()
    local component_name = valid(component) and component:GetFullName() or "-"
    -- UE4SS copies both packed HitResult flags through the same unmasked byte in Lua out tables.
    -- Verify the native trace's distance from its start before accepting its blocking component.
    local impact = { time = hit.Time, distance = hit.Distance, depth = hit.PenetrationDepth }
    if not finite(impact.time) or not finite(impact.distance) or not finite(impact.depth)
        or impact.time < 0 or impact.time > 1 or impact.distance < 0
        or impact.distance > sweep - sweep_margin or impact.depth < 0
        or math.abs(impact.distance - impact.time * reach) > 1 then
        return "invalid_impact", component_name, impact
    end
    if impact.time == 0 or impact.distance < 1 or impact.depth > 0 then
        return "start_penetrating", component_name, impact
    end
    if not valid(component) or not same(component:GetOwner(), guest.pawn) then return "other_hit", component_name, impact end
    local collision = component:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return "guest_collision_unqualified", component_name, impact end
    return "guest_hit", component_name, impact
end

local function place_pair(world, host, guest, target, cohort)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local anchor = point(target:K2_GetActorLocation())
    if not valid(trace) or not anchor then error("stock_husk_approach_geometry_unavailable", 0) end
    local ignored = { host.pawn, guest.pawn }
    for _, actor in ipairs(cohort) do ignored[#ignored + 1] = actor end
    local sweep = combat_fixture and types[view].trace_sweep
    local angles, diagnostics = {}, nil
    if sweep then
        local facing = point(target:GetActorForwardVector())
        if target.Melee_SweepRange ~= sweep or not facing or facing.X ^ 2 + facing.Y ^ 2 < 0.25 then
            error("stock_" .. view .. "_approach_sweep_unqualified", 0)
        end
        local heading = math.atan(facing.Y, facing.X)
        for _, offset in ipairs(frontal_angles) do angles[#angles + 1] = heading + math.rad(offset) end
        diagnostics = { floors = 0, placements = 0, penetrations = 0, obstructions = 0, trace = "not_tried" }
    else
        for step = 0, 11 do angles[#angles + 1] = step * math.pi / 6 end
    end
    local radii = combat_fixture and types[view].combat_radii or { 2800, 2200, 3400, 1800 }
    for _, radius in ipairs(radii) do
        for _, angle in ipairs(angles) do
            local x, y = anchor.X + math.cos(angle) * radius, anchor.Y + math.sin(angle) * radius
            local first, second
            if combat_fixture then
                first, second = safe_melee_placement(trace, host, guest, target, cohort, anchor, x, y, angle, ignored, sweep)
            else
                local dx, dy = -math.sin(angle) * 150, math.cos(angle) * 150
                first = floor_at(trace, host, anchor, x + dx, y + dy, ignored)
                second = floor_at(trace, guest, anchor, x - dx, y - dy, ignored)
            end
            if first and second and (combat_fixture or math.abs(first.Z - second.Z) <= 120) then
                if diagnostics then diagnostics.floors = diagnostics.floors + 1 end
                local ok, first_read, second_read = pcall(function()
                    if host.pawn:K2_TeleportTo(first, host.facing) ~= true
                        or not at(host.pawn, first, readback_tolerance)
                        or guest.pawn:K2_TeleportTo(second, guest.facing) ~= true then return end
                    if not same(host.pawn:GetWorld(), world) or not same(guest.pawn:GetWorld(), world) then return end
                    local host_at, guest_at = at(host.pawn, first, readback_tolerance),
                        at(guest.pawn, second, readback_tolerance)
                    if host_at and guest_at and distance(host_at, anchor) <= approach_radius
                        and distance(guest_at, anchor) <= approach_radius then
                        if sweep then
                            diagnostics.placements = diagnostics.placements + 1
                            diagnostics.trace, diagnostics.component, diagnostics.impact =
                                forward_melee_trace(trace, target, guest, guest_at, sweep)
                            if diagnostics.trace == "start_penetrating" then
                                diagnostics.penetrations = diagnostics.penetrations + 1
                            elseif diagnostics.trace == "other_hit" then
                                diagnostics.obstructions = diagnostics.obstructions + 1
                            end
                            if diagnostics.trace ~= "guest_hit" then return end
                        end
                        return host_at, guest_at
                    end
                end)
                if ok and first_read and second_read then
                    host.pawn:ForceNetUpdate()
                    guest.pawn:ForceNetUpdate()
                    return distance(first_read, anchor), distance(second_read, anchor), diagnostics
                end
                if not ok and diagnostics then diagnostics.trace = "callback_failed" end
                local restored = true
                for _, player in ipairs({ host, guest }) do
                    local checked, result = pcall(restore_position, player)
                    restored = restored and checked and result ~= nil and result ~= false
                end
                if not restored then error("mutation_unverified_stock_husk_approach_restore", 0) end
            end
        end
    end
    if diagnostics then
        local impact = diagnostics.impact or {}
        error(string.format("stock_%s_approach_no_verified_position floors=%d placements=%d penetrations=%d obstructions=%d trace=%s component=%s origin=(%.1f,%.1f,%.1f) impact=(%s,%s,%s)",
            view, diagnostics.floors, diagnostics.placements, diagnostics.penetrations, diagnostics.obstructions,
            diagnostics.trace, diagnostics.component or "-", anchor.X, anchor.Y, anchor.Z,
            tostring(impact.time or "-"), tostring(impact.distance or "-"), tostring(impact.depth or "-")), 0)
    end
    error("stock_husk_approach_no_safe_floor_pair", 0)
end

local function light_pistol(state, pawn)
    local weapon = pawn.EquipedItem
    if exact(weapon, "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C") and weapon:HasAuthority()
        and not weapon:IsActorBeingDestroyed() and same(weapon:GetWorld(), state.world) and same(weapon:GetOwner(), pawn)
        and same(weapon:GetInstigator(), pawn) and same(weapon.User, pawn) and same(weapon.Player_Ref, pawn)
        and weapon.HeldByPlayer == true and pawn.IsWeaponBlocked == false then return weapon end
end

local function verified_pistol_guid(guest, detached, guid)
    if type(guid) ~= "string" or not guid:match("^[0-9A-F]+$") or #guid ~= 32 or not guid:find("[^0]")
        or type(SilverfishInspectNativeGuestWeapon) ~= "function" then return false end
    local checked, equipped, status, live, stored = pcall(SilverfishInspectNativeGuestWeapon,
        guest.pawn:GetAddress(), detached:GetAddress(), guid)
    return checked and equipped == true and status == "equipped" and finite(live) and live >= 2 and live == stored
end

local function flayer_projectile_aim(state, host, guest, target, cohort)
    local pawn, controller = guest.pawn, guest.controller_object
    local weapon, camera = light_pistol(state, pawn), pawn.Camera
    local muzzle = valid(weapon) and weapon.Arrow_Muzzle
    local capsule = target.CapsuleComponent
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local original = placement(pawn)
    if not original or not valid(trace) or not valid(weapon) or target.Alive ~= true
        or not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn)
        or not exact(muzzle, "/Script/Engine.ArrowComponent") or not same(muzzle:GetOwner(), weapon)
        or not exact(capsule, "/Script/Engine.CapsuleComponent") or not same(capsule:GetOwner(), target) then
        error("stock_flayer_shot_exact_pistol_or_components_unqualified", 0)
    end
    local control_pose, eye_pose, muzzle_pose = rotation(controller:GetControlRotation()),
        rotation(camera.RelativeRotation), rotation(muzzle.RelativeRotation)
    local anchor = point(capsule:K2_GetComponentLocation())
    if not anchor or not control_pose or not eye_pose or not muzzle_pose then error("stock_flayer_shot_pose_unqualified", 0) end
    local ignored = {host.pawn, pawn, weapon}
    for _, member in ipairs(cohort) do ignored[#ignored + 1] = member end
    local function toward(from, to)
        local dx, dy, dz = to.X - from.X, to.Y - from.Y, to.Z - from.Z
        return {Pitch = math.deg(math.atan(dz, math.sqrt(dx^2 + dy^2))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}
    end
    local moved, floors, rays = false, 0, 0
    local function verified_reach(floor)
        local aim = toward(floor, anchor)
        if pawn:K2_TeleportTo(floor, {Pitch = 0, Yaw = aim.Yaw, Roll = 0}) ~= true then return end
        moved = true
        local eye = point(camera:K2_GetComponentLocation())
        if not eye or not at(pawn, floor, 18) then return end
        local view_aim = toward(eye, anchor)
        controller:SetControlRotation(view_aim)
        camera:K2_SetWorldRotation(view_aim, false, {}, true)
        local from = point(muzzle:K2_GetComponentLocation())
        if not from or distance(eye, from) > 180 then return end
        muzzle:K2_SetWorldRotation(toward(from, anchor), false, {}, true)
        local forward = point(muzzle:GetForwardVector())
        local reach = distance(from, anchor)
        if not forward or reach < 300 or reach > 660 then return end
        local ray = {X = from.X + (reach + 10) * forward.X, Y = from.Y + (reach + 10) * forward.Y,
            Z = from.Z + (reach + 10) * forward.Z}
        local hit = {}
        rays = rays + 1
        local found = trace:LineTraceSingle(pawn, from, ray, 0, true, {pawn, weapon}, 0, hit, true, trace_color, trace_color, 0)
        hit = hit.OutHit or hit
        local component = hit.Component and hit.Component:Get()
        if found == true and valid(component) and same(component:GetOwner(), target)
            and hit.PenetrationDepth == 0 and finite(hit.Time) and hit.Time > 0 and hit.Time <= 1 then
            pawn:ForceNetUpdate()
            return reach
        end
    end
    local applied, result = pcall(function()
        local bearing = math.atan(original.origin.Y - anchor.Y, original.origin.X - anchor.X)
        for _, radius in ipairs({455, 550, 385}) do
            for _, offset in ipairs({0, 45, -45, 90, -90, 135, -135, 180}) do
                local angle = bearing + math.rad(offset)
                local floor = floor_at(trace, original, anchor, anchor.X + radius * math.cos(angle),
                    anchor.Y + radius * math.sin(angle), ignored)
                if floor and math.abs(floor.Z - anchor.Z) <= 120 then
                    floors = floors + 1
                    local reach = verified_reach(floor)
                    if reach then return reach end
                end
            end
        end
    end)
    local function restore_pose()
        if not moved then return end
        local restored, located = pcall(function()
            if not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, controller)
                or not same(pawn.Camera, camera) or not same(pawn.EquipedItem, weapon) then return false end
            local found = pawn:K2_TeleportTo(original.origin, original.facing)
            controller:SetControlRotation(control_pose)
            camera:K2_SetRelativeRotation(eye_pose, false, {}, true)
            muzzle:K2_SetRelativeRotation(muzzle_pose, false, {}, true)
            pawn:ForceNetUpdate()
            return found == true and at(pawn, original.origin, 18)
                and same_rotation(rotation(pawn:K2_GetActorRotation()), original.facing)
                and same_rotation(rotation(controller:GetControlRotation()), control_pose)
                and same_rotation(rotation(camera.RelativeRotation), eye_pose)
                and same_rotation(rotation(muzzle.RelativeRotation), muzzle_pose)
        end)
        if not restored or not located then error("mutation_unverified_stock_flayer_shot_pose_restore", 0) end
    end
    if applied and result then return result, restore_pose end
    restore_pose()
    if not applied then error("stock_flayer_shot_probe_failed: " .. tostring(result), 0) end
    error(("stock_flayer_shot_no_stock_lane floors=%d rays=%d"):format(floors, rays), 0)
end

function campaign_npc.before_guest_fire(state, phase, name, guid)
    local ticket = shot_ticket
    if not ticket then return end
    shot_ticket, shot_aim = nil, nil
    if not fixture or view ~= "flayer" or phase ~= "hosting" or state.map ~= map or not valid(state.world)
        or ticket.world ~= state.world:GetAddress() or os.time() > ticket.expires then
        return nil, "stock_flayer_shot_ticket_expired"
    end
    local host, guest, detached = paired(state, phase)
    if not host then return nil, "stock_flayer_shot_pair_" .. guest end
    if (name ~= ticket.shooter or name ~= guest.name or guid ~= ticket.guid or guest.pawn:GetAddress() ~= ticket.pawn
        or guest.controller_object:GetAddress() ~= ticket.controller or detached:GetAddress() ~= ticket.detached
        or guest.controller_object.Player:GetAddress() ~= ticket.connection) then
        return nil, "stock_flayer_shot_ticket_owner_changed"
    end
    local weapon = light_pistol(state, guest.pawn)
    local target, cohort = stock_member(state.world, host.pawn, ticket.victim)
    if not valid(weapon) or weapon:GetAddress() ~= ticket.weapon or not verified_pistol_guid(guest, detached, guid) then
        return nil, "stock_flayer_shot_ticket_equipment_changed"
    end
    if not target or target:GetAddress() ~= ticket.target then return nil, "stock_flayer_shot_ticket_member_changed" end
    local posed, reach, restore = pcall(flayer_projectile_aim, state, host, guest, target, cohort)
    if not posed then return nil, tostring(reach) end
    return function()
        restore()
        shot_aim = {world = ticket.world, victim = ticket.victim, shooter = name, reach = reach, state = "applied"}
    end
end

function campaign_npc.command(fields, state, phase)
    if fields[2] == "native-campaign-npc-refresh" then
        local mode = fields[3]
        if not refresh_fixture or view ~= "husk" or #fields ~= 3 or (mode ~= "inspect" and mode ~= "refresh")
            or type(SilverfishStockCampaignNpcRefresh) ~= "function" then error("stock_husk_refresh_fixture_unavailable", 0) end
        local host = paired(state, phase)
        if not host then error("stock_husk_refresh_exact_pair_unavailable", 0) end
        local receipt = table.pack(SilverfishStockCampaignNpcRefresh(host.pawn:GetAddress(), mode == "refresh"))
        if receipt.n ~= 10 then error("stock_husk_refresh_receipt_unavailable", 0) end
        for index = 1, receipt.n do if receipt[index] == "" then receipt[index] = "-" end end
        refresh = { world = state.world:GetAddress(), mode = mode, receipt = receipt }
        return "stock Husk director result reported"
    end
    if fields[2] ~= "native-campaign-npc-approach" then return end
    local projectile = #fields == 5 and fields[4] == "projectile"
    local returning = #fields == 4 and fields[4] == "return"
    local ambient = production_roster and state.map == dead_bridge and headless_fixtures and (#fields == 3 or returning)
    if returning and not ambient then error("stock_husk_return_fixture_disabled", 0) end
    if (not ambient and (not fixture or (view == "notdeer" and not combat_fixture))) or (#fields ~= 3 and not projectile and not returning) or (projectile and (view ~= "flayer"
        or type(SilverfishReadCampaignFlayerProjectile) ~= "function")) then error("stock_husk_approach_fixture_disabled", 0) end
    if not returning then approach = nil end
    if projectile then shot_ticket, shot_aim = nil, nil end
    local host, guest, detached = paired(state, phase, ambient)
    if not host then
        error(projectile and "stock_flayer_shot_pair_" .. guest or "stock_husk_approach_exact_pair_unavailable", 0)
    end
    if returning then
        local origins = approach and approach.origins
        if not origins or approach.world ~= state.world:GetAddress() or approach.member ~= fields[3]
            or not same(host.pawn, origins[1].pawn) or not same(guest.pawn, origins[2].pawn) then
            error("stock_husk_return_pair_unavailable", 0)
        end
        local first = restore_position(origins[1])
        local second = restore_position(origins[2])
        if not first or not second then error("mutation_unverified_stock_husk_return", 0) end
        for _, player in ipairs(origins) do player.pawn:ForceNetUpdate() end
        approach.origins = nil
        return "verified headless pair return to both exact original positions"
    end
    local target, cohort = stock_member(state.world, host.pawn, fields[3], state.map)
    if not target then error("stock_husk_approach_cohort_member_unavailable", 0) end
    if projectile then
        local weapon = light_pistol(state, guest.pawn)
        if not valid(weapon) or not verified_pistol_guid(guest, detached, fields[5]) then
            error("stock_flayer_shot_exact_equipped_guid_required", 0)
        end
        shot_ticket = {world = state.world:GetAddress(), shooter = guest.name, victim = fields[3], guid = fields[5],
            pawn = guest.pawn:GetAddress(), controller = guest.controller_object:GetAddress(), detached = detached:GetAddress(),
            connection = guest.controller_object.Player:GetAddress(), target = target:GetAddress(),
            weapon = weapon:GetAddress(), expires = os.time() + 8}
        shot_aim = {world = shot_ticket.world, victim = shot_ticket.victim, shooter = shot_ticket.shooter, state = "armed"}
        return "exact stock light pistol shot fixture armed"
    end
    local first, second = placement(host.pawn), placement(guest.pawn)
    if not first or not second then error("stock_husk_approach_capsule_unavailable", 0) end
    if combat_fixture and not ambient then
        local flayer = view == "flayer"
        local family = view == "husk" and {} or { view }
        if (flayer and not detached_sickness) or type(SilverfishArmCampaignNpcCombat) ~= "function"
            or not SilverfishArmCampaignNpcCombat(host.pawn:GetAddress(), guest.pawn:GetAddress(),
                detached:GetAddress(), target:GetAddress(), table.unpack(family)) then
            error("stock_" .. view .. "_combat_scope_unavailable", 0)
        end
    end
    local placed, host_distance, guest_distance, geometry = pcall(place_pair, state.world, first, second, target, cohort)
    if not placed then
        if combat_fixture and type(SilverfishClearCampaignNpcCombat) == "function" then SilverfishClearCampaignNpcCombat() end
        error(host_distance, 0)
    end
    approach = { world = state.world:GetAddress(), member = fields[3], host = host.name, guest = guest.name,
        host_distance = host_distance, guest_distance = guest_distance, geometry = geometry,
        origins = ambient and { first, second } or nil }
    return "verified headless pair placement near an exact stock " .. view
end

local function family_rows(state, pawn, host, status_row, family, topic)
    local receipts, stage, spawner, stock_data, cohort, replicated, spawn_denials, ai_denials, natural_calls,
        authority, simulated, other, simulated_controllers, antfarm, roster, saved_calls, saved_denials,
        saved_stage, saved_spawner, saved_data, authority_network_ready, local_begins, local_initializations,
        early_denials, last_actor, last_caller, last_script, replica = read_family(pawn, host, family, state.map)
    if type(receipts) ~= "number" then return {} end
    local viewer = host and "host" or "client"
    local rows = {
        status_row(topic, state.map, viewer, receipts, stage, spawner ~= "" and spawner or "-",
            stock_data ~= "" and stock_data or "-", cohort, replicated, spawn_denials),
        status_row(topic .. "-census", state.map, viewer, natural_calls, authority, simulated, other,
            simulated_controllers, ai_denials, antfarm),
    }
    if topic == "native-campaign-npc" and type(saved_calls) == "number" and type(saved_denials) == "number"
        and type(saved_stage) == "string"
        and type(saved_spawner) == "string" and type(saved_data) == "string" then
        rows[#rows + 1] = status_row("native-campaign-npc-saved-squads", state.map, viewer, saved_calls, saved_denials,
            saved_stage, saved_spawner ~= "" and saved_spawner or "-", saved_data ~= "" and saved_data or "-")
    end
    if type(authority_network_ready) == "number" and type(local_begins) == "number"
        and type(local_initializations) == "number" and type(early_denials) == "number"
        and type(last_actor) == "string" and type(last_caller) == "string" and type(last_script) == "string" then
        rows[#rows + 1] = status_row(topic .. "-network", state.map, viewer, authority_network_ready,
            local_begins, local_initializations, early_denials, last_actor ~= "" and last_actor or "-",
            last_caller ~= "" and last_caller or "-", last_script ~= "" and last_script or "-")
    end
    for actor in roster:gmatch("[^\n]+") do
        rows[#rows + 1] = status_row(topic .. "-member", state.map, viewer, actor)
    end
    if topic == "native-campaign-npc" and family == "notdeer" then
        rows[#rows + 1] = status_row("native-campaign-npc-notdeer", state.map, viewer,
            not host and type(replica) == "string" and replica ~= "" and replica or "-")
    end
    return rows
end

local function append_approach(rows, state, status_row)
    if not approach or approach.world ~= active or state.local_pawn.name ~= approach.host
        or not state.guests or #state.guests ~= 1 or state.guests[1].name ~= approach.guest then return end
    rows[#rows + 1] = status_row("native-campaign-npc-approach", state.map, approach.member,
        approach.host, approach.guest, approach.host_distance, approach.guest_distance)
    local geometry = approach.geometry
    if geometry then
        rows[#rows + 1] = status_row("native-campaign-npc-approach-geometry", state.map, approach.member, types[view].trace_sweep,
            geometry.floors, geometry.placements, geometry.trace, geometry.component)
        local impact = geometry.impact
        rows[#rows + 1] = status_row("native-campaign-npc-approach-impact", state.map, approach.member,
            impact.time, impact.distance, impact.depth)
    end
end

local function read_rows(state, phase, status_row)
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    local pawn = state.local_pawn and state.local_pawn.pawn
    local ambient = production_roster and ambient_worlds[state.map]
    if (state.map ~= map and not ambient) or not state.listening or not (host or client)
        or not valid(state.world) or not valid(pawn) then
        -- During initial load a native receipt can precede the first bridge poll.
        -- Only clear state after leaving a world that the bridge observed.
        if active then campaign_npc.clear() end
        return {}
    end
    if type(SilverfishReadCampaignNpc) ~= "function" then return {} end
    local rows = family_rows(state, pawn, host, status_row, ambient and "husk" or view, "native-campaign-npc")
    if #rows == 0 then return rows end
    active = state.world:GetAddress()
    if host and type(SilverfishReadCampaignNpcHostRoster) == "function" then
        local husks, boars, defeated
        if ambient then husks, boars, defeated = SilverfishReadCampaignNpcHostRoster(pawn:GetAddress(), state.map)
        else husks, boars, defeated = SilverfishReadCampaignNpcHostRoster(pawn:GetAddress()) end
        if type(husks) == "number" then
            rows[#rows + 1] = status_row("native-campaign-npc-host-roster", state.map, husks, boars, defeated)
        end
    end
    if ambient then
        for _, row in ipairs(family_rows(state, pawn, host, status_row, "boar", "native-campaign-npc-boar")) do
            rows[#rows + 1] = row
        end
        if host then append_approach(rows, state, status_row) end
        if state.map == "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P" then
            for _, row in ipairs(family_rows(state, pawn, host, status_row, "ambient", "native-campaign-npc-ambient")) do
                rows[#rows + 1] = row
            end
            if type(SilverfishReadRuinDirectorDiagnostic) == "function" then
                local trace = SilverfishReadRuinDirectorDiagnostic(pawn:GetAddress(), host)
                if type(trace) == "string" then
                    for line in trace:gmatch("[^\n]+") do
                        local fields = {}
                        for field in line:gmatch("[^\t]+") do fields[#fields + 1] = field end
                        if #fields == 9 then
                            rows[#rows + 1] = status_row("native-campaign-npc-ruin-director", state.map,
                                host and "host" or "client", table.unpack(fields))
                        end
                    end
                end
            end
        end
        return rows
    end
    if shot_ticket then
        local guest = state.guests and #state.guests == 1 and state.guests[1]
        local controller = guest and guest.controller_object
        local connection = valid(controller) and controller.Player
        if os.time() > shot_ticket.expires or shot_ticket.world ~= active or not guest or not valid(guest.pawn)
            or guest.pawn:GetAddress() ~= shot_ticket.pawn or not valid(connection)
            or connection:GetAddress() ~= shot_ticket.connection then shot_ticket, shot_aim = nil, nil end
    end
    local secondary = view == "flayer" and "husk" or "flayer"
    for _, row in ipairs(family_rows(state, pawn, host, status_row, secondary, "native-campaign-npc-" .. secondary)) do
        rows[#rows + 1] = row
    end
    if host and refresh and refresh.world == active then
        rows[#rows + 1] = status_row("native-campaign-npc-refresh", map, refresh.mode,
            table.unpack(refresh.receipt, 1, refresh.receipt.n))
    end
    if host and combat_fixture and type(SilverfishReadCampaignNpcCombat) == "function" then
        local receipt = table.pack(SilverfishReadCampaignNpcCombat())
        if receipt.n == 14 then
            for index = 1, receipt.n do if receipt[index] == "" then receipt[index] = "-" end end
            rows[#rows + 1] = status_row("native-campaign-npc-combat", map, table.unpack(receipt, 1, receipt.n))
        end
        if type(SilverfishReadCampaignNpcCombatDiagnostic) == "function" then
            local diagnostic = table.pack(SilverfishReadCampaignNpcCombatDiagnostic())
            if diagnostic.n == 9 then
                rows[#rows + 1] = status_row("native-campaign-npc-combat-diagnostic", map, table.unpack(diagnostic))
            end
        end
        if view == "flayer" and type(SilverfishReadCampaignNpcCombatSickness) == "function" then
            local infection = table.pack(SilverfishReadCampaignNpcCombatSickness())
            if infection.n == 13 then
                rows[#rows + 1] = status_row("native-campaign-npc-melee-sickness", map, table.unpack(infection))
            end
        end
    end
    if host and type(SilverfishReadCampaignFlayerProjectile) == "function" then
        local diagnostic = SilverfishReadCampaignFlayerProjectile(-1)
        if type(diagnostic) == "string" then
            rows[#rows + 1] = status_row("native-campaign-flayer-projectile-status", state.map, diagnostic)
        end
        for age = 0, 15 do
            local hit = table.pack(SilverfishReadCampaignFlayerProjectile(age))
            if hit.n ~= 7 then break end
            rows[#rows + 1] = status_row("native-campaign-flayer-projectile", state.map, table.unpack(hit, 1, hit.n))
        end
        if shot_aim and shot_aim.world == active then
            rows[#rows + 1] = status_row("native-campaign-flayer-shot-aim", state.map, shot_aim.victim, shot_aim.shooter,
                shot_aim.reach or "-", shot_aim.state)
        end
    end
    if host then append_approach(rows, state, status_row) end
    return rows
end

function campaign_npc.rows(state, phase, status_row)
    if not enabled then return {} end
    local ok, result = pcall(read_rows, state, phase, status_row)
    if ok then return result end
    return { status_row("native-campaign-npc-diagnostic", state.map, phase, tostring(result):sub(1, 512)) }
end

return campaign_npc
