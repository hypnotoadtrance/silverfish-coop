-- Test-only driver for the mounted stock Character Editor in an isolated headless title.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or not mailbox
    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" then return end
local seat, nonce = mailbox:match("[/\\]titles[/\\]([a-zA-Z0-9][a-zA-Z0-9_%-]*)[/\\]run[/\\]game%-bridge[/\\]([0-9a-f]+)$")
if not seat or #seat > 64 or #nonce ~= 32 then return end

local command = mailbox .. "/stock-creator-probe-command.tsv"
local reply = mailbox .. "/stock-creator-probe-reply.tsv"
local editor_map = "/Game/Classes/Maps/CharacterSelect_Map_P"
local editor_class = "/Game/Classes/GUI/Character_Editor_Widget.Character_Editor_Widget_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local stock_button_class = "/Game/Classes/GUI/SubWidgets/Button_Default_Widget.Button_Default_Widget_C"
local selector_class = "/Game/Classes/GUI/SubWidgets/Setting_Selector_Widget.Setting_Selector_Widget_C"
local next_choice = "BndEvt__Setting_Selector_Widget_Button_Next_K2Node_ComponentBoundEvent_0_OnButtonClickedEvent__DelegateSignature"
local rules_key = "ReplenishLoot_5_0542042A4D8D98E206019BA9B5A3E23F"

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, class) return valid(object) and same(object:GetClass(), StaticFindObject(class)) end
local function value_text(value) return type(value) == "userdata" and value:ToString() or value end

local function editor()
    local viewport = FindFirstOf("GameViewportClient")
    local world = exact(viewport, "/Script/Engine.GameViewportClient") and viewport.World
    if not valid(world) or world:GetFullName() ~= "World " .. editor_map .. ".CharacterSelect_Map_P"
        or valid(world.NetDriver) then return nil, "stock_creator_world_unavailable" end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local controller = valid(statics) and statics:GetPlayerController(world, 0)
    if not exact(controller, controller_class) or not controller:IsLocalController()
        or not controller:HasAuthority() or not same(controller:GetWorld(), world) then
        return nil, "stock_creator_controller_unavailable"
    end
    local instance = statics:GetGameInstance(controller)
    local slot = exact(instance, instance_class) and value_text(instance.CurrentSaveSlot)
    if type(slot) ~= "string" or not (#slot == 43 and slot:match("^SFCoopLive_[0-9a-f]+$")
        or #slot == 51 and slot:match("^SFCoopGuestCreator_[0-9a-f]+$")) then
        return nil, "stock_creator_scoped_save_unavailable"
    end
    local stack = controller.MenuStack
    local count = stack and stack:GetArrayNum()
    if type(count) ~= "number" or count < 1 or count > 32 then return nil, "stock_creator_menu_stack_unavailable" end
    local menu = stack[count]
    if not exact(menu, editor_class) or not same(menu:GetOwningPlayer(), controller)
        or not menu:IsInViewport() or not menu:IsVisible() then return nil, "stock_creator_menu_unavailable" end
    local switch = menu.WidgetSwitcher_0
    if not exact(switch, "/Script/UMG.WidgetSwitcher") then return nil, "stock_creator_switcher_unavailable" end
    local page = switch:GetActiveWidget()
    if not valid(page) then return nil, "stock_creator_page_unavailable" end
    return menu, switch, page
end

local function control(page, widget, class)
    assert(exact(widget, class), "stock_creator_control_class_mismatch")
    assert(widget:GetIsEnabled() and widget:IsVisible(), "stock_creator_control_unavailable")
    local ancestor = widget
    for _ = 1, 24 do
        if same(ancestor, page) then return widget end
        if not valid(ancestor) then break end
        ancestor = ancestor:GetParent()
    end
    error(("stock_creator_control_outside_active_page: %s in %s"):format(widget:GetFName():ToString(),
        page:GetFName():ToString()), 0)
end

local function click(menu, page, field, handler)
    control(page, menu[field], stock_button_class)
    menu[handler](menu)
end

local function pick(selector, index)
    local settings = selector.Settings
    local count = settings and settings:GetArrayNum()
    assert(type(count) == "number" and count >= 2 and count <= 64 and index >= 0 and index < count,
        "stock_creator_selector_index_unqualified")
    for _ = 1, count do
        if selector.IndexOutput == index then return end
        selector[next_choice](selector)
    end
    error("stock_creator_stock_selector_did_not_reach_target", 0)
end

local function page_is(switch, expected)
    local actual = switch:GetActiveWidgetIndex()
    assert(actual == expected, ("stock_creator_wrong_page:expected=%d;observed=%s"):format(expected, tostring(actual)))
end

local actions = {}

function actions.observe(_, switch)
    return "page=" .. tostring(switch:GetActiveWidgetIndex())
end

function actions.preset(menu, switch, page)
    page_is(switch, 0)
    click(menu, page, "Button_Class", "BndEvt__Character_Editor_Widget_Button_Class_K2Node_ComponentBoundEvent_50_Pressed__DelegateSignature")
    page_is(switch, 4)
    return "page=4"
end

function actions.veteran(menu, switch, page)
    page_is(switch, 4)
    local selector = control(page, menu.Setting_Selector_Class, selector_class)
    local classes = menu.PlayerClasses
    local count = classes and classes:GetArrayNum()
    assert(type(count) == "number" and count >= 2 and count <= 64, "stock_creator_class_rows_unqualified")
    local veteran
    for index = 1, count do
        if value_text(classes[index].RowName) == "Veteran" then
            assert(veteran == nil, "stock_creator_veteran_row_ambiguous")
            veteran = index - 1
        end
    end
    assert(veteran, "stock_creator_veteran_row_missing")
    pick(selector, veteran)
    local selected = menu.Selected_ClassStruct
    assert(menu.Selected_Class_Index == veteran and selected
        and selected.Starting_Cash_48_B196F1C1461EF00CACE44F9AD878A91A == 350
        and value_text(selected.Spawn_Map_35_781D11264117AA867DE5FBBB008A9151) == "Observitory_P",
        "stock_creator_veteran_row_not_applied")
    return "class=Veteran;starting_cash=350;starting_map=Observitory_P"
end

function actions.next(menu, switch, page)
    page_is(switch, 4)
    click(menu, page, "Button_Next_1", "BndEvt__Character_Editor_Widget_Button_Next_1_K2Node_ComponentBoundEvent_49_Pressed__DelegateSignature")
    page_is(switch, 3)
    return "page=3"
end

function actions.custom(menu, switch, page)
    page_is(switch, 3)
    local selector = control(page, menu.Setting_Selector_Widget, selector_class)
    pick(selector, 0)
    assert(menu.SelectedMode_Index == 0, "stock_creator_custom_difficulty_not_applied")
    local checkbox = control(page, menu.CheckBox_LootReplenish, "/Script/UMG.CheckBox")
    assert(checkbox:IsChecked() == true and menu.CustomRules[rules_key] == true,
        "stock_creator_default_loot_replenish_expected")
    checkbox:SetIsChecked(false)
    menu:BndEvt__Difficulty_Character_Select_Widget1_CheckBox_LootReplenish_K2Node_ComponentBoundEvent_5_OnCheckBoxComponentStateChanged__DelegateSignature(false)
    assert(checkbox:IsChecked() == false and menu.CustomRules[rules_key] == false,
        "stock_creator_loot_replenish_not_applied")
    return "difficulty=Custom;replenish_loot=0"
end

function actions.finish(menu, switch, page)
    page_is(switch, 3)
    click(menu, page, "Button_Confirm", "BndEvt__DifficultySelect_Widget_Button_Select_K2Node_ComponentBoundEvent_1_Pressed__DelegateSignature")
    return "stock_finish_invoked"
end

local function guest_style(menu, switch, page, kit)
    page_is(switch, 0)
    click(menu, page, "Button_Class_1", "BndEvt__Character_Editor_Widget_Button_Class_1_K2Node_ComponentBoundEvent_45_Pressed__DelegateSignature")
    page_is(switch, 1)
    local head, hair = menu.Setting_Selector_FaceType, menu.Setting_Selector_Plumage
    for _, field in ipairs({{head, selector_class}, {hair, selector_class}, {menu.Button_Confirm_1, stock_button_class}}) do
        local widget = field[1]
        assert(exact(widget, field[2]) and widget:GetIsEnabled() and widget:IsVisible()
            and valid(widget:GetOuter()) and same(widget:GetOuter():GetOuter(), menu),
            "guest_stock_creator_widget_unavailable")
    end
    local selected = menu.Selected_ClassStruct
    local head_field = "Mesh_Head_60_630BB776410363C64BE2C89A3F72B1C5"
    local hair_field = "Mesh_Plumage_80_4B6C3DD94D27DEC901D6829472D5F546"
    local prior = selected[head_field]
    pick(head, (head.IndexOutput + 1) % head.Settings:GetArrayNum())
    pick(hair, (hair.IndexOutput + 1) % hair.Settings:GetArrayNum())
    assert(menu.Setting_Selector_Faction:GetIsEnabled() == false
        and menu.Setting_Selector_StartMap:GetIsEnabled() == false, "campaign_choices_enabled_for_guest")
    pick(menu.Setting_Selector_Kit, kit)
    local personality = menu.Selector_Vox_Type
    local choice
    for index = 1, personality.Settings:GetArrayNum() do
        if value_text(personality.Settings[index]) == "Fem_02" then choice = index - 1 end
    end
    pick(personality, assert(choice, "stock voice choice unavailable"))
    local stance = menu.Setting_Selector_Stance
    pick(stance, (stance.IndexOutput + 1) % stance.Settings:GetArrayNum())
    pick(menu.Setting_Selector_Passives, 2)
    menu.Passive_Selector.SelectedPassives = {FName("WeirdLuck")}
    selected = menu.Selected_ClassStruct
    assert(valid(selected[head_field]) and not same(selected[head_field], prior), "guest_stock_creator_head_did_not_change")
    local head_name = selected[head_field]:GetFullName():match("^StaticMesh (.+)$")
    local hair_name = valid(selected[hair_field]) and selected[hair_field]:GetFullName():match("^StaticMesh (.+)$") or "-"
    assert(head_name and hair_name, "guest_stock_creator_stock_option_unavailable")
    return head_name .. "|" .. hair_name
end

function actions.gueststyle(menu, switch, page)
    return guest_style(menu, switch, page, 2)
end

function actions.guestkit(menu, switch, page)
    return guest_style(menu, switch, page, 1)
end

function actions.guesthyperkit(menu, switch, page)
    return guest_style(menu, switch, page, 3)
end

function actions.guestnext(menu, switch)
    page_is(switch, 1)
    menu:BndEvt__Character_Editor_Widget_Button_Confirm_1_K2Node_ComponentBoundEvent_35_Pressed__DelegateSignature()
    page_is(switch, 3)
    return "page=3"
end

function actions.cancel(menu, switch, page)
    page_is(switch, 0)
    click(menu, page, "Button_Class_2", "BndEvt__Character_Editor_Widget_Button_Class_2_K2Node_ComponentBoundEvent_47_Pressed__DelegateSignature")
    return "stock_back_invoked"
end

local last, pending

local function run(operation)
    local menu, switch, page = editor()
    if not menu then return "waiting", switch end
    return "ok", actions[operation](menu, switch, page)
end

LoopInGameThreadWithDelay(75, function()
    if not pending then
        local input = io.open(command, "rb")
        if not input then return end
        local request = input:read(128)
        input:close()
        if not request then return end
        local ticket, operation = request:match("^1\t([0-9a-f]+)\t([a-z]+)\n$")
        if not ticket or #ticket ~= 32 or ticket == last or not actions[operation] then return end
        local ran, state, detail = pcall(run, operation)
        if not ran then state, detail = "error", state end
        detail = tostring(detail):gsub("%c", " "):sub(1, 500)
        pending, last = table.concat({"1", ticket, state, detail}, "\t") .. "\n", ticket
    end

    local temporary = reply .. ".tmp"
    local output = io.open(temporary, "wb")
    if not output then return end
    local written = output:write(pending)
    local closed = output:close()
    if written and closed and os.rename(temporary, reply) then pending = nil end
end)
