package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local now, seat = 1, "client"
local original_getenv = os.getenv
os.clock = function() return now end
os.getenv = function(name)
    if name == "SILVERFISH_COOP_NATIVE_ZONE" then return "1" end
    return original_getenv(name)
end

local function object(fields)
    fields = fields or {}
    function fields:IsValid() return self.valid ~= false end
    function fields:GetAddress() return self end
    return fields
end

local folder = "/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/"
local fixture_class = folder .. "Flare_Projectile_BP.Flare_Projectile_BP_C"
local capsule_class, primitive_class, grab_fn = object(), object(), object()
local traced, grab_hook, pre, post, authoritative_pawn
local origin, center = {X = 0, Y = 0, Z = 0}, {X = 100, Y = 0, Z = 0}
local trace = object()
function trace:LineTraceSingle(_, _, _, channel, _, _, _, hit)
    if channel ~= 1 or not traced then return false end
    hit.Component = {Get = function() return traced end}
    hit.ImpactPoint = center
    return true
end
local known = {
    ["/Script/Engine.CapsuleComponent"] = capsule_class,
    ["/Script/Engine.PrimitiveComponent"] = primitive_class,
    ["/Script/Engine.Default__KismetSystemLibrary"] = trace,
    ["/Game/Classes/Player/Player_FP.Player_FP_C:Grab_Release_Object"] = grab_fn,
}
function StaticFindObject(path) return known[path] end
function RegisterBeginPlayPreHook(callback) pre = callback end
function RegisterBeginPlayPostHook(callback) post = callback end
function RegisterHook(_, callback) grab_hook = callback end
EObjectFlags = {RF_WasLoaded = 1}

package.loaded.guest_save = {exact_item_context = function() return authoritative_pawn end}
package.loaded.native_corpse_view = {binding = function() end}
package.loaded.native_observer_target = {}
package.loaded.native_guest_drop_projection = {start_carry = function() end}
package.loaded.players = {is_standing_guest = function(pawn) return pawn == authoritative_pawn end}

local function projectile(path, server, world)
    if not world then
        world = object()
        world.NetDriver = object({World = world, ServerConnection = not server and object() or nil})
    end
    local class = object()
    function class:GetFullName() return "BlueprintGeneratedClass " .. path end
    local actor = object({Lit = true, bReplicateMovement = true, ReplicatedMovement = {bRepPhysics = false}})
    function actor:GetClass() return class end
    function actor:GetWorld() return world end
    function actor:GetLocalRole() return server and 3 or 1 end
    function actor:HasAuthority() return server end
    function actor:IsActorBeingDestroyed() return false end
    function actor:K2_GetActorLocation() return center end
    function actor:GetFullName() return path end
    function actor:HasAnyFlags() return false end
    function actor:SetReplicates(value) self.bReplicates = value end
    function actor:SetReplicateMovement(value) self.bReplicateMovement = value end
    function actor:ForceNetUpdate() end
    function actor:SetActorEnableCollision(value) self.collision = value end
    local function primitive()
        local part = object({response = {}})
        function part:GetOwner() return actor end
        function part:SetGenerateOverlapEvents(value) self.overlaps = value end
        function part:SetNotifyRigidBodyCollision(value) self.notify_hit = value end
        function part:SetCollisionEnabled(value) self.mode = value end
        function part:GetCollisionEnabled() return self.mode end
        function part:SetEnableGravity(value) self.gravity = value end
        function part:IsAnySimulatingPhysics() return self.simulating == true end
        function part:SetSimulatePhysics(value) self.simulating = value end
        function part:SetCollisionResponseToChannel(channel, value) self.response[channel] = value end
        function part:GetCollisionResponseToChannel(channel) return self.response[channel] end
        function part:SetCollisionResponseToAllChannels(value)
            for channel = 0, 31 do self.response[channel] = value end
        end
        return part
    end
    local function movement()
        return object({active = true, Deactivate = function(self) self.active = false end})
    end
    actor.ProjectileMovement, actor.RotatingMovement = movement(), movement()
    actor.Capsule, actor.DisplayMesh, actor.Sphere = primitive(), primitive(), primitive()
    actor.RootComponent = actor.Capsule
    function actor.Capsule:GetClass() return capsule_class end
    function actor:K2_GetComponentsByClass(requested_class)
        assert(requested_class == primitive_class)
        return {self.Capsule, self.DisplayMesh, self.Sphere}
    end
    local context = {get = function() return actor end}
    return actor, {world = world, listening = true, server = not server, map = "observatory"}, context
end

local function pawn(host)
    local player, grip = object({InteractionRange = 200, Dead = false}), object()
    player.Camera, player.PhysicsHandle = object({RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}}), grip
    function player:GetFullName() return "Guest pawn" end
    function player:IsLocallyControlled() return not host end
    function player:HasAuthority() return host end
    function player:K2_GetActorLocation() return origin end
    function player.Camera:GetOwner() return player end
    function player.Camera:K2_GetComponentLocation() return origin end
    function player.Camera:GetForwardVector() return {X = 1, Y = 0, Z = 0} end
    function player.Camera:K2_SetWorldRotation() end
    function player.Camera:K2_SetRelativeRotation() end
    function grip:GetOwner() return player end
    function grip:GetGrabbedComponent() return self.grabbed end
    function player:Grab_Release_Object(throwing, dropping, pickup)
        assert(pickup == false, "Flare is a physics carry, never an inventory claim")
        if throwing or dropping then
            self.HeldObject, grip.grabbed = nil, nil
        else
            local target = assert(self.ActorBeingLookedAt)
            assert(target.RootComponent:IsAnySimulatingPhysics())
            self.HeldObject, grip.grabbed = target, target.RootComponent
        end
    end
    return player
end

local flare = require("native_flare")
local carry = require("native_prop_carry")
flare.start(function() return seat end)

local replica, client, client_context = projectile(fixture_class, false)
local guest = pawn(false)
client.local_pawn = {pawn = guest}
pre(client_context)
post(client_context)
assert(not replica.collision and not replica.ProjectileMovement.active and not replica.RotatingMovement.active)
flare.sync(client, "connected")
assert(replica.collision == false and not flare.carry_root(replica, client.world), "in-flight copy must remain inert")
local bolt_path = folder .. "Bolt_Projectile_BP.Bolt_Projectile_BP_C"
local bolt, bolt_client, bolt_context = projectile(bolt_path, false, client.world)
bolt.FuseTime = 3
pre(bolt_context)
post(bolt_context)
assert(bolt.collision == false and not bolt.ProjectileMovement.active and not bolt.RotatingMovement.active)
assert(bolt.FuseTime == 3, "bolt replica must keep the stock lifetime")
bolt.ReplicatedMovement.bRepPhysics = true
now = now + 1
flare.sync(bolt_client, "connected")
assert(not flare.carry_root(bolt, bolt_client.world) and bolt.collision == false, "bolt was treated as a flare")
local grenade, _, grenade_context = projectile(folder .. "GrenadeProjectile_Master_BP.GrenadeProjectile_Master_BP_C", false)
pre(grenade_context)
post(grenade_context)
assert(grenade.collision == nil and grenade.ProjectileMovement.active, "unscoped projectile touched")

now = now + 1
replica.ReplicatedMovement.bRepPhysics = true
flare.sync(client, "connected")
assert(flare.carry_root(replica, client.world) == replica.Capsule and replica.collision)
assert(replica.Capsule.simulating and replica.Capsule.mode == 3 and replica.Capsule.gravity == false)
assert(replica.DisplayMesh.mode == 0 and replica.Sphere.mode == 0)
assert(not replica.ProjectileMovement.active and not replica.RotatingMovement.active)
for _, part in ipairs({replica.Capsule, replica.DisplayMesh, replica.Sphere}) do
    assert(part.overlaps == false and part.notify_hit == false)
    for channel = 0, 31 do
        assert(part.response[channel] == (part == replica.Capsule and channel == 4 and 2 or 0))
    end
end
assert(not flare.carry_root(replica, object()), "wrong-world copy qualified")

carry.sync(client, "connected")
guest.ActorBeingLookedAt = replica
guest:Grab_Release_Object(false, false, false)
local false_parameter = {get = function() return false end}
grab_hook({get = function() return guest end}, false_parameter, false_parameter)
local rows = carry.rows(client, "connected", function(...) return {...} end)
assert(#rows == 1 and rows[1][1] == "native-prop-carry-input" and rows[1][5] == "grab" and rows[1][6] == fixture_class)
flare.sync(client, "disconnected")
assert(replica.collision == false and not flare.carry_root(replica, client.world), "inactive copy left targetable")

now = now + 1
function replica.Capsule:GetCollisionResponseToChannel(channel)
    if channel == 0 then return 2 end
    return self.response[channel]
end
flare.sync(client, "connected")
assert(replica.collision == false and not flare.carry_root(replica, client.world), "unsafe physics mask did not fail closed")

seat = "host"
local authority, host, host_context = projectile(fixture_class, true)
authoritative_pawn = pawn(true)
host.local_pawn = {pawn = pawn(true)}
pre(host_context)
post(host_context)
carry.sync(host, "hosting")
traced = authority.Capsule
local lease = ("a"):rep(32)
local grab = {"ticket", "native-prop-carry", lease, "Guest pawn", "grab", "1", "0", "0", fixture_class, "100", "0", "0"}
assert(not pcall(carry.command, grab, host, "hosting"), "host allowed a grab before stock physics")
authority.Capsule:SetSimulatePhysics(true)
host.local_pawn.pawn.HeldObject = authority
assert(not pcall(carry.command, grab, host, "hosting"), "host grip was stolen")
host.local_pawn.pawn.HeldObject = nil
assert(carry.command(grab, host, "hosting") == "stock physics prop action verified")
assert(authoritative_pawn.HeldObject == authority)
local drop = {"ticket", "native-prop-carry", lease, "Guest pawn", "drop", "2", "0", "0"}
assert(carry.command(drop, host, "hosting") == "stock physics prop action verified")
assert(not authoritative_pawn.HeldObject and authority.collision == nil, "host stock collision was altered")

local stock_bolt, bolt_host, stock_bolt_context = projectile(bolt_path, true)
stock_bolt.FuseTime = 3
stock_bolt.Lit = false
function stock_bolt:K2_DestroyActor() error("flare expiry touched a stock bolt") end
pre(stock_bolt_context)
post(stock_bolt_context)
assert(stock_bolt.bReplicates and stock_bolt.bReplicateMovement)
assert(stock_bolt.collision == nil and stock_bolt.ProjectileMovement.active and stock_bolt.RotatingMovement.active)
assert(stock_bolt.FuseTime == 3 and not flare.carry_root(stock_bolt, bolt_host.world))
flare.sync(bolt_host, "hosting")
print("qualified flare grip and inert bolt replica use distinct stock lifecycles")
