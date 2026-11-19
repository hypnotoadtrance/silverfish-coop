local view = {}
local profiles = require("native_tape_profiles").maps

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local station_class = "/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local box_class = "/Script/Engine.BoxComponent"
local camera_class = "/Script/Engine.CameraComponent"
local widget_class = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local events, heartbeat, is_downed
local binding, session, widget, shown
local last_sequence = 0

local messages = {
    focused = "Interact to inspect the tape exchange.",
    checking = "Checking the party's tape exchange...",
    missing = "Bring one Blank Data Tape in your backpack.\nInteract to check again.",
    trading = "Exchanging the selected tape...",
    unavailable = "The exchange is unavailable.\nInteract to check again.",
}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function reject(reason) error("native_mosby_station_" .. reason, 0) end
local function guid(value) return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]") end

local function context(state, phase)
    local profile = profiles[state.map]
    if not enabled or not events or phase ~= "connected" or not profile or state.server ~= true or not state.listening
        or not valid(state.world) or state.world:GetFullName() ~= "World " .. profile.world then return end
    local pawn, controller = state.local_pawn and state.local_pawn.pawn, state.controller
    local station = StaticFindObject(profile.path)
    if not exact(pawn, pawn_class) or not valid(controller) or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or state.local_pawn.name ~= pawn:GetFullName() or not exact(station, station_class)
        or station:GetFullName() ~= profile.station or not same(station:GetWorld(), state.world) or station:IsActorBeingDestroyed() then return end
    local box = station.Box
    if not exact(box, box_class) or not same(box:GetOwner(), station) then return end
    local collision = box:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return end
    return {pawn = pawn, controller = controller, world = state.world, station = station, box = box,
        name = state.local_pawn.name, profile = profile}
end

local function matches(a, b)
    return a and b and same(a.world, b.world) and same(a.pawn, b.pawn) and same(a.controller, b.controller)
        and same(a.station, b.station) and same(a.box, b.box)
end

local function within(a, b, range)
    return (a.X - b.X)^2 + (a.Y - b.Y)^2 + (a.Z - b.Z)^2 <= range^2
end

local function standing(current)
    local pawn, controller = current.pawn, current.controller
    local reach = pawn.InteractionRange
    if type(reach) ~= "number" or not (reach > 0 and reach <= 1000) or controller.Loading ~= false
        or not controller.MenuStack or controller.MenuStack:GetArrayNum() ~= 0 or pawn.Dead ~= false or is_downed(pawn) ~= false then return false end
    local point = pawn:K2_GetActorLocation()
    return within(point, current.station:K2_GetActorLocation(), reach) and within(point, current.box:K2_GetComponentLocation(), reach)
end

local function fresh(now)
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local stamp = tonumber(file:read(32))
    file:close()
    return stamp and stamp <= now and now <= stamp + 2
end

local function focused(current)
    return current.pawn.CanInteract == true and same(current.pawn.InteractionTarget_Component, current.box)
end

local function hide()
    if valid(widget) then widget:RemoveFromParent() end
    widget, shown = nil, nil
end

function view.clear()
    hide()
    if binding and type(SilverfishClearNativeMosbyStationScope) == "function" then SilverfishClearNativeMosbyStationScope() end
    binding, session = nil, nil
end

local function display(current, status)
    if headless then return true end
    local profile = current.profile
    local message = messages[status]
    if status == "offer" then message = "Exchange one Blank Data Tape for one " .. profile.label .. " tape.\nInteract again to confirm." end
    if status == "exchanged" then message = profile.label .. " added to your backpack." end
    message = profile.title .. "\n\n" .. message
    if shown == message and valid(widget) then return true end
    if not valid(widget) then
        local class, library = StaticFindObject(widget_class), StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) then LoadAsset(widget_class); class = StaticFindObject(widget_class) end
        if not valid(class) or not valid(library) then reject("widget_unavailable") end
        widget = library:Create(current.pawn, class, current.controller)
        if not exact(widget, widget_class) or not same(widget:GetOwningPlayer(), current.controller)
            or not exact(widget.TextBlock_Notes, "/Script/UMG.TextBlock") then reject("widget_unqualified") end
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(31) ~= true or not widget:IsInViewport() then reject("widget_not_visible") end
        widget:SetDesiredSizeInViewport({X = 600, Y = 265})
        widget:SetPositionInViewport({X = 42, Y = 138}, false)
        widget.TextBlock_Notes:SetAutoWrapText(true)
    end
    widget.TextBlock_Notes:SetText(FText(message))
    if widget.TextBlock_Notes:GetText():ToString() ~= message then reject("widget_readback_unverified") end
    shown = message
    return true
end

local function present(current, status)
    local checked, restored = pcall(display, current, status)
    if not checked or restored ~= true then hide(); return false end
    return true
end

local function arm(current, accepting)
    return type(SilverfishSetNativeMosbyStationScope) == "function" and type(SilverfishTakeNativeMosbyStationPress) == "function"
        and SilverfishSetNativeMosbyStationScope(current.pawn:GetAddress(), accepting == true) == true
end

local function drain(current, now, press_fixture)
    local sequence = math.tointeger((SilverfishTakeNativeMosbyStationPress(current.pawn:GetAddress())))
    if not sequence or sequence <= last_sequence or sequence >= 2^53 then return false end
    last_sequence = sequence
    if session and session.pending then return false end
    local kind, opened, blank = "view", sequence, "-"
    if session and session.state == "offer" and session.expires > now then
        if (session.fixture == true) ~= (press_fixture == true) or not present(current, "offer") then return false end
        kind, opened, blank = "trade", session.open, session.blank
    end
    local file = io.open(events, "ab")
    if not file then return false end
    local written = file:write(table.concat({"mosby-station", sequence, opened, kind, current.name, current.profile.station, blank}, "\t"), "\n")
    local closed = file:close()
    if not written or not closed then return false end
    session = {sequence = sequence, open = opened, pending = kind, state = kind == "trade" and "trading" or "checking",
        blank = blank, filled = "-", expires = now + 25, fixture = press_fixture}
    return true
end

function view.sync(state, phase)
    local current, now = context(state, phase), os.time()
    if not current then if binding then view.clear() end; return end
    if not matches(current, binding) then view.clear(); binding = current end
    local usable = standing(current)
    local facing = focused(current)
    local live = usable and fresh(now)
    if session and (not usable or session.expires <= now or not (session.fixture or facing)
        or not live and not session.pending and not (session.grace and now <= session.grace)) then session = nil end
    binding.state = "waiting"
    if not arm(current, live and not (session and session.pending)) then session = nil; hide(); return end
    binding.state = live and (facing and "focused" or "armed") or "waiting"
    if live and facing and not (session and session.pending) then drain(current, now, false) end
    if session then present(current, session.state)
    elseif live and facing then present(current, "focused")
    else hide() end
end

local function fixture(current, now)
    local pawn, box, controller = current.pawn, current.box, current.controller
    local camera = pawn.Camera
    if not exact(camera, camera_class) or not same(camera:GetOwner(), pawn) then reject("fixture_camera_unavailable") end
    local origin = camera.RelativeRotation
    local rotation = {Pitch = origin.Pitch, Yaw = origin.Yaw, Roll = origin.Roll}
    local eye, center = camera:K2_GetComponentLocation(), box:K2_GetComponentLocation()
    local size, cursor = box:GetScaledBoxExtent(), controller.bShowMouseCursor
    if not size or not (size.Y > 0 and size.Y <= 200 and size.Z > 0 and size.Z <= 200) then reject("fixture_box_unqualified") end
    local checked, issue = pcall(function()
        local up, right = box:GetUpVector(), box:GetRightVector()
        local last_hit = "-"
        for _, offset in ipairs({{0, 0}, {0, 0.35}, {0, -0.35}, {0, 0.65}, {-0.65, 0}, {0.65, 0},
                {-0.65, 0.65}, {0.65, 0.65}}) do
            local side, lift = offset[1] * size.Y, offset[2] * size.Z
            local dx = center.X - eye.X + side * right.X + lift * up.X
            local dy = center.Y - eye.Y + side * right.Y + lift * up.Y
            local dz = center.Z - eye.Z + side * right.Z + lift * up.Z
            local direction = math.sqrt(dx^2 + dy^2)
            if direction > 1 then
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(dz, direction)), Yaw = math.deg(math.atan(dy, dx)), Roll = 0}, false, {}, true)
                pawn:LookTrace()
                if focused(current) then
                    local consumed, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                    if consumed ~= true or reason ~= "stock_started_consumed" or controller.MenuStack:GetArrayNum() ~= 0
                        or controller.bShowMouseCursor ~= cursor or not drain(current, now, true) then reject("fixture_stock_press_unverified") end
                    return
                end
                local hit = pawn.InteractionTarget_Component
                if valid(hit) then last_hit = hit:GetFullName() end
            end
        end
        reject("fixture_stock_focus_unavailable|" .. last_hit)
    end)
    camera:K2_SetRelativeRotation(rotation, false, {}, true)
    pawn:LookTrace()
    local restored = camera.RelativeRotation
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((restored[axis] - rotation[axis] + 180) % 360 - 180) > 0.05 then reject("fixture_camera_restore_unverified") end
    end
    if not checked then error(issue, 0) end
    return "exact stock download station press verified"
end

function view.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-mosby-station-render" and action ~= "native-mosby-station-fixture" then return end
    local current, now = context(state, phase), os.time()
    if not matches(current, binding) or not standing(current) then reject("exact_local_scope_unavailable") end
    if action == "native-mosby-station-fixture" then
        if #fields ~= 2 or not headless or not fresh(now) or session and session.pending
            or type(SilverfishProbeNativeInteractInput) ~= "function" or not arm(current, true) then reject("fixture_unavailable") end
        return fixture(current, now)
    end
    local sequence, opened = math.tointeger(tonumber(fields[3])), math.tointeger(tonumber(fields[4]))
    local status, blank, filled = fields[5], fields[6], fields[7]
    if #fields ~= 7 or not session or not session.pending or session.sequence ~= sequence or session.open ~= opened
        or session.expires <= now or not (session.fixture or focused(current)) then reject("render_expired") end
    local accepted = status == "unavailable" and blank == "-" and filled == "-"
        or session.pending == "view" and filled == "-" and (status == "offer" and guid(blank) or status == "missing" and blank == "-")
        or session.pending == "trade" and status == "exchanged" and blank == session.blank and guid(filled) and blank ~= filled
    if not accepted or not present(current, status) then reject("render_proof_unqualified") end
    session.state, session.blank, session.filled, session.pending = status, blank, filled, nil
    session.expires, session.grace = now + 30, now + 2
    return "authenticated station state rendered"
end

function view.rows(state, phase, row)
    if not binding or phase ~= "connected" or state.map ~= binding.profile.map or not same(state.world, binding.world)
        or not state.local_pawn or not same(state.local_pawn.pawn, binding.pawn) then return {} end
    return {row("native-mosby-station-view", binding.profile.map, binding.name, binding.profile.station, session and session.state or binding.state,
        session and session.sequence or 0, session and session.open or 0, session and session.blank or "-", session and session.filled or "-")}
end

function view.start(bridge, is_downed_apply)
    events, heartbeat = bridge .. "/native-mosby-station-events", bridge .. "/native-mosby-station-heartbeat"
    is_downed = is_downed_apply
end

return view
