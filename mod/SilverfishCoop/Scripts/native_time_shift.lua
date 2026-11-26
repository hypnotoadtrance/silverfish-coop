local shift = {}
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local current, installed, expected_class

local function valid(object)
    return object and object:IsValid()
end

local function scope(state, phase)
    local pawn = state.local_pawn and state.local_pawn.pawn
    if (phase ~= "hosting" and phase ~= "connected") or not valid(pawn) or not pawn:IsLocallyControlled()
        or not valid(state.world) then return end
    local instance = state.world.OwningGameInstance
    if not valid(instance) then return end
    local address = instance:GetClass():GetAddress()
    if valid(expected_class) and expected_class:GetAddress() == address then return instance, pawn end
    expected_class = StaticFindObject(instance_class)
    if valid(expected_class) and expected_class:GetAddress() == address then return instance, pawn end
end

function shift.sync(state, phase)
    current = scope(state, phase)
    if current and not installed then
        RegisterHook("/Script/Engine.GameplayStatics:SetGlobalTimeDilation", function(_, context, dilation)
            local owner = context:get()
            -- TimeShift_Global already boosts the local pawn's stock movement
            -- stats. Keep the world clock unchanged during that effect.
            if valid(current) and valid(owner) and current:GetAddress() == owner:GetAddress()
                and owner["Time Shifted"] == true and dilation:get() < 1 then
                dilation:set(1.0)
            end
        end)
        installed = true
    end
end

function shift.command(fields, state, phase)
    if fields[2] ~= "native-time-shift-fixture" then return end
    local owner, pawn = scope(state, phase)
    if not owner or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
        or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1" or #fields ~= 3 then
        error("Time shift fixture unavailable", 0)
    end
    local statics = StaticFindObject(statics_path)
    if fields[3] == "start" then
        owner:TimeShift_Global(true, 0.25, 3)
    elseif fields[3] ~= "observe" then
        error("Expected the start or observe time shift fixture", 0)
    end
    return ("%d;%.3f;%.3f"):format(owner["Time Shifted"] and 1 or 0,
        statics:GetGlobalTimeDilation(pawn), pawn.CharacterMovement.MaxWalkSpeed)
end

return shift
