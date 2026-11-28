-- Run with Lua 5.4 from the repository root.
package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local function object(address, fields)
    function fields:GetAddress() return address end
    function fields:IsValid() return true end
    return fields
end

local reflective_reads, native_reads = 0, 0
local actor = object(7, {Triggered = false})
local spark_state = true
local center = {X = 14, Y = 25, Z = 36}
local function owner()
    reflective_reads = reflective_reads + 1
    return actor
end
actor.NI_Sparks = object(8, {GetOwner = owner, IsActive = function() return spark_state end})
actor.SparkLoop_Cue = object(9, {GetOwner = owner})
actor.Sphere = object(10, {K2_GetComponentLocation = function() return center end})

local fallback = require("native_electrode_view")
package.loaded.native_electrode_view = nil
function SilverfishReadNativeElectrodeSample(address, world)
    native_reads = native_reads + 1
    assert(address == 7)
    if world == 99 then return actor.Triggered, spark_state, center.X, center.Y, center.Z end
end
local native = require("native_electrode_view")

local function row(...) return table.concat({...}, "|") end
local function compare(phase, world, expected)
    local placed = {actor = actor, placed = "/Game/Map.Map:PersistentLevel.Electrode", armed = true}
    local readback = fallback.observe(placed, phase, row, 1, 2, 3, 7, world)
    local before = reflective_reads
    assert(native.observe(placed, phase, row, 1, 2, 3, 7, world) == readback)
    assert(readback == "native-electrode-phase|" .. placed.placed .. "|1|2|3|" .. expected)
    assert(reflective_reads - before == (world == 99 and 0 or 2))
    native.retire(placed)
    assert(native.retired_rows(row)[1] == "native-electrode-phase|" .. placed.placed .. "|1|2|3|2|0|1|2|3")
end

compare("hosting", 99, "1|1|14|25|36")
actor.Triggered = true
spark_state = false
compare("hosting", 99, "0|0|14|25|36")
compare("connected", 99, "1|0|14|25|36")
compare("hosting", 98, "0|0|14|25|36")
actor.Triggered = false
actor.Sphere = nil
compare("hosting", 98, "1|0|1|2|3")
assert(native_reads == 5)
