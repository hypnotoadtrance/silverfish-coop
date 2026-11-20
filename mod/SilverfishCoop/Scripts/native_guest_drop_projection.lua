local projection = {}
local maps = require("native_guest_drop_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local ammo_class = "/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"
local color = { R = 0, G = 0, B = 0, A = 1 }
local current

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
    local leaf = maps[state.map]
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
        return SilverfishClearNativeGuestDropProjection()
    end
    return true
end

local function proof()
    if not current or type(SilverfishReadNativeGuestDropProjection) ~= "function" then return end
    local active, epoch, id, actor, sequence = SilverfishReadNativeGuestDropProjection()
    if active == true and current.epoch == epoch and current.id == id and type(actor) == "string" then
        return actor, sequence
    end
end

function projection.take_press(pawn)
    if not current or not same(pawn, current.pawn) or type(SilverfishTakeNativeGuestDropProjectionPress) ~= "function" then return end
    local epoch, id = SilverfishTakeNativeGuestDropProjectionPress(pawn:GetAddress())
    if identifier(epoch) and identifier(id) and current.epoch == epoch and current.id == id then
        return "stack-drop:" .. epoch .. ":" .. id
    end
end

function projection.rows(state, phase, row)
    if not current then return {} end
    if current.map ~= state.map or not same(local_pawn(state, phase), current.pawn) or not same(state.world, current.world) then
        projection.clear()
        return {}
    end
    local actor, sequence = proof()
    if not actor then return {} end
    return { row("native-guest-drop-projection", current.map, current.epoch, current.id, current.pawn:GetFullName(), actor, sequence) }
end

local function fixture(pawn, id)
    local displayed, before = proof()
    if not headless or not current or current.id ~= id or not displayed or type(before) ~= "number"
        or type(SilverfishProbeNativeInteractInput) ~= "function" then error("stock_drop_projection_fixture_scope_unavailable", 0) end
    local path = displayed:match("^Ammo_Pistol_BP_C (%S+)$")
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
        if not same(pawn.InteractionTarget_Component, box) then error("stock_drop_projection_fixture_focus_unverified", 0) end
        local ok, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if not ok then error(status, 0) end
        local exact, after = proof()
        if status ~= "stock_started_consumed" or exact ~= displayed or after ~= before + 1 then
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
    if command == "native-guest-drop-projection-clear" then
        if #fields ~= 2 then error("stock_drop_projection_clear_arguments", 0) end
        if not projection.clear() then error("stock_drop_projection_retirement_pending", 0) end
        return "stock drop projection cleared"
    elseif command == "native-guest-drop-projection-fixture" then
        local pawn = local_pawn(state, phase)
        if #fields ~= 3 or not pawn or not current or not same(pawn, current.pawn) then
            error("stock_drop_projection_fixture_scope_unavailable", 0)
        end
        return fixture(pawn, fields[3])
    elseif command == "native-guest-drop-projection-sync" then
        local pawn = local_pawn(state, phase)
        local area, epoch, id = fields[3], fields[4], fields[5]
        local source = { tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8]) }
        if #fields ~= 8 or area ~= state.map or not pawn or not identifier(epoch) or not identifier(id) or not coordinate(source[1])
            or not coordinate(source[2]) or not coordinate(source[3])
            or type(SilverfishSyncNativeGuestDropProjection) ~= "function" then
            projection.clear()
            error("stock_drop_projection_snapshot_unqualified", 0)
        end
        local cached = current and same(pawn, current.pawn) and current.map == area and current.epoch == epoch and current.id == id
            and source[1] == current.source[1] and source[2] == current.source[2] and source[3] == current.source[3]
        if not cached then projection.clear() end
        if not valid(StaticFindObject(ammo_class)) then LoadAsset(ammo_class) end
        local visual_z = cached and current.visual_z or floor_z(state, source)
        local ok, detail = SilverfishSyncNativeGuestDropProjection(pawn:GetAddress(), epoch, id,
            source[1], source[2], source[3], visual_z)
        if not ok then
            projection.clear()
            error(detail, 0)
        end
        current = { pawn = pawn, world = state.world, map = area, epoch = epoch, id = id, source = source, visual_z = visual_z }
        return "stock drop projection synchronized"
    end
end

return projection
