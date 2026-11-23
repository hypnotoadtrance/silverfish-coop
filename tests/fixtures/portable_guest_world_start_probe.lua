-- Read the stock Observatory day-cycle inputs at first private guest world startup.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then return end
local _, nonce = mailbox:match("[/\\]titles[/\\]([a-zA-Z0-9][a-zA-Z0-9_%-]*)[/\\]run[/\\]game%-bridge[/\\]([0-9a-f]+)$")
if not nonce or #nonce ~= 32 then return end

local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local actor_path = world_path .. ":PersistentLevel.DayCycle_SunControler_BP_C_0"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local request = mailbox .. "/portable-guest-world-start-request.tsv"
local reply = mailbox .. "/portable-guest-world-start-reply.tsv"

local function valid(object) return object and object:IsValid() end
local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end
local function text(value) return type(value) == "userdata" and value:ToString() or value end

local function observe(phase, world, actor)
    if not valid(actor) or actor:GetFullName() ~= "DayCycle_SunControler_BP_C " .. actor_path
        or not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not same(actor:GetWorld(), world) or not same(actor:GetOuter(), world.PersistentLevel)
        or not actor:HasAnyFlags(EObjectFlags.RF_WasLoaded) then return end
    local input = io.open(request, "rb")
    if not input then return end
    local line = input:read("*l")
    input:close()
    if not line then return end
    local session, visit, character = line:match("^([0-9a-f]+)\t([0-9a-f]+)\t([0-9a-f]+)$")
    if not session or #session ~= 32 or #visit ~= 32 or #character ~= 32 then return end
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
    output:write(("%s\t%s\t%s\t%s\t%s\t%d\t%d\t%.12g\n"):format(phase, session, visit, map, slot,
        save.InstanceSeed, milliseconds, instance.Travel_TimeStep))
    output:close()
end

RegisterBeginPlayPreHook(function(context)
    local actor = context:get()
    if valid(actor) and actor:GetFullName() == "DayCycle_SunControler_BP_C " .. actor_path then
        observe("begin-play-pre", actor:GetWorld(), actor)
    end
end)

RegisterLoadMapPostHook(function(_, loaded)
    local world = loaded:get()
    if valid(world) and world:GetFullName() == "World " .. world_path then
        observe("load-map-post", world, StaticFindObject(actor_path))
    end
end)
