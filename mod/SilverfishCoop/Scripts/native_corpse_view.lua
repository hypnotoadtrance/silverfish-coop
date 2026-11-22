local corpses = {}

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local marker_path = "/Script/Engine.TargetPoint"
local npc_class, marker_class, clock, death_hook
local active_world, markers, mirrors, ended = nil, {}, {}, {}
local next_sample = 0
local sample_period = .1

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

local function anchor(mesh)
    local pelvis = FName("pelvis", EFindName.FNAME_Find)
    if mesh:DoesSocketExist(pelvis) then return mesh:GetSocketLocation(pelvis) end
    return mesh:K2_GetComponentLocation()
end

local function between(point, target)
    local x, y, z = target.X - point.X, target.Y - point.Y, target.Z - point.Z
    return {X = x, Y = y, Z = z}, x * x + y * y + z * z
end

local function publish(actor)
    local world = world_driver(actor, false)
    if not same(world, active_world) or not actor:HasAuthority() or actor.bReplicates ~= true or actor.Alive ~= false
        or actor:IsActorBeingDestroyed() or not valid(marker_class) then return end
    local address = actor:GetAddress()
    if valid(markers[address]) and same(markers[address]:GetOwner(), actor) then return end
    -- The owner reference resolves to the same NPC over each connection even
    -- when Unreal assigns a different local name to the replicated pawn.
    local mesh = actor.Mesh
    if not valid(mesh) then return end
    local marker = world:SpawnActor(marker_class, anchor(mesh), {Pitch = 0, Yaw = 0, Roll = 0})
    if not valid(marker) then return end
    marker:SetOwner(actor)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker.NetUpdateFrequency = 10
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    markers[address] = marker
end

local function apply(marker)
    if not valid(marker) or not same(marker:GetClass(), marker_class) or marker:GetLocalRole() ~= 1 then return end
    local actor = marker:GetOwner()
    local world = world_driver(marker, true)
    if not world or not valid(actor) or not valid(npc_class) or not actor:IsA(npc_class)
        or not same(actor:GetWorld(), world) or actor:GetLocalRole() ~= 1 or actor:IsActorBeingDestroyed() then return end
    local mesh, capsule, movement = actor.Mesh, actor.CapsuleComponent, actor.CharacterMovement
    if not valid(mesh) or not valid(capsule) or not valid(movement) then return end
    local key = marker:GetAddress()
    if not mirrors[key] then mirrors[key] = {actor = marker} end
    if actor.Alive == false and mesh:IsAnySimulatingPhysics() then return end
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

local function mirror_positions(world)
    for address, mirror in pairs(mirrors) do
        local marker = mirror.actor
        local actor = valid(marker) and marker:GetOwner()
        if not valid(actor) or not same(marker:GetWorld(), world) or actor:IsActorBeingDestroyed() then
            mirrors[address] = nil
        elseif actor:GetLocalRole() == 1 and actor.Alive == false then
            local mesh = actor.Mesh
            if valid(mesh) and mesh:IsAnySimulatingPhysics() then
                local target = marker:K2_GetActorLocation()
                local _, changed = between(mirror.target or target, target)
                if not mirror.target or changed > 3^2 then
                    local offset, error_squared = between(anchor(mesh), target)
                    if error_squared > 15^2 then mesh:K2_AddWorldOffset(offset, false, {}, true) end
                    mirror.target = target
                end
            end
        end
    end
end

local function publish_positions(world)
    for address, marker in pairs(markers) do
        local actor = valid(marker) and marker:GetOwner()
        if not valid(actor) or not same(marker:GetWorld(), world) or actor:IsActorBeingDestroyed() then
            markers[address] = nil
        else
            local mesh = actor.Mesh
            if valid(mesh) and actor.Alive == false and mesh:IsAnySimulatingPhysics() then
                local point = anchor(mesh)
                local _, distance_squared = between(marker:K2_GetActorLocation(), point)
                if distance_squared > 3^2 then marker:K2_SetActorLocation(point, false, {}, true) end
            end
        end
    end
end

function corpses.start(role)
    if type(RegisterHook) ~= "function" or type(RegisterEndPlayPreHook) ~= "function" then return end
    marker_class = StaticFindObject(marker_path)
    clock = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(marker_class) then return end
    RegisterHook("/Script/Engine.Actor:OnRep_Owner", function(context)
        if role() == "client" then pcall(apply, context:get()) end
    end)
    RegisterEndPlayPreHook(function(context)
        if next(markers) == nil and next(mirrors) == nil then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        mirrors[address] = nil
        local marker = markers[address]
        if valid(marker) and same(marker:GetOwner(), actor) then
            ended[address], markers[address] = marker, nil
        end
    end)
end

function corpses.sync(state, phase)
    if not valid(marker_class) or not valid(clock) then return end
    local hosting = phase == "hosting" and state.listening and not state.server
    local world = (hosting or phase == "connected" and state.server) and state.world or nil
    if not same(world, active_world) then
        active_world, markers, ended, next_sample = world, {}, {}, 0
        if hosting then mirrors = {} end
    end
    if not valid(world) then return end
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
    if next(hosting and markers or mirrors) ~= nil then
        local now = clock:GetRealTimeSeconds(world)
        if now >= next_sample then
            next_sample = now + sample_period
            if hosting then publish_positions(world) else mirror_positions(world) end
        end
    end
end

return corpses
