local stock = {}
local screen_class = "/Game/Classes/GUI/Dialogue_Widget.Dialogue_Widget_C"
local response_class = "/Game/Classes/GUI/SubWidgets/PlayerResponce_Widget.PlayerResponce_Widget_C"
local response_text = "TextContent_2_891360234FC305CA4D09FE9EA69C8936"
local screen, controller, cards

local function valid(object) return object ~= nil and object ~= false and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function require_screen(condition, detail)
    if not condition then error("native_castaway_stock_screen_" .. detail, 0) end
end

function stock.exists() return valid(screen) end

function stock.present(current)
    local stack = valid(controller) and controller.MenuStack
    return current and same(controller, current.controller) and stack and stack:GetArrayNum() == 1 and same(stack[1], screen)
end

function stock.close()
    local widget, owner = screen, controller
    screen, controller, cards = nil, nil, nil
    if not valid(widget) then return end
    widget:SetIsEnabled(false)
    if type(SilverfishSetNativeCastawayDialogueScreen) == "function" then SilverfishSetNativeCastawayDialogueScreen(0) end
    local stack = valid(owner) and owner.MenuStack
    if stack and stack:GetArrayNum() == 1 and same(stack[1], widget) then owner:CloseMenu({}) end
    if widget:IsInViewport() then widget:RemoveFromParent() end
end

function stock.open(current, content)
    require_screen(type(SilverfishSetNativeCastawayDialogueScreen) == "function"
        and type(SilverfishTakeNativeCastawayResponse) == "function", "response_hook")
    if not stock.present(current) then
        stock.close()
        require_screen(current.controller.MenuStack:GetArrayNum() == 0, "another_menu")
        local class = StaticFindObject(screen_class)
        if not valid(class) then LoadAsset(screen_class); class = StaticFindObject(screen_class) end
        local library = StaticFindObject("/Script/UMG.Default__WidgetBlueprintLibrary")
        require_screen(valid(class) and valid(library), "class")
        controller = current.controller
        screen = library:Create(current.pawn, class, controller)
        require_screen(exact(screen, screen_class) and same(screen:GetOwningPlayer(), controller), "owner")
        screen:SetIsEnabled(false)
        controller:AddMenu(screen)
        require_screen(stock.present(current), "mount")
    end
    screen:SetIsEnabled(false)
    local panel = screen.VerticalBox_PlayerResponces
    local line = screen.TextBlock_CurrentLine
    require_screen(exact(panel, "/Script/UMG.VerticalBox") and exact(line, "/Script/UMG.TextBlock")
        and exact(screen.TextBlock_NpcName, "/Script/UMG.TextBlock") and valid(screen.ContinueButton), "layout")
    local display = table.concat(content.lines, "\n\n")
    screen.TextBlock_NpcName:SetText(FText(current.profile.title))
    line:SetText(FText(display))
    if exact(screen.TextBlock_CurrentLine_Fake, "/Script/UMG.TextBlock") then
        screen.TextBlock_CurrentLine_Fake:SetText(FText(display))
    end
    require_screen(line:GetText():ToString() == display, "text")
    screen.ContinueButton:SetVisibility(1)

    -- The stock buttons display only the host's response text. Their native click handler sends the authorized ordinal.
    local responses = {}
    for index, ordinal in ipairs(content.ordinals) do responses[index] = {[response_text] = FText(content.choices[ordinal])} end
    screen["Player Responces"] = responses
    panel:ClearChildren()
    screen:Display_Responces()
    require_screen(panel:GetChildrenCount() == #responses, "response_count")
    local parameters = {screen:GetAddress()}
    cards = {}
    for index, ordinal in ipairs(content.ordinals) do
        local card = panel:GetChildAt(index - 1)
        require_screen(exact(card, response_class) and same(card.ParentWidget_Ref, screen) and valid(card.Button_0)
            and card.Entry[response_text]:ToString() == content.choices[ordinal], "response")
        cards[ordinal] = card
        parameters[#parameters + 1] = ordinal
        parameters[#parameters + 1] = card:GetAddress()
    end
    require_screen(SilverfishSetNativeCastawayDialogueScreen(table.unpack(parameters)) == true, "response_registration")
    screen:SetIsEnabled(true)
end

function stock.take(current)
    if stock.present(current) then return SilverfishTakeNativeCastawayResponse(screen:GetAddress()) end
end

function stock.freeze()
    if valid(screen) then screen:SetIsEnabled(false) end
end

function stock.fixture(ordinal)
    local card = cards and cards[ordinal]
    require_screen(valid(card), "fixture_response")
    card:BndEvt__PlayerResponce_Widget_Button_0_K2Node_ComponentBoundEvent_1_OnButtonClickedEvent__DelegateSignature()
end

return stock
