local corpses = {}

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local marker_path = "/Script/Engine.TargetPoint"
local npc_class, marker_class, death_hook
local active_world, markers, ended = nil, {}, {}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function world_driver(actor, guest)
    local world = valid(actor) and actor:GetWorld()
    local driver = valid(world) and world.NetDriver
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection) ~= guest then return end
    return world
end

local function publish(actor)
    local world = world_driver(actor, false)
    if not same(world, active_world) or not actor:HasAuthority() or actor.bReplicates ~= true or actor.Alive ~= false
        or actor:IsActorBeingDestroyed() or not valid(marker_class) then return end
    local address = actor:GetAddress()
    if valid(markers[address]) and same(markers[address]:GetOwner(), actor) then return end
    -- The owner reference resolves to the same NPC over each connection even
    -- when Unreal assigns a different local name to the replicated pawn.
    local marker = world:SpawnActor(marker_class, actor:K2_GetActorLocation(), {Pitch = 0, Yaw = 0, Roll = 0})
    if not valid(marker) then return end
    marker:SetOwner(actor)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker:SetReplicates(true)
    marker:ForceNetUpdate()
    markers[address] = marker
end

local function apply(marker)
    if not valid(marker) or not same(marker:GetClass(), marker_class) or marker:GetLocalRole() ~= 1 then return end
    local actor = marker:GetOwner()
    local world = world_driver(marker, true)
    if not world or not valid(actor) or not valid(npc_class) or not actor:IsA(npc_class)
        or not same(actor:GetWorld(), world) or actor:GetLocalRole() ~= 1 or actor:IsActorBeingDestroyed()
        or actor.Alive == false then return end
    local mesh, capsule, movement = actor.Mesh, actor.CapsuleComponent, actor.CharacterMovement
    if not valid(mesh) or not valid(capsule) or not valid(movement) then return end
    movement:SetMovementMode(0, 0)
    capsule:SetCollisionEnabled(0)
    mesh:K2_DetachFromComponent(1, 1, 1, false)
    mesh:SetCollisionProfileName(FName("Ragdoll", EFindName.FNAME_Find), true)
    -- Local physics presents the fall. The host retains death, loot, AI and saves.
    mesh:SetCollisionResponseToChannel(2, 0)
    mesh:SetSimulatePhysics(true)
    mesh:SetAnimationMode(2, true)
    actor.Alive = false
    actor.Agro = false
end

function corpses.start(role)
    if type(RegisterHook) ~= "function" or type(RegisterEndPlayPreHook) ~= "function" then return end
    marker_class = StaticFindObject(marker_path)
    if not valid(marker_class) then return end
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if role() == "client" then pcall(apply, context:get()) end
    end)
    RegisterEndPlayPreHook(function(context)
        if next(markers) == nil then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        local marker = markers[address]
        if valid(marker) and same(marker:GetOwner(), actor) then
            ended[address], markers[address] = marker, nil
        end
    end)
end

function corpses.sync(state, phase)
    if not valid(marker_class) then return end
    local hosting = phase == "hosting" and state.listening and not state.server
    local world = hosting and state.world or nil
    if not same(world, active_world) then active_world, markers, ended = world, {}, {} end
    if not state.listening then return end
    if not valid(npc_class) then npc_class = StaticFindObject(npc_path) end
    if not valid(npc_class) then return end
    if hosting and not death_hook and valid(StaticFindObject(npc_path .. ":ResolveDeath")) then
        -- UE4SS invokes hooks on Blueprint functions after the original script.
        RegisterHook(npc_path .. ":ResolveDeath", function(context) pcall(publish, context:get()) end)
        death_hook = true
    end
    for address, marker in pairs(ended) do
        ended[address] = nil
        if valid(marker) and same(marker:GetWorld(), world) then marker:K2_DestroyActor() end
    end
end

return corpses
