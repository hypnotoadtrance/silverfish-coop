local view = {}

view.body = "BlueprintGeneratedClass /Game/Classes/Anomalies/Pyrogeist_BP.Pyrogeist_BP_C"
view.jet = "BlueprintGeneratedClass /Game/Classes/Anomalies/Fire_Jet_Directed_BP.Fire_Jet_Directed_BP_C"
local crossroads = "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local jet_particle = "NiagaraSystem /Game/Assets/PFX/NI_FireJet_Continuous_2.NI_FireJet_Continuous_2"
local pending = {}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_body(actor)
    return valid(actor) and not actor:IsActorBeingDestroyed() and actor:GetClass():GetFullName() == view.body
end

function view.in_scope(actor)
    local world = actor:GetWorld()
    return valid(world) and world:GetFullName() == crossroads
end

local function owned(component, actor)
    return valid(component) and same(component:GetOwner(), actor)
end

local function source(jet)
    local body = jet.Instigator
    if not is_body(body) or not same(body:GetWorld(), jet:GetWorld()) then return end
    local sphere, root = body.Sphere, jet.RootComponent
    if owned(sphere, body) and owned(root, jet) and same(root:GetAttachParent(), sphere) then return body end
end

function view.created(jet, seat, replica)
    if not view.in_scope(jet) then return end
    if seat == "host" then
        -- Stock assigns the instigator before BeginPlay, then attaches after FinishSpawning.
        if not jet:HasAuthority() or not is_body(jet.Instigator) then return end
    elseif jet:GetLocalRole() ~= 1 then
        return
    elseif replica(jet) then
        jet:SetActorEnableCollision(false)
        jet:SetActorTickEnabled(false)
    end
    pending[jet:GetAddress()] = {actor = jet, retries = 8}
end

function view.ended(address)
    pending[address] = nil
end

function view.recover(world, replica)
    for _, jet in ipairs(FindAllOf("Fire_Jet_Directed_BP_C") or {}) do
        if valid(jet) and not jet:IsActorBeingDestroyed() and same(jet:GetWorld(), world)
            and jet:GetClass():GetFullName() == view.jet then view.created(jet, "client", replica) end
    end
end

function view.apply(world, phase, active, replica)
    for address, entry in pairs(pending) do
        local jet = entry.actor
        if not valid(jet) or jet:IsActorBeingDestroyed() or not same(jet:GetWorld(), world) then
            pending[address] = nil
        else
            local body = source(jet)
            if phase == "hosting" then
                local parent = body and active[body:GetAddress()]
                if parent and parent.kind == "pyrogeist" then
                    jet.NetUpdateFrequency = math.min(jet.NetUpdateFrequency, 5)
                    jet:SetReplicates(true)
                    jet:SetReplicateMovement(true)
                    jet:ForceNetUpdate()
                end
                pending[address] = nil
            elseif replica(jet) then
                jet:SetActorEnableCollision(false)
                jet:SetActorTickEnabled(false)
                if body and replica(body) then
                    local particles = jet.NI_FireMassive
                    if owned(particles, jet) and valid(particles.Asset) and particles.Asset:GetFullName() == jet_particle then
                        particles:Activate(false)
                        local audio = jet.Fireplace_Low_02_Loop
                        if owned(audio, jet) and not audio:IsPlaying() then audio:Play(0) end
                    end
                    pending[address] = nil
                else
                    entry.retries = entry.retries - 1
                    if entry.retries == 0 then pending[address] = nil end
                end
            end
        end
    end
end

return view
