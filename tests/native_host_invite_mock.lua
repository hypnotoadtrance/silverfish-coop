local journal_class = "/Game/Classes/GUI/Menu_QuestLog_Widget.Menu_QuestLog_Widget_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local viewport_class = "/Script/Engine.GameViewportClient"
local change = 0
local function object(class)
    change = change + 1
    local id = change
    return {IsValid = function() return true end, GetAddress = function() return id end,
        GetClass = function() return class end, GetOuter = function(self) return self.outer end,
        GetWorld = function(self) return self.world end, IsActorBeingDestroyed = function() return false end,
        HasAuthority = function() return true end, IsInViewport = function(self) return self.visible == true end,
        IsVisible = function(self) return self.visible == true end}
end

local registered = {}
for _, path in ipairs({journal_class, controller_class, pawn_class, viewport_class}) do registered[path] = object() end
local engine, world = object(), object()
local controller, pawn = object(registered[controller_class]), object(registered[pawn_class])
local viewport = object(registered[viewport_class])
local instance, statics = object(), object()
local stack = {GetArrayNum = function(self) return #self end}
local paused, downed, focused = false, false, false
local open_calls, close_calls, copies, operations = 0, 0, 0, 0
function controller:IsLocalController() return true end
function controller:OpenMenu(class, result)
    assert(class == registered[journal_class] and #stack == 0)
    local menu = object(class)
    function menu:GetOwningPlayer() return controller end
    menu.outer, menu.visible = instance, true
    stack[1], result.CreatedMenu = menu, menu
    open_calls = open_calls + 1
end
function controller:CloseMenu(result)
    assert(#stack == 1 and stack[1]:GetClass() == registered[journal_class])
    stack[1].visible = false
    stack[1], result.Success = nil, true
    close_calls = close_calls + 1
end
function pawn:IsLocallyControlled() return true end
controller.world, controller.Loading, controller.Pawn, controller.MenuStack = world, false, pawn, stack
pawn.world, pawn.Controller, pawn.Dead = world, controller, false
world.NetDriver = object()
viewport.World, viewport.outer, instance.outer = world, engine, engine
function statics:GetGameInstance(who) assert(who == controller); return instance end
function statics:IsGamePaused(who) assert(who == controller); return paused end
registered["/Script/Engine.Default__GameplayStatics"] = statics
local guid = object()
registered["/Script/Engine.Default__KismetGuidLibrary"] = guid
function guid:NewGuid()
    operations = operations + 1
    return {A = 1, B = -1, C = 0x33, D = operations}
end

local state = {map = "/Game/Classes/Maps/Observatory", world = world, controller = controller,
    local_pawn = {pawn = pawn}, listening = true, server = false}
function pawn:GetFullName()
    local level = state.map:match("[^/]+$")
    return "Player_FP_C " .. state.map .. "." .. level .. ":PersistentLevel.Player_FP_C_0"
end
local flags = {SILVERFISH_COOP_HEADLESS = "1", SILVERFISH_COOP_NATIVE_FIXTURES = "1"}
local time = 100
local offer = "bridge/host-invite-offer.tsv"
local request = "bridge/host-invite-request.tsv"
local response = "bridge/host-invite-response.tsv"
local generation, epoch = string.rep("a", 32), string.rep("b", 32)
local files, hooks = {}, {}
local game_tick, view, editing
local payload = '{"credential":"private only"}'
local display = {}
function display.mount(context, caption)
    assert(context.menu == stack[#stack] and context.controller == controller and type(caption) == "string")
    view = object()
    view.caption, view.visible = caption, true
    return view
end
function display.remove(widget) widget.visible = false end

local editor = {}
function editor.mount(frame, phase, field, _, initial, probe)
    assert(not editing and probe(frame, phase).menu == stack[#stack])
    editing = {field = field, value = initial or "", probe = probe}
    return editing
end
function editor.focus(handle, frame, phase)
    assert(handle == editing and handle.probe(frame, phase))
    return false
end
function editor.read(handle, frame, phase)
    assert(handle == editing and handle.probe(frame, phase))
    return handle.value
end
function editor.write(handle, frame, phase, value)
    assert(handle == editing and handle.probe(frame, phase))
    handle.value = value
    return value
end
function editor.remove(handle)
    assert(handle == editing)
    editing = nil
    return true
end

local env = setmetatable({
    require = function(name)
        if name == "native_title_display" then return display end
        if name == "native_title_settings_widget" then return editor end
        assert(name == "players")
        return {is_downed = function(who) assert(who == pawn); return downed end}
    end,
    StaticFindObject = function(path) return registered[path] end,
    FindFirstOf = function(class) assert(class == "GameViewportClient"); return viewport end,
    LoadAsset = function(path) error("Unexpected asset load: " .. path) end,
    os = {
        getenv = function(name) return flags[name] end,
        time = function() return time end,
        rename = function(source, target)
            assert(target == request)
            if files[target] then return false end
            files[source], files[target] = nil, files[source]
            return true
        end,
        remove = function(path) files[path] = nil end,
    },
    io = {open = function(path, mode)
        if mode == "rb" then
            if not files[path] then return end
            return {read = function(_, cap) return files[path]:sub(1, cap) end, close = function() return true end}
        end
        assert(mode == "wb" and path:sub(1, #request) == request)
        return {write = function(_, ...) files[path] = table.concat({...}); return true end, close = function() return true end}
    end},
    Key = {F7 = "F7", F8 = "F8", F9 = "F9"},
    RegisterKeyBind = function(key, action) hooks[key] = action end,
    LoopInGameThreadWithDelay = function(_, action) game_tick = action end,
    SilverfishNativeGameWindowFocused = function() return focused end,
    SilverfishCopyHostInvitation = function(text, id)
        assert(focused and text == payload and #id == 32)
        copies = copies + 1
        return true
    end,
}, {__index = _G})

local function build()
    local module = assert(loadfile("mod/SilverfishCoop/Scripts/native_host_invite.lua", "t", env))()
    module.start("bridge")
    return module
end
local module = build()
local function action(verb, value)
    return pcall(module.command, {"fixture", "native-host-invite-fixture", verb, value}, state, "hosting")
end
local function report()
    local rows = module.rows(state, "hosting", table.pack)
    if #rows == 0 then return end
    assert(rows[1][1] == "native-host-invite-journal" and rows[1][2] == state.map)
    return rows[1][3]
end
local function tick() module.sync(state, "hosting") end

assert(not report())
assert(action("open") and open_calls == 1 and report() == "journal")
local ok, reason = action("begin")
assert(not ok and reason == "native_host_invite_offer_unavailable")
files[offer] = ("1\t%s\t%s\t110\n"):format(generation, epoch)
assert(not action("begin"))
files[offer] = ("1\t%s\t%s\t104\n"):format(generation, epoch)
assert(action("begin") and editing.field == "participant" and report() == "editing")
assert(action("participant", "Alice Test") and editing.field == "starter")
assert(action("starter", "Mule") and editing.field == "address")
assert(action("address") and editing == nil)
assert(action("submit") and report() == "pending" and operations == 1)
local id = "00000001ffffffff0000003300000001"
assert(files[request] == ("1\t%s\t%s\t%s\tAlice Test\tMule\t\n"):format(generation, epoch, id))
local export_file = "bridge/host-invite-" .. id .. ".invite.json"
files[export_file] = payload .. "\r\n"
files[response] = ("1\t%s\t%s\t%s\tok\texported\n"):format(generation, epoch, id)
tick()
assert(report() == "pending")
files[request] = nil
tick()
ok, reason = action("observe")
assert(ok and reason == "native_host_invite_phase=exported;op=" .. id)
assert(not action("copy") and copies == 0 and not view.caption:find("credential", 1, true))
paused = true
tick()
assert(not report())
paused = false
downed = true
assert(not report())
downed = false
assert(action("close") and not report() and close_calls == 1)

-- Exercise the physical dispatch branch with scoped mocks and no system clipboard.
flags.SILVERFISH_COOP_HEADLESS = nil
module = build()
controller:OpenMenu(registered[journal_class], {})
tick()
local function press(key) hooks[key](); game_tick() end
press("F7")
assert(report() == "journal" and not editing)
focused = true
press("F7")
assert(report() == "editing" and editing.field == "participant")
editing.value = "Bob Test"
press("F8")
editing.value = "Pip"
press("F8")
editing.value = "127.0.0.1"
press("F8")
assert(report() == "editing" and not files[request])
press("F7")
id = "00000001ffffffff0000003300000002"
assert(files[request] == ("1\t%s\t%s\t%s\tBob Test\tPip\t127.0.0.1\n"):format(generation, epoch, id))
files[request] = nil
files[response] = ("1\t%s\t%s\t%s\tok\texported\n"):format(generation, epoch, id)
files["bridge/host-invite-" .. id .. ".invite.json"] = payload .. "\n"
tick()
assert(report() == "exported" and copies == 0)
focused = false
press("F7")
assert(copies == 0)
focused = true
press("F7")
assert(copies == 1 and report() == "exported")
files["bridge/host-invite-" .. id .. ".invite.json"] = '{"replaced":true}\n'
press("F7")
assert(copies == 1 and report() == "refused")
world.NetDriver.ServerConnection = object()
tick()
assert(not report() and not view.visible)

local market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
local package_guid = string.rep("C", 32)
local first_offer, second_offer = string.rep("d", 32), string.rep("e", 32)
local scout = "11e3913f-d53e-43c3-bbe9-893b5a38ebaf"
local courier = "d117a077-d233-4b16-8673-24585da3f398"
local scout_label, courier_label = "Courier (Guest 1)", "Courier (Guest 2)"

local function handoff_command(verb, ...)
    return pcall(module.command, {"bridge", "native-package-handoff-" .. verb, ...}, state, "hosting")
end

local function install_offer(offer_id, last_label)
    return handoff_command("offer", offer_id, package_guid, scout, scout_label, courier, last_label or courier_label)
end

local function package_status(expected_state, offer_id, selected)
    local rows = module.rows(state, "hosting", table.pack)
    local journal, intent = rows[2], rows[3]
    assert(journal and #journal == 5 and journal[1] == "native-package-handoff-journal"
        and journal[2] == market and journal[3] == pawn:GetFullName()
        and journal[4] == expected_state and journal[5] == (offer_id or "-"))
    if selected then
        assert(intent and #intent == 5 and intent[1] == "native-package-handoff-intent"
            and intent[2] == market and intent[3] == pawn:GetFullName()
            and intent[4] == offer_id and intent[5] == selected)
    else
        assert(not intent)
    end
end

world.NetDriver.ServerConnection = nil
controller:CloseMenu({})
tick()
assert(not install_offer(first_offer))
controller:OpenMenu(registered[journal_class], {})
tick()
assert(not install_offer(first_offer) and #module.rows(state, "hosting", table.pack) == 1)
controller:CloseMenu({})
state.map = market
controller:OpenMenu(registered[journal_class], {})
tick()
package_status("idle")
assert(install_offer(first_offer) and view.caption:find("F8   Give", 1, true))
press("F8")
assert(view.caption:find(scout_label, 1, true))
press("F8")
assert(view.caption:find(courier_label, 1, true))
assert(install_offer(first_offer) and view.caption:find(courier_label, 1, true))
assert(not install_offer(first_offer, "Changed label"))
package_status("choosing", first_offer)
hooks.F7()
assert(install_offer(second_offer))
game_tick()
package_status("idle", second_offer)
assert(view.caption:find("F8   Give", 1, true))
press("F8")
press("F8")
assert(view.caption:find(courier_label, 1, true))
view.visible = false
press("F7")
package_status("choosing", second_offer)
view.visible = true
press("F7")
package_status("submitting", second_offer, courier)
time = 109
tick()
assert(not handoff_command("result", first_offer, "transferred"))
assert(handoff_command("clear"))
package_status("submitting", second_offer, courier)
assert(handoff_command("result", second_offer, "transferred"))
assert(handoff_command("result", second_offer, "transferred"))
package_status("transferred")
assert(view.caption:find("package given", 1, true))
press("F9")
package_status("idle")
controller:CloseMenu({})
tick()
assert(not report())

flags.SILVERFISH_COOP_HEADLESS = "1"
module = build()
assert(action("open"))
package_status("idle")
assert(not action("handoff-open"))
assert(install_offer(first_offer))
assert(action("handoff-open") and action("handoff-next"))
assert(action("handoff-back"))
package_status("idle", first_offer)
assert(action("handoff-open") and action("handoff-confirm"))
package_status("submitting", first_offer, scout)
assert(action("close"))
assert(not report() and not handoff_command("result", first_offer, "transferred"))
assert(action("open"))
package_status("idle")
assert(action("close"))

print("Qualified host invitation and package handoff Lua mock passed")
