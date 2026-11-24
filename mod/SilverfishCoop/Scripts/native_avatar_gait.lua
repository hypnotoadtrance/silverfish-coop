local gait = {}

local root = "/Game/Classes/NPCs/NPC_Assets/Animations/"
local profiles = {
    unarmed = {name = "Unarmed_Walk_Blendspace", [0] = "Idle_Unarmed", "Walking_Unarmed", "Running_Unarmed", ramp = true},
    pistol = {name = "HandGun_Lowered_BlendSpace", [0] = "HandGun_Idle_2", "HandGun_Walk", "HandGun_Run"},
    rifle = {name = "Rifle_Lowered_BlendSpace", [0] = "Rifle_Idle_3", "Rifle_Walk", "Rifle_Run"},
}
local ramp_duration = .2

local function valid(object) return object and object:IsValid() end

local function is_current(instance, state)
    if not valid(instance) or instance:GetAddress() ~= state.instance then return false end
    local current = instance.CurrentAsset
    return valid(current) and current:GetAddress() == state.asset
end

local function move_axis(instance, state, value)
    instance:SetBlendSpacePosition({X = value, Y = 0, Z = 0})
    state.value = value
end

function gait.apply(entry, mesh, pace, load_asset, prepare_body)
    local profile = profiles[entry.weapon_stance] or profiles.unarmed
    local body = entry.body
    local mesh_address = mesh:GetAddress()
    local instance = body.AnimScriptInstance
    local state = entry.gait_blend
    if not state or state.name ~= profile.name or entry.pose_mesh ~= mesh_address or not is_current(instance, state) then
        local class = StaticFindObject("/Script/Engine.BlendSpace1D")
        local blend = valid(class) and load_asset(root .. profile.name .. "." .. profile.name)
        if not valid(blend) or not blend:IsA(class) or not valid(mesh.Skeleton) or not valid(blend.Skeleton)
            or blend.Skeleton:GetAddress() ~= mesh.Skeleton:GetAddress() then return false end

        prepare_body(entry)
        if body.AnimationMode == 1 and not valid(instance) then body:SetAnimationMode(1, true) end
        body:PlayAnimation(blend, true)
        instance = body.AnimScriptInstance
        if not valid(instance) or not valid(instance.CurrentAsset)
            or instance.CurrentAsset:GetAddress() ~= blend:GetAddress() then
            body:SetAnimationMode(entry.animation_mode, true)
            return false
        end
        state = {name = profile.name, asset = blend:GetAddress(), instance = instance:GetAddress(), target = pace}
        move_axis(instance, state, pace)
        entry.pose_asset, entry.pose_mesh, entry.gait_blend = state.asset, mesh_address, state
    elseif pace ~= state.target then
        state.target = pace
        if profile.ramp then
            -- The stock unarmed asset has no input interpolation. Advance only
            -- its active transition on the existing avatar presentation timer.
            state.from, state.started = state.value, nil
        else
            move_axis(instance, state, pace)
        end
    end
    -- Report the destination sample, while CurrentAsset keeps the blend space.
    entry.pose_animation, entry.pose_is_blend = profile[pace], true
    return true
end

function gait.advance(entry, now)
    local state = entry.gait_blend
    if not state or not state.from then return end
    local pawn, body = entry.pawn, entry.body
    local movement = pawn.CharacterMovement
    local instance = valid(body) and body.AnimScriptInstance
    if pawn.Dead or pawn.bIsCrouched or not valid(movement) or not movement:IsMovingOnGround()
        or not is_current(instance, state) or body:IsAnySimulatingPhysics() then
        entry.gait_blend = nil
        return
    end

    state.started = state.started or now
    local progress = math.min(1, math.max(0, (now - state.started) / ramp_duration))
    local alpha = progress * progress * (3 - 2 * progress)
    local value = state.from + (state.target - state.from) * alpha
    if value ~= state.value then move_axis(instance, state, value) end
    if progress == 1 then state.from, state.started = nil, nil end
end

return gait
