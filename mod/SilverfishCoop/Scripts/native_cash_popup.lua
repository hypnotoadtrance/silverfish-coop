local popup = {}
local widget_path = "/Game/Classes/GUI/SubWidgets/Allert_Widget.Allert_Widget_C"
local windows_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local limits = {}
for _, entry in ipairs(require("authored_cash_catalog")) do
    limits[entry.class] = {entry.cash_min, entry.cash_max}
end

local current

local function valid(object) return object and object:IsValid() end
local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function clear()
    if current and valid(current.widget) and current.widget:IsInViewport() then
        current.widget:RemoveFromParent()
    end
    current = nil
end

function popup.enqueue(state, pawn, epoch, path, amount)
    local range = limits[path]
    if not range or not math.tointeger(amount) or amount < range[1] or amount > range[2]
        or not pawn:IsLocallyControlled() or pawn:HasAuthority() or pawn:IsActorBeingDestroyed()
        or not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, state.controller) then return false end
    if not current or current.epoch ~= epoch or not same(current.pawn, pawn) or not same(current.world, state.world) then
        clear()
        current = {epoch = epoch, pawn = pawn, world = state.world, controller = state.controller, queue = {}}
    end
    if #current.queue >= 16 then return false end
    current.queue[#current.queue + 1] = amount
    return true
end

function popup.show(controller, message)
    local widgets = StaticFindObject(windows_path)
    local class = StaticFindObject(widget_path)
    if not valid(class) then LoadAsset(widget_path); class = StaticFindObject(widget_path) end
    if not valid(widgets) or not valid(class) then return end
    local widget = widgets:Create(controller, class, controller)
    if not valid(widget) or not same(widget:GetClass(), class)
        or not same(widget:GetOwningPlayer(), controller) then return end
    widget.Messaage = FText(message)
    widget:AddToViewport(9999)
    return widget:IsInViewport() and widget or nil
end

function popup.update(state, phase)
    if not current then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "connected" or not same(pawn, current.pawn) or not same(state.world, current.world)
        or not same(state.controller, current.controller) then clear(); return end
    if not current.queue[1] or valid(current.widget) and current.widget:IsInViewport() then return end
    local now = os.clock()
    if current.retry and now < current.retry then return end
    current.retry = now + .25
    local ok, widget = pcall(popup.show, current.controller, ("Received $%d!"):format(current.queue[1]))
    if not ok or not widget then return end
    current.widget = widget
    table.remove(current.queue, 1)
end

return popup
