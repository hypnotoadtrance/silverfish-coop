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

local function remove(entry)
    if valid(entry.head) and valid(entry.pawn) then
        entry.head:K2_DestroyComponent(entry.pawn)
    end
    if entry.installed_body and valid(entry.body) then
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
    entry.collision, entry.overlap = body:GetCollisionEnabled(), body:GetGenerateOverlapEvents()
    entry.installed_body = true
    body:SetCollisionEnabled(0)
    body:SetGenerateOverlapEvents(false)
    body:SetSkeletalMeshAsset(body_asset)
    body:SetHiddenInGame(false, false)
    body:SetVisibility(true, false)

    local head = entry.pawn:AddComponentByClass(component_type, true, identity, true)
    if not valid(head) then error("Player head component could not be created") end
    entry.head = head
    head:SetCollisionEnabled(0)
    head:SetGenerateOverlapEvents(false)
    head:SetMobility(2)
    entry.pawn:FinishAddComponent(head, true, identity)
    local socket = FName("HelmetSocket", EFindName.FNAME_Find)
    if not head:K2_AttachToComponent(body, socket, 2, 2, 0, false) then
        error("Player head component could not attach to its stock socket")
    end
    head:SetStaticMesh(head_asset)
    if not valid(body.SkinnedAsset) or not valid(head.StaticMesh) or not body:IsVisible() then
        error("Stock avatar did not pass mesh readback")
    end
    entry.status = "default_body"
end

function avatar.sync(state, phase)
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

function avatar.rows(row)
    local rows = {}
    for _, entry in pairs(avatars) do
        rows[#rows + 1] = row("native-player-avatar", entry.map, entry.name, entry.status, entry.failure or "")
    end
    return rows
end

function avatar.visible(pawn)
    local entry = avatars[pawn:GetAddress()]
    return entry and entry.status == "default_body"
end

return avatar
