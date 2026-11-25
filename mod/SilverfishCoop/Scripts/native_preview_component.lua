local function valid(object) return object and object:IsValid() end

return function(entry, field, class, parent, transform, source)
    if not valid(class) then return end
    local actor = entry.actor
    local component = actor:AddComponentByClass(class, true, transform, true)
    if not valid(component) then return end
    entry[field] = component
    if source then
        component:SetCollisionEnabled(0)
        component:SetGenerateOverlapEvents(false)
        if not component:SetStaticMesh(source.StaticMesh) then return end
        for index = 0, math.min(source:GetNumMaterials(), 8) - 1 do
            local material = source:GetMaterial(index)
            if valid(material) then component:SetMaterial(index, material) end
        end
    end
    component:SetMobility(2)
    actor:FinishAddComponent(component, true, transform)
    if not component:K2_AttachToComponent(parent, FName("None", EFindName.FNAME_Find), 0, 0, 0, false) then return end
    component:K2_SetRelativeTransform(transform, false, {}, true)
    return component
end
