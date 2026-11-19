local flags = {SILVERFISH_COOP_HEADLESS = "1", SILVERFISH_COOP_NATIVE_FIXTURES = "1"}
os.getenv = function(name) return flags[name] end

local function object(address)
    return {IsValid = function() return true end, GetAddress = function() return address end,
        GetFullName = function() return "mock object " .. address end}
end

local docks = "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P"
local path = docks .. ".Docks_01_P:PersistentLevel.LootCrate_Small_BP_C_10"
local profile = {index = 1, map = docks, path = path, name = "LootCrate_Small_BP_C " .. path, guarantee_spawn = true}
package.loaded.authored_loot_registry = {profile}
package.loaded.authored_supply_catalog = {}

local world, pawn, crate, box, sibling, trace = object(1), object(2), object(3), object(4), object(5), object(6)
local camera, capsule, camera_class, capsule_class, box_class = object(7), object(8), object(9), object(10), object(11)
local center = {X = 0, Y = 0, Z = 77}
local pos = {X = 999, Y = 1000, Z = 120}
local rotation = {Pitch = 0, Yaw = 90, Roll = 0}
local placements, net_updates, lower_hits, visibility_hits = 0, 0, 0, 0
local resolved_box = box

pawn.GetWorld, crate.GetWorld = function() return world end, function() return world end
pawn.HasAuthority, crate.HasAuthority = function() return true end, function() return true end
pawn.Dead, pawn.Camera, pawn.CapsuleComponent, pawn.InteractionRange = false, camera, capsule, 250
pawn.K2_GetActorLocation, pawn.K2_GetActorRotation = function() return pos end, function() return rotation end
pawn.K2_TeleportTo = function(_, at, facing)
    assert(facing.Yaw == rotation.Yaw)
    placements, pos = placements + 1, at
    return true
end
pawn.ForceNetUpdate = function() net_updates = net_updates + 1 end
camera.GetOwner, capsule.GetOwner, box.GetOwner = function() return pawn end, function() return pawn end, function() return crate end
camera.GetClass, capsule.GetClass, box.GetClass = function() return camera_class end, function() return capsule_class end, function() return box_class end
camera.K2_GetComponentLocation = function() return {X = pos.X, Y = pos.Y, Z = pos.Z + 60} end
capsule.GetScaledCapsuleHalfHeight = function() return 96 end
crate.Box, crate.GetFullName = box, function() return profile.name end
box.K2_GetComponentLocation = function() return center end
box.GetScaledBoxExtent = function() return {X = 24, Y = 20, Z = 30} end
box.GetForwardVector = function() return {X = 1, Y = 0, Z = 0} end
box.GetRightVector = function() return {X = 0, Y = 1, Z = 0} end
box.GetUpVector = function() return {X = 0, Y = 0, Z = 1} end

trace.LineTraceSingle = function(_, opener, from, to, channel, complex, ignored, _, hit)
    assert(opener == pawn)
    if channel == 0 then
        assert(ignored[1] == crate and ignored[2] == pawn)
        if from.Z > 208 then
            hit.ImpactPoint = {X = from.X, Y = from.Y, Z = 208}
        elseif from.Z > 25 then
            lower_hits = lower_hits + 1
            hit.ImpactPoint = {X = from.X, Y = from.Y, Z = 25}
        else
            return false
        end
        hit.ImpactNormal = {X = 0, Y = 0, Z = 1}
        return true
    end
    assert(ignored[1] == pawn and from.Z < 200, "The higher floor puts the camera outside stock range")
    if channel == 1 then
        assert(complex == true)
        visibility_hits = visibility_hits + 1
        return false
    end
    assert(channel == 4 and complex == false)
    hit.Component = {Get = function() return math.abs(from.Y) < 10 and sibling or resolved_box end}
    return true
end

local objects = {[path] = crate, ["/Script/Engine.Default__KismetSystemLibrary"] = trace,
    ["/Script/Engine.CameraComponent"] = camera_class, ["/Script/Engine.CapsuleComponent"] = capsule_class,
    ["/Script/Engine.BoxComponent"] = box_class}
StaticFindObject = function(name) return objects[name] end
SilverfishProbeNativeInteractInput = function() error("Authority geometry must never invoke stock Interact") end

local input = assert(loadfile("mod/SilverfishCoop/Scripts/native_authored_pickup_input_fixture.lua"))()
local state = {map = docks, world = world, server = false, listening = true, pawns = {{pawn = pawn}}}
input.approach_docks_crate(state, "hosting", profile, pawn, crate, box)
assert(placements == 1 and net_updates == 1 and pos.Z == 123 and math.abs(pos.Y) > 10)
assert(lower_hits >= 2 and visibility_hits >= 3)

resolved_box = sibling
local accepted, failure = pcall(input.approach_docks_crate, state, "hosting", profile, pawn, crate, box)
assert(not accepted and failure:find("native_loot_docks_fixture_no_clear_sight", 1, true) == 1)
assert(placements == 1 and net_updates == 1, "A sibling Box must never qualify as exact sight")

accepted, failure = pcall(input.approach_docks_crate, state, "hosting", profile, pawn, crate, sibling)
assert(not accepted and failure == "native_loot_docks_fixture_scope_unavailable")
print("Docks crate fixture proves a reachable stock Box from the lower floor")
