local alarm = {}

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(value, path)
    return valid(value) and same(value:GetClass(), StaticFindObject(path))
end

local function resolve(crate)
    local mount = crate.AlarmLight_BP
    if not exact(mount, "/Script/Engine.ChildActorComponent") or not same(mount:GetOwner(), crate) then return end
    local child = mount.ChildActor
    if not exact(child, "/Game/Classes/DynamicLights/AlarmLight_BP.AlarmLight_BP_C")
        or not same(child:GetWorld(), crate:GetWorld()) or not same(child:GetParentComponent(), mount)
        or child:IsActorBeingDestroyed() then return end
    local light, audio = child.PointLight, child.Audio
    if not exact(light, "/Script/Engine.PointLightComponent") or not same(light:GetOwner(), child)
        or not exact(audio, "/Script/Engine.AudioComponent") or not same(audio:GetOwner(), child) then return end
    return child, light, audio
end

function alarm.read(crate)
    local child, light, audio = resolve(crate)
    if not child then return end
    return light:IsVisible(), audio:IsActive()
end

function alarm.apply(crate, lit, audible)
    local child, light, audio = resolve(crate)
    if not child then error("stock_loot_alarm_scope_unavailable", 0) end
    if light:IsVisible() ~= lit then child:LightFunc(lit) end
    if audio:IsActive() ~= audible then
        if audible then audio:Activate(true) else audio:Deactivate() end
    end
    if light:IsVisible() ~= lit or audio:IsActive() ~= audible then error("stock_loot_alarm_readback_unverified", 0) end
end

return alarm
