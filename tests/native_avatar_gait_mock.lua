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
    body.PlayAnimation = function(self, asset, loop)
        assert(loop)
        self.played = self.played + 1
        self.AnimationMode = 1
        self.AnimScriptInstance = object({
            CurrentAsset = asset,
            SetBlendSpacePosition = function(_, axis)
                assert(axis.Y == 0 and axis.Z == 0 and axis.X >= 0 and axis.X <= 2, axis.X)
                self.inputs[#self.inputs + 1] = axis.X
            end,
        })
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
    -- A stock action or mesh change may replace the node; do not reuse the old driver.
    entry.body:PlayAnimation(object({}), true)
    assert(gait.apply(entry, mesh, 1, load, prepared))
    assert(entry.body.played == 3 and entry.body.inputs[#entry.body.inputs] == 1)
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
print("Stock grounded blends preserve their driver, interpolate, cancel on crouch, and refuse incompatible assets")
