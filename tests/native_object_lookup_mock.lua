package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local module = require("native_object_lookup")
local path = "/Game/Maps/SomeMap.SomeMap:PersistentLevel.SomeActor"
local valid = {IsValid = function() return true end}
local missing = {IsValid = function() return false end}
local fallbacks, attempts = 0, 0
local fallback_args
local stock = function(...)
    fallbacks = fallbacks + 1
    fallback_args = table.pack(...)
    return valid
end
StaticFindObject = stock
module.install()
assert(StaticFindObject == stock, "Older add-ons use the existing UE4SS lookup")

local result
SilverfishFindObjectByPath = function(name)
    assert(name == path)
    attempts = attempts + 1
    return result
end
module.install()
assert(StaticFindObject(path) == valid and fallbacks == 1, "Early startup retains the original lookup")
result = valid
assert(StaticFindObject(path) == valid and fallbacks == 1, "Resolved full paths use the hash result")
result = missing
assert(not StaticFindObject(path):IsValid() and fallbacks == 1, "A hash miss cannot trigger the global scan")

assert(StaticFindObject("SomeShortName") == valid and fallbacks == 2 and attempts == 3)
assert(StaticFindObject(nil, nil, path, true) == valid and fallbacks == 3 and attempts == 3)
assert(fallback_args.n == 4 and fallback_args[3] == path and fallback_args[4] == true)
print("Exact object misses skip the global scan and preserve the ordinary lookup overloads")
