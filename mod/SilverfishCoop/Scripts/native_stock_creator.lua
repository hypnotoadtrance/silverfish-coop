local creator = {}
local title = require("native_title_entry")
local save_scope = require("native_save_scope")
local appearance = require("native_avatar_character_view")
local guest_profile = require("native_guest_creator")

local main_menu = "/Game/Classes/Maps/MainMenu_P"
local editor_map = "/Game/Classes/Maps/CharacterSelect_Map_P"
local widget_path = "/Game/Classes/GUI/SubWidgets/NewGame_SubWidget.NewGame_SubWidget_C"
local game_instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
local editor_class = "/Game/Classes/GUI/Character_Editor_Widget.Character_Editor_Widget_C"
local record, hooked

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function text(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function leaf(name)
    return type(name) == "string" and name:match("([^/]+)$") or nil
end

local function world_is(world, map)
    return valid(world) and world:GetFullName() == "World " .. map .. "." .. leaf(map)
end

local function terminal(state)
    return state == "captured" or state == "cancelled" or state == "failed"
end

local function authorizes(record)
    return exact(record.save, save_path) and same(record.instance.SaveSlot_Ref, record.save)
        and text(record.instance.CurrentSaveSlot) == record.slot
end

local function clean_guest(record)
    if save_scope.selected() ~= record.scope then return false end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return false end
    if statics:DoesSaveGameExist(record.slot, 0) and not statics:DeleteGameInSlot(record.slot, 0) then return false end
    local instance = record.instance
    instance.SaveSlot_Ref = valid(record.previous_save) and record.previous_save or nil
    instance.CurrentSaveSlot = record.previous_slot
    instance.SaveAfterTransition = record.previous_autosave
    return (same(instance.SaveSlot_Ref, record.previous_save)
            or not valid(instance.SaveSlot_Ref) and not valid(record.previous_save))
        and text(instance.CurrentSaveSlot) == record.previous_slot
        and instance.SaveAfterTransition == record.previous_autosave
        and statics:DoesSaveGameExist(record.slot, 0) == false
end

local function observe_guest_editor(state)
    local controller = state.controller
    local stack = valid(controller) and controller.MenuStack
    local count = stack and stack:GetArrayNum()
    if not count or count < 1 or count > 32 then return end
    local menu = stack[count]
    if not exact(menu, editor_class) or not same(menu:GetOwningPlayer(), controller) then return end
    if not same(record.editor, menu) then
        record.editor = menu
        for _, field in ipairs({"Setting_Selector_Faction", "Setting_Selector_StartMap", "Selector_QP_Rep",
            "Selector_QP_Quest", "Setting_Selector_Widget", "CheckBox_BarterOnly", "CheckBox_BoneBag",
            "CheckBox_CombineStash", "CheckBox_FailContracts", "CheckBox_ItemsLostPerDeath", "CheckBox_LockSaves",
            "CheckBox_LootReplenish", "CheckBox_PermaDeath", "CheckBox_SaveLvlTrans", "CheckBox_Shops",
            "CheckBox_XP", "Setting_Selector_RespawnMode"}) do
            local control = menu[field]
            if valid(control) then
                control:SetIsEnabled(false)
                control:SetToolTipText(FText("Controlled by the co-op host"))
            end
        end
    end
    local page = menu.WidgetSwitcher_0:GetActiveWidgetIndex()
    if page == 1 then
        if record.name and not record.named then
            record.named = true
            menu.EditableTextBox_Name:SetText(FText(record.name))
        end
        record.route = "custom"
    elseif page == 4 then
        record.route = "preset"
    end
end

local function captured_profile(record)
    local menu, character = record.editor, record.save.Character
    if not valid(menu) or not record.route then error("Guest character choices are unavailable", 0) end
    local recipe
    if record.route == "preset" then
        local selected = menu.PlayerClasses[menu.Selected_Class_Index + 1]
        recipe = "preset_" .. text(selected.RowName)
    else
        recipe = "kit" .. tostring(menu.Setting_Selector_Kit.IndexOutput)
    end
    return appearance.observe(character), guest_profile.observe(character, recipe), text(character[guest_profile.name_field])
end

local function guest_departure(target, absolute, options)
    if record.state == "returning" then return end
    record.result, record.model, record.profile = "failed", nil, nil
    if save_scope.selected() == record.scope then
        if leaf(target) == leaf(main_menu) then
            record.result = "cancelled"
        elseif absolute == true and options == "" and authorizes(record) and leaf(target) == text(record.save.Level) then
            local statics = StaticFindObject(statics_path)
            if valid(statics) and statics:DoesSaveGameExist(record.slot, 0) == true then
                local ok, model, profile, name = pcall(captured_profile, record)
                if ok and model ~= "-" and appearance.decode(model) then
                    record.result, record.model, record.profile = "captured", model, profile
                    name = name and name:match("^%s*(.-)%s*$")
                    if name and utf8.len(name) and utf8.len(name) > 0 and utf8.len(name) <= 64 and not name:find("%c") then
                        record.name = name
                    end
                end
            end
        end
    end
    record.state = "returning"
    -- Remove the working slot before the returning title scans for Continue candidates.
    local checked, clean = pcall(clean_guest, record)
    if not checked or not clean then record.result, record.model = "failed", nil end
end

local function route_open_level(_, context, name, absolute, options)
    if not record then return end
    local source = context:get()
    local world = valid(source) and source:GetWorld()
    if not world_is(world, editor_map) or not same(world.OwningGameInstance, record.instance) then return end
    local target = text(name:get())
    if record.attempt then
        -- Replace every departure from the guest editor, including a duplicate submit.
        local target_absolute, target_options = absolute:get(), text(options:get())
        name:set(FName(main_menu))
        absolute:set(true)
        options:set("")
        guest_departure(target, target_absolute, target_options)
        return
    end
    if (record.state ~= "opening" and record.state ~= "creating") or save_scope.selected() ~= record.scope then return end
    if leaf(target) == leaf(main_menu) then return end
    local destination = record.resolve(target)
    local save = record.instance.SaveSlot_Ref
    local statics = StaticFindObject(statics_path)
    if not destination or absolute:get() ~= true or text(options:get()) ~= ""
        or not authorizes(record) or text(save.Level) ~= leaf(destination)
        or not valid(statics) or statics:DoesSaveGameExist(record.slot, 0) ~= true
        or type(SilverfishArmNativeListenPort) ~= "function"
        or SilverfishArmNativeListenPort(record.port, world:GetAddress(),
            editor_map .. "." .. leaf(editor_map), destination) ~= true then
        record.state = "failed"
        name:set(FName(main_menu))
        return
    end
    name:set(FName(destination))
    options:set("listen")
    record.destination, record.state = destination, "travelling"
    record.on_departure(destination, record.port)
end

local function open_editor(state, phase, slot, new_record)
    local context = title.probe(state, phase)
    local scope = save_scope.selected()
    if not context or not scope or record then error("stock_creator_requires_a_scoped_title", 0) end
    local instance = context.world.OwningGameInstance
    local class = StaticFindObject(widget_path)
    local widgets = StaticFindObject(widgets_path)
    if not exact(instance, game_instance_path) or not valid(class) or not valid(widgets) then
        error("stock_creator_title_objects_unavailable", 0)
    end
    if not hooked then
        RegisterHook("/Script/Engine.GameplayStatics:OpenLevel", route_open_level)
        hooked = true
    end
    local widget = widgets:Create(context.menu, class, context.controller)
    if not exact(widget, widget_path) then error("stock_creator_new_game_widget_unavailable", 0) end
    local before = instance.SaveSlot_Ref
    new_record.state, new_record.scope, new_record.slot, new_record.instance = "opening", scope, slot, instance
    if new_record.attempt then
        new_record.previous_save, new_record.previous_slot = before, text(instance.CurrentSaveSlot)
        new_record.previous_autosave = instance.SaveAfterTransition
    end
    record = new_record
    widget:NewGame()
    local save = instance.SaveSlot_Ref
    if not exact(save, save_path) or same(save, before) then
        record.state = "failed"
        error("stock_creator_new_game_did_not_allocate_a_save", 0)
    end
    instance.CurrentSaveSlot = slot
    record.save = save
    return "opened stock character creator"
end

function creator.begin(state, phase, slot, port, resolve, on_departure)
    if type(slot) ~= "string" or #slot ~= 43 or not slot:match("^SFCoopLive_[0-9a-f]+$")
        or not port or port < 1 or port > 65535 then
        error("stock_creator_requires_a_scoped_title", 0)
    end
    return open_editor(state, phase, slot, {port = port, resolve = resolve, on_departure = on_departure})
end

function creator.begin_guest(state, phase, attempt, name)
    if type(attempt) ~= "string" or #attempt ~= 32 or not attempt:match("^[0-9a-f]+$") then
        error("guest_stock_creator_attempt_invalid", 0)
    end
    return open_editor(state, phase, "SFCoopGuestCreator_" .. attempt, {attempt = attempt, name = name})
end

function creator.clear_guest(state, phase, attempt)
    local scope = save_scope.selected()
    if not record or record.attempt ~= attempt or scope and scope ~= record.scope then
        error("guest_stock_creator_attempt_invalid", 0)
    end
    if terminal(record.state) and title.probe(state, phase) then
        record = nil
        return "cleared stock guest character creator"
    end
    if scope ~= record.scope or record.state ~= "creating" or not world_is(state.world, editor_map) or not valid(state.controller)
        or not same(state.world.OwningGameInstance, record.instance) then error("guest_stock_creator_not_ready_to_cancel", 0) end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then error("guest_stock_creator_cannot_return", 0) end
    statics:OpenLevel(state.controller, FName(main_menu), true, "")
    return "returning from stock guest character creator"
end

function creator.guest_busy()
    return record and record.attempt and not terminal(record.state)
end

function creator.guest_result(attempt)
    if record and record.attempt == attempt then
        if record.state == "captured" then return record.state, record.model, record.profile, record.name end
        return record.state
    end
end

function creator.rows(state, phase, row)
    if not record then return {} end
    if record.attempt then
        if record.state == "opening" and world_is(state.world, editor_map) then
            record.state = "creating"
        elseif (record.state == "returning" or record.state == "creating") and title.probe(state, phase) then
            -- The stock Back action can return without calling the intercepted OpenLevel.
            local result = record.state == "creating" and "cancelled" or record.result
            local checked, clean = pcall(clean_guest, record)
            record.state = checked and clean and result or "failed"
            if record.state ~= "captured" then record.model = nil end
        end
        if record.state == "creating" and world_is(state.world, editor_map) then observe_guest_editor(state) end
        return {row("native-guest-stock-creator", record.state, record.attempt,
            record.state == "captured" and record.model or "-", record.state == "captured" and record.profile or "-")}
    end
    if record.state == "opening" and world_is(state.world, editor_map) then
        record.state = "creating"
    elseif record.state == "creating" and state.map ~= editor_map and phase == "ready" then
        record.state = state.map == main_menu and "cancelled" or "failed"
    elseif record.state == "travelling" and phase == "hosting" and state.map == record.destination and state.local_pawn then
        local pawn = state.local_pawn.pawn
        if pawn:HasAuthority() and same(pawn.Save, record.save) and same(record.instance.SaveSlot_Ref, record.save)
            and text(record.instance.CurrentSaveSlot) == record.slot then
            record.state = "verified"
        end
    end
    return {row("native-stock-creator", record.state, record.destination or "-", record.slot)}
end

function creator.clear_host()
    if record and not record.attempt then record = nil end
end

return creator
