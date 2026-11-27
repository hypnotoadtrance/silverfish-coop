package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
-- Run with Lua 5.4 from the repository root.
local flags = {
    SILVERFISH_COOP_HEADLESS = "1",
    SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES = "1",
    SILVERFISH_COOP_NATIVE_LOOT = "1",
}
os.getenv = function(name) return flags[name] end

local function object(address, name)
    return {IsValid = function() return true end, GetAddress = function() return address end, GetFullName = function() return name end}
end

local world = object(1, "world")
local pawn = object(2, "local guest")
local area = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local path = area .. ".City_01_P:PersistentLevel.LootCrate_Small_BP_C_5"
local profile = {index = 1, map = area, path = path, name = "LootCrate_Small_BP_C " .. path}
local crate = object(3, profile.name)
crate.IsActorBeingDestroyed = function() return false end
local camera_type, box_type, mesh_type = object(4, "camera class"), object(5, "box class"), object(6, "mesh class")
local camera, box, door = object(7, "camera"), object(8, "exact crate box"), object(9, "door")
local sibling = object(10, "another crate box")
pawn.GetWorld, crate.GetWorld = function() return world end, function() return world end
pawn.Camera, crate.Box, crate.SM_Prop_Cabinet_Small_Door_01, crate.Looted = camera, box, door, false
camera.GetClass, box.GetClass, door.GetClass = function() return camera_type end, function() return box_type end, function() return mesh_type end
camera.GetOwner, box.GetOwner, door.GetOwner = function() return pawn end, function() return crate end, function() return crate end
box.GetCollisionEnabled = function() return 1 end
box.GetScaledBoxExtent = function() return {X = 24, Y = 20, Z = 30} end
box.GetRightVector = function() return {X = 0, Y = 1, Z = 0} end
box.GetUpVector = function() return {X = 0, Y = 0, Z = 1} end
camera.K2_GetComponentLocation = function() return {X = 0, Y = 0, Z = 40} end
box.K2_GetComponentLocation = function() return {X = 160, Y = 0, Z = 40} end
camera.RelativeRotation = {Pitch = 0, Yaw = 90, Roll = 0}
camera.K2_SetWorldRotation = function(self, aim) self.RelativeRotation = aim end
camera.K2_SetRelativeRotation = camera.K2_SetWorldRotation

local objects = { [path] = crate, ["/Script/Engine.CameraComponent"] = camera_type,
    ["/Script/Engine.BoxComponent"] = box_type, ["/Script/Engine.StaticMeshComponent"] = mesh_type }
StaticFindObject = function(name) return objects[name] end
package.loaded.native_loot = {profile = function(map, target) if map == area and target == profile.name then return profile end end}

local chosen, sequence = box, 0
pawn.LookTrace = function(self)
    self.InteractionTarget_Component = math.abs(camera.RelativeRotation.Yaw) < 15 and chosen or nil
    self.CanInteract = self.InteractionTarget_Component ~= nil
end
SilverfishSetNativeLootCrateInputScope = function(pawn_address, host, index)
    return pawn_address == pawn:GetAddress() and not host and index == profile.index
end
SilverfishReadNativeLootCrateInput = function(index)
    assert(index == profile.index)
    return true, sequence, "live", box:GetAddress()
end
SilverfishSetNativeInteractScope = function(pawn_address, host) return pawn_address == pawn:GetAddress() and not host end
SilverfishProbeNativeInteractInput = function(address, stock)
    assert(stock == true and address == pawn:GetAddress())
    assert(pawn.CanInteract and pawn.InteractionTarget_Component == box and math.abs(camera.RelativeRotation.Yaw) < 15)
    sequence = sequence + 1
    return true, "stock_started_consumed"
end

local input = assert(loadfile("mod/SilverfishCoop/Scripts/native_loot_crate_input.lua"))()
local state = {map = area, world = world, server = true, listening = true, local_pawn = {pawn = pawn, name = pawn:GetFullName()}}
local request = {"request", "native-loot-crate-fixture", profile.name}
assert(input.command(request, state, "connected") == "stock crate interaction intercepted")
assert(sequence == 1 and camera.RelativeRotation.Yaw == 90 and pawn.InteractionTarget_Component == nil)

chosen = sibling
local accepted, failure = pcall(input.command, request, state, "connected")
assert(not accepted and failure:find("native_loot_crate_stock_fixture_focus_unavailable:another crate box", 1, true) == 1)
assert(sequence == 1 and camera.RelativeRotation.Yaw == 90 and pawn.InteractionTarget_Component == nil)
print("The first registered crate uses stock LookTrace and restores the camera")

profile.small_cabinet = true
local frame, second_door = object(11, "exact cabinet frame"), object(12, "exact second cabinet door")
crate.SM_Prop_Cabinet_Small_02, crate.SM_Prop_Cabinet_Small_Door_02 = frame, second_door
frame.GetClass, second_door.GetClass = door.GetClass, door.GetClass
frame.GetOwner, second_door.GetOwner = door.GetOwner, door.GetOwner
door.GetAttachParent = function() return frame end
box.GetAttachParent, second_door.GetAttachParent = door.GetAttachParent, door.GetAttachParent
local function set_rotation(component, angle) component.RelativeRotation = angle end
door.RelativeRotation, second_door.RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}, {Pitch = 0, Yaw = 0, Roll = 0}
door.K2_SetRelativeRotation, second_door.K2_SetRelativeRotation = set_rotation, set_rotation
local query_collision = 1
box.GetCollisionEnabled = function() return query_collision end
box.SetCollisionEnabled = function(_, value) query_collision = value end
local project = {"request", "native-loot-crate-project", "exhausted", profile.name}
second_door.GetAttachParent = function() return sibling end
assert(not pcall(input.command, project, state, "connected"))
assert(query_collision == 1 and crate.Looted == false)
second_door.GetAttachParent = door.GetAttachParent
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(query_collision == 0 and crate.Looted and door.RelativeRotation.Yaw == 95 and second_door.RelativeRotation.Yaw == -95)
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified" and sequence == 1)
print("Per-cabinet projection proves both exact doors and never replays stock Interact")

profile.door_locations = {{X = -49, Y = 23, Z = 68}, {X = 49, Y = 23, Z = 68}}
door.RelativeLocation, second_door.RelativeLocation = {X = -47, Y = 20, Z = 68}, {X = 47, Y = 20, Z = 68}
local function set_pose(component, location, angle)
    component.RelativeLocation, component.RelativeRotation = location, angle
end
door.K2_SetRelativeLocationAndRotation, second_door.K2_SetRelativeLocationAndRotation = set_pose, set_pose
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(door.RelativeLocation.X == -49 and second_door.RelativeLocation.X == 49)
assert(door.RelativeLocation.Y == 23 and second_door.RelativeLocation.Y == 23 and sequence == 1)
print("A cabinet with fixed door locations projects the full stock pose")

profile.small_cabinet, profile.door_locations = nil, nil
profile.random_lid, profile.lid_ignores_channel = {"Pitch", 95, 105}, 5
crate.Looted, query_collision = false, 1
door.RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}
local response = 2
door.GetCollisionResponseToChannel = function(_, channel) assert(channel == 5); return response end
door.SetCollisionResponseToChannel = function(_, channel, value) assert(channel == 5); response = value end
door.K2_SetRelativeRotation = function(self, angle)
    self.RelativeRotation = {Pitch = 180 - angle.Pitch, Yaw = 180, Roll = 180}
end
assert(not pcall(input.command, project, state, "connected"))
project[5] = "106"
assert(not pcall(input.command, project, state, "connected"))
assert(response == 2 and query_collision == 1 and not crate.Looted)
project[5] = "99"
assert(input.command(project, state, "connected") == "stock crate exhausted projection verified")
assert(response == 0 and query_collision == 0 and crate.Looted and door.RelativeRotation.Pitch == 81)
package.loaded.native_loot.profiles = function() return {profile} end
local function row(...)
    return table.concat({...}, "\t")
end

local rows = input.rows(state, "connected", row)
assert(rows[1] == row("native-loot-crate-state", profile.index, "exhausted", 0, "-", 99))
print("An authored random lid pose keeps the stock collision response and equivalent rotation")

local action = "native-loot-crate-diagnostic"
local query = {"read", action, area, pawn:GetFullName(), profile.name}
local _, diagnostic = input.diagnostic(query, state, "connected", row)
assert(diagnostic.row:find(action .. "\t" .. area .. "\t" .. profile.name, 1, true) == 1)
assert(diagnostic.row:find("\tconnected\tactive\tlive\tlive\t1\t0\t180", 1, true))
assert(diagnostic.pawn == pawn and diagnostic.world == world and diagnostic.phase == "connected")
assert(not pcall(input.diagnostic, {"id", action, "different map", query[4], query[5]}, state, "connected", row))
assert(not pcall(input.diagnostic, {"id", action, area, "old pawn", query[5]}, state, "connected", row))
assert(not pcall(input.diagnostic, {"id", action, area, query[4], "unregistered"}, state, "connected", row))
assert(not pcall(input.diagnostic, {"id", action, area, query[4], query[5], "extra crate"}, state, "connected", row))
assert(not pcall(input.diagnostic, query, state, "hosting", row))
crate.IsActorBeingDestroyed = function() return true end
assert(not pcall(input.diagnostic, query, state, "connected", row))
crate.IsActorBeingDestroyed = function() return false end

local function upvalue(callback, wanted, replacement)
    for index = 1, math.huge do
        local name, value = debug.getupvalue(callback, index)
        if not name then break end
        if name == wanted then
            if replacement then debug.setupvalue(callback, index, replacement) end
            return value
        end
    end
    error(wanted .. " was not found")
end

local function status_case(installed, headless, resident)
    local callback, memory, file, reply
    local attached, time, collections = true, 0, 0
    local modules = setmetatable({}, {__index = function(_, key)
        if key == "rows" or key:sub(-5) == "_rows" then return function() return {} end end
        return function() end
    end})
    local env = setmetatable({
        require = function(name)
            if name == "native_loot_crate_input" then return input end
            if name == "native_flashlight" then
                return setmetatable({rows = function() end}, {__index = modules})
            end
            if name == "players" then
                return setmetatable({critters = {row = function() end}}, {__index = modules})
            end
            if name == "native_stash_view" then
                return setmetatable({memory_body_limit = function() return 32768 end}, {__index = modules})
            end
            if name == "native_cubes" then return {new = function() return modules end} end
            if name == "native_stock_creator" then
                return setmetatable({rows = function() collections = collections + 1; return {} end}, {__index = modules})
            end
            return modules
        end,
        LoopInGameThreadWithDelay = function(_, fn) callback = fn end,
        os = {
            clock = function() return time end,
            getenv = function(key)
                if key == "SILVERFISH_COOP_BRIDGE" then return "mock" end
                if key == "SILVERFISH_COOP_RESIDENT_TITLE" and resident then return "1" end
                if key == "SILVERFISH_COOP_INSTALLED_TITLE" and installed then return "1" end
                if key == "SILVERFISH_COOP_HEADLESS" and headless then return "1" end
            end,
            rename = function() return true end,
        },
        io = {open = function()
            return {write = function(_, frame) file = frame; return true end, close = function() return true end}
        end},
        SilverfishPublishLocalBridgeStatus = function(frame) memory = attached and frame or nil; return attached end,
        SilverfishPublishLocalBridgeReply = function(_, frame) reply = frame end,
        SilverfishClaimLocalBridgeCommand = function(ticket) return ticket == 1 end,
    }, {__index = _G})
    assert(loadfile("mod/SilverfishCoop/Scripts/main.lua", "t", env))()
    local publication = upvalue(callback, "status_publication")
    local tick = upvalue(publication.dispatch, "tick")
    upvalue(tick, "observe_session", function() return state, "connected" end)
    local write = upvalue(tick, "write_status")
    local function status(fields, pending, requested)
        time = time + .3
        write(state, "connected", nil, fields, pending, requested)
        assert((memory or file):find("native-loot-crate-state", 1, true))
    end
    local function has_detail(frame)
        return frame and frame:find(action .. "\t", 1, true)
    end

    state.pawns, state.clients = {}, 0
    status()
    assert(has_detail(memory))
    status()
    local before, frame = collections, memory
    time = time + .025
    write(state, "connected")
    if installed and not headless and resident then
        assert(collections == before and memory == frame, "An unpublished stable resident pass must skip collection")
        assert(not has_detail(memory) and not file)

        state.clients = 1
        write(state, "connected")
        assert(collections == before + 1, "A membership change still collects before the publish deadline")
        state.clients = 0
        write(state, "connected", "mock failure")
        assert(collections == before + 2, "An observation failure still enters the full writer path")

        local pending = {ticket = 1, fields = query}
        tick(query, pending)
        assert(collections == before + 3 and memory == frame, "An immediate command gets a full receipt without forcing a publish")
        assert(not has_detail(memory) and has_detail(reply))
        assert(reply:find("ack\t" .. query[1] .. "\taccepted", 1, true))
        local _, detail_end = reply:find(action .. "\t", 1, true)
        assert(detail_end and not reply:find(action .. "\t", detail_end + 1, true))
        local stale = {}
        for key, value in pairs(diagnostic) do stale[key] = value end
        stale.pawn = sibling
        status(query, pending, stale)
        assert(not has_detail(reply) and reply:find("native_loot_crate_diagnostic_scope_changed", 1, true))

        status(query, nil, diagnostic)
        assert(not has_detail(memory) and has_detail(file))
        attached, file = false, nil
        status()
        assert(not memory and has_detail(file))
    else
        assert(collections == before + 1, "Legacy and mirrored sessions retain their full collection path")
        assert(has_detail(memory))
        if headless then assert(has_detail(file)) end
    end
end

status_case(true, false, true)
status_case(true, true, true)
status_case(false, false, true)
status_case(true, false, false)
print("Only healthy installed resident status omits full crate diagnostics, and one exact query replies locally")
