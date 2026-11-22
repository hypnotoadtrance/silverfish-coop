local view = {}

local equipment_type = "/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local static_type = "/Script/Engine.StaticMeshComponent"
local skeletal_type = "/Script/Engine.SkeletalMeshComponent"
local scene_type = "/Script/Engine.SceneComponent"
local statics_type = "/Script/Engine.Default__GameplayStatics"
local math_library = "/Script/Engine.Default__KismetMathLibrary"
local bat_mesh = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleeWep_Models/SM_Wep_Bat_01.SM_Wep_Bat_01"
-- The bat mesh starts at the knob and extends along local Z. Put the handle
-- inside the hand socket and keep the orientation chosen by the stock rig.
local bat_grip = {X = 0, Y = 0, Z = 12}
local kinds = { s = {static_type, "StaticMesh", "/Script/Engine.StaticMesh"},
    k = {skeletal_type, "SkinnedAsset", "/Script/Engine.SkeletalMesh"} }
local maximum_parts = 12
local active_recoil = {}
local kick_time = .05
local identity = {
    Rotation = { X = 0, Y = 0, Z = 0, W = 1 },
    Translation = { X = 0, Y = 0, Z = 0 },
    Scale3D = { X = 1, Y = 1, Z = 1 },
}

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function stock_asset(path)
    if type(path) ~= "string" or #path > 192 then return false end
    local root, leaf, name = path:match("^/Game/(%a+)/[%w_/]+/([%w_]+)%.([%w_]+)$")
    return (root == "Classes" or root == "Assets") and leaf == name
end

local function valid_pose(values)
    for index = 1, 9 do
        local value = values[index]
        if not value or value ~= value or math.abs(value) > (index < 4 and 250 or index < 7 and 180 or 4)
            or (index >= 7 and math.abs(value) < .01) then
            return false
        end
    end
    return true
end

function view.decode(model)
    if model == "-" then return {} end
    if type(model) ~= "string" or #model > 4096 or model == "" then return end
    local parts = {}
    for encoded in (model .. "|"):gmatch("([^|]*)|") do
        local fields = {}
        for field in (encoded .. "@"):gmatch("([^@]*)@") do fields[#fields + 1] = field end
        if #parts >= maximum_parts or #fields ~= 11 or not kinds[fields[1]] or not stock_asset(fields[2]) then
            return
        end
        local pose = {}
        for index = 1, 9 do
            local number = fields[index + 2]
            if not number:match("^%-?%d%d?%d?%.%d%d$") then return end
            pose[index] = tonumber(number)
        end
        if not valid_pose(pose) then return end
        parts[#parts + 1] = { kind = fields[1], path = fields[2], pose = pose }
    end
    return parts
end

local function encode_pose(component, math_api, root_transform)
    local transform = math_api:MakeRelativeTransform(component:K2_GetComponentToWorld(), root_transform)
    local rotation = math_api:Quat_Rotator(transform.Rotation)
    local location, scale = transform.Translation, transform.Scale3D
    local values = { location.X, location.Y, location.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, scale.X, scale.Y, scale.Z }
    if not valid_pose(values) then return end
    for index, value in ipairs(values) do values[index] = string.format("%.2f", value) end
    return table.concat(values, "@")
end

function view.observe(pawn, save, cached)
    local equipment = pawn.EquipedItem
    if not valid(equipment) or not same(equipment.User, pawn) or not same(equipment:GetWorld(), pawn:GetWorld())
        or not equipment:HasAuthority() or equipment:IsActorBeingDestroyed() or not same(equipment:GetAttachParentActor(), pawn) then
        return "-", nil
    end
    local selected, slots = math.tointeger(save.PlayerEquipIndex), save.PlayerEquipment
    local row = selected and slots and selected >= 0 and selected < slots:GetArrayNum() and slots[selected + 1]
    if not row or not same(row[item_class], equipment:GetClass()) then return "-", nil end
    if cached and same(equipment, cached.actor) then return cached.model, cached end
    local class = StaticFindObject(equipment_type)
    local root, math_api = equipment.RootComponent, StaticFindObject(math_library)
    if not valid(class) or not equipment:IsA(class) or not valid(root) or not valid(math_api) then return "-", nil end
    local root_transform, parts = root:K2_GetComponentToWorld(), {}
    for _, kind in ipairs({"s", "k"}) do
        local profile = kinds[kind]
        local class = StaticFindObject(profile[1])
        if not valid(class) then return "-", nil end
        local components = equipment:K2_GetComponentsByClass(class)
        for index = 1, #components do
            local component = components[index]
            if type(component) == "userdata" and type(component.type) == "function" and component:type() == "RemoteUnrealParam" then
                component = component:get()
            end
            if valid(component) and same(component:GetOwner(), equipment) and component:IsVisible()
                and component.bHiddenInGame == false then
                local asset = component[profile[2]]
                local path = valid(asset) and asset:GetFullName():match("^%S+ (%S+)$")
                local pose = path and stock_asset(path) and encode_pose(component, math_api, root_transform)
                if pose and #parts < maximum_parts then parts[#parts + 1] = table.concat({kind, path, pose}, "@") end
            end
        end
    end
    if #parts == 0 then return "-", nil end
    table.sort(parts)
    local model = table.concat(parts, "|")
    return model, { actor = equipment, model = model }
end

function view.clear(entry)
    active_recoil[entry] = nil
    for _, component in ipairs(entry.weapon_parts or {}) do
        if valid(component) and valid(entry.pawn) then component:K2_DestroyComponent(entry.pawn) end
    end
    if valid(entry.weapon_anchor) and valid(entry.pawn) then entry.weapon_anchor:K2_DestroyComponent(entry.pawn) end
    entry.weapon_parts, entry.weapon_model, entry.weapon_anchor = nil, nil, nil
end

local function align_bat_grip(component, body, socket)
    local math_api = StaticFindObject(math_library)
    if not valid(math_api) then error("Stock bat grip transform is unavailable") end
    local target = body:GetSocketLocation(socket)
    local origin = component:K2_GetComponentLocation()
    local grip = math_api:TransformLocation(component:K2_GetComponentToWorld(), bat_grip)
    component:K2_SetWorldLocation({X = origin.X + target.X - grip.X, Y = origin.Y + target.Y - grip.Y,
        Z = origin.Z + target.Z - grip.Z}, false, {}, true)
    local attached = math_api:TransformLocation(component:K2_GetComponentToWorld(), bat_grip)
    if (attached.X - target.X)^2 + (attached.Y - target.Y)^2 + (attached.Z - target.Z)^2 > .25 then
        error("Stock bat grip could not align with the hand")
    end
end

local function attach(entry, parts)
    local socket = FName("Weapon_Socket01", EFindName.FNAME_Find)
    if not entry.body:DoesSocketExist(socket) then error("Stock weapon hand socket was unavailable") end
    local parent, none = entry.body, FName("None", EFindName.FNAME_Find)
    if entry.firearm_class ~= "-" then
        local anchor = entry.pawn:AddComponentByClass(StaticFindObject(scene_type), true, identity, true)
        if not valid(anchor) then error("Held firearm anchor could not be created") end
        entry.weapon_anchor = anchor
        anchor:SetMobility(2)
        entry.pawn:FinishAddComponent(anchor, true, identity)
        if not anchor:K2_AttachToComponent(entry.body, socket, 2, 2, 0, false) then
            error("Held firearm anchor could not attach to the stock hand socket")
        end
        parent = anchor
    end
    for _, part in ipairs(parts) do
        local profile = kinds[part.kind]
        local asset = StaticFindObject(part.path)
        if not valid(asset) then
            LoadAsset(part.path)
            asset = StaticFindObject(part.path)
        end
        if not valid(asset) or not asset:IsA(StaticFindObject(profile[3])) then
            error("Stock held weapon mesh was unavailable")
        end
        local component = entry.pawn:AddComponentByClass(StaticFindObject(profile[1]), true, identity, true)
        if not valid(component) then error("Held weapon component could not be created") end
        entry.weapon_parts[#entry.weapon_parts + 1] = component
        component:SetCollisionEnabled(0)
        component:SetGenerateOverlapEvents(false)
        component:SetMobility(2)
        entry.pawn:FinishAddComponent(component, true, identity)
        if not component:K2_AttachToComponent(parent, parent == entry.body and socket or none, 2, 2, 0, false) then
            error("Held weapon component could not attach to the stock hand socket")
        end
        local p = part.pose
        component:K2_SetRelativeLocationAndRotation({X = p[1], Y = p[2], Z = p[3]},
            {Pitch = p[4], Yaw = p[5], Roll = p[6]}, false, {}, true)
        component:SetRelativeScale3D({X = p[7], Y = p[8], Z = p[9]})
        if part.kind == "s" then component:SetStaticMesh(asset) else component:SetSkeletalMeshAsset(asset) end
        if part.path == bat_mesh and part.kind == "s" then align_bat_grip(component, entry.body, socket) end
        component:SetHiddenInGame(false, false)
        component:SetVisibility(true, false)
        if not same(component[profile[2]], asset) or not component:IsVisible() then
            error("Held weapon component did not pass mesh readback")
        end
    end
end

function view.apply(entry, model, firearm)
    firearm = firearm or "-"
    if entry.weapon_model == model and entry.firearm_class == firearm then return end
    view.clear(entry)
    local parts = view.decode(model)
    if not parts then error("Invalid stock held weapon model") end
    entry.firearm_class = firearm
    entry.weapon_parts = {}
    if #parts > 0 then
        local ok, failure = pcall(attach, entry, parts)
        if not ok then
            view.clear(entry)
            error(failure, 0)
        end
    end
    entry.weapon_model = model
end

function view.recoil(entry, world, path, serial, values)
    local anchor = entry.weapon_anchor
    if entry.firearm_class ~= path or not valid(anchor) then return false end
    local clock = StaticFindObject(statics_type)
    if not valid(clock) then return false end
    local offset, rotation = anchor.RelativeLocation, anchor.RelativeRotation
    local prior = entry.last_recoil
    local record = {world = world:GetAddress(), serial = serial, start = clock:GetRealTimeSeconds(world),
        weapon = path, peak = 0, angle = 0, count = prior and prior.count + 1 or 1,
        from = {offset.X, offset.Y, offset.Z, rotation.Pitch, rotation.Yaw, rotation.Roll}, to = values}
    active_recoil[entry], entry.last_recoil = record, record
    return true
end

function view.animate_recoil(get_world)
    if not next(active_recoil) then return end
    local world, clock = get_world(), StaticFindObject(statics_type)
    if not valid(world) or not valid(clock) then return end
    local now, scope = clock:GetRealTimeSeconds(world), world:GetAddress()
    for entry, recoil in pairs(active_recoil) do
        local anchor, elapsed = entry.weapon_anchor, now - recoil.start
        if not valid(anchor) or scope ~= recoil.world then
            active_recoil[entry] = nil
        else
            local pose = {}
            for index = 1, 6 do
                pose[index] = elapsed < kick_time
                    and recoil.from[index] + (recoil.to[index] - recoil.from[index]) * math.max(0, elapsed) / kick_time
                    or recoil.to[index] * math.max(0, 1 - (elapsed - kick_time) / recoil.to[7])
            end
            anchor:K2_SetRelativeLocationAndRotation({X = pose[1], Y = pose[2], Z = pose[3]},
                {Pitch = pose[4], Yaw = pose[5], Roll = pose[6]}, false, {}, true)
            local offset, rotation = anchor.RelativeLocation, anchor.RelativeRotation
            recoil.peak = math.max(recoil.peak, math.sqrt(offset.X^2 + offset.Y^2 + offset.Z^2))
            recoil.angle = math.max(recoil.angle, math.abs(rotation.Pitch), math.abs(rotation.Yaw), math.abs(rotation.Roll))
            if elapsed >= kick_time + recoil.to[7] then
                active_recoil[entry] = nil
                recoil.restored = math.abs(offset.X) + math.abs(offset.Y) + math.abs(offset.Z) < .001
                    and math.abs(rotation.Pitch) + math.abs(rotation.Yaw) + math.abs(rotation.Roll) < .001
            end
        end
    end
end

function view.recoil_row(entry, row)
    local recoil = entry.last_recoil
    if not recoil then return end
    return row("native-avatar-recoil", entry.name, recoil.weapon, recoil.serial, recoil.count,
        recoil.peak, recoil.angle, active_recoil[entry] and 1 or 0, recoil.restored and 1 or 0)
end

return view
