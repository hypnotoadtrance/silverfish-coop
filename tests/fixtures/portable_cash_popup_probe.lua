local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local polls, observed, last

local function valid(object) return object and object:IsValid() end

local function collect()
    if not polls then return end
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    if not valid(world) or world:GetFullName() ~= "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
        or not valid(world.NetDriver) then return end
    for _, widget in ipairs(FindAllOf("Allert_Widget_C") or {}) do
        if valid(widget) and widget:IsInViewport() and not observed[widget:GetAddress()] then
            local text = widget.Messaage:ToString()
            local label = widget.TextBlock_Message:GetText():ToString()
            if text:match("^Received %$[%d,]+!$") and label == text:upper() then
                observed[widget:GetAddress()] = true
                polls[#polls + 1] = label
            end
        end
    end
end

LoopInGameThreadWithDelay(100, function()
    collect()
    local file = io.open(mailbox .. "/portable-cash-popup-command.tsv", "rb")
    if not file then return false end
    local request = file:read(128)
    file:close()
    local ticket, action = (request or ""):match("^(%x+)\t(%w+)%s*$")
    if not ticket or #ticket ~= 32 or ticket == last then return false end
    last = ticket
    if action == "arm" then polls, observed = {}, {} end
    local result = action == "arm" and "armed"
        or action == "read" and polls and (#polls == 0 and "waiting" or table.concat(polls, "|"))
        or "unavailable"
    local reply = io.open(mailbox .. "/portable-cash-popup-reply.tsv", "wb")
    if reply then reply:write(ticket, "\tok\t", result, "\n"); reply:close() end
    return false
end)
