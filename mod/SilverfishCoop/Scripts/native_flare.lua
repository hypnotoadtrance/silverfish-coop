local flare = {}

local folder = "/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/"
local classes = {}
for _, name in ipairs({"Flare_Projectile_BP", "Blue_Flare_Projectile_BP", "Yellow_Flare_Projectile_BP"}) do
    classes["BlueprintGeneratedClass " .. folder .. name .. "." .. name .. "_C"] = true
end

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function qualified(context, client)
    local actor = context:get()
    if not valid(actor) then return end
    local class = actor:GetClass()
    if not valid(class) or not classes[class:GetFullName()] or actor:IsActorBeingDestroyed() then return end
    local world = actor:GetWorld()
    local driver = valid(world) and world.NetDriver
    if not valid(driver) or not valid(driver.World) or driver.World:GetAddress() ~= world:GetAddress()
        or valid(driver.ServerConnection) ~= client then return end
    if client and actor:GetLocalRole() == 1 or not client and actor:HasAuthority() then return actor end
end

function flare.start(role)
    if type(RegisterBeginPlayPreHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function" then return end
    RegisterBeginPlayPreHook(function(context)
        if role() ~= "client" then return end
        local actor = qualified(context, true)
        if actor then actor:SetActorEnableCollision(false) end
    end)
    RegisterBeginPlayPostHook(function(context)
        local current = role()
        if current ~= "host" and current ~= "client" then return end
        local client = current == "client"
        local actor = qualified(context, client)
        if not actor then return end
        if client then
            actor:SetActorEnableCollision(false)
            local movement = actor.ProjectileMovement
            if valid(movement) then movement:Deactivate() end
            local spin = actor.RotatingMovement
            if valid(spin) then spin:Deactivate() end
        else
            actor.NetUpdateFrequency = 24
            actor:SetReplicates(true)
            actor:SetReplicateMovement(true)
            actor:ForceNetUpdate()
        end
    end)
end

return flare
