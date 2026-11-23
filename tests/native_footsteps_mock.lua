package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path
local footsteps = require("native_footsteps")
local game_world, next_world = {}, {}
local function object(fields)
    fields.IsValid = function() return true end
    fields.GetAddress = function(self) return self end
    return fields
end
object(game_world)
object(next_world)
local function constant(value) return function() return value end end
local function parameter(value) return {get = constant(value)} end
local function pawn(role, local_control, name, x)
    local source = object({GetLocalRole = constant(role), IsLocallyControlled = constant(local_control),
        GetFullName = constant(name), IsActorBeingDestroyed = constant(false), GetWorld = constant(game_world),
        K2_GetActorLocation = constant({X = x, Y = 0, Z = 80})})
    source.CapsuleComponent = object({GetOwner = constant(source), GetScaledCapsuleHalfHeight = constant(75),
        K2_GetComponentLocation = source.K2_GetActorLocation})
    return source
end
local area, listener_name = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P", "local pawn"
local listener, remote, distant = pawn(2, true, listener_name, 0), pawn(1, false, "peer pawn", 360), pawn(1, false, "distant", 3100)
local root = "/Game/Assets/Sounds/"
local attpath = root .. "SoundClasses/Player_FootStep_Attenuation.Player_FootStep_Attenuation"
local concpath = root .. "SoundClasses/SoundCon_FootSteps.SoundCon_FootSteps"
local cue = root .. "CUEs/FootStep_Cues/FootStep_Stone_Cue.FootStep_Stone_Cue"
local att, conc = object({}), object({})
local stone = object({GetFullName = constant("SoundCue " .. cue)})
local stock_audio = object({PlaySoundAtLocation = function(_, world_context, sound, where, rotation, volume, pitch,
    start, attenuation, concurrency, owner)
    assert(world_context == listener and owner == remote and sound == stone)
    assert(where.X == 360 and where.Y == 0 and where.Z == 5)
    assert(math.abs(volume - .2) < .0001 and math.abs(pitch - .95) < .0001)
    assert(start == 0 and attenuation == att and concurrency == conc)
    _G.played = (_G.played or 0) + 1
end})
local assets = {[attpath] = att, [concpath] = conc, [cue] = stone,
    ["/Script/Engine.Default__GameplayStatics"] = stock_audio,
    ["/Script/Engine.GameplayStatics:SpawnSoundAttached"] = object({})}
StaticFindObject = function(path) return assets[path] end
LoadAsset = function(path) error("unexpected load " .. path) end
local hook
RegisterHook = function(path, callback)
    assert(path == "/Script/Engine.GameplayStatics:SpawnSoundAttached" and not hook)
    hook = callback
end
local mailbox, local_records = nil, {}
SilverfishTakeRemoteFootsteps = function()
    local pending = mailbox
    mailbox = nil
    return pending
end
SilverfishPublishLocalWeaponCue = function(map, row, remaining)
    assert(map == area and remaining == 160)
    local_records[#local_records + 1] = row
end
local state = {world = game_world, map = area, local_pawn = {name = listener_name, pawn = listener},
    pawns = {{pawn = listener, id = 3}, {pawn = remote, id = 7}, {pawn = distant, id = 9}}}
footsteps.sync(state, "connected", function(details) return details.id end)

local function step(owner, selected_concurrency, sound)
    hook(nil, parameter(sound), parameter(owner.CapsuleComponent), nil, nil, nil, nil, parameter(.2),
        parameter(.95), nil, parameter(att), parameter(selected_concurrency))
end
step(remote, conc, stone)
step(listener, object({}), stone)
step(listener, conc, object({GetFullName = constant("SoundCue /Game/Fake.Fake")}))
assert(#local_records == 0)
step(listener, conc, stone)
assert(#local_records == 1 and local_records[1] == "step|1|Stone|200|950|local pawn")
mailbox = area .. "\n" .. listener_name .. "\n7|Stone|200|950\n3|Stone|200|950\n9|Stone|200|950\n"
footsteps.update(constant(game_world))
assert(_G.played == 1, tostring(_G.played))
mailbox = area .. "\n" .. listener_name .. "\n7|Stone|200|950\n"
footsteps.update(constant(next_world))
assert(_G.played == 1)
mailbox = area .. "\nold pawn\n7|Stone|200|950\n"
footsteps.update(constant(game_world))
assert(_G.played == 1)
footsteps.sync(state, "ready", function(details) return details.id end)
step(listener, conc, stone)
assert(#local_records == 1)
print("Stock local sound capture and scoped cosmetic footstep playback passed")
