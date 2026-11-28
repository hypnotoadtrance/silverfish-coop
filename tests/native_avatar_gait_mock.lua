package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local gait = require("native_avatar_gait")

local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end

local blend_class = object({})
StaticFindObject = function(path)
    assert(path == "/Script/Engine.BlendSpace1D")
    return blend_class
end

local skeleton = object({})
local function assets(name, compatible)
    return function(path)
        assert(path == "/Game/Classes/NPCs/NPC_Assets/Animations/" .. name .. "." .. name, path)
        return object({Skeleton = compatible and skeleton or object({}), IsA = function(_, class) return class == blend_class end})
    end
end

local function seat(stance)
    local mesh = object({Skeleton = skeleton})
    local movement = object({grounded = true, IsMovingOnGround = function(self) return self.grounded end})
    local pawn = object({CharacterMovement = movement, Dead = false, bIsCrouched = false})
    local body = object({AnimationMode = 0, played = 0, inputs = {}, IsAnySimulatingPhysics = function() return false end})
    body.SetAnimationMode = function(self, mode, force)
        assert(mode == 1 and not force)
        self.AnimationMode = mode
        self.AnimScriptInstance = object({
            SetBlendSpacePosition = function(_, axis)
                assert(axis.Y == 0 and axis.Z == 0 and axis.X >= 0 and axis.X <= 2, axis.X)
                self.inputs[#self.inputs + 1] = axis.X
            end,
        })
    end
    body.SetAnimation = function(self, asset) self.AnimScriptInstance.CurrentAsset = asset end
    body.Play = function(self, loop)
        assert(loop)
        self.played = self.played + 1
    end
    return {body = body, pawn = pawn, weapon_stance = stance}, mesh
end

local function prepared(entry)
    entry.prepared = (entry.prepared or 0) + 1
end

for _, profile in ipairs({
    {"pistol", "HandGun_Lowered_BlendSpace", "HandGun_Idle_2", "HandGun_Walk", "HandGun_Run"},
    {"rifle", "Rifle_Lowered_BlendSpace", "Rifle_Idle_3", "Rifle_Walk", "Rifle_Run"},
}) do
    local stance, name, idle, walk, run = table.unpack(profile)
    local entry, mesh = seat(stance)
    local load = assets(name, true)
    for _, sample in ipairs({{0, idle}, {1, walk}, {2, run}, {2, run}, {1, walk}, {0, idle}}) do
        assert(gait.apply(entry, mesh, sample[1], load, prepared))
        assert(entry.pose_animation == sample[2])
    end
    assert(entry.body.played == 1 and entry.prepared == 1)
    assert(table.concat(entry.body.inputs, ",") == "0,1,2,1,0")
    -- Resume the active node after a temporary stock action replaces its asset.
    local node = entry.body.AnimScriptInstance
    entry.body:SetAnimation(object({}))
    assert(gait.apply(entry, mesh, 1, load, prepared))
    assert(entry.body.played == 2 and entry.body.AnimScriptInstance == node and entry.body.inputs[#entry.body.inputs] == 1)
end

local entry, mesh = seat()
local unarmed = assets("Unarmed_Walk_Blendspace", true)
assert(gait.apply(entry, mesh, 0, unarmed, prepared))
assert(gait.apply(entry, mesh, 1, unarmed, prepared))
gait.advance(entry, 11)
gait.advance(entry, 11.1)
assert(math.abs(entry.body.inputs[#entry.body.inputs] - .5) < .001, "the unarmed mesh must traverse an intermediate pose")
assert(gait.apply(entry, mesh, 2, unarmed, prepared))
gait.advance(entry, 11.125)
gait.advance(entry, 11.225)
assert(math.abs(entry.body.inputs[#entry.body.inputs] - 1.25) < .001, "retarget the blend from the displayed pose")
gait.advance(entry, 11.35)
assert(entry.body.inputs[#entry.body.inputs] == 2 and entry.pose_animation == "Running_Unarmed")
assert(entry.body.played == 1)

assert(gait.apply(entry, mesh, 0, unarmed, prepared))
entry.pawn.bIsCrouched = true
gait.advance(entry, 11.375)
assert(not entry.gait_blend and entry.body.inputs[#entry.body.inputs] == 2, "crouch must cancel a queued ground interpolation")

entry.pawn.bIsCrouched = false
assert(gait.apply(entry, mesh, 0, unarmed, prepared))
assert(gait.apply(entry, mesh, 1, unarmed, prepared))
entry.pawn.CharacterMovement.grounded = false
gait.advance(entry, 11.4)
assert(not entry.gait_blend and entry.body.inputs[#entry.body.inputs] == 0, "airborne actors must leave the gait alone")

local missing, missing_mesh = seat("rifle")
assert(not gait.apply(missing, missing_mesh, 0, function() end, prepared))
assert(not gait.apply(missing, missing_mesh, 0, assets("Rifle_Lowered_BlendSpace", false), prepared))
assert(missing.body.played == 0 and not missing.gait_blend and not missing.prepared)

local sequence, transient = object({}), object({})
local stock = StaticFindObject
StaticFindObject = function(path)
    if path == "/Engine/Transient" then return transient end
    if path == "/Script/Engine.AnimSequence" then return sequence end
    return stock(path)
end
local function samples(seed)
    local rows = {GetArrayNum = function() return 3 end}
    for i = 1, 3 do rows[i] = {SampleValue = {X = i - 1}, Animation = seed} end
    return rows
end
local template = object({Skeleton = skeleton, SampleData = samples(), GetClass = function() return blend_class end})
local clips = {}
local function load(path)
    local name = assert(path:match("%.(%w[%w_]*)$"))
    if name == "Unarmed_Walk_Blendspace" then return template end
    clips[name] = clips[name] or object({Skeleton = skeleton, IsA = function(_, class) return class == sequence end})
    return clips[name]
end
EObjectFlags = {RF_Transient = 1, RF_MarkAsRootSet = 2}
local clones = 0
StaticConstructObject = function(class, owner, _, flags, _, _, _, original)
    assert(class == blend_class and owner == transient and flags == 3 and original == template)
    clones = clones + 1
    return object({Skeleton = skeleton, SampleData = samples()})
end
local blend_entry, blend_mesh = seat("pistol")
local body = blend_entry.body
local idle, raised = "HandGun_Idle_2", "Rifle_Aiming_Low_Close1"
local function pose(group, name)
    return gait.transition(blend_entry, blend_mesh, group, name, load, prepared)
end
assert(not pose("grounded", idle))
body:SetAnimationMode(1, false)
body:SetAnimation(load("." .. idle))
blend_entry.pose_asset = body.AnimScriptInstance.CurrentAsset:GetAddress()
assert(pose("aim", raised))
gait.advance(blend_entry, 20)
gait.advance(blend_entry, 20.09)
local progress = body.inputs[#body.inputs]
assert(math.abs(progress - .5) < .001 and clones == 1)
local initial_asset = body.AnimScriptInstance.CurrentAsset
assert(pose("grounded", idle))
gait.advance(blend_entry, 20.1)
assert(body.inputs[#body.inputs] == progress, "A reversal must keep the displayed mix")
gait.advance(blend_entry, 20.145)
local returning = body.inputs[#body.inputs]
assert(returning > 0 and returning < progress)
assert(pose("aim", raised))
gait.advance(blend_entry, 20.155)
assert(body.inputs[#body.inputs] == returning and body.AnimScriptInstance.CurrentAsset == initial_asset)
gait.advance(blend_entry, 20.35)
assert(body.inputs[#body.inputs] == 1 and not blend_entry.pose_transition and clones == 1)
assert(template.SampleData[1].Animation == nil, "The stock blend space must stay intact")

local native_entry, native_mesh = seat()
assert(gait.apply(native_entry, native_mesh, 0, unarmed, prepared))
assert(gait.apply(native_entry, native_mesh, 1, unarmed, prepared))
native_entry.world, native_entry.pawn_address, native_entry.head_pivot_path = 1, 2, "pivot"
local native_result, native_writes, expected_ground = true, {}, true
SilverfishAdvanceNativeRemoteGait = function(world, pawn, pivot, instance, asset, ground, write)
    assert(world == 1 and pawn == 2 and pivot == "pivot" and ground == expected_ground)
    local expected = native_entry.pose_transition or native_entry.gait_blend
    assert(instance == expected.instance and asset == expected.asset)
    if native_result and write ~= nil then native_writes[#native_writes + 1] = write end
    return native_result
end
gait.advance(native_entry, 30)
assert(#native_writes == 0 and #native_entry.body.inputs == 1)
gait.advance(native_entry, 30.1)
assert(math.abs(native_writes[1] - .5) < .001 and #native_entry.body.inputs == 1)
native_result = nil
gait.advance(native_entry, 30.25)
assert(native_entry.body.inputs[2] == 1 and not native_entry.gait_blend.from)
assert(gait.apply(native_entry, native_mesh, 2, unarmed, prepared))
native_result = false
gait.advance(native_entry, 31)
assert(not native_entry.gait_blend and #native_entry.body.inputs == 2)
native_entry.pose_transition = {start_value = 0, goal = 1, value = 0, started = 32, instance = 4, asset = 5}
native_result, expected_ground = true, false
gait.advance(native_entry, 32.2)
assert(native_writes[2] == 1 and not native_entry.pose_transition)
SilverfishAdvanceNativeRemoteGait = nil
print("Grounded gait and pose reversals preserve their animation driver and refuse incompatible assets")
