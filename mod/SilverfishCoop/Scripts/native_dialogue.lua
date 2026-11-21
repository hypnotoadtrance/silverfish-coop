local dialogue = {}
local guest_save = require("guest_save")
local mosby = require("native_dialogue_mosby")
local shopkeep = require("native_dialogue_shopkeep")
local mama = require("native_dialogue_mama")
local sewer = require("native_dialogue_sewer")
local shop_view = require("native_shop_view")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local world_path = observatory .. ".Observitory_P"
local level = world_path .. ":PersistentLevel."
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local widget_path = "/Game/Classes/GUI/SubWidgets/SimpleText_01_Widget.SimpleText_01_Widget_C"
local root, story = "Cast_Root", "Cast_Origin"
local castaway = {
    id = "castaway", title = "Castaway", map = observatory, world = world_path, root = root,
    npc = level .. "NPC_Castaway_C_1", npc_kind = "NPC_Castaway_C",
    npc_class = "/Game/Classes/NPCs/NPC_Characters/NPC_Castaway.NPC_Castaway_C",
    component = "Sphere_Interact", component_class = "/Script/Engine.SphereComponent",
    controller = level .. "Dialogue_Controler_BP_C_3", receiver = level .. "GlobalPing_ScriptingReciver_BP_C_3",
    source = "/Game/Classes/Libraries/Tables_Dialogue/Dialogue_Observatory.Dialogue_Observatory",
    row_order = {root, story},
    rows = {[root] = {lines = 1, choices = {1, 3}, next = {[1] = false, [3] = story}, count = 3},
        [story] = {lines = 3, choices = {1}, next = {[1] = root}, count = 1, continuation = true}},
}
local profiles = {[observatory] = castaway, [mosby.map] = mosby, [shopkeep.map] = shopkeep, [mama.map] = mama, [sewer.map] = sewer}

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
local activate = "Activate_Quests_22_323EA585468EFFDA9A12A188EBDF5022"
local ping_field = "GlobalPings_38_DB12D37348CDE946D7BA20BD3DE97CDD"
local choice_gates = {require_flag = "Flags_PreReq_26_50B864DB4BE3226CB3721BB63C4D74DE",
    hide_flag = "Flags_HideIfMet_28_6501CD9247D8EA6D695B5C9F5FD8C639"}
local effect_arrays = {"Add_JournalEntries_17_E77934214DFD7B85991F729C59678191",
    "Check_TurnIn_Quests_20_4B851D164A4A4164B7B74D80DE4EED23", ping_field}
local effect_maps = {add = "Add_SaveFlags_13_0FEB9B204B1F6F013B6309BF12311EF7",
    require_flag = "Flags_PreReq_26_50B864DB4BE3226CB3721BB63C4D74DE",
    hide_flag = "Flags_HideIfMet_28_6501CD9247D8EA6D695B5C9F5FD8C639", rep = "Rep_Effect_33_CA3AB131482DEE2637F973B7480AA04F"}

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
    local profile = profiles[state.map]
    if not enabled or not profile or state.listening ~= true or not valid(state.world)
        or state.world:GetFullName() ~= "World " .. profile.world then return end
    local npc = StaticFindObject(profile.npc)
    if not exact(npc, profile.npc_class) or npc:GetFullName() ~= profile.npc_kind .. " " .. profile.npc
        or not same(npc:GetWorld(), state.world) or npc:IsActorBeingDestroyed() or npc.Alive ~= true then return end
    local target = profile.target and StaticFindObject(profile.target) or npc
    if profile.target and (not exact(target, profile.target_class) or target:GetFullName() ~= profile.target_kind .. " " .. profile.target
        or not same(target:GetWorld(), state.world) or target:IsActorBeingDestroyed()) then return end
    local component = target[profile.component]
    if not exact(component, profile.component_class) or not same(component:GetOwner(), target) then return end
    local collision = component:GetCollisionEnabled()
    if collision ~= 1 and collision ~= 3 then return end
    return profile, npc, target, component
end

local function pointer_matches(pointer, source, row)
    if not pointer then return false end
    if not row then return not valid(pointer.DataTable) and none(pointer.RowName) end
    return same(pointer.DataTable, source) and named(pointer.RowName) == row
end

local function catalog(state, profile, progress)
    local controller = StaticFindObject(profile.controller)
    local source = StaticFindObject(profile.source)
    local schema = valid(source) and source:GetRowStruct()
    if not valid(source) or source:GetFullName() ~= "DataTable " .. profile.source or not valid(schema)
        or schema:GetFullName() ~= "UserDefinedStruct /Game/Classes/Libraries/Structs/Conversation_Struct.Conversation_Struct"
        or not exact(controller, "/Game/Classes/ScriptingActors/Dialogue_Controler_BP.Dialogue_Controler_BP_C")
        or controller:GetFullName() ~= "Dialogue_Controler_BP_C " .. profile.controller or not same(controller:GetWorld(), state.world)
        or not pointer_matches(controller.ConversationPointer, source, profile.root)
        or (profile.first_flag and named(controller.Flag_OnMeet) ~= profile.first_flag
            or not profile.first_flag and not none(controller.Flag_OnMeet))
        or not pointer_matches(controller.FirstMeet_Pointer, source, profile.first or false) then
        reject("stock_scope_unqualified")
    end
    if profile.volume then
        local trigger = StaticFindObject(profile.target)
        if not exact(trigger, profile.target_class) or trigger.TriggerOnce ~= false or count(trigger.ActorsToPower) ~= 1
            or not same(trigger.ActorsToPower[1], controller) then reject("stock_volume_unqualified") end
    end
    if profile.receiver then
        local receiver = StaticFindObject(profile.receiver)
        if not exact(receiver, "/Game/Classes/ScriptingActors/GlobalPing_ScriptingReciver_BP.GlobalPing_ScriptingReciver_BP_C")
            or receiver:GetFullName() ~= "GlobalPing_ScriptingReciver_BP_C " .. profile.receiver or not same(receiver:GetWorld(), state.world)
            or named(receiver.Ping_ID) ~= (profile.talk_ping or "Talk_Castaway") or count(receiver.ActorsToPower) ~= 1
            or not same(receiver.ActorsToPower[1], controller) then reject("stock_receiver_unqualified") end
    end
    local result = {}
    for _, name in ipairs(profile.row_order) do
        local spec, row = profile.rows[name], source:FindRow(name)
        local lines, choices = row and row[npc_lines], row and row[responses]
        if count(lines) ~= spec.lines or count(choices) ~= spec.count then reject("stock_row_unqualified:" .. name) end
        local found = {lines = {}, choices = {}}
        for index = 1, spec.lines do
            local line = lines[index]
            local inherited_speaker = spec.continuation == true and index > 1
            if not line or line[ignore_speaker] ~= inherited_speaker or count(line[give]) ~= 0
                or not pointer_matches(line[fallback], source, false) then
                reject("stock_line_effects_unqualified:" .. name .. ":" .. tostring(index))
            end
            local target = line[speaker]
            if (inherited_speaker and valid(target)) or (not inherited_speaker
                and (not valid(target) or target:GetFullName() ~= "BlueprintGeneratedClass " .. profile.npc_class)) then
                reject("stock_line_effects_unqualified")
            end
            found.lines[index] = text(line[line_text])
        end
        for _, ordinal in ipairs(spec.choices) do
            local choice, following = choices[ordinal], spec.next[ordinal]
            local effect = profile.effects and profile.effects[name] and profile.effects[name][ordinal] or {}
            local pointer = spec.pointer and spec.pointer[ordinal] or following
            if not choice or choice[exit_field] ~= (not following)
                or not pointer_matches(choice[next_pointer], source, pointer) then reject("stock_choice_unqualified:" .. name) end
            for _, field in ipairs(effect_arrays) do
                local ping = field == ping_field and effect.ping
                local values = choice[field]
                if count(values) ~= (ping and 1 or 0) or ping and named(values[1]) ~= ping then
                    reject("stock_choice_effects_unqualified")
                end
            end
            for kind, field in pairs(effect_maps) do
                local values, expected = choice[field], effect[kind]
                if not values or #values ~= (expected and 1 or 0) then reject("stock_choice_effects_unqualified:" .. name .. ":" .. kind) end
                if expected then
                    local key = FName(expected, EFindName.FNAME_Find)
                    if key:GetComparisonIndex() == 0 or key:ToString() ~= expected
                        or not values:Contains(key) or values:Find(key):get() ~= 1 then reject("stock_choice_flag_unqualified:" .. name) end
                end
            end
            local quests = choice[activate]
            if count(quests) ~= (effect.quest and 1 or 0) then reject("stock_choice_quest_unqualified:" .. name) end
            if effect.quest and not pointer_matches(quests[1], profile.table_and_key(effect.quest), effect.quest) then
                reject("stock_choice_quest_unqualified:" .. name)
            end
            if profile == mosby then
                for key, field in pairs(choice_gates) do
                    local values, expected = choice[field], effect[key]
                    if not values or #values ~= (expected and 1 or 0) then reject("stock_choice_gate_unqualified:" .. name) end
                    if expected then
                        local flag = FName(expected, EFindName.FNAME_Find)
                        if flag:GetComparisonIndex() == 0 or flag:ToString() ~= expected
                            or not values:Contains(flag) or values:Find(flag):get() ~= 1 then
                            reject("stock_choice_gate_unqualified:" .. name)
                        end
                    end
                end
            end
            if not profile.available or profile.available(effect, progress) then found.choices[ordinal] = text(choice[choice_text]) end
        end
        result[name] = found
    end
    return result, controller
end

local function reach(pawn, target, component, profile)
    if profile.volume then return component:IsOverlappingActor(pawn) == true end
    local limit, origin = pawn.InteractionRange, pawn:K2_GetActorLocation()
    if type(limit) ~= "number" or not (limit > 0 and limit <= 1000) then return false end
    for _, point in ipairs({target:K2_GetActorLocation(), component:K2_GetComponentLocation()}) do
        local distance = (point.X - origin.X)^2 + (point.Y - origin.Y)^2 + (point.Z - origin.Z)^2
        if not (distance <= limit ^ 2) then return false end
    end
    return true
end

local function guest_context(state, phase, name)
    local profile, npc, target, component = placement(state)
    if phase ~= "hosting" or state.server ~= false or not npc or not npc:HasAuthority() or not players
        or type(name) ~= "string" or #name > 512 then reject("host_guest_scope_unavailable") end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    local controller = valid(pawn) and pawn.Controller
    local connection = valid(controller) and controller.Player
    if not exact(pawn, pawn_class) or pawn:GetFullName() ~= name or pawn.Dead ~= false or not pawn:HasAuthority()
        or not target:HasAuthority() or not valid(connection) or not same(connection.Driver, state.world.NetDriver) or not same(pawn.Save, save)
        or not players.is_standing_guest(pawn, name) then reject("guest_identity_unavailable") end
    return pawn, profile, npc, target, component, connection
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
    local profile, npc, target, component = placement(state)
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not events or phase ~= "connected" or state.server ~= true or not npc or not exact(pawn, pawn_class)
        or not valid(controller) or not controller:IsLocalController() or not pawn:IsLocallyControlled()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), state.world)
        or detail.name ~= pawn:GetFullName() then return end
    return {world = state.world, pawn = pawn, controller = controller, npc = npc, target = target, component = component,
        name = detail.name, profile = profile}
end

local function same_scope(first, second)
    return first and second and same(first.world, second.world) and same(first.pawn, second.pawn)
        and first.profile == second.profile and same(first.controller, second.controller) and same(first.npc, second.npc)
        and same(first.target, second.target) and same(first.component, second.component)
end

local function fresh()
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local raw = file:read(32)
    file:close()
    local stamp, now = tonumber(raw), os.time()
    return stamp and stamp <= now and now <= stamp + 2
end

local function can_use(current, pending_response)
    local stack = current.controller.MenuStack
    return current.controller.Loading == false and stack and stack:GetArrayNum() == 0 and current.pawn.Dead == false
        and players.is_downed(current.pawn) == false and (pending_response or fresh())
        and reach(current.pawn, current.target, current.component, current.profile)
end

local function hide()
    if valid(widget) then widget:RemoveFromParent() end
    widget, last_text = nil, nil
end

function dialogue.clear()
    hide()
    mama.clear()
    if binding and type(SilverfishClearNativeDialogueInputScope) == "function" then SilverfishClearNativeDialogueInputScope() end
    binding, session, host_report = nil, nil, nil
    connections = {}
    selected, move = 1, 0
end

local function show(current)
    if headless then return end
    if not session or not session.content then hide(); return end
    local lines = {current.profile.title, ""}
    for _, line in ipairs(session.content.lines) do lines[#lines + 1] = line end
    lines[#lines + 1] = ""
    local choices = session.content.ordinals
    local first = session.trade and math.max(1, math.min(selected - 3, #choices - 6)) or 1
    local last = session.trade and math.min(#choices, first + 6) or #choices
    for index = first, last do
        local ordinal = choices[index]
        lines[#lines + 1] = (index == selected and "> " or "   ") .. session.content.choices[ordinal]
    end
    local answer = session.trade and "Press Interact to confirm."
        or current.profile.volume and "Press Interact to answer."
        or "Face " .. current.profile.title .. " and press Interact to answer."
    lines[#lines + 1] = (#choices > 1 and "Up / Down to select. " or "") .. answer
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
        widget.TextBlock_Notes:SetAutoWrapText(true)
    end
    widget.TextBlock_Notes:SetText(FText(display))
    if widget.TextBlock_Notes:GetText():ToString() ~= display then reject("widget_readback_unverified") end
    last_text = display
end

local function drain(current)
    local sequence = SilverfishTakeNativeDialogueInput(current.pawn:GetAddress())
    if not math.tointeger(sequence) or sequence < 1 or sequence >= 2^53 or (session and session.pending) then return false end
    local token, choice = "-", "-"
    if session and session.content then token, choice = session.token, session.content.ordinals[selected] end
    local file = io.open(events, "ab")
    if not file then return false end
    local written = file:write(table.concat({"dialogue-input", sequence, current.name, token, choice}, "\t"), "\n")
    local closed = file:close()
    if not written or not closed then return false end
    session = session or {}
    session.sequence, session.pending, session.token, session.expires = sequence, true, token, os.time() + 25
    return true
end

function dialogue.sync(state, phase)
    local current = local_context(state, phase)
    if not current then if binding then dialogue.clear() end; return end
    if not same_scope(binding, current) then dialogue.clear(); binding = current end
    local standing, now = can_use(current, true), os.time()
    local allow = standing and fresh() and not shop_view.reception_open(current.pawn)
    if session and (not standing or session.expires <= now
        or not current.profile.volume and not allow and not session.pending
        and not (session.response_grace and now <= session.response_grace)) then
        session = nil; hide()
    end
    binding.armed = false
    if type(SilverfishSetNativeDialogueInputScope) ~= "function" or type(SilverfishTakeNativeDialogueInput) ~= "function"
        or SilverfishSetNativeDialogueInputScope(current.pawn:GetAddress(), allow == true,
            current.profile.volume == true and session ~= nil and session.content ~= nil and not session.pending) ~= true then
        session = nil; hide(); return
    end
    binding.armed = allow
    if allow and session and session.content and not session.pending and move ~= 0 then
        selected = ((selected - 1 + move) % #session.content.ordinals) + 1
        if not pcall(show, current) then hide() end
    end
    move = 0
    if current.profile.volume and not reach(current.pawn, current.target, current.component, current.profile) then
        binding.entered = nil
    end
    if allow then
        if current.profile.volume and not binding.entered and not session
            and type(SilverfishOpenNativeSewerDialogue) == "function" then
            local queued = SilverfishOpenNativeSewerDialogue(current.pawn:GetAddress())
            if not queued then return end
            binding.entered = drain(current) or nil
        else
            drain(current)
        end
    end
end

local function host_command(fields, state, phase)
    host_report = nil
    local action, name = fields[2], fields[3]
    local commit = action == "native-dialogue-commit"
    local readings_open = action == "native-dialogue-mosby-readings-open"
    local mama_open = action == "native-dialogue-mama-open"
    local sewer_open = action == "native-dialogue-sewer-open"
    local shop_open = action == "native-dialogue-shopkeep-open" or mama_open or sewer_open
    local shop_cash = action == "native-dialogue-shopkeep-cash"
    local mama_trade = action == "native-dialogue-mama-trade"
    local sewer_trade = action == "native-dialogue-sewer-trade"
    if (commit and #fields ~= 12 and #fields ~= 16)
        or (not commit and #fields ~= (readings_open and 14 or mama_open and 8 or shop_open and 7
            or (shop_cash or mama_trade or sewer_trade) and 6 or 3)) then
        reject("arguments_unqualified")
    end
    local pawn, profile, npc, target, component, connection = guest_context(state, phase, name)
    if (action == "native-dialogue-shopkeep-open" or shop_cash) and profile ~= shopkeep then reject("shopkeep_scope_unavailable") end
    if (action == "native-dialogue-mama-open" or mama_trade) and profile ~= mama then reject("mama_scope_unavailable") end
    if (sewer_open or sewer_trade) and profile ~= sewer then reject("sewer_scope_unavailable") end
    if readings_open and profile ~= mosby then reject("readings_scope_unavailable") end
    if action == "native-dialogue-approach" then
        if not (headless and fixtures) then reject("approach_unavailable") end
        local location, floor = target:K2_GetActorLocation(), npc:K2_GetActorLocation().Z
        local offsets = profile.fixture_offsets or profile == mosby and {{-115, 0}, {-115, 95}, {-115, -95}, {0, 145}, {0, -145}, {115, 0}}
            or {{115, 0}, {-115, 0}, {0, 115}, {0, -115}}
        for _, offset in ipairs(offsets) do
            if pawn:K2_TeleportTo({X = location.X + offset[1], Y = location.Y + offset[2], Z = floor + 20},
                                  pawn:K2_GetActorRotation()) and reach(pawn, target, component, profile) then
                pawn:ForceNetUpdate()
                return "exact " .. profile.title .. " approach verified"
            end
        end
        reject("approach_blocked")
    end
    if not reach(pawn, target, component, profile) then reject("guest_outside_stock_reach") end
    local identity = connection_identity(connection, state.world.NetDriver)
    local progress, context, effect_name = nil, nil, "read"
    if profile.progress then progress, context = profile.progress(state) end
    local rows, controller = catalog(state, profile, progress)
    if commit then
        local row, ordinal = fields[4], math.tointeger(tonumber(fields[5]))
        local effect = profile.effects and profile.effects[row] and profile.effects[row][ordinal]
        if not effect or not effect.effect or not rows[row].choices[ordinal] or identity ~= fields[6]
            or effect.prototype and #fields ~= 16
            or not profile.accepts_progress_fields(progress, fields, 7) then reject("commit_scope_changed") end
        progress = profile.commit(context, effect, progress)
        local checked, updated = pcall(catalog, state, profile, progress)
        if not checked then error("mutation_unverified_native_dialogue_catalog:" .. tostring(updated), 0) end
        rows, effect_name = updated, effect.effect
    elseif readings_open then
        if identity ~= fields[4] or not profile.accepts_progress_fields(progress, fields, 5) then
            reject("readings_open_scope_changed")
        end
        progress = profile.open_readings(state, context, progress)
        local checked, updated = pcall(catalog, state, profile, progress)
        if not checked then error("mutation_unverified_native_dialogue_catalog:" .. tostring(updated), 0) end
        rows, effect_name = updated, "readings_opened"
    elseif shop_open then
        if identity ~= fields[4] or tostring(progress.meet) ~= fields[5] or tostring(progress.cash) ~= fields[6]
            or tostring(progress.xp) ~= fields[7] or mama_open and tostring(progress.rev_orders) ~= fields[8] then
            reject(profile.id .. "_open_scope_changed")
        end
        progress, effect_name = profile.open(context, progress)
    elseif shop_cash or mama_trade or sewer_trade then
        local row, ordinal = fields[4], math.tointeger(tonumber(fields[5]))
        local effect = profile.effects[row] and profile.effects[row][ordinal]
        if not effect or not effect.ping or not rows[row].choices[ordinal] or identity ~= fields[6] then
            reject(profile.id .. "_trade_scope_changed")
        end
        if shop_cash then
            profile.grant_cash(state, phase, pawn, connection, context)
            effect_name = "cash-granted"
        else
            profile.grant(state, phase, pawn, connection, context)
            effect_name = "trade-granted"
        end
    end
    host_report = {profile = profile, world = state.world, pawn = pawn, name = name, npc = npc, target = target,
        controller = controller, rows = rows, connection = identity, progress = progress, effect = effect_name}
    return "exact stock " .. profile.title .. " dialogue verified"
end

local function stock_press(current)
    local pawn, component = current.pawn, current.component
    local camera = pawn.Camera
    if not exact(camera, "/Script/Engine.CameraComponent") or not same(camera:GetOwner(), pawn) then
        reject("fixture_camera_unavailable")
    end
    local original = camera.RelativeRotation
    original = {Pitch = original.Pitch, Yaw = original.Yaw, Roll = original.Roll}
    local eye, center = camera:K2_GetComponentLocation(), component:K2_GetComponentLocation()
    local radius = current.profile.component == "Box" and component:GetScaledBoxExtent().Z or component:GetScaledSphereRadius()
    if type(radius) ~= "number" or not (radius > 0 and radius <= 200) then reject("fixture_component_unqualified") end
    local pressed, failure = pcall(function()
        for _, lift in ipairs({0, 0.4, -0.4, 0.75}) do
            local x, y, z = center.X - eye.X, center.Y - eye.Y, center.Z - eye.Z + radius * lift
            local horizontal = math.sqrt(x^2 + y^2)
            if horizontal > 1 then
                camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, horizontal)),
                    Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
                pawn:LookTrace()
                if pawn.CanInteract == true and same(pawn.InteractionTarget_Component, component) then
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
    if action == "native-dialogue-sewer-host-fixture" then return sewer.host_fixture(fields, state, phase) end
    if action == "native-dialogue-read" or action == "native-dialogue-approach" or action == "native-dialogue-commit"
        or action == "native-dialogue-mosby-readings-open"
        or action == "native-dialogue-shopkeep-open" or action == "native-dialogue-shopkeep-cash"
        or action == "native-dialogue-mama-open" or action == "native-dialogue-mama-trade"
        or action == "native-dialogue-sewer-open" or action == "native-dialogue-sewer-trade" then
        return host_command(fields, state, phase)
    end
    local handoff = action == "native-dialogue-shopkeep-view"
    local trade_view = action == "native-dialogue-sewer-offers"
    if action ~= "native-dialogue-render" and action ~= "native-dialogue-stock-fixture" and not handoff and not trade_view then return end
    local current = local_context(state, phase)
    if not same_scope(current, binding) or not can_use(current, action == "native-dialogue-render" or handoff or trade_view) then
        reject("client_scope_unavailable")
    end
    if trade_view then
        local sequence, token = tonumber(fields[3]), fields[4]
        local active = session and session.content and session.content.ordinals[selected]
        if current.profile ~= sewer or not session or not session.pending or session.sequence ~= sequence
            or session.expires <= os.time() or not active or active == 0 or not (session.trade or active == 1)
            or type(token) ~= "string" or #token ~= 32 or not token:match("^[0-9a-f]+$")
            or #fields < 6 or #fields > 58 or #fields % 2 ~= 0 then reject("sewer_trade_view_unqualified") end
        local content = {lines = {}, choices = {}, ordinals = {}}
        local previous, selection = 0, 1
        for index = 5, #fields, 2 do
            local raw = fields[index]
            local ordinal = math.tointeger(tonumber(raw))
            if not ordinal or tostring(ordinal) ~= raw or (ordinal == 0 and index ~= #fields - 1)
                or (ordinal ~= 0 and (ordinal <= previous or ordinal > 26)) then reject("sewer_trade_offer_unqualified") end
            previous = ordinal
            content.ordinals[#content.ordinals + 1] = ordinal
            content.choices[ordinal] = text(fields[index + 1])
            if session.trade and ordinal == active then selection = #content.ordinals end
        end
        if previous ~= 0 then reject("sewer_trade_close_unavailable") end
        local now = os.time()
        session = {sequence = sequence, row = "Sewer_Trade", token = token, expires = now + 30,
            response_grace = now + 2, trade = true, content = content}
        selected = selection
        show(current)
        return "exact Sewer Thing trade offers shown"
    end
    if handoff then
        local sequence = math.tointeger(tonumber(fields[3]))
        local effect = session and session.content and shopkeep.effects[session.row]
        effect = effect and effect[session.content.ordinals[selected]]
        if current.profile ~= shopkeep or #fields ~= 4 or not session or not session.pending
            or not effect or not effect.ping or session.sequence ~= sequence or session.token ~= fields[4]
            or session.expires <= os.time() or not shop_view.reception_pending(state, phase, sequence) then
            reject("shopkeep_client_handoff_unavailable")
        end
        session = nil
        hide()
        return "exact ShopKeep dialogue opened the local merchant view"
    end
    if action == "native-dialogue-stock-fixture" then
        local target = fields[3]
        if not headless or #fields ~= 3 or type(SilverfishProbeNativeInteractInput) ~= "function"
            or type(SilverfishSetNativeDialogueInputScope) ~= "function"
            or SilverfishSetNativeDialogueInputScope(current.pawn:GetAddress(), true, current.profile.volume == true) ~= true then
            reject("fixture_unavailable")
        end
        if target == "open" then
            if session and (session.pending or session.content) then reject("fixture_choice_unavailable") end
        else
            if not session or not session.content or session.pending then reject("fixture_choice_unavailable") end
            local index
            for i, ordinal in ipairs(session.content.ordinals) do if tostring(ordinal) == target then index = i end end
            if not index then reject("fixture_choice_unavailable") end
            selected = index
        end
        if current.profile.volume then
            local accepted, status = SilverfishProbeNativeInteractInput(current.pawn:GetAddress(), true)
            if accepted ~= true or status ~= "stock_started_consumed" or not drain(current) then
                reject("fixture_volume_answer_unverified:" .. tostring(status))
            end
        else
            stock_press(current)
        end
        return "exact stock " .. current.profile.title .. " Interact verified"
    end

    local sequence, row, token = tonumber(fields[3]), fields[4], fields[5]
    if not math.tointeger(sequence) or not session or not session.pending
        or session.sequence ~= sequence or session.expires <= os.time() then reject("render_expired") end
    if row == "refused" and token == "-" and #fields == 5 then
        session = nil
        hide()
        return "exact pending dialogue response dismissed"
    end
    local profile, following = current.profile, current.profile.root
    if session.trade then
        if row ~= "-" or session.content.ordinals[selected] ~= 0 then reject("sewer_trade_close_unavailable") end
        following = false
    elseif session.content then
        following = profile.rows[session.row].next[session.content.ordinals[selected]]
    end
    if not session.content and profile.first and row == profile.first then following = profile.first end
    if not session.content and profile == mosby and row == profile.readings then following = profile.readings end
    local content
    if row == "-" then
        if following ~= false or token ~= "-" or #fields ~= 5 then reject("render_unqualified") end
    else
        local spec = profile.rows[row]
        if row ~= following or not spec or not token or #token ~= 32 or not token:match("^[0-9a-f]+$") then reject("render_unqualified") end
        local ordinals, start = spec.choices, 5
        if profile == mosby then
            local encoded, previous = fields[6], 0
            if not encoded or #encoded > 15 then reject("render_choices_unqualified") end
            ordinals, start = {}, 6
            for raw in (encoded .. ","):gmatch("([^,]*),") do
                local ordinal = math.tointeger(tonumber(raw))
                if not ordinal or tostring(ordinal) ~= raw or ordinal <= previous or spec.next[ordinal] == nil then
                    reject("render_choices_unqualified")
                end
                ordinals[#ordinals + 1], previous = ordinal, ordinal
            end
        end
        if #fields ~= start + spec.lines + #ordinals or #ordinals < 1 then reject("render_unqualified") end
        content = {lines = {}, choices = {}, ordinals = ordinals}
        for index = 1, spec.lines do content.lines[index] = text(fields[start + index]) end
        for index, ordinal in ipairs(ordinals) do content.choices[ordinal] = text(fields[start + spec.lines + index]) end
    end
    local now = os.time()
    session = {sequence = sequence, row = row, token = token, expires = now + 30, response_grace = now + 2, content = content}
    selected = 1
    show(current)
    return "exact " .. profile.title .. " dialogue shown"
end

function dialogue.rows(state, phase, status_row)
    local result = {}
    if host_report and phase == "hosting" and state.map == host_report.profile.map and same(host_report.world, state.world)
        and valid(host_report.pawn) and valid(host_report.target) and valid(host_report.npc) and valid(host_report.controller) then
        local profile = host_report.profile
        result[1] = status_row("native-dialogue-stock", profile.map, host_report.npc:GetFullName(), host_report.controller:GetFullName(),
            host_report.name, host_report.connection)
        if host_report.progress then
            result[#result + 1] = profile.progress_row(host_report.name, host_report.progress, host_report.effect, status_row)
        end
        for _, name in ipairs(profile.row_order) do
            local row = host_report.rows[name]
            for index, line in ipairs(row.lines) do result[#result + 1] = status_row("native-dialogue-line", name, index, line) end
            for _, ordinal in ipairs(profile.rows[name].choices) do
                if row.choices[ordinal] then result[#result + 1] = status_row("native-dialogue-choice", name, ordinal, row.choices[ordinal]) end
            end
        end
    elseif binding and phase == "connected" and state.map == binding.profile.map and same(binding.world, state.world)
        and state.local_pawn and same(state.local_pawn.pawn, binding.pawn) then
        local status = session and (session.pending and "pending" or session.content and "open" or "closed")
            or binding.armed and "armed" or "waiting"
        result[1] = status_row("native-dialogue-view", binding.name, status, session and session.sequence or 0,
            session and session.row or "-", session and session.token or "-")
    end
    for _, row in ipairs(sewer.host_rows(state, phase, status_row)) do result[#result + 1] = row end
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
