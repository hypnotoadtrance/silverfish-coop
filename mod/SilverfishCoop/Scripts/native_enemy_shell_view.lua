local view = {}
local profiles = require("native_enemy_loot_registry")

local by_world = {}
for _, profile in pairs(profiles) do by_world["World " .. profile.world] = profile end
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_INPUT") == "1"
local host_enabled = os.getenv("SILVERFISH_COOP_NATIVE_CQC_LOOT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
        or os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1")
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local camera_path = "/Script/Engine.CameraComponent"
local mesh_actor_path = "/Script/Engine.StaticMeshActor"
local mesh_path = "/Engine/BasicShapes/Cube.Cube"
local material_path = "/Game/Assets/Materials/M_Light_Yellow.M_Light_Yellow"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local rotation = { Pitch = 0, Yaw = 0, Roll = 0 }
local scale = { X = 0.12, Y = 0.12, Z = 0.24 }
local trace_color = { R = 0, G = 0, B = 0, A = 1 }
local cue_height, frame_lifetime = 40, 2.5
local frame, widget, is_downed

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function coordinate(value)
    return type(value) == "number" and value == value and math.abs(value) <= 10000000
end

local function vector(value)
    return value and coordinate(value.X) and coordinate(value.Y) and coordinate(value.Z)
end

local function identifier(value, allowed)
    return type(value) == "string" and #value == 32 and not value:find(allowed) and value:find("[^0]")
end

local function distance_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function raw_path(profile, name)
    if not profile or type(name) ~= "string" then return end
    local prefix = profile.raw_prefix
    if name:sub(1, #prefix) == prefix and name:sub(#prefix + 1):match("^[0-9]+$") then return name:match("^%S+ (.+)$") end
end

function view.raw_retired(world, name)
    local path = valid(world) and raw_path(by_world[world:GetFullName()], name)
    return path and type(SilverfishIsNativeCqcShellRetired) == "function"
        and SilverfishIsNativeCqcShellRetired(world:GetAddress(), path) == true
end

local function viewer(state, phase)
    local profile = profiles[state.map]
    if not enabled or not profile or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. profile.world or state.listening ~= true
        or type(SilverfishTakeNativeInteractPress) ~= "function" then return end
    local hosting = phase == "hosting" and host_enabled and state.server == false
    if not hosting and (phase ~= "connected" or state.server ~= true) then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_path) or not exact(controller, controller_path)
        or pawn:HasAuthority() ~= hosting or not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or not same(detail.controller_object, controller) or not same(controller.Pawn, pawn)
        or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), state.world)
        or not same(controller:GetWorld(), state.world) or detail.name ~= pawn:GetFullName() then return end
    return pawn, controller
end

local function now(pawn)
    return StaticFindObject("/Script/Engine.Default__GameplayStatics"):GetRealTimeSeconds(pawn)
end

local function aim_point(point)
    return { X = point.X, Y = point.Y, Z = point.Z + cue_height }
end

local function in_reach(pawn, point)
    local here, reach = pawn:K2_GetActorLocation(), pawn.InteractionRange
    return vector(here) and vector(point) and coordinate(reach) and reach > 0 and reach <= 1000
        and distance_squared(here, point) <= math.min(reach, 250)^2
end

local function camera_pose(pawn)
    local camera = pawn.Camera
    if exact(camera, camera_path) and same(camera:GetOwner(), pawn) then
        local eye = camera:K2_GetComponentLocation()
        if vector(eye) then return camera, eye end
    end
end

local function trace_visibility(pawn, eye, target, physical)
    local library = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not valid(library) then return false end
    local ignore, held = { pawn }, pawn.EquipedItem
    if valid(held) and held.HeldByPlayer == true and same(held:GetOwner(), pawn) then
        ignore[#ignore + 1] = held
    end
    local hit = {}
    local obstructed = library:LineTraceSingle(pawn, eye, target, physical and 1 or 0, physical == true,
        ignore, 0, hit, true, trace_color, trace_color, 0)
    if obstructed == false then return true end
    local impact = (hit.OutHit or hit).ImpactPoint
    return physical == true and obstructed == true and vector(impact) and distance_squared(target, impact) <= 4
end

local function unobstructed(pawn, eye, target)
    return trace_visibility(pawn, eye, target, false)
end

-- The remote guest's camera direction is local. Authority checks its own pawn's
-- range and line from the actual eye to the same world point independently.
function view.clear_line(pawn, point, physical)
    if not in_reach(pawn, point) then return false end
    local _, eye = camera_pose(pawn)
    return eye ~= nil and trace_visibility(pawn, eye, physical and point or aim_point(point), physical)
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function remove_widget()
    if valid(widget) then widget:RemoveFromParent() end
    widget = nil
end

local function remove_marker(row)
    local actor = row.marker
    if valid(actor) and not actor:IsActorBeingDestroyed() then actor:K2_DestroyActor() end
    row.marker = nil
end

function view.clear()
    local previous = frame
    frame = nil
    remove_widget()
    if not previous then return end
    for _, row in pairs(previous.rows) do remove_marker(row) end
end

local function marker_visible(row)
    local actor = row.marker
    if not exact(actor, mesh_actor_path) or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), frame.world) or actor.bReplicates ~= false or not actor:HasAuthority()
        or actor.bHidden ~= false or actor:GetActorEnableCollision() ~= false then return false end
    local at, component = actor:K2_GetActorLocation(), actor.StaticMeshComponent
    return vector(at) and distance_squared(row.cue, at) <= 4
        and valid(component) and same(component:GetOwner(), actor) and component:IsVisible()
        and component:GetCollisionEnabled() == 0 and same(component.StaticMesh, row.mesh)
        and same(component:GetMaterial(0), row.material)
end

local function create_marker(row)
    local class = StaticFindObject(mesh_actor_path)
    row.mesh, row.material = asset(mesh_path), asset(material_path)
    if not valid(class) or not valid(row.mesh) or not valid(row.material) then return false end
    local actor = frame.world:SpawnActor(class, row.cue, rotation)
    row.marker = actor
    if not exact(actor, mesh_actor_path) or not same(actor:GetWorld(), frame.world)
        or actor.bReplicates ~= false or not actor:HasAuthority() then return false end
    local component = actor.StaticMeshComponent
    if not valid(component) or not same(component:GetOwner(), actor) then return false end
    actor:SetActorEnableCollision(false)
    component:SetCollisionEnabled(0)
    actor.bCanBeDamaged = false
    component:SetMobility(2)
    if not component:SetStaticMesh(row.mesh) then return false end
    component:SetMaterial(0, row.material)
    actor:SetActorScale3D(scale)
    return marker_visible(row)
end

local function closest_focus(pawn, controller)
    local stack = controller.MenuStack
    if pawn.Dead ~= false or not is_downed or is_downed(pawn) ~= false or pawn.CanInteract ~= false
        or controller.Loading ~= false or not stack or stack:GetArrayNum() ~= 0 then return end
    local camera, eye = camera_pose(pawn)
    if not camera then return end
    local direction = camera:GetForwardVector()
    if not vector(direction) then return end
    local found, nearest
    for _, guid in ipairs(frame.visible) do
        local row = frame.rows[guid]
        if in_reach(pawn, row.point) and marker_visible(row) then
            local x, y, z = row.cue.X - eye.X, row.cue.Y - eye.Y, row.cue.Z - eye.Z
            local depth = direction.X * x + direction.Y * y + direction.Z * z
            local sideways = x*x + y*y + z*z - depth*depth
            if depth > 10 and sideways <= 28^2 and (not nearest or depth < nearest)
                and unobstructed(pawn, eye, row.cue) then
                found, nearest = guid, depth
            end
        end
    end
    return found
end

local function paint(pawn, controller, guid)
    local quantity = frame.rows[guid].quantity
    local text = string.format("Shotgun shells (%d)  |  Interact", quantity)
    if not valid(widget) then
        local class, library = asset(widget_path), StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(library) then return false end
        widget = library:Create(pawn, class, controller)
        if not exact(widget, widget_path) or not same(widget:GetOwningPlayer(), controller)
            or not exact(widget.TextBlock_Notes, "/Script/UMG.TextBlock") then return false end
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(29) ~= true or not widget:IsInViewport() then return false end
        widget:SetDesiredSizeInViewport({ X = 300, Y = 40 })
        widget:SetPositionInViewport({ X = 44, Y = 118 }, false)
    end
    widget.TextBlock_Notes:SetText(FText(text))
    return widget.TextBlock_Notes:GetText():ToString() == text
end

local function row_at(encoded, host_profile)
    local guid, units, x, y, z, raw
    if host_profile then
        guid, units, x, y, z, raw = encoded:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
    else
        guid, units, x, y, z = encoded:match("^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
    end
    local quantity = math.tointeger(tonumber(units))
    local point = { X = tonumber(x), Y = tonumber(y), Z = tonumber(z) }
    if not identifier(guid, "[^0-9A-F]") or not quantity or quantity < 3 or quantity > 9
        or not vector(point) or (host_profile and not raw_path(host_profile, raw)) then
        error("native_enemy_shell_frame_invalid", 0)
    end
    return guid, { quantity = quantity, point = point, cue = aim_point(point), raw = raw }
end

function view.command(fields, state, phase)
    local action = fields[2]
    if action == "native-enemy-shell-clear" and #fields == 2 then
        view.clear()
        return "local shell cues cleared"
    end
    local present = action == "native-enemy-shell-frame"
    if not present and (action ~= "native-enemy-shell-authority-frame" or phase ~= "hosting") then return end
    local pawn = viewer(state, phase)
    local epoch = fields[3]
    if not pawn or fields[4] ~= pawn:GetFullName() or not identifier(epoch, "[^0-9a-f]") or #fields < 5 or #fields > 6 then
        error("native_enemy_shell_frame_scope_unavailable", 0)
    end
    local rows, order = {}, {}
    for index = 5, #fields do
        local guid, row = row_at(fields[index], phase == "hosting" and profiles[state.map])
        if rows[guid] then error("native_enemy_shell_frame_invalid", 0) end
        rows[guid], order[#order + 1] = row, guid
    end
    if frame and (frame.epoch ~= epoch or not same(frame.world, state.world) or not same(frame.pawn, pawn)) then
        view.clear()
    end
    for guid, old in pairs(frame and frame.rows or {}) do
        local row = rows[guid]
        if row and row.quantity == old.quantity and row.raw == old.raw and distance_squared(row.point, old.point) == 0 then
            rows[guid] = old
        else
            remove_marker(old)
        end
    end
    frame = { epoch = epoch, pawn = pawn, world = state.world, map = state.map, rows = rows, order = order, visible = {}, eligible = {},
        expires = now(pawn) + frame_lifetime, present = present }
    return "local shell frame accepted"
end

function view.sync(state, phase)
    if not frame then return end
    local pawn, controller = viewer(state, phase)
    if not same(pawn, frame.pawn) or not same(frame.world, state.world) or now(pawn) >= frame.expires then
        view.clear()
        return
    end
    frame.visible, frame.eligible = {}, {}
    for _, guid in ipairs(frame.order) do
        local row = frame.rows[guid]
        if phase == "hosting" and not view.raw_retired(state.world, row.raw) then
            remove_marker(row)
        else
            frame.eligible[#frame.eligible + 1] = guid
            if not frame.present then
                remove_marker(row)
            elseif not marker_visible(row) then
                remove_marker(row)
                local ok, visible = pcall(create_marker, row)
                if not ok or not visible then remove_marker(row) end
            end
            if frame.present and marker_visible(row) then frame.visible[#frame.visible + 1] = guid end
        end
    end
    local focus = closest_focus(pawn, controller)
    local ok, shown = false, false
    if focus then ok, shown = pcall(paint, pawn, controller, focus) end
    frame.focus = ok and shown and focus or nil
    if not frame.focus then remove_widget() end
end

function view.focused_target(pawn)
    if not frame or not frame.focus or not same(pawn, frame.pawn) or now(pawn) >= frame.expires
        or closest_focus(pawn, pawn.Controller) ~= frame.focus then return end
    return "enemy-shell:" .. frame.epoch .. ":" .. frame.focus
end

function view.fixture(state, phase, target, press)
    local pawn = viewer(state, phase)
    local guid = type(target) == "string" and target:match("^enemy%-shell:[0-9a-f]+:([0-9A-F]+)$")
    local row = frame and guid and frame.rows[guid]
    if not fixtures or not row or not same(pawn, frame.pawn)
        or target ~= "enemy-shell:" .. frame.epoch .. ":" .. guid or not in_reach(pawn, row.point) then
        error("native_enemy_shell_fixture_scope_unavailable", 0)
    end
    local camera, eye = camera_pose(pawn)
    if not camera then error("native_enemy_shell_fixture_camera_unavailable", 0) end
    local relative = camera.RelativeRotation
    if not relative or not coordinate(relative.Pitch) or not coordinate(relative.Yaw) or not coordinate(relative.Roll) then
        error("native_enemy_shell_fixture_camera_unavailable", 0)
    end
    local original = { Pitch = relative.Pitch, Yaw = relative.Yaw, Roll = relative.Roll }
    local x, y, z = row.cue.X - eye.X, row.cue.Y - eye.Y, row.cue.Z - eye.Z
    local aimed = { Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))), Yaw = math.deg(math.atan(y, x)), Roll = 0 }
    local accepted, failure = pcall(function()
        camera:K2_SetWorldRotation(aimed, false, {}, true)
        pawn:LookTrace()
        view.sync(state, phase)
        if view.focused_target(pawn) ~= target then error("native_enemy_shell_fixture_focus_unavailable", 0) end
        if press() ~= target then error("native_enemy_shell_fixture_press_unverified", 0) end
    end)
    local restored = pcall(function()
        if not same(pawn.Camera, camera) then error("native_enemy_shell_fixture_camera_changed", 0) end
        camera:K2_SetRelativeRotation(original, false, {}, true)
        local current = camera.RelativeRotation
        if not current or math.abs(current.Pitch - original.Pitch) > .05 or math.abs(current.Yaw - original.Yaw) > .05
            or math.abs(current.Roll - original.Roll) > .05 then error("native_enemy_shell_fixture_camera_changed", 0) end
        pawn:LookTrace()
        view.sync(state, phase)
    end)
    if not restored then error("mutation_unverified_native_enemy_shell_fixture_camera", 0) end
    if not accepted then error(failure, 0) end
    return "stock shell interact input verified"
end

function view.rows(state, phase, status_row)
    if not viewer(state, phase) then return {} end
    local rows = { status_row("native-enemy-shell-view-capability", 2) }
    if frame and same(frame.world, state.world) then
        if phase == "hosting" then
            rows[#rows + 1] = status_row("native-enemy-shell-authority", frame.map, frame.epoch, frame.pawn:GetFullName(),
                #frame.eligible > 0 and table.concat(frame.eligible, ",") or "-")
        end
        if #frame.visible > 0 then
            rows[#rows + 1] = status_row("native-enemy-shell-view", frame.map, frame.epoch,
                table.concat(frame.visible, ","), frame.focus or "-")
        end
    end
    return rows
end

function view.start(downed)
    is_downed = downed
end

return view
