local clock = {}
local world_path = require("world_path")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_CLOCK") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_CLOCK_FIXTURES") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/Time_Location_Header_Widget.Time_Location_Header_Widget_C"
local max_sequence, max_ms = 9007199254740991, 922337203685477
local lease, fixture

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function context(state, phase)
    if not enabled or phase ~= "connected" or state.listening ~= true or state.server ~= true
        or not world_path.leaf(state.map) then return end
    local world, controller, detail = state.world, state.controller, state.local_pawn
    local pawn = detail and detail.pawn
    if not valid(world) or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map
        or not exact(pawn, pawn_path) or not exact(controller, controller_path)
        or not same(detail.controller_object, controller) or not same(pawn.Controller, controller)
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or pawn:HasAuthority() or controller:HasAuthority() or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() or controller.Loading ~= false
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return end
    local driver, instance = world.NetDriver, world.OwningGameInstance
    local connection = valid(driver) and driver.ServerConnection
    if not valid(connection) or not same(driver.World, world) or not same(connection.Driver, driver)
        or not valid(instance) or not same(instance.SaveSlot_Ref, pawn.Save) then return end
    return {world = world, pawn = pawn, controller = controller, instance = instance, connection = connection,
        map = state.map, name = detail.name}
end

local function same_context(first, second)
    return first and second and first.map == second.map and first.name == second.name
        and same(first.world, second.world) and same(first.pawn, second.pawn) and same(first.controller, second.controller)
        and same(first.instance, second.instance) and same(first.connection, second.connection)
end

local function number(value, min, max)
    if type(value) ~= "string" or #value > 16 or not value:match("^%d+$") then return end
    local n = math.tointeger(tonumber(value))
    return n and n >= min and n <= max and n or nil
end

local function read(current)
    if type(SilverfishReadNativeGuestClock) ~= "function" then return end
    local sequence, ms, hits, original, epoch = SilverfishReadNativeGuestClock(current.pawn:GetAddress())
    if current.native then
        if epoch ~= current.epoch or math.type(sequence) ~= "integer" or sequence < 1 or sequence > max_sequence
            or math.type(ms) ~= "integer" or ms < 0 or ms > max_ms then return end
    elseif sequence ~= current.sequence or ms ~= current.ms then
        return
    end
    if math.type(hits) ~= "integer" or hits < 0 or hits > 100000
        or (hits > 0 and math.type(original) ~= "integer") then return end
    return {sequence = sequence, ms = ms, hits = hits, original = original}
end

function clock.clear()
    if lease and type(SilverfishClearNativeGuestClock) == "function" then SilverfishClearNativeGuestClock() end
    lease, fixture = nil, nil
end

local function observed_seat(state, phase, current)
    local local_player = state.local_pawn
    return phase == "connected" and state.listening == true and state.server == true
        and current.map == state.map and local_player and local_player.name == current.name
        and same(local_player.pawn, current.pawn) and same(state.world, current.world)
        and same(state.controller, current.controller)
end

function clock.sync(state, phase)
    if not lease then return end
    if type(SilverfishVerifyNativeGuestClockSeat) == "function" and observed_seat(state, phase, lease)
        and SilverfishVerifyNativeGuestClockSeat(lease.world:GetAddress(), lease.pawn:GetAddress(),
            lease.controller:GetAddress(), lease.instance:GetAddress(), lease.connection:GetAddress(),
            lease.native and lease.epoch or lease.sequence) == true then
        return
    end
    if not same_context(context(state, phase), lease) then clock.clear() end
end

local function load_widget()
    local class = StaticFindObject(widget_path)
    if not valid(class) then
        LoadAsset(widget_path)
        class = StaticFindObject(widget_path)
    end
    return class
end

local function probe(current)
    local active = lease
    if not fixtures or not same_context(current, active) or not read(active)
        or type(SilverfishProbeNativeGuestClock) ~= "function" then error("guest_clock_fixture_scope_unavailable", 0) end
    local class = load_widget()
    local widgets = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
    local widget = valid(class) and valid(widgets) and widgets:Create(current.pawn, class, current.controller)
    if not exact(widget, widget_path) or widget:IsInViewport() or not same(widget:GetOwningPlayer(), current.controller)
        or not exact(widget.TextBlock_Name, "/Script/UMG.TextBlock") then error("guest_clock_fixture_widget_unavailable", 0) end
    -- Offscreen Create does not necessarily dispatch Construct. Set its sole
    -- read dependency on this scratch widget before running the actual Tick.
    widget.GameInstance = current.instance
    if not same(widget.GameInstance, current.instance) then error("guest_clock_fixture_instance_unavailable", 0) end
    local ok, status = SilverfishProbeNativeGuestClock(widget:GetAddress())
    if ok ~= true or status ~= "stock_widget_tick_verified" then error(tostring(status), 0) end
    local report = read(active)
    local text = widget.TextBlock_Name:GetText():ToString()
    local minutes = report and report.ms // 60000 or 0
    local time = ("%02d:%02d"):format((minutes // 60) % 24, minutes % 60)
    if not report or report.hits < 1 or type(text) ~= "string" or #text > 512 or not text:find(time, 1, true) then
        error("guest_clock_fixture_stock_text_unverified", 0)
    end
    fixture = { epoch = active.epoch, sequence = report.sequence, ms = report.ms, hits = report.hits, text = text:gsub("%c", " ") }
    return "stock guest time widget verified"
end

function clock.command(fields, state, phase)
    local action = fields[2]
    if action == "native-guest-clock-clear" and #fields == 2 then
        clock.clear()
        return "guest clock projection cleared"
    end
    if action ~= "native-guest-clock-apply" and action ~= "native-guest-clock-fixture" then return end
    local current = context(state, phase)
    if not current then
        clock.clear()
        error("guest_clock_exact_client_unavailable", 0)
    end
    if action == "native-guest-clock-fixture" then
        if #fields ~= 2 then error("guest_clock_fixture_arguments", 0) end
        return probe(current)
    end
    local epoch, sequence, map, ms = fields[3], number(fields[4], 1, max_sequence), fields[5], number(fields[6], 0, max_ms)
    if #fields ~= 6 or type(epoch) ~= "string" or #epoch > 128 or not epoch:match("^[%w_%-]+$")
        or not sequence or map ~= current.map or not ms or type(SilverfishSetNativeGuestClock) ~= "function" then
        error("guest_clock_exact_frame_required", 0)
    end
    if lease and not same_context(current, lease) then clock.clear() end
    if lease and (lease.epoch ~= epoch or sequence <= lease.sequence) then
        error("guest_clock_frame_stale_or_wrong_epoch", 0)
    end
    if not valid(load_widget()) then error("guest_clock_widget_class_unavailable", 0) end
    local ok, status, native = SilverfishSetNativeGuestClock(current.pawn:GetAddress(), current.controller:GetAddress(),
        current.world:GetAddress(), sequence, ms, epoch, map, current.name)
    if ok ~= true or status ~= "armed" then error(tostring(status), 0) end
    current.epoch, current.sequence, current.ms, current.native = epoch, sequence, ms, native == true
    lease = current
    return "authenticated guest clock projection armed", current
end

local function lease_row(current, report, status_row)
    return status_row("native-guest-clock", current.map, current.name, current.epoch, report.sequence, report.ms,
        report.hits > 0 and "projected" or "armed", report.hits, report.original or "-")
end

function clock.command_reply(fields, state, phase, sequence, status_row)
    local accepted, message, current = pcall(clock.command, fields, state, phase)
    local report = accepted and current and read(current)
    if accepted and (not report or report.sequence ~= current.sequence or report.ms ~= current.ms) then
        return status_row("native-guest-clock-reply", 1, fields[1], sequence, "error",
            "guest_clock_lease_readback_unavailable") .. "\n"
    end
    local header = status_row("native-guest-clock-reply", 1, fields[1], sequence,
        accepted and "accepted" or "error", message) .. "\n"
    return report and header .. lease_row(current, report, status_row) .. "\n" or header
end

function clock.rows(state, phase, status_row)
    local current = lease
    if not current or not observed_seat(state, phase, current) then return {} end
    -- The native read also validates the connection, possession, save,
    -- loading state and lease before returning a row for this seat.
    local checked, report = pcall(read, current)
    if not checked then return {} end
    if not report then return {} end
    local rows = {lease_row(current, report, status_row)}
    if fixture and fixture.epoch == lease.epoch then
        rows[#rows + 1] = status_row("native-guest-clock-fixture", current.map, current.name, fixture.epoch,
            fixture.sequence, fixture.ms, fixture.hits, fixture.text)
    end
    return rows
end

return clock
