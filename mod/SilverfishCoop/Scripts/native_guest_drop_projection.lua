local projection = {}
local world_path = require("world_path")
local drop_items = require("native_guest_drop_items")
local pickup_fixture = require("native_authored_pickup_input_fixture")
local pickup_audio = require("native_pickup_audio")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local title_fixtures = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
local color = { R = 0, G = 0, B = 0, A = 1 }
local current
local world_items, slot_count = {}, 64
local carry_physics = type(SilverfishSetNativeWorldLootPhysics) == "function"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function projection.refresh_stock_inventory(state, backpack)
    local controller = state.controller
    local stack = valid(controller) and controller.MenuStack
    local screen = stack and stack:GetArrayNum() == 1 and stack[1] or nil
    local class = StaticFindObject("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")
    if valid(screen) and same(screen:GetClass(), class) and same(screen:GetOwningPlayer(), controller) then
        if backpack then screen.BackpackPanel:FullRefresh() end
        screen.FloorPanel:FullRefresh()
    end
end

local function identifier(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") ~= nil
end

local function coordinate(value)
    return type(value) == "number" and value == value and math.abs(value) <= 1e7
end

local function local_pawn(state, phase)
    local leaf = world_path.leaf(state.map)
    local active = (phase == "connected" and state.server == true) or (phase == "hosting" and state.server == false)
    if not enabled or not active or not leaf or state.listening ~= true then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if valid(pawn) and valid(state.world) and state.world:GetFullName() == "World " .. state.map .. "." .. leaf
        and same(pawn:GetWorld(), state.world) and pawn:IsLocallyControlled() then
        return pawn
    end
end

function projection.floor_z(state, source)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(library) then return source[3] end
    local ignored = {}
    for _, member in ipairs(state.pawns or {}) do
        if valid(member.pawn) then ignored[#ignored + 1] = member.pawn end
    end
    local x, y, z = table.unpack(source)
    local top, bottom = z + 15, z - 145
    local hit = {}
    local found = library:LineTraceSingle(state.local_pawn.pawn, { X = x, Y = y, Z = top },
        { X = x, Y = y, Z = bottom }, 0, false, ignored, 0, hit, true, color, color, 0)
    hit = hit.OutHit or hit
    local point, normal = hit.ImpactPoint, hit.ImpactNormal
    local surface = hit.Component and hit.Component:Get()
    if found == true and valid(surface) and surface:GetCollisionObjectType() == 0 and point and normal
        and coordinate(point.Z) and coordinate(normal.Z) and point.Z < top - 1 and point.Z >= bottom and normal.Z >= .65 then
        return point.Z + 3
    end
    return z
end

function projection.clear()
    current = nil
    if enabled and type(SilverfishClearNativeGuestDropProjection) == "function" then
        for _, entry in pairs(world_items) do SilverfishClearNativeGuestDropProjection(entry.slot) end
        world_items = {}
        return SilverfishClearNativeGuestDropProjection()
    end
    return true
end

local function proof()
    if not current or type(SilverfishReadNativeGuestDropProjection) ~= "function" then return end
    local active, epoch, id, actor, sequence = SilverfishReadNativeGuestDropProjection()
    if active == true and current.epoch == epoch and current.id == id
        and drop_items.actor_path(current.class, current.map, world_path.leaf(current.map), actor) then
        return actor, sequence
    end
end

function projection.take_press(pawn)
    if not valid(pawn) or type(SilverfishTakeNativeGuestDropProjectionPress) ~= "function" then return end
    local epoch, id = SilverfishTakeNativeGuestDropProjectionPress(pawn:GetAddress())
    if not identifier(epoch) or not identifier(id) then return end
    local item = world_items[id]
    if item and item.epoch == epoch and same(item.pawn, pawn) then
        local active, observed_epoch, observed_id, actor, sequence = SilverfishReadNativeGuestDropProjection(item.slot)
        if active and observed_epoch == epoch and observed_id == id and math.type(sequence) == "integer" and sequence > 0 then
            local qualified = drop_items.actor_path(item.class, item.map, world_path.leaf(item.map), actor)
            return "world-loot:" .. epoch .. ":" .. id .. ":" .. tostring(sequence), qualified and actor or nil
        end
        return
    end
    if current and same(pawn, current.pawn) and current.epoch == epoch and current.id == id then
        local actor = proof()
        return "stack-drop:" .. epoch .. ":" .. id, actor
    end
end

function projection.authority_sight(state, phase, pawn, id, position)
    local viewer = phase == "hosting" and local_pawn(state, phase)
    local item = viewer and world_items[id]
    if not item or not same(viewer, item.pawn) or not same(state.world, item.world)
        or type(SilverfishReadNativeGuestDropProjection) ~= "function" then return false end
    local active, epoch, exact, name = SilverfishReadNativeGuestDropProjection(item.slot)
    if active ~= true or epoch ~= item.epoch or exact ~= id or not drop_items.actor_path(item.class, item.map, world_path.leaf(item.map), name) then
        return false
    end
    local path = name:match("^%S+ (.+)$")
    local actor = path and StaticFindObject(path)
    if not valid(actor) or actor:GetFullName() ~= name then return false end
    local point = actor:K2_GetActorLocation()
    if not point or not coordinate(point.X) or not coordinate(point.Y) or not coordinate(point.Z)
        or (point.X - position.X)^2 + (point.Y - position.Y)^2 + (point.Z - position.Z)^2 > 5^2 then return false end
    return pickup_fixture.stock_item_sight(pawn, actor)
end

local function projected_item(actor)
    if not carry_physics or not valid(actor) or not actor:HasAnyFlags(EObjectFlags.RF_Transient) then return end
    local name = actor:GetFullName()
    for id, entry in pairs(world_items) do
        if entry.actor_name == name and same(actor:GetWorld(), entry.world) then return id, entry end
    end
end

function projection.carry_actor(id)
    local entry = world_items[id]
    if not carry_physics or not entry or not entry.carryable or entry.moving then return end
    local active, epoch, exact, name = SilverfishReadNativeGuestDropProjection(entry.slot)
    local path = active and epoch == entry.epoch and exact == id and name:match("^%S+ (.+)$")
    local actor = path and StaticFindObject(path)
    if valid(actor) and actor:GetFullName() == name and same(actor:GetWorld(), entry.world) then return actor end
end

function projection.arm_carry(pawn, actor)
    local id, entry = projected_item(actor)
    if not entry or not entry.carryable or entry.moving or not same(pawn:GetWorld(), entry.world) then return end
    if entry.motion and entry.motion.holder then return same(entry.motion.holder, pawn) and id or nil end
    local active, epoch, exact, observed = SilverfishReadNativeGuestDropProjection(entry.slot)
    if not active or epoch ~= entry.epoch or exact ~= id or observed ~= entry.actor_name then return end
    if SilverfishSetNativeWorldLootPhysics(entry.slot, epoch, id, true) then
        entry.motion = {actor = actor, holder = pawn, quiet = 0}
        return id
    end
end

function projection.stop_carry(pawn, actor)
    local id, entry = projected_item(actor)
    if not entry or not entry.motion or not same(entry.motion.holder, pawn) then return end
    if SilverfishSetNativeWorldLootPhysics(entry.slot, entry.epoch, id, false) then entry.motion = nil end
end

function projection.carry_body(actor)
    local _, entry = projected_item(actor)
    if entry and entry.motion and not entry.motion.settled and actor.Physics == true then return actor.Box end
end

function projection.start_carry(pawn, actor)
    if not valid(pawn) or not valid(actor) or not same(pawn.HeldObject, actor)
        or not same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box) then return end
    local id = projection.arm_carry(pawn, actor)
    if not id then return end
    local arrow = pawn.Prop_Arrow:K2_GetComponentRotation()
    local pivot = actor.Box:K2_GetComponentLocation()
    pawn.PhysicsHandle:ReleaseComponent()
    pawn.PhysicsHandle:GrabComponentAtLocationWithRotation(actor.Box, FName("None", EFindName.FNAME_Find),
        pivot, { Pitch = 0, Yaw = arrow.Yaw, Roll = 0 })
    if not same(pawn.PhysicsHandle:GetGrabbedComponent(), actor.Box) then
        projection.stop_carry(pawn, actor)
        return
    end
    return id
end

local function motion_pose(actor)
    local point, rotation, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
    local pose = {point.X, point.Y, point.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, scale.X, scale.Y, scale.Z}
    for _, axis in ipairs(pose) do if not coordinate(axis) then return end end
    return pose
end

local function motion_row(item, id, row)
    local motion = item.motion
    if not motion or not valid(motion.actor) or motion.actor:IsActorBeingDestroyed() then return end
    local actor, holder = motion.actor, motion.holder
    local now = os.clock()
    if not motion.settled and now >= (motion.next_sample or 0) then
        motion.next_sample = now + .07
        motion.pose = motion_pose(actor)
        if valid(holder) and same(holder.HeldObject, actor) then
            motion.quiet, motion.released = 0, nil
        else
            motion.holder = nil
            motion.released = motion.released or now
            local bone = FName("None", EFindName.FNAME_Find)
            local speed = actor.Box:GetPhysicsLinearVelocity(bone)
            local spin = actor.Box:GetPhysicsAngularVelocityInDegrees(bone)
            local still = speed.X^2 + speed.Y^2 + speed.Z^2 < 9 and spin.X^2 + spin.Y^2 + spin.Z^2 < 25
            motion.quiet = still and motion.quiet + 1 or 0
            if motion.pose and (motion.quiet >= 3 or now - motion.released >= 5) then
                motion.settled = SilverfishSetNativeWorldLootPhysics(item.slot, item.epoch, id, false) == true
            end
        end
    end
    if motion.pose then
        return row("native-world-loot-physics", item.map, item.epoch, id, item.class,
            motion.settled and "settled" or "moving", table.unpack(motion.pose))
    end
end

local function same_pose(first, second)
    for i = 1, 9 do if math.abs(first[i] - second[i]) > .001 then return false end end
    return true
end

local function start_drop_fall(id, entry)
    local actor = projection.carry_actor(id)
    local pose = valid(actor) and motion_pose(actor)
    if not pose or SilverfishSetNativeWorldLootPhysics(entry.slot, entry.epoch, id, true) ~= true then
        return
    end
    entry.motion = {actor = actor, pose = pose, quiet = 0, released = os.clock()}
    -- Give the stock Box the same wake-up as an ordinary inventory drop.
    pcall(function() actor.Box:WakeAllRigidBodies() end)
end

local function append_world_rows(rows, state, phase, row, pawn, epoch_filter)
    for id, item in pairs(world_items) do
        if item.map ~= state.map or not same(pawn, item.pawn) or not same(state.world, item.world) then
            if SilverfishClearNativeGuestDropProjection(item.slot) then world_items[id] = nil end
        elseif epoch_filter and item.epoch ~= epoch_filter then
            -- An older projection may still be retiring from the same world.
        elseif item.error then
            rows[#rows + 1] = row("native-world-loot-projection-error", item.map, item.epoch, id, item.class, item.error)
        else
            local active, epoch, observed, name, press = SilverfishReadNativeGuestDropProjection(item.slot)
            if active and epoch == item.epoch and observed == id then
                if item.carryable then item.actor_name = name end
                local fields = {"native-world-loot-item", item.map, epoch, id, pawn:GetFullName(), name, press, item.class}
                if phase == "connected" and item.moving then fields[#fields + 1] = "moving" end
                rows[#rows + 1] = row(table.unpack(fields))
                if phase == "hosting" and item.motion then
                    rows[#rows + 1] = motion_row(item, id, row)
                end
            end
        end
    end
end

function projection.rows(state, phase, row)
    local rows, pawn = {}, local_pawn(state, phase)
    local shared_pickups = valid(pawn) and type(SilverfishSyncNativeWorldLootItem) == "function"
    if shared_pickups then
        rows[#rows + 1] = row("native-world-loot-capability", 2)
        if phase == "hosting" then rows[#rows + 1] = row("native-world-loot-host-presentation", 1) end
    end
    if current and (current.map ~= state.map or not same(pawn, current.pawn) or not same(state.world, current.world)) then
        current = nil
        SilverfishClearNativeGuestDropProjection()
    end
    local actor, sequence = proof()
    if actor then
        rows[#rows + 1] = row("native-guest-drop-projection", current.map, current.epoch, current.id,
            current.pawn:GetFullName(), actor, sequence)
    end
    append_world_rows(rows, state, phase, row, pawn)
    return rows, shared_pickups
end

local function item_frame(fields, state, phase)
    local area, epoch = fields[3], fields[4]
    local pawn = local_pawn(state, phase)
    if not pawn or area ~= state.map or not identifier(epoch) or #fields > slot_count + 4
        or type(SilverfishSyncNativeWorldLootItem) ~= "function" then
        error("world_loot_frame_scope_unavailable", 0)
    end
    local incoming, occupied = {}, {}
    for index = 5, #fields do
        local values = {}
        for value in fields[index]:gmatch("[^|]+") do values[#values + 1] = value end
        local id, class = values[1], values[2]
        local mode = #values == 13 and values[3]
        local snap, moving = mode == "ground" or mode == "carry-ground", mode == "moving"
        local fall = mode == "fall" and phase == "hosting"
        local carryable = mode == "carry" or mode == "carry-ground" or fall
        local stack_count = math.tointeger(values[#values])
        if (#values ~= 12 and not snap and not moving and not carryable) or not identifier(id) or incoming[id]
            or not stack_count or stack_count < 0 or stack_count > 2147483647
            or not drop_items.stock_actor_class(class) then error("world_loot_item_identity_invalid", 0) end
        local transform = {}
        for axis = mode and 4 or 3, #values - 1 do
            local value = tonumber(values[axis])
            if not coordinate(value) then error("world_loot_item_transform_invalid", 0) end
            transform[#transform + 1] = value
        end
        incoming[id] = { class = class, transform = transform, snap = snap, moving = moving, fall = fall,
            carryable = carryable, stack_count = stack_count }
    end
    local changed = false
    for id, entry in pairs(world_items) do
        if not incoming[id] or entry.epoch ~= epoch or not same(entry.pawn, pawn) then
            if SilverfishClearNativeGuestDropProjection(entry.slot) then
                world_items[id] = nil
                changed = changed or not entry.error and not entry.moving
            end
        end
        if world_items[id] then occupied[entry.slot] = true end
    end
    local errors = 0
    for id, item in pairs(incoming) do
        local previous = world_items[id]
        local slot = previous and previous.slot
        if not slot then
            for index = 1, slot_count do
                if not occupied[index] then slot = index; break end
            end
        end
        if slot then
            occupied[slot] = true
            if (not previous or previous.class ~= item.class) and not valid(StaticFindObject(item.class)) then
                pcall(LoadAsset, item.class)
            end
            local motion = previous and previous.motion
            if motion and motion.settled and not item.fall and not item.snap
                and same_pose(motion.pose, item.transform) then motion = nil end
            local t = motion and motion.pose or item.transform
            local visual_z = t[3]
            if item.snap and not motion then
                local same_origin = previous and t[1] == previous.x and t[2] == previous.y and t[3] == previous.z
                visual_z = same_origin and previous.visual_z or projection.floor_z(state, t)
            end
            local ok, reason = SilverfishSyncNativeWorldLootItem(pawn:GetAddress(), epoch, id,
                t[1], t[2], visual_z, visual_z, item.class, t[4], t[5], t[6], t[7], t[8], t[9], item.stack_count,
                not item.moving, slot)
            local entry = { map = area, epoch = epoch, pawn = pawn, world = state.world,
                class = item.class, slot = slot, visual_z = visual_z, x = t[1], y = t[2], z = t[3],
                stack_count = item.stack_count, moving = item.moving, carryable = item.carryable, motion = motion,
                actor_name = previous and previous.actor_name,
                error = not ok and (reason or "stock_drop_projection_unavailable") or nil }
            world_items[id] = entry
            if ok and item.fall and not motion then
                pcall(start_drop_fall, id, entry)
                if not entry.motion then entry.error = "stock_drop_projection_fall_unavailable" end
            end
            if item.fall and entry.motion and not entry.motion.settled then entry.moving = true end
            local was_pickable = previous ~= nil and not previous.error and not previous.moving
            local pickable = not entry.error and not entry.moving
            changed = changed or was_pickable ~= pickable or (was_pickable and previous.stack_count ~= item.stack_count)
            if entry.error then errors = errors + 1 end
        end
    end
    if changed then pcall(projection.refresh_stock_inventory, state, false) end
    if errors > 0 then return "stock world loot synchronized with projection errors" end
    return "stock world loot synchronized"
end

function projection.frame_reply(fields, state, phase, row)
    local pawn = local_pawn(state, phase)
    local accepted, message = pcall(item_frame, fields, state, phase)
    local records = {row("native-world-loot-frame-reply", 1, fields[1], phase, state.map,
        pawn and pawn:GetFullName() or "-", fields[4] or "-", accepted and "accepted" or "error", message)}
    if accepted then append_world_rows(records, state, phase, row, pawn, fields[4]) end
    return table.concat(records, "\n") .. "\n"
end

local function fixture(pawn, proof_for)
    local displayed, before = proof_for()
    if not headless or not displayed or type(before) ~= "number"
        or type(SilverfishProbeNativeInteractInput) ~= "function" then error("stock_drop_projection_fixture_scope_unavailable", 0) end
    local path = displayed:match("^%S+ (/[A-Za-z0-9_./:%-]+)$")
    local actor = path and StaticFindObject(path)
    local box = valid(actor) and actor.Box
    local camera = pawn.Camera
    if not valid(actor) or actor:GetFullName() ~= displayed or not same(actor:GetWorld(), pawn:GetWorld())
        or not valid(box) or not valid(camera) then error("stock_drop_projection_fixture_components_unavailable", 0) end

    local rotation = camera.RelativeRotation
    local eye, point = camera:K2_GetComponentLocation(), box:K2_GetComponentLocation()
    if not rotation or not coordinate(rotation.Pitch) or not coordinate(rotation.Yaw) or not coordinate(rotation.Roll)
        or not eye or not coordinate(eye.X) or not coordinate(eye.Y) or not coordinate(eye.Z)
        or not point or not coordinate(point.X) or not coordinate(point.Y) or not coordinate(point.Z) then
        error("stock_drop_projection_fixture_pose_unavailable", 0)
    end
    local original = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    local dx, dy, dz = point.X - eye.X, point.Y - eye.Y, point.Z - eye.Z
    local aimed = { Pitch = math.deg(math.atan(dz, math.sqrt(dx*dx+dy*dy))), Yaw = math.deg(math.atan(dy, dx)), Roll = 0 }
    local succeeded, detail = pcall(function()
        camera:K2_SetWorldRotation(aimed, false, {}, true)
        pawn:LookTrace()
        local focused = pawn.InteractionTarget_Component
        if not same(focused, box) then
            error(string.format("stock_drop_projection_fixture_focus_unverified: eye=(%.1f,%.1f,%.1f), "
                .. "box=(%.1f,%.1f,%.1f), focus=%s", eye.X, eye.Y, eye.Z, point.X, point.Y, point.Z,
                valid(focused) and focused:GetFullName() or "none"), 0)
        end
        local ok, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if not ok then error(status, 0) end
        local exact, after = proof_for()
        if status ~= "stock_started_consumed" or exact ~= displayed or type(after) ~= "number" or after <= before then
            error("stock_drop_projection_fixture_interaction_veto_unverified", 0)
        end
    end)
    local reset = pcall(function()
        if not same(pawn.Camera, camera) then error("fixture camera changed", 0) end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        local actual = camera.RelativeRotation
        if not actual or math.abs(actual.Pitch - original.Pitch) > .05 or math.abs(actual.Yaw - original.Yaw) > .05
            or math.abs(actual.Roll - original.Roll) > .05 then error("fixture camera restore unverified", 0) end
        pawn:LookTrace()
    end)
    if not reset then
        projection.clear()
        error("stock_drop_projection_fixture_camera_restore_unverified", 0)
    end
    if not succeeded then error(detail, 0) end
    return "stock drop interaction intercepted"
end

local function retire(state, phase, epoch, id)
    local pawn, entry = local_pawn(state, phase), world_items[id]
    if not pawn or not identifier(epoch) or not identifier(id)
        or (entry and (entry.epoch ~= epoch or not same(entry.pawn, pawn) or not same(entry.world, state.world))) then
        error("world_loot_retirement_scope_unavailable", 0)
    end
    if entry then
        if not SilverfishClearNativeGuestDropProjection(entry.slot) then error("world_loot_retirement_pending", 0) end
        world_items[id] = nil
        pcall(projection.refresh_stock_inventory, state, phase == "hosting")
    end
    return "stock world loot retired"
end

local function present_host_claim(fields, state, phase)
    local epoch, id, area, audio_epoch, cue = fields[3], fields[4], fields[5], fields[6], fields[7]
    local entry, pawn = world_items[id], local_pawn(state, phase)
    local class = cue and cue:match("^%x+|([^|]+)|")
    if #fields ~= 7 or phase ~= "hosting" or not pawn or area ~= state.map
        or not identifier(epoch) or not identifier(id) or not identifier(audio_epoch) or not class then
        error("world_loot_retirement_scope_unavailable", 0)
    end
    local audible = entry and entry.epoch == epoch and same(entry.pawn, pawn) and same(entry.world, state.world)
        and entry.class == class and not entry.error and not entry.moving
    if audible then
        local ok, active, reported_epoch, reported_id = pcall(SilverfishReadNativeGuestDropProjection, entry.slot)
        audible = ok and active == true and reported_epoch == epoch and reported_id == id
    end
    local removed, outcome = pcall(retire, state, phase, epoch, id)
    local heard = ""
    if audible then
        -- A projected item is already loaded. An unresolved audio asset must fall back
        -- to normal playback outside this game-thread command.
        local ok, receipt = pcall(pickup_audio.command,
            {fields[1], "native-sound-pickup", area, audio_epoch, cue}, state, phase, true)
        if ok then heard = receipt end
    end
    local status = removed and "retired" or tostring(outcome):match("^world_loot_retirement_[%l_]+$")
        or "world_loot_retirement_unverified"
    return status .. "|" .. heard
end

function projection.command(fields, state, phase)
    local command = fields[2]
    if command == "native-world-loot-frame" then
        return item_frame(fields, state, phase)
    elseif command == "native-world-loot-retire" then
        if #fields ~= 4 then error("world_loot_retirement_scope_unavailable", 0) end
        return retire(state, phase, fields[3], fields[4])
    elseif command == "native-world-loot-present-host-claim" then
        return present_host_claim(fields, state, phase)
    elseif command == "native-world-loot-approach" then
        local id, pawn_name = fields[3], fields[4]
        local item, pawn = world_items[id]
        local scope = item or (current and current.id == id and current)
        if not scope or (#fields ~= 4 and #fields ~= 5) or not same(state.world, scope.world) then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        for _, member in ipairs(state.pawns) do
            if member.name == pawn_name and valid(member.pawn) and member.pawn:GetFullName() == pawn_name then
                pawn = member.pawn
                break
            end
        end
        local name = proof()
        if item then
            local active, epoch, exact, actor = SilverfishReadNativeGuestDropProjection(item.slot)
            name = active and epoch == item.epoch and exact == id and actor or nil
        end
        local path = name and name:match("^%S+ (/[A-Za-z0-9_./:%-]+)$")
        local actor = path and StaticFindObject(path)
        if not valid(pawn) or not valid(actor) or actor:GetFullName() ~= name then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        pickup_fixture.approach_world_loot(state, phase, pawn, actor, actor.Box, title_fixtures, fields[5])
        return "stock loot height and line of sight verified"
    elseif command == "native-world-loot-fixture" then
        local pawn, id = local_pawn(state, phase), fields[3]
        local item = world_items[id]
        if #fields ~= 3 or not item or not same(pawn, item.pawn) or not same(state.world, item.world) then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        local active, epoch, exact, name, before = SilverfishReadNativeGuestDropProjection(item.slot)
        local path = active and epoch == item.epoch and exact == id and name:match("^%S+ (/[A-Za-z0-9_./:%-]+)$")
        local actor = path and StaticFindObject(path)
        if not valid(actor) or actor:GetFullName() ~= name or type(before) ~= "number"
            or type(SilverfishProbeNativeInteractInput) ~= "function" then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        return pickup_fixture.press_world_loot(state, pawn, actor, actor.Box, function()
            local ok, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            local present, current_epoch, current_id, current_actor, after = SilverfishReadNativeGuestDropProjection(item.slot)
            if not ok or status ~= "stock_started_consumed" or not present or current_epoch ~= epoch
                or current_id ~= exact or current_actor ~= name or type(after) ~= "number" or after <= before then
                error("world_loot_fixture_interaction_unverified:" .. tostring(status), 0)
            end
            return "stock world loot interaction intercepted"
        end)
    elseif command == "native-guest-drop-projection-clear" then
        if #fields ~= 2 then error("stock_drop_projection_clear_arguments", 0) end
        current = nil
        if not SilverfishClearNativeGuestDropProjection() then error("stock_drop_projection_retirement_pending", 0) end
        return "stock drop projection cleared"
    elseif command == "native-guest-drop-projection-fixture" then
        local pawn = local_pawn(state, phase)
        if #fields ~= 3 or not pawn or not current or fields[3] ~= current.id or not same(pawn, current.pawn) then
            error("stock_drop_projection_fixture_scope_unavailable", 0)
        end
        return fixture(pawn, proof)
    elseif command == "native-guest-drop-projection-sync" then
        local pawn = local_pawn(state, phase)
        local area, epoch, id = fields[3], fields[4], fields[5]
        local source = { tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8]) }
        local class = fields[9] or drop_items.ammo
        if (#fields ~= 8 and #fields ~= 9) or not drop_items.profiles[class]
            or area ~= state.map or not pawn or not identifier(epoch) or not identifier(id) or not coordinate(source[1])
            or not coordinate(source[2]) or not coordinate(source[3])
            or type(SilverfishSyncNativeGuestDropProjection) ~= "function" then
            projection.clear()
            error("stock_drop_projection_snapshot_unqualified", 0)
        end
        local cached = current and same(pawn, current.pawn) and current.map == area and current.epoch == epoch and current.id == id
            and current.class == class
            and source[1] == current.source[1] and source[2] == current.source[2] and source[3] == current.source[3]
        if not cached then
            current = nil
            SilverfishClearNativeGuestDropProjection()
        end
        if not valid(StaticFindObject(class)) then LoadAsset(class) end
        local visual_z = cached and current.visual_z or floor_z(state, source)
        local extra = class ~= drop_items.ammo and {class} or {}
        local ok, detail = SilverfishSyncNativeGuestDropProjection(pawn:GetAddress(), epoch, id,
            source[1], source[2], source[3], visual_z, table.unpack(extra))
        if not ok then
            current = nil
            SilverfishClearNativeGuestDropProjection()
            error(detail, 0)
        end
        current = { pawn = pawn, world = state.world, map = area, epoch = epoch, id = id, class = class,
            source = source, visual_z = visual_z }
        return "stock drop projection synchronized"
    end
end

return projection
