package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local enrolled = 0
package.loaded.native_prop_carry = {enroll = function()
    enrolled = enrolled + 1
    return true
end}
package.loaded.guest_save = {}

local getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" then return "1" end
    return getenv(name)
end

local function object(fields)
    fields = fields or {}
    function fields:GetAddress() return self end
    function fields:IsValid() return true end
    return fields
end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local path = map .. ".Observitory_P:PersistentLevel.Prop_Lantern_01_C_1"
local name = "Prop_Lantern_01_C " .. path
local actor_type, mesh_type, sphere_type = object(), object(), object()
local classes = {
    ["/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"] = actor_type,
    ["/Script/Engine.StaticMeshComponent"] = mesh_type,
    ["/Script/Engine.SphereComponent"] = sphere_type,
}
local active
function StaticFindObject(argument) return argument == path and active or classes[argument] end

local function make_scene(server)
    local world, driver, actor = object({PersistentLevel = object()}), object(), object()
    world.NetDriver, driver.World = driver, world
    if server then driver.ServerConnection = object({Driver = driver}) end
    function world:GetFullName() return "World " .. map .. ".Observitory_P" end
    function actor:GetFullName() return name end
    function actor:GetWorld() return world end
    function actor:GetOuter() return world.PersistentLevel end
    function actor:HasAuthority() return not server end
    function actor:GetClass() return actor_type end
    function actor:IsActorBeingDestroyed() return false end
    function actor:K2_GetActorLocation() return {X = self.x, Y = 0, Z = 0} end
    actor.x, actor.updates = 5, 0
    actor.bReplicates, actor.bReplicateMovement, actor.bNetLoadOnClient = false, false, true
    actor.Lit, actor.BeingHeld = false, false
    function actor:SetReplicates(value)
        assert(not server)
        self.bReplicates = value
    end
    function actor:SetReplicateMovement(value)
        assert(not server)
        self.bReplicateMovement = value
    end
    function actor:ForceNetUpdate() self.updates = self.updates + 1 end
    local mesh, sphere = object({response = 0}), object({overlaps = true})
    function mesh:GetClass() return mesh_type end
    function sphere:GetClass() return sphere_type end
    function mesh:GetOwner() return actor end
    function sphere:GetOwner() return actor end
    function sphere:GetGenerateOverlapEvents() return self.overlaps end
    function sphere:SetGenerateOverlapEvents(value) self.overlaps = value end
    function mesh:GetCollisionResponseToChannel(channel)
        assert(channel == 16)
        return self.response
    end
    function mesh:SetCollisionResponseToChannel(channel, response)
        assert(channel == 16)
        self.response = response
    end
    function actor:SetLitUnlit(lit, sound)
        assert(server and sphere.overlaps == false and sound == false)
        self.Lit = lit
    end
    actor.PropMesh, actor.RootComponent, actor.Sphere = mesh, mesh, sphere
    active = actor
    return {world = world, server = server, listening = true, map = map}, actor
end

local lantern = require("native_lantern")
local function row(...) return {...} end
local host_state, host_actor = make_scene(false)
lantern.sync(host_state, "hosting")
assert(host_actor.bReplicates and host_actor.bReplicateMovement and host_actor.updates == 1)
lantern.sync(host_state, "hosting")
assert(host_actor.updates == 1, "steady stock physics must use Unreal's normal movement schedule")
assert(enrolled == 1)
host_actor.Lit, host_actor.BeingHeld, host_actor.x = true, true, 99
lantern.sync(host_state, "hosting")
local observed = lantern.status(host_state, "hosting", row)
assert(observed[4] == 1 and observed[5] == 1 and observed[6] == 1 and observed[7] == "99.00")
assert(host_actor.PropMesh.response == 2, "the lit co-op lantern must expose stock Interact")
assert(host_actor.updates == 2, "a host grab should wake movement replication")
host_actor.BeingHeld = false
lantern.sync(host_state, "hosting")
assert(host_actor.updates == 3, "a host release should wake movement replication")

local guest_state, guest_actor = make_scene(true)
local on = {"request", "native-observatory-lantern-light", map, name, "1"}
assert(not pcall(lantern.command, on, guest_state, "connected"), "the guest fire sensor must be guarded first")
lantern.sync(guest_state, "connected")
assert(guest_actor.Sphere.overlaps == false and guest_actor.Lit == false)
assert(host_actor.PropMesh.response == 0, "leaving the lantern world must restore the response")
assert(lantern.command(on, guest_state, "connected") and guest_actor.Lit and not guest_actor.Sphere.overlaps)
lantern.sync(guest_state, "connected")
assert(guest_actor.PropMesh.response == 2 and not guest_actor.Sphere.overlaps)
local foreign = {"request", "native-observatory-lantern-light", map, name .. "_other", "0"}
assert(not pcall(lantern.command, foreign, guest_state, "connected") and guest_actor.Lit)
local off = {"request", "native-observatory-lantern-light", map, name, "0"}
assert(lantern.command(off, guest_state, "connected") and not guest_actor.Lit)
lantern.sync(guest_state, "connected")
assert(guest_actor.PropMesh.response == 0, "extinguished lamps keep pickup targeting")
assert(lantern.command(on, guest_state, "connected"))
lantern.sync(guest_state, "connected")
assert(guest_actor.PropMesh.response == 2)
guest_state.listening = false
lantern.sync(guest_state, "standalone")
assert(guest_actor.PropMesh.response == 0, "singleplayer must keep the authored collision response")
assert(guest_actor.Sphere.overlaps, "singleplayer must retain its stock fire overlaps")
assert(not guest_actor.bReplicates and not guest_actor.bReplicateMovement, "guests must never start prop replication")
