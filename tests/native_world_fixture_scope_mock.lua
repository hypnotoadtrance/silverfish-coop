-- Run with the pinned Lua CLI from the repository root.
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local playtest = "/Game/Classes/Maps/PlaytestMap_01"
local environment = {SILVERFISH_COOP_NATIVE_ZONE = "1"}
local function object(fields)
    function fields:IsValid() return true end
    function fields:GetAddress() return self end
    return fields
end

local shop_scans, quest_reads = 0, 0
local world = object({})
local pawn = object({HasAuthority = function() return true end, GetWorld = function() return world end})
local save = object({
    InstanceSeed = 42,
    RespawnLevel = crossroads,
    GameRules = {},
    Flags = {ForEach = function() end},
    ActiveQuests = {Contains = function(_, key)
        assert(key == "Jmr_CrossRoads")
        quest_reads = quest_reads + 1
        return false
    end},
})
local instance = object({SaveSlot_Ref = save, GetFullName = function() return "GameInstance_FP_C Test" end})
local shop_class = object({})
local statics = object({
    GetGameInstance = function() return instance end,
    GetAllActorsOfClass = function(_, requested_world, class, output)
        assert(requested_world == world and class == shop_class)
        shop_scans = shop_scans + 1
        output.OutActors = {}
    end,
})
local modules = {
    zone_maps = {[crossroads] = {}},
    world_seed = {row = function() end, static_placement_seed = function() return "-" end},
}
local env = setmetatable({
    os = {getenv = function(flag) return environment[flag] end, time = function() return 1 end},
    require = function(name) return assert(modules[name]) end,
    FName = function(name) return name end,
    StaticFindObject = function(path)
        if path == "/Script/Engine.Default__GameplayStatics" then return statics end
        if path == "/Game/Classes/Actors/Shop_BP.Shop_BP_C" then return shop_class end
    end,
}, {__index = _G})

local function observe(map, staged)
    local module = assert(loadfile("mod/SilverfishCoop/Scripts/native_world.lua", "t", env))()
    local state = {map = map, world = world, local_pawn = {pawn = pawn}}
    local shops_before, quests_before = shop_scans, quest_reads
    local shops, quest, scene, rules = 0, nil, nil, 0
    for _, row in ipairs(module.rows(state, "hosting", table.pack)) do
        if row[1] == "native-shop" then shops = shops + 1 end
        if row[1] == "native-quest" then quest = row[3] end
        if row[1] == "native-scene" then scene = row end
        if row[1] == "native-rule" then rules = rules + 1 end
    end
    assert(scene[2] == map and scene[5] == crossroads and scene[7] == 42 and rules == 11)
    assert(shops == (staged and 2 or 0) and quest == (staged and "missing" or nil))
    assert(shop_scans - shops_before == (staged and 1 or 0) and quest_reads - quests_before == (staged and 1 or 0))
end

observe(crossroads, false)
observe(playtest, true)
environment.SILVERFISH_COOP_HEADLESS = "1"
for _, flag in ipairs({"SILVERFISH_COOP_NATIVE_FIXTURES", "SILVERFISH_COOP_TITLE_FIXTURES"}) do
    environment[flag] = "1"
    observe(crossroads, true)
    environment[flag] = nil
end
