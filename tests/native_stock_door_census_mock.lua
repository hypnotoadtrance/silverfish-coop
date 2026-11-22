package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local tick, frames, work = nil, 100, 0
local actor_lookups, qualifiers = 0, 0
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
function statics:GetAllActorsOfClass(world, class, result)
    assert(class == door_class)
    result.OutActors = world.doors
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
    for index = 1, count do
        local path = map .. "." .. name .. ":PersistentLevel.BasicDoor_" .. index
        local door = object({Locked = false, LockBroken = false, Open = false, name = "BasicDoor_C " .. path})
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
        world.doors[index], paths[path] = door, door
    end
    return {map = map, world = world, server = true, listening = true}
end

local function row(...) return {...} end
local function current_rows(doors, state, command)
    return doors.rows(state, "connected", row, command)
end

local doors = require("native_stock_door")
doors.start()
local state = make_state("CrossRoads_P", 30)
assert(#current_rows(doors, state) == 0 and qualifiers == 0, "dense census must not qualify every actor in status")
tick()
assert(qualifiers > 0 and qualifiers < 10, "qualification should stop when the game-thread time budget expires")

local target = state.world.doors[30]
local command = {"request", "native-stock-door-project", state.map, target.name .. "|0|0|1|0|90|0|0|5|0"}
assert(doors.command(command, state, "connected") and target.Open and actor_lookups == 1)
local exact = current_rows(doors, state, command)
assert(#exact == 1 and exact[1][3] == target.name and exact[1][6] == 1, "the exact action must read back before ack")

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
tick()
local new = make_state("DeadBridge_P", 2)
local fresh = current_rows(doors, new)
assert(#fresh == 2 and fresh[1][2] == new.map and fresh[2][2] == new.map, "travel must retire the old census")
tick()
assert(#current_rows(doors, new) == 2, "an old background pass must never publish into the new world")
