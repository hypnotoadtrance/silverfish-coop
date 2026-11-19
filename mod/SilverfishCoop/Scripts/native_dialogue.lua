local dialogue = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = map .. ".Observitory_P"
local level = world_path .. ":PersistentLevel."
local npc_path = level .. "NPC_Castaway_C_1"
local controller_path = level .. "Dialogue_Controler_BP_C_3"
local receiver_path = level .. "GlobalPing_ScriptingReciver_BP_C_3"
local table_path = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Observatory.Dialogue_Observatory"
local npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Castaway.NPC_Castaway_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local root, story = "Cast_Root", "Cast_Origin"
local profiles = {[root] = {lines = 1, choices = {1, 3}, next = {[1] = false, [3] = story}, count = 3},
    [story] = {lines = 3, choices = {1}, next = {[1] = root}, count = 1}}

local npc_lines = "NPC_Dialogue_3_CE4B2EF741973F79EF3C89BB03B96687"
local responses = "PlayerResponces_6_DA452ED342AF8EEEC7BD299C134AF0F3"
local line_text = "TextContent_5_E96526B44216FB838D11F08D14CD50BA"
local speaker = "Target_Speaker_28_B9614ED64813B7DA696AB3AD3BE16DC4"
local ignore_speaker = "Ignore_TargetClass_11_6074F1FD435E9273F25B45B157A03ECE"
local give = "GivePlayer_Items_32_3E8D37BB404111364FC9F69D591F5264"
local fallback = "FallbackPointer_24_9DB7DB6F4078EDB4D209A5B9751FB7A1"
local choice_text = "TextContent_2_891360234FC305CA4D09FE9EA69C8936"
local exit_field = "Terminate_Convo_5_BAA128604982D7FC877B498C6500026C"
local next_pointer = "NextConvo_8_59FA977C44DA6739371CD9992F385F4F"
local effect_arrays = {"Add_JournalEntries_17_E77934214DFD7B85991F729C59678191",
    "Check_TurnIn_Quests_20_4B851D164A4A4164B7B74D80DE4EED23", "Activate_Quests_22_323EA585468EFFDA9A12A188EBDF5022",
    "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD"}
local effect_maps = {"Add_SaveFlags_13_0FEB9B204B1F6F013B6309BF12311EF7", "Flags_PreReq_26_50B864DB4BE3226CB3721BB63C4D74DE",
    "Flags_HideIfMet_28_6501CD9247D8EA6D695B5C9F5FD8C639", "Rep_Effect_33_CA3AB131482DEE2637F973B7480AA04F"}

local events, heartbeat, players, host_report
local binding, session, widget, last_text
local selected, move = 1, 0
local connections, connection_serial = {}, 0

local function valid(value) return value ~= nil and value ~= false and value:IsValid() end
local function same(first, second) return valid(first) and valid(second) and first:GetAddress() == second:GetAddress() end
local function exact(value, path) return valid(value) and same(value:GetClass(), StaticFindObject(path)) end
local function count(array) return array and array:GetArrayNum() end
local function reject(reason) error("native_dialogue_" .. reason, 0) end
local function named(value) return type(value) == "userdata" and value:ToString() or value end
local function none(value) return named(value) == "None" or named(value) == "" end

local function text(value)
    local result = named(value)
    if type(result) ~= "string" or #result < 1 or #result > 1024 then reject("stock_text_unavailable") end
    return (result:gsub("[\t\r\n]+", " "))
end

local function placement(state)
    if not enabled or state.map ~= map or state.listening ~= true or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. world_path then return end
    local npc = StaticFindObject(npc_path)
    if not exact(npc, npc_class) or npc:GetFullName() ~= "NPC_Castaway_C " .. npc_path
        or not same(npc:GetWorld(), state.world) or npc:IsActorBeingDestroyed() or npc.Alive ~= true then return end
    local sphere = npc.Sphere_Interact
    if not exact(sphere, "/Script/Engine.SphereComponent") or not same(sphere:GetOwner(), npc) then return end
    local collision = sphere:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return end
    return npc, sphere
end

local function pointer_matches(pointer, source, row)
    if not pointer then return false end
    if not row then return not valid(pointer.DataTable) and none(pointer.RowName) end
    return same(pointer.DataTable, source) and named(pointer.RowName) == row
end

local function catalog(state)
    local controller = StaticFindObject(controller_path)
    local receiver = StaticFindObject(receiver_path)
    local source = StaticFindObject(table_path)
    local schema = valid(source) and source:GetRowStruct()
    if not valid(source) or source:GetFullName() ~= "DataTable " .. table_path or not valid(schema)
        or schema:GetFullName() ~= "UserDefinedStruct /Game/Classes/Libraries/Structs/Conversation_Struct.Conversation_Struct"
        or not exact(controller, "/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C")
        or controller:GetFullName() ~= "Dialogue_Controler_BP_C " .. controller_path or not same(controller:GetWorld(), state.world)
        or not pointer_matches(controller.ConversationPointer, source, root) or not none(controller.Flag_OnMeet)
        or not pointer_matches(controller.FirstMeet_Pointer, source, false)
        or not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C")
        or receiver:GetFullName() ~= "GlobalPing_ScriptingReciver_BP_C " .. receiver_path or not same(receiver:GetWorld(), state.world)
        or named(receiver.Ping_ID) ~= "Talk_Castaway" or count(receiver.ActorsToPower) ~= 1
        or not same(receiver.ActorsToPower[1], controller) then reject("stock_scope_unqualified") end
    local result = {}
    for _, name in ipairs({root, story}) do
        local profile, row = profiles[name], source:FindRow(name)
        local lines, choices = row and row[npc_lines], row and row[responses]
        if count(lines) ~= profile.lines or count(choices) ~= profile.count then reject("stock_row_unqualified") end
        local found = {lines = {}, choices = {}}
        for index = 1, profile.lines do
            local line = lines[index]
            local inherited_speaker = name == story and index > 1
            if not line or line[ignore_speaker] ~= inherited_speaker or count(line[give]) ~= 0
                or not pointer_matches(line[fallback], source, false) then
                reject("stock_line_effects_unqualified")
            end
            local target = line[speaker]
            if (inherited_speaker and valid(target)) or (not inherited_speaker
                and (not valid(target) or target:GetFullName() ~= "BlueprintGeneratedClass " .. npc_class)) then
                reject("stock_line_effects_unqualified")
            end
            found.lines[index] = text(line[line_text])
        end
        for _, ordinal in ipairs(profile.choices) do
            local choice, following = choices[ordinal], profile.next[ordinal]
            if not choice or choice[exit_field] ~= (not following)
                or not pointer_matches(choice[next_pointer], source, following) then reject("stock_choice_unqualified") end
            for _, field in ipairs(effect_arrays) do
                if count(choice[field]) ~= 0 then reject("stock_choice_effects_unqualified") end
            end
            for _, field in ipairs(effect_maps) do
                if not choice[field] or #choice[field] ~= 0 then reject("stock_choice_effects_unqualified") end
            end
            found.choices[ordinal] = text(choice[choice_text])
        end
        result[name] = found
    end
    return result, controller
end

local function reach(pawn, npc, sphere)
    local limit, origin = pawn.InteractionRange, pawn:K2_GetActorLocation()
    if type(limit) ~= "number" or not (limit > 0 and limit <= 1000) then return false end
    for _, point in ipairs({npc:K2_GetActorLocation(), sphere:K2_GetComponentLocation()}) do
        local distance = (point.X - origin.X)^2 + (point.Y - origin.Y)^2 + (point.Z - origin.Z)^2
        if not (distance <= limit ^ 2) then return false end
    end
    return true
end

local function guest_context(state, phase, name)
    local npc, sphere = placement(state)
    if phase ~= "hosting" or state.server ~= false or not npc or not npc:HasAuthority() or not players
        or type(name) ~= "string" or #name > 512 then reject("host_guest_scope_unavailable") end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    local controller = valid(pawn) and pawn.Controller
    local connection = valid(controller) and controller.Player
    if not exact(pawn, pawn_class) or pawn:GetFullName() ~= name or pawn.Dead ~= false or not pawn:HasAuthority()
        or not valid(connection) or not same(connection.Driver, state.world.NetDriver) or not same(pawn.Save, save)
        or not players.is_standing_guest(pawn, name) then reject("guest_identity_unavailable") end
    return pawn, npc, sphere, connection
end

local function connection_identity(connection, driver)
    for name, entry in pairs(connections) do
        if not valid(entry.object) or not same(entry.object.Driver, driver) then connections[name] = nil end
    end
    local name = connection:GetFullName()
    local entry = connections[name]
    if not entry or not same(entry.object, connection) then
        connection_serial = connection_serial + 1
        entry = {object = connection, identity = name .. "@" .. tostring(connection_serial)}
        connections[name] = entry
    end
    return entry.identity
end

local function local_context(state, phase)
    local npc, sphere = placement(state)
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not events or phase ~= "connected" or state.server ~= true or not npc or not exact(pawn, pawn_class)
        or not valid(controller) or not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), state.world)
        or detail.name ~= pawn:GetFullName() then return end
    return {world = state.world, pawn = pawn, controller = controller, npc = npc, sphere = sphere, name = detail.name}
end

local function same_scope(first, second)
    return first and second and same(first.world, second.world) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller) and same(first.npc, second.npc) and same(first.sphere, second.sphere)
end

local function fresh()
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local raw = file:read(32)
    file:close()
    local stamp, now = tonumber(raw), os.time()
    return stamp and stamp <= now and now <= stamp + 2
end

local function can_use(current)
    local stack = current.controller.MenuStack
    return current.controller.Loading == false and stack and stack:GetArrayNum() == 0 and current.pawn.Dead == false
        and players.is_downed(current.pawn) == false and fresh() and reach(current.pawn, current.npc, current.sphere)
end

local function hide()
    if valid(widget) then widget:RemoveFromParent() end
    widget, last_text = nil, nil
end

function dialogue.clear()
    hide()
    if binding and type(SilverfishClearNativeDialogueInputScope) == "function" then SilverfishClearNativeDialogueInputScope() end
    binding, session, host_report = nil, nil, nil
    connections = {}
    selected, move = 1, 0
end

local function show(current)
    if headless then return end
    if not session or not session.content then hide(); return end
    local lines = {"Castaway", ""}
    for _, line in ipairs(session.content.lines) do lines[#lines + 1] = line end
    lines[#lines + 1] = ""
    local choices = profiles[session.row].choices
    for index, ordinal in ipairs(choices) do
        lines[#lines + 1] = (index == selected and "> " or "   ") .. session.content.choices[ordinal]
    end
    lines[#lines + 1] = #choices > 1 and "Up / Down to select. Face Castaway and press Interact to answer."
        or "Face Castaway and press Interact to answer."
    local display = table.concat(lines, "\n")
    if last_text == display and valid(widget) then return end
    if not valid(widget) then
        local class = StaticFindObject(widget_path)
        if not valid(class) then LoadAsset(widget_path); class = StaticFindObject(widget_path) end
        local library = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        if not valid(class) or not valid(library) then reject("widget_class_unavailable") end
        widget = library:Create(current.pawn, class, current.controller)
        if not exact(widget, widget_path) or not same(widget:GetOwningPlayer(), current.controller)
            or not exact(widget.TextBlock_Notes, "/Script/UMG.TextBlock") then reject("widget_unqualified") end
        widget:SetVisibility(3)
        if widget:AddToPlayerScreen(31) ~= true or not widget:IsInViewport() then reject("widget_unavailable") end
        widget:SetDesiredSizeInViewport({X = 650, Y = 380})
        widget:SetPositionInViewport({X = 42, Y = 138}, false)
    end
    widget.TextBlock_Notes:SetText(FText(display))
    if widget.TextBlock_Notes:GetText():ToString() ~= display then reject("widget_readback_unverified") end
    last_text = display
end

local function drain(current)
    local sequence = SilverfishTakeNativeDialogueInput(current.pawn:GetAddress())
    if not math.tointeger(sequence) or sequence < 1 or sequence >= 2^53 or (session and session.pending) then return false end
    local token, choice = "-", "-"
    if session and session.content then token, choice = session.token, profiles[session.row].choices[selected] end
    local file = io.open(events, "ab")
    if not file then return false end
    local written = file:write(table.concat({"dialogue-input", sequence, current.name, token, choice}, "\t"), "\n")
    local closed = file:close()
    if not written or not closed then return false end
    session = session or {}
    session.sequence, session.pending, session.token, session.expires = sequence, true, token, os.time() + 10
    return true
end

function dialogue.sync(state, phase)
    local current = local_context(state, phase)
    if not current then if binding then dialogue.clear() end; return end
    if not same_scope(binding, current) then dialogue.clear(); binding = current end
    local allow = can_use(current)
    if session and (not allow or session.expires <= os.time()) then session = nil; hide() end
    binding.armed = false
    if type(SilverfishSetNativeDialogueInputScope) ~= "function" or type(SilverfishTakeNativeDialogueInput) ~= "function"
        or SilverfishSetNativeDialogueInputScope(current.pawn:GetAddress(), allow == true) ~= true then
        session = nil; hide(); return
    end
    binding.armed = allow
    if allow and session and session.content and not session.pending and move ~= 0 then
        selected = ((selected - 1 + move) % #profiles[session.row].choices) + 1
        if not pcall(show, current) then hide() end
    end
    move = 0
    if allow then drain(current) end
end

local function host_command(fields, state, phase)
    host_report = nil
    local action, name = fields[2], fields[3]
    if #fields ~= 3 then reject("arguments_unqualified") end
    local pawn, npc, sphere, connection = guest_context(state, phase, name)
    if action == "native-dialogue-approach" then
        if not (headless and fixtures) then reject("approach_unavailable") end
        local location = npc:K2_GetActorLocation()
        for _, offset in ipairs({{115, 0}, {-115, 0}, {0, 115}, {0, -115}}) do
            if pawn:K2_TeleportTo({X = location.X + offset[1], Y = location.Y + offset[2], Z = location.Z + 20},
                                  pawn:K2_GetActorRotation()) and reach(pawn, npc, sphere) then
                pawn:ForceNetUpdate()
                return "exact Castaway approach verified"
            end
        end
        reject("approach_blocked")
    end
    if not reach(pawn, npc, sphere) then reject("guest_outside_stock_reach") end
    local rows, controller = catalog(state)
    host_report = {world = state.world, pawn = pawn, name = name, npc = npc, controller = controller, rows = rows,
        connection = connection_identity(connection, state.world.NetDriver)}
    return "exact stock Castaway dialogue read"
end

local function stock_press(current)
    local pawn, sphere = current.pawn, current.sphere
    local camera = pawn.Camera
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn) then
        reject("fixture_camera_unavailable")
    end
    local original = camera.RelativeRotation
    original = {Pitch = original.Pitch, Yaw = original.Yaw, Roll = original.Roll}
    local eye, center = camera:K2_GetComponentLocation(), sphere:K2_GetComponentLocation()
    local radius = sphere:GetScaledSphereRadius()
    if type(radius) ~= "number" or not (radius > 0 and radius <= 200) then reject("fixture_sphere_unqualified") end
    local pressed, failure = pcall(function()
        for _, lift in ipairs({0, 0.4, -0.4, 0.75}) do
            local x, y, z = center.X - eye.X, center.Y - eye.Y, center.Z - eye.Z + radius * lift
            local horizontal = math.sqrt(x^2 + y^2)
            if horizontal > 1 then
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, horizontal)),
                    Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, sphere) then
                    local accepted, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                    if accepted ~= true or reason ~= "stock_started_consumed" or not drain(current) then
                        reject("fixture_input_unverified:" .. tostring(reason))
                    end
                    return
                end
            end
        end
        reject("fixture_focus_unavailable")
    end)
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(pawn.Camera, camera)
        or not same(pawn:GetWorld(), current.world) then reject("fixture_camera_restore_unverified") end
    camera:K2_SetRelativeRotation(original, false, {}, true)
    pawn:LookTrace()
    local restored = camera.RelativeRotation
    for _, axis in ipairs({"Pitch", "Yaw", "Roll"}) do
        if math.abs((restored[axis] - original[axis] + 180) % 360 - 180) > 0.05 then
            reject("fixture_camera_restore_unverified")
        end
    end
    if not pressed then error(failure, 0) end
end

function dialogue.command(fields, state, phase)
    local action = fields[2]
    if action == "native-dialogue-read" or action == "native-dialogue-approach" then return host_command(fields, state, phase) end
    if action ~= "native-dialogue-render" and action ~= "native-dialogue-stock-fixture" then return end
    local current = local_context(state, phase)
    if not same_scope(current, binding) or not can_use(current) then reject("client_scope_unavailable") end
    if action == "native-dialogue-stock-fixture" then
        local target = fields[3]
        if not headless or #fields ~= 3 or type(SilverfishProbeNativeInteractInput) ~= "function"
            or type(SilverfishSetNativeDialogueInputScope) ~= "function"
            or SilverfishSetNativeDialogueInputScope(current.pawn:GetAddress(), true) ~= true then reject("fixture_unavailable") end
        if target == "open" then
            if session and (session.pending or session.content) then reject("fixture_choice_unavailable") end
        else
            if not session or not session.content or session.pending then reject("fixture_choice_unavailable") end
            local index
            for i, ordinal in ipairs(profiles[session.row].choices) do if tostring(ordinal) == target then index = i end end
            if not index then reject("fixture_choice_unavailable") end
            selected = index
        end
        stock_press(current)
        return "exact stock Castaway Interact verified"
    end

    local sequence, row, token = tonumber(fields[3]), fields[4], fields[5]
    if not math.tointeger(sequence) or not session or not session.pending
        or session.sequence ~= sequence or session.expires <= os.time() then reject("render_expired") end
    if row == "refused" and token == "-" and #fields == 5 then
        session = nil
        hide()
        return "exact Castaway pending response dismissed"
    end
    local following = root
    if session.content then following = profiles[session.row].next[profiles[session.row].choices[selected]] end
    local content
    if row == "-" then
        if following ~= false or token ~= "-" or #fields ~= 5 then reject("render_unqualified") end
    else
        local profile = profiles[row]
        if row ~= following or not profile or not token or #token ~= 32 or not token:match("^[0-9a-f]+$")
            or #fields ~= 5 + profile.lines + #profile.choices then reject("render_unqualified") end
        content = {lines = {}, choices = {}}
        for index = 1, profile.lines do content.lines[index] = text(fields[5 + index]) end
        for index, ordinal in ipairs(profile.choices) do content.choices[ordinal] = text(fields[5 + profile.lines + index]) end
    end
    session = {sequence = sequence, row = row, token = token, expires = os.time() + 30, content = content}
    selected = 1
    show(current)
    return "exact Castaway dialogue shown"
end

function dialogue.rows(state, phase, status_row)
    local result = {}
    if host_report and phase == "hosting" and state.map == map and same(host_report.world, state.world)
        and valid(host_report.pawn) and valid(host_report.npc) and valid(host_report.controller) then
        result[1] = status_row("native-dialogue-stock", map, host_report.npc:GetFullName(), host_report.controller:GetFullName(),
            host_report.name, host_report.connection)
        for _, name in ipairs({root, story}) do
            local row = host_report.rows[name]
            for index, line in ipairs(row.lines) do result[#result + 1] = status_row("native-dialogue-line", name, index, line) end
            for _, ordinal in ipairs(profiles[name].choices) do
                result[#result + 1] = status_row("native-dialogue-choice", name, ordinal, row.choices[ordinal])
            end
        end
    elseif binding and phase == "connected" and state.map == map and same(binding.world, state.world)
        and state.local_pawn and same(state.local_pawn.pawn, binding.pawn) then
        local status = session and (session.pending and "pending" or session.content and "open" or "closed")
            or binding.armed and "armed" or "waiting"
        result[1] = status_row("native-dialogue-view", binding.name, status, session and session.sequence or 0,
            session and session.row or "-", session and session.token or "-")
    end
    return result
end

function dialogue.start(bridge, scopes)
    if not enabled then return end
    events, heartbeat, players = bridge .. "/native-dialogue-events", bridge .. "/native-dialogue-heartbeat", scopes
    if not headless and type(RegisterKeyBind) == "function" and Key and Key.UP_ARROW and Key.DOWN_ARROW then
        RegisterKeyBind(Key.UP_ARROW, function() if session and session.content and not session.pending then move = move - 1 end end)
        RegisterKeyBind(Key.DOWN_ARROW, function() if session and session.content and not session.pending then move = move + 1 end end)
    end
end

return dialogue
