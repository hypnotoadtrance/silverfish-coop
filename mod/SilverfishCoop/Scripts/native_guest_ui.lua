local guest_ui = {}
local content_paths = require("content_paths")
local guest_save = require("guest_save")
local scope = require("native_save_scope")
local wallet = require("wallet")
local inventory_grant = require("native_inventory_grant")
local creator = require("native_guest_creator")
local appearance = require("native_avatar_character_view")
local native_world = require("native_world")
local guest_weapon = require("native_guest_weapon")
local quest_catalog = require("native_quest_catalog")
local players = require("players")
local inventory_view = require("native_inventory_view")
local statics = "/Script/Engine.Default__GameplayStatics"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_class = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local texture_class = "/Script/Engine.Texture2D"
local quest_photos = "/Game/Classes/GUI/GUI_Elements/QuestPhotos/"
local add_rep_bonus = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Add_RepBonus"
local item = {
    class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81",
    ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788",
    weight = "Weight_12_11AB9ED547504E78A849A692A36DC938",
    value = "Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9",
    no_drop = "NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D",
    guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A",
}
local notes = {
    header = "Header_2_F519DEE247621F4BFF89E2BF92B4DE49",
    body = "Body_6_0085FFB14DEABEFA900A46A1A53AC532",
    image = "Image_9_00979C944126E5FE0A030B93256AD50A",
}
local quest = {
    name = "DisplayName_2_7285E7CE446EB084E06391BFCE927182",
    giver = "QuestGiver_37_600BE36F48EDD8652F17A2B41BEFDEA0",
    kind = "QuestType_47_1031681D471E9929E30B17985E174BBE",
    descriptions = "Descriptions_ByStep_20_CE78450B4708786A5D7D71BFE2D3DE0B",
    icon = "Icon_91_1888AD0747DA23F500530E9AAC14D830",
    images = "Images_ByStep_92_0AB9A29B482457A8FB719ABB327DDD56",
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B",
    objectives = "Objective_Items_56_9FD46BE9431873A188AED4B08F0AF083",
}
local slots = { backpack = "PlayerBackpack", equipment = "PlayerEquipment",
    armor = "PlayerArmor", helmet = "PlayerHelmet" }
local axes = {"A", "B", "C", "D"}
local max_size, max_rows = 20000, 128
local min_int32, max_int32, completed_step = -2147483648, 2147483647, -10
local journal_view
local reputation_view, reputation_hook

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function exact(object, path)
    return valid(object) and object:GetClass():GetAddress() == StaticFindObject(path):GetAddress()
end

local function stock_player_info(state)
    local stack = state.controller.MenuStack
    for index = math.min(stack:GetArrayNum(), 32), 1, -1 do
        local widget = stack[index]
        if widget and type(widget.type) == "function" and widget:type() == "RemoteUnrealParam" then
            widget = widget:get()
        end
        if exact(widget, "/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C") then
            return widget.PlayerInfoBox
        end
    end
end

local function encode(value)
    local text = type(value) == "userdata" and value:ToString() or value
    if type(text) ~= "string" or #text > 4096 then error("Invalid co-op Journal text", 0) end
    return text:gsub(".", function(char) return ("%02x"):format(char:byte()) end)
end

local function decode(value)
    if #value > 8192 or #value % 2 ~= 0 or value:find("[^0-9a-f]") then error("Invalid co-op Journal text", 0) end
    return FText((value:gsub("..", function(byte) return string.char(tonumber(byte, 16)) end)))
end

local function integer(value, minimum, maximum)
    local number = math.tointeger(tonumber(value))
    if not number or number < minimum or number > maximum then error("Invalid co-op view number", 0) end
    return number
end

local function capture_reputation_bonuses(instance, save, row)
    local helmet = save.PlayerHelmet[item.class]
    if not valid(helmet) then return end
    local bonuses = helmet:GetCDO().RepBonus
    if not bonuses or #bonuses == 0 then return end
    local factions = {}
    bonuses:ForEach(function(key)
        factions[#factions + 1] = integer(key:get(), 0, 255)
    end)
    table.sort(factions)
    for _, faction in ipairs(factions) do
        local result = {}
        instance:Add_RepBonus(0, faction, result)
        row[#row + 1] = faction
        row[#row + 1] = integer(result.Output, min_int32, max_int32)
    end
end

local function project_reputation_bonuses(instance, save, bonuses)
    if not reputation_hook then
        reputation_hook = RegisterHook(add_rep_bonus, function(context, points, faction, output)
            local view = reputation_view
            if not view or scope.selected() ~= view.owner then return end
            local current = context:get()
            if not valid(current) or current:GetAddress() ~= view.instance then return end
            local canonical = current.SaveSlot_Ref
            if not valid(canonical) or canonical:GetAddress() ~= view.save then return end
            -- Preserve the Blueprint's signed 32-bit addition semantics.
            local sum = (points:get() + (view.bonuses[faction:get()] or 0)) & 0xffffffff
            output:set(sum > max_int32 and sum - 0x100000000 or sum)
        end)
    end
    local owner, address, save_address = scope.selected(), instance:GetAddress(), save:GetAddress()
    local previous = reputation_view
    if previous and previous.owner == owner and previous.instance == address and previous.save == save_address then
        local changed = false
        for faction, bonus in pairs(bonuses) do changed = changed or previous.bonuses[faction] ~= bonus end
        for faction in pairs(previous.bonuses) do changed = changed or bonuses[faction] == nil end
        if not changed then return false end
    end
    reputation_view = {owner = owner, instance = address, save = save_address, bonuses = bonuses}
    return true
end

local function count(array)
    return integer(array:GetArrayNum(), 0, max_rows)
end

local function fields(array, wrap)
    local result = {}
    for index = 1, count(array) do result[index] = wrap(array[index]) end
    return result
end

local function item_path(class)
    local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass ([^%s]+)$")
    if content_paths.item_class(path, 512) then return path end
end

local function resolve_item_class(name)
    if not content_paths.item_class(name, 512) then return end
    local class = StaticFindObject(name)
    if not valid(class) then
        if not pcall(LoadAsset, name) then return end
        class = StaticFindObject(name)
    end
    local base = StaticFindObject(item_class)
    local defaults = item_path(class) and class:GetCDO()
    if valid(defaults) and valid(base) and defaults:IsA(base) then return class end
end

local function photo_name(texture)
    if not exact(texture, texture_class) then return "-" end
    local path = texture:GetFullName():match("^Texture2D (.+)$")
    if not path or path:sub(1, #quest_photos) ~= quest_photos then return "-" end
    local package, name = path:sub(#quest_photos + 1):match("^([%w_%-]+)%.([%w_%-]+)$")
    return package and package == name and #name <= 96 and name or "-"
end

local function photo(name)
    if name == "-" then return nil end
    if #name > 96 or not name:match("^[%w_%-]+$") then error("Invalid co-op Journal image", 0) end
    local path = quest_photos .. name .. "." .. name
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    if exact(object, texture_class) then return object end
end

local function append_quests(canonical, rows)
    local address = canonical:GetAddress()
    if not journal_view or journal_view.address ~= address then
        journal_view = {address = address, sequence = 0, steps = {}, completed = {}}
    end
    journal_view.sequence = journal_view.sequence + 1
    local steps, completed, quests = {}, {}, {}
    canonical.ActiveQuests:ForEach(function(raw_key, raw_value)
        local id, entry = raw_key:get():ToString(), raw_value:get()
        if not content_paths.quest_id(id) then return end
        local ok, step = pcall(function()
            return integer(quest_catalog.tracked_step(canonical, entry), min_int32, max_int32)
        end)
        if not ok then return end
        if step == completed_step then
            -- A quest can start and complete between two guest journal captures.
            completed[id] = journal_view.steps[id] ~= completed_step and journal_view.sequence or journal_view.completed[id]
        end
        steps[id] = step
        quests[#quests + 1] = {id = id, step = step, finished = completed[id] or 0}
    end)
    journal_view.steps, journal_view.completed = steps, completed
    table.sort(quests, function(first, second)
        if (first.step > 0) ~= (second.step > 0) then return first.step > 0 end
        if first.finished ~= second.finished then return first.finished > second.finished end
        return first.id < second.id
    end)

    local size, included = 0, 0
    for _, row in ipairs(rows) do size = size + #row + 1 end
    for _, candidate in ipairs(quests) do
        if #rows >= max_rows or size >= max_size then break end
        local ok, frame = pcall(function()
            local key = FName(candidate.id, EFindName.FNAME_Find)
            local entry = canonical.ActiveQuests:Find(key):get()
            local images = fields(entry[quest.images], photo_name)
            local objectives = {}
            for index = 1, count(entry[quest.objectives]) do
                local path = item_path(entry[quest.objectives][index])
                if path then objectives[#objectives + 1] = path end
            end
            local row = {#objectives > 0 and "quest-objectives" or "quest", candidate.id,
                integer(entry[quest.kind], 0, 255), candidate.step, integer(entry[quest.final], 0, max_int32),
                encode(entry[quest.name]), encode(entry[quest.giver]), photo_name(entry[quest.icon]), table.concat(images, ",")}
            if #objectives > 0 then row[#row + 1] = table.concat(objectives, ",") end
            for _, description in ipairs(fields(entry[quest.descriptions], encode)) do row[#row + 1] = description end
            return table.concat(row, "\t")
        end)
        if ok and size + #frame + 1 <= max_size then
            rows[#rows + 1], size = frame, size + #frame + 1
            included = included + 1
        end
    end
    local omitted = #canonical.ActiveQuests - included
    if omitted > 0 and omitted ~= journal_view.omitted then
        print(("[SilverfishCoop] Guest Journal omitted %d quest rows due to unsupported data or view limits\n"):format(omitted))
    end
    journal_view.omitted = omitted
end

local function capture(state, phase, pawn_name)
    local pawn, save, character = guest_save.exact_item_context(state, phase, pawn_name)
    local host = state.local_pawn and state.local_pawn.pawn
    local instance = state.world.OwningGameInstance
    local canonical = instance.SaveSlot_Ref
    if phase ~= "hosting" or not exact(save, save_class) or not exact(canonical, save_class)
        or not valid(host) or not valid(host.Save) or host.Save:GetAddress() ~= canonical:GetAddress()
        or not character or not pawn or pawn:GetFullName() ~= pawn_name then
        error("The co-op view requires an authenticated guest save and host Journal", 0)
    end
    local pack_size, equipment_size = count(save.PlayerBackpack), count(save.PlayerEquipment)
    local selected = integer(save.PlayerEquipIndex, -1, math.max(equipment_size - 1, 0))
    local cash = FName("Player_Cash")
    local balance = canonical.Flags:Contains(cash) and canonical.Flags:Find(cash):get() or 0
    local rows = {table.concat({state.map, character}, "\t"),
        table.concat({"inventory-layout", pack_size, equipment_size, selected}, "\t"),
        table.concat({"cash", integer(balance, min_int32, max_int32)}, "\t")}
    local checked, character_row = pcall(function()
        local character = save.Character
        local name = character[creator.name_field]:ToString()
        if name ~= "" then
            return table.concat({"character", encode(name), appearance.observe(character), creator.observe(character)}, "\t")
        end
    end)
    if checked and character_row then rows[#rows + 1] = character_row end
    local reputation_available, reputation = pcall(native_world.reputation_flags, canonical)
    if reputation_available then
        local row = {"reputation"}
        for _, flag in ipairs(reputation) do
            row[#row + 1] = flag[1]
            row[#row + 1] = flag[2]
        end
        rows[#rows + 1] = table.concat(row, "\t")
        local bonuses = {"reputation-bonuses"}
        capture_reputation_bonuses(instance, canonical, bonuses)
        rows[#rows + 1] = table.concat(bonuses, "\t")
    end
    for _, key in ipairs({"backpack", "equipment", "armor", "helmet"}) do
        local source = save[slots[key]]
        local array = key == "backpack" or key == "equipment"
        local total = array and count(source) or 1
        for index = 1, total do
            local entry = array and source[index] or source
            local class = entry and entry[item.class]
            if valid(class) then
                local class_path = item_path(class)
                local guid, pieces = entry[item.guid], {}
                if not class_path then error("Invalid co-op inventory class", 0) end
                for j, name in ipairs(axes) do pieces[j] = tostring(guid[name]) end
                rows[#rows+1] = table.concat({"item", key, index, class_path, entry[item.ammo],
                    entry[item.weight], entry[item.value], entry[item.no_drop] and 1 or 0, table.unpack(pieces)}, "\t")
            end
        end
    end
    for i = 1, count(canonical.JournalEntries) do
        local entry = canonical.JournalEntries[i]
        rows[#rows+1] = table.concat({"note", encode(entry[notes.header]), photo_name(entry[notes.image]),
            table.unpack(fields(entry[notes.body], encode))}, "\t")
    end
    append_quests(canonical, rows)
    local contents = table.concat(rows, "\n") .. "\n"
    if #rows > max_rows or #contents > max_size then error("The co-op view exceeds its transfer bounds", 0) end
    return contents
end

local function item_row(columns)
    local name = columns[4]
    if #columns ~= 12 or not slots[columns[2]] then error("Invalid co-op inventory row", 0) end
    local class = resolve_item_class(name)
    if not class then error("Invalid co-op item class", 0) end
    local guid, weight = {}, tonumber(columns[6])
    if not weight or not (weight >= 0 and weight <= 100000) then error("Invalid co-op item weight", 0) end
    for index, axis in ipairs(axes) do guid[axis] = integer(columns[index+8], -2147483648, 4294967295) end
    return {[item.class] = class, [item.ammo] = integer(columns[5], -1, 2147483647), [item.weight] = weight,
        [item.value] = integer(columns[7], -2147483648, 2147483647),
        [item.no_drop] = integer(columns[8], 0, 1) == 1, [item.guid] = guid}
end

local function different_item(before, after)
    local previous, incoming = before[item.guid], after[item.guid]
    for _, axis in ipairs(axes) do
        if previous[axis] ~= incoming[axis] then return true end
    end
    return false
end

local function project(state, phase, character, path, raw, sequence)
    local old, pawn, id = scope.bind_guest(state, phase)
    if not old or character:gsub("-", "") ~= id then error("The co-op view belongs to another guest", 0) end
    if inventory_grant.pending(state, phase) or not inventory_view.accept_layout_projection(pawn, sequence) then
        return "stock personal inventory update pending"
    end
    if path then
        local file = io.open(path, "rb")
        if not file then error("The co-op view mailbox is unavailable", 0) end
        raw = file:read(max_size + 1)
        file:close()
    end
    if not raw or #raw > max_size or raw:sub(-1) ~= "\n" then error("Invalid co-op view payload", 0) end
    local api, class = StaticFindObject(statics), StaticFindObject(save_class)
    if not valid(api) or not valid(class) then error("The stock co-op view class is unavailable", 0) end
    local view = api:CreateSaveGameObject(class)
    if not exact(view, save_class) then error("The stock co-op view could not be allocated", 0) end
    local processed, items, note_count, quest_count = 0, 0, 0, 0
    local completed, attachments, reputation, balance = 0, 0, nil, nil
    local reputation_bonuses
    local layout, item_positions = nil, {backpack = {}, equipment = {}}
    for line in raw:gmatch("([^\n]+)\n") do
        processed = processed + 1
        if processed > max_rows then error("The co-op view contains too many rows", 0) end
        local row = {}
        for field in (line .. "\t"):gmatch("([^\t]*)\t") do row[#row+1] = field end
        if processed == 1 then
            if #row ~= 2 or row[1] ~= state.map or row[2] ~= character then error("The co-op view is stale", 0) end
        elseif row[1] == "inventory-layout" and #row == 4 and not layout and processed == 2 then
            layout = {
                backpack = integer(row[2], 0, max_rows),
                equipment = integer(row[3], 0, max_rows),
            }
            layout.selected = integer(row[4], -1, math.max(layout.equipment - 1, 0))
        elseif row[1] == "character" and #row == 4 then
            local name = decode(row[2]):ToString()
            creator.install(view, row[4], name)
            if row[3] ~= "-" then appearance.install(view.Character, row[3]) end
        elseif row[1] == "cash" and #row == 2 and balance == nil then
            balance = integer(row[2], min_int32, max_int32)
        elseif row[1] == "reputation" and not reputation and #row <= 65 and #row % 2 == 1 then
            reputation = {}
            for index = 2, #row, 2 do
                local key = row[index]
                if #key > 96 or not key:match("^Rep_[A-Za-z0-9_%-]+$") or reputation[key] ~= nil then
                    error("Invalid co-op reputation flag", 0)
                end
                reputation[key] = integer(row[index + 1], min_int32, max_int32)
            end
        elseif row[1] == "reputation-bonuses" and not reputation_bonuses and #row <= 513 and #row % 2 == 1 then
            reputation_bonuses = {}
            for index = 2, #row, 2 do
                local faction = integer(row[index], 0, 255)
                if reputation_bonuses[faction] ~= nil then error("Duplicate co-op reputation bonus", 0) end
                reputation_bonuses[faction] = integer(row[index + 1], min_int32, max_int32)
            end
        elseif row[1] == "item" then
            local entry = item_row(row)
            local name = slots[row[2]]
            if row[2] == "backpack" or row[2] == "equipment" then
                if not layout then error("The co-op inventory layout is unavailable", 0) end
                local index = integer(row[3], 1, layout[row[2]])
                local positions = item_positions[row[2]]
                if positions[index] then error("The co-op inventory slot is duplicated", 0) end
                positions[index] = entry
            else
                view[name] = entry
            end
            items = items + 1
        elseif row[1] == "note" and #row >= 3 then
            note_count = note_count + 1
            local journal = view.JournalEntries
            local image = photo(row[3])
            journal[note_count] = {[notes.header] = decode(row[2]), [notes.image] = image}
            if image then attachments = attachments + 1 end
            local body = journal[note_count][notes.body]
            for i = 4, #row do body[i - 3] = decode(row[i]) end
        elseif (row[1] == "quest" and #row >= 9 or row[1] == "quest-objectives" and #row >= 10)
            and content_paths.quest_id(row[2]) then
            local key, quests = FName(row[2]), view.ActiveQuests
            local step = integer(row[4], min_int32, max_int32)
            local icon = photo(row[8])
            quests:Add(key, {[quest.name] = decode(row[6]), [quest.giver] = decode(row[7]),
                [quest.kind] = integer(row[3], 0, 255), [quest.step] = step,
                [quest.final] = integer(row[5], 0, max_int32), [quest.icon] = icon})
            if step == completed_step then completed = completed + 1 end
            if icon then attachments = attachments + 1 end
            local entry = quests:Find(key):get()
            local first_description = 10
            if row[1] == "quest-objectives" then
                first_description = 11
                local index = 0
                for path in (row[10] .. ","):gmatch("([^,]*),") do
                    local objective = resolve_item_class(path)
                    if objective then
                        index = integer(index + 1, 1, max_rows)
                        entry[quest.objectives][index] = objective
                    end
                end
            end
            local descriptions, images = entry[quest.descriptions], entry[quest.images]
            for i = first_description, #row do descriptions[i - first_description + 1] = decode(row[i]) end
            if row[9] ~= "" then
                local index = 0
                for name in (row[9] .. ","):gmatch("([^,]*),") do
                    index = integer(index + 1, 1, max_rows)
                    local image = photo(name)
                    images[index] = image
                    if image then attachments = attachments + 1 end
                end
            end
            quest_count = quest_count + 1
        else
            error("Invalid co-op Journal row", 0)
        end
    end
    if not layout or count(view.JournalEntries) ~= note_count or #view.ActiveQuests ~= quest_count then
        error("The stock co-op Journal view failed readback", 0)
    end
    for key, positions in pairs(item_positions) do
        local name = slots[key]
        view[name] = {}
        local array = view[name]
        for index = 1, layout[key] do array[index] = positions[index] or {} end
        if count(array) ~= layout[key] then error("The stock co-op inventory layout failed readback", 0) end
    end
    if type(SilverfishPreserveInventoryGrantPayloads) == "function" then
        local path = view:GetFullName():match("^%S+ (.+)$")
        if not path then error("The staged inventory projection is unavailable", 0) end
        local preserved, reason = SilverfishPreserveInventoryGrantPayloads(pawn:GetAddress(), old:GetAddress(), path)
        if not preserved then error(reason, 0) end
    end
    local selected = old.PlayerEquipIndex
    local projected_selection = layout.selected
    if selected ~= projected_selection and pawn.Dead == false and players.is_downed(pawn) == false then
        -- An inventory reply can predate a local quick-slot switch. Keep the live selection while its exact row is present.
        projected_selection = guest_weapon.local_projected_selection(pawn, old, view.PlayerEquipment) or projected_selection
    end
    local cleared_slot = selected == projected_selection and selected >= 0 and selected < count(old.PlayerEquipment)
        and valid(old.PlayerEquipment[selected + 1][item.class]) and not item_positions.equipment[selected + 1]
    -- Magazine updates use the live equipment snapshot. A slower inventory
    -- response must leave that count in place for the same held item.
    guest_weapon.preserve_magazine(pawn, old, view.PlayerEquipment, projected_selection)
    local helmet_changed = different_item(old.PlayerHelmet, view.PlayerHelmet)
    local armor_changed = different_item(old.PlayerArmor, view.PlayerArmor)
    for _, name in pairs(slots) do old[name] = view[name] end
    old.PlayerEquipIndex = projected_selection
    if old.PlayerEquipIndex ~= projected_selection then error("The stock co-op equipped slot failed readback", 0) end
    if cleared_slot then guest_weapon.clear_empty_slot_hold(pawn, old) end
    if view.Character[creator.name_field]:ToString() ~= "" then
        local old_voice, old_pitch = creator.voice(old.Character)
        local voice, pitch = creator.voice(view.Character)
        old.Character = view.Character
        if old_voice ~= voice or old_pitch ~= pitch then pawn:Compile_VoxSetup() end
    end
    old.JournalEntries = view.JournalEntries
    old.ActiveQuests = view.ActiveQuests
    if balance ~= nil and not wallet.project_guest(old, balance) then
        error("The attached co-op wallet failed readback", 0)
    end
    local reputation_changed = false
    if reputation then
        local flags, previous = old.Flags, {}
        flags:ForEach(function(raw)
            local key = raw:get():ToString()
            if key:match("^Rep_[A-Za-z0-9_%-]+$") and reputation[key] == nil then
                previous[#previous + 1] = key
            end
        end)
        for _, key in ipairs(previous) do
            flags:Remove(FName(key))
            reputation_changed = true
        end
        for key, points in pairs(reputation) do
            local flag = FName(key)
            if not flags:Contains(flag) or flags:Find(flag):get() ~= points then
                flags:Add(flag, points)
                if not flags:Contains(flag) or flags:Find(flag):get() ~= points then
                    error("The attached co-op reputation failed readback", 0)
                end
                reputation_changed = true
            end
        end
    end
    if count(old.JournalEntries) ~= note_count or #old.ActiveQuests ~= quest_count then
        error("The attached co-op Journal view failed readback", 0)
    end
    local instance = state.world.OwningGameInstance
    local bonuses_changed = reputation_bonuses and project_reputation_bonuses(instance, old, reputation_bonuses)
    if helmet_changed then instance:UpdateHelmet() end
    if armor_changed then instance:UpdateArmor() end
    if helmet_changed or armor_changed or reputation_changed or bonuses_changed then pawn:Configure_Disguise() end
    -- The stock weight update compiles passives after resolving player zero.
    -- Keep the explicit refresh if that lookup cannot reach this bound pawn.
    local stock_player = api:GetPlayerCharacter(state.world, 0)
    if not valid(stock_player) or stock_player:GetAddress() ~= pawn:GetAddress() then
        instance:Compile_PassiveEffects()
    end
    instance:Calc_CarryWeight()
    local info = stock_player_info(state)
    if valid(info) then
        info:RefreshPassives()
        info:UpdateStats()
    end
    return ("stock co-op inventory and Journal projected %d %d %d %d %d"):format(
        items, note_count, quest_count, completed, attachments)
end

local function matches_health(current, expected)
    return type(current) == "number" and math.abs(current - expected) <= 0.0001
end

local function project_health(fields, state, phase)
    local area, session, epoch, token, name = fields[3], fields[4], fields[5], fields[6], fields[7]
    local health, maximum = tonumber(fields[8]), tonumber(fields[9])
    local food, regen, stim = tonumber(fields[10]), tonumber(fields[11]), tonumber(fields[12])
    local fatigue, intox, sickness = math.tointeger(fields[13]), math.tointeger(fields[14]), math.tointeger(fields[15])
    local rads = tonumber(fields[16])
    if (#fields ~= 9 and #fields ~= 11 and #fields ~= 12 and #fields ~= 14 and #fields ~= 15 and #fields ~= 16)
        or phase ~= "connected" or state.map ~= area or scope.selected() ~= token
        or not health or not maximum or not (1 <= maximum and maximum <= 1000000 and 0 <= health and health <= maximum)
        or #fields >= 11 and ((fields[10] ~= "-" and not (food and 0 <= food and food <= 200))
            or (fields[11] ~= "-" and not (regen and 0 <= regen and regen <= maximum)))
        or #fields >= 12 and fields[12] ~= "-" and not (stim and 0 <= stim and stim <= 1000000)
        or #fields >= 14 and ((fields[13] ~= "-" and not (fatigue and 0 <= fatigue and fatigue <= 100))
            or (fields[14] ~= "-" and not (intox and 0 <= intox and intox <= 100)))
        or #fields >= 15 and fields[15] ~= "-" and not (sickness and -2^31 <= sickness and sickness < 2^31)
        or #fields == 16 and fields[16] ~= "-" and not (rads and 0 <= rads and rads <= 1000000)
        or type(SilverfishReadNativeClientDamageScope) ~= "function" then
        error("The authenticated stock health view is unavailable", 0)
    end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(pawn) or pawn:GetFullName() ~= name or pawn.Dead ~= false
        or SilverfishReadNativeClientDamageScope(pawn:GetAddress(), session, epoch) ~= true then
        error("The stock health view belongs to another guest or visit", 0)
    end
    local save, bound, owner = scope.bind_guest(state, phase)
    if not valid(save) or owner ~= token or bound:GetAddress() ~= pawn:GetAddress() then
        error("The isolated stock health view is unavailable", 0)
    end
    local old_fatigue, old_intox, old_sickness = scope.personal_flags(save)
    local fatigue_changed, intox_changed = fatigue ~= nil and fatigue ~= old_fatigue, intox ~= nil and intox ~= old_intox
    local sickness_changed = sickness ~= nil and sickness ~= old_sickness
    if fatigue_changed then save.Flags:Add(FName("Player_Fatigue"), fatigue) end
    if intox_changed then save.Flags:Add(FName("Player_Intox"), intox) end
    if sickness_changed then save.Flags:Add(FName("Sickness"), sickness) end
    if intox ~= nil and pawn.Intoxication ~= intox then pawn.Intoxication = intox end
    local current_fatigue, current_intox, current_sickness = scope.personal_flags(save)
    if not matches_health(pawn.MaxHealth, maximum) then pawn.MaxHealth = maximum end
    if not matches_health(save.PlayerHealth, health) then save.PlayerHealth = health end
    local food_changed = food ~= nil and not matches_health(save.PlayerFood, food)
    if food_changed then save.PlayerFood = food end
    if regen ~= nil and not matches_health(save.PlayerRegen, regen) then save.PlayerRegen = regen end
    if stim ~= nil and not matches_health(save.PlayerStim, stim) then save.PlayerStim = stim end
    local radiation_changed = rads ~= nil and not matches_health(save.PlayerRads, rads)
    if radiation_changed then save.PlayerRads = rads end
    if not matches_health(pawn.MaxHealth, maximum) or not matches_health(save.PlayerHealth, health)
        or food ~= nil and not matches_health(save.PlayerFood, food)
        or regen ~= nil and not matches_health(save.PlayerRegen, regen)
        or stim ~= nil and not matches_health(save.PlayerStim, stim)
        or rads ~= nil and not matches_health(save.PlayerRads, rads)
        or fatigue ~= nil and current_fatigue ~= fatigue
        or sickness ~= nil and current_sickness ~= sickness
        or intox ~= nil and (current_intox ~= intox or pawn.Intoxication ~= intox) then
        error("The stock health view failed readback", 0)
    end
    if food_changed or fatigue_changed or intox_changed or sickness_changed or radiation_changed then
        local info = stock_player_info(state)
        if valid(info) then info:UpdateStats() end
    end
    return "stock co-op guest health projected"
end

function guest_ui.command(fields, state, phase, bridge)
    local op, target, token = fields[2], fields[3], fields[4]
    if op == "native-guest-health-project" then return project_health(fields, state, phase) end
    if op ~= "native-guest-ui-read" and op ~= "native-guest-ui-project" then return end
    if (#fields ~= 4 and (#fields ~= 5 or op ~= "native-guest-ui-project"))
        or not token or #token ~= 32 or not token:match("^[a-f0-9]+$") then
        error("Invalid co-op view request", 0)
    end
    local path = bridge .. "/native-guest-ui-" .. token .. ".tsv"
    if op == "native-guest-ui-read" then
        local contents = capture(state, phase, target)
        local file = io.open(path, "wb")
        if not file then error("The co-op view mailbox is unavailable", 0) end
        local written = file:write(contents)
        if not file:close() or not written then error("The co-op view was not written", 0) end
        return "captured co-op guest view"
    end
    return project(state, phase, target, path, nil, fields[5])
end

function guest_ui.is_memory_command(operation)
    return operation == "native-guest-ui-read-memory" or operation == "native-guest-ui-project-memory"
end

function guest_ui.memory_command(fields, state, phase, payload)
    if (#fields ~= 3 and (#fields ~= 4 or fields[2] ~= "native-guest-ui-project-memory"))
        or #fields[1] ~= 32 or not fields[1]:match("^[a-f0-9]+$") then
        error("Invalid resident co-op view request", 0)
    end
    if fields[2] == "native-guest-ui-read-memory" and payload == "" then
        return "captured co-op guest view", capture(state, phase, fields[3])
    end
    if fields[2] == "native-guest-ui-project-memory" and payload and #payload > 0 and #payload <= max_size
        and payload:sub(-1) == "\n" and not payload:find("[^\t\n -~]") then
        return project(state, phase, fields[3], nil, payload, fields[4])
    end
    error("Invalid resident co-op view payload", 0)
end

return guest_ui
