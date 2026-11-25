local light_view = require("native_avatar_light")
local visor = {}

local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local source_class = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local spot_class = "/Script/Engine.SpotLightComponent"
local tac = {R = .40625, G = .614151, B = 1}
local torch = {R = 1, G = .970104, B = .947917}
-- FlashLightTypes_DT entries selected by the stock pawn-mounted emitter.
local profiles = {
    [1] = {intensity = 10, radius = 3000, inner = 10, outer = 30, color = tac},
    [2] = {intensity = 20, radius = 4500, inner = 15, outer = 35, color = tac},
    [3] = {intensity = 17, radius = 5000, inner = 15, outer = 40, color = torch},
    [4] = {intensity = 18, radius = 6000, inner = 20, outer = 50, color = torch},
    [5] = {intensity = 20, radius = 6000, inner = 30, outer = 60, color = {R = 1, G = 1, B = 1}},
}

local function valid(value) return value and value:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function visor.powered(pawn, world)
    local save = pawn.Save
    local instance = world.OwningGameInstance
    if pawn.Dead or pawn:IsActorBeingDestroyed() or not pawn:IsLocallyControlled()
        or not same(pawn:GetWorld(), world) or not valid(instance) or not same(save, instance.SaveSlot_Ref) then return end
    local class = save.PlayerHelmet[item_class]
    if not valid(class) then return end
    local defaults = class:GetCDO()
    local kind = valid(defaults) and defaults.FlashLightType
    local profile = profiles[kind]
    if not profile then return end

    local child = pawn.FlashLight_Source_BP
    local source = valid(child) and child.ChildActor
    local spot = valid(source) and source.SpotLight
    if not valid(source) or not valid(spot) or source:IsActorBeingDestroyed()
        or not same(source:GetClass(), StaticFindObject(source_class)) or not same(source:GetWorld(), world)
        or not same(child:GetOwner(), pawn) or not same(source:GetParentActor(), pawn)
        or not same(spot:GetClass(), StaticFindObject(spot_class)) or not same(spot:GetOwner(), source)
        or source.Type ~= kind then return end
    return source.Powered == true and spot:IsVisible() and not spot.bHiddenInGame and not source.bHidden
        and spot.Intensity == profile.intensity and spot.AttenuationRadius == profile.radius
end

function visor.clear(entry)
    if valid(entry.visor_light) and valid(entry.pawn) then entry.visor_light:K2_DestroyComponent(entry.pawn) end
    entry.visor_light, entry.visor_retry = nil, nil
end

function visor.sync(entry, powered, listener)
    local specs = entry.visor_spec
    if not specs or not valid(entry.helmet) then
        visor.clear(entry)
        entry.visor_previous = nil
        return
    end
    if entry.visor_previous ~= nil and entry.visor_previous ~= powered and valid(listener) then
        light_view.click(listener, entry.helmet)
    end
    entry.visor_previous = powered
    if not powered then visor.clear(entry); return end
    if valid(entry.visor_light) then return end
    local now = os.clock()
    if entry.visor_retry and now < entry.visor_retry then return end
    entry.visor_retry = now + .5
    local color = specs.profile.color
    entry.visor_light = light_view.create(entry.pawn, entry.helmet, specs.profile,
        {specs.location.X, specs.location.Y, specs.location.Z - 162, 0, 0, 0, color.R, color.G, color.B})
    if valid(entry.visor_light) then entry.visor_light:SetVisibility(true, false) end
end

function visor.equip(entry, defaults)
    visor.clear(entry)
    entry.visor_previous = nil
    local profile = valid(defaults) and profiles[defaults.FlashLightType]
    -- Cooked helmet light locations use the full player rig. Convert the
    -- nominal head height to the local origin of the projected helmet mesh.
    entry.visor_spec = profile and {profile = profile, location = defaults.FlashlightLocation} or nil
end

return visor
