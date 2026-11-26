package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local now = 10
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local bandage = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local frames, clears = {}, 0
local attached, accept_until = true, math.huge
SilverfishPublishLocalWeaponCue = function(map, record, remaining)
    if not attached or #frames >= accept_until then return false end
    assert(map == area and remaining > 0 and remaining <= 250)
    frames[#frames + 1] = {record, remaining}
    return true
end
SilverfishClearLocalWeaponCues = function() clears = clears + 1 end
io.open = function() error("Gameplay cues must never open a mailbox file") end

local function object(properties)
    properties.IsValid = function() return true end
    properties.GetAddress = function(self) return self end
    return properties
end
local state = {map = area, world = object({})}
local pawn = object({
    HasAuthority = function() return true end,
    GetWorld = function() return state.world end,
    K2_GetActorLocation = function() return {X = 100, Y = 200, Z = 300} end,
})
local statics = object({GetRealTimeSeconds = function() return now end})
StaticFindObject = function(path)
    if path == "/Script/Engine.Default__GameplayStatics" then return statics end
end
LoadAsset = function() end

local avatar = {player_id = function() return 17 end}
local view = require("native_weapon_fire_view")
local function report()
    view.consumed(pawn, bandage, true)
end
local function flush()
    view.flush(function() return state.world end)
end
local function record(id)
    return ("%d|consume|%s|100|200|300|17"):format(id, bandage)
end

view.sync(state, "hosting", avatar)
report()
now = 10.1
report()
flush()
flush()
assert(#frames == 2)
assert(frames[1][1] == record(1) and frames[1][2] >= 149 and frames[1][2] <= 150)
assert(frames[2][1] == record(2) and frames[2][2] >= 249)

report()
now = 10.4
flush()
assert(#frames == 2)

report()
state.world = object({})
flush()
view.sync(state, "hosting", avatar)
assert(clears == 1 and #frames == 2)
report()
flush()
assert(frames[3][1] == record(5))

-- A resident worker disappearing during teardown does not switch active cues to disk.
attached = false
report()
flush()
assert(#frames == 3)
view.sync(state, "ready", avatar)
assert(clears == 2)

attached = true
view.sync(state, "hosting", avatar)
for _ = 1, 40 do report() end
flush()
assert(#frames == 39 and frames[4][1] == record(11) and frames[39][1] == record(46))
-- Detach after accepting one entry. The accepted prefix must not replay on recovery.
accept_until = 40
report()
report()
flush()
assert(#frames == 40 and frames[40][1] == record(47))
accept_until = math.huge
now = now + .04
flush()
assert(#frames == 41 and frames[41][1] == record(48) and frames[41][2] <= 210)
view.sync(state, "ready", avatar)

-- The first cue can arrive before the resident worker attaches.
attached = false
view.sync(state, "hosting", avatar)
report()
flush()
assert(#frames == 41)
attached = true
now = now + .04
flush()
assert(#frames == 42 and frames[42][1] == record(49))

attached = false
report()
flush()
now = now + .3
attached = true
flush()
assert(#frames == 42, "an expired cue must not play after the bridge comes back")

view.sync(state, "ready", avatar)
SilverfishPublishLocalWeaponCue = nil
SilverfishClearLocalWeaponCues = nil
view.sync(state, "hosting", avatar)
report()
flush()
assert(#frames == 42)
-- The shooter uses their live first-person muzzle, while observers keep the host pose.
local weapon_path = "/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C"
local muzzle_location = {X = 152, Y = 225, Z = 325}
local projected = {}
local weapon_base = object({})
local muzzle_flash = object({})
local first_person_flash = object({})
local defaults = object({IsA = function(_, other) return other == weapon_base end, MuzzleFlash = muzzle_flash})
local weapon_class = object({
    GetFullName = function() return "BlueprintGeneratedClass " .. weapon_path end,
    GetCDO = function() return defaults end,
})
local held = object({User = pawn, MuzzleFlash = first_person_flash,
    GetWorld = function() return state.world end, GetClass = function() return weapon_class end,
    IsA = function(_, other) return other == weapon_base end})
held.Arrow_Muzzle = object({
    GetOwner = function() return held end,
    K2_GetComponentLocation = function() return muzzle_location end,
    K2_GetComponentRotation = function() return {Pitch = 0, Yaw = 66, Roll = 0} end,
})
local niagara = object({
    SpawnSystemAttached = function(_, effect, muzzle, socket, point, rotation, attachment)
        assert(effect == first_person_flash and muzzle == held.Arrow_Muzzle and socket == "None" and attachment == 0)
        assert(point.X == 0 and point.Y == 0 and point.Z == 0 and rotation.Yaw == 0)
        projected[#projected + 1] = {muzzle = muzzle}
        return object({SetWorldScale3D = function(_, scale) assert(scale.X == 1 and scale.Y == 1 and scale.Z == 1) end})
    end,
    SpawnSystemAtLocation = function(_, _, effect, point, rotation)
        assert(effect == muzzle_flash)
        projected[#projected + 1] = {point = point, yaw = rotation.Yaw}
        return object({})
    end,
})
FName = function(name) return name end
local resolve = StaticFindObject
local weapon_base_path = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local recoil_function = object({})
local math_library = object({ComposeRotators = function(_, rest, kick)
    return {Pitch = kick.Pitch, Yaw = rest.Yaw + kick.Yaw, Roll = kick.Roll}
end})
StaticFindObject = function(path)
    if path == weapon_path then return weapon_class end
    if path == weapon_base_path then return weapon_base end
    if path == weapon_base_path .. ":ApplyRecoil" then return recoil_function end
    if path == "/Script/Engine.Default__KismetMathLibrary" then return math_library end
    if path == "/Script/Niagara.Default__NiagaraFunctionLibrary" then return niagara end
    return resolve(path)
end
pawn.IsLocallyControlled = function() return true end
pawn.EquipedItem = held
state.local_pawn = {pawn = pawn}
local function shot(id, shooter)
    return ("%d|%s|100|200|300|0|15|0|1|0|0|%d"):format(id, weapon_path, shooter)
end
view.command({"-", "native-weapon-play-shots", area, ("1"):rep(32), shot(1, 17), shot(2, 18)}, state, "connected", avatar)
assert(#projected == 2 and projected[1].muzzle == held.Arrow_Muzzle)
assert(projected[2].point.X == 100 and projected[2].yaw == 15)
local local_kicks, avatar_kicks = {}, {}
pawn.Dead = false
held.ApplyRecoil = function(_, offset, rotation, camera)
    assert(offset.X == -12 and rotation.Yaw == -90)
    local_kicks[#local_kicks + 1] = camera
end
avatar.play_recoil = function(world, player, path, serial, kick)
    assert(world == state.world and player == 18 and path == weapon_path and serial == 4)
    assert(#kick == 7)
    avatar_kicks[#avatar_kicks + 1] = kick
    return true
end
local values = "|-12|0|0|1|0|-2|0.2"
local camera = "|1.18|0.71|0.12"
view.command({"-", "native-weapon-play-shots", area, ("1"):rep(32),
    shot(3, 17) .. values .. camera, shot(4, 18) .. values .. camera,
    shot(5, 17) .. values}, state, "connected", avatar)
assert(#local_kicks == 2 and #avatar_kicks == 1)
assert(local_kicks[1].Pitch == 1.18 and local_kicks[1].Yaw == 0.71 and local_kicks[1].Roll == 0.12)
assert(local_kicks[2].Pitch == 0 and local_kicks[2].Yaw == 0 and local_kicks[2].Roll == 0)
pawn.EquipedItem = nil
view.command({"-", "native-weapon-play-shots", area, ("1"):rep(32), shot(6, 17) .. values .. camera},
    state, "connected", avatar)
assert(#projected == 5 and #local_kicks == 2, "a delayed shot must not flash or kick after holstering")
print("Weapon cues preserve in-memory delivery and use the firing guest's live muzzle")
