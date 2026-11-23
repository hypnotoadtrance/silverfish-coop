package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function object(fields)
    fields = fields or {}
    function fields:IsValid() return not self.destroyed end
    function fields:GetAddress() return self end
    return fields
end

local function component()
    local result = object()
    function result:K2_AttachToComponent(parent) self.parent = parent; return true end
    function result:GetAttachParent() return self.parent end
    function result:K2_SetRelativeLocationAndRotation(location, rotation)
        self.location, self.rotation = location, rotation
    end
    function result:K2_DestroyComponent() self.destroyed = true end
    function result:SetRelativeScale3D(scale) self.scale = scale end
    function result:SetHiddenInGame(hidden) self.hidden = hidden end
    function result:SetCollisionEnabled(mode) self.collision = mode end
    function result:SetGenerateOverlapEvents(value) self.overlaps = value end
    function result:Activate(value) self.active = value end
    function result:SetVisibility(visible) self.visibility = visible end
    function result:SetMobility() end
    function result:SetIntensity(value) self.intensity = value end
    function result:SetLightColor(value) self.color = value end
    function result:SetLightFunctionMaterial(value) self.material = value end
    function result:K2_GetComponentLocation() return {X = 3, Y = 4, Z = 5} end
    return result
end

local clock, emitted = 0, {}
local world, flame_asset, fire_material, point_class = object(), object(), object(), object()
local opened, closed = object(), object()
local keys = {
    ["/Game/Assets/PFX/NI_LighterFlame.NI_LighterFlame"] = flame_asset,
    ["/Game/Assets/Materials/Mat_Misc/LF_Light_Fire.LF_Light_Fire"] = fire_material,
    ["/Script/Engine.PointLightComponent"] = point_class,
    ["/Game/Assets/Sounds/CUEs/Equipment_Cues/Lighter_Open_Cue.Lighter_Open_Cue"] = opened,
    ["/Game/Assets/Sounds/CUEs/Equipment_Cues/Lighter_Close_Cue.Lighter_Close_Cue"] = closed,
}

keys["/Script/Engine.Default__GameplayStatics"] = object({
    GetRealTimeSeconds = function() return clock end,
    PlaySoundAtLocation = function(_, _, sound, location)
        assert(location.X == 3 and location.Y == 4 and location.Z == 5)
        emitted[#emitted + 1] = sound
    end,
})
keys["/Script/Niagara.Default__NiagaraFunctionLibrary"] = object({
    SpawnSystemAttached = function(_, asset, parent, _, position, rotation, _, auto_destroy, auto_activate)
        assert(asset == flame_asset and position.Z > 9 and rotation.Yaw == 90 and not auto_destroy and not auto_activate)
        local effect = component()
        effect.Asset, effect.parent = asset, parent
        return effect
    end,
})

function FName(name) return name end
function StaticFindObject(path) return keys[path] end
function LoadAsset(path) error("Mock should already have " .. path) end

local lighter = require("native_avatar_lighter")
local view = require("native_avatar_weapon_view")
local pawn = object()
function pawn:AddComponentByClass(class) assert(class == point_class); return component() end
function pawn:FinishAddComponent() end
local entry = {pawn = pawn, world = world}
local body, lid = component(), component()
lighter.mount(entry, body, lid)
lighter.apply(entry, "closed")
assert(lid.parent == body and lid.scale.X == 1 and lid.rotation.Pitch == -135 and not entry.lighter.flame and #emitted == 0)
lighter.apply(entry, "lit")
local flame, point = entry.lighter.flame, entry.lighter.light
assert(flame.parent == body and flame.Asset == flame_asset and flame.active and flame.collision == 0 and flame.overlaps == false)
assert(point.parent == body and point.visibility and point.intensity == 3000 and point.material == fire_material)
assert(#emitted == 1 and emitted[1] == opened)
clock = .1
lighter.animate(function() return world end)
assert(lid.rotation.Pitch > -135 and lid.rotation.Pitch < 0)
clock = .21
lighter.animate(function() return world end)
assert(lid.rotation.Pitch == 0)
lighter.apply(entry, "lit")
assert(entry.lighter.flame == flame and #emitted == 1, "Repeated outfit recreated the flame or cue")
lighter.hide(entry, true)
assert(flame.hidden and point.hidden)
lighter.hide(entry, false)
assert(not flame.hidden and not point.hidden)
lighter.apply(entry, "closed")
assert(flame.destroyed and point.destroyed and emitted[2] == closed)
lighter.clear(entry)
assert(not entry.lighter)

local late = {pawn = pawn, world = world}
local late_lid = component()
lighter.mount(late, component(), late_lid)
lighter.apply(late, "lit")
assert(late_lid.rotation.Pitch == 0 and late.lighter.flame and #emitted == 2)
local late_flame, late_light = late.lighter.flame, late.lighter.light
lighter.clear(late)
assert(late_flame.destroyed and late_light.destroyed, "Unequip left a loose effects component")

local pose = "@0.00@0.00@0.00@0.00@0.00@0.00@1.00@1.00@1.00"
local body_part = "s@" .. lighter.body_path .. pose
local lid_part = "s@" .. lighter.lid_path .. pose
local model = body_part .. "|" .. lid_part
local parts, mode = view.decode(model .. "~lit")
assert(#parts == 2 and mode == "lit")
assert(not view.decode(body_part .. "|" .. body_part .. "~lit"), "Non-lighter model accepted lighter state")
assert(view.lighter_override(model, "lit") == model, "Unqualified base model accepted a guest edge")

local selected_class, stock_root, stock_body, stock_lid = object(), component(), component(), component()
local base_asset, lid_asset = object(), object()
keys[lighter.class] = selected_class
keys[lighter.body_path] = base_asset
keys[lighter.lid_path] = lid_asset
keys["/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C"] = object()
keys["/Script/Engine.Default__KismetMathLibrary"] = object({
    MakeRelativeTransform = function(_, transform) return transform end,
    Quat_Rotator = function() return {Pitch = 0, Yaw = 0, Roll = 0} end,
})
local stock_flame = component()
stock_flame.Asset = flame_asset
function stock_flame:IsVisible() return self.visibility == true end
local stock_light = component()
stock_light.IsVisible = stock_flame.IsVisible
local stock = object({Open = false, User = pawn, RootComponent = stock_root,
    SM_Item_Lighter_Flip_01 = stock_body, SM_Item_Lighter_Flip_Lid_01 = stock_lid,
    FX_CandleFlame_01_Converted = stock_flame, PointLight = stock_light})
local geometry_reads = 0
for _, part in ipairs({stock_root, stock_body, stock_lid}) do
    function part:K2_GetComponentToWorld()
        geometry_reads = geometry_reads + 1
        return {Translation = {X = 0, Y = 0, Z = 0}, Rotation = {}, Scale3D = {X = 1, Y = 1, Z = 1}}
    end
end
for _, part in ipairs({stock_body, stock_lid, stock_flame, stock_light}) do
    function part:GetOwner() return stock end
end
stock_lid.parent, stock_body.StaticMesh, stock_lid.StaticMesh = stock_body, base_asset, lid_asset
function stock:GetClass() return selected_class end
function stock:GetWorld() return world end
function stock:GetAttachParentActor() return pawn end
function stock:HasAuthority() return true end
function stock:IsActorBeingDestroyed() return false end
function stock:IsA() return true end
function pawn:GetWorld() return world end
pawn.EquipedItem = stock
local inventory = {GetArrayNum = function() return 1 end, {
    Item_Class_3_2813641E42AA35FC0693029E4EE49F81 = selected_class,
}}
pawn.Save = object({PlayerEquipIndex = 0, PlayerEquipment = inventory})
local first, cached = view.observe(pawn, pawn.Save)
assert(first == model .. "~closed" and geometry_reads == 3)
stock.Open, stock_flame.visibility, stock_light.visibility = true, true, true
assert(view.local_lighter(pawn) == "lit" and view.observe(pawn, pawn.Save, cached) == model .. "~lit")
assert(geometry_reads == 3, "Lighter toggle reran the equipment geometry census")
assert(stock.Open and stock_flame.visibility and stock_light.visibility, "Visual projection modified stock equipment")
print("Stock lighter lid, flame, glow, positional cues, and cleanup passed")
