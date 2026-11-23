-- Read stock day-cycle inputs at the first private guest startup of one armed map.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then return end
local _, nonce = mailbox:match("[/\\]titles[/\\]([a-zA-Z0-9][a-zA-Z0-9_%-]*)[/\\]run[/\\]game%-bridge[/\\]([0-9a-f]+)$")
if not nonce or #nonce ~= 32 then return end

local maps = "/Game/Classes/Maps/Maps_OverWorld/"
local day_class = "DayCycle_SunControler_BP_C"
local by_actor, by_world = {}, {}
for _, row in ipairs({{"Observitory_P", 0}, {"CrossRoads_P", 1}, {"RuinFeilds_P", 0}}) do
    local map = maps .. row[1]
    local world_path = map .. "." .. row[1]
    local actor_path = world_path .. ":PersistentLevel." .. day_class .. "_" .. row[2]
    local entry = {map = map, world = "World " .. world_path, path = actor_path, actor = day_class .. " " .. actor_path}
    by_actor[entry.actor], by_world[entry.world] = entry, entry
end
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local request = mailbox .. "/portable-guest-world-start-request.tsv"
local reply = mailbox .. "/portable-guest-world-start-reply.tsv"

local function valid(object) return object and object:IsValid() end
local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end
local function text(value) return type(value) == "userdata" and value:ToString() or value end

local function observe(phase, entry, world, actor)
    if not valid(actor) or actor:GetFullName() ~= entry.actor
        or not valid(world) or world:GetFullName() ~= entry.world
        or not same(actor:GetWorld(), world) or not same(actor:GetOuter(), world.PersistentLevel)
        or not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then return end
    local input = io.open(request, "rb")
    if not input then return end
    local line = input:read("*l")
    input:close()
    if not line then return end
    local session, arm, destination, character = line:match("^([0-9a-f]+)\t([0-9a-f]+)\t([^\t]+)\t([0-9a-f]+)$")
    if not session or #session ~= 32 or #arm ~= 32 or #character ~= 32 or destination ~= entry.map then return end
    local slot = "SFCoopGuest_" .. character
    local instance = world.OwningGameInstance
    if not valid(instance) or not same(instance:GetClass(), StaticFindObject(instance_class))
        or text(instance.CurrentSaveSlot) ~= slot or instance.SaveAfterTransition ~= false then return end
    local save = instance.SaveSlot_Ref
    if not valid(save) or not same(save:GetClass(), StaticFindObject(save_class))
        or not save:GetFullName():match("^SaveGame_BP_C /Engine/Transient[.:]") then return end
    local math_cdo = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
    if not valid(math_cdo) then return end
    local parts = {}
    math_cdo:BreakTimespan(save.GameTime, parts, parts, parts, parts, parts)
    local milliseconds = (((parts.Days * 24 + parts.Hours) * 60 + parts.Minutes) * 60 + parts.Seconds) * 1000
        + parts.Milliseconds
    local output = assert(io.open(reply, "ab"))
    output:write(("%s\t%s\t%s\t%s\t%s\t%d\t%d\t%.12g\n"):format(phase, session, arm, entry.map, slot,
        save.InstanceSeed, milliseconds, instance.Travel_TimeStep))
    output:close()
end

RegisterBeginPlayPreHook(function(context)
    local actor = context:get()
    local entry = valid(actor) and by_actor[actor:GetFullName()]
    if entry then observe("begin-play-pre", entry, actor:GetWorld(), actor) end
end)

RegisterLoadMapPostHook(function(_, loaded)
    local world = loaded:get()
    local entry = valid(world) and by_world[world:GetFullName()]
    if entry then observe("load-map-post", entry, world, StaticFindObject(entry.path)) end
end)
