local projection = {}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local stock_class = "/Game/Classes/Actors/BoneBag_BP.BoneBag_BP_C"
local maximum_bags = 128
local trace_color = { R = 0, G = 0, B = 0, A = 1 }
local current

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function bag_id(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9a-f]+$") ~= nil
end

local function guest_id(value)
    if type(value) ~= "string" or #value ~= 36 then return false end
    local compact = value:sub(1, 8) .. value:sub(10, 13) .. value:sub(15, 18) .. value:sub(20, 23) .. value:sub(25)
    return value:sub(9, 9) == "-" and value:sub(14, 14) == "-" and value:sub(19, 19) == "-"
        and value:sub(24, 24) == "-" and bag_id(compact)
end

local function coordinate(value)
    return type(value) == "number" and value == value and math.abs(value) <= 1e8
end

local function object_path(object)
    return valid(object) and object:GetFullName():match("^%S+%s+(/Game/%S+)$")
end

local function context(state, phase, owner)
    if not enabled or type(state) ~= "table" or state.listening ~= true then return end
    local host = phase == "hosting" and state.server == false and owner == "host"
    local guest = phase == "connected" and state.server == true and guest_id(owner)
    if not host and not guest then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    local world_name, pawn_name = object_path(state.world), object_path(pawn)
    if not world_name or not pawn_name or not pawn:IsLocallyControlled() then return end
    return pawn_name, world_name, host
end

local function ground_z(state, xyz)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(library) then return xyz[3] end
    local ignore = {}
    for _, member in ipairs(state.pawns or {}) do
        if valid(member.pawn) then ignore[#ignore + 1] = member.pawn end
    end
    local x, y, z = table.unpack(xyz)
    local top, bottom = z + 15, z - 145
    local hit = {}
    local found = library:LineTraceSingle(state.local_pawn.pawn, { X = x, Y = y, Z = top },
        { X = x, Y = y, Z = bottom }, 0, false, ignore, 0, hit, true, trace_color, trace_color, 0)
    hit = hit.OutHit or hit
    local point, normal = hit.ImpactPoint, hit.ImpactNormal
    if found == true and point and normal and coordinate(point.Z) and coordinate(normal.Z)
        and point.Z < top - 1 and point.Z >= bottom and normal.Z >= 0.65 then
        return point.Z + 3
    end
    return z
end

function projection.clear()
    current = nil
    if enabled and type(SilverfishClearNativeBagProjection) == "function" then SilverfishClearNativeBagProjection() end
end

local function failure(detail)
    projection.clear()
    return false, detail
end

function projection.sync(state, phase, owner, epoch, durable)
    local pawn, world, host = context(state, phase, owner)
    if not pawn or not bag_id(epoch) or type(durable) ~= "table" then return failure("projection_scope_unavailable") end
    if type(SilverfishSetNativeBagProjectionScope) ~= "function" or type(SilverfishShowNativeBagProjection) ~= "function"
        or type(SilverfishRemoveNativeBagProjection) ~= "function" then return failure("projection_hook_unavailable") end

    local rows, ids = {}, {}
    for id, bag in pairs(durable) do
        if not bag_id(id) or type(bag) ~= "table" or bag.owner_id ~= owner or type(bag.position) ~= "table"
            or type(bag.position.map) ~= "string" or type(bag.position.location) ~= "table"
            or type(bag.count) ~= "number" or not math.tointeger(bag.count) or bag.count < 1 then
            return failure("projection_row_invalid")
        end
        local xyz = bag.position.location
        if #xyz ~= 3 or not coordinate(xyz[1]) or not coordinate(xyz[2]) or not coordinate(xyz[3]) then
            return failure("projection_position_invalid")
        end
        if bag.position.map == state.map then
            rows[id] = xyz
            ids[#ids + 1] = id
        end
    end
    if #ids > maximum_bags then return failure("projection_capacity") end
    table.sort(ids)

    if current and (current.owner ~= owner or current.epoch ~= epoch or current.pawn ~= pawn or current.world ~= world) then
        projection.clear()
    end
    if not valid(StaticFindObject(stock_class)) then LoadAsset(stock_class) end
    local bound, detail = SilverfishSetNativeBagProjectionScope(pawn, world, host, epoch)
    if bound ~= true then return failure(detail) end

    local old = current and current.rows or {}
    current = { owner = owner, epoch = epoch, pawn = pawn, world = world, map = state.map, rows = {}, requested = #ids,
        status = "projected" }
    for id, prior in pairs(old) do
        local wanted = rows[id]
        if not wanted or wanted[1] ~= prior[1] or wanted[2] ~= prior[2] or wanted[3] ~= prior[3] then
            SilverfishRemoveNativeBagProjection(id)
            old[id] = nil
        end
    end
    for _, id in ipairs(ids) do
        local xyz = rows[id]
        local z = old[id] and old[id][4] or ground_z(state, xyz)
        local ok, why = SilverfishShowNativeBagProjection(id, xyz[1], xyz[2], xyz[3], z)
        if ok == true then current.rows[id] = { xyz[1], xyz[2], xyz[3], z } else current.status = why end
    end
    return current.status == "projected", current.status
end

function projection.focused_target(pawn)
    if not current or not valid(pawn) or type(SilverfishFocusedNativeBagProjection) ~= "function" then return end
    local id = SilverfishFocusedNativeBagProjection(pawn:GetAddress())
    if bag_id(id) and current.rows[id] then return id end
end

function projection.status()
    if not current or type(SilverfishReadNativeBagProjection) ~= "function" then return end
    local active, epoch, count, intent, sequence, fixture = SilverfishReadNativeBagProjection()
    if active ~= true or epoch ~= current.epoch then return end
    return { owner = current.owner, epoch = epoch, map = current.map, count = count, requested = current.requested,
        status = current.status, bag = intent, sequence = sequence, fixture = fixture }
end

function projection.rows(state, phase, status_row)
    if not current then return {} end
    local pawn, world = context(state, phase, current.owner)
    if pawn ~= current.pawn or world ~= current.world or state.map ~= current.map then
        projection.clear()
        return {}
    end
    local status = projection.status()
    if not status then
        projection.clear()
        return {}
    end
    local rows = { status_row("native-bag-projection", status.map, status.epoch, status.owner,
        status.count, status.requested, status.status) }
    if bag_id(status.bag) and status.sequence > 0 then
        rows[#rows + 1] = status_row("native-bag-projection-interaction", status.map, status.epoch, status.bag,
            status.sequence, status.fixture and "fixture" or "stock")
    end
    return rows
end

function projection.command(fields, state, phase)
    local command = fields[2]
    if command == "native-bag-projection-clear" then
        if #fields ~= 2 then error("projection_clear_arguments", 0) end
        projection.clear()
        return "native backpack projection cleared"
    elseif command == "native-bag-projection-sync" then
        local owner, epoch, map = fields[3], fields[4], fields[5]
        if #fields < 5 or (#fields - 5) % 5 ~= 0 or #fields > 5 + maximum_bags * 5 or map ~= state.map then
            error("projection_snapshot_arguments", 0)
        end
        local rows = {}
        for index = 6, #fields, 5 do
            local id = fields[index]
            if not bag_id(id) or rows[id] then error("projection_snapshot_bag_id_invalid", 0) end
            rows[id] = { owner_id = owner, count = tonumber(fields[index + 4]), position = { map = map,
                location = { tonumber(fields[index + 1]), tonumber(fields[index + 2]), tonumber(fields[index + 3]) } } }
        end
        local ok, detail = projection.sync(state, phase, owner, epoch, rows)
        if not ok then error(detail, 0) end
        return "native backpack projection synchronized"
    elseif command == "native-bag-projection-fixture" then
        local id = fields[3]
        if not headless or (phase == "hosting" and not host_fixtures) or #fields ~= 3 or not current or not current.rows[id]
            or not context(state, phase, current.owner)
            or type(SilverfishProbeNativeBagProjection) ~= "function" then error("projection_fixture_unavailable", 0) end
        local ok, detail = SilverfishProbeNativeBagProjection(id)
        if not ok then error(detail, 0) end
        return "stock backpack interaction intercepted"
    end
end

return projection
