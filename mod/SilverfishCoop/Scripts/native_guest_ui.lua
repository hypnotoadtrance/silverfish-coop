local guest_ui = {}
local guest_save = require("guest_save")
local scope = require("native_save_scope")
local statics = "/Script/Engine.Default__GameplayStatics"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local item_class = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
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
}
local quest = {
    name = "DisplayName_2_7285E7CE446EB084E06391BFCE927182",
    giver = "QuestGiver_37_600BE36F48EDD8652F17A2B41BEFDEA0",
    kind = "QuestType_47_1031681D471E9929E30B17985E174BBE",
    descriptions = "Descriptions_ByStep_20_CE78450B4708786A5D7D71BFE2D3DE0B",
    step = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A",
    final = "Objective_FinalStep_57_F5A16DBF4E231A030BBD9F864F62A14B",
}
local slots = { backpack = "PlayerBackpack", equipment = "PlayerEquipment",
    armor = "PlayerArmor", helmet = "PlayerHelmet" }
local axes = {"A", "B", "C", "D"}
local max_size, max_rows = 20000, 128

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function exact(object, path)
    return valid(object) and object:GetClass():GetAddress() == StaticFindObject(path):GetAddress()
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

local function count(array)
    return integer(array:GetArrayNum(), 0, max_rows)
end

local function fields(array, wrap)
    local result = {}
    for index = 1, count(array) do result[index] = wrap(array[index]) end
    return result
end

local function capture(state, phase, pawn_name, path)
    local pawn, save, character = guest_save.exact_item_context(state, phase, pawn_name)
    local host = state.local_pawn and state.local_pawn.pawn
    local canonical = state.world.OwningGameInstance.SaveSlot_Ref
    if phase ~= "hosting" or not exact(save, save_class) or not exact(canonical, save_class)
        or not valid(host) or not valid(host.Save) or host.Save:GetAddress() ~= canonical:GetAddress()
        or not character or not pawn or pawn:GetFullName() ~= pawn_name then
        error("The co-op view requires an authenticated guest save and host Journal", 0)
    end
    local rows = {table.concat({state.map, character}, "\t")}
    for _, key in ipairs({"backpack", "equipment", "armor", "helmet"}) do
        local source = save[slots[key]]
        local array = key == "backpack" or key == "equipment"
        local total = array and count(source) or 1
        for index = 1, total do
            local entry = array and source[index] or source
            local class = entry and entry[item.class]
            if valid(class) then
                local class_path = class:GetFullName():match("^BlueprintGeneratedClass (/Game/[%w_./%-]+)$")
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
        rows[#rows+1] = table.concat({"note", encode(entry[notes.header]), table.unpack(fields(entry[notes.body], encode))}, "\t")
    end
    local quests = {}
    canonical.ActiveQuests:ForEach(function(key, value)
        local id, entry = key:get():ToString(), value:get()
        if not id:match("^[A-Za-z0-9_]+$") or #id > 96 then error("Invalid co-op quest identifier", 0) end
        quests[#quests+1] = table.concat({"quest", id, entry[quest.kind], entry[quest.step], entry[quest.final],
            encode(entry[quest.name]), encode(entry[quest.giver]), table.unpack(fields(entry[quest.descriptions], encode))}, "\t")
    end)
    table.sort(quests)
    for _, row in ipairs(quests) do rows[#rows+1] = row end
    local contents = table.concat(rows, "\n") .. "\n"
    if #rows > max_rows or #contents > max_size then error("The co-op view exceeds its transfer bounds", 0) end
    local file = io.open(path, "wb")
    if not file then error("The co-op view mailbox is unavailable", 0) end
    local written = file:write(contents)
    if not file:close() or not written then error("The co-op view was not written", 0) end
    return "captured co-op guest view"
end

local function item_row(columns)
    local name = columns[4]
    if #columns ~= 12 or not slots[columns[2]] or #name > 512 or not name:match("^/Game/[%w_./%-]+_C$")
        or name:find("..", 1, true) then error("Invalid co-op inventory row", 0) end
    local class = StaticFindObject(name)
    if not valid(class) then LoadAsset(name); class = StaticFindObject(name) end
    local stock = StaticFindObject(item_class)
    if not valid(class) or not valid(stock) or not class:GetCDO():IsA(stock) then error("Invalid co-op item class", 0) end
    local guid, weight = {}, tonumber(columns[6])
    if not weight or not (weight >= 0 and weight <= 100000) then error("Invalid co-op item weight", 0) end
    for index, axis in ipairs(axes) do guid[axis] = integer(columns[index+8], -2147483648, 4294967295) end
    return {[item.class] = class, [item.ammo] = integer(columns[5], -1, 2147483647), [item.weight] = weight,
        [item.value] = integer(columns[7], -2147483648, 2147483647),
        [item.no_drop] = integer(columns[8], 0, 1) == 1, [item.guid] = guid}
end

local function project(state, phase, character, path)
    local old, pawn, id = scope.bind_guest(state, phase)
    if not old or character:gsub("-", "") ~= id then error("The co-op view belongs to another guest", 0) end
    local file = io.open(path, "rb")
    if not file then error("The co-op view mailbox is unavailable", 0) end
    local raw = file:read(max_size + 1)
    file:close()
    if not raw or #raw > max_size or raw:sub(-1) ~= "\n" then error("Invalid co-op view payload", 0) end
    local api, class = StaticFindObject(statics), StaticFindObject(save_class)
    if not valid(api) or not valid(class) then error("The stock co-op view class is unavailable", 0) end
    local view = api:CreateSaveGameObject(class)
    if not exact(view, save_class) then error("The stock co-op view could not be allocated", 0) end
    local processed, items, note_count, quest_count = 0, 0, 0, 0
    for line in raw:gmatch("([^\n]+)\n") do
        processed = processed + 1
        if processed > max_rows then error("The co-op view contains too many rows", 0) end
        local row = {}
        for field in (line .. "\t"):gmatch("([^\t]*)\t") do row[#row+1] = field end
        if processed == 1 then
            if #row ~= 2 or row[1] ~= state.map or row[2] ~= character then error("The co-op view is stale", 0) end
        elseif row[1] == "item" then
            local entry = item_row(row)
            local name = slots[row[2]]
            if row[2] == "backpack" or row[2] == "equipment" then
                local array = view[name]
                array[array:GetArrayNum()+1] = entry
            else
                view[name] = entry
            end
            items = items + 1
        elseif row[1] == "note" and #row >= 2 then
            note_count = note_count + 1
            local journal = view.JournalEntries
            journal[note_count] = {[notes.header] = decode(row[2])}
            local body = journal[note_count][notes.body]
            for i = 3, #row do body[i - 2] = decode(row[i]) end
        elseif row[1] == "quest" and #row >= 7 and #row[2] <= 96 and row[2]:match("^[A-Za-z0-9_]+$") then
            local key, quests = FName(row[2]), view.ActiveQuests
            quests:Add(key, {[quest.name] = decode(row[6]), [quest.giver] = decode(row[7]),
                [quest.kind] = integer(row[3], 0, 255), [quest.step] = integer(row[4], -1, 100000),
                [quest.final] = integer(row[5], 0, 100000)})
            local descriptions = quests:Find(key):get()[quest.descriptions]
            for i = 8, #row do descriptions[i - 7] = decode(row[i]) end
            quest_count = quest_count + 1
        else
            error("Invalid co-op Journal row", 0)
        end
    end
    if processed == 0 or count(view.JournalEntries) ~= note_count or #view.ActiveQuests ~= quest_count then
        error("The stock co-op Journal view failed readback", 0)
    end
    for _, name in pairs(slots) do old[name] = view[name] end
    old.JournalEntries = view.JournalEntries
    old.ActiveQuests = view.ActiveQuests
    if count(old.JournalEntries) ~= note_count or #old.ActiveQuests ~= quest_count then
        error("The attached co-op Journal view failed readback", 0)
    end
    return ("stock co-op inventory and Journal projected %d %d %d"):format(items, note_count, quest_count)
end

function guest_ui.command(fields, state, phase, bridge)
    local op, target, token = fields[2], fields[3], fields[4]
    if op ~= "native-guest-ui-read" and op ~= "native-guest-ui-project" then return end
    if #fields ~= 4 or not token or #token ~= 32 or not token:match("^[a-f0-9]+$") then
        error("Invalid co-op view request", 0)
    end
    local path = bridge .. "/native-guest-ui-" .. token .. ".tsv"
    if op == "native-guest-ui-read" then return capture(state, phase, target, path) end
    return project(state, phase, target, path)
end

return guest_ui
