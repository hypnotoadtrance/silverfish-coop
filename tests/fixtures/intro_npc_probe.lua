local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or not mailbox:match("authored%-route%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local intro = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"
local last

local function valid(actor) return actor and actor:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function census()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(world) and world:GetFullName() == "World " .. intro and valid(driver) and same(driver.World, world),
        "the private pair is not in Tutorial Dock")
    local guest = valid(driver.ServerConnection)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local npc = StaticFindObject("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C")
    assert(valid(statics) and valid(npc), "stock NPC census classes unavailable")
    local found, result = {}, {guest and "guest" or "host"}
    statics:GetAllActorsOfClass(world, npc, found)
    local function record(actor, family)
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            local alive = family == "npc" and tostring(actor.Alive) or "-"
            result[#result + 1] = ("%s;%s;%d;%s;%s"):format(family, actor:GetClass():GetFullName():match(" (%S+)$"),
                actor:GetLocalRole(), tostring(actor.bReplicates), alive)
            assert(#result <= 129, "Tutorial Dock actor census exceeded its bound")
        end
    end
    for _, actor in ipairs(found.OutActors or found) do record(actor, "npc") end
    for _, actor in ipairs(FindAllOf("Prop_Mannequin_C") or {}) do record(actor, "mannequin") end
    return table.concat(result, "|")
end

LoopInGameThreadWithDelay(80, function()
    local input = io.open(mailbox .. "/intro-npc-command.tsv", "rb")
    if not input then return end
    local line = input:read(96)
    input:close()
    local token = line and line:match("^(%x+)\tcensus\t%-$")
    if not token or #token ~= 32 or token == last then return end
    last = token
    local ok, value = pcall(census)
    local output = assert(io.open(mailbox .. "/intro-npc-reply.tsv", "wb"))
    output:write(token, "\t", ok and "ok" or "error", "\t", (tostring(value):gsub("[\r\n\t]", " ")))
    output:close()
end)
