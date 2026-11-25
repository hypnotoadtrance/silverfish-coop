local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-impact-"
local decal_path = "/Script/Engine.GameplayStatics:SpawnDecalAttached"
local armed, last, decals, failure = false, nil, {}, nil

local function record(_, material, _, parent, _, where)
    local asset, component, point = material:get(), parent:get(), where:get()
    if not asset:IsValid() or not component:IsValid() then return end
    local path = asset:GetFullName():match(" (.*)$")
    if not path:match("^/Game/Assets/Materials/Decals/D_BHole_") then return end
    local surface = component:GetFullName():match(" (.*)$")
    decals[#decals + 1] = ("%s,%s,%.2f,%.2f,%.2f"):format(path, surface, point.X, point.Y, point.Z)
end

local function run(action)
    if action == "arm" then
        if not armed then
            RegisterHook(decal_path, function(...)
                local ok, error_message = pcall(record, ...)
                if not ok then failure = error_message end
            end)
            armed = true
        end
        decals, failure = {}, nil
        return "armed"
    end
    assert(action == "decals" and armed, "the stock impact probe is not armed")
    assert(not failure, failure)
    return table.concat(decals, "|")
end

LoopInGameThreadWithDelay(35, function()
    local request = io.open(prefix .. "command.tsv", "rb")
    if not request then return end
    local data = request:read(96)
    request:close()
    if not data then return end
    local ticket, action = data:match("^(%x+)\t([a-z%-]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, response = pcall(run, action)
    local output = assert(io.open(prefix .. "reply.tsv", "wb"))
    local message = tostring(response):gsub("[\r\n\t]", " ")
    output:write(ticket, "\t", ok and "ok" or "error", "\t", message)
    output:close()
end)
