local feedback = {}
local guest_save = require("guest_save")

local statics_path = "/Script/Engine.Default__GameplayStatics"
local damage_class = "BlueprintGeneratedClass /Game/Classes/Player/PlayerAssets/PPActor_Damage_BP.PPActor_Damage_BP_C"
local shake_path = "/Game/Classes/Player/CS_Jumping.CS_Jumping_C"
local shake_function = "/Script/Engine.PlayerController:ClientStartCameraShake"
local pending = {}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
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
        local effect = pending[#pending]
        if not effect or not same(context:get(), effect.host) or not same(shake:get(), effect.shake) then return end
        local magnitude = scale:get()
        if type(magnitude) == "number" and magnitude >= .099 and magnitude <= 5 then
            effect.magnitude = magnitude
            scale:set(0.0)
        end
    end)
    RegisterBeginPlayPreHook(function(context)
        if not is_host() then return end
        local actor = context:get()
        if not valid(actor) or actor:GetClass():GetFullName() ~= damage_class or not actor:HasAuthority() then return end
        local victim = actor.Player
        if not valid(victim) or not guest_save.matches(victim) or victim:IsLocallyControlled() then return end
        local world, controller = actor:GetWorld(), victim.Controller
        local statics = StaticFindObject(statics_path)
        local host = valid(statics) and statics:GetPlayerController(world, 0) or nil
        local shake = StaticFindObject(shake_path)
        if not valid(host) or not host:IsLocalController() or not valid(controller) or controller:IsLocalController()
            or not same(controller.Pawn, victim) or not same(victim:GetWorld(), world)
            or not same(host:GetWorld(), world) or not valid(shake) then return end
        pending[#pending + 1] = {actor = actor, host = host, victim = controller, shake = shake}
        mute_post_process(actor)
    end)
    RegisterBeginPlayPostHook(function(context)
        local effect = pending[#pending]
        if not effect or not same(context:get(), effect.actor) then return end
        pending[#pending] = nil
        mute_post_process(effect.actor)
        effect.actor:SetLifeSpan(.03)
        if effect.magnitude and valid(effect.victim) then
            effect.victim:ClientStartCameraShake(effect.shake, effect.magnitude, 0, {Pitch = 0, Yaw = 0, Roll = 0})
        end
    end)
end

return feedback
