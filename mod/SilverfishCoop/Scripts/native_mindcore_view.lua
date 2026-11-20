local view = {}

local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world = map .. ".DataSilo_01_P"
local path = world .. ":PersistentLevel.MindCore_Cradle_BP_C_1"
local cradle_class = "/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local widget_class = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local fixture = "native-mindcore-interact-fixture"
local render = "native-mindcore-render"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"

local heartbeat, is_downed
local binding, request, widget, shown
local latest = 0

local messages = {
    focused = "Interact to check for Drill Test Results.",
    checking = "Checking for Drill Test Results...",
    offer = "Upload your Drill Test Results?\nInteract again to confirm.",
    missing = "Bring your Drill Test Results in your backpack.",
    submitting = "Compiling the Drill Test Results...",
    compiled = "Results compiled. Your tape is still in your backpack.",
    unavailable = "The upload is unavailable.",
}

local function refuse(reason) error("native_mindcore_view_" .. reason, 0) end
local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]") end

local function context(state, phase)
    if not heartbeat or phase ~= "connected" or state.map ~= map or state.server ~= true or not state.listening
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. world then return end
    local pawn, controller = state.local_pawn and state.local_pawn.pawn, state.controller
    local cradle = StaticFindObject(path)
    if not exact(pawn, pawn_class) or not valid(controller) or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or state.local_pawn.name ~= pawn:GetFullName() or not exact(cradle, cradle_class)
        or cradle:GetFullName() ~= "MindCore_Cradle_BP_C " .. path or not same(cradle:GetWorld(), state.world)
        or cradle:IsActorBeingDestroyed() then return end
    local box = cradle.Button_Tape
    if not exact(box, "/Script/Engine.BoxComponent") or not same(box:GetOwner(), cradle) then return end
    local mode = box:GetCollisionEnabled()
    if mode ~= 1 and mode ~= 3 then return end
    return {pawn = pawn, controller = controller, cradle = cradle, box = box, world = state.world,
        name = state.local_pawn.name}
end

local function matches(a, b)
    return a and b and same(a.world, b.world) and same(a.pawn, b.pawn) and same(a.controller, b.controller)
        and same(a.cradle, b.cradle) and same(a.box, b.box)
end

local function within(a, b, range)
    return (a.X-b.X)^2 + (a.Y-b.Y)^2 + (a.Z-b.Z)^2 <= range^2
end

local function standing(current)
    local pawn, controller = current.pawn, current.controller
    local reach = pawn.InteractionRange
    if type(reach) ~= "number" or not (reach > 0 and reach <= 1000) or controller.Loading ~= false
        or not controller.MenuStack or controller.MenuStack:GetArrayNum() ~= 0
        or pawn.Dead ~= false or is_downed(pawn) ~= false then return false end
    local position = pawn:K2_GetActorLocation()
    return within(position, current.cradle:K2_GetActorLocation(), reach)
        and within(position, current.box:K2_GetComponentLocation(), reach)
end

local function focused(current)
    return current.pawn.CanInteract == true and same(current.pawn.InteractionTarget_Component, current.box)
end

local function stage_one(current, now)
    if current.cradle.Status ~= 0 and current.cradle.Status ~= 1 then return false end
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local contents = file:read(600)
    file:close()
    local seconds, pawn = (contents or ""):match("^(%d+)\t([^\r\n]+)[\r\n]*$")
    local stamp = math.tointeger(tonumber(seconds))
    return pawn == current.name and stamp and stamp <= now and now <= stamp + 2
end

local function disarm()
    if type(SilverfishClearNativeMindCoreInput) == "function" then SilverfishClearNativeMindCoreInput() end
end

local function hide()
    if valid(widget) then widget:RemoveFromParent() end
    widget, shown = nil, nil
end

function view.clear()
    disarm()
    hide()
    binding, request = nil, nil
end

local function display(current, status)
    if headless then return true end
    local message = "MindCore Console\n\n" .. messages[status]
    if shown == message and valid(widget) then return true end
    if not valid(widget) then
        local class, library = StaticFindObject(widget_class), StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) then LoadAsset(widget_class); class = StaticFindObject(widget_class) end
        if not valid(class) or not valid(library) then return false end
        widget = library:Create(current.pawn, class, current.controller)
        if not exact(widget, widget_class) or not same(widget:GetOwningPlayer(), current.controller)
            or not exact(widget.TextBlock_Notes, "/Script/UMG.TextBlock") then return false end
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(31) ~= true or not widget:IsInViewport() then return false end
        widget:SetDesiredSizeInViewport({X = 580, Y = 200})
        widget:SetPositionInViewport({X = 42, Y = 138}, false)
        widget.TextBlock_Notes:SetAutoWrapText(true)
    end
    widget.TextBlock_Notes:SetText(FText(message))
    if widget.TextBlock_Notes:GetText():ToString() ~= message then return false end
    shown = message
    return true
end

local function present(current, status)
    local ok, shown_ok = pcall(display, current, status)
    if ok and shown_ok == true then return true end
    hide()
    return false
end

local function arm(current, accepting)
    return type(SilverfishSetNativeMindCoreInput) == "function" and type(SilverfishTakeNativeMindCorePress) == "function"
        and SilverfishSetNativeMindCoreInput(current.pawn:GetAddress(), accepting) == true
end

local function drain(current, now, staged)
    local sequence = math.tointeger((SilverfishTakeNativeMindCorePress(current.pawn:GetAddress())))
    if not sequence or sequence <= latest or sequence >= 2^53 then return false end
    latest = sequence
    local status, opened, tape = "checking", sequence, "-"
    if request and request.state == "offer" then
        if request.staged ~= staged or not present(current, "offer") then return false end
        status, opened, tape = "submitting", request.opened, request.guid
    end
    request = {state = status, sequence = sequence, opened = opened, guid = tape, pending = true, staged = staged, expires = now + 28}
    return true
end

function view.sync(state, phase)
    local current, now = context(state, phase), os.time()
    if not current then if binding then view.clear() end; return end
    if not matches(current, binding) then view.clear(); binding = current end
    local usable, facing = standing(current), focused(current)
    local leased = usable and not binding.complete and stage_one(current, now)
    if request and (not usable or request.expires <= now
        or not (request.staged or facing or request.pending)
        or not leased and not request.pending and request.state ~= "compiled") then request = nil end
    binding.state = "pass-through"
    if leased and arm(current, not (request and request.pending)) then
        binding.state = facing and "focused" or "armed"
        if facing and not (request and request.pending) then drain(current, now, false) end
    else
        disarm()
    end
    if request and (facing or request.staged or request.state == "compiled") then present(current, request.state)
    elseif binding.state == "focused" then present(current, "focused")
    else hide() end
end

local function press_fixture(current, now)
    local pawn, controller, box = current.pawn, current.controller, current.box
    local camera = pawn.Camera
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn) then refuse("fixture_camera") end
    local source = camera.RelativeRotation
    local original = {Pitch = source.Pitch, Yaw = source.Yaw, Roll = source.Roll}
    local eye, center = camera:K2_GetComponentLocation(), box:K2_GetComponentLocation()
    local size, cursor = box:GetScaledBoxExtent(), controller.bShowMouseCursor
    if not size or not (size.Y > 0 and size.Y <= 200 and size.Z > 0 and size.Z <= 200) then refuse("fixture_box_extent") end
    local ok, reason = pcall(function()
        local up, side = box:GetUpVector(), box:GetRightVector()
        for _, pair in ipairs({{0, 0}, {0, .35}, {0, -.35}, {0, .65}, {-.65, 0}, {.65, 0}, {-.65, .65}, {.65, .65}}) do
            local lift, width = pair[2] * size.Z, pair[1] * size.Y
            local dx = center.X - eye.X + lift * up.X + width * side.X
            local dy = center.Y - eye.Y + lift * up.Y + width * side.Y
            local dz = center.Z - eye.Z + lift * up.Z + width * side.Z
            local distance = math.sqrt(dx^2 + dy^2)
            if distance > 1 then
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(dz, distance)), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}, false, {}, true)
                pawn:LookTrace()
                if focused(current) then
                    local handled, stock = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                    if handled ~= true or stock ~= "stock_started_consumed" or controller.MenuStack:GetArrayNum() ~= 0
                        or controller.bShowMouseCursor ~= cursor or not drain(current, now, true) then refuse("fixture_stock_press") end
                    return
                end
            end
        end
        local hit = pawn.InteractionTarget_Component
        refuse("fixture_stock_focus:" .. (valid(hit) and hit:GetFullName():sub(1, 250) or "-"))
    end)
    camera:K2_SetRelativeRotation(original, false, {}, true)
    pawn:LookTrace()
    local restored = camera.RelativeRotation
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((restored[axis] - original[axis] + 180) % 360 - 180) > .05 then refuse("fixture_camera_restore") end
    end
    if not ok then error(reason, 0) end
    return "stock MindCore tape button Interact verified"
end

function view.command(fields, state, phase)
    local action = fields[2]
    if action ~= fixture and action ~= render then return end
    local current, now = context(state, phase), os.time()
    if not matches(current, binding) or not standing(current) then refuse("scope") end
    if action == fixture then
        if #fields ~= 2 or not headless or binding.complete or not stage_one(current, now) or request and request.pending
            or type(SilverfishProbeNativeInteractInput) ~= "function" or not arm(current, true) then refuse("fixture_unavailable") end
        return press_fixture(current, now)
    end
    local sequence, opened, status, tape = math.tointeger(tonumber(fields[3])), math.tointeger(tonumber(fields[4])), fields[5], fields[6]
    if #fields ~= 6 or not request or not request.pending or request.sequence ~= sequence or request.opened ~= opened
        or request.expires <= now then refuse("render_expired") end
    local accepted = status == "unavailable" and tape == "-"
        or request.state == "checking" and (status == "missing" and tape == "-" or status == "offer" and guid(tape))
        or request.state == "submitting" and status == "compiled" and request.guid == tape
    if not accepted then refuse("render_proof") end
    if status == "compiled" then binding.complete = true; disarm() end
    if status == "offer" and not (request.staged or focused(current)) then refuse("render_focus_changed") end
    if not present(current, status) then refuse("render_unavailable") end
    request.state, request.guid, request.pending = status, tape, false
    request.expires = now + (status == "compiled" and 8 or 22)
    return "authenticated MindCore Results response verified"
end

function view.rows(state, phase, row)
    if not binding or phase ~= "connected" or state.map ~= map or not same(state.world, binding.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, binding.pawn) then return {} end
    return {row("native-mindcore-view", map, binding.name, "MindCore_Cradle_BP_C " .. path,
        request and request.state or binding.state, request and request.sequence or 0,
        request and request.opened or 0, request and request.guid or "-")}
end

function view.start(bridge, downed)
    heartbeat, is_downed = bridge .. "/native-mindcore-upload-heartbeat", downed
end

return view
