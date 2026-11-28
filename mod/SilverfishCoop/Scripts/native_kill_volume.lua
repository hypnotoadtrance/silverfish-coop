local volume_guard = {}
local death = require("native_death")
local guest_save = require("guest_save")
local players = require("players")
local transport = require("native_transport")

local area = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local world_path = area .. ".CrossRoads_P"
local volume_path = world_path .. ":PersistentLevel.KillVolume_BP_C_0"
local spawn_path = world_path .. ":PersistentLevel.PlayerStart_2"
local volume_class = "/Game/Classes/Actors/KillVolume_BP.KillVolume_BP_C"
local capsule_class = "/Script/Engine.CapsuleComponent"
local movement_class = "/Script/Engine.CharacterMovementComponent"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local native_authority = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local color = {R = 0, G = 0, B = 0, A = 0}
local near_offsets = {{110, 0}, {-110, 0}, {0, 110}, {0, -110}, {115, 115}, {115, -115}, {-115, 115}, {-115, -115}}
local fallback_offsets = {{0, 0}, {110, 0}, {-110, 0}, {0, 110}, {0, -110}}
local ground_sample_interval = 0.75
local current

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function finite(value) return type(value) == "number" and value == value and math.abs(value) < 100000000 end
local function vector(value) return value and finite(value.X) and finite(value.Y) and finite(value.Z) end
local function rotation(value) return value and finite(value.Pitch) and finite(value.Yaw) and finite(value.Roll) end
local function squared(a, b) return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 end

local function active(state, phase)
    return enabled and state.map == area and valid(state.world) and state.world:GetFullName() == "World " .. world_path
        and (phase == "hosting" and state.listening and not state.server or phase == "connected" and state.server)
end

local function placed(state, path, class)
    local actor = StaticFindObject(path)
    if exact(actor, class) and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
        and actor:GetFullName():match("^%S+ (.+)$") == path and not actor:IsActorBeingDestroyed() then return actor end
end

local function stock_box(state)
    local volume = placed(state, volume_path, volume_class)
    if not valid(volume) then return end
    local box = volume.Box
    if not exact(box, "/Script/Engine.BoxComponent") or not same(box:GetOwner(), volume)
        or box:GetFullName() ~= "BoxComponent " .. volume_path .. ".Box" then return end
    local position, extent = box:K2_GetComponentLocation(), box:GetScaledBoxExtent()
    if not vector(position) or not vector(extent) or math.abs(position.Z + 22574.027039) > 1 or extent.Z <= 0
        or extent.Z > 1000 or math.min(extent.X, extent.Y) < 1000 then return end
    return {volume = volume, box = box, top = position.Z + extent.Z, center = position, extent = extent}
end

local function authority(state, phase)
    if not native_authority then return end
    local detail, fresh = transport.scoped_host(state, phase)
    local host = detail and detail.pawn
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = exact(host, pawn_class) and valid(statics) and statics:GetGameInstance(host)
    local save = exact(instance, instance_class) and instance.SaveSlot_Ref
    if not fresh or not exact(save, save_class) or not same(host.Save, save) or not same(instance["As Player FP"], host) then return end
    return {name = detail.name, pawn = host, save = save}
end

local function member(state, phase, detail, host, ground_only)
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not pawn:HasAuthority() or pawn:IsActorBeingDestroyed()
        or pawn.Dead ~= false or not same(pawn:GetWorld(), state.world) or pawn:GetFullName() ~= detail.name then return end
    local save, standing
    if detail.kind == "local" and same(pawn, host.pawn) and detail.name == host.name then
        save, standing = host.save, players.is_downed(pawn) == false
    elseif detail.kind == "guest" then
        local binding = ground_only and guest_save.observed_item_context or guest_save.exact_item_context
        local qualified, exact_pawn, detached = pcall(binding, state, phase, ground_only and detail or detail.name)
        if not qualified or not same(pawn, exact_pawn) or not exact(detached, save_class)
            or same(detached, host.save) or not same(pawn.Save, detached) then return end
        save, standing = detached, players.is_standing_guest(pawn, detail.name)
    else
        return
    end
    local body, movement = pawn.CapsuleComponent, pawn.CharacterMovement
    if not exact(body, capsule_class) or not same(body:GetOwner(), pawn)
        or not exact(movement, movement_class) or not same(movement:GetOwner(), pawn) then return end
    local radius, height, here = body:GetScaledCapsuleRadius(), body:GetScaledCapsuleHalfHeight(), pawn:K2_GetActorLocation()
    if not finite(radius) or radius <= 0 or radius > 100 or not finite(height) or height < radius or height > 200
        or not vector(here) then return end
    return {pawn = pawn, name = detail.name, save = save, radius = radius, height = height, here = here, movement = movement,
        standing = standing, grounded = movement:IsMovingOnGround() == true,
        downed = not standing and pawn.HoldingCrouch == true and pawn.JumpMaxCount == 0}
end

local function verified_floor(victim, geometry, anchor, offsets, occupants, reach)
    local query = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(query) or not vector(anchor) then return end
    local ignored = {victim.pawn, geometry.volume}
    for _, person in ipairs(occupants) do ignored[#ignored+1] = person.pawn end
    for _, offset in ipairs(offsets) do
        local x, y = anchor.X + offset[1], anchor.Y + offset[2]
        local from, to = {X = x, Y = y, Z = anchor.Z + 140}, {X = x, Y = y, Z = anchor.Z - 380}
        local hit = {}
        local found = query:LineTraceSingle(victim.pawn, from, to, 0, false, ignored, 0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        if found == true and vector(hit.ImpactPoint) and vector(hit.ImpactNormal) and hit.ImpactNormal.Z >= 0.7
            and hit.ImpactPoint.Z > geometry.top + 400 and hit.ImpactPoint.Z < from.Z - 1 and hit.ImpactPoint.Z >= to.Z then
            local candidate = {X = x, Y = y, Z = hit.ImpactPoint.Z + victim.height + 4}
            local clear = not reach or squared(candidate, anchor) <= reach^2
            for _, other in ipairs(occupants) do
                if math.abs(candidate.Z - other.here.Z) < victim.height + other.height
                    and (candidate.X-other.here.X)^2 + (candidate.Y-other.here.Y)^2 < (victim.radius+other.radius+16)^2 then
                    clear = false
                    break
                end
            end
            local up = {X = x, Y = y, Z = candidate.Z + 2}
            if clear and query:CapsuleTraceSingle(victim.pawn, candidate, up, victim.radius, victim.height,
                0, false, ignored, 0, {}, true, color, color, 0) == false then return candidate end
        end
    end
end

local function safe_spot(state, victim, geometry, everyone)
    local neighbors = {}
    for _, person in ipairs(everyone) do
        if not same(person.pawn, victim.pawn) then neighbors[#neighbors+1] = person end
    end
    for _, person in ipairs(neighbors) do
        if person.standing and person.grounded then
            local spot = verified_floor(victim, geometry, person.here, near_offsets, neighbors, 220)
            if spot then return spot, "teammate" end
        end
    end
    local last = current.ground[victim.name]
    if last and same(last.pawn, victim.pawn) and same(last.save, victim.save) and os.time() - last.time <= 30 then
        local spot = verified_floor(victim, geometry, last.point, fallback_offsets, neighbors)
        if spot then return spot, "last-ground" end
    end
    local marker = placed(state, spawn_path, "/Script/Engine.PlayerStart")
    local start = valid(marker) and marker:K2_GetActorLocation()
    if vector(start) and squared(start, {X = -15907.666949, Y = 18404.667169, Z = 617.030527}) <= 1 then
        local spot = verified_floor(victim, geometry, start, fallback_offsets, neighbors)
        if spot then return spot, "player-start" end
    end
end

local function relocate(victim, geometry, target)
    local pawn, facing = victim.pawn, victim.pawn:K2_GetActorRotation()
    if not rotation(facing) or pawn:K2_TeleportTo(target, facing) ~= true then return false end
    victim.movement:StopMovementImmediately()
    local moved = pawn:K2_GetActorLocation()
    if not vector(moved) or squared(moved, target) > 10^2 or geometry.box:IsOverlappingActor(pawn) ~= false then
        error("unsafe_recovery_readback", 0)
    end
    pawn:ForceNetUpdate()
    return true
end

local function sample_members(state, phase, geometry, lead, ground_only)
    local everyone, members, sampled = {}, {}, {}
    for _, detail in ipairs(state.pawns) do
        local person = lead and member(state, phase, detail, lead, ground_only)
        if person then
            local address = person.pawn:GetAddress()
            if members[address] then error("ambiguous_authority_pawn", 0) end
            everyone[#everyone+1], members[address] = person, person
            local ground = current.ground[person.name]
            if person.grounded and person.here.Z > geometry.top + 400 then
                ground = {pawn = person.pawn, save = person.save, time = os.time(), point = person.here}
            end
            sampled[person.name] = ground
        end
    end
    current.ground = sampled
    return everyone, members
end

local function service(state, phase, geometry, lead, ground_only)
    local everyone, members = sample_members(state, phase, geometry, lead, ground_only)
    local moved, pending, cursor = false, false, 0
    for _ = 1, 5 do
        local sequence, address, source, expired = SilverfishNextNativeKillVolumeCrossing(cursor)
        if sequence == 0 then break end
        if type(sequence) ~= "number" or sequence <= cursor or geometry.volume:GetAddress() ~= source then
            error("unqualified_crossing", 0)
        end
        cursor = sequence
        if ground_only then
            -- A stock callback may queue a crossing while sampling. Freshly qualify
            -- all participants before repositioning a pawn or changing its save.
            everyone, members = sample_members(state, phase, geometry, authority(state, phase), false)
            ground_only = false
        end
        local person = members[address]
        if person then
            local target, method = safe_spot(state, person, geometry, everyone)
            if target and relocate(person, geometry, target) then
                local confirmed, at_emission = death.publish_kill_volume_down(state, phase, person.pawn, person.save, person.downed)
                if not confirmed or SilverfishFinishNativeKillVolumeCrossing(sequence, address) ~= true then error("unverified_downed_event", 0) end
                current.recovered[person.name] = {sequence = sequence, method = method, target = target, pawn = person.pawn,
                    projected = person.downed, at = os.time(), health = at_emission}
                if current.fixture and same(current.fixture.pawn, person.pawn) then current.fixture = nil end
                person.here, person.standing = person.pawn:K2_GetActorLocation(), false
                moved = true
            elseif expired then
                error("safe_floor_unavailable", 0)
            else
                pending = true
            end
        elseif expired then
            -- No stock mutation is allowed while the exact pawn has no campaign owner.
            error("crossing_owner_unavailable", 0)
        else
            pending = true
        end
    end
    if current.fixture and os.time() - current.fixture.released >= 5 then error("fixture_stock_crossing_unobserved", 0) end
    current.pending = pending or current.fixture ~= nil
    for name, result in pairs(current.recovered) do
        local person = valid(result.pawn) and members[result.pawn:GetAddress()]
        if not person or person.name ~= name then
            if result.projected then current.recovered[name] = nil end
        elseif person.downed then
            result.projected = true
        end
        if not result.projected and os.time() - result.at >= 6 then error("recovered_downed_projection_unverified", 0) end
    end
    return moved
end

local function awaiting_downed_projection()
    for _, result in pairs(current.recovered) do
        if not result.projected then return true end
    end
    return false
end

function volume_guard.sync(state, phase)
    if not active(state, phase) then current = nil; return false end
    local driver = state.world.NetDriver
    if not current or not same(current.world, state.world) or not same(driver, current.driver) or phase ~= current.phase then
        current = {world = state.world, driver = driver, phase = phase, ground = {}, recovered = {}, next_ground = 0}
    end
    if current.fault then return false end
    if type(SilverfishReadNativeKillVolume) ~= "function" then return false end
    local read, armed, overlaps, reloads = pcall(SilverfishReadNativeKillVolume)
    if not read or not armed then return false end
    current.overlaps, current.reloads = overlaps, reloads
    if phase ~= "hosting" then current.armed = true; return false end
    local ok, moved = pcall(function()
        local now = os.clock()
        local crossing = SilverfishNextNativeKillVolumeCrossing(0)
        -- Sample fallback ground periodically. A crossing or pending recovery uses live geometry and ownership immediately.
        local ground_only = crossing == 0 and not current.pending and not awaiting_downed_projection()
        if ground_only and now < current.next_ground then return false end
        current.next_ground = now + ground_sample_interval
        local geometry = stock_box(state)
        current.armed = geometry ~= nil
        if not geometry then
            if crossing ~= 0 then error("stock_volume_geometry_unavailable", 0) end
            return false
        end
        return service(state, phase, geometry, authority(state, phase), ground_only)
    end)
    if not ok then
        current.fault = type(moved) == "string" and moved:match("^[a-z_]+$") or "recovery_bridge_unavailable"
        return false
    end
    return moved
end

function volume_guard.blocked(state, phase)
    return current and phase == "hosting" and current.phase == phase and same(current.world, state.world)
        and (current.fault ~= nil or current.pending == true)
end

function volume_guard.capture_blocked(state, phase)
    if volume_guard.blocked(state, phase) then return true end
    if not current or phase ~= "hosting" or not same(current.world, state.world) then return false end
    return awaiting_downed_projection()
end

function volume_guard.command(fields, state, phase)
    if fields[2] ~= "native-kill-volume-fall-fixture" then return end
    local host = active(state, phase) and authority(state, phase)
    local geometry = host and stock_box(state)
    if #fields ~= 3 or not fixtures or not geometry or not current or not current.armed or volume_guard.blocked(state, phase) then
        error("stock_kill_volume_fixture_unavailable", 0)
    end
    local who
    for _, detail in ipairs(state.pawns) do
        if detail.name == fields[3] then who = member(state, phase, detail, host); break end
    end
    if not who or not who.standing or not who.grounded or who.here.Z <= geometry.top + 400 then
        error("stock_kill_volume_fixture_standing_floor_required", 0)
    end
    local facing = who.pawn:K2_GetActorRotation()
    local start = {X = geometry.center.X, Y = geometry.center.Y, Z = geometry.top + who.height + 120}
    if not rotation(facing) or who.pawn:K2_TeleportTo(start, facing) ~= true then error("stock_kill_volume_fixture_drop_refused", 0) end
    local landed = who.pawn:K2_GetActorLocation()
    if geometry.box:IsOverlappingActor(who.pawn) ~= false or not vector(landed) or squared(landed, start) > 10^2 then
        local rolled_back = who.pawn:K2_TeleportTo(who.here, facing)
        landed = who.pawn:K2_GetActorLocation()
        if rolled_back ~= true or not vector(landed) or squared(landed, who.here) > 10^2 then
            current.fault = "fixture_drop_rollback_unverified"
        end
        error("stock_kill_volume_fixture_drop_unverified", 0)
    end
    current.ground[who.name] = {pawn = who.pawn, save = who.save, time = os.time(), point = who.here}
    current.fixture, current.pending = {pawn = who.pawn, released = os.time()}, true
    who.movement:StopMovementImmediately()
    who.movement:SetMovementMode(3, 0) -- Stock MOVE_Falling. Physics enters the placed Box on a later tick.
    who.pawn:ForceNetUpdate()
    return "exact pawn released above the stock CrossRoads kill volume"
end

function volume_guard.rows(state, phase, row)
    if not active(state, phase) or not current or not same(current.world, state.world) then return {} end
    local status = current.fault and "fault" or current.pending and "pending" or current.armed and "armed" or "arming"
    local rows = {row("native-kill-volume", area, "KillVolume_BP_C " .. volume_path, status,
        current.overlaps or 0, current.reloads or 0, current.fault or "-")}
    if phase == "hosting" then
        for name, recovery in pairs(current.recovered) do
            local target = recovery.target
            rows[#rows+1] = row("native-kill-volume-recovered", name, recovery.sequence, recovery.method,
                target.X, target.Y, target.Z, recovery.health or "-")
        end
    end
    return rows
end

return volume_guard
