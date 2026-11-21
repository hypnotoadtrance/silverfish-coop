local projection = {}
local maps = require("native_guest_drop_maps")
local drop_items = require("native_guest_drop_items")
local pickup_fixture = require("native_authored_pickup_input_fixture")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local color = { R = 0, G = 0, B = 0, A = 1 }
local current
local world_items, slot_count = {}, 64

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function identifier(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") ~= nil
end

local function coordinate(value)
    return type(value) == "number" and value == value and math.abs(value) <= 1e7
end

local function local_pawn(state, phase)
    local leaf = type(state.map) == "string" and state.map:match("^/Game/Classes/Maps/[%w_/%-]*/([%w_%-]+)$")
    local active = (phase == "connected" and state.server == true) or (phase == "hosting" and state.server == false)
    if not enabled or not active or not leaf or state.listening ~= true then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if valid(pawn) and valid(state.world) and state.world:GetFullName() == "World " .. state.map .. "." .. leaf
        and same(pawn:GetWorld(), state.world) and pawn:IsLocallyControlled() then
        return pawn
    end
end

local function floor_z(state, source)
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
        and drop_items.actor_path(current.class, current.map, maps[current.map], actor) then
        return actor, sequence
    end
end

function projection.take_press(pawn)
    if not valid(pawn) or type(SilverfishTakeNativeGuestDropProjectionPress) ~= "function" then return end
    local epoch, id = SilverfishTakeNativeGuestDropProjectionPress(pawn:GetAddress())
    if not identifier(epoch) or not identifier(id) then return end
    local item = world_items[id]
    if item and item.epoch == epoch and same(item.pawn, pawn) then return "world-loot:" .. epoch .. ":" .. id end
    if current and same(pawn, current.pawn) and current.epoch == epoch and current.id == id then
        return "stack-drop:" .. epoch .. ":" .. id
    end
end

function projection.rows(state, phase, row)
    local rows, pawn = {}, local_pawn(state, phase)
    if pawn and type(SilverfishSyncNativeWorldLootItem) == "function" then
        rows[#rows + 1] = row("native-world-loot-capability", 2)
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
    for id, item in pairs(world_items) do
        if item.map ~= state.map or not same(pawn, item.pawn) or not same(state.world, item.world) then
            if SilverfishClearNativeGuestDropProjection(item.slot) then world_items[id] = nil end
        else
            local active, epoch, observed, name, press = SilverfishReadNativeGuestDropProjection(item.slot)
            if active and epoch == item.epoch and observed == id then
                rows[#rows + 1] = row("native-world-loot-item", item.map, epoch, id, pawn:GetFullName(), name, press, item.class)
            end
        end
    end
    return rows
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
        local snap = #values == 12 and values[3] == "ground"
        if (#values ~= 11 and not snap) or not identifier(id) or incoming[id] or #class > 256
            or not class:match("^/Game/Classes/[%w_./%-]+_C$") then error("world_loot_item_identity_invalid", 0) end
        local transform = {}
        for axis = snap and 4 or 3, #values do
            local value = tonumber(values[axis])
            if not coordinate(value) then error("world_loot_item_transform_invalid", 0) end
            transform[#transform + 1] = value
        end
        incoming[id] = { class = class, transform = transform, snap = snap }
    end
    for id, entry in pairs(world_items) do
        if not incoming[id] or entry.epoch ~= epoch or not same(entry.pawn, pawn) then
            if SilverfishClearNativeGuestDropProjection(entry.slot) then world_items[id] = nil end
        end
        if world_items[id] then occupied[entry.slot] = true end
    end
    local errors = {}
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
            if not valid(StaticFindObject(item.class)) then LoadAsset(item.class) end
            local t = item.transform
            local visual_z = previous and previous.visual_z
            if not visual_z then visual_z = item.snap and floor_z(state, t) or t[3] end
            local ok, reason = SilverfishSyncNativeWorldLootItem(pawn:GetAddress(), epoch, id,
                t[1], t[2], visual_z, visual_z, item.class, t[4], t[5], t[6], t[7], t[8], t[9], slot)
            world_items[id] = { map = area, epoch = epoch, pawn = pawn, world = state.world,
                class = item.class, slot = slot, visual_z = visual_z }
            if not ok then errors[#errors + 1] = reason end
        end
    end
    if #errors > 0 then error(errors[1], 0) end
    return "stock world loot synchronized"
end

local function fixture(pawn, proof_for)
    local displayed, before = proof_for()
    if not headless or not displayed or type(before) ~= "number"
        or type(SilverfishProbeNativeInteractInput) ~= "function" then error("stock_drop_projection_fixture_scope_unavailable", 0) end
    local path = displayed:match("^%S+ (/Game/[%w_./:%-]+)$")
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

function projection.command(fields, state, phase)
    local command = fields[2]
    if command == "native-world-loot-frame" then
        return item_frame(fields, state, phase)
    elseif command == "native-world-loot-approach" then
        local id, pawn_name = fields[3], fields[4]
        local item, pawn = world_items[id]
        local scope = item or (current and current.id == id and current)
        if not scope or #fields ~= 4 or not same(state.world, scope.world) then
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
        local path = name and name:match("^%S+ (/Game/[%w_./:%-]+)$")
        local actor = path and StaticFindObject(path)
        if not valid(pawn) or not valid(actor) or actor:GetFullName() ~= name then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        pickup_fixture.approach_world_loot(state, phase, pawn, actor, actor.Box)
        return "stock loot height and line of sight verified"
    elseif command == "native-world-loot-fixture" then
        local pawn, id = local_pawn(state, phase), fields[3]
        local item = world_items[id]
        if #fields ~= 3 or not item or not same(pawn, item.pawn) or not same(state.world, item.world) then
            error("world_loot_fixture_scope_unavailable", 0)
        end
        local active, epoch, exact, name, before = SilverfishReadNativeGuestDropProjection(item.slot)
        local path = active and epoch == item.epoch and exact == id and name:match("^%S+ (/Game/[%w_./:%-]+)$")
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
