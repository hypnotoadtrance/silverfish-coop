local feedback = {}
local guest_save = require("guest_save")
local save_scope = require("native_save_scope")
local voice = require("native_voice")

local statics_path = "/Script/Engine.Default__GameplayStatics"
local damage_path = "/Game/Classes/Player/PlayerAssets/PPActor_Damage_BP.PPActor_Damage_BP_C"
local damage_class = "BlueprintGeneratedClass " .. damage_path
local psyche_path = "/Game/Classes/Player/PlayerAssets/PPActor_PsyAttack_BP.PPActor_PsyAttack_BP_C"
local psyche_class = "BlueprintGeneratedClass " .. psyche_path
local dizzy_path = "/Game/Classes/Player/CS_Dizzy.CS_Dizzy_C"
local whisper_path = "/Game/Assets/Sounds/WAVs/Anomaly_Wav/Misc_Whisper.Misc_Whisper"
local psyche_mask = 16
local shake_path = "/Game/Classes/Player/CS_Jumping.CS_Jumping_C"
local shake_function = "/Script/Engine.PlayerController:ClientStartCameraShake"
local pending, health, events = {}, {}, {}
local world, active_overlay, psyche_overlay, overlay_controller, visit, last_played
local serial = 0
local hit_sounds = {
    {1, "Kenetic_2_47ECE93447043FC41017B8B98F2C7DE5", "ArmorPenitration_4_E48DD35840D3699D8F37E48678EF29E4",
        "Stabbing_8_64710D2D4AB4232B4DD604AC3CD0219C", "Slashing_10_58FFFE444BD41CE5D15EAA80BC0D9DF1"},
    {2, "BluntForce_6_6923B3964740DE281BD773BCCAAC2772"},
    {4, "Fire_12_2B415A724C533C075B6BB390B76BF247", "Acid_20_F4C551B34F620FDF72D2F2A64DE15F3E"},
    {8, "Poison_14_68E96E324AD705A7F95362BBC781E220"},
}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value, limit)
    return type(value) == "number" and value == value and math.abs(value) <= limit
end

local function shadow_target(pawn)
    return valid(pawn) and type(SilverfishIsNativeShadowFeedbackTarget) == "function"
        and SilverfishIsNativeShadowFeedbackTarget(pawn:GetAddress()) == true
end

local function mask_for(damage)
    local mask = 0
    for _, kind in ipairs(hit_sounds) do
        for index = 2, #kind do
            local amount = damage[kind[index]]
            if finite(amount, 1e6) and amount > 3 then mask = mask | kind[1] break end
        end
    end
    return mask
end

function feedback.sync(state, phase)
    local hosting = phase == "hosting" and valid(state.world)
    local address = hosting and state.world:GetAddress() or nil
    if world ~= address then
        world, events, pending = address, {}, {}
    end
    health = {}
    if not hosting then return end
    for _, detail in ipairs(state.guests or {}) do
        local pawn = detail.pawn
        local save = valid(pawn) and pawn.Save
        if valid(save) and finite(save.PlayerHealth, 1e6) then
            health[pawn:GetAddress()] = {save = save, value = save.PlayerHealth}
        end
    end
end

function feedback.rows(state, phase, row)
    if phase ~= "hosting" or not valid(state.world) or state.world:GetAddress() ~= world then return {} end
    local rows = {}
    for _, event in ipairs(events) do
        if os.time() - event.timestamp < 3 then
            rows[#rows + 1] = row("native-guest-hurt", event.serial, event.pawn,
                event.amount, event.x, event.y, event.z, event.mask)
        end
    end
    return rows
end

local function record(pawn, amount, direction, mask)
    if serial >= 2^53 - 1 then return end
    serial = serial + 1
    events[#events + 1] = {serial = serial, pawn = pawn:GetFullName(), amount = math.min(amount, 1000),
        x = direction.X, y = direction.Y, z = direction.Z, mask = mask, timestamp = os.time()}
    if #events > 12 then table.remove(events, 1) end
end

local function observed_hit(actor, pawn)
    local previous = health[pawn:GetAddress()]
    local direction, amount = actor.HitDirection, actor.Ammount
    if not previous or not same(previous.save, pawn.Save) or not finite(previous.save.PlayerHealth, 1e6)
        or previous.save.PlayerHealth >= previous.value or not finite(amount, 1e6) or amount < 1
        or not direction or not finite(direction.X, 1) or not finite(direction.Y, 1) or not finite(direction.Z, 1) then return end
    previous.value = previous.save.PlayerHealth
    record(pawn, amount, direction, mask_for(actor.Damage))
end

local function mute_post_process(actor)
    for _, name in ipairs({"PostProcessDirection", "PostProcess_FX"}) do
        local component = actor[name]
        if valid(component) and same(component:GetOwner(), actor) then component.bEnabled = false end
    end
end

function feedback.start(is_host)
    if type(RegisterBeginPlayPreHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function"
        or not valid(StaticFindObject(shake_function)) then return end
    RegisterHook(shake_function, function(context, shake, scale)
        local controller, pattern = context:get(), shake:get()
        if same(controller, overlay_controller) and same(pattern, StaticFindObject(shake_path)) then
            scale:set(0.0)
            return
        end
        local effect = pending[#pending]
        if not effect or not same(controller, effect.host) or not same(pattern, effect.shake) then return end
        local magnitude = scale:get()
        if type(magnitude) == "number" and magnitude >= .099 and magnitude <= 5 then
            effect.magnitude = magnitude
            scale:set(0.0)
        end
    end)
    if type(SilverfishIsNativeShadowFeedbackTarget) == "function" then
        RegisterHook("/Script/Engine.GameplayStatics:PlaySound2D", function(_, source, _, volume)
            local actor = source:get()
            if valid(actor) and actor:GetClass():GetFullName() == psyche_class and shadow_target(actor.Player) then
                volume:set(0.0)
            end
        end)
        RegisterHook("/Script/Engine.GameplayStatics:SpawnSoundAttached",
            function(_, sound, component, _, _, _, _, _, volume)
                local asset, attached = sound:get(), component:get()
                if valid(asset) and asset:GetFullName() == "SoundWave " .. whisper_path
                    and valid(attached) and shadow_target(attached:GetOwner()) then volume:set(0.0) end
            end)
    end
    RegisterBeginPlayPreHook(function(context)
        if not is_host() then return end
        local actor = context:get()
        if not valid(actor) then return end
        local class = actor:GetClass():GetFullName()
        if (class ~= damage_class and class ~= psyche_class) or not actor:HasAuthority() then return end
        local victim = actor.Player
        if not valid(victim) or not guest_save.matches(victim) or victim:IsLocallyControlled() then return end
        local psyche = class == psyche_class
        if psyche and not shadow_target(victim) then return end
        local world, controller = actor:GetWorld(), victim.Controller
        local statics = StaticFindObject(statics_path)
        local host = valid(statics) and statics:GetPlayerController(world, 0) or nil
        local shake = StaticFindObject(psyche and dizzy_path or shake_path)
        if not valid(host) or not host:IsLocalController() or not valid(controller) or controller:IsLocalController()
            or not same(controller.Pawn, victim) or not same(victim:GetWorld(), world)
            or not same(host:GetWorld(), world) or not valid(shake) then return end
        pending[#pending + 1] = {actor = actor, host = host, victim = controller, shake = shake, psyche = psyche}
        mute_post_process(actor)
        if psyche then
            if finite(actor.Ammount, 10) and actor.Ammount >= 1 then
                record(victim, actor.Ammount, {X = 0, Y = 0, Z = 0}, psyche_mask)
            end
        else
            pcall(observed_hit, actor, victim)
        end
    end)
    RegisterBeginPlayPostHook(function(context)
        local effect = pending[#pending]
        if not effect or not same(context:get(), effect.actor) then return end
        pending[#pending] = nil
        mute_post_process(effect.actor)
        effect.actor:SetLifeSpan(.03)
        if not effect.psyche and effect.magnitude and valid(effect.victim) then
            effect.victim:ClientStartCameraShake(effect.shake, effect.magnitude, 0, {Pitch = 0, Yaw = 0, Roll = 0})
        end
    end)
end

local function show_psyche(state, pawn, amount)
    local statics, class, whisper = StaticFindObject(statics_path), StaticFindObject(psyche_path), StaticFindObject(whisper_path)
    if not valid(class) then LoadAsset(psyche_path); class = StaticFindObject(psyche_path) end
    if not valid(whisper) then LoadAsset(whisper_path); whisper = StaticFindObject(whisper_path) end
    if not valid(statics) or not valid(class) or not valid(whisper) or not valid(pawn.Camera)
        or not same(pawn.Camera:GetOwner(), pawn) then return false end
    local transform = {Translation = pawn:K2_GetActorLocation(),
        Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
    local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, pawn, 0)
    if not valid(actor) or not same(actor:GetWorld(), state.world) or not same(actor:GetClass(), class) then return false end
    actor.Player, actor.Ammount = pawn, amount
    local ok, finished = pcall(statics.FinishSpawningActor, statics, actor, transform, 0)
    if not ok or not same(finished, actor) or not same(actor.Player, pawn) or not valid(actor.PostProcess_FX)
        or actor.PostProcess_FX.bEnabled ~= true then
        actor:K2_DestroyActor()
        return false
    end
    if valid(psyche_overlay) and not psyche_overlay:IsActorBeingDestroyed() then psyche_overlay:K2_DestroyActor() end
    psyche_overlay = actor
    local sound = statics:SpawnSoundAttached(whisper, pawn.Camera, FName("None"), {X = 0, Y = 0, Z = 0},
        {Pitch = 0, Yaw = 0, Roll = 0}, 0, false, 1, 1, 0, nil, nil, true)
    if valid(sound) then sound:FadeOut(4 + 3 * math.random(), 0, 0) end
    return true
end

local function show_overlay(state, pawn, direction, amount)
    if active_overlay and same(active_overlay.world, state.world) and same(active_overlay.pawn, pawn)
        and valid(active_overlay.actor) and not active_overlay.actor:IsActorBeingDestroyed() then
        active_overlay.actor:Reset(direction, amount)
        return true
    end
    local statics, class = StaticFindObject(statics_path), StaticFindObject(damage_path)
    if not valid(class) then
        LoadAsset(damage_path)
        class = StaticFindObject(damage_path)
    end
    if not valid(statics) or not valid(class) then return false end
    local transform = {Translation = pawn:K2_GetActorLocation(),
        Rotation = {X = 0, Y = 0, Z = 0, W = 1}, Scale3D = {X = 1, Y = 1, Z = 1}}
    local actor = statics:BeginDeferredActorSpawnFromClass(pawn, class, transform, 1, pawn, 0)
    if not valid(actor) or not same(actor:GetWorld(), state.world) or not same(actor:GetClass(), class) then return false end
    actor.Player, actor.HitDirection, actor.Ammount = pawn, direction, amount
    -- The server already sent the stock camera shake directly to this controller.
    overlay_controller = state.controller
    local ok, finished = pcall(statics.FinishSpawningActor, statics, actor, transform, 0)
    overlay_controller = nil
    if not ok or not same(finished, actor) or not same(actor.Player, pawn) or not valid(actor.PostProcess_FX)
        or actor.PostProcess_FX.bEnabled ~= true then
        actor:K2_DestroyActor()
        return false
    end
    active_overlay = {world = state.world, pawn = pawn, actor = actor}
    return true
end

function feedback.command(fields, state, phase)
    if fields[2] ~= "native-guest-hurt-play" then return end
    local epoch, name = fields[4], fields[5]
    local local_player, controller = state.local_pawn, state.controller
    if phase ~= "connected" or #fields ~= 11 or state.map ~= fields[3] or not epoch or #epoch ~= 32
        or not epoch:match("^[a-f0-9]+$") or not local_player or local_player.name ~= name
        or not valid(controller) or not controller:IsLocalController() then error("Guest hurt feedback scope unavailable", 0) end
    local save, pawn = save_scope.bind_guest(state, phase)
    if not valid(save) or not same(pawn, controller.Pawn) or not same(pawn.Controller, controller)
        or not same(pawn:GetWorld(), state.world) then error("Guest hurt feedback owner unavailable", 0) end
    local id, amount, x, y, z, mask = table.unpack({tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8]),
        tonumber(fields[9]), tonumber(fields[10]), tonumber(fields[11])})
    if math.type(id) ~= "integer" or id < 1 or id >= 2^53 or not finite(amount, 1000) or amount < 1
        or not finite(x, 1) or not finite(y, 1) or not finite(z, 1)
        or math.type(mask) ~= "integer" or mask < 0 or mask > psyche_mask
        or (mask == psyche_mask and (amount > 10 or x ~= 0 or y ~= 0 or z ~= 0
            or state.map ~= "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P")) then
        error("Guest hurt feedback cue unavailable", 0)
    end
    if visit ~= epoch then
        if active_overlay and valid(active_overlay.actor) then active_overlay.actor:K2_DestroyActor() end
        if valid(psyche_overlay) then psyche_overlay:K2_DestroyActor() end
        visit, last_played, active_overlay = epoch, 0, nil
        psyche_overlay = nil
    end
    if id <= last_played then return "guest hurt feedback already played" end
    last_played = id
    local health_before = save.PlayerHealth
    if mask == psyche_mask then
        if not show_psyche(state, pawn, amount) or save.PlayerHealth ~= health_before then
            error("Guest psychic feedback readback failed", 0)
        end
        return "guest hurt feedback played"
    end
    local shown = show_overlay(state, pawn, {X = x, Y = y, Z = z}, amount)
    local damage = {}
    for _, kind in ipairs(hit_sounds) do
        if mask & kind[1] ~= 0 then damage[kind[2]] = 4 end
    end
    voice.local_feedback(pawn, function()
        if mask > 0 then pawn:DmgSfx(damage) end
        if amount >= 40 and valid(pawn.Vox_Hurt) then pawn:Speak(pawn.Vox_Hurt) end
    end)
    if not shown or save.PlayerHealth ~= health_before then error("Guest hurt feedback readback failed", 0) end
    return "guest hurt feedback played"
end

return feedback
