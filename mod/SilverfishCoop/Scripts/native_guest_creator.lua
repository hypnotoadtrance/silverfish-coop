local profile = {}
local tables = "/Game/Classes/Libraries/DataTables/"
local inv = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local editor = "/Game/Classes/GUI/Character_Editor_Widget.Character_Editor_Widget_C"
local armor_root = "/Game/Classes/Armor_Items/"
local idle_root = "/Game/Classes/NPCs/NPC_Assets/Animations/Anim_Idle/"
local animations = "/Game/Classes/NPCs/NPC_Assets/Animations/"
local patch_root = "/Game/Classes/NPCs/NPC_Assets/NPC_Materials/NPC_Textures/"
local materials = "/Game/Classes/NPCs/NPC_Assets/NPC_Materials/"
local starting_items = "Starting_Items_30_E5CF623041C2520E535C948426AB9126"
local starting_armor = "Starting_Armor_26_62DBA26E4A5E2A9843D8FB9F8A3D9B76"
local permanent = "PermanentEffects_42_E298D351429CF6DCAE0911A4D1EEDB00"
local positive = "Passives_Positive_160_E298D351429CF6DCAE0911A4D1EEDB00"
local negative = "Passives_Negative_165_C3D32CC74CE1CB554D397A9D0460B056"
local armors = "Starting_Armor_109_62DBA26E4A5E2A9843D8FB9F8A3D9B76"
local stance = "Display_Animation_86_3A0001A64AF07499EF01E2A856E38CDC"
local voice = "VoxRef_97_E223354141E070B2E1F41F9E2CB7BD58"
local pitch = "Vox_Pitch_100_3439A01A4F568098E2FC93ADB75FE9D7"
local badge = "Patch_71_0B651F4541683455D3912BAACCB36DF7"
local material = "Mat_Chr_Default_68_FA9429A2499A3E568FEA90A638511357"
profile.name_field = "Character_Name_5_326F50964910FDA86FAFF78BCA9DB92F"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"

local function valid(value) return value and value:IsValid() end
local function text(value) return type(value) == "userdata" and value:ToString() or value end
local function need(value, reason)
    if not value then error("Guest character creation: " .. reason, 0) end
    return value
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return need(valid(object) and object, "stock asset unavailable")
end

local function row(table_name, row_name)
    local table_asset = asset(tables .. table_name .. "." .. table_name)
    return need(table_asset:FindRow(row_name), "stock option unavailable")
end

local function leaf(object, root, suffix)
    if not valid(object) then return "-" end
    local path = object:GetFullName():match("^%S+ (.+)$")
    if path and path:sub(1, #root) == root then
        local package, export = path:sub(#root + 1):match("^([%w_]+)%.([%w_]+)$")
        if package and export == package .. (suffix or "") then return package end
    end
    error("Guest character creation: unexpected stock asset", 0)
end

local function array_names(values)
    local result = {}
    for index = 1, values:GetArrayNum() do result[index] = text(values[index]) end
    return result
end

function profile.observe(character, recipe)
    local pitch_value = character[pitch]
    local traits = array_names(character[permanent])
    local idle = character[stance]
    local material_ok, model_material = pcall(leaf, character[material], materials)
    return table.concat({recipe or "-", text(character[voice]), string.format("%.4f", pitch_value),
        valid(idle) and idle:GetFName():ToString() == "Idle_Unarmed" and leaf(idle, animations) or leaf(idle, idle_root),
        leaf(character[starting_armor], armor_root, "_C"), leaf(character[badge], patch_root), table.concat(traits, ","),
        material_ok and model_material or "-"}, "|")
end

function profile.voice(character)
    local personality, level = text(character[voice]), character[pitch]
    if type(personality) == "string" and personality:match("^%w[%w_]*$") and #personality <= 30
        and personality ~= "None" and personality ~= "Mute" and level > 0 and level <= 3 then
        return personality, level
    end
end

local function parse(model)
    local fields = {}
    for field in (model .. "|"):gmatch("([^|]*)|") do fields[#fields + 1] = field end
    need((#fields == 7 or #fields == 8) and #model <= 1000, "invalid profile length")
    fields[8] = fields[8] or "-"
    local recipe = fields[1]
    need(recipe == "-" or recipe:match("^kit[0-4]$") or recipe:match("^preset_[A-Za-z0-9_]+$"), "invalid starting kit")
    for _, index in ipairs({2, 4, 5, 6, 8}) do
        need(fields[index] == "-" or (#fields[index] <= 80 and fields[index]:match("^[A-Za-z0-9_]+$")),
            "invalid stock option")
    end
    local value = tonumber(fields[3])
    need(value and value >= 0 and value <= 3, "invalid voice pitch")
    local traits, seen = {}, {}
    if fields[7] ~= "" then
        for trait in (fields[7] .. ","):gmatch("([^,]*),") do
            need(#traits < 16 and #trait <= 64 and trait:match("^[A-Za-z0-9_]+$"), "invalid trait")
            if not seen[trait] then traits[#traits+1], seen[trait] = trait, true end
        end
    end
    return fields, traits
end

local function allowed_armor(options, candidate)
    local allowed = false
    options[armors]:ForEach(function(_, value)
        local class = value:get()
        if valid(class) and class:GetAddress() == candidate:GetAddress() then allowed = true end
    end)
    return allowed
end

local function character_fields(character, fields, traits, name, validate_creation)
    local options = row("CharacterCustomization_Options_DT", "Defaults")
    local recipe, personality, raw_pitch, idle, armor, patch = table.unpack(fields, 1, 6)
    local preset_name = recipe:match("^preset_(.+)$")
    local preset = preset_name and row("PlayerClasses_DT", preset_name) or nil
    local names = {}
    local preset_traits = {}
    if preset then
        for _, trait in ipairs(array_names(preset[permanent])) do preset_traits[trait] = true end
    end
    for index, trait in ipairs(traits) do
        local key = FName(trait, EFindName.FNAME_Find)
        if validate_creation then
            need(key:ToString() == trait and (options[positive]:Contains(key)
                or options[negative]:Contains(key) or preset_traits[trait]), "trait is outside the stock choices")
        end
        names[index] = key
    end
    if personality ~= "None" and personality ~= "-" then
        row("Player_Vox_DT", personality)
        character[voice] = FName(personality, EFindName.FNAME_Find)
    end
    character[pitch] = tonumber(raw_pitch)
    if idle ~= "-" then
        local root = idle == "Idle_Unarmed" and animations or idle_root
        local animation = asset(root .. idle .. "." .. idle)
        need(animation:IsA(StaticFindObject("/Script/Engine.AnimationAsset")), "invalid stance")
        character[stance] = animation
    end
    if patch ~= "-" then
        local texture = asset(patch_root .. patch .. "." .. patch)
        need(patch:match("^Patch_") and texture:IsA(StaticFindObject("/Script/Engine.Texture2D")), "invalid patch")
        character[badge] = texture
    end
    if fields[8] ~= "-" then
        local name = fields[8]
        local skin = asset(materials .. name .. "." .. name)
        need(skin:IsA(StaticFindObject("/Script/Engine.MaterialInterface")), "invalid outfit material")
        character[material] = skin
    end
    if armor ~= "-" then
        local class = asset(armor_root .. armor .. "." .. armor .. "_C")
        if validate_creation then
            local default = preset and preset[starting_armor]
            need((valid(default) and default:GetAddress() == class:GetAddress()) or allowed_armor(options, class),
                "armor is outside the stock choices")
        end
        character[starting_armor] = class
    else
        character[starting_armor] = nil
    end
    character[permanent] = names
    character[profile.name_field] = FText(name)
    need(table.concat(array_names(character[permanent]), ",") == table.concat(traits, ",")
        and text(character[profile.name_field]) == name, "character fields did not survive stock readback")
    return preset
end

local function kit(recipe, preset)
    if preset then return preset[starting_items] end
    if recipe == "kit0" then return {} end
    if recipe == "kit1" or recipe == "kit4" then return asset(editor):GetCDO()["Basic Kit"] end
    local bag = recipe == "kit3" and "HyperRandom_GearBag_Starter" or "Random_GearBag_Starter"
    return {asset("/Game/Classes/Items/" .. bag .. "." .. bag .. "_C")}
end

function profile.install(save, model, name, pawn)
    local fields, traits = parse(model)
    local preset = character_fields(save.Character, fields, traits, name, pawn ~= nil)
    if not pawn then return end
    need(fields[1] ~= "-", "missing starting kit")
    local factory = asset(inv)
    local function make(class)
        local out = {}
        factory:MakeDefaultItem(class, pawn, out)
        local item = out.Item or out
        need(valid(item[item_class]) and item[item_class]:GetAddress() == class:GetAddress(),
            "stock starter item could not be created")
        return item
    end
    local armor = save.Character[starting_armor]
    if valid(armor) then save.PlayerArmor = make(armor) end
    local backpack, classes = save.PlayerBackpack, kit(fields[1], preset)
    local total = type(classes) == "table" and #classes or classes:GetArrayNum()
    need(total <= 32, "stock kit exceeds its item limit")
    for index = 1, total do
        local class = classes[index]
        local path = valid(class) and class:GetFullName():match("^BlueprintGeneratedClass (.+)$")
        -- Presets can include contracts or keys tied to their solo starting faction.
        if path and not path:find("/Key_Items/", 1, true) then
            backpack[backpack:GetArrayNum() + 1] = make(class)
        end
    end
end

return profile
