local view = {}
local content_paths = require("content_paths")
local save_scope = require("native_save_scope")

view.memory_render = "native-stash-view-render-memory"
view.memory_header_limit = 1024
local small_memory_limit, full_page_limit = 20000, 2500000
local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local stash_path = "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C"
local screen_path = "/Game/Classes/GUI/New/StashScreen.StashScreen_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_base = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"
local item_weight = "Weight_12_11AB9ED547504E78A849A692A36DC938"
local item_value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"
local item_never_drop = "NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"
local current, downed, mailbox
local sequence = 0

function view.memory_body_limit()
    if type(SilverfishLocalBridgeStashBodyLimit) == "function"
        and SilverfishLocalBridgeStashBodyLimit() == full_page_limit then
        return full_page_limit
    end
    return small_memory_limit
end

local function valid(object)
    return object and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function entry(array, index)
    local value = array[index]
    if value and type(value.type) == "function" and value:type() == "RemoteUnrealParam" then return value:get() end
    return value
end

local function visible(controller, screen, top_only)
    local stack = controller.MenuStack
    local count = stack:GetArrayNum()
    for index = top_only and count or 1, count do
        if index > 0 and same(entry(stack, index), screen) then return true end
    end
    return false
end

local function report(state, phase)
    if phase ~= "connected" or not state.listening or state.server ~= true or not state.local_pawn
        or type(state.map) ~= "string" then return end
    local controller, pawn = state.controller, state.local_pawn.pawn
    if not valid(controller) or not valid(pawn) or controller.Loading ~= false or pawn.Dead ~= false
        or not controller:IsLocalController() or not pawn:IsLocallyControlled() or pawn:HasAuthority()
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), state.world)
        or type(downed) ~= "function" or downed(pawn) ~= false then return end
    return {controller = controller, pawn = pawn, world = state.world, map = state.map, name = state.local_pawn.name}
end

local function identifier(id)
    if not id then return end
    local a, b, c, d = math.tointeger(id.A), math.tointeger(id.B), math.tointeger(id.C), math.tointeger(id.D)
    if not a or not b or not c or not d or (a | b | c | d) == 0 then return end
    return string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
end

local function site_id(actor, binding)
    local base = StaticFindObject(stash_path)
    if not valid(base) or not valid(actor) or not actor:IsA(base) or not same(actor:GetWorld(), binding.world)
        or actor:IsActorBeingDestroyed() then return end
    local path, leaf = actor:GetFullName():match("^%S+ (/Game/%S+)$"), binding.map:match("([^/]+)$")
    local prefix = leaf and binding.map .. "." .. leaf .. ":PersistentLevel."
    if prefix and path and path:sub(1, #prefix) == prefix then return identifier(actor.Stash_ID) end
end

local function near(pawn, actor)
    local from, to, range = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation(), pawn.InteractionRange
    return from and to and type(range) == "number" and range > 0 and range <= 1000
        and (from.X - to.X)^2 + (from.Y - to.Y)^2 + (from.Z - to.Z)^2 <= range^2
end

function view.clear()
    local previous = current
    current = nil
    if previous and valid(previous.screen) then
        local controller = previous.binding.controller
        if valid(controller) and visible(controller, previous.screen, true) then controller:CloseMenu({}) end
    end
    if type(SilverfishClearNativeStashScreen) == "function" then SilverfishClearNativeStashScreen() end
end

local function queue(mode, item, before, amount, expected)
    sequence = sequence + 1
    current.sequence, current.mode, current.item, current.before = sequence, mode, item, before
    current.amount, current.expected = expected and amount or nil, expected
    current.state, current.expires = "pending", os.time() + 45
end

local function begin(binding, actor)
    local id = site_id(actor, binding)
    if not id or not near(binding.pawn, actor) or binding.controller.MenuStack:GetArrayNum() ~= 0
        or type(SilverfishSetNativeStashScreen) ~= "function" then return false end
    current = {binding = binding, actor = actor, id = id}
    queue("stash")
    return true
end

function view.sync(state, phase)
    local binding = current and report(state, phase)
    if current and (not binding or not same(binding.pawn, current.binding.pawn)
        or site_id(current.actor, binding) ~= current.id or not near(binding.pawn, current.actor)
        or current.screen and not visible(binding.controller, current.screen)
        or current.state == "pending" and os.time() >= current.expires) then
        view.clear()
    end
    if current and current.state == "rendered" and visible(binding.controller, current.screen, true) then
        local guid, deposit, _, amount, before, expected = SilverfishTakeNativeStashMove(current.screen:GetAddress())
        if guid then queue(deposit and "backpack" or "stash", guid, before, amount, expected) end
    end
    if phase ~= "connected" or not state.listening or state.server ~= true
        or type(SilverfishTakeNativeStashPress) ~= "function" then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(pawn) then return end
    -- The native read renews only its current local guest scope. Qualify the
    -- full UI binding when a stock press can actually open the screen.
    local press, path = SilverfishTakeNativeStashPress(pawn:GetAddress())
    if press and not current and type(path) == "string" then
        binding = binding or report(state, phase)
        if binding then begin(binding, StaticFindObject(path)) end
    end
end

local function item_row(line, seen)
    local guid, path, ammo, weight, value, protected = line:match(
        "^([0-9A-F]+)\t([^\t]+)\t(-?%d+)\t([^\t]+)\t(-?%d+)\t([01])$")
    ammo, weight, value = math.tointeger(tonumber(ammo)), tonumber(weight), math.tointeger(tonumber(value))
    if not guid or #guid ~= 32 or not guid:find("[^0]") or seen[guid] or not content_paths.item_class(path, 512)
        or not ammo or ammo < -2147483648 or ammo > 2147483647 or not weight or not (weight >= 0 and weight <= 100000)
        or not value or value < -2147483648 or value > 2147483647 then
        error("native_stash_view_invalid_item", 0)
    end
    local class = StaticFindObject(path)
    if not valid(class) then LoadAsset(path); class = StaticFindObject(path) end
    local stock = StaticFindObject(item_base)
    if not valid(class) or not valid(stock) or not class:GetCDO():IsA(stock) then
        error("native_stash_view_invalid_item_class", 0)
    end
    local row = {[item_class] = class, [item_ammo] = ammo, [item_weight] = weight, [item_value] = value,
        [item_never_drop] = protected == "1", [item_guid] = {A = tonumber(guid:sub(1, 8), 16),
            B = tonumber(guid:sub(9, 16), 16), C = tonumber(guid:sub(17, 24), 16), D = tonumber(guid:sub(25), 16)}}
    seen[guid] = row
    return row, guid
end

local function render(fields, state, phase, payload)
    local resident = fields[2] == view.memory_render
    local expected = math.tointeger(tonumber(fields[resident and 6 or 7]))
    if not expected or expected < 0 or expected > 4096 then error("native_stash_view_invalid_page", 0) end
    local raw
    if resident then
        if #fields ~= 6 or type(payload) ~= "string" or #payload > view.memory_body_limit() or payload:sub(1, 1) == "\n"
            or payload:find("[^\t\n -~]") or payload:find("\n\n", 1, true) then
            error("native_stash_view_invalid_page", 0)
        end
        raw = payload
    else
        local nonce = fields[6]
        if #fields ~= 7 or not nonce or #nonce ~= 32 or not nonce:match("^[0-9a-f]+$") then
            error("native_stash_view_invalid_page", 0)
        end
        local file = io.open(mailbox .. "/native-stash-screen-" .. nonce .. ".tsv", "rb")
        if not file then error("native_stash_view_missing_page", 0) end
        raw = file:read(full_page_limit + 1) or ""
        file:close()
    end
    if #raw > full_page_limit or (#raw > 0 and raw:sub(-1) ~= "\n") then error("native_stash_view_invalid_page", 0) end
    local api, save_class = StaticFindObject(statics_path), StaticFindObject(save_path)
    local save = save_scope.bind_guest(state, phase)
    if not valid(api) or not valid(save_class) or not valid(save) then error("native_stash_view_save_unavailable", 0) end
    local preview = api:CreateSaveGameObject(save_class)
    local rows, seen, count = preview.PlayerBackpack, {}, 0
    local projected_order = current.before and {}
    for line in raw:gmatch("([^\n]+)\n") do
        count = count + 1
        if count > expected then error("native_stash_view_invalid_page", 0) end
        local value, guid = item_row(line, seen)
        rows[count] = value
        if projected_order then projected_order[count] = guid end
    end
    if count ~= expected then error("native_stash_view_invalid_page", 0) end

    local binding, screen, original = current.binding, current.screen, nil
    if not screen then
        local widgets, class = StaticFindObject(widgets_path), StaticFindObject(screen_path)
        if not valid(class) then LoadAsset(screen_path); class = StaticFindObject(screen_path) end
        if binding.controller.MenuStack:GetArrayNum() ~= 0 or not valid(widgets) or not valid(class) then
            error("native_stash_view_stock_menu_unavailable", 0)
        end
        original = api:CreateSaveGameObject(save_class)
        screen = widgets:Create(binding.pawn, class, binding.controller)
        if not valid(screen) then error("native_stash_view_stock_menu_unavailable", 0) end
        current.screen, screen.Stash = screen, current.actor
        binding.controller:AddMenu(screen)
    end
    local address = screen:GetAddress()
    if not SilverfishSetNativeStashScreen(address, false)
        or not SilverfishReplaceNativeStashPreview(address, preview:GetAddress(), original and original:GetAddress() or 0) then
        error("native_stash_view_stock_binding_unavailable", 0)
    end
    screen.StashPanel:FullRefresh()
    screen.BackpackPanel:FullRefresh()
    local cards = screen.StashPanel.Cards
    if cards:GetArrayNum() ~= count then error("native_stash_view_stock_cards_unavailable", 0) end
    for index = 1, count do
        local card = entry(cards, index)
        local item = valid(card) and card.Item
        local guid = item and identifier(item[item_guid])
        local expected_row = guid and seen[guid]
        if not expected_row or (projected_order and projected_order[index] ~= guid)
            or not same(item[item_class], expected_row[item_class])
            or item[item_ammo] ~= expected_row[item_ammo] then error("native_stash_view_stock_card_changed", 0) end
        seen[guid] = nil
    end
    if not SilverfishSetNativeStashScreen(address, true) then
        error("native_stash_view_stock_cards_unavailable", 0)
    end
    current.state = "rendered"
end

local function fixture(binding, id)
    local pawn = binding.pawn
    local actor
    for _, candidate in ipairs(FindAllOf("Stash_Master_BP_C") or {}) do
        if site_id(candidate, binding) == id then actor = candidate; break end
    end
    local camera = pawn.Camera
    if not valid(actor) or not valid(camera) or not near(pawn, actor) or current
        or not SilverfishSetNativeInteractScope(pawn:GetAddress(), false) then
        error("native_stash_view_fixture_scope_unavailable", 0)
    end
    SilverfishTakeNativeStashPress(pawn:GetAddress())
    local origin, center, rotation = camera:K2_GetComponentLocation(), actor:K2_GetActorLocation(), camera.RelativeRotation
    local original = {Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll}
    local focused, dispatch
    local ok, proof, path = pcall(function()
        local box = actor.Box
        if valid(box) then center = box:K2_GetComponentLocation() end
        for _, offset in ipairs({0, 20, -20, 35, -35}) do
            local x, y, z = center.X - origin.X, center.Y - origin.Y, center.Z - origin.Z + offset
            camera:K2_SetWorldRotation({Pitch = math.deg(math.atan(z, math.sqrt(x*x + y*y))),
                Yaw = math.deg(math.atan(y, x)), Roll = 0}, false, {}, true)
            pawn:LookTrace()
            local component = pawn.InteractionTarget_Component
            if valid(component) then focused = component:GetFullName() end
            if pawn.CanInteract == true and valid(component) and same(component:GetOwner(), actor) then
                local consumed, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                dispatch = status
                if consumed ~= true or status ~= "stock_started_consumed" then break end
                return SilverfishTakeNativeStashPress(pawn:GetAddress())
            end
        end
    end)
    camera:K2_SetRelativeRotation(original, false, {}, true)
    pawn:LookTrace()
    if not ok then error(proof, 0) end
    local matched = proof and same(StaticFindObject(path), actor) and begin(binding, actor)
    if not matched or binding.controller.bShowMouseCursor ~= false then
        view.clear()
        error(string.format("native_stash_view_fixture_stock_interact_unverified: focus=%s dispatch=%s path=%s cursor=%s",
            tostring(focused), tostring(dispatch), tostring(path), tostring(binding.controller.bShowMouseCursor)), 0)
    end
    return "stock stash interaction verified"
end

local function split_fixture(screen, source, card, count, gesture)
    local manager, expected, source_guid = screen.ItemManager, card.Item[item_ammo], identifier(card.Item[item_guid])
    local stash_count, pack_count = screen.StashPanel.Cards:GetArrayNum(), screen.BackpackPanel.Cards:GetArrayNum()
    if not count or not source_guid or math.type(expected) ~= "integer" or count >= expected then
        error("native_stash_view_fixture_split_count", 0)
    end
    manager:OpenContextMenu(card)
    manager:ActionSplitStack()
    local popup = manager.SplitStackPopup
    if manager.IsSplittingStack ~= true or not valid(popup) or not same(popup.ItemCard, card) then
        error("native_stash_view_fixture_stock_split_popup", 0)
    end
    popup:SetCount(count)
    manager:FinalSplitStack(count)
    local held = manager.HeldItem
    local portion = identifier(held[item_guid])
    if manager.HoldingItem ~= true or not manager.HeldItemWidget:IsVisible() or not portion or portion == source_guid
        or held[item_ammo] ~= count or card.Item[item_ammo] ~= expected
        or SilverfishTakeNativeStashMove(screen:GetAddress()) then
        error("native_stash_view_fixture_stock_split_cursor", 0)
    end
    if gesture == "cancel" then
        manager:ReturnItem(held, {})
    elseif gesture == "move" then
        local destination = source == "stash" and screen.BackpackPanel or screen.StashPanel
        if destination.IsInsertMode ~= true then error("native_stash_view_fixture_split_target", 0) end
        manager:InsertItem(destination, -1)
        manager:InsertItem(destination, -1)
    else
        error("native_stash_view_fixture_split_gesture", 0)
    end
    local guid, deposit, _, quantity, before, prior = SilverfishTakeNativeStashMove(screen:GetAddress())
    if manager.HoldingItem ~= false or identifier(manager.HeldItem[item_guid]) or card.Item[item_ammo] ~= expected
        or screen.StashPanel.Cards:GetArrayNum() ~= stash_count or screen.BackpackPanel.Cards:GetArrayNum() ~= pack_count
        or (gesture == "cancel" and guid)
        or (gesture == "move" and (guid ~= source_guid or deposit ~= (source == "backpack")
            or quantity ~= count or before or prior ~= expected or SilverfishTakeNativeStashMove(screen:GetAddress()))) then
        error("native_stash_view_fixture_stock_split_request", 0)
    end
    if guid then queue(source, guid, nil, quantity, prior) end
    return "stock stash split cursor verified"
end

function view.command(fields, state, phase, payload)
    local verb = fields[2]
    if verb == "native-stash-view-close" then view.clear(); return "private stash closed" end
    if verb == "native-stash-view-fixture" and fixtures and #fields == 3 then
        local binding = report(state, phase)
        if binding then return fixture(binding, fields[3]) end
        error("native_stash_view_fixture_scope_unavailable", 0)
    end
    if verb == "native-stash-view-click-fixture" and fixtures and #fields == 3 then
        local target = fields[3]
        local screen = current and current.screen
        if not valid(screen) or current.state ~= "rendered" then error("native_stash_view_fixture_click_unavailable", 0) end
        if target == "close" then view.clear(); return "stock stash closed" end
        local to_move, stock_index = target:match("^reorder%-stash:([0-9A-F]+):(%d+)$")
        local split_side, split_guid, split_count, split_gesture = target:match("^split%-(%a+):([0-9A-F]+):(%d+):(%a+)$")
        local action, source, wanted = target:match("^(%a+)%-(%a+):([0-9A-F]+)$")
        if to_move then action, source, wanted = "reorder", "stash", to_move
        elseif split_side then source, wanted = split_side, split_guid
        elseif not action then source, wanted = target:match("^(%a+):([0-9A-F]+)$") end
        local panel = source == "backpack" and screen.BackpackPanel or source == "stash" and screen.StashPanel
        if not valid(panel) then error("native_stash_view_fixture_panel_unavailable", 0) end
        local manager = screen.ItemManager
        for index = 1, panel.Cards:GetArrayNum() do
            local card = entry(panel.Cards, index)
            if valid(card) and identifier(card.Item[item_guid]) == wanted then
                if split_side then
                    return split_fixture(screen, source, card, math.tointeger(tonumber(split_count)), split_gesture)
                end
                local stash_count, backpack_count = screen.StashPanel.Cards:GetArrayNum(), screen.BackpackPanel.Cards:GetArrayNum()
                if action then
                    manager:StartHoverCard(card)
                    manager:InitiateItemTake()
                    manager:EndHoverCard(card)
                    if manager.HoldingItem ~= true or identifier(manager.HeldItem[item_guid]) ~= wanted
                        or not manager.HeldItemWidget:IsVisible()
                        or screen.StashPanel.Cards:GetArrayNum() ~= stash_count
                        or screen.BackpackPanel.Cards:GetArrayNum() ~= backpack_count
                        or SilverfishTakeNativeStashMove(screen:GetAddress()) then
                        error("native_stash_view_fixture_stock_pickup_unverified", 0)
                    end
                    if action == "close" then
                        local controller = current.binding.controller
                        view.clear()
                        if visible(controller, screen) then
                            error("native_stash_view_fixture_close_while_holding_failed", 0)
                        end
                        return "stock stash closed while holding an uncommitted item"
                    end
                    if action == "cancel" then
                        manager:ReturnItem(manager.HeldItem, {})
                    else
                        local destination = (action == "return" or action == "reorder") and panel
                            or source == "backpack" and screen.StashPanel or screen.BackpackPanel
                        if (action == "drag" or action == "reorder") and destination.IsInsertMode ~= true then
                            error("native_stash_view_fixture_stock_target_not_ready", 0)
                        end
                        local index = stock_index and math.tointeger(tonumber(stock_index)) or -1
                        manager:InsertItem(destination, index)
                        manager:InsertItem(destination, index)
                    end
                    if manager.HoldingItem ~= false or identifier(manager.HeldItem[item_guid]) then
                        error("native_stash_view_fixture_stock_cursor_not_cleared", 0)
                    end
                    if action ~= "drag" and action ~= "reorder" then
                        if SilverfishTakeNativeStashMove(screen:GetAddress())
                            or screen.StashPanel.Cards:GetArrayNum() ~= stash_count
                            or screen.BackpackPanel.Cards:GetArrayNum() ~= backpack_count then
                            error("native_stash_view_fixture_stock_return_changed_inventory", 0)
                        end
                        return "stock stash cursor cleared without moving an item"
                    end
                elseif source == "backpack" then
                    manager.HoveredCard = card
                    manager:QuickMove()
                    manager:QuickMove()
                    manager.HoveredCard = nil
                else
                    manager:OpenContextMenu(card)
                    local menu = manager.ItemContextMenu
                    if not valid(menu) or not same(menu["Item Card"], card) then
                        error("native_stash_view_fixture_context_menu_unavailable", 0)
                    end
                    manager:ActionQuickMove()
                    manager:ActionQuickMove()
                end
                local guid, deposit, _, _, before = SilverfishTakeNativeStashMove(screen:GetAddress())
                if guid ~= wanted or deposit ~= (source == "backpack")
                    or (before ~= nil) ~= (action == "reorder")
                    or screen.StashPanel.Cards:GetArrayNum() ~= stash_count
                    or screen.BackpackPanel.Cards:GetArrayNum() ~= backpack_count
                    or SilverfishTakeNativeStashMove(screen:GetAddress()) then error("native_stash_view_fixture_move_unverified", 0) end
                queue(source, guid, before)
                return action and "stock stash drag captured" or "stock stash QuickMove captured"
            end
        end
        error("native_stash_view_fixture_card_unavailable", 0)
    end
    if verb ~= "native-stash-view-render" and verb ~= view.memory_render and verb ~= "native-stash-view-unavailable" then return end
    if verb == view.memory_render and type(payload) ~= "string" then error("native_stash_view_invalid_page", 0) end
    local binding = report(state, phase)
    if not binding or not current or not same(binding.pawn, current.binding.pawn) or current.state ~= "pending"
        or tostring(current.sequence) ~= fields[3] or binding.map ~= fields[4] or current.id ~= fields[5]
        or site_id(current.actor, binding) ~= current.id or not near(binding.pawn, current.actor) then
        error("native_stash_view_request_expired", 0)
    end
    if verb == "native-stash-view-render" or verb == view.memory_render then
        local ok, failure = pcall(render, fields, state, phase, payload)
        if not ok then view.clear(); error(failure, 0) end
    else
        view.clear()
    end
    return verb == view.memory_render and "private stash view updated in memory" or "private stash view updated"
end

function view.rows(state, phase, row)
    if not current or phase ~= "connected" or current.binding.map ~= state.map then return {} end
    local fields = {current.state, current.sequence, current.binding.map, current.binding.name,
        current.id, current.mode, 0, current.item or "-", current.before}
    if current.expected then fields[9], fields[10] = current.amount, current.expected end
    return {row("native-stash-view", table.unpack(fields))}
end

function view.start(bridge, is_downed)
    mailbox, downed = bridge, is_downed
end

return view
