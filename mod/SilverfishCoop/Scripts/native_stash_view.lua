local view = {}
local surface = require("native_title_surface")

local headless = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"
local stash_path = "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local page_size = 6
local current, menu, cursor, downed
local sequence = 0

local function valid(object)
    return object and object:IsValid()
end

local function same(a, b)
    return valid(a) and valid(b) and a:GetAddress() == b:GetAddress()
end

local function report(state, phase)
    if phase ~= "connected" or not state.listening or state.server ~= true or not state.local_pawn
        or type(state.map) ~= "string" then return end
    local controller, pawn = state.controller, state.local_pawn.pawn
    if not valid(controller) or not valid(pawn) or controller.Loading ~= false or pawn.Dead ~= false
        or not controller:IsLocalController() or not pawn:IsLocallyControlled() or pawn:HasAuthority()
        or not same(controller.Pawn, pawn) or not same(pawn:GetWorld(), state.world)
        or controller.MenuStack:GetArrayNum() ~= 0 or type(downed) ~= "function" or downed(pawn) ~= false then return end
    return {controller = controller, pawn = pawn, world = state.world, map = state.map, name = state.local_pawn.name}
end

local function site_id(actor, binding)
    local base = StaticFindObject(stash_path)
    if not valid(base) or not valid(actor) or not actor:IsA(base) or not same(actor:GetWorld(), binding.world)
        or actor:IsActorBeingDestroyed() then return end
    local path, leaf = actor:GetFullName():match("^%S+ (/Game/%S+)$"), binding.map:match("([^/]+)$")
    local prefix = leaf and binding.map .. "." .. leaf .. ":PersistentLevel."
    local id = actor.Stash_ID
    if not prefix or not path or path:sub(1, #prefix) ~= prefix or not id then return end
    local a, b, c, d = math.tointeger(id.A), math.tointeger(id.B), math.tointeger(id.C), math.tointeger(id.D)
    if not a or not b or not c or not d or (a | b | c | d) == 0 then return end
    return string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
end

local function near(pawn, actor)
    local from, to, range = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation(), pawn.InteractionRange
    return from and to and type(range) == "number" and range > 0 and range <= 1000
        and (from.X - to.X)^2 + (from.Y - to.Y)^2 + (from.Z - to.Z)^2 <= range^2
end

local function release_input()
    local control = cursor
    cursor = nil
    if not control or not valid(control.controller) then return end
    local controller = control.controller
    controller:SetIgnoreMoveInput(false)
    controller:SetIgnoreLookInput(false)
    if controller.MenuStack:GetArrayNum() == 0 then
        controller.bShowMouseCursor = control.previous
        local widgets = StaticFindObject(widgets_path)
        if valid(widgets) then widgets:SetInputMode_GameOnly(controller, false) end
    end
end

function view.clear()
    current = nil
    surface.remove(menu)
    menu = nil
    release_input()
end

local function queue(mode, offset, item)
    sequence = sequence + 1
    current.sequence, current.mode, current.offset = sequence, mode, offset
    current.item, current.state = item, "pending"
    current.expires = os.time() + 45
end

local function draw(title, note, rows, on_click)
    surface.remove(menu)
    menu = nil
    local binding = current.binding
    local mounted, result = pcall(surface.mount, binding, {title = title, note = note, rows = rows}, on_click)
    if not mounted then
        view.clear()
        error(result, 0)
    end
    menu = result
    if headless then return end
    local controller = binding.controller
    if not cursor then
        cursor = {controller = controller, previous = controller.bShowMouseCursor}
        controller:SetIgnoreMoveInput(true)
        controller:SetIgnoreLookInput(true)
    end
    local widgets = StaticFindObject(widgets_path)
    controller.bShowMouseCursor = true
    widgets:SetInputMode_GameAndUIEx(controller, menu.widget, 0, false, false)
end

local function click(key)
    if not current then return end
    if key == "close" then view.clear(); return end
    if current.state ~= "rendered" then return end
    if key == "backpack" or key == "stash" then
        if current.mode ~= key then queue(key, 0) end
    elseif key == "previous" and current.offset > 0 then
        queue(current.mode, current.offset - page_size)
    elseif key == "next" and current.more then
        queue(current.mode, current.offset + page_size)
    elseif current.items[key] then
        queue(current.mode, 0, key)
    end
end

local function begin(binding, actor)
    local id = site_id(actor, binding)
    if not id or not near(binding.pawn, actor) then return false end
    current = {binding = binding, actor = actor, id = id}
    queue("stash", 0)
    return true
end

function view.sync(state, phase)
    local binding = report(state, phase)
    if current and (not binding or not same(binding.pawn, current.binding.pawn)
        or site_id(current.actor, binding) ~= current.id or not near(binding.pawn, current.actor)
        or current.state == "pending" and os.time() >= current.expires) then
        view.clear()
    end
    if not binding or type(SilverfishTakeNativeStashPress) ~= "function" then return end
    local press, path = SilverfishTakeNativeStashPress(binding.pawn:GetAddress())
    if press and not current and type(path) == "string" then begin(binding, StaticFindObject(path)) end
end

local function item_row(field, items)
    local guid, count, path = field:match("^([0-9A-F]+)|([0-9]+)|(/Game/[%w_./%-]+)$")
    local quantity = math.tointeger(tonumber(count))
    local name = path and path:match("%.([%w_%-]+)_C$")
    if not guid or #guid ~= 32 or not quantity or quantity < 1 or not name or items[guid] then
        error("native_stash_view_invalid_item", 0)
    end
    items[guid] = true
    name = name:gsub("^Item_", ""):gsub("^Wep_", ""):gsub("_BP$", ""):gsub("_", " ")
    return {{key = guid, label = name:sub(1, 40), detail = "×" .. quantity}}
end

local function render(fields)
    local combined, more = fields[6], fields[7]
    if #fields < 7 or #fields > 7 + page_size or (combined ~= "0" and combined ~= "1")
        or (more ~= "0" and more ~= "1") or (more == "1" and #fields ~= 7 + page_size) then
        error("native_stash_view_invalid_page", 0)
    end
    local items, rows = {}, {{{key = "stash", label = "Stash"}, {key = "backpack", label = "Backpack"}}}
    for index = 8, #fields do rows[#rows + 1] = item_row(fields[index], items) end
    local buttons = {}
    if current.offset > 0 then buttons[#buttons + 1] = {key = "previous", label = "Previous"} end
    if more == "1" then buttons[#buttons + 1] = {key = "next", label = "Next"} end
    buttons[#buttons + 1] = {key = "close", label = "Close"}
    rows[#rows + 1] = buttons
    local title = current.mode == "stash" and "Stash" or "Backpack"
    local action = current.mode == "stash" and "Select an item to withdraw." or "Select an item to deposit."
    if #fields == 7 then action = current.mode == "stash" and "Your stash is empty." or "Your backpack is empty." end
    local scope = combined == "1" and "All rest sites" or "This rest site"
    draw(title, scope .. "  ·  " .. action, rows, click)
    current.state, current.items, current.more = "rendered", items, more == "1"
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

function view.command(fields, state, phase)
    local verb = fields[2]
    if verb == "native-stash-view-close" then view.clear(); return "private stash closed" end
    if verb == "native-stash-view-fixture" and fixtures and #fields == 3 then
        local binding = report(state, phase)
        if binding then return fixture(binding, fields[3]) end
        error("native_stash_view_fixture_scope_unavailable", 0)
    end
    if verb == "native-stash-view-click-fixture" and fixtures and #fields == 3 then
        if not surface.press_fixture(menu, fields[3]) then error("native_stash_view_fixture_click_unavailable", 0) end
        return "stock stash UI button pressed"
    end
    if verb ~= "native-stash-view-render" and verb ~= "native-stash-view-unavailable" then return end
    local binding = report(state, phase)
    if not binding or not current or not same(binding.pawn, current.binding.pawn) or current.state ~= "pending"
        or tostring(current.sequence) ~= fields[3] or binding.map ~= fields[4] or current.id ~= fields[5]
        or site_id(current.actor, binding) ~= current.id or not near(binding.pawn, current.actor) then
        error("native_stash_view_request_expired", 0)
    end
    if verb == "native-stash-view-render" then
        render(fields)
    else
        draw("Stash unavailable", "Reopen the stash to try again.", {{{key = "close", label = "Close"}}}, click)
        current.state = "unavailable"
    end
    return "private stash view updated"
end

function view.rows(state, phase, row)
    if not current or phase ~= "connected" or current.binding.map ~= state.map then return {} end
    return {row("native-stash-view", current.state, current.sequence, current.binding.map, current.binding.name,
        current.id, current.mode, current.offset, current.item or "-")}
end

function view.start(is_downed)
    downed = is_downed
end

return view
