local props = {}

local spawner_path = "/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C"
local container_path = "/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C"
local prop_path = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C"
local world, spawner_class, container_class, prop_class, hooked

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function publish(spawner, actor)
    if not valid(world) or not valid(spawner) or not same(spawner:GetClass(), spawner_class)
        or not valid(actor) or not actor:IsA(prop_class) or not actor:HasAuthority()
        or actor:IsActorBeingDestroyed() or actor.bReplicates ~= false or not same(actor:GetWorld(), world) then return end
    local owner = spawner:GetOwner()
    if not valid(owner) or not owner:IsA(container_class) or not same(spawner.Container, owner)
        or not owner:HasAuthority() or not same(owner:GetWorld(), world) then return end
    local mesh = actor.PropMesh
    if not valid(mesh) or not same(mesh, actor.RootComponent) or not same(mesh:GetOwner(), actor) then return end
    actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 15)
    actor:SetReplicates(true)
    actor:SetReplicateMovement(true)
    actor:ForceNetUpdate()
end

function props.sync(state, phase)
    world = phase == "hosting" and state.listening and not state.server and state.world or nil
    if hooked or not valid(world) or type(RegisterHook) ~= "function" then return end
    local spawn = spawner_path .. ":SpawnLoot"
    spawner_class, container_class, prop_class = StaticFindObject(spawner_path),
        StaticFindObject(container_path), StaticFindObject(prop_path)
    if not valid(spawner_class) or not valid(container_class) or not valid(prop_class)
        or not valid(StaticFindObject(spawn)) then return end
    -- SpawnLoot sets the final transform before returning the stock actor.
    RegisterHook(spawn, function(context, _, _, _, _, _, _, _, _, _, object)
        if valid(world) then pcall(publish, context:get(), object:get()) end
    end)
    hooked = true
end

return props
