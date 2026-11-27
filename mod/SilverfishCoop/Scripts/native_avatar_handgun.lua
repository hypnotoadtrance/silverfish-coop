local handgun = {}

local math_library = "/Script/Engine.Default__KismetMathLibrary"
local arrow_class = "/Script/Engine.ArrowComponent"
local zero = {Pitch = 0, Yaw = 0, Roll = 0}
local max_pitch, max_turn = 45, 75

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value, bound)
    return type(value) == "number" and math.abs(value) <= bound
end

local function signed(angle)
    return (angle + 180) % 360 - 180
end

local function lower(view)
    if not view.absolute or not valid(view.outer) then return end
    view.outer:SetAbsolute(false, false, false)
    view.outer:K2_SetRelativeRotation(zero, false, {}, true)
    view.absolute, view.pitch, view.yaw = false, nil, nil
end

function handgun.clear(entry)
    if valid(entry.handgun_hardpoint) and valid(entry.pawn) then
        entry.handgun_hardpoint:K2_DestroyComponent(entry.pawn)
    end
    entry.handgun_hardpoint, entry.handgun_view = nil, nil
end

function handgun.release(entry)
    local view = entry.handgun_view
    if not view then return end
    view.requested = false
    lower(view)
end

local function source(entry)
    local actor, pawn = entry.stock_weapon_actor, entry.pawn
    if not valid(actor) or actor:IsActorBeingDestroyed() or actor.ItemType ~= 3
        or actor:GetClass():GetFullName() ~= "BlueprintGeneratedClass " .. entry.firearm_class
        or not same(actor:GetWorld(), pawn:GetWorld()) or not same(actor:GetAttachParentActor(), pawn)
        or not (same(actor:GetOwner(), pawn) or same(actor.User, pawn)) then return end
    local root, muzzle = actor.RootComponent, actor.Arrow_Muzzle
    local arrow = StaticFindObject(arrow_class)
    if not valid(root) or not same(root:GetOwner(), actor)
        or not valid(muzzle) or not valid(arrow) or not muzzle:IsA(arrow) or not same(muzzle:GetOwner(), actor) then return end
    return actor, root, muzzle
end

function handgun.sync(entry, aiming, create_hardpoint)
    local inner, view = entry.weapon_anchor, entry.handgun_view
    if view and not same(view.inner, inner) then
        -- The weapon view has already destroyed the old recoil anchor and its parts.
        handgun.clear(entry)
        view = nil
    end
    if not aiming or entry.weapon_stance ~= "pistol" or not valid(inner) then
        handgun.release(entry)
        return
    end
    local actor, root, muzzle = source(entry)
    if not actor then
        handgun.release(entry)
        return
    end
    if view and same(view.actor, actor) and same(view.muzzle, muzzle) then
        view.requested = true
        return
    end

    local api = StaticFindObject(math_library)
    if not valid(api) then return end
    local local_muzzle = api:MakeRelativeTransform(muzzle:K2_GetComponentToWorld(), root:K2_GetComponentToWorld())
    local point = local_muzzle.Translation
    if not finite(point.X, 250) or not finite(point.Y, 250) or not finite(point.Z, 250) then return end
    local inverse = api:InvertTransform(local_muzzle)
    local correction = api:Quat_Rotator(inverse.Rotation)
    if not finite(correction.Pitch, 180) or not finite(correction.Yaw, 180) or not finite(correction.Roll, 180) then return end

    local outer = view and view.outer or create_hardpoint(entry)
    if not valid(outer) then return end
    if not view and not inner:K2_AttachToComponent(outer, FName("None", EFindName.FNAME_Find), 0, 0, 0, false) then
        handgun.clear(entry)
        return
    end
    if view then lower(view) end
    -- Keep the old anchor relative to this new pivot so recoil and weapon cycles keep their stock deltas.
    entry.handgun_view = {outer = outer, inner = inner, actor = actor, muzzle = muzzle,
        correction = correction, requested = true}
end

function handgun.update(entry, api, aim_pitch, aim_yaw)
    local view, pawn = entry.handgun_view, entry.pawn
    if not view or not view.requested or not valid(pawn) or pawn:IsActorBeingDestroyed() then return end
    local actor = view.actor
    if not same(actor, entry.stock_weapon_actor) or actor:IsActorBeingDestroyed()
        or not valid(view.muzzle) or not valid(view.outer) or not same(view.muzzle:GetOwner(), actor)
        or not same(view.inner, entry.weapon_anchor) or not same(view.inner:GetAttachParent(), view.outer)
        or not same(actor:GetAttachParentActor(), pawn) or pawn:IsLocallyControlled() or pawn.Dead
        or pawn.CharacterMovement:IsFalling() then
        lower(view)
        return
    end
    if not finite(aim_pitch, 360) or not finite(aim_yaw, 360) then
        local aim = pawn:GetBaseAimRotation()
        aim_pitch, aim_yaw = aim.Pitch, aim.Yaw
    end
    local facing = pawn:K2_GetActorRotation()
    if not finite(aim_pitch, 360) or not finite(aim_yaw, 360) or not finite(facing.Yaw, 360)
        or math.abs(signed(aim_yaw - facing.Yaw)) > max_turn then
        lower(view)
        return
    end
    local pitch, yaw = math.max(-max_pitch, math.min(max_pitch, signed(aim_pitch))), signed(aim_yaw)
    if view.absolute and math.abs(view.pitch - pitch) < .05 and math.abs(signed(view.yaw - yaw)) < .05 then return end
    if not view.absolute then view.outer:SetAbsolute(false, true, false) end
    local rotation = api:ComposeRotators(view.correction, {Pitch = pitch, Yaw = yaw, Roll = 0})
    view.outer:K2_SetWorldRotation(rotation, false, {}, true)
    view.absolute, view.pitch, view.yaw = true, pitch, yaw
end

return handgun
