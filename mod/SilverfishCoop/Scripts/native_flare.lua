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
local projectiles, replicas = {}, {}
local next_flare_check, next_replica_check = 0, 0
local look_trace, blocking, ignore = 4, 2, 0 -- Stock LookTrace uses ECC_Camera.
local query_and_physics, no_collision = 3, 0
local replica_fuse = 24 * 60 * 60
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local ammo_field = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local empty_guid = ("0"):rep(32)

local function row_guid(row)
    local id = row and row[guid_field]
    if not id then return end
    return ("%08X%08X%08X%08X"):format(id.A & 0xffffffff, id.B & 0xffffffff, id.C & 0xffffffff, id.D & 0xffffffff)
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

local function capsule(actor)
    local root = actor.Capsule
    local class = StaticFindObject("/Script/Engine.CapsuleComponent")
    if same(root, actor.RootComponent) and same(root:GetClass(), class)
        and same(root:GetOwner(), actor) then return root end
end

function flare.carry_root(actor, world)
    if not valid(actor) then return end
    local key = actor:GetAddress()
    local entry = projectiles[key] or replicas[key]
    if not entry or not same(entry.actor, actor) or not valid(entry.root) or actor:IsActorBeingDestroyed()
        or not same(actor:GetWorld(), world) or actor.bReplicateMovement ~= true then return end
    if same(actor.RootComponent, entry.root) and same(entry.root:GetOwner(), actor) then return entry.root end
end

local function enable_grip(actor, root)
    local class = StaticFindObject("/Script/Engine.PrimitiveComponent")
    if not valid(class) then return end
    local parts = actor:K2_GetComponentsByClass(class)
    if not parts or #parts < 3 or #parts > 12 then return end
    actor:SetActorEnableCollision(false)
    local found = false
    for index = 1, #parts do
        local part = parts[index]
        if type(part) == "userdata" and type(part.type) == "function" and part:type() == "RemoteUnrealParam" then
            part = part:get()
        end
        if not valid(part) or not same(part:GetOwner(), actor) then return end
        part:SetGenerateOverlapEvents(false)
        part:SetNotifyRigidBodyCollision(false)
        part:SetCollisionResponseToAllChannels(ignore)
        if same(part, root) then found = true else part:SetCollisionEnabled(no_collision) end
    end
    if not found then return end
    -- The inert replica needs a stock trace target and a body for PhysicsHandle.
    -- Object collision responses stay ignored, which prevents local impact/ignition.
    root:SetCollisionResponseToChannel(look_trace, blocking)
    for channel = 0, 31 do
        if root:GetCollisionResponseToChannel(channel) ~= (channel == look_trace and blocking or ignore) then return end
    end
    root:SetEnableGravity(false)
    root:SetCollisionEnabled(query_and_physics)
    actor:SetActorEnableCollision(true)
    if not root:IsAnySimulatingPhysics() then root:SetSimulatePhysics(true) end
    if root:IsAnySimulatingPhysics() and root:GetCollisionEnabled() == query_and_physics then return root end
end

local function landed(actor)
    local movement = actor.ReplicatedMovement
    return movement and movement.bRepPhysics == true
end

local function update_replica_grips(state, phase)
    if not next(replicas) then return end
    local active = phase == "connected" and state.server == true and state.listening
    local now = os.clock()
    if active and now < next_replica_check then return end
    next_replica_check = now + .25
    for key, entry in pairs(replicas) do
        local actor = entry.actor
        if not valid(actor) or actor:IsActorBeingDestroyed() or actor:GetLocalRole() ~= 1 then
            replicas[key] = nil
        elseif not active or not same(actor:GetWorld(), state.world) then
            if entry.root then actor:SetActorEnableCollision(false) end
            entry.root = nil
            if not same(actor:GetWorld(), state.world) then replicas[key] = nil end
        elseif not entry.refused then
            local known, rigid = pcall(landed, actor)
            if known and rigid and actor.Lit == true and actor.bReplicateMovement == true then
                if not entry.root then
                    local ok, root = pcall(function()
                        local owned = capsule(actor)
                        if owned then return enable_grip(actor, owned) end
                    end)
                    if ok and root then
                        entry.root = root
                    else
                        actor:SetActorEnableCollision(false)
                        entry.refused = true
                    end
                end
            elseif entry.root then
                actor:SetActorEnableCollision(false)
                entry.root = nil
            end
        end
    end
end

function flare.start(role)
    if type(RegisterBeginPlayPreHook) ~= "function" or type(RegisterBeginPlayPostHook) ~= "function" then return end
    RegisterBeginPlayPreHook(function(context)
        if role() ~= "client" then return end
        local actor = qualified(context, true)
        if actor then
            actor:SetActorEnableCollision(false)
            -- FuseTime is supplied by the equipped flare and is not replicated.
            -- The host retires this actor after the real stock fuse and fade.
            actor.FuseTime = replica_fuse
        end
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
            replicas[actor:GetAddress()] = {actor = actor}
        else
            actor.NetUpdateFrequency = 24
            actor:SetReplicates(true)
            actor:SetReplicateMovement(true)
            actor:ForceNetUpdate()
            projectiles[actor:GetAddress()] = {actor = actor, root = capsule(actor)}
        end
    end)
end

local function faded(light)
    return not valid(light) or type(light.Intensity) == "number" and light.Intensity <= .001
end

local function retire_spent(state)
    if not valid(state.world) or not state.listening or state.server ~= false or not next(projectiles) then return end
    local now = os.clock()
    if now < next_flare_check then return end
    next_flare_check = now + .25
    for key, entry in pairs(projectiles) do
        local actor = entry.actor
        if not valid(actor) or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), state.world) then
            projectiles[key] = nil
        elseif actor.Lit == false and faded(actor.PointLight) and faded(actor.PointLight1) then
            -- A newly replicated stock actor starts its own burn timer in BeginPlay.
            -- Keep the stock fade and remove the spent projectile from the network.
            actor:K2_DestroyActor()
            if not valid(actor) or actor:IsActorBeingDestroyed() then projectiles[key] = nil end
        end
    end
end

function flare.sync(state, phase)
    if phase == "hosting" then retire_spent(state) end
    update_replica_grips(state, phase)
    if phase ~= "connected" or not state.server or not state.listening or type(SilverfishTakeLocalGuestFlare) ~= "function" then
        intent = nil
        return
    end
    local local_pawn = state.local_pawn
    if not local_pawn or not valid(local_pawn.pawn) then return end
    local sequence, guid, toss, class = SilverfishTakeLocalGuestFlare(local_pawn.pawn:GetAddress())
    if sequence and flare.equipment[class] then
        local pawn = local_pawn.pawn
        intent = {state.map, local_pawn.name, sequence, guid, toss and 1 or 0, class}
        intent.actor, intent.slot, intent.save = pawn.EquipedItem, pawn.Save.PlayerEquipIndex, pawn.Save
    end
end

local function complete(fields, state, phase)
    local sequence, guid, before, after = math.tointeger(fields[3]), fields[4], math.tointeger(fields[5]), math.tointeger(fields[6])
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase ~= "connected" or not state.server or not intent or not valid(pawn) or not pawn:IsLocallyControlled()
        or state.map ~= intent[1] or #fields ~= 6 or sequence ~= intent[3] or guid ~= intent[4]
        or not before or before < 1 or after ~= before - 1 then error("native_guest_flare_local_receipt_expired", 0) end
    local actor, save, slot = intent.actor, intent.save, intent.slot
    if not same(actor, pawn.EquipedItem) or not same(save, pawn.Save) or slot ~= save.PlayerEquipIndex then
        return "stock flare slot has changed"
    end
    local row = save.PlayerEquipment[slot + 1]
    local identity = row_guid(row)
    local amount = row and row[ammo_field]
    local class = actor:GetClass()
    local expected = StaticFindObject(intent[6])
    if not same(class, expected) or not same(actor.Player_Ref, pawn) or not same(actor:GetOwner(), pawn)
        or not (identity == guid and same(row[class_field], class) and (amount == before or amount == after)
            or after == 0 and row and not valid(row[class_field]) and identity == empty_guid) then
        error("native_guest_flare_local_receipt_mismatch", 0)
    end
    -- Consume adjusts the attached co-op row from the actor's own Ammo value.
    -- A newer projection can already have the resulting count in that row.
    if actor.Ammo == before then
        actor:Consume(1, {})
    elseif actor.Ammo ~= after then
        error("native_guest_flare_local_ammo_mismatch", 0)
    end
    local updated = save.PlayerEquipment[slot + 1]
    if after == 0 then
        if not updated or valid(updated[class_field]) or row_guid(updated) ~= empty_guid then
            error("native_guest_flare_local_consumption_unverified", 0)
        end
        if same(actor, pawn.EquipedItem) then
            -- An empty quick-slot ring can leave a reference to the retired actor.
            if not actor:IsActorBeingDestroyed() then actor:K2_DestroyActor() end
            pawn.EquipedItem = nil
        end
    elseif actor.Ammo ~= after or updated[ammo_field] ~= after then
        error("native_guest_flare_local_consumption_unverified", 0)
    end
    return "stock flare charge reconciled"
end

function flare.command(fields, state, phase)
    if fields[2] == "native-guest-flare-complete" then return complete(fields, state, phase) end
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
