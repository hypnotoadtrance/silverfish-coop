local creator = {}
local title = require("native_title_entry")
local save_scope = require("native_save_scope")

local main_menu = "/Game/Classes/Maps/MainMenu_P"
local editor_map = "/Game/Classes/Maps/CharacterSelect_Map_P"
local widget_path = "/Game/Classes/GUI/SubWidgets/NewGame_SubWidget.NewGame_SubWidget_C"
local game_instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local widgets_path = "/Script/UMG.Default__WidgetBlueprintLibrary"
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

local function route_open_level(_, context, name, absolute, options)
    if not record or (record.state ~= "opening" and record.state ~= "creating")
        or save_scope.selected() ~= record.scope then return end
    local source = context:get()
    local world = valid(source) and source:GetWorld()
    if not world_is(world, editor_map) or not same(world.OwningGameInstance, record.instance) then return end
    local target = text(name:get())
    if leaf(target) == leaf(main_menu) then return end
    local destination = record.resolve(target)
    local save = record.instance.SaveSlot_Ref
    local statics = StaticFindObject(statics_path)
    if not destination or absolute:get() ~= true or text(options:get()) ~= ""
        or not exact(save, save_path) or not same(save, record.save)
        or text(record.instance.CurrentSaveSlot) ~= record.slot or text(save.Level) ~= leaf(destination)
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

function creator.begin(state, phase, slot, port, resolve, on_departure)
    local context = title.probe(state, phase)
    local scope = save_scope.selected()
    if not context or not scope or record or type(slot) ~= "string" or #slot ~= 43
        or not slot:match("^SFCoopLive_[0-9a-f]+$") or not port or port < 1 or port > 65535 then
        error("stock_creator_requires_a_scoped_title", 0)
    end
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
    record = {state = "opening", scope = scope, slot = slot, port = port, instance = instance,
              resolve = resolve, on_departure = on_departure}
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

function creator.rows(state, phase, row)
    if not record then return {} end
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

function creator.clear()
    record = nil
end

return creator
