package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function valid_object()
    local object = {valid = true}
    function object:IsValid() return self.valid end
    return object
end

local function component(parent)
    local object = valid_object()
    object.RelativeRotation = {Pitch = 0, Yaw = 0, Roll = 0}
    object.RelativeLocation = {X = 0, Y = 0, Z = 0}
    object.collision, object.visible = 3, true
    function object:SetCollisionEnabled(collision) self.collision = collision end
    function object:IsVisible() return self.visible end
    function object:SetVisibility(visible) self.visible = visible end
    function object:GetAttachParent() return parent end
    function object:K2_GetComponentToWorld() return {Rotation = self.RelativeRotation} end
    function object:SetStaticMesh(mesh) self.StaticMesh = mesh; return true end
    function object:GetNumMaterials() return 0 end
    function object:K2_AttachToComponent() return true end
    function object:K2_SetRelativeTransform(transform) self.RelativeRotation = transform.Rotation end
    function object:K2_SetRelativeRotation(rotation) self.RelativeRotation = rotation end
    function object:K2_DestroyComponent() self.valid = false end
    function object:SetGenerateOverlapEvents() end
    function object:SetMobility() end
    return object
end

local world, root, actor = valid_object(), component(), valid_object()
function world:GetAddress() return 1 end
function actor:GetWorld() return world end
function actor:IsActorBeingDestroyed() return false end
local created = {}
function actor:AddComponentByClass()
    local part = component()
    created[#created + 1] = part
    return part
end
function actor:FinishAddComponent() end
actor.Looted, actor.Locked = false, false
local panel = component(root)
panel.StaticMesh = valid_object()
local clock, math_api, class = valid_object(), valid_object(), valid_object()
local now = 0
function clock:GetRealTimeSeconds() return now end
function math_api:MakeRelativeTransform(transform) return transform end
function StaticFindObject(path)
    if path:find("GameplayStatics") then return clock end
    if path:find("KismetMathLibrary") then return math_api end
    return class
end
FName = function() return "None" end
EFindName = {FNAME_Find = 0}

local view = require("native_loot_crate_preview")
local current = {crate = actor, profile = {name = "crate"}}
local goal = {{Pitch = 0, Yaw = 0, Roll = -95}}
view.start(current, 1, {panel}, goal, {})
assert(#created == 1 and created[1].RelativeRotation.Roll < -1)
now = .04
view.tick(world)
assert(#created == 1 and created[1].collision == 0 and created[1].RelativeRotation.Roll < -1)
assert(panel.RelativeRotation.Roll == 0 and not panel.visible and panel.collision == 3 and not actor.Looted and not actor.Locked)

view.start(current, 2, {panel}, goal, {})
view.resolve("crate", 1)
assert(#created == 1 and created[1].valid and not panel.visible)
view.resolve("crate", 2)
assert(not created[1].valid and panel.visible and panel.RelativeRotation.Roll == 0)

view.start(current, 3, {panel}, goal, {})
now = 1
view.tick(world)
assert(not created[2].valid and panel.visible)
view.start(current, 4, {panel}, goal, {})
actor.Looted = true
view.tick(world)
assert(not created[3].valid and panel.visible)

actor.Looted = false
view.start(current, 5, {panel}, goal, {})
local motion = created[4]
local preview_roll = motion.RelativeRotation.Roll
panel.RelativeRotation = goal[1]
actor.Looted = true
view.confirm("crate", goal, {})
assert(motion.valid and not panel.visible and motion.collision == 0)
now = 1.06
view.tick(world)
assert(motion.RelativeRotation.Roll < preview_roll and motion.RelativeRotation.Roll > -95 and not panel.visible)
now = 1.13
view.tick(world)
assert(not motion.valid and panel.visible and panel.RelativeRotation.Roll == -95)
print("Stock crate preview moves a collision-free copy and eases into the verified stock pose")
