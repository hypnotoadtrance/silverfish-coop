package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local avatar = require("native_player_avatar")
local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end

local function upvalue(callback, expected)
    for index = 1, math.huge do
        local name, value = debug.getupvalue(callback, index)
        assert(name, "Missing avatar state " .. expected)
        if name == expected then return value end
    end
end

local world, another_world, skeleton = object({}), object({}), object({})
local mesh = object({Skeleton = skeleton})
local clip = object({Skeleton = skeleton, GetPlayLength = function() return 1 end})
local now, positions, modes = 0, {}, {}
local clock = object({GetRealTimeSeconds = function() return now end})
local player = object({GetWorld = function() return world end, IsActorBeingDestroyed = function() return false end})
local body = object({
    SkinnedAsset = mesh,
    VisibilityBasedAnimTickOption = 3,
    IsAnySimulatingPhysics = function(self) return self.ragdoll == true end,
    SetAnimationMode = function(_, mode) modes[#modes + 1] = mode end,
    SetAnimation = function(_, animation) assert(animation == clip) end,
    Stop = function() end,
    SetPosition = function(_, position) positions[#positions + 1] = position end,
    GetSocketTransform = function() return {Translation = {X = 0, Y = 0, Z = 0}} end,
})
function StaticFindObject(path)
    if path == "/Script/Engine.Default__GameplayStatics" then return clock end
    if path:match("Standing_Melee_Attack_Horizontal%.Standing_Melee_Attack_Horizontal$") then return clip end
end
function FName(name) return name end
EFindName = {FNAME_Find = 1}

local entries = upvalue(avatar.play_melee, "avatars")
local entry = {id = 1, status = "default_body", body = body, pawn = player, animation_mode = 0,
    firearm_class = "-", weapon_parts = {object({})}, pose_request = {old = true}}
entries[player] = entry
local function swing()
    return avatar.play_melee(world, 1, "slash", 9, .8, 0)
end
local function update()
    avatar.animate_melee(function() return world end)
end

assert(swing() == "Standing_Melee_Attack_Horizontal" and entry.pose_request == nil)
now = .1
update()
assert(#positions == 2 and positions[2] > 0 and modes[1] == 1)
entry.weapon_parts = {object({})}
entry.firearm_class = "/Game/Classes/Weapons/Wep_RustyPistol.Wep_RustyPistol_C"
update()
assert(#positions == 2 and modes[2] == 0 and body.VisibilityBasedAnimTickOption == 3)
assert(not swing() and #positions == 2 and #modes == 2, "A late melee cue must leave the firearm pose alone")

entry.firearm_class = "-"
assert(swing())
avatar.animate_melee(function() return another_world end)
assert(#positions == 3 and #modes == 3, "World travel must not write to the retired body")
assert(swing())
body.ragdoll = true
update()
assert(#positions == 4 and #modes == 4 and not swing(), "A ragdoll must not receive swing pose writes")
print("Melee pose follows the current held model and skips retired bodies and firearms")
