package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local view = require("native_avatar_weapon_view")
local slot_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local rig_socket = "SK_Nagant_Frame"
local nominal = 0.009999999776
local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end
local function fixed(value)
    return function() return value end
end
local function vector(x, y, z)
    return {X = x, Y = y, Z = z}
end
local function asset(name)
    local path = "/Game/Classes/Weapons/" .. name .. "." .. name
    return object({GetFullName = fixed("SkeletalMesh " .. path)}), path
end
local rig_asset, rig_path = asset("SK_Nagant_Obrez_Fixture")
local sight_asset, sight_path = asset("SK_Nagant_Sights")
local root = object({K2_GetComponentToWorld = fixed("root")})
local class = object({})
local scope = object({})
local pawn = object({GetWorld = fixed(scope)})
local weapon = object({
    User = pawn, RootComponent = root, GetAttachParentActor = fixed(pawn), GetWorld = fixed(scope),
    HasAuthority = fixed(true), IsActorBeingDestroyed = fixed(false), GetClass = fixed(class), IsA = fixed(true),
})
pawn.EquipedItem = weapon
local socket_name = {ToString = fixed(rig_socket)}
local rig = object({
    GetOwner = fixed(weapon), GetAttachParent = fixed(root), SkinnedAsset = rig_asset,
    RelativeScale3D = vector(1, 1, 1), IsVisible = fixed(true), bHiddenInGame = false,
    DoesSocketExist = function(_, name) return name == socket_name end,
})
local sight = object({
    GetOwner = fixed(weapon), GetAttachParent = fixed(rig), GetAttachSocketName = fixed(socket_name),
    SkinnedAsset = sight_asset, IsVisible = fixed(true), bHiddenInGame = false,
    RelativeLocation = vector(0, .416486, .124872), RelativeRotation = {Pitch = -1.98, Yaw = 0, Roll = 0},
    RelativeScale3D = vector(.01, nominal, nominal), K2_GetComponentToWorld = fixed("sight"),
})
weapon.Wep_Skeli = rig

local skeletal = object({})
local root_scale = vector(.02, .03, .04)
local root_reads = 0
local math_api = object({
    MakeRelativeTransform = function(_, child, parent)
        assert(child == "sight" and parent == "root")
        root_reads = root_reads + 1
        return {Translation = vector(35, 17, 23), Rotation = "root rotation", Scale3D = root_scale}
    end,
    Quat_Rotator = function(_, rotation)
        assert(rotation == "root rotation")
        return {Pitch = 1, Yaw = 2, Roll = 3}
    end,
})
StaticFindObject = function(path)
    if path == "/Script/Engine.Default__KismetMathLibrary" then return math_api end
    if path == "/Script/Engine.SkeletalMeshComponent" then return skeletal end
    return object({})
end
weapon.K2_GetComponentsByClass = function(_, component_type)
    if component_type == skeletal then return {rig, sight} end
    return {}
end
local equipment = {GetArrayNum = fixed(1), {[slot_class] = class}}
local save = {PlayerEquipIndex = 0, PlayerEquipment = equipment}
local function project()
    local model = view.observe(pawn, save)
    local parts = assert(view.decode(model), model)
    local projected_sight
    for _, part in ipairs(parts) do
        if part.path == sight_path then
            assert(not projected_sight)
            projected_sight = part
        else
            assert(part.path == rig_path and part.kind == "r")
        end
    end
    return projected_sight
end

local projected = assert(project())
assert(projected.kind == "k" and projected.socket == rig_socket and root_reads == 0)
assert(projected.pose[2] == .42 and projected.pose[3] == .12 and projected.pose[4] == -1.98)
assert(projected.pose[7] == .01 and projected.pose[8] == .01 and projected.pose[9] == .01)
assert(sight.RelativeScale3D.Y == nominal, "Owner transform must stay untouched")

sight.RelativeScale3D = vector(-nominal, .01, .01)
projected = assert(project())
assert(projected.socket == rig_socket and projected.pose[7] == -.01 and root_reads == 0)

sight.RelativeScale3D = vector(.00999, .01, .01)
projected = assert(project())
assert(projected.socket == nil and root_reads == 1, "A root pose must never mount on the old socket")
assert(projected.pose[1] == 35 and projected.pose[2] == 17 and projected.pose[3] == 23 and projected.pose[7] == .02)

root_scale = vector(.00999, .01, .01)
assert(not project() and root_reads == 2, "A genuinely underscaled part must stay invalid in both spaces")

sight.RelativeScale3D = vector(4.00001, 1, 1)
assert(not project() and root_reads == 3, "Out-of-range large scales must stay invalid")
print("Stock socket pose and root-space fallback passed")
