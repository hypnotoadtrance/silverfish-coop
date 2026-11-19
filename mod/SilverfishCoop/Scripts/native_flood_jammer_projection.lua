local jammer = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local flood = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
local world_path = flood .. ".RuinFeilds_P"
local level_path = world_path .. ":PersistentLevel"
local actor_path = level_path .. ".SignalJammer_BP_C_1"
local actor_name = "SignalJammer_BP_C " .. actor_path
local jammer_class = "/Game/Classes/ScriptingActors/Quest_Objectives/SignalJammer_BP.SignalJammer_BP_C"
local pawn_class = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_class = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local sound_path = "/Game/Assets/Sounds/WAVs/Ambiant_Wav/sw_Amb_PhoneBox_LP.sw_Amb_PhoneBox_LP"
local binding

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function context(state, phase)
    if not enabled or phase ~= "connected" or state.listening ~= true or state.server ~= true or state.map ~= flood then return end
    local detail, controller, world = state.local_pawn, state.controller, state.world
    local pawn = detail and detail.pawn
    if not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not same(world.PersistentLevel, StaticFindObject(level_path))
        or not exact(pawn, pawn_class) or not exact(controller, controller_class)
        or not same(detail.controller_object, controller) or not same(controller.Pawn, pawn)
        or not same(pawn.Controller, controller) or not same(pawn:GetWorld(), world) or not same(controller:GetWorld(), world)
        or pawn:HasAuthority() or controller:HasAuthority() or not pawn:IsLocallyControlled()
        or not controller:IsLocalController() or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or controller.Loading ~= false or detail.name ~= pawn:GetFullName() then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    if not valid(connection) or not same(driver.World, world) or not same(connection.Driver, driver) then return end
    return { world = world, pawn = pawn, controller = controller, connection = connection }
end

local function same_context(first, second)
    return first and second and same(first.world, second.world) and same(first.pawn, second.pawn)
        and same(first.controller, second.controller) and same(first.connection, second.connection)
end

local function live_actor(current)
    local class = StaticFindObject(jammer_class)
    if not valid(class) then return nil, "class_unavailable" end
    local actor = StaticFindObject(actor_path)
    if not valid(actor) then
        for _, candidate in ipairs(FindAllOf("SignalJammer_BP_C") or {}) do
            if valid(candidate) and same(candidate:GetClass(), class) and same(candidate:GetWorld(), current.world)
                and candidate:GetFullName() == actor_name then return nil, "identity_unresolved" end
        end
        return nil, "absent"
    end
    if not same(actor:GetClass(), class) or actor:GetFullName() ~= actor_name or not same(actor:GetWorld(), current.world)
        or actor:IsActorBeingDestroyed() then return nil, "actor_unqualified" end
    local capsule = actor.Capsule
    if not exact(capsule, "/Script/Engine.CapsuleComponent") or not same(capsule:GetOwner(), actor)
        or not same(capsule, actor.RootComponent) then return nil, "root_unqualified" end
    return actor, capsule
end

local function audio_component(actor, root)
    local component = actor.Audio
    if not valid(component) then return nil, "none" end
    if not exact(component, "/Script/Engine.AudioComponent") or not same(component:GetOwner(), actor)
        or component:GetFullName() ~= "AudioComponent " .. actor_path .. ".Audio"
        or not same(component:GetAttachParent(), root) or not same(component.Sound, StaticFindObject(sound_path)) then
        return nil, "unqualified"
    end
    return component
end

local function hidden(actor)
    return actor.bHidden == true and actor:GetActorEnableCollision() == false
end

function jammer.clear()
    binding = nil
end

function jammer.command(fields, state, phase)
    if fields[2] ~= "native-flood-jammer-project" then return end
    if #fields ~= 3 or fields[3] ~= "destroyed" then error("flood_jammer_projection_exact_state_required", 0) end
    local current = context(state, phase)
    if not current then error("flood_jammer_projection_exact_client_required", 0) end
    local actor, root = live_actor(current)
    if not actor then
        if root ~= "absent" then error("flood_jammer_projection_" .. root, 0) end
        binding = nil
        return "stock Flood Plains jammer already absent"
    end
    local audio, audio_state = audio_component(actor, root)
    if not hidden(actor) then
        actor:SetActorEnableCollision(false)
        actor:SetActorHiddenInGame(true)
    end
    if not hidden(actor) then error("mutation_unverified_flood_jammer_visibility", 0) end
    if audio then
        -- Muting preserves the component lifecycle and leaves its delegates idle.
        if audio.VolumeMultiplier ~= 0 then audio:SetVolumeMultiplier(0) end
        if audio.VolumeMultiplier ~= 0 then error("mutation_unverified_flood_jammer_audio", 0) end
        audio_state = "muted"
    end
    current.actor, current.audio, current.audio_state = actor, audio, audio_state
    binding = current
    return "stock Flood Plains jammer projection verified"
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
    if not same_context(current, binding) then jammer.clear() end
    if not actor then
        if root ~= "absent" then return {} end
        return {status_row("native-flood-jammer-projection", flood, actor_name, "absent", "none")}
    end
    if binding and same(binding.actor, actor) and hidden(actor) then
        local audio, audio_state = audio_component(actor, root)
        if audio and same(audio, binding.audio) and audio.VolumeMultiplier == 0 then
            return {status_row("native-flood-jammer-projection", flood, actor_name, "hidden", "muted")}
        end
        if not audio and audio_state == binding.audio_state then
            return {status_row("native-flood-jammer-projection", flood, actor_name, "hidden", audio_state)}
        end
    end
    return {status_row("native-flood-jammer-projection", flood, actor_name, "present", "-")}
end

return jammer
