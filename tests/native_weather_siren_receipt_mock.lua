package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function object(name, class)
    local value = {}
    function value:IsValid() return true end
    function value:GetAddress() return self end
    function value:GetClass() return object(class) end
    function value:GetFullName() return name end
    return value
end

local world = object("World stock")
local location = "/Game/Crossroads:PersistentLevel.AlarmSiren_BP_C_1"
local alarm = object("AlarmSiren_BP_C " .. location,
    "BlueprintGeneratedClass /Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C")
alarm.bReplicates = false
function alarm:GetWorld() return world end
function alarm:IsActorBeingDestroyed() return false end
function alarm:HasAuthority() return true end
alarm.Audio = object("Audio", "Class /Script/Engine.AudioComponent")
function alarm.Audio:GetOwner() return alarm end

local sound_prefix = "/Game/Assets/Sounds/CUEs/Alarm_Cues/"
local storm_path = sound_prefix .. "StormSiren_02_Cue.StormSiren_02_Cue"
local pestilence_path = sound_prefix .. "StormSiren_03_Cue.StormSiren_03_Cue"
local storm = object("SoundCue " .. storm_path)
local pestilence = object("SoundCue " .. pestilence_path)
local base = object("SoundCue " .. sound_prefix .. "Alarm_Base_Loop_01_Cue.Alarm_Base_Loop_01_Cue")
local siren_function = "/Game/Classes/Actors/AlarmSiren_BP.AlarmSiren_BP_C:StormSiren"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local now, wall, hook = 200, 100, nil
os.time = function() return wall end
local clock = object("Clock")
function clock:GetTimeSeconds(test_world) assert(test_world == world); return now end
local objects = {[siren_function] = object("Siren function"), [statics_path] = clock, [location] = alarm,
    [storm_path] = storm, [pestilence_path] = pestilence}
function StaticFindObject(path) return objects[path] end
function RegisterHook(function_path, callback) assert(function_path == siren_function); hook = callback end

local function wrapped(value) return {get = function() return value end} end
local function call(sound) hook(wrapped(alarm), wrapped(sound), wrapped(12)) end
local state = {world = world}
local weather = require("native_weather_view")
weather.sync(state, "hosting")
local function rows() return weather.siren_rows(state, "hosting", table.pack) end

alarm.Audio.Sound = storm
call(storm)
local first = rows()[1]
assert(first[2] == 1 and first[4] == 2 and first[5] == 112)
now, wall = 203, 103
call(pestilence) -- Stock DoOnce rejects the attempted replacement.
call(storm) -- An identical request also cannot refresh the previous receipt.
assert(#rows() == 1 and rows()[1][2] == 1 and rows()[1][5] == 112)
now, wall = 213, 113
call(storm) -- The cue is still assigned during the reset delay.
assert(#rows() == 0)
now, wall = 216, 116
alarm.Audio.Sound = base -- The stock horn restores this sound when it reopens.
alarm.Audio.Sound = storm
call(storm)
assert(rows()[1][2] == 2 and rows()[1][4] == 2 and rows()[1][5] == 128)
now, wall = 233, 133
alarm.Audio.Sound = pestilence
call(pestilence)
assert(rows()[1][2] == 3 and rows()[1][4] == 3)

local playback_calls = 0
function alarm:StormSiren(sound)
    playback_calls = playback_calls + 1
    self.Audio.Sound = sound
end
assert(not weather.play_siren(state, location, 3, 10) and playback_calls == 0)
alarm.Audio.Sound = base
assert(weather.play_siren(state, location, 3, 10) and playback_calls == 1)
