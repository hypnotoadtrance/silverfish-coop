local view = {}

local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world_name = "World " .. map .. ".DataSilo_01_P"
local persistent = map .. ".DataSilo_01_P:PersistentLevel."
local core_path, cradle_path = persistent .. "MindCore_BP_C_1", persistent .. "MindCore_Cradle_BP_C_1"
local core_name = "MindCore_BP_C " .. core_path
local mesh_class = "/Script/Engine.StaticMeshComponent"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local fixture = "native-mindcore-guest-carry-fixture"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local heartbeat, input_path, is_downed, binding, sample
local input_sequence, aim_sequence = 0, 0
local sequence_limit = 2 ^ 53

local function refuse(reason) error("native_mindcore_guest_carry_" .. reason, 0) end
local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(value, class) return valid(value) and same(value:GetClass(), StaticFindObject(class)) end
local function owned(value, owner, class) return exact(value, class) and same(value:GetOwner(), owner) end
local function bounded(n, limit) return type(n) == "number" and n == n and math.abs(n) <= limit end
local function sequence(n) return math.type(n) == "integer" and n > 0 and n < sequence_limit end
local function nonce(value) return #value == 32 and value:match("^[0-9a-f]+$") and value:find("[^0]") end
local function point(p) return p and bounded(p.X, 10000000) and bounded(p.Y, 10000000) and bounded(p.Z, 10000000) end

local function within(a, b, range)
    return point(a) and point(b) and (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= range*range
end

local function placed(value, class, name, world)
    return exact(value, class) and value:GetFullName() == name and not value:IsActorBeingDestroyed()
        and same(value:GetOuter(), world.PersistentLevel) and same(value:GetWorld(), world)
end

local function context(state, phase)
    local world = state.world
    if not enabled or not heartbeat or phase ~= "connected" or state.map ~= map or state.listening ~= true
        or state.server ~= true or not valid(world) or world:GetFullName() ~= world_name then return nil, "world" end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(connection) or not same(connection.Driver, driver) or not same(driver.World, world) then return nil, "driver" end

    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_class) or not exact(controller, controller_class) or not same(detail.controller_object, controller)
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or not pawn:IsLocallyControlled() or pawn:HasAuthority() or not controller:IsLocalController()
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed() or detail.name ~= pawn:GetFullName()
        or #detail.name > 512 or detail.name:find("%c") then return nil, "pawn" end
    if controller.Loading ~= false or not controller.MenuStack or controller.MenuStack:GetArrayNum() ~= 0
        or pawn.Dead ~= false or is_downed(pawn) ~= false or valid(pawn.HeldObject) then return nil, "standing" end
    local camera = pawn.Camera
    if not owned(camera, pawn, "/Script/Engine.CameraComponent") then return nil, "camera" end

    local core = StaticFindObject(core_path)
    if not placed(core, "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C", core_name, world)
        or core:GetLocalRole() ~= 1 or core.bReplicateMovement ~= true
        or core:GetActorEnableCollision() ~= true then return nil, "replicated_core" end
    local frame = core.PropMesh
    if not owned(frame, core, mesh_class) or not same(core.RootComponent, frame)
        or frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) ~= true then return nil, "core_mesh" end

    local cradle = StaticFindObject(cradle_path)
    if not placed(cradle, "/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C",
        "MindCore_Cradle_BP_C " .. cradle_path, world) or cradle.Status ~= 0
        or not same(cradle.MindCore_Ref, core) then return nil, "cradle" end
    local capsule, mount = cradle.Capsule, cradle.SM_MindCoffin_Cradle
    if not owned(capsule, cradle, "/Script/Engine.CapsuleComponent")
        or capsule:GetFullName() ~= "CapsuleComponent " .. cradle_path .. ".Capsule"
        or not owned(mount, cradle, mesh_class) or not same(capsule:GetAttachParent(), mount)
        or capsule:GetGenerateOverlapEvents() ~= false then return nil, "quiet_cradle" end
    local position, destination = core:K2_GetActorLocation(), mount:K2_GetComponentLocation()
    if not point(position) or not point(destination) or within(position, destination, 24) then return nil, "mounted" end
    return {world = world, driver = driver, connection = connection, pawn = pawn, controller = controller,
        camera = camera, core = core, frame = frame, name = detail.name}
end

local function lease(current, now)
    local file = io.open(heartbeat, "rb")
    if not file then return end
    local content = file:read(1025)
    file:close()
    if not content or #content > 1024 then return end
    local seconds, session, epoch, pawn, state = content:match(
        "^1\t(%d+)\t([0-9a-f]+)\t([0-9a-f]+)\t([^%c]+)\t([a-z]+)\r?\n$")
    local stamp = math.tointeger(tonumber(seconds))
    if not stamp or stamp > now or now > stamp + 2 or not nonce(session) or not nonce(epoch)
        or pawn ~= current.name or (state ~= "ready" and state ~= "held") then return end
    current.session, current.epoch, current.state, current.stamp = session, epoch, state, stamp
    return current
end

local function matches(a, b)
    return a and b and a.session == b.session and a.epoch == b.epoch and a.name == b.name
        and same(a.world, b.world) and same(a.driver, b.driver) and same(a.connection, b.connection)
        and same(a.pawn, b.pawn) and same(a.controller, b.controller) and same(a.camera, b.camera)
        and same(a.core, b.core) and same(a.frame, b.frame)
end

local function camera_aim(current)
    local v = current.camera:GetForwardVector()
    if not point(v) then return end
    local horizontal = math.sqrt(v.X * v.X + v.Y * v.Y)
    local length = math.sqrt(horizontal * horizontal + v.Z * v.Z)
    if length < .98 or length > 1.02 then return end
    local pitch, yaw = math.deg(math.atan(v.Z, horizontal)), math.deg(math.atan(v.Y, v.X))
    if bounded(pitch, 89.9) and bounded(yaw, 180) then return pitch, yaw end
end

local function degrees(n)
    return string.format("%.3f", n)
end

local function sample_aim(current)
    sample = nil
    if current.state ~= "held" or aim_sequence + 1 >= sequence_limit then return end
    local pitch, yaw = camera_aim(current)
    if not pitch then return end
    aim_sequence = aim_sequence + 1
    sample = {sequence = aim_sequence, pitch = pitch, yaw = yaw}
    return sample
end

function view.clear()
    if binding and type(SilverfishClearNativeMindCoreCarryInputScope) == "function" then
        SilverfishClearNativeMindCoreCarryInputScope()
    end
    binding, sample = nil, nil
end

local function arm(current)
    if type(SilverfishSetNativeMindCoreCarryInputScope) ~= "function"
        or type(SilverfishTakeNativeMindCoreCarryInput) ~= "function"
        or type(SilverfishClearNativeMindCoreCarryInputScope) ~= "function" then return end
    if not matches(binding, current) then view.clear() end
    if SilverfishSetNativeMindCoreCarryInputScope(current.pawn:GetAddress(), current.state == "held") ~= true then
        view.clear()
        return
    end
    binding, sample = current, nil
    return true
end

local function consume(current)
    local serial, operation, source = SilverfishTakeNativeMindCoreCarryInput(current.pawn:GetAddress())
    if not sequence(serial) or serial <= input_sequence then return end
    input_sequence = serial
    if operation ~= (current.state == "held" and "release" or "grab")
        or (source ~= "interact" and source ~= "pickup") then return end
    local pitch, yaw = camera_aim(current)
    if not pitch and operation == "grab" then return end
    local file = io.open(input_path, "ab")
    if not file then return end
    local written = file:write(table.concat({"native-mindcore-carry-toggle", serial, map, current.name, core_name,
        current.session, current.epoch, operation, source, degrees(pitch or 0), degrees(yaw or 0)}, "\t"), "\n")
    local closed = file:close()
    if written and closed then return operation, source, serial end
end

local function sync_input(state, phase)
    local current = context(state, phase)
    current = current and lease(current, os.time())
    if not current or not arm(current) then view.clear(); return end
    consume(current)
    sample_aim(current)
end

function view.sync(state, phase)
    local ok = pcall(sync_input, state, phase)
    if not ok then view.clear() end
end

local function aimed_rotation(eye, target, height)
    local x, y, z = target.X-eye.X, target.Y-eye.Y, target.Z+height-eye.Z
    local horizontal = math.sqrt(x*x + y*y)
    if horizontal > 1 then return {Pitch = math.deg(math.atan(z, horizontal)), Yaw = math.deg(math.atan(y, x)), Roll = 0} end
end

local function set_aim(current, angle)
    current.controller:SetControlRotation(angle)
    current.camera:K2_SetWorldRotation(angle, false, {}, true)
    current.pawn:LookTrace()
end

local function describe(value)
    return valid(value) and value:GetFullName():gsub("%c", " "):sub(1, 145) or "-"
end

local function focused(current, operation)
    local pawn = current.pawn
    if valid(pawn.InteractionTarget_Component) then return false end
    if operation == "release" then return true end
    local range, distance = pawn.InteractionRange, pawn.ActorLookedAt_Distance
    return same(pawn.ActorBeingLookedAt, current.core) and bounded(range, 1000) and range > 0
        and bounded(distance, range) and distance > 0 and distance < range
end

local function copy_rotation(angle)
    if angle and bounded(angle.Pitch, 360) and bounded(angle.Yaw, 360) and bounded(angle.Roll, 360) then
        return {Pitch = angle.Pitch, Yaw = angle.Yaw, Roll = angle.Roll}
    end
end

local function restore_aim(current, control, relative)
    current.controller:SetControlRotation(control)
    current.camera:K2_SetRelativeRotation(relative, false, {}, true)
    current.pawn:LookTrace()
end

local function press_fixture(current, operation)
    local pawn = current.pawn
    local eye, center = current.camera:K2_GetComponentLocation(), current.core:K2_GetActorLocation()
    local control, relative = copy_rotation(current.controller:GetControlRotation()), copy_rotation(current.camera.RelativeRotation)
    if not point(eye) or not point(center) or not control or not relative then refuse("fixture_camera_pose") end
    local ok, failure = pcall(function()
        pawn:LookTrace()
        if not focused(current, operation) then
            for _, height in ipairs({0, 18, -18, 34, -34}) do
                local angle = aimed_rotation(eye, center, height)
                if angle then set_aim(current, angle) end
                if focused(current, operation) then break end
            end
        end
        if not focused(current, operation) then refuse("fixture_stock_focus:actor=" .. describe(pawn.ActorBeingLookedAt)
            .. ":component=" .. describe(pawn.InteractionTarget_Component) .. ":distance=" .. tostring(pawn.ActorLookedAt_Distance)) end
        local accepted, stock = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if accepted ~= true or stock ~= "stock_started_consumed" then
            -- A non-consumed stock press still has a general bridge receipt. Do not project it as a revive.
            if stock == "stock_started" and type(SilverfishTakeNativeInteractPress) == "function" then
                SilverfishTakeNativeInteractPress(pawn:GetAddress())
            end
            refuse("fixture_stock_input:" .. tostring(stock))
        end
        local action, source = consume(current)
        if action ~= operation or source ~= "interact" or valid(pawn.HeldObject) then refuse("fixture_stock_receipt") end
    end)
    if not ok then
        restore_aim(current, control, relative)
        error(failure, 0)
    end
end

local function turn_fixture(current, pitch, yaw)
    local previous_pitch, previous_yaw = camera_aim(current)
    if not previous_pitch then refuse("fixture_camera_aim") end
    local control, relative = copy_rotation(current.controller:GetControlRotation()), copy_rotation(current.camera.RelativeRotation)
    if not control or not relative then refuse("fixture_camera_pose") end
    pitch = pitch or previous_pitch
    yaw = yaw or (previous_yaw + 25 + 180) % 360 - 180
    set_aim(current, {Pitch = pitch, Yaw = yaw, Roll = 0})
    local proof = sample_aim(current)
    if not proof or math.abs(proof.pitch - pitch) > .25
        or math.abs((proof.yaw - yaw + 180) % 360 - 180) > .25 then
        restore_aim(current, control, relative)
        refuse("fixture_camera_turn_readback")
    end
end

function view.command(fields, state, phase)
    if fields[2] ~= fixture then return end
    local operation = fields[4]
    local absolute_aim = operation == "turn" and #fields == 6
    if not headless or (#fields ~= 4 and not absolute_aim)
        or (operation ~= "grab" and operation ~= "release" and operation ~= "turn")
        or type(SilverfishProbeNativeInteractInput) ~= "function" then refuse("fixture_unavailable") end
    local pitch, yaw
    if absolute_aim then
        pitch, yaw = tonumber(fields[5]), tonumber(fields[6])
        if not bounded(pitch, 89.9) or not bounded(yaw, 180) then refuse("fixture_camera_aim") end
    end
    local current, why = context(state, phase)
    if not current or current.name ~= fields[3] then refuse("fixture_scope:" .. (why or "pawn")) end
    current = lease(current, os.time())
    if not current or current.state ~= (operation == "grab" and "ready" or "held") or not arm(current) then
        refuse("fixture_lease")
    end
    if operation == "turn" then turn_fixture(current, pitch, yaw) else press_fixture(current, operation) end
    return "stock guest MindCore " .. operation .. " observed"
end

function view.rows(state, phase, row)
    if not binding or phase ~= "connected" or state.map ~= map or os.time() > binding.stamp + 2
        or not same(state.world, binding.world) or not state.local_pawn or not same(state.local_pawn.pawn, binding.pawn) then return {} end
    local rows = {row("native-mindcore-guest-carry", map, binding.name, core_name, binding.session, binding.epoch, binding.state)}
    if sample then
        rows[2] = row("native-mindcore-carry-aim", map, binding.name, core_name, binding.session, binding.epoch,
            sample.sequence, degrees(sample.pitch), degrees(sample.yaw))
    end
    return rows
end

function view.start(bridge, downed)
    if not enabled or type(downed) ~= "function" then return end
    heartbeat = bridge .. "/native-mindcore-carry-heartbeat"
    input_path = bridge .. "/native-mindcore-carry-input.tsv"
    is_downed = downed
end

return view
