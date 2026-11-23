local view = {}
local lighter = require("native_avatar_lighter")
view.lighter_override = lighter.override
view.hide_lighter = lighter.hide

local equipment_type = "/Game/Classes/Equipment/Equipment_Master_BP.Equipment_Master_BP_C"
local firearm_type = "/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local static_type = "/Script/Engine.StaticMeshComponent"
local skeletal_type = "/Script/Engine.SkeletalMeshComponent"
local mesh_type = "/Script/Engine.MeshComponent"
local scene_type = "/Script/Engine.SceneComponent"
local statics_type = "/Script/Engine.Default__GameplayStatics"
local math_library = "/Script/Engine.Default__KismetMathLibrary"
local sound_attenuation = "/Game/Assets/Sounds/SoundClasses/SmallSound_Attenuation.SmallSound_Attenuation"
local bat_mesh = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Assets/MeleeWep_Models/SM_Wep_Bat_01.SM_Wep_Bat_01"
-- The bat mesh starts at the knob and extends along local Z. Put the handle
-- inside the hand socket and keep the orientation chosen by the stock rig.
local bat_grip = {X = 0, Y = 0, Z = 12}
local kinds = { s = {static_type, "StaticMesh", "/Script/Engine.StaticMesh"},
    k = {skeletal_type, "SkinnedAsset", "/Script/Engine.SkeletalMesh"} }
local rigs = {r = true, h = true}
local active_cycle = {}
local maximum_parts = 12
local minimum_scale = .01
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

local function unwrap(object)
    if type(object) == "userdata" and type(object.type) == "function" and object:type() == "RemoteUnrealParam" then
        return object:get()
    end
    return object
end

local function stock_asset(path)
    if type(path) ~= "string" or #path > 192 then return false end
    local root, leaf, name = path:match("^/Game/(%a+)/[%w_/%-]+/([%w_%-]+)%.([%w_%-]+)$")
    return (root == "Classes" or root == "Assets") and leaf == name
end

local function socket_name(socket)
    return type(socket) == "string" and #socket <= 64 and socket:match("^[%w_]+$")
end

local function valid_pose(values)
    for index = 1, 9 do
        local value = values[index]
        if not value or value ~= value or math.abs(value) > (index < 4 and 250 or index < 7 and 180 or 4)
            or (index >= 7 and math.abs(value) < minimum_scale) then
            return false
        end
    end
    return true
end

function view.decode(model)
    if model == "-" then return {} end
    if type(model) ~= "string" or #model > 4096 or model == "" then return end
    local shape, mode = lighter.split(model)
    local parts, has_rig, needs_rig = {}, false, false
    for encoded in (shape .. "|"):gmatch("([^|]*)|") do
        local fields = {}
        for field in (encoded .. "@"):gmatch("([^@]*)@") do fields[#fields + 1] = field end
        local kind, socket = fields[1], fields[12]
        if #parts >= maximum_parts or (#fields ~= 11 and #fields ~= 12) or not (kinds[kind] or rigs[kind])
            or not stock_asset(fields[2])
            or socket and (rigs[kind] or not socket_name(socket))
            or rigs[kind] and has_rig then
            return
        end
        has_rig, needs_rig = has_rig or rigs[kind] == true, needs_rig or socket ~= nil
        local pose = {}
        for index = 1, 9 do
            local number = fields[index + 2]
            if not number:match("^%-?%d%d?%d?%.%d%d$") then return end
            pose[index] = tonumber(number)
        end
        if not valid_pose(pose) then return end
        parts[#parts + 1] = {kind = kind, path = fields[2], pose = pose, socket = socket}
    end
    if needs_rig and not has_rig then return end
    if mode and (#parts ~= 2 or parts[1].kind ~= "s" or parts[2].kind ~= "s"
        or parts[1].socket or parts[2].socket or parts[1].path ~= lighter.body_path or parts[2].path ~= lighter.lid_path) then return end
    return parts, mode, shape
end

local function encode_pose(location, rotation, scale)
    local values = { location.X, location.Y, location.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, scale.X, scale.Y, scale.Z }
    -- Stock 0.01 socket scales can be stored as a float just below the wire minimum.
    for index = 7, 9 do
        local value = values[index]
        local magnitude = value and math.abs(value)
        if magnitude and magnitude < minimum_scale and magnitude >= minimum_scale - 1e-9 then
            values[index] = value < 0 and -minimum_scale or minimum_scale
        end
    end
    if not valid_pose(values) then return end
    for index, value in ipairs(values) do values[index] = string.format("%.2f", value) end
    return table.concat(values, "@")
end

local function root_pose(component, math_api, root_transform)
    local transform = math_api:MakeRelativeTransform(component:K2_GetComponentToWorld(), root_transform)
    return encode_pose(transform.Translation, math_api:Quat_Rotator(transform.Rotation), transform.Scale3D)
end

local function cycle_rig(equipment, root)
    local base = StaticFindObject(firearm_type)
    if not valid(base) or not equipment:IsA(base) then return end
    local rig = equipment.Wep_Skeli
    if not valid(rig) or not same(rig:GetOwner(), equipment) or not same(rig:GetAttachParent(), root) then return end
    local mesh = rig.SkinnedAsset
    local path = valid(mesh) and mesh:GetFullName():match("^%S+ (%S+)$")
    if not stock_asset(path) then return end
    -- The stock firearm returns its skeleton to this transform after recoil.
    local pose = encode_pose({X = 0, Y = 0, Z = 0}, {Pitch = 0, Yaw = -90, Roll = 0}, rig.RelativeScale3D)
    if not pose then return end
    local kind = rig:IsVisible() and not rig.bHiddenInGame and "r" or "h"
    return rig, table.concat({kind, path, pose}, "@")
end

local function equipped(pawn, save)
    local equipment = pawn.EquipedItem
    if not valid(equipment) or not same(equipment.User, pawn) or not same(equipment:GetWorld(), pawn:GetWorld())
        or not equipment:HasAuthority() or equipment:IsActorBeingDestroyed() or not same(equipment:GetAttachParentActor(), pawn) then
        return
    end
    local selected, slots = math.tointeger(save.PlayerEquipIndex), save.PlayerEquipment
    local row = selected and slots and selected >= 0 and selected < slots:GetArrayNum() and slots[selected + 1]
    if row and same(row[item_class], equipment:GetClass()) then return equipment end
end

function view.local_lighter(pawn)
    if not valid(pawn.Save) then return end
    local equipment = equipped(pawn, pawn.Save)
    if equipment and lighter.parts(equipment) then return lighter.state(equipment) end
end

function view.observe(pawn, save, cached)
    local equipment = equipped(pawn, save)
    if not equipment then return "-", nil end
    if cached and same(equipment, cached.actor) then
        local mode = cached.lighter and lighter.state(equipment)
        return mode and cached.model .. "~" .. mode or cached.model, cached
    end
    local class = StaticFindObject(equipment_type)
    local root, math_api = equipment.RootComponent, StaticFindObject(math_library)
    if not valid(class) or not equipment:IsA(class) or not valid(root) or not valid(math_api) then return "-", nil end
    local root_transform, parts = root:K2_GetComponentToWorld(), {}
    local body, lid = lighter.parts(equipment)
    if body then
        local body_pose, lid_pose = root_pose(body, math_api, root_transform), root_pose(lid, math_api, root_transform)
        local mode = lighter.state(equipment)
        if body_pose and lid_pose and mode then
            local model = "s@" .. lighter.body_path .. "@" .. body_pose .. "|s@" .. lighter.lid_path .. "@" .. lid_pose
            return model .. "~" .. mode, {actor = equipment, model = model, lighter = true}
        end
    end
    local rig, rig_description = cycle_rig(equipment, root)
    if rig then parts[1] = rig_description end
    for _, kind in ipairs({"s", "k"}) do
        local profile = kinds[kind]
        local class = StaticFindObject(profile[1])
        if not valid(class) then return "-", nil end
        local components = equipment:K2_GetComponentsByClass(class)
        for index = 1, #components do
            local component = unwrap(components[index])
            if valid(component) and not same(component, rig) and same(component:GetOwner(), equipment) and component:IsVisible()
                and component.bHiddenInGame == false then
                local asset = component[profile[2]]
                local path = valid(asset) and asset:GetFullName():match("^%S+ (%S+)$")
                if path and stock_asset(path) and #parts < maximum_parts then
                    local socket
                    if rig and same(component:GetAttachParent(), rig) then
                        local attachment = component:GetAttachSocketName()
                        local name = attachment:ToString()
                        if socket_name(name) and (name == "None" or rig:DoesSocketExist(attachment)) then socket = name end
                    end
                    local pose
                    if socket then
                        pose = encode_pose(component.RelativeLocation, component.RelativeRotation, component.RelativeScale3D)
                    end
                    if not pose then
                        socket = nil
                        pose = root_pose(component, math_api, root_transform)
                    end
                    if pose then parts[#parts + 1] = table.concat({kind, path, pose, socket}, "@") end
                end
            end
        end
    end
    if #parts == 0 or #parts == 1 and rig_description and rig_description:sub(1, 1) == "h" then return "-", nil end
    table.sort(parts)
    local model = table.concat(parts, "|")
    return model, { actor = equipment, model = model }
end

local function restore_stock(entry)
    for _, component in ipairs(entry.stock_weapon_masks or {}) do
        if valid(component) and component.bOnlyOwnerSee then component:SetOnlyOwnerSee(false) end
    end
    entry.stock_weapon_actor, entry.stock_weapon_masks = nil, nil
end

local function matches_displayed_mesh(entry, actor)
    local class = StaticFindObject(mesh_type)
    if not valid(class) then return false end
    local components = actor:K2_GetComponentsByClass(class)
    if not components or #components > 24 then return false end
    for index = 1, #components do
        local component = unwrap(components[index])
        if valid(component) and same(component:GetOwner(), actor) then
            for _, profile in pairs(kinds) do
                local component_type = StaticFindObject(profile[1])
                if valid(component_type) and component:IsA(component_type) then
                    local mesh = component[profile[2]]
                    if valid(mesh) and entry.weapon_meshes and entry.weapon_meshes[mesh:GetAddress()] then return true end
                end
            end
        end
    end
    return false
end

local function stock_held_actor(entry)
    local pawn, firearm = entry.pawn, entry.firearm_class
    local base = StaticFindObject(equipment_type)
    if not valid(base) then return end
    local function qualifies(actor)
        return valid(actor) and actor:IsA(base) and not actor:IsActorBeingDestroyed()
            and same(actor:GetAttachParentActor(), pawn)
            and (same(actor:GetOwner(), pawn) or same(actor.User, pawn))
            and (firearm == "-" or actor:GetClass():GetFullName() == "BlueprintGeneratedClass " .. firearm)
    end
    if qualifies(pawn.EquipedItem) then return pawn.EquipedItem end
    if qualifies(entry.stock_weapon_actor) then return entry.stock_weapon_actor end
    -- Unreal can replicate the attachment without the equipped-item reference.
    -- Non-firearms need a match to a mesh in the current observer's held model.
    local output = {}
    pawn:GetAttachedActors(output, true, false)
    local attached = output.OutActors or output
    if #attached > 24 then return end
    local result
    for index = 1, #attached do
        local actor = unwrap(attached[index])
        if qualifies(actor) and (firearm ~= "-" or matches_displayed_mesh(entry, actor)) then
            if result then return end
            result = actor
        end
    end
    return result
end

function view.suppress_stock(entry)
    local stock = #entry.weapon_parts > 0 and stock_held_actor(entry)
    if stock and same(stock, entry.stock_weapon_actor) then return end
    restore_stock(entry)
    if not stock then return end
    local class = StaticFindObject(mesh_type)
    if not valid(class) then return end
    local components = stock:K2_GetComponentsByClass(class)
    entry.stock_weapon_actor, entry.stock_weapon_masks = stock, {}
    for index = 1, #components do
        local component = unwrap(components[index])
        if valid(component) and same(component:GetOwner(), stock) and not component.bOnlyOwnerSee then
            -- Keep collision, actor replication, and the stock owner's own
            -- first-person meshes intact. Only this observer renders the copy.
            component:SetOnlyOwnerSee(true)
            entry.stock_weapon_masks[#entry.stock_weapon_masks + 1] = component
        end
    end
end

function view.clear(entry)
    lighter.clear(entry)
    restore_stock(entry)
    active_recoil[entry] = nil
    active_cycle[entry] = nil
    for _, component in ipairs(entry.weapon_parts or {}) do
        if valid(component) and valid(entry.pawn) then component:K2_DestroyComponent(entry.pawn) end
    end
    if valid(entry.weapon_anchor) and valid(entry.pawn) then entry.weapon_anchor:K2_DestroyComponent(entry.pawn) end
    entry.weapon_parts, entry.weapon_model, entry.weapon_anchor, entry.weapon_rig = nil, nil, nil, nil
    entry.weapon_shape, entry.weapon_meshes = nil, nil
    entry.weapon_stance = nil
    entry.weapon_slide = nil
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

local function attach(entry, parts, lighter_mode)
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
    local body, lid
    local function attach_part(part)
        local is_rig = rigs[part.kind]
        local profile = is_rig and kinds.k or kinds[part.kind]
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
        local target = part.socket and entry.weapon_rig or parent
        local attachment = part.socket and FName(part.socket, EFindName.FNAME_Find) or parent == entry.body and socket or none
        if not valid(target) or part.socket and part.socket ~= "None" and not target:DoesSocketExist(attachment)
            or not component:K2_AttachToComponent(target, attachment, 2, 2, 0, false) then
            error("Held weapon component could not attach to the stock hand socket")
        end
        local p = part.pose
        component:K2_SetRelativeLocationAndRotation({X = p[1], Y = p[2], Z = p[3]},
            {Pitch = p[4], Yaw = p[5], Roll = p[6]}, false, {}, true)
        component:SetRelativeScale3D({X = p[7], Y = p[8], Z = p[9]})
        if part.kind == "s" then component:SetStaticMesh(asset) else component:SetSkeletalMeshAsset(asset) end
        entry.weapon_meshes[asset:GetAddress()] = true
        if lighter_mode and part.path == lighter.body_path then body = component end
        if lighter_mode and part.path == lighter.lid_path then lid = component end
        if part.path == bat_mesh and part.kind == "s" then align_bat_grip(component, entry.body, socket) end
        local visible = part.kind ~= "h"
        component:SetHiddenInGame(not visible, false)
        component:SetVisibility(visible, false)
        if not same(component[profile[2]], asset) or component:IsVisible() ~= visible then
            error("Held weapon component did not pass mesh readback")
        end
        if is_rig then
            entry.weapon_rig = component
        elseif part.socket == "Wep_SlideSocket" then
            entry.weapon_slide = component
        end
    end
    for _, part in ipairs(parts) do
        if rigs[part.kind] then attach_part(part) end
    end
    for _, part in ipairs(parts) do
        if not rigs[part.kind] then attach_part(part) end
    end
    if lighter_mode then lighter.mount(entry, body, lid) end
end

function view.apply(entry, model, firearm)
    firearm = firearm or "-"
    if entry.weapon_model == model and entry.firearm_class == firearm then return end
    local parts, mode, shape = view.decode(model)
    if not parts or mode and firearm ~= "-" then error("Invalid stock held weapon model") end
    if mode and entry.lighter and entry.weapon_shape == shape and entry.firearm_class == firearm then
        lighter.apply(entry, mode)
        entry.weapon_model = model
        return
    end
    view.clear(entry)
    entry.firearm_class = firearm
    entry.weapon_parts, entry.weapon_meshes = {}, {}
    if #parts > 0 then
        local ok, failure = pcall(attach, entry, parts, mode)
        if not ok then
            view.clear(entry)
            error(failure, 0)
        end
        if firearm ~= "-" then
            local class = StaticFindObject(firearm)
            if not valid(class) then
                LoadAsset(firearm)
                class = StaticFindObject(firearm)
            end
            local defaults = valid(class) and class:GetCDO()
            local base = StaticFindObject(firearm_type)
            if valid(defaults) and valid(base) and defaults:IsA(base) then
                entry.weapon_stance = defaults.ItemType == 3 and "pistol" or "rifle"
            end
        end
    end
    if mode then lighter.apply(entry, mode) end
    entry.weapon_model, entry.weapon_shape = model, shape
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
    lighter.animate(get_world)
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

local function moving_bone(rig)
    local slide = FName("Wep_SlideSocket", EFindName.FNAME_Find)
    if rig:DoesSocketExist(slide) then return slide end
    for index = 0, math.min(48, rig:GetNumBones()) - 1 do
        local bone = rig:GetBoneName(index)
        local name = bone:ToString():lower()
        if name:find("bolt", 1, true) or name:find("slide", 1, true) or name:find("pump", 1, true) then
            return bone
        end
    end
end

local function mechanism_position(rig, slide, bone)
    if valid(slide) then
        local math_api = StaticFindObject(math_library)
        if valid(math_api) then
            return math_api:InverseTransformLocation(rig:K2_GetComponentToWorld(), slide:K2_GetComponentLocation())
        end
    end
    if bone then return rig:GetSocketTransform(bone, 2).Translation end
end

local function reload_sounds(weapon, clip, length)
    local class = StaticFindObject(weapon)
    local defaults = valid(class) and class:GetCDO()
    if not valid(defaults) or not (same(clip, defaults.Reload_Wet_Anim) or same(clip, defaults.Reload_Dry_Anim)) then
        return {}
    end
    local notify_class = StaticFindObject("/Script/Engine.AnimNotify_PlaySound")
    local notifies, sounds = clip.Notifies, {}
    for index = 1, math.min(notifies:GetArrayNum(), 64) do
        local event = notifies[index]
        local notify, at = event.Notify, event.LinkValue
        if same(valid(notify) and notify:GetClass() or nil, notify_class) and valid(notify.Sound)
            and event.LinkMethod == 0 and type(at) == "number" and at >= 0 and at <= length then
            sounds[#sounds + 1] = {at = at, sound = notify.Sound,
                volume = notify.VolumeMultiplier, pitch = notify.PitchMultiplier}
        end
    end
    table.sort(sounds, function(left, right) return left.at < right.at end)
    return sounds
end

function view.cycle(entry, world, weapon, serial, path)
    local rig = entry.weapon_rig
    if entry.firearm_class ~= weapon or not valid(rig) then return false end
    local clip = StaticFindObject(path)
    if not valid(clip) then
        LoadAsset(path)
        clip = StaticFindObject(path)
    end
    local mesh = rig.SkinnedAsset
    if not valid(clip) or not clip:IsA(StaticFindObject("/Script/Engine.AnimSequence"))
        or not valid(mesh) or not same(clip.Skeleton, mesh.Skeleton) then return false end
    local clock, length = StaticFindObject(statics_type), clip:GetPlayLength()
    if not valid(clock) or length <= 0 or length > 5 then return false end
    local previous = active_cycle[entry]
    local bone = previous and previous.bone or moving_bone(rig)
    local rest = previous and previous.rest or mechanism_position(rig, entry.weapon_slide, bone)
    local previous_tick = previous and previous.previous_tick or rig.VisibilityBasedAnimTickOption
    rig.VisibilityBasedAnimTickOption = 0
    rig:SetAnimationMode(1, false)
    rig:SetAnimation(clip)
    rig:Stop()
    rig:SetPosition(0, false)
    local read, sounds = pcall(reload_sounds, weapon, clip, length)
    local prior = entry.last_cycle
    local cycle = {world = world:GetAddress(), start = clock:GetRealTimeSeconds(world), duration = length,
        clip = path, weapon = weapon, serial = serial, count = prior and prior.count + 1 or 1, peak = 0,
        bone = bone, rest = rest, sounds = read and sounds or {}, sound_index = 1, previous_tick = previous_tick}
    active_cycle[entry], entry.last_cycle = cycle, cycle
    return true
end

local local_reload

local function clear_local_reload()
    local current = local_reload and active_cycle[local_reload]
    if current and valid(local_reload.weapon_rig) then
        local_reload.weapon_rig.VisibilityBasedAnimTickOption = current.previous_tick
    end
    if local_reload then active_cycle[local_reload] = nil end
    local_reload = nil
end

function view.local_reload(pawn, world, weapon, serial, path)
    local held = pawn.EquipedItem
    if not valid(held) or not same(held.User, pawn) or not same(held:GetWorld(), world)
        or held:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. weapon then return false end
    local rig, clip = held.Wep_Skeli, StaticFindObject(path)
    if not valid(rig) or not same(rig:GetOwner(), held) or not valid(clip)
        or not (same(clip, held.Reload_Wet_Anim) or same(clip, held.Reload_Dry_Anim)) then return false end
    if local_reload and not same(local_reload.weapon_rig, rig) then clear_local_reload() end
    local_reload = local_reload or {pawn = pawn, weapon_rig = rig, firearm_class = weapon}
    return view.cycle(local_reload, world, weapon, serial, path)
end

function view.animate_cycle(get_world)
    if not next(active_cycle) then return end
    local world, clock = get_world(), StaticFindObject(statics_type)
    if not valid(world) or not valid(clock) then return end
    local now, scope = clock:GetRealTimeSeconds(world), world:GetAddress()
    for entry, cycle in pairs(active_cycle) do
        local rig = entry.weapon_rig
        local local_view = entry == local_reload
        if not valid(rig) or scope ~= cycle.world
            or local_view and (not valid(entry.pawn) or not same(entry.pawn.EquipedItem, rig:GetOwner())) then
            if local_view then clear_local_reload() else active_cycle[entry] = nil end
        elseif cycle.finished then
            rig.VisibilityBasedAnimTickOption = cycle.previous_tick
            active_cycle[entry] = nil
            if local_view then local_reload = nil end
        else
            if cycle.rest then
                local point = mechanism_position(rig, entry.weapon_slide, cycle.bone)
                if point then
                    cycle.peak = math.max(cycle.peak,
                        math.sqrt((point.X - cycle.rest.X)^2 + (point.Y - cycle.rest.Y)^2 + (point.Z - cycle.rest.Z)^2))
                end
            end
            local elapsed = math.max(0, now - cycle.start)
            rig:SetPosition(math.min(elapsed, cycle.duration), false)
            local sound = cycle.sounds[cycle.sound_index]
            while sound and sound.at <= elapsed do
                local attenuation = StaticFindObject(sound_attenuation)
                if not valid(attenuation) then
                    LoadAsset(sound_attenuation)
                    attenuation = StaticFindObject(sound_attenuation)
                end
                if valid(attenuation) and valid(sound.sound) then
                    clock:PlaySoundAtLocation(entry.pawn, sound.sound, rig:K2_GetComponentLocation(),
                        {Pitch = 0, Yaw = 0, Roll = 0}, sound.volume, sound.pitch, 0, attenuation, nil, nil, nil)
                end
                cycle.sound_index = cycle.sound_index + 1
                sound = cycle.sounds[cycle.sound_index]
            end
            cycle.finished = elapsed >= cycle.duration
        end
    end
end

function view.cycle_row(entry, row)
    local cycle = entry.last_cycle
    if not cycle then return end
    return row("native-avatar-cycle", entry.name, cycle.weapon, cycle.clip, cycle.serial, cycle.count,
        cycle.peak, active_cycle[entry] and 1 or 0)
end

return view
