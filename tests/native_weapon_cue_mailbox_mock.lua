package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local now = 10
local area = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local bandage = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local bridge = "private-weapon-cues"
local original_getenv = os.getenv
os.getenv = function(name)
    if name == "SILVERFISH_COOP_BRIDGE" then return bridge end
    return original_getenv(name)
end

local frames, files, clears = {}, {}, 0
local attached = true
SilverfishPublishLocalWeaponCue = function(map, record, remaining)
    if not attached then return false end
    assert(map == area and remaining > 0 and remaining <= 250)
    frames[#frames + 1] = {record, remaining}
    return true
end
SilverfishClearLocalWeaponCues = function() clears = clears + 1 end
io.open = function(path, mode)
    assert(path == bridge .. "/native-weapon-shots.tmp" and mode == "wb")
    return {
        write = function(_, text, suffix) files[#files + 1] = text .. suffix; return true end,
        close = function() return true end,
    }
end
os.rename = function(from, to)
    assert(from == bridge .. "/native-weapon-shots.tmp" and to == bridge .. "/native-weapon-shots")
    return true
end

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
assert(#frames == 2 and #files == 0)
assert(frames[1][1] == record(1) and frames[1][2] >= 149 and frames[1][2] <= 150)
assert(frames[2][1] == record(2) and frames[2][2] >= 249)

report()
now = 10.4
flush()
assert(#frames == 2 and #files == 0)

report()
state.world = object({})
flush()
view.sync(state, "hosting", avatar)
assert(clears == 1 and #frames == 2 and #files == 0)
report()
flush()
assert(frames[3][1] == record(5) and #files == 0)

-- A resident worker disappearing during teardown does not switch active cues to disk.
attached = false
report()
flush()
assert(#frames == 3 and #files == 0)
view.sync(state, "ready", avatar)
assert(clears == 2)

attached = true
view.sync(state, "hosting", avatar)
for _ = 1, 40 do report() end
flush()
assert(#frames == 39 and frames[4][1] == record(11) and frames[39][1] == record(46))
assert(#files == 0)
view.sync(state, "ready", avatar)

-- An unattached worker and an older native core both preserve the legacy file route.
attached = false
view.sync(state, "hosting", avatar)
report()
flush()
assert(files[1] == area .. "\n" .. record(47) .. "\n")
view.sync(state, "ready", avatar)
SilverfishPublishLocalWeaponCue = nil
SilverfishClearLocalWeaponCues = nil
view.sync(state, "hosting", avatar)
report()
flush()
assert(files[2] == area .. "\n" .. record(48) .. "\n")
-- The shooter uses their live first-person muzzle, while observers keep the host pose.
local weapon_path = "/Game/Classes/Weapons/Wep_Obrez_01.Wep_Obrez_01_C"
local local_muzzle = {X = 152, Y = 225, Z = 325}
local projected = {}
local weapon_base = object({})
local muzzle_flash = object({})
local defaults = object({IsA = function(_, other) return other == weapon_base end, MuzzleFlash = muzzle_flash})
local weapon_class = object({
    GetFullName = function() return "BlueprintGeneratedClass " .. weapon_path end,
    GetCDO = function() return defaults end,
})
local held = object({User = pawn, GetWorld = function() return state.world end, GetClass = function() return weapon_class end})
held.Arrow_Muzzle = object({
    GetOwner = function() return held end,
    K2_GetComponentLocation = function() return local_muzzle end,
    K2_GetComponentRotation = function() return {Pitch = 0, Yaw = 66, Roll = 0} end,
})
local niagara = object({
    SpawnSystemAtLocation = function(_, _, effect, point, rotation)
        assert(effect == muzzle_flash)
        projected[#projected + 1] = {point = point, yaw = rotation.Yaw}
        return object({})
    end,
})
local resolve = StaticFindObject
StaticFindObject = function(path)
    if path == weapon_path then return weapon_class end
    if path == "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C" then return weapon_base end
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
assert(#projected == 2 and projected[1].point == local_muzzle and projected[1].yaw == 66)
assert(projected[2].point.X == 100 and projected[2].yaw == 15)
pawn.EquipedItem = nil
view.command({"-", "native-weapon-play-shots", area, ("1"):rep(32), shot(3, 17)}, state, "connected", avatar)
assert(#projected == 2, "a delayed shot must not flash at the old position after holstering")
print("Weapon cues preserve in-memory delivery and use the firing guest's live muzzle")
