package.path = "mod/SilverfishCoop/Scripts/?.lua;" .. package.path

local environment = {
    SILVERFISH_COOP_NATIVE_GUEST_SAVE = "1",
    SILVERFISH_COOP_NATIVE_FIXTURES = "1",
    SILVERFISH_COOP_HEADLESS = "1",
    SILVERFISH_COOP_NATIVE_CAMPAIGN_NPC_VIEW = "flayer",
}
os.getenv = function(name) return environment[name] end

local pawn = { GetAddress = function() return 1 end }
local detached = { GetAddress = function() return 2 end }
package.loaded.guest_save = { exact_item_context = function() return pawn, detached end }
local order, mode, broken_fire, broken_restore = {}, nil, false, false
package.loaded.native_campaign_npc = {
    before_guest_fire = function(_, phase, name, guid)
        assert(phase == "hosting" and name == "exact guest" and #guid == 32)
        order[#order + 1] = "check"
        if mode == "refused" then return nil, "stock_flayer_shot_pair_guest_not_standing" end
        if mode == "armed" then
            return function()
                order[#order + 1] = "restore"
                if broken_restore then error("stock pose unavailable") end
            end
        end
    end,
}

SilverfishInspectNativeGuestWeapon = function()
    order[#order + 1] = "inspect"
    return true, "equipped", 6, 6
end
SilverfishFireNativeGuestWeapon = function(pawn_address, detached_address)
    assert(pawn_address == 1 and detached_address == 2)
    order[#order + 1] = "stock fire"
    if broken_fire then error("partially executed stock fire") end
    return true, "fired", 7, 6, 1
end

local weapon = require("native_guest_weapon")
local state = { map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P", listening = true, guests = {} }
local fields = { "request", "native-guest-weapon-fire", "exact guest", ("A"):rep(32) }
local function fire(expected_order, status, ok)
    order = {}
    assert(weapon.command(fields, state, "hosting"))
    local row = weapon.rows(state, "hosting", function(...) return {...} end)[1]
    assert(table.concat(order, ",") == expected_order, table.concat(order, ","))
    assert(row[6] == ok and row[7] == status, row[7])
end

fire("check,stock fire,inspect", "equipped", 1)
mode = "armed"
fire("check,stock fire,restore,inspect", "equipped", 1)
broken_fire = true
fire("check,stock fire,restore", "mutation_unverified_stock_flayer_shot_native_fire", 0)
broken_fire, broken_restore = false, true
fire("check,stock fire,restore", "mutation_unverified_stock_flayer_shot_pose_restore", 0)
mode = "refused"
fire("check", "stock_flayer_shot_pair_guest_not_standing", 0)
