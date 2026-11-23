-- Read only a requested NPC census from the exact private, muted Ruin Fields pair.
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_RUIN_NPC_PROBE") ~= "1"
    or not bridge or not bridge:match("[/\\]campaign%-npc%-ruin%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then
    return
end

local world_name = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"
local level_path = world_name .. ":PersistentLevel."
local classes = {"NPC_Zomb_01_C", "NPC_WildBoar_C", "NPC_FeralDog_C", "NPC_Survivor_FlowerLady_C",
    "NPC_MinorStag_C", "NPC_Doe_C", "NPC_Survivor_Worker_C", "NPC_Homunculus_C"}
local last

local function valid(value) return value and value:IsValid() end

local function census()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == "World " .. world_name and valid(world.NetDriver), "world")
    local director = StaticFindObject(level_path .. "DynaimicSpawnControler_BP_C_0")
    assert(valid(director) and valid(director.AmbientSquadPool) and director.AmbientSquadPool:GetFullName() ==
        "AmbientSquadPoolData_C /Game/Classes/Libraries/DataAssets/AmbientSpawnPools/FloodPlainsSquadPool.FloodPlainsSquadPool",
        "director")
    local viewer = valid(world.NetDriver.ServerConnection) and "client" or "host"
    local rows = {viewer}
    for _, class in ipairs(classes) do
        local expected = "/Game/Classes/NPCs/" .. class:sub(1, -3) .. "." .. class
        local prefix = class .. " " .. level_path
        for _, actor in ipairs(FindAllOf(class) or {}) do
            local name = actor:GetFullName()
            if name:sub(1, #prefix) == prefix and actor:GetClass():GetFullName() == "BlueprintGeneratedClass " .. expected
                and actor:GetWorld():GetAddress() == world:GetAddress() and not actor:IsActorBeingDestroyed() then
                local point = actor:K2_GetActorLocation()
                rows[#rows + 1] = ("%s\t%s\t%d\t%s\t%s\t%.2f\t%.2f\t%.2f"):format(class, name,
                    actor:GetLocalRole(), tostring(actor.bReplicates), tostring(actor.Alive), point.X, point.Y, point.Z)
                assert(#rows <= 129, "census limit")
            end
        end
    end
    return table.concat(rows, "\n")
end

LoopInGameThreadWithDelay(150, function()
    local file = io.open(bridge .. "/ruin-npc-command.tsv", "rb")
    if not file then return false end
    local request = file:read(40)
    file:close()
    local ticket = (request or ""):match("^(%x+)\n$")
    if not ticket or #ticket ~= 32 or ticket == last then return false end
    last = ticket
    local ok, result = pcall(census)
    local output = assert(io.open(bridge .. "/ruin-npc-reply.tsv", "wb"))
    output:write(ticket, ok and "\n" or "\nerror\t", result, "\nend\n")
    output:close()
    return false
end)
