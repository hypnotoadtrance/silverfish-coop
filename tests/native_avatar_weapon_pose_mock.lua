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
local class = object({GetFullName = fixed("BlueprintGeneratedClass /Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C")})
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

local mesh_component = object({})
local find_stock_asset = StaticFindObject
StaticFindObject = function(path)
    if path == "/Script/Engine.MeshComponent" then return mesh_component end
    return find_stock_asset(path)
end

local function stock_component(owner, owner_only)
    return object({
        GetOwner = fixed(owner),
        bOnlyOwnerSee = owner_only,
        SetOnlyOwnerSee = function(self, value)
            self.bOnlyOwnerSee = value
            self.writes = (self.writes or 0) + 1
        end,
    })
end

local function reconcile(actor_path, firearm)
    local scans = 0
    local remote = object({})
    local held_class = object({GetFullName = fixed("BlueprintGeneratedClass " .. actor_path)})
    local stock = object({
        User = remote, GetOwner = fixed(remote), GetClass = fixed(held_class), IsA = fixed(true),
        IsActorBeingDestroyed = fixed(false), GetAttachParentActor = fixed(remote),
        K2_GetComponentsByClass = function(self, component_class)
            assert(component_class == mesh_component)
            scans = scans + 1
            return self.components
        end,
    })
    remote.EquipedItem = stock
    local projection = stock_component(remote, false)
    projection.K2_DestroyComponent = function(self, owner)
        assert(owner == remote)
        self.destroyed = true
    end
    local frame = stock_component(stock, false)
    local authored_owner_only = stock_component(stock, true)
    local unrelated = stock_component(remote, false)
    stock.components = {frame, authored_owner_only, unrelated}

    local entry = {pawn = remote, firearm_class = firearm and actor_path or "-", weapon_parts = {projection}}
    view.suppress_stock(entry, 10)
    assert(frame.bOnlyOwnerSee and not projection.bOnlyOwnerSee and scans == 1)
    assert(authored_owner_only.bOnlyOwnerSee and not authored_owner_only.writes and not unrelated.writes)

    local late = stock_component(stock, false)
    stock.components[4] = late
    view.suppress_stock(entry, 10.10)
    assert(not late.bOnlyOwnerSee and scans == 1, "Do not rescan stock equipment every avatar update")
    view.suppress_stock(entry, 10.25)
    assert(late.bOnlyOwnerSee and scans == 2 and frame.writes == 1, "Mask a late part on the same actor")
    assert(entry.weapon_parts[1] == projection and not projection.writes, "Keep the existing hand projection")

    frame.bOnlyOwnerSee = false
    view.suppress_stock(entry, 10.50)
    assert(frame.bOnlyOwnerSee and frame.writes == 2, "Restore observer masking after an equipment visibility update")
    stock.components = {authored_owner_only, unrelated, late}
    view.suppress_stock(entry, 10.75)
    assert(not frame.bOnlyOwnerSee and frame.writes == 3, "Restore a part leaving the stock mesh set")

    local replacement = object(setmetatable({}, {__index = stock}))
    local replacement_frame = stock_component(replacement, false)
    replacement.components = {replacement_frame}
    remote.EquipedItem = replacement
    view.suppress_stock(entry, 11)
    assert(not late.bOnlyOwnerSee and late.writes == 2 and replacement_frame.bOnlyOwnerSee,
        "Reconcile replacement equipment even when its projected model is unchanged")
    assert(entry.weapon_parts[1] == projection and not projection.writes)

    view.clear(entry)
    assert(not replacement_frame.bOnlyOwnerSee and replacement_frame.writes == 2 and authored_owner_only.bOnlyOwnerSee
        and not authored_owner_only.writes and not unrelated.writes and projection.destroyed)
end

reconcile("/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C", true)
reconcile("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C", false)
local wire_mesh = "s@/Game/Classes/Weapons/WeaponMeshes/Nagant.Nagant@0.00@0.00@0.00@0.00@0.00@0.00@1.00@1.00@1.00"
local parts, _, _, grip = view.decode(wire_mesh .. "^29.51,-10.54,10.65")
assert(parts and #parts == 1 and grip.offset.X == 29.51 and grip.offset.Y == -10.54)
assert(not view.decode(wire_mesh .. "^99.00,0.00,0.00") and not view.decode(wire_mesh .. "^0.00,0.00,0.00"))
assert(view.decode(wire_mesh), "outfits without foregrip offsets must still decode")
print("Late rifle and melee meshes are masked at the observer and restored on removal")
