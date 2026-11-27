-- Run with Lua 5.4 from the repository root.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local fire = require("native_drum_fire")

local function object(fields)
    fields = fields or {}
    function fields:IsValid() return true end
    function fields:GetAddress() return self end
    return fields
end

local function row(...) return {...} end
local area, epoch = "/Game/Classes/Maps/CrossRoads", string.rep("a", 32)
local asset_path = "/Game/Assets/PFX/NI_FireMolotov.NI_FireMolotov"
local function vec(x, y, z) return {X = x, Y = y, Z = z} end
local asset_names, asset_lookups, world_transforms, rotations = 0, 0, 0, 0
local asset = object({GetFullName = function()
    asset_names = asset_names + 1
    return "NiagaraSystem " .. asset_path
end})
local clock, pending = 1, false
local engine_time_reads = 0
os.clock = function() return clock end
local spawned = {}
local host_world, guest_world, host_field = object(), object(), object()
local function world_field() return host_world end
local point = vec(-100, 200, 0)
host_field.GetWorld = world_field
host_field.HasAuthority = function() return true end
host_field.GetClass = function()
    return object({GetFullName = function()
        return "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Molotov.Fire_Molotov_C"
    end})
end
host_field.K2_GetActorLocation = function() return point end
host_field.IsActorBeingDestroyed = function(self) return self.retired end
local particles = {}
local function particle(id)
    local location = vec(point.X + id, point.Y, id)
    return object({Asset = asset, location = location, owner = host_field,
        GetOwner = function(self) return self.owner end,
        K2_GetComponentToWorld = function()
            world_transforms = world_transforms + 1
            return {Translation = location, Scale3D = vec(1, 1, .5)}
        end,
        K2_GetComponentRotation = function()
            rotations = rotations + 1
            return {Pitch = 10, Yaw = -90, Roll = 0}
        end})
end
for id = 1, 5 do particles[id] = particle(id) end
particles.GetArrayNum = function(self) return #self end
host_field.Particles = particles
local statics = object({GetTimeSeconds = function()
    engine_time_reads = engine_time_reads + 1
    return clock
end})
local niagara = object()
function niagara:SpawnSystemAtLocation(_, provided, location, rotation, scale, destroy, activate)
    assert(provided == asset and destroy and not activate)
    assert(rotation.Yaw == -90 and scale.Z == .5 and location.Y == 200)
    local component = object({location = location})
    function component:SetVariableVec2(key, center)
        assert(key == "User.Center" and center.X == -100 and center.Y == 200)
        self.configured = true
    end
    function component:Activate()
        assert(self.configured)
        self.active = true
    end
    function component:K2_SetWorldLocationAndRotation(updated_location, updated_rotation)
        assert(updated_rotation.Yaw == -90)
        self.location = updated_location
    end
    function component:K2_SetWorldScale3D(updated_scale) assert(updated_scale.Z == .5) end
    function component:Deactivate() self.active = false end
    function component:K2_DestroyComponent() self.active = false end
    function component:GetOwner() return nil end
    spawned[#spawned + 1] = component
    return component
end

local objects = {[asset_path] = asset, ["fire-verified-by-native"] = host_field,
    ["/Script/Engine.Default__GameplayStatics"] = statics,
    ["/Script/Niagara.Default__NiagaraFunctionLibrary"] = niagara}
function StaticFindObject(path)
    if path == asset_path then asset_lookups = asset_lookups + 1 end
    return objects[path]
end
function FName(name) return name end
function SilverfishTakeNativeDrumFire(world)
    if pending and world == host_world then
        pending = false
        return "fire-verified-by-native"
    end
end

local hosting = {map = area, world = host_world}
assert(#fire.rows(hosting, "hosting", row) == 0 and engine_time_reads == 0)
pending = true
clock = 2
local active = fire.rows(hosting, "hosting", row)[1]
assert(active[2] == 1 and active[3]:find("1,-99.000,200.000,1.000,10.000,-90.000,0.000,1.000,1.000,0.500", 1, true))
clock = 2.05
assert(fire.rows(hosting, "hosting", row)[1] == active and world_transforms == 5 and rotations == 5)
clock = 2.2
assert(fire.rows(hosting, "hosting", row)[1] == active and engine_time_reads == 0)
assert(world_transforms == 10 and rotations == 10 and asset_lookups == 2 and asset_names == 0)
particles[1].location.X = -98.25
clock = 2.4
local moved = fire.rows(hosting, "hosting", row)[1]
assert(moved[2] == 2 and moved[3]:find("1,-98.250,200.000,1.000", 1, true))
table.remove(particles, 1)
clock = 3
local fizzling = fire.rows(hosting, "hosting", row)[1]
assert(fizzling[2] == 3 and fizzling[3]:find("2,-98.000", 1, true) and not fizzling[3]:find("1,-98.250", 1, true))
host_field.retired = true
clock = 4
local retired = fire.rows(hosting, "hosting", row)[1]
assert(retired[2] == 4 and #retired == 2)
assert(fire.rows(hosting, "hosting", row)[1] == retired and engine_time_reads == 0)

local local_pawn = object({GetWorld = function() return guest_world end, IsLocallyControlled = function() return true end})
local guest = {map = area, world = guest_world, server = true, listening = true, local_pawn = {pawn = local_pawn}}
local function project(revision, fields)
    local args = {"ticket", "native-drum-fire-view", area, epoch, tostring(revision)}
    if fields then
        -- Two fragments from a single bridge ticket are reconstructed before any scene changes.
        args[6], args[7] = fields:sub(1, 25), fields:sub(26)
    end
    return fire.command(args, guest, "connected")
end
project(1, active[3])
local first = fire.rows(guest, "connected", row)[1]
assert(first[4] == 5 and first[5] == 4 and #spawned == 4)
assert(fire.rows(guest, "connected", row)[1][5] == 5 and #spawned == 5)
assert(not pcall(project, 2, active[3] .. ";1,1,2,3,0,0,0,1,1,1"))
local changed
for _, component in ipairs(spawned) do if component.location.Z == 1 then changed = component end end
assert(changed.location.X == -99)
project(2, moved[3])
assert(fire.rows(guest, "connected", row)[1][5] == 5 and #spawned == 5 and changed.location.X == -98.25)
project(3, fizzling[3])
local fewer = fire.rows(guest, "connected", row)[1]
assert(fewer[4] == 4 and fewer[5] == 4 and #spawned == 5)
for _, component in ipairs(spawned) do assert(component.active == (component.location.Z ~= 1)) end
project(1, active[3])
assert(fire.rows(guest, "connected", row)[1][5] == 4 and #spawned == 5)
local reads_before_removal = engine_time_reads
project(4)
assert(fire.rows(guest, "connected", row)[1][5] == 0 and engine_time_reads == reads_before_removal)
for _, component in ipairs(spawned) do assert(not component.active) end

-- Requalify live particles, even when the previously published pose is unchanged.
host_field.retired, pending, clock = false, true, 5
local original = fire.rows(hosting, "hosting", row)[1]
assert(original[3]:find("1,-98.000", 1, true))
particles[1].Asset = object({GetFullName = function() return "NiagaraSystem /Game/Other.Asset" end})
particles[2].owner = object()
particles[3] = particle(8)
clock = 5.2
local replaced = fire.rows(hosting, "hosting", row)[1]
assert(replaced[2] == 2 and replaced[3]:find("5,-92.000", 1, true))
assert(not replaced[3]:find("1,-98.000", 1, true) and not replaced[3]:find("2,-97.000", 1, true)
    and not replaced[3]:find("3,-96.000", 1, true) and asset_names == 0)
particles[1].Asset, particles[2].owner = asset, host_field
objects[asset_path] = nil
clock = 5.4
local restored = fire.rows(hosting, "hosting", row)[1]
assert(restored[2] == 3 and restored[3]:find("1,-98.000", 1, true)
    and restored[3]:find("2,-97.000", 1, true) and asset_names == 4)
