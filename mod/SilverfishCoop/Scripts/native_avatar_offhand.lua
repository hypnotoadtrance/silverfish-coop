local offhand = {}

local mesh_path = "/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_Flashlight_01.SM_Prop_Flashlight_01"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local math_class = "/Script/Engine.Default__KismetMathLibrary"
local mount_transform = {
    Translation = {X = 0, Y = 0, Z = 0}, Rotation = {X = 0, Y = 0, Z = 0, W = 1},
    Scale3D = {X = 1, Y = 1, Z = 1},
}

-- Mirrored stock right-hand torch, measured in the observer's left palm basis.
local grip_offset = {X = 10.406804, Y = 3.259001, Z = 3.000433}
local grip_x = {X = .09869143, Y = -.05460232, Z = -.99361893}
local grip_y = {X = .61628099, Y = .78732183, Z = .01794657}

local function valid(object) return object and object:IsValid() end

local function difference(a, b)
    return {X = a.X - b.X, Y = a.Y - b.Y, Z = a.Z - b.Z}
end

local function normalized(v)
    local length = math.sqrt(v.X^2 + v.Y^2 + v.Z^2)
    if length < .001 then return end
    return {X = v.X / length, Y = v.Y / length, Z = v.Z / length}
end

local function placement(body, socket, api)
    local index = FName("LeftHandIndex1", EFindName.FNAME_Find)
    local ring = FName("LeftHandRing1", EFindName.FNAME_Find)
    if not body:DoesSocketExist(socket) or not body:DoesSocketExist(index) or not body:DoesSocketExist(ring) then return end
    local wrist, a, b = body:GetSocketLocation(socket), body:GetSocketLocation(index), body:GetSocketLocation(ring)
    local center = {X = (a.X + b.X + wrist.X * 2) / 4, Y = (a.Y + b.Y + wrist.Y * 2) / 4, Z = (a.Z + b.Z + wrist.Z * 2) / 4}
    local x, toward = normalized(difference(a, b)), difference(center, wrist)
    if not x then return end
    local projection = toward.X*x.X + toward.Y*x.Y + toward.Z*x.Z
    local y = normalized({X = toward.X - projection*x.X, Y = toward.Y - projection*x.Y, Z = toward.Z - projection*x.Z})
    if not y then return end
    local z = {X = x.Y*y.Z - x.Z*y.Y, Y = x.Z*y.X - x.X*y.Z, Z = x.X*y.Y - x.Y*y.X}
    local function rotate(v)
        return {X = x.X*v.X + y.X*v.Y + z.X*v.Z, Y = x.Y*v.X + y.Y*v.Y + z.Y*v.Z, Z = x.Z*v.X + y.Z*v.Y + z.Z*v.Z}
    end
    local offset = rotate(grip_offset)
    return {X = center.X + offset.X, Y = center.Y + offset.Y, Z = center.Z + offset.Z},
        api:MakeRotFromXY(rotate(grip_x), rotate(grip_y))
end

function offhand.create(pawn, body)
    if not valid(body) then return end
    local api, class = StaticFindObject(math_class), StaticFindObject(mesh_class)
    if not valid(api) or not valid(class) then return end
    local socket = FName("LeftHand", EFindName.FNAME_Find)
    local position, rotation = placement(body, socket, api)
    if not position then return end
    local stock_mesh = StaticFindObject(mesh_path)
    if not valid(stock_mesh) then LoadAsset(mesh_path); stock_mesh = StaticFindObject(mesh_path) end
    if not valid(stock_mesh) then return end
    local mount = pawn:AddComponentByClass(class, true, mount_transform, true)
    if not valid(mount) then return end
    local ok = pcall(function()
        mount:SetStaticMesh(stock_mesh)
        mount:SetMobility(2)
        mount:SetCollisionEnabled(0)
        mount:SetGenerateOverlapEvents(false)
        pawn:FinishAddComponent(mount, true, mount_transform)
        if not mount:K2_AttachToComponent(body, socket, 2, 2, 0, false) then error("Offhand torch attachment refused", 0) end
        mount:K2_SetWorldLocationAndRotation(position, rotation, false, {}, true)
    end)
    if ok then return mount end
    mount:K2_DestroyComponent(pawn)
end

return offhand
