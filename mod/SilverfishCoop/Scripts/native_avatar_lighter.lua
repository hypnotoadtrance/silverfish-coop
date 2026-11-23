local lighter = {}

lighter.class = "/Game/Classes/Equipment/Eqp_Lighter.Eqp_Lighter_C"
local meshes = "/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/"
lighter.body_path = meshes .. "SM_Item_Lighter_Flip_01.SM_Item_Lighter_Flip_01"
lighter.lid_path = meshes .. "SM_Item_Lighter_Flip_Lid_01.SM_Item_Lighter_Flip_Lid_01"
local flame_path = "/Game/Assets/PFX/NI_LighterFlame.NI_LighterFlame"
local material_path = "/Game/Assets/Materials/Mat_Misc/LF_Light_Fire.LF_Light_Fire"
local sounds = "/Game/Assets/Sounds/CUEs/Equipment_Cues/"
local niagara_library = "/Script/Niagara.Default__NiagaraFunctionLibrary"
local light_type = "/Script/Engine.PointLightComponent"
local statics = "/Script/Engine.Default__GameplayStatics"
local closed, dark, lit = "closed", "open", "lit"
local modes = {[closed] = true, [dark] = true, [lit] = true}
local moving = {}
local lid_location = {X = -3.673602, Y = 0, Z = 3.346335}
local neutral_rotation = {Pitch = 0, Yaw = 0, Roll = 0}
local flame_location, light_location = {X = 0, Y = 0, Z = 9.447741}, {X = 0, Y = 0, Z = 7.354829}
local identity = {
    Rotation = {X = 0, Y = 0, Z = 0, W = 1},
    Translation = {X = 0, Y = 0, Z = 0},
    Scale3D = {X = 1, Y = 1, Z = 1},
}

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then
        LoadAsset(path)
        object = StaticFindObject(path)
    end
    return valid(object) and object or nil
end

function lighter.parts(equipment)
    if not same(equipment:GetClass(), StaticFindObject(lighter.class)) then return end
    local body, lid = equipment.SM_Item_Lighter_Flip_01, equipment.SM_Item_Lighter_Flip_Lid_01
    if not valid(body) or not valid(lid) or not same(body:GetOwner(), equipment) or not same(lid:GetOwner(), equipment)
        or not same(lid:GetAttachParent(), body)
        or not same(body.StaticMesh, StaticFindObject(lighter.body_path))
        or not same(lid.StaticMesh, StaticFindObject(lighter.lid_path)) then return end
    return body, lid
end

function lighter.state(equipment)
    if equipment.Open == false then return closed end
    if equipment.Open ~= true then return end
    local flame, light = equipment.FX_CandleFlame_01_Converted, equipment.PointLight
    if not valid(flame) or not valid(light) or not same(flame:GetOwner(), equipment) or not same(light:GetOwner(), equipment)
        or not same(flame.Asset, StaticFindObject(flame_path)) then return end
    return flame:IsVisible() and light:IsVisible() and lit or dark
end

function lighter.split(model)
    if type(model) == "string" then
        local shape, mode = model:match("^(.-)~(%l+)$")
        if modes[mode] then return shape, mode end
    end
    return model
end

function lighter.override(model, mode)
    local shape, prior = lighter.split(model)
    if prior and modes[mode] then return shape .. "~" .. mode end
    return model
end

local function extinguish(entry)
    local holder = entry.lighter
    if not holder then return end
    for _, component in pairs({holder.flame, holder.light}) do
        if valid(component) and valid(entry.pawn) then component:K2_DestroyComponent(entry.pawn) end
    end
    holder.flame, holder.light = nil, nil
end

function lighter.clear(entry)
    moving[entry] = nil
    extinguish(entry)
    entry.lighter = nil
end

function lighter.mount(entry, body, lid)
    if not valid(body) or not valid(lid) or not lid:K2_AttachToComponent(body, FName("None"), 2, 2, 0, false) then
        error("Stock lighter lid could not attach")
    end
    lid:SetRelativeScale3D(identity.Scale3D)
    entry.lighter = {body = body, lid = lid}
end

function lighter.hide(entry, hidden)
    local holder = entry.lighter
    if not holder or holder.hidden == hidden then return end
    holder.hidden = hidden
    for _, component in pairs({holder.flame, holder.light}) do
        if valid(component) then component:SetHiddenInGame(hidden, false) end
    end
end

local function ignite(entry)
    local holder = entry.lighter
    local particles, fire = StaticFindObject(niagara_library), asset(flame_path)
    local class, material = StaticFindObject(light_type), asset(material_path)
    if not valid(particles) or not fire or not valid(class) or not material then return end
    holder.flame = particles:SpawnSystemAttached(fire, holder.body, FName("None"), flame_location,
        {Pitch = 0, Yaw = 90, Roll = 0}, 0, false, false, 0, false)
    if not valid(holder.flame) or not same(holder.flame:GetAttachParent(), holder.body)
        or not same(holder.flame.Asset, fire) then return end
    holder.flame:SetCollisionEnabled(0)
    holder.flame:SetGenerateOverlapEvents(false)
    holder.flame:SetHiddenInGame(holder.hidden == true, false)
    holder.flame:Activate(true)
    holder.light = entry.pawn:AddComponentByClass(class, true, identity, true)
    local spot = holder.light
    if not valid(spot) then return end
    spot:SetVisibility(false, false)
    spot:SetMobility(2)
    entry.pawn:FinishAddComponent(spot, true, identity)
    if not spot:K2_AttachToComponent(holder.body, FName("None"), 2, 2, 0, false) then return end
    spot:K2_SetRelativeLocationAndRotation(light_location, neutral_rotation, false, {}, true)
    spot:SetIntensity(3000)
    spot:SetLightColor({R = 1, G = 180 / 255, B = 84 / 255, A = 1}, false)
    spot:SetLightFunctionMaterial(material)
    spot:SetHiddenInGame(holder.hidden == true, false)
    spot:SetVisibility(true, false)
    return true
end

local function cue(entry, opened)
    local name = opened and "Lighter_Open_Cue" or "Lighter_Close_Cue"
    local sound = asset(sounds .. name .. "." .. name)
    local api = StaticFindObject(statics)
    if valid(api) and sound then
        api:PlaySoundAtLocation(entry.pawn, sound, entry.lighter.body:K2_GetComponentLocation(),
            neutral_rotation, 1, 1, 0, nil, nil, nil, nil)
    end
end

local function set_lid(holder, pitch)
    holder.lid:K2_SetRelativeLocationAndRotation(lid_location,
        {Pitch = pitch, Yaw = 0, Roll = 0}, false, {}, true)
    holder.pitch = pitch
end

function lighter.animate(get_world)
    if not next(moving) then return end
    local world, clock = get_world(), StaticFindObject(statics)
    if not valid(world) or not valid(clock) then return end
    local now = clock:GetRealTimeSeconds(world)
    for entry, motion in pairs(moving) do
        local holder = entry.lighter
        if not holder or not valid(holder.lid) or world:GetAddress() ~= entry.world then
            moving[entry] = nil
        else
            local alpha = math.max(0, math.min(1, (now - motion.start) / .2))
            set_lid(holder, motion.from + (motion.to - motion.from) * alpha^2)
            if alpha == 1 then moving[entry] = nil end
        end
    end
end

function lighter.apply(entry, mode)
    local holder = entry.lighter
    if not holder or not modes[mode] or holder.mode == mode then return end
    local opened, prior = mode ~= closed, holder.mode
    local pitch = opened and 0 or -135
    local toggled = prior and (prior ~= closed) ~= opened
    if toggled then
        local clock = StaticFindObject(statics)
        if valid(clock) then
            moving[entry] = {start = clock:GetRealTimeSeconds(entry.pawn), from = holder.pitch, to = pitch}
        else
            set_lid(holder, pitch)
        end
    elseif not prior then
        set_lid(holder, pitch)
    end
    if mode == lit and prior ~= lit then
        local ok, made = pcall(ignite, entry)
        if not ok or not made then extinguish(entry) end
    elseif prior == lit then
        extinguish(entry)
    end
    holder.mode = mode
    if toggled then pcall(cue, entry, opened) end
end

return lighter
