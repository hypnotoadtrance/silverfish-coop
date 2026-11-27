package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local handgun = require("native_avatar_handgun")

local function object(value)
    value.IsValid = function(self) return self.alive ~= false end
    value.GetAddress = function(self) return self end
    return value
end

local world, weapon_class, arrow_class = object({}), object({}), object({})
local firearm = "/Game/Classes/Weapons/Wep_Revolver_01.Wep_Revolver_01_C"
weapon_class.GetFullName = function() return "BlueprintGeneratedClass " .. firearm end
local aim = {Pitch = 15, Yaw = 25, Roll = 42}
local aim_reads = 0
local pawn = object({
    GetWorld = function() return world end,
    IsActorBeingDestroyed = function() return false end,
    IsLocallyControlled = function(self) return self.local_control == true end,
    GetBaseAimRotation = function() aim_reads = aim_reads + 1; return aim end,
    K2_GetActorRotation = function() return {Yaw = 10} end,
    CharacterMovement = {IsFalling = function(self) return self.falling == true end},
})
local actor = object({
    ItemType = 3,
    GetWorld = pawn.GetWorld,
    IsActorBeingDestroyed = function(self) return self.retiring == true end,
    GetClass = function() return weapon_class end,
    GetAttachParentActor = function() return pawn end,
    GetOwner = function() return pawn end,
})
local root_transform, muzzle_transform = {}, {}
actor.RootComponent = object({GetOwner = function() return actor end,
    K2_GetComponentToWorld = function() return root_transform end})
local muzzle = object({GetOwner = function() assert(not actor.retiring, "Do not probe a retiring stock muzzle"); return actor end,
    IsA = function(_, class) return class == arrow_class end,
    K2_GetComponentToWorld = function() return muzzle_transform end})
actor.Arrow_Muzzle = muzzle

local relative, inverted = {Translation = {X = 30, Y = 0, Z = 5}}, {}
local correction = {Pitch = 0, Yaw = -90, Roll = 0}
local api = object({
    MakeRelativeTransform = function(_, target, reference)
        assert(target == muzzle_transform and reference == root_transform)
        return relative
    end,
    InvertTransform = function(_, value)
        assert(value == relative)
        return {Rotation = inverted}
    end,
    Quat_Rotator = function(_, value)
        assert(value == inverted)
        return correction
    end,
    ComposeRotators = function(_, from, to)
        assert(from == correction and to.Roll == 0)
        return {Pitch = to.Pitch, Yaw = to.Yaw - 90, Roll = 0}
    end,
})
StaticFindObject = function(path)
    if path == "/Script/Engine.Default__KismetMathLibrary" then return api end
    if path == "/Script/Engine.ArrowComponent" then return arrow_class end
end
EFindName, FName = {FNAME_Find = 0}, function(name) return name end

local parent, attachment, rotations
local inner = object({
    GetAttachParent = function() return parent end,
    K2_AttachToComponent = function(_, target, socket, position, rotation, scale, weld)
        assert(socket == "None" and position == 0 and rotation == 0 and scale == 0 and not weld)
        parent, attachment = target, true
        return true
    end,
})
local outer = object({
    SetAbsolute = function(self, location, rotation, scale)
        assert(not location and not scale)
        self.absolute = rotation
    end,
    K2_SetWorldRotation = function(self, value)
        rotations = (rotations or 0) + 1
        self.world_rotation = value
    end,
    K2_SetRelativeRotation = function(self, value) self.relative_rotation = value end,
    K2_DestroyComponent = function(self, owner) assert(owner == pawn); self.alive = false end,
})
local entry = {pawn = pawn, stock_weapon_actor = actor, weapon_anchor = inner, weapon_stance = "pistol",
    firearm_class = firearm}
local function attach(target)
    assert(target == entry and entry.handgun_hardpoint == nil)
    entry.handgun_hardpoint = outer
    return outer
end

handgun.sync(entry, false, attach)
assert(not attachment and not entry.handgun_view)
muzzle.IsA = function() return false end
handgun.sync(entry, true, attach)
assert(not attachment and not entry.handgun_view)
muzzle.IsA = function(_, class) return class == arrow_class end
handgun.sync(entry, true, attach)
assert(attachment and parent == outer)
handgun.update(entry, api)
assert(outer.absolute and outer.world_rotation.Pitch == 15 and outer.world_rotation.Yaw + 90 == 25)
handgun.update(entry, api, 15, 25)
assert(rotations == 1 and aim_reads == 1, "A shared steady sample must not reread aim or rewrite the transform")
handgun.update(entry, api, 345, 40)
assert(outer.absolute and outer.world_rotation.Pitch == -15 and outer.world_rotation.Yaw + 90 == 40)
assert(aim_reads == 1)
aim.Yaw = 170
handgun.update(entry, api, 0, math.huge)
assert(not outer.absolute and outer.relative_rotation.Yaw == 0 and aim_reads == 2)
aim.Yaw = 0
handgun.update(entry, api)
assert(outer.absolute)
muzzle.alive = false
handgun.update(entry, api)
assert(not outer.absolute, "A retired muzzle left the hardpoint pinned")
muzzle.alive = true
handgun.update(entry, api)
assert(outer.absolute)
actor.retiring = true
handgun.update(entry, api)
assert(not outer.absolute and outer.relative_rotation.Yaw == 0, "A still-valid retiring weapon must release its aim pivot")
actor.retiring = false
handgun.sync(entry, false, attach)
assert(not entry.handgun_view.requested and parent == outer)
entry.weapon_stance, entry.weapon_anchor = "rifle", object({})
inner.alive = false
handgun.sync(entry, true, attach)
assert(not outer.alive and not entry.handgun_view and not entry.handgun_hardpoint)
print("stock handgun aim preserves recoil parenting and releases the visual hardpoint")
