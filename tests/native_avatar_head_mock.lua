package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path
package.loaded.guest_save = {}

local avatar = require("native_player_avatar")
local handgun = require("native_avatar_handgun")
local function object(fields)
    fields.IsValid = function(self) return not self.retired end
    fields.GetAddress = function(self) return self end
    return fields
end

local function avatars()
    for index = 1, math.huge do
        local name, value = debug.getupvalue(avatar.animate_head, index)
        assert(name, "Missing avatar state")
        if name == "avatars" then return value end
    end
end

local world = object({})
local now, clock_lookups, math_lookups, stock_reads, roll_reads = 0, 0, 0, 0, 0
local clock = object({GetRealTimeSeconds = function(_, value) assert(value == world); return now end})
local math_api = object({})
function StaticFindObject(path)
    if path == "/Script/Engine.Default__GameplayStatics" then
        clock_lookups = clock_lookups + 1
        return clock
    elseif path == "/Script/Engine.Default__KismetMathLibrary" then
        math_lookups = math_lookups + 1
        return math_api
    end
end

local pawn = object({
    IsActorBeingDestroyed = function() return false end,
    GetBaseAimRotation = function()
        stock_reads = stock_reads + 1
        return {Pitch = 76, Yaw = -172, Roll = 0}
    end,
})
local roll, writes = 13, {}
local pivot = object({
    K2_SetWorldRotation = function(_, rotation, sweep, _, teleport)
        assert(not sweep and teleport)
        writes[#writes + 1] = rotation
    end,
    K2_GetComponentRotation = function()
        roll_reads = roll_reads + 1
        return {Roll = roll}
    end,
})
local entry = {world = world, map = "/Game/Test", name = "Guest", status = "default_body",
    pawn = pawn, head_pivot = pivot, handgun_view = {requested = true}}
avatars()[pawn] = entry
local function head_row() return avatar.rows(table.pack)[1][9] end
local function animate() avatar.animate_head(function() return world end) end

local pitch, yaw = 90, 177
function SilverfishReadNativeRemoteAim(scope, actor)
    assert(scope == world and actor == pawn)
    return pitch, yaw
end
local raw
function handgun.update(value, math_library, aim_pitch, aim_yaw)
    assert(value == entry and math_library == math_api)
    raw = {aim_pitch, aim_yaw}
end

assert(head_row() == "-" and roll_reads == 0)
animate()
assert(writes[1].Pitch == 45 and writes[1].Yaw == 177 and raw[1] == 90 and raw[2] == 177)
pitch, yaw, now = -90, -177, .05
animate()
assert(writes[2].Pitch < 0 and writes[2].Pitch > -45 and math.abs(writes[2].Yaw) > 177)
assert(raw[1] == -90 and raw[2] == -177 and stock_reads == 0)
pitch = -80
animate()
assert(#writes == 2 and raw[1] == -80 and raw[2] == -177)
assert(clock_lookups == 1 and math_lookups == 1 and roll_reads == 0)

assert(head_row() == 13 and roll_reads == 1)
roll = 22
assert(head_row() == 13 and roll_reads == 1, "An unchanged aim must not repeat the readback")
now = .06
animate()
assert(head_row() == 22 and roll_reads == 2 and #writes == 3, "Publish actual component roll after changing aim")
now = .07
animate()
function pivot.K2_GetComponentRotation() error("component retiring") end
assert(head_row() == 22, "A failed read must preserve the last published roll")

clock.retired, math_api.retired = true, true
clock = object({GetRealTimeSeconds = function() return now end})
math_api = object({})
SilverfishReadNativeRemoteAim, now = nil, .1
animate()
assert(clock_lookups == 2 and math_lookups == 2 and #writes == 5 and stock_reads == 1)
assert(raw[1] == 76 and raw[2] == -172, "The stock fallback is sampled once for both consumers")

local native_writes, accept = 0, true
entry.pawn_address, entry.head_pivot_path = pawn, "/Game/Test.Test:PersistentLevel.Guest.SceneComponent_0"
function SilverfishWriteNativeRemoteHeadRotation(scope, actor, path, pitch, yaw, roll)
    assert(scope == world and actor == pawn and path == entry.head_pivot_path)
    assert(pitch == 45 and yaw == -172 and roll == 0)
    native_writes = native_writes + 1
    return accept
end
entry.head_aim, now = nil, .15
animate()
assert(native_writes == 1 and #writes == 5, "Native success uses a single rotation write")
entry.head_aim, now, accept = nil, .2, false
animate()
assert(native_writes == 2 and #writes == 6 and writes[6].Pitch == 45 and writes[6].Yaw == -172,
    "A refused native write applies the identical stock fallback")
