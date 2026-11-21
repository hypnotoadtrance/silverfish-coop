local view = {}

local asset_fields = {
    {"Mesh_Head_60_630BB776410363C64BE2C89A3F72B1C5", "/Script/Engine.StaticMesh"},
    {"Mesh_Plumage_80_4B6C3DD94D27DEC901D6829472D5F546", "/Script/Engine.StaticMesh"},
    {"Mesh_SK_Nude_63_AD8A03C54B40D56768EBA7AAA1E67E6D", "/Script/Engine.SkeletalMesh"},
    {"Mesh_Tail_SM_74_4FD875FB4D8D71A94E98888810B6EA64", "/Script/Engine.StaticMesh"},
    {"Mesh_Tail_SK_77_F2B7A1A441682020D88D3AA95D84A47E", "/Script/Engine.SkeletalMesh"},
    {"ScalePattern_89_1A2997C4406AB83801A3CAA377A49D3A", "/Script/Engine.Texture2D"},
}
local color_fields = {
    "Color_Skin_53_0B756DAC41D1CF33A2BB2480770F4445",
    "Color_Plumage_57_ACF2841E464A4898A5B12393FB251ADD",
    "Color_Eyes_55_02AD749F4DFCA95AC2AF72B4F045099B",
}
local eye_field = "EyeType_83_470E782541AF25166B548AA28A834625"
local npc_root = "/Game/Classes/NPCs/NPC_Assets/"
local material_root = npc_root .. "NPC_Materials/"
local model_root = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/"
local eye_tracker = npc_root .. "NPM_EyeTracker_BP.NPM_EyeTracker_BP_C"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function stock_asset(path, index)
    if path == "-" then return true end
    if #path > 192 then return false end
    local folder, package, name = path:match("^(/Game/Classes/[%w_/]+/)([%w_]+)%.([%w_]+)$")
    if not folder or package ~= name then return false end
    if index <= 2 then return folder == model_root end
    if index <= 5 then return folder:sub(1, #npc_root) == npc_root or folder == model_root end
    return folder == material_root .. "NPC_Textures/"
end

local function color_valid(value)
    return type(value) == "number" and value >= 0 and value <= 8
end

local function color_from_text(text)
    local color = {}
    for value in (text .. ","):gmatch("([^,]*),") do
        if not value:match("^%d%.%d%d%d%d%d$") then return end
        local number = tonumber(value)
        if not color_valid(number) then return end
        color[#color + 1] = number
    end
    if #color == 4 then return { R = color[1], G = color[2], B = color[3], A = color[4] } end
end

function view.decode(model)
    if model == "-" then return {assets = {false, false, false, false, false, false}, colors = {}} end
    if type(model) ~= "string" or #model > 1280 then return end
    local fields = {}
    for value in (model .. "|"):gmatch("([^|]*)|") do fields[#fields + 1] = value end
    if #fields ~= 10 then return end
    local cosmetics = { assets = {}, colors = {} }
    for index, field in ipairs(asset_fields) do
        local path = fields[index]
        if not stock_asset(path, index) then return end
        cosmetics.assets[index] = path ~= "-" and path or false
    end
    for index in ipairs(color_fields) do
        local value = fields[6 + index]
        if value ~= "-" then
            local color = color_from_text(value)
            if not color then return end
            cosmetics.colors[index] = color
        end
    end
    if fields[10] ~= "-" then
        local index = tonumber(fields[10])
        if not fields[10]:match("^%d%d?$") or not index or index < 0 or index > 20 then return end
        cosmetics.eye = index
    end
    return cosmetics
end

function view.observe(character)
    if not character then return "-" end
    local fields = {}
    for index, field in ipairs(asset_fields) do
        local object = character[field[1]]
        local path = valid(object) and object:GetFullName():match("^%S+ (%S+)$")
        fields[index] = path and stock_asset(path, index) and path or "-"
    end
    for index, field in ipairs(color_fields) do
        local color = character[field]
        local value = "-"
        if color and color_valid(color.R) and color_valid(color.G) and color_valid(color.B) and color_valid(color.A)
            and color.R + color.G + color.B + color.A > 0 then
            value = string.format("%.5f,%.5f,%.5f,%.5f", color.R, color.G, color.B, color.A)
        end
        fields[6 + index] = value
    end
    local eye = math.tointeger(character[eye_field])
    fields[10] = eye and eye >= 0 and eye <= 20 and tostring(eye) or "-"
    return table.concat(fields, "|")
end

local function checked_asset(path, index, asset)
    if not path then return end
    local loaded = asset(path)
    if valid(loaded) and loaded:IsA(StaticFindObject(asset_fields[index][2])) then return loaded end
end

local function set_mesh(component, mesh, skeletal)
    if skeletal then component:SetSkeletalMeshAsset(mesh) else component:SetStaticMesh(mesh) end
    local installed = skeletal and component.SkinnedAsset or component.StaticMesh
    if not valid(installed) or installed:GetAddress() ~= mesh:GetAddress() then
        error("Stock character appearance did not pass mesh readback")
    end
end

local function paint(component, index, source, parameter, value, pattern_param, pattern)
    local material = component:CreateDynamicMaterialInstance(index, source, FName("None", EFindName.FNAME_Find))
    if not valid(material) then error("Stock character material could not be instanced") end
    if value then material:SetVectorParameterValue(FName(parameter, EFindName.FNAME_Find), value) end
    if valid(pattern) then material:SetTextureParameterValue(FName(pattern_param, EFindName.FNAME_Find), pattern) end
end

local function tint(component, colors)
    local slots = {}
    for index = 0, component:GetNumMaterials() - 1 do
        local material = component:GetMaterial(index)
        local base = valid(material) and material:GetBaseMaterial()
        if base and base:IsValid() then
            local name = base:GetFullName():match("%.([%w_]+)$")
            local color = name == "M_NPC_Scales_Master" and colors[1] or name == "M_NPC_Feathers_Master" and colors[2]
            if color then
                paint(component, index, material, "Color", color)
                slots[#slots + 1] = index
            end
        end
    end
    return slots
end

function view.clear_body(entry)
    for _, index in ipairs(entry.body_tint_slots or {}) do entry.body:SetMaterial(index, nil) end
    entry.body_tint_slots = nil
end

function view.clear(entry)
    for _, key in ipairs({"plumage", "tail_static", "tail_skeletal"}) do
        local component = entry[key]
        if valid(component) and valid(entry.pawn) then component:K2_DestroyComponent(entry.pawn) end
        entry[key] = nil
    end
end

function view.body(cosmetics, asset)
    return checked_asset(cosmetics.assets[3], 3, asset)
end

function view.apply(entry, cosmetics, model, asset, add_component)
    if entry.appearance == model and entry.appearance_body == entry.body_worn and entry.appearance_helmet == entry.head_worn then return end
    local paths, colors = cosmetics.assets, cosmetics.colors
    local changed = entry.appearance ~= model
    if changed then
        view.clear(entry)
        local face = checked_asset(paths[1], 1, asset) or entry.default_head
        for index = 0, entry.head:GetNumMaterials() - 1 do entry.head:SetMaterial(index, nil) end
        set_mesh(entry.head, face, false)
        local face_base = asset(material_root .. "M_NPC_Face_Master.M_NPC_Face_Master")
        local face_material = entry.head:GetMaterial(0)
        local pattern = checked_asset(paths[6], 6, asset)
        if valid(face_material) and valid(face_base) and valid(face_material:GetBaseMaterial())
            and face_material:GetBaseMaterial():GetAddress() == face_base:GetAddress() and (colors[1] or pattern) then
            paint(entry.head, 0, face_base, "Color", colors[1], "ScalePattern", pattern)
        end
        local eyes = entry.head:GetMaterialIndex(FName("Eyes", EFindName.FNAME_Find))
        if eyes >= 0 and (colors[3] or cosmetics.eye) then
            local eyes_base = asset(material_root .. "M_NPC_Eyes_Master.M_NPC_Eyes_Master")
            local tracker = asset(eye_tracker)
            local defaults = valid(tracker) and tracker:GetCDO()
            local outlines = valid(defaults) and defaults.Eye_Outlines
            local index = cosmetics.eye and cosmetics.eye + 1
            local outline = index and outlines and #outlines >= index and outlines[index] or nil
            if type(outline) == "userdata" and type(outline.type) == "function" and outline:type() == "RemoteUnrealParam" then
                outline = outline:get()
            end
            if valid(eyes_base) then paint(entry.head, eyes, eyes_base, "IrisColor", colors[3], "Eye_Outline", outline) end
        end
        local plume = checked_asset(paths[2], 2, asset)
        if plume then
            set_mesh(add_component(entry, "plumage", entry.head), plume, false)
            tint(entry.plumage, colors)
        end
        local tail_static = checked_asset(paths[4], 4, asset)
        local tail_skeletal = checked_asset(paths[5], 5, asset)
        if tail_static then
            set_mesh(add_component(entry, "tail_static", entry.body, "TailSocket_01"), tail_static, false)
            tint(entry.tail_static, colors)
        elseif tail_skeletal then
            set_mesh(add_component(entry, "tail_skeletal", entry.body, "TailSocket_01", true), tail_skeletal, true)
            tint(entry.tail_skeletal, colors)
        end
    end
    if valid(entry.plumage) then entry.plumage:SetVisibility(not entry.hide_plumage, false) end
    if changed or entry.appearance_body ~= entry.body_worn or entry.body_tint_slots == nil then
        view.clear_body(entry)
        entry.body_tint_slots = tint(entry.body, colors)
    end
    entry.appearance, entry.appearance_body, entry.appearance_helmet = model, entry.body_worn, entry.head_worn
end

return view
