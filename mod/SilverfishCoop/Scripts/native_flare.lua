local flare = {}
local guest_save = require("guest_save")

local folder = "/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/"
local classes = {}
for _, name in ipairs({"Flare_Projectile_BP", "Blue_Flare_Projectile_BP", "Yellow_Flare_Projectile_BP"}) do
    classes["BlueprintGeneratedClass " .. folder .. name .. "." .. name .. "_C"] = true
end
flare.equipment = {}
for _, name in ipairs({"Eqp_Flare", "Eqp_Flare_Blue", "Eqp_Flare_Yellow"}) do
    flare.equipment["/Game/Classes/Equipment/" .. name .. "." .. name .. "_C"] = true
end
local intent, receipt

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

function flare.sync(state, phase)
    if phase ~= "connected" or not state.server or not state.listening or type(SilverfishTakeLocalGuestFlare) ~= "function" then
        intent = nil
        return
    end
    local local_pawn = state.local_pawn
    if not local_pawn or not valid(local_pawn.pawn) then return end
    local sequence, guid, toss, class = SilverfishTakeLocalGuestFlare(local_pawn.pawn:GetAddress())
    if sequence and flare.equipment[class] then
        intent = {state.map, local_pawn.name, sequence, guid, toss and 1 or 0, class}
    end
end

function flare.command(fields, state, phase)
    if fields[2] ~= "native-guest-flare-throw" then return end
    local name, guid, toss = fields[3], fields[4], fields[5]
    if phase ~= "hosting" or #fields ~= 5 or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]")
        or (toss ~= "0" and toss ~= "1") or type(SilverfishThrowNativeGuestFlare) ~= "function" then
        error("native_guest_flare_refused_scope", 0)
    end
    local qualified, pawn, detached = pcall(guest_save.exact_item_context, state, phase, name)
    if not qualified or not valid(pawn) or not valid(detached) then error("native_guest_flare_refused_owner", 0) end
    local ok, reason, before, after, class, projectile = SilverfishThrowNativeGuestFlare(
        pawn:GetAddress(), detached:GetAddress(), guid, toss == "1")
    if not ok then error(reason, 0) end
    receipt = {state.map, name, guid, toss, before, after, class, projectile}
    return "stock flare thrown"
end

function flare.rows(state, phase, row)
    if phase == "connected" and intent and intent[1] == state.map then
        return {row("native-guest-flare-input", table.unpack(intent))}
    end
    if phase == "hosting" and receipt and receipt[1] == state.map then
        return {row("native-guest-flare-throw", table.unpack(receipt))}
    end
    return {}
end

return flare
