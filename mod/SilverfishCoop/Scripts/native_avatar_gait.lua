local gait = {}

local root = "/Game/Classes/NPCs/NPC_Assets/Animations/"
local transitions = {}
local profiles = {
    unarmed = {name = "Unarmed_Walk_Blendspace", [0] = "Idle_Unarmed", "Walking_Unarmed", "Running_Unarmed", ramp = true},
    pistol = {name = "HandGun_Lowered_BlendSpace", [0] = "HandGun_Idle_2", "HandGun_Walk", "HandGun_Run"},
    rifle = {name = "Rifle_Lowered_BlendSpace", [0] = "Rifle_Idle_3", "Rifle_Walk", "Rifle_Run"},
}
local ramp_duration = .2
local posture_duration = .18

local function valid(object) return object and object:IsValid() end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

function gait.play(body, animation)
    if body.AnimationMode ~= 1 then body:SetAnimationMode(1, false) end
    local instance = body.AnimScriptInstance
    if not valid(instance) then return end
    body:SetAnimation(animation)
    local current = instance.CurrentAsset
    if not valid(current) or current:GetAddress() ~= animation:GetAddress() then return end
    body:Play(true)
    return instance
end

local function is_current(instance, state)
    if not valid(instance) or instance:GetAddress() ~= state.instance then return false end
    local current = instance.CurrentAsset
    return valid(current) and current:GetAddress() == state.asset
end

local function move_axis(instance, state, value)
    instance:SetBlendSpacePosition({X = value, Y = 0, Z = 0})
    state.value = value
end

local function profile_for(entry) return profiles[entry.weapon_stance] or profiles.unarmed end

function gait.pose_name(entry, pace) return profile_for(entry)[pace] end

local function transition_asset(mesh, from, to, load)
    local key = from .. "/" .. to
    if transitions[key] ~= nil then return transitions[key] or nil end
    local function construct()
        local source = load(root .. from .. "." .. from)
        local target = load(root .. to .. "." .. to)
        local template = load(root .. "Unarmed_Walk_Blendspace.Unarmed_Walk_Blendspace")
        local transient = StaticFindObject("/Engine/Transient")
        local sequence = StaticFindObject("/Script/Engine.AnimSequence")
        if not valid(source) or not valid(target) or not valid(sequence) or not source:IsA(sequence) or not target:IsA(sequence)
            or not valid(template) or not valid(transient) or not same(source.Skeleton, mesh.Skeleton)
            or not same(target.Skeleton, mesh.Skeleton) or not same(template.Skeleton, mesh.Skeleton) then return end
        local baseline = template.SampleData
        if baseline:GetArrayNum() ~= 3 then return end
        local at = {}
        for index = 1, 3 do
            local x = baseline[index].SampleValue.X
            if x ~= 0 and x ~= 1 and x ~= 2 or at[x] then return end
            at[x] = index
        end
        -- Copy the stock cooked interpolation grid. Only the three animation
        -- references on our transient object are replaced, before it is played.
        local blend = StaticConstructObject(template:GetClass(), transient, 0,
            EObjectFlags.RF_Transient | EObjectFlags.RF_MarkAsRootSet, 0, false, false, template)
        if not valid(blend) or same(blend, template) or not same(blend.Skeleton, mesh.Skeleton) then return end
        local samples = blend.SampleData
        if samples:GetArrayNum() ~= 3 then return end
        for x = 0, 2 do
            local index, clip = at[x], x == 0 and source or target
            local sample = samples[index]
            sample.Animation = clip
            if not same(samples[index].Animation, clip) then return end
        end
        return blend
    end
    local ok, blend = pcall(construct)
    if not ok or not valid(blend) then
        transitions[key] = false
        return
    end
    transitions[key] = blend
    return blend
end

function gait.transition(entry, mesh, group, name, load, prepare)
    local body = entry.body
    local mesh_address = mesh:GetAddress()
    local request = entry.pose_request
    local active = entry.pose_transition
    if active and active.group == group and active.name == name and active.mesh == mesh_address
        and is_current(body.AnimScriptInstance, active) then return true end
    entry.pose_transition = nil
    entry.pose_request = name and {group = group, name = name, mesh = mesh_address, stance = entry.weapon_stance} or nil
    if not request or not name or request.mesh ~= mesh_address or request.stance ~= entry.weapon_stance
        or request.name == name or request.group == group and not active then return end
    local from = active and active.value < .5 and active.from or request.name
    if from == name then return end
    local blend = transition_asset(mesh, from, name, load)
    if not valid(blend) then return end
    prepare(entry)
    local instance = gait.play(body, blend)
    if not valid(instance) then return end
    entry.gait_blend = nil
    entry.pose_transition = {group = group, name = name, from = from, mesh = mesh_address,
        asset = blend:GetAddress(), instance = instance:GetAddress(), value = 0, blend = blend}
    entry.pose_asset, entry.pose_mesh, entry.pose_animation, entry.pose_is_blend = blend:GetAddress(), mesh_address, name, true
    move_axis(instance, entry.pose_transition, 0)
    return true
end

function gait.apply(entry, mesh, pace, load_asset, prepare_body)
    local profile = profile_for(entry)
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
        instance = gait.play(body, blend)
        if not instance then
            body:SetAnimationMode(entry.animation_mode, false)
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
    local transition = entry.pose_transition
    if transition then
        local body = entry.body
        local instance = valid(body) and body.AnimScriptInstance
        if entry.pawn.Dead or not is_current(instance, transition) or body:IsAnySimulatingPhysics() then
            entry.pose_transition = nil
            return
        end
        transition.started = transition.started or now
        local progress = math.min(1, math.max(0, (now - transition.started) / posture_duration))
        move_axis(instance, transition, progress * progress * (3 - 2 * progress))
        if progress == 1 then entry.pose_transition = nil end
        return
    end
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
