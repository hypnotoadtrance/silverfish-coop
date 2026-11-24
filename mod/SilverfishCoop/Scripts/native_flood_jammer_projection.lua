local jammer = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local profiles = require("native_jammer_profiles")
local jammer_class = "/Game/Classes/ScriptingActors/Quest_Objectives/SignalJammer_BP.SignalJammer_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local sound_path = "/Game/Assets/Sounds/WAVs/Ambiant_Wav/sw_Amb_PhoneBox_LP.sw_Amb_PhoneBox_LP"
local binding, observed

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function asset_path(object, path)
    return valid(object) and object:GetFullName():match("^%S+ (.+)$") == path
end

local function exact(object, path)
    return valid(object) and asset_path(object:GetClass(), path)
end

local function context(state, phase)
    local profile = profiles.by_map[state.map]
    local hosting = phase == "hosting"
    if not profile or not enabled or (not hosting and phase ~= "connected")
        or state.listening ~= true or state.server ~= (not hosting) then return end
    local detail, controller, world = state.local_pawn, state.controller, state.world
    local pawn = detail and detail.pawn
    local level = valid(world) and world.PersistentLevel
    if not valid(level) or world:GetFullName() ~= "World " .. profile.objective_world
        or level:GetFullName() ~= "Level " .. profile.objective_world .. ":PersistentLevel"
        or not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or not same(detail.controller_object, controller) or not same(controller.Pawn, pawn)
        or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or pawn:HasAuthority() ~= hosting or controller:HasAuthority() ~= hosting or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or detail.name ~= pawn:GetFullName() then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(driver) or not same(driver.World, world) or (hosting and valid(connection))
        or (not hosting and (not valid(connection) or not same(connection.Driver, driver))) then return end
    return {profile = profile, world = world, level = level, pawn = pawn, controller = controller, connection = connection}
end

local function same_context(first, second)
    return first and second and first.profile == second.profile and same(first.world, second.world) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller) and same(first.connection, second.connection)
end

local function live_actor(current, immediate)
    local profile = current.profile
    if not observed or observed.profile ~= profile or not same(observed.level, current.level) then
        observed = {profile = profile, level = current.level}
    end
    local actor = observed.actor
    if not valid(actor) or actor:IsActorBeingDestroyed() then
        observed.actor = nil
        local now = os.clock()
        if not immediate and observed.retry and now < observed.retry then return nil, observed.reason end
        actor = StaticFindObject(profile.jammer_path)
        if not valid(actor) then
            local reason = "absent"
            for _, candidate in ipairs(FindAllOf("SignalJammer_BP_C") or {}) do
                if exact(candidate, jammer_class) and same(candidate:GetWorld(), current.world)
                    and candidate:GetFullName() == profile.jammer_name then
                    reason = "identity_unresolved"
                    break
                end
            end
            observed.retry, observed.reason = now + .75, reason
            return nil, reason
        end
        if not exact(actor, jammer_class) or actor:GetFullName() ~= profile.jammer_name
            or not same(actor:GetWorld(), current.world) or not same(actor:GetOuter(), current.level)
            or actor:IsActorBeingDestroyed() then return nil, "actor_unqualified" end
        observed.actor, observed.retry, observed.reason = actor, nil, nil
    end
    local capsule = actor.Capsule
    if not exact(capsule, "/Script/Engine.CapsuleComponent") or not same(capsule:GetOwner(), actor)
        or not same(capsule, actor.RootComponent) then return nil, "root_unqualified" end
    return actor, capsule
end

local function audio_component(profile, actor, root)
    local component = actor.Audio
    if not valid(component) then return nil, "none" end
    if not exact(component, "/Script/Engine.AudioComponent") or not same(component:GetOwner(), actor)
        or component:GetFullName() ~= "AudioComponent " .. profile.jammer_path .. ".Audio"
        or not same(component:GetAttachParent(), root) or not asset_path(component.Sound, sound_path) then
        return nil, "unqualified"
    end
    return component
end

local function hidden(actor)
    return actor.bHidden == true and actor:GetActorEnableCollision() == false
end

local function valid_pose(values)
    for index = 1, 9 do
        local value = values[index]
        if type(value) ~= "number" or value ~= value
            or (index <= 3 and math.abs(value) > 1e7)
            or (index > 3 and index <= 6 and math.abs(value) > 360)
            or (index > 6 and (value == 0 or math.abs(value) > 100)) then return false end
    end
    return true
end

local function pose(actor)
    local loc, rot, scale = actor:K2_GetActorLocation(), actor:K2_GetActorRotation(), actor:GetActorScale3D()
    if not loc or not rot or not scale then return end
    local values = {loc.X, loc.Y, loc.Z, rot.Pitch, rot.Yaw, rot.Roll, scale.X, scale.Y, scale.Z}
    if valid_pose(values) then return values end
end

local function matches(before, after)
    if not before or not after then return false end
    for i = 1, 9 do
        local delta = before[i] - after[i]
        if i > 3 and i <= 6 then delta = (delta + 180) % 360 - 180 end
        if math.abs(delta) > (i <= 6 and 0.1 or 0.005) then return false end
    end
    return true
end

local function apply_pose(actor, values)
    if matches(pose(actor), values) then return true end
    if actor:K2_SetActorLocationAndRotation({X = values[1], Y = values[2], Z = values[3]},
        {Pitch = values[4], Yaw = values[5], Roll = values[6]}, false, {}, true) ~= true then return false end
    actor:SetActorScale3D({X = values[7], Y = values[8], Z = values[9]})
    return matches(pose(actor), values)
end

function jammer.clear()
    binding, observed = nil, nil
end

function jammer.command(fields, state, phase)
    local tag = type(fields[2]) == "string" and fields[2]:match("^(native%-%l+%-jammer)%-project$")
    local profile = profiles.by_tag[tag]
    if not profile then return end
    local current = context(state, phase)
    if phase ~= "connected" or not current or current.profile ~= profile or current.controller.Loading ~= false then
        error("jammer_projection_exact_client_required", 0)
    end
    local actor, root = live_actor(current, true)
    if fields[3] == "pose" and #fields == 12 then
        local expected = {}
        for index = 1, 9 do expected[index] = tonumber(fields[index + 3]) end
        if not actor or hidden(actor) or not valid_pose(expected) then error("jammer_projection_exact_pose_required", 0) end
        local original = pose(actor)
        if not original then error("jammer_projection_local_pose_unavailable", 0) end
        if not apply_pose(actor, expected) then
            pcall(apply_pose, actor, original)
            error("jammer_projection_pose_unverified", 0)
        end
        return "stock authored jammer pose verified"
    end
    if #fields ~= 3 or fields[3] ~= "destroyed" then error("jammer_projection_exact_state_required", 0) end
    if not actor then
        if root ~= "absent" then error("jammer_projection_" .. root, 0) end
        binding = nil
        return "stock authored jammer already absent"
    end
    local audio, audio_state = audio_component(current.profile, actor, root)
    if not hidden(actor) then
        actor:SetActorEnableCollision(false)
        actor:SetActorHiddenInGame(true)
    end
    if not hidden(actor) then error("mutation_unverified_jammer_visibility", 0) end
    if audio then
        -- Muting preserves the component lifecycle and leaves its delegates idle.
        if audio.VolumeMultiplier ~= 0 then audio:SetVolumeMultiplier(0) end
        if audio.VolumeMultiplier ~= 0 then error("mutation_unverified_jammer_audio", 0) end
        audio_state = "muted"
    end
    current.actor, current.audio, current.audio_state = actor, audio, audio_state
    binding = current
    return "stock authored jammer projection verified"
end

function jammer.rows(state, phase, status_row)
    local checked, current, actor, root = pcall(function()
        local connected = context(state, phase)
        if not connected then return end
        local placed, detail = live_actor(connected)
        return connected, placed, detail
    end)
    if not checked or not current then
        jammer.clear()
        return {}
    end
    local profile = current.profile
    local function pose_row()
        local values = actor and pose(actor)
        if values then
            return status_row(profile.tag .. "-pose", profile.objective_map, profile.jammer_name, table.unpack(values))
        end
    end
    if phase == "hosting" then
        if not actor or not actor:HasAuthority() or hidden(actor) then return {} end
        return {pose_row()}
    end
    if binding and not same_context(current, binding) then binding = nil end
    if not actor then
        if root ~= "absent" then return {} end
        return {status_row(profile.tag .. "-projection", profile.objective_map, profile.jammer_name, "absent", "none")}
    end
    if binding and same(binding.actor, actor) and hidden(actor) then
        local audio, audio_state = audio_component(current.profile, actor, root)
        if audio and same(audio, binding.audio) and audio.VolumeMultiplier == 0 then
            return {status_row(profile.tag .. "-projection", profile.objective_map, profile.jammer_name, "hidden", "muted")}
        end
        if not audio and audio_state == binding.audio_state then
            return {status_row(profile.tag .. "-projection", profile.objective_map, profile.jammer_name, "hidden", audio_state)}
        end
    end
    return {status_row(profile.tag .. "-projection", profile.objective_map, profile.jammer_name, "present", "-"), pose_row()}
end

return jammer
