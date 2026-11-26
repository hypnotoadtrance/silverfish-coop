package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local tick, frames, work = nil, 100, 0
local actor_lookups, qualifiers, encodes = 0, 0, 0
os.time = function() return frames end
os.clock = function() return work end
function LoopInGameThreadWithDelay(delay, callback)
    assert(delay == 16)
    tick = callback
end
function SilverfishStockDoorGuardAvailable() return true end

local function object(fields)
    fields = fields or {}
    fields.GetAddress = function(self) return self end
    fields.IsValid = function(self) return not self.invalid end
    return fields
end
local function vector() return {X = 0, Y = 0, Z = 0} end

local door_class = object()
local arrow_type, mesh_type, box_type = object(), object(), object()
local statics = object()
local catalog, travel_pending = {}, false
function SilverfishNativeListenTravelPending() return travel_pending end
function SilverfishSnapshotNativeWorldActorPaths(address, name, kind)
    assert(address:GetFullName() == "World " .. name and kind == "hinged-door")
    return table.concat(catalog[address], "\n")
end

local paths = {
    ["/Game/Assets/StaticMeshes/Doors/BasicDoor_Master.BasicDoor_Master_C"] = door_class,
    ["/Script/Engine.Default__GameplayStatics"] = statics,
    ["/Script/Engine.ArrowComponent"] = arrow_type,
    ["/Script/Engine.StaticMeshComponent"] = mesh_type,
    ["/Script/Engine.BoxComponent"] = box_type,
}
function StaticFindObject(path)
    if path:find(":PersistentLevel", 1, true) then actor_lookups = actor_lookups + 1 end
    return paths[path]
end

local function make_state(name, count)
    local map = "/Game/Classes/Maps/Maps_OverWorld/" .. name
    local world = object({PersistentLevel = object(), doors = {}})
    function world:GetFullName() return "World " .. map .. "." .. name end
    catalog[world] = {}
    for index = 1, count do
        local path = map .. "." .. name .. ":PersistentLevel.BasicDoor_" .. index
        local door = object({Locked = false, LockBroken = false, Open = false, name = "BasicDoor_C " .. path})
        function door:HasAuthority() return true end
        function door:GetWorld() return world end
        function door:GetOuter() return world.PersistentLevel end
        function door:IsA(class) return class == door_class end
        function door:IsActorBeingDestroyed() return self.destroying == true end
        function door:GetFullName()
            qualifiers = qualifiers + 1
            work = work + .0005
            return self.name
        end
        local function part(class, parent)
            return object({GetClass = function() return class end, GetAttachParent = function() return parent end,
                GetOwner = function() return door end, K2_GetComponentLocation = vector,
                GetCollisionEnabled = function() return 3 end})
        end
        door.RootComponent = object()
        door.Arrow = part(arrow_type, door.RootComponent)
        door.Arrow.RelativeLocation, door.Arrow.RelativeRotation = vector(), {Pitch = 0, Yaw = 0, Roll = 0}
        function door.Arrow:K2_SetRelativeLocationAndRotation(location, rotation)
            self.RelativeLocation, self.RelativeRotation = location, rotation
        end
        door.DoorMesh = part(mesh_type, door.Arrow)
        door.Box_Knob = part(box_type, door.DoorMesh)
        world.doors[index], paths[path], catalog[world][index] = door, door, path
    end
    return {map = map, world = world, server = true, listening = true}
end

local function row(...)
    encodes = encodes + 1
    return {...}
end
local function current_rows(doors, state, command)
    return doors.rows(state, "connected", row, command)
end

local guest = object({Dead = false})
package.loaded.guest_save = {exact_item_context = function(_, phase, name)
    assert(phase == "hosting" and name == "guest pawn")
    return guest
end}

local doors = require("native_stock_door")
doors.start()
local state = make_state("CrossRoads_P", 30)
assert(#current_rows(doors, state) == 0 and qualifiers == 0, "dense census must not qualify every actor in status")
tick()
assert(qualifiers > 0 and qualifiers < 10, "qualification should stop when the game-thread time budget expires")

local target = state.world.doors[30]
local command = {"request", "native-stock-door-project", state.map, target.name .. "|0|0|1|0|90|0|0|5|0"}
local before_exact = actor_lookups
assert(doors.command(command, state, "connected") and target.Open and actor_lookups == before_exact + 1)
local exact = current_rows(doors, state, command)
assert(#exact == 1 and exact[1][1] == "native-stock-door-state"
    and exact[1][3] == "BasicDoor_30" and exact[1][6] == 1, "the exact action must read back before ack")

for _ = 1, 40 do
    tick()
    current_rows(doors, state)
end
assert(#current_rows(doors, state) == 30, "the bounded pass must still publish every valid door")

target.destroying = true
assert(not pcall(doors.command, command, state, "connected"), "a cached door undergoing destruction cannot be projected")
target.destroying = false

frames = frames + 1
local before = qualifiers
assert(#current_rows(doors, state) == 30 and qualifiers == before,
    "a refresh must keep the last completed readback and start without a full scan")
paths[catalog[state.world][1]] = nil
travel_pending = true
local before_travel = actor_lookups
tick()
assert(actor_lookups == before_travel, "a pending handoff suspends the background door scan")
travel_pending = false
for _ = 1, 40 do
    tick()
    current_rows(doors, state)
end
assert(#current_rows(doors, state) == 29, "a door removed after discovery must be skipped safely")

local new = make_state("DeadBridge_P", 2)
local fresh = current_rows(doors, new)
local stable_encodes = encodes
assert(#fresh == 2 and current_rows(doors, new)[1] == fresh[1] and encodes == stable_encodes,
    "unchanged doors in a small census must keep their encoded rows")
local moved = new.world.doors[1]
moved.Arrow.RelativeRotation.Yaw = 90
moved.Box_Knob.K2_GetComponentLocation = function() return {X = 3, Y = 0, Z = 0} end
local current = current_rows(doors, new)
assert(encodes == stable_encodes + 1, "only the door that moved should be encoded again")
for _, reading in ipairs(current) do
    assert(reading[1] == "native-stock-door-state" and reading[2] == "BasicDoor_C"
        and (reading[3] == "BasicDoor_1" or reading[3] == "BasicDoor_2"), "travel must retire the old census")
    if reading[3] == "BasicDoor_1" then
        assert(reading[8] == "90.000" and reading[16] == "3.000", "the updated hinge and knob must be published together")
    end
end
tick()
assert(#current_rows(doors, new) == 2, "an old background pass must never publish into the new world")

local authority = make_state("Observitory_P", 1)
authority.server = false
local opened = authority.world.doors[1]
opened.Open = true
guest.ActorBeingLookedAt = object()
guest.InteractionTarget_Component = object()
guest.CanInteract = false
function opened:SendInteractionToActor(pawn, knob)
    assert(pawn == guest and knob == self.Box_Knob)
    self.Open = not self.Open
end

local close = {"request", "native-stock-door-guest-interact", opened.name, "guest pawn"}
assert(doors.command(close, authority, "hosting") == "stock hinged door interaction dispatched open" and not opened.Open,
    "host focus may disagree with the knob captured on the guest's stock press")
assert(doors.command(close, authority, "hosting") == "stock hinged door interaction dispatched closed" and opened.Open,
    "the exact qualified door can reopen")
opened.Locked = true
local ok, reason = pcall(doors.command, close, authority, "hosting")
assert(not ok and reason == "stock_door_locked" and opened.Open, "the host's live lock still prevents the toggle")
