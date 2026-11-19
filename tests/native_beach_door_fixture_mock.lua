package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_HEADLESS" then return "1" end
    return getenv(name)
end
local zone = require("native_zone")
os.getenv = getenv

local function upvalue(callback, wanted)
    for index = 1, 100 do
        local name, value = debug.getupvalue(callback, index)
        if name == wanted then return value end
    end
    error(wanted .. " was not found")
end

local place = upvalue(upvalue(zone.command, "door_command"), "place_at_named_door")
local function v(x, y, z) return { X = x, Y = y, Z = z } end
local function offset(point, x, y, z) return v(point.X + x, point.Y + y, point.Z + z) end
local function object(name, fields)
    fields = fields or {}
    fields.GetFullName = function() return name end
    fields.GetAddress = function(self) return self end
    fields.IsValid = function() return true end
    return fields
end
local trench = arg[1] == "trench"
local origin = trench and v(24600, -67050, 3200) or v(96502.4381613, -16595.7727588, 1027.7103561)
local map_name = trench and "Trench_01_P" or "Beach_01_P"
local map = "/Game/Classes/Maps/Maps_OverWorld/" .. map_name
local face, height = trench and -1 or 1, trench and .125 or .414
local door_name = trench and "Dungeon_TransitionDoor_BP_C_2" or "Dungeon_TransitionDoor_BP_C_1"
local floor_name = trench and "StaticMeshActor_185" or "StaticMeshActor_881"
local mesh_path = "/Game/Assets/StaticMeshes/Mesh_BuildingSets/"
    .. (trench and "Set_Prim/SM_Buildings_Floor_5x5_01.SM_Buildings_Floor_5x5_01" or "Set_Bunker/SM_Bld_Bunker_03.SM_Bld_Bunker_03")
local level = map .. "." .. map_name .. ":PersistentLevel."
local world, mesh = object("Beach"), object("SM_Bld_Bunker_03")
local types = { actor = object("StaticMeshActor"), floor = object("StaticMeshComponent") }
local bunker = object("StaticMeshActor " .. level .. floor_name, {
    GetWorld = function() return world end, HasAuthority = function() return true end,
    IsActorBeingDestroyed = function() return false end, GetActorEnableCollision = function() return true end,
    GetClass = function() return types.actor end,
})
local floor = object("StaticMeshComponent " .. level .. floor_name .. ".StaticMeshComponent0", {
    StaticMesh = mesh, GetOwner = function() return bunker end, GetClass = function() return types.floor end,
    GetCollisionEnabled = function() return 3 end, GetCollisionObjectType = function() return 0 end,
    GetCollisionResponseToChannel = function(_, channel) assert(channel == 2); return 2 end,
    K2_GetComponentLocation = function()
        if trench then return offset(origin, -750, -600, 0) end
        return offset(origin, 242.25, 681.25, -11.90)
    end,
})
bunker.StaticMeshComponent, bunker.RootComponent = floor, floor

local door = object("Dungeon_TransitionDoor_BP_C " .. level .. door_name, {
    K2_GetActorLocation = function() return origin end,
})
door.Box = object("Stock door Box", {
    GetOwner = function() return door end, K2_GetComponentLocation = function() return offset(origin, 0, 30 * face, 129) end,
})
door.LevelExit_Spawn_BP = object("Stock spawn component")
door.LevelExit_Spawn_BP.ChildActor = object("Stock Beach_Leto arrival", {
    Arrow = object("Stock arrow", { K2_GetComponentLocation = function() return offset(origin, 0, 150 * face, 150) end }),
    GetWorld = function() return world end, GetParentComponent = function() return door.LevelExit_Spawn_BP end,
})

local function player()
    local pawn = object("Player_FP_C", { InteractionRange = 350, current = v(51100, -11900, 2), moved = 0 })
    pawn.Camera = object("Camera", {
        GetOwner = function() return pawn end,
        K2_GetComponentLocation = function() return offset(pawn.current, 0, 0, 45) end,
    })
    pawn.CapsuleComponent = object("Capsule", { GetScaledCapsuleHalfHeight = function() return 88 end })
    pawn.K2_GetActorLocation = function(self) return self.current end
    pawn.K2_TeleportTo = function(self, point)
        self.current, self.moved = point, self.moved + 1
        return true
    end
    pawn.ForceNetUpdate = function() end
    return pawn
end
local host, guest = player(), player()
local outside_floor = object("Foreign mesh")
local blocks, wrong_component, floor_probes = false, false, 0
local traces = object("KismetSystemLibrary")
local function fill(out, component, point)
    out.OutHit = { Component = { Get = function() return component end }, ImpactPoint = point, ImpactNormal = v(0, 0, 1) }
    return true
end
function traces:LineTraceSingle(_, from, to, channel, complex, ignore, _, hit)
    assert(#ignore == 2 and ignore[1] == host and ignore[2] == guest, "door must be visible to its own interaction trace")
    if channel == 0 then
        floor_probes = floor_probes + 1
        assert(not complex and from.Z == origin.Z + 210 and to.Z == origin.Z - 35, "floor ray must start below the roof")
        return fill(hit, wrong_component and outside_floor or floor, v(from.X, from.Y, origin.Z + height))
    end
    if channel == 1 then
        assert(complex)
        return blocks and fill(hit, outside_floor, offset(from, 0, -10, 0))
    end
    assert(channel == 4 and not complex)
    if not blocks then return fill(hit, door.Box, door.Box:K2_GetComponentLocation()) end
    assert((from.X - to.X)^2 + (from.Y - to.Y)^2 + (from.Z - to.Z)^2 < 11^2, "stock visibility must cap reach")
    return false
end
function StaticFindObject(path)
    local fixtures = {
        [level .. floor_name] = bunker,
        ["/Script/Engine.StaticMeshActor"] = types.actor,
        ["/Script/Engine.StaticMeshComponent"] = types.floor,
        ["/Script/Engine.Default__KismetSystemLibrary"] = traces,
        [mesh_path] = mesh,
    }
    return fixtures[path]
end

local state = { map = map, world = world, local_pawn = { pawn = host }, pawns = {{ pawn = host }, { pawn = guest }} }
local forward, rotation, tag = v(0, face, 0), {}, trench and "Annex_Trench" or "Beach_Leto"
assert(place(state, host, door, forward, rotation, 180, tag))
assert(place(state, guest, door, forward, rotation, 180, tag))
assert(host.current.X == origin.X + 70 and guest.current.X == origin.X - 70 and floor_probes == 2)
assert(host.current.Y == origin.Y + 180 * face and host.current.Z == origin.Z + height + 90)

blocks = true
local previous, moves = guest.current, guest.moved
local ok, failure = pcall(place, state, guest, door, forward, rotation, 180, tag)
assert(not ok and failure:find("5 stock floor hits, 5 safe positions", 1, true), tostring(failure))
assert(guest.current == previous and guest.moved == moves + 10, "each unqualified position must restore the pawn")

wrong_component, moves = true, guest.moved
ok, failure = pcall(place, state, guest, door, forward, rotation, 180, tag)
assert(not ok and failure:find("0 stock floor hits, 0 safe positions", 1, true), tostring(failure))
assert(guest.current == previous and guest.moved == moves, "foreign geometry must not move the pawn")
print(tag .. " cooked floor, exact stock sight ray, and reversible placement verified")
