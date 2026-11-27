-- Run from the repository root with the pinned Lua CLI.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local pools = require("native_acid_pools")
local area = "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P"
local epoch = string.rep("9", 32)
local worlds, actors = {}, {}
EObjectFlags = {RF_WasLoaded = 1}

local function object(properties)
    properties.IsValid = function() return true end
    properties.GetAddress = function(self) return self.address end
    return properties
end

local function pool(world, id, address, authored)
    local actor = object({address = address, bHidden = false, FizzledOut = false, collision = true,
        world = world, authored = authored, calls = 0, SizeScaler = 1, x = 10})
    actor.path = world.map .. ".Lab_EastWing_P:PersistentLevel." .. id
    actor.GetFullName = function(self) return "AcidPool_BP_C " .. self.path end
    actor.GetWorld = function(self) return self.world end
    actor.HasAuthority = function(self) return not self.world.guest end
    actor.HasAnyFlags = function(self, flag) assert(flag == EObjectFlags.RF_WasLoaded); return self.authored end
    actor.GetFName = function() return {ToString = function() return id end} end
    actor.K2_GetActorLocation = function(self) return {X = self.x, Y = 20, Z = 30} end
    actor.K2_GetActorRotation = function() return {Pitch = 0, Yaw = 0, Roll = 0} end
    actor.GetActorScale3D = function() return {X = 1, Y = 1, Z = 1} end
    actor.IsActorBeingDestroyed = function(self) return self.ended == true end
    actor.SetActorHiddenInGame = function(self, value) self.bHidden = value end
    actor.GetActorEnableCollision = function(self) return self.collision end
    actor.SetActorEnableCollision = function(self, value) self.collision = value end
    actor.FizzleOut_Mat = object({playing = false, IsPlaying = function(self) return self.playing end})
    actor.Sizzle_SFX = object({
        playing = true,
        IsPlaying = function(self) return self.playing end,
        GetOwner = function() return actor end,
        Stop = function(self) self.playing = false end,
        Play = function(self) self.playing = true end,
    })
    actors[#actors + 1] = actor
    return actor
end

FindAllOf = function(name) assert(name == "AcidPool_BP_C"); return actors end
SilverfishNativeAcidPool = function(path, world, target)
    for _, actor in ipairs(actors) do
        if actor.path == path and actor.world.address == world then
            if target then
                assert(actor.world.guest)
                actor.calls = actor.calls + 1
                if target == "f" then actor.FizzleOut_Mat.playing = true end
                if target == "e" then actor.FizzledOut = true end
                return true
            end
            return true, actor.world.guest
        end
    end
    return false, false
end

local first, second = "AcidPool_BP_C_1", "AcidPool_BP_C_9"
worlds.host = object({map = area, address = 1, guest = false})
worlds.guest = object({map = area, address = 2, guest = true})
local host = {world = worlds.host, map = area}
local guest = {world = worlds.guest, map = area}
local host_first = pool(host.world, first, 3, true)
pool(host.world, second, 4, true)
local guest_first = pool(guest.world, first, 5, true)
local guest_second = pool(guest.world, second, 6, true)
local dynamic = pool(guest.world, "Dynamic_Test", 7, false)
local function row(...) return {...} end

assert(pools.rows(host, "hosting", row)[3] == first .. ":a|" .. second .. ":a")
local unchanged = pools.rows(host, "hosting", row)
assert(pools.rows(host, "hosting", row) == unchanged)
local native_phases = "f?"
SilverfishReadNativePlacedAcidPhases = function(world, addresses)
    assert(world == host.world.address and #addresses == 2 and addresses[1] == 3 and addresses[2] == 4)
    return native_phases
end
assert(pools.rows(host, "hosting", row)[3] == first .. ":f|" .. second .. ":a")
native_phases = "-?"
assert(pools.rows(host, "hosting", row)[3] == second .. ":a")
native_phases = nil
assert(pools.rows(host, "hosting", row)[3] == first .. ":a|" .. second .. ":a")
SilverfishReadNativePlacedAcidPhases = nil
host_first.FizzleOut_Mat.playing = true
assert(pools.rows(host, "hosting", row)[3] == first .. ":f|" .. second .. ":a")
host_first.FizzledOut = true
assert(pools.rows(host, "hosting", row)[3] == first .. ":e|" .. second .. ":a")
pools.ended(host_first.address, true)
assert(pools.rows(host, "hosting", row)[3] == second .. ":a")
local dynamic_host = pool(host.world, "Dynamic_Test", 8, false)
pools.created(dynamic_host, "host")
assert(pools.rows(host, "hosting", row)[3]:find("spawned_Dynamic_Test:a:10.000", 1, true))
dynamic_host.x = 20
local dynamic_row = pools.rows(host, "hosting", row)[3]
assert(dynamic_row:find("spawned_Dynamic_Test:a:20.000", 1, true))
local native_phase = "a"
SilverfishReadNativeDynamicAcidSample = function(address, world)
    assert(address == dynamic_host.address and world == host.world.address)
    return native_phase, "20.000,20.000,30.000:0.000,0.000,0.000:1.000,1.000,1.000:1.000"
end
assert(pools.rows(host, "hosting", row)[3] == dynamic_row)
native_phase = "f"
assert(pools.rows(host, "hosting", row)[3]:find("spawned_Dynamic_Test:f:20.000", 1, true))
native_phase = false
assert(pools.rows(host, "hosting", row)[3] == second .. ":a")
native_phase = nil
assert(pools.rows(host, "hosting", row)[3] == dynamic_row)
SilverfishReadNativeDynamicAcidSample = nil
pools.ended(dynamic_host.address, true)
assert(pools.rows(host, "hosting", row)[3] == second .. ":a")

assert(pools.rows(guest, "connected", row)[3] == "-")
local function apply(...)
    return pools.command({"", "native-acid-pools-apply", area, epoch, ...}, guest, "connected")
end
local malformed = pcall(apply, second .. ":a", first .. ":f")
assert(not malformed and guest_first.calls == 0)
local stale = pcall(pools.command, {"", "native-acid-pools-apply", "/OldMap", epoch, "-"}, guest, "connected")
assert(not stale and not guest_first.bHidden)
apply(first .. ":f", second .. ":a")
apply(first .. ":f", second .. ":a")
assert(guest_first.calls == 1 and not guest_first.FizzledOut and guest_second.calls == 0)
apply(first .. ":e", second .. ":a")
assert(guest_first.calls == 2 and guest_first.FizzledOut)
apply(second .. ":a")
assert(guest_first.bHidden and not guest_first.collision and not guest_first.Sizzle_SFX.playing)
assert(not guest_second.bHidden and dynamic.bHidden and not dynamic.collision and pools.rows(guest, "connected", row)[3] == epoch)
pools.clear()
assert(not guest_first.bHidden and guest_first.collision)
