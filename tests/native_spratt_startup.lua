package.path = "./mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local read_env = os.getenv
os.getenv = function(key)
    if key == "SILVERFISH_COOP_HEADLESS" or key == "SILVERFISH_COOP_NATIVE_FIXTURES" then return "1" end
    return read_env(key)
end

-- The staged game loads native_lenny before the other two entry points.
assert(type(require(arg[1] or "native_lenny")) == "table")
assert(type(require("native_guest_npc").approach_revive) == "function")
assert(type(require("native_guest_weapon").command) == "function")
assert(type(require("native_spratt_fixture").before_guest_fire) == "function")
