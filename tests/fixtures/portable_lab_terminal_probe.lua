local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1"
    or not mailbox or not mailbox:match("[/\\]portable%-title%-%x+[/\\][%a]+[/\\]titles[/\\]local[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local prefix = mailbox .. "/portable-lab-terminal-"
local map = "World /Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P"
local menu_class = "WidgetBlueprintGeneratedClass /Game/Classes/GUI/Menu_Terminal_Widget.Menu_Terminal_Widget_C"
local commit_event = "BndEvt__Menu_Terminal_Widget_EditableTextBox_0_K2Node_ComponentBoundEvent_1_OnEditableTextBoxCommittedEvent__DelegateSignature"
local last, terminal, command, confirmation

local function valid(value) return value and value:IsValid() end
local function same(a,b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function scene()
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    assert(valid(world) and world:GetFullName() == map, "stock Lab E206 is unavailable")
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local pawn = statics:GetPlayerPawn(world,0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and not pawn:HasAuthority(), "local connected pawn required")
    return world,pawn
end

local function menu(pawn)
    local stack = pawn.Controller.MenuStack
    local count = stack:GetArrayNum()
    local top = count > 0 and stack[count]
    assert(valid(top) and top:GetClass():GetFullName() == menu_class and same(top.TerminalRef, terminal),
        "stock terminal widget is not active")
    return top
end

local function run(action)
    local world,pawn = scene()
    if action == "open" then
        local candidates = {}
        for _, actor in ipairs(FindAllOf("Terminal_BP_C") or {}) do
            if valid(actor) and same(world, actor:GetWorld()) and actor.Locked_ByPassword == false and valid(actor.Box) then
                local binds = {}
                actor.Active_Actors:ForEach(function(key, value)
                    local target, label = key:get(), value:get():ToString()
                    if valid(target) and label:match("^[A-Za-z0-9_%-%s]+$") then
                        binds[#binds + 1] = label:upper()
                    end
                end)
                if #binds > 0 then
                    table.sort(binds)
                    candidates[#candidates+1] = {actor=actor, command=binds[1]}
                end
            end
        end
        assert(#candidates > 0, "no unlocked stock terminal linked to a function in Lab E206")
        table.sort(candidates, function(a,b) return a.actor:GetFullName() < b.actor:GetFullName() end)
        terminal, command = candidates[1].actor, candidates[1].command
        terminal:SendInteractionToActor(pawn, terminal.Box)
        local view = menu(pawn)
        return terminal:GetFullName() .. ";" .. view:GetFullName()
    end
    local view = menu(pawn)
    if action == "submit" then
        local before = terminal.SavedText:ToString()
        view[commit_event](view, FText(command), 1)
        local after = terminal.SavedText:ToString()
        local appended = after:sub(#before + 1):upper()
        assert(appended:find(command, 1, true)
            and (appended:find("-ACTIVATE", 1, true) or appended:find("-DEACTIVATE", 1, true)),
            "stock terminal did not acknowledge the linked function")
        confirmation = after
        return command .. ";" .. #before .. ";" .. #after
    end
    if action == "status" then
        assert(confirmation and terminal.SavedText:ToString() == confirmation, "stock terminal response did not persist")
        return "confirmed"
    end
    error("unknown Lab E206 fixture operation")
end

LoopInGameThreadWithDelay(50, function()
    local file = io.open(prefix .. "command.tsv","rb")
    if not file then return end
    local input = file:read(64)
    file:close()
    local ticket, action = input:match("^(%x+)\t([a-z]+)$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, result = pcall(run,action)
    local reply = assert(io.open(prefix .. "reply.tsv","wb"))
    local message = tostring(result):gsub("[\r\n\t]"," ")
    reply:write(ticket,"\t",ok and "ok" or "error","\t",message)
    reply:close()
end)
