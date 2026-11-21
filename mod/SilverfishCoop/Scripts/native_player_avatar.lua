local guest_save = require("guest_save")
local avatar = {}

local body_path = "/Game/Classes/NPCs/NPC_Assets/CharacterModels/Player_NoArmor.Player_NoArmor"
local head_path = "/Game/Classes/Player/PlayerAssets/HelmetMeshes/SM_Head_UV_01.SM_Head_UV_01"
local head_class = "/Script/Engine.StaticMeshComponent"
local identity = {
    Rotation = { X = 0, Y = 0, Z = 0, W = 1 },
    Translation = { X = 0, Y = 0, Z = 0 },
    Scale3D = { X = 1, Y = 1, Z = 1 },
}
local avatars = {}
local observations, client_outfits = {}, {}
local client_world, client_receipt
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local armor_root = "/Game/Classes/Armor_Items/"

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return object
end

local function player_id(details)
    local state = details.pawn.PlayerState
    if not valid(state) and valid(details.controller_object) then state = details.controller_object.PlayerState end
    if not valid(state) then return end
    local ok, value = pcall(function() return state:GetPlayerId() end)
    if not ok or value == nil then value = state.PlayerId end
    if type(value) == "number" and math.tointeger(value) and value >= 0 and value <= 2147483647 then
        return math.tointeger(value)
    end
end

local function outfit_path(path)
    local package, class = path:match("^/Game/Classes/Armor_Items/([%w_]+)%.([%w_]+)_C$")
    return #path <= 160 and (path == "-" or package ~= nil and package == class)
end

local function worn(row)
    local class = row and row[item_class]
    local path = valid(class) and class:GetFullName():match("^%S+ (%S+)$")
    return path and outfit_path(path) and path or "-"
end

local function observe(state, phase)
    observations = {}
    if phase ~= "hosting" then return end
    local known = {}
    for _, details in ipairs(state.pawns) do
        local ok, id = pcall(player_id, details)
        if ok and id ~= nil then
            if known[id] then
                observations[id] = nil
            else
                known[id] = true
                local read, body, head = pcall(function()
                    local pawn, save = details.pawn
                    if pawn:IsLocallyControlled() then
                        save = state.world.OwningGameInstance.SaveSlot_Ref
                        if not pawn:HasAuthority() or not valid(save) or not valid(pawn.Save)
                            or pawn.Save:GetAddress() ~= save:GetAddress() then return end
                    else
                        pawn, save = guest_save.exact_item_context(state, phase, details.name)
                    end
                    if valid(pawn) and valid(save) then return worn(save.PlayerArmor), worn(save.PlayerHelmet) end
                end)
                if read and body then observations[id] = { pawn = details.name, body = body, head = head } end
            end
        end
    end
end

local function mesh_component(entry, mesh)
    local component = entry.pawn:AddComponentByClass(StaticFindObject(head_class), true, identity, true)
    if not valid(component) then error("Player head or helmet component could not be created") end
    local field = mesh and "head" or "helmet"
    component:SetCollisionEnabled(0)
    component:SetGenerateOverlapEvents(false)
    component:SetMobility(2)
    entry.pawn:FinishAddComponent(component, true, identity)
    local socket = FName("HelmetSocket", EFindName.FNAME_Find)
    if not component:K2_AttachToComponent(entry.body, socket, 2, 2, 0, false) then
        component:K2_DestroyComponent(entry.pawn)
        error("Player head or helmet component could not attach to its stock socket")
    end
    entry[field] = component
    if mesh then component:SetStaticMesh(mesh) end
    return component
end

local function remove(entry)
    if valid(entry.helmet) and valid(entry.pawn) then
        entry.helmet:K2_DestroyComponent(entry.pawn)
    end
    if valid(entry.head) and valid(entry.pawn) then
        entry.head:K2_DestroyComponent(entry.pawn)
    end
    if entry.installed_body and valid(entry.body) then
        if entry.material_override then entry.body:SetMaterial(0, nil) end
        entry.body:SetVisibility(entry.visible, false)
        entry.body:SetHiddenInGame(entry.hidden, false)
        entry.body:SetSkeletalMeshAsset(entry.original_asset)
        entry.body:SetCollisionEnabled(entry.collision)
        entry.body:SetGenerateOverlapEvents(entry.overlap)
    end
end

local function create(entry)
    local body = entry.pawn.Mesh
    if not valid(body) then error("Player body component was unavailable") end
    entry.body = body
    entry.original_asset = body.SkinnedAsset
    if valid(entry.original_asset) then
        entry.status = "stock_body"
        return
    end
    local body_asset, head_asset = asset(body_path), asset(head_path)
    local component_type = StaticFindObject(head_class)
    if not valid(body_asset) or not valid(head_asset) or not valid(component_type) then
        error("Stock avatar assets were unavailable")
    end
    entry.visible, entry.hidden = body.bVisible, body.bHiddenInGame
    entry.default_asset = body_asset
    entry.collision, entry.overlap = body:GetCollisionEnabled(), body:GetGenerateOverlapEvents()
    entry.installed_body = true
    body:SetCollisionEnabled(0)
    body:SetGenerateOverlapEvents(false)
    body:SetSkeletalMeshAsset(body_asset)
    body:SetHiddenInGame(false, false)
    body:SetVisibility(true, false)

    local head = mesh_component(entry, head_asset)
    if not valid(body.SkinnedAsset) or not valid(head.StaticMesh) or not body:IsVisible() then
        error("Stock avatar did not pass mesh readback")
    end
    entry.status = "default_body"
end

local function outfit_mesh(path, base_name, field, mesh_class)
    if path == "-" then return end
    local base = asset(armor_root .. base_name .. "." .. base_name .. "_C")
    local class = asset(path)
    if not valid(base) or not valid(class) then return end
    local defaults = class:GetCDO()
    if not valid(defaults) or not defaults:IsA(base) then return end
    local mesh = defaults[field]
    if not valid(mesh) or not mesh:IsA(StaticFindObject(mesh_class)) then return end
    return mesh, defaults
end

local function apply_outfit(entry, outfit)
    local body_path = outfit and outfit.body or "-"
    local head_path = outfit and outfit.head or "-"
    if entry.request_body ~= body_path then
        local mesh, defaults = outfit_mesh(body_path, "Item_ArmorMaster_BP", "SkeliMesh", "/Script/Engine.SkeletalMesh")
        local target = mesh or entry.default_asset
        local material = defaults and defaults.Default_Material
        entry.body:SetSkeletalMeshAsset(target)
        if valid(material) and material:IsA(StaticFindObject("/Script/Engine.MaterialInterface")) then
            entry.body:SetMaterial(0, material)
            entry.material_override = true
        elseif entry.material_override then
            entry.body:SetMaterial(0, nil)
            entry.material_override = nil
        end
        if not valid(entry.body.SkinnedAsset) or entry.body.SkinnedAsset:GetAddress() ~= target:GetAddress() then
            error("Stock armor mesh did not pass readback")
        end
        entry.body_worn = mesh and body_path or "-"
        entry.request_body = body_path
    end
    if entry.request_head ~= head_path then
        local mesh = outfit_mesh(head_path, "Item_HelmetMaster_BP", "HelmMesh", "/Script/Engine.StaticMesh")
        if mesh then
            local component = valid(entry.helmet) and entry.helmet or mesh_component(entry)
            component:SetStaticMesh(mesh)
            component:SetVisibility(true, false)
            if not valid(component.StaticMesh) or component.StaticMesh:GetAddress() ~= mesh:GetAddress() then
                error("Stock helmet mesh did not pass readback")
            end
        elseif valid(entry.helmet) then
            entry.helmet:SetVisibility(false, false)
        end
        entry.head_worn = mesh and head_path or "-"
        entry.request_head = head_path
    end
end

function avatar.sync(state, phase)
    local world = phase == "connected" and state.world:GetAddress() or nil
    if client_world ~= world then client_world, client_outfits, client_receipt = world, {}, nil end
    observe(state, phase)
    local outfits = phase == "hosting" and observations or client_outfits
    local seen = {}
    if (phase == "hosting" or phase == "connected") and state.local_pawn then
        for _, details in ipairs(state.guests) do
            local pawn = details.pawn
            if valid(pawn) and not pawn:IsActorBeingDestroyed() and not pawn:IsLocallyControlled() then
                local address = pawn:GetAddress()
                seen[address] = true
                local entry = avatars[address]
                if not entry or not valid(entry.pawn) or entry.name ~= details.name then
                    entry = { pawn = pawn, name = details.name, map = state.map }
                    avatars[address] = entry
                    local ok, failure = pcall(create, entry)
                    if not ok then
                        pcall(remove, entry)
                        entry.body, entry.head, entry.installed_body = nil, nil, false
                        entry.status, entry.failure = "unavailable", tostring(failure)
                    end
                end
                if entry.status == "default_body" then
                    local ok, failure = pcall(function()
                        entry.id = player_id(details)
                        apply_outfit(entry, outfits[entry.id])
                    end)
                    entry.failure = not ok and tostring(failure) or nil
                end
            end
        end
    end
    for address, entry in pairs(avatars) do
        if not seen[address] then
            pcall(remove, entry)
            avatars[address] = nil
        end
    end
end

function avatar.command(fields, state, phase)
    if fields[2] ~= "native-avatar-outfits" then return end
    local receipt = fields[4]
    if phase ~= "connected" or fields[3] ~= state.map or not receipt or #receipt ~= 32
        or not receipt:match("^[0-9a-f]+$") or (#fields - 4) % 3 ~= 0 or #fields > 19 then
        error("Invalid remote avatar outfit scope")
    end
    local outfits = {}
    for index = 5, #fields, 3 do
        local id = math.tointeger(tonumber(fields[index]))
        local body, head = fields[index + 1], fields[index + 2]
        if not id or id < 0 or id > 2147483647 or outfits[id] or not outfit_path(body) or not outfit_path(head) then
            error("Invalid remote avatar outfit")
        end
        outfits[id] = { body = body, head = head }
    end
    client_world, client_outfits, client_receipt = state.world:GetAddress(), outfits, receipt
    return "Remote avatar outfits received"
end

function avatar.rows(row)
    local rows = {}
    for _, entry in pairs(avatars) do
        rows[#rows + 1] = row("native-player-avatar", entry.map, entry.name, entry.status, entry.failure or "",
            entry.id or "-", entry.body_worn or "-", entry.head_worn or "-")
    end
    for id, outfit in pairs(observations) do
        rows[#rows + 1] = row("native-avatar-outfit", id, outfit.pawn, outfit.body, outfit.head)
    end
    if client_receipt then rows[#rows + 1] = row("native-avatar-outfits-receipt", client_receipt) end
    return rows
end

function avatar.visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.status == "default_body"
end

return avatar
