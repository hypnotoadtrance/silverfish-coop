local shadows = {}

shadows.class = "BlueprintGeneratedClass /Game/Classes/Anomalies/ShadowSphere_BP.ShadowSphere_BP_C"
local observer_fixture = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_SHADOW_SPHERE_FIXTURE") == "1"
shadows.enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and (os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1" or observer_fixture)
local material_path = "/Game/Assets/Materials/Mat_Misc/PP_Darkness_VoidAnom.PP_Darkness_VoidAnom"
local material_class = "Material " .. material_path
local material_library = "/Script/Engine.Default__KismetMaterialLibrary"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function owned(component, actor)
    local owner = valid(component) and component:GetOwner()
    return valid(owner) and owner:GetAddress() == actor:GetAddress()
end

local function setup(actor)
    local sphere, mesh, post = actor.Sphere, actor.FX_Sphere_01, actor.PostProcess_CreepingShadows
    if not owned(sphere, actor) or not owned(mesh, actor) or not owned(post, actor) then return end
    if sphere:GetClass():GetFullName() ~= "Class /Script/Engine.SphereComponent"
        or mesh:GetClass():GetFullName() ~= "Class /Script/Engine.StaticMeshComponent"
        or post:GetClass():GetFullName() ~= "Class /Script/Engine.PostProcessComponent"
        or type(sphere.SphereRadius) ~= "number" or type(actor.BlendRadius) ~= "number"
        or actor.BlendRadius <= 0 or actor.BlendRadius >= sphere.SphereRadius then return end
    -- An unbound local blend uses the camera-distance check below. The proxy's collision stays disabled.
    post.bUnbound = true
    post.bEnabled = false
    return {sphere = sphere, mesh = mesh, post = post, radius = sphere.SphereRadius, blend = actor.BlendRadius, scale = 1}
end

local function bind_material(actor, visual)
    local source, library = StaticFindObject(material_path), StaticFindObject(material_library)
    if not valid(source) or source:GetFullName() ~= material_class or not valid(library) then return end
    local material = library:CreateDynamicMaterialInstance(actor, source, FName("None"), 0)
    if not valid(material) then return end
    local intensity = FName("Intensity")
    material:SetScalarParameterValue(intensity, 0)
    visual.post.Settings.WeightedBlendables.Array[1] = {Weight = 1, Object = material}
    visual.material, visual.parameter = material, intensity
end

function shadows.project(active, state, phase)
    if not shadows.enabled or phase ~= "connected" or state.server ~= true then return end
    local point, clock, strongest, amount = nil, nil, nil, 0
    for _, entry in pairs(active) do
        local actor = entry.actor
        if entry.kind == "shadow-sphere" and valid(actor) and (entry.placed or actor:GetLocalRole() == 1)
            and not actor:IsActorBeingDestroyed() and not actor:GetActorEnableCollision() then
            local visual = entry.shadow or setup(actor)
            entry.shadow = visual
            if visual then
                if not point then
                    local pawn = state.local_pawn and state.local_pawn.pawn
                    local camera = valid(pawn) and pawn.Camera
                    if not valid(pawn) or not pawn:IsLocallyControlled() or not owned(camera, pawn) then return end
                    point = camera:K2_GetComponentLocation()
                    clock = StaticFindObject(gameplay_statics):GetTimeSeconds(state.world)
                end
                local center = visual.sphere:K2_GetComponentLocation()
                local distance = math.sqrt((center.X - point.X)^2 + (center.Y - point.Y)^2 + (center.Z - point.Z)^2)
                local fraction = math.max(0, math.min(1, (visual.radius - distance) / visual.blend))
                local alpha = math.max(0, math.min(1, (clock - (visual.last or clock)) * 10))
                visual.last = clock
                local scale = visual.scale + (1 + 4 * fraction - visual.scale) * alpha
                if math.abs(scale - visual.scale) > .001 then
                    visual.mesh:SetRelativeScale3D({X = scale, Y = scale, Z = scale})
                    visual.scale = scale
                end
                if fraction > amount then strongest, amount = entry, fraction end
            end
        end
    end
    for _, entry in pairs(active) do
        local visual = entry.shadow
        if visual and valid(visual.post) then
            if entry == strongest then
                if not valid(visual.material) then bind_material(entry.actor, visual) end
                if valid(visual.material) then
                    local strength = amount * .97
                    if visual.strength ~= strength then
                        visual.material:SetScalarParameterValue(visual.parameter, strength)
                        visual.strength = strength
                    end
                    if not visual.post.bEnabled then visual.post.bEnabled = true end
                end
            elseif visual.post.bEnabled then
                visual.post.bEnabled = false
            end
        end
    end
end

return shadows
