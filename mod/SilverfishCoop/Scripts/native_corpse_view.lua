local corpses = {}

local npc_path = "/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C"
local marker_path = "/Script/Engine.TargetPoint"
local npc_class, marker_class, clock, death_hook
local active_world, markers, numbered, mirrors, ended, anchors = nil, {}, {}, {}, {}, {}
local pending = {}
local next_sample, corpse_number = 0, 0
local sample_period = .1
local byte_angle = 360 / 256
local anchor_limit = 120

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

local function rotation_byte(angle)
    return math.floor(angle / byte_angle + .5) % 256
end

local function anchor_code(pitch)
    local code = rotation_byte(pitch)
    if code <= 60 then return code end
    if code >= 196 then return code - 135 end
    return 0
end

local function anchor_pitch(index)
    local code = (index or -1) + 1
    -- Stay within the canonical rotator's pitch range so yaw and roll retain
    -- the corpse ID after Unreal converts through a quaternion.
    return (code <= 60 and code or code - 121) * byte_angle
end

local function anchor_index(actor)
    local mesh, address = actor.Mesh, actor:GetAddress()
    local mirror = mirrors[address]
    if mirror and valid(mirror.marker) then
        local index = anchor_code(mirror.marker:K2_GetActorRotation().Pitch) - 1
        return index >= 0 and index < mesh:GetNumBones() and index or nil
    end
    if anchors[address] then return anchors[address] end
    local pelvis = FName("pelvis", EFindName.FNAME_Find)
    local index = mesh:GetBoneIndex(pelvis)
    if index >= 0 and index < anchor_limit and mesh:IsSimulatingPhysics(pelvis) then
        anchors[address] = index
        return index
    end
    -- ResolveDeath can fire before the bodies begin simulating. Retry the
    -- selection on later samples and publish the chosen bone to all observers.
    for candidate = 0, math.min(anchor_limit - 1, mesh:GetNumBones() - 1) do
        if mesh:IsSimulatingPhysics(mesh:GetBoneName(candidate)) then
            anchors[address] = candidate
            return candidate
        end
    end
end

function corpses.position(actor)
    local mesh, index = actor.Mesh, anchor_index(actor)
    if index then
        return mesh:GetSocketLocation(mesh:GetBoneName(index)), index
    end
    return mesh:K2_GetComponentLocation(), nil
end

local function marker_number(marker)
    -- This invisible marker's rotation carries a world-local ID in two
    -- movement-quantized axes. Its Owner remains the replicated corpse.
    local rotation = marker:K2_GetActorRotation()
    return rotation_byte(rotation.Roll) * 256 + rotation_byte(rotation.Yaw)
end

function corpses.binding(actor, guest)
    if not valid(actor) or not valid(npc_class) or not actor:IsA(npc_class)
        or not same(world_driver(actor, guest), active_world) or actor.Alive ~= false
        or actor:IsActorBeingDestroyed() then return end
    local address = actor:GetAddress()
    local mirror = guest and mirrors[address]
    local marker = guest and mirror and mirror.marker or not guest and markers[address]
    local mesh = actor.Mesh
    if not valid(marker) or not same(marker:GetOwner(), actor) or not same(active_world, marker:GetWorld())
        or not valid(mesh) or not same(mesh:GetOwner(), actor) or not mesh:IsAnySimulatingPhysics() then return end
    local number = marker_number(marker)
    if number > 0 then return number, mesh end
end

function corpses.authoritative(number)
    local marker = numbered[number]
    local actor = valid(marker) and marker:GetOwner()
    local identifier, mesh = corpses.binding(actor, false)
    if identifier == number then return actor, mesh end
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
    if corpse_number == 65535 then return end
    corpse_number = corpse_number + 1
    local point, index = corpses.position(actor)
    local marker = world:SpawnActor(marker_class, point, {
        Pitch = anchor_pitch(index),
        Yaw = (corpse_number % 256) * byte_angle, Roll = (corpse_number // 256) * byte_angle,
    })
    if not valid(marker) then return end
    marker:SetOwner(actor)
    marker.bAlwaysRelevant = false
    marker.bNetUseOwnerRelevancy = true
    marker.NetUpdateFrequency = 10
    marker:SetReplicates(true)
    marker:SetReplicateMovement(true)
    marker:ForceNetUpdate()
    markers[address], numbered[corpse_number] = marker, marker
end

local function apply(marker, world)
    if not valid(marker) or not same(marker:GetWorld(), world) then return true end
    local actor = marker:GetOwner()
    if not valid(actor) or not actor:IsA(npc_class) or not same(actor:GetWorld(), world)
        or actor:IsActorBeingDestroyed() then return true end
    -- The owner, simulated role and first movement update can arrive in separate frames.
    if marker:GetLocalRole() ~= 1 or actor:GetLocalRole() ~= 1 or not same(world_driver(marker, true), world)
        or marker_number(marker) == 0 then return end
    local mesh, capsule, movement = actor.Mesh, actor.CapsuleComponent, actor.CharacterMovement
    if not valid(mesh) or not valid(capsule) or not valid(movement) then return end
    local key = actor:GetAddress()
    if not mirrors[key] or not same(mirrors[key].marker, marker) then mirrors[key] = {marker = marker} end
    if actor.Alive == false and mesh:IsAnySimulatingPhysics() then return true end
    movement:SetMovementMode(0, 0)
    capsule:SetCollisionEnabled(0)
    mesh:K2_DetachFromComponent(1, 1, 1, false)
    mesh:SetCollisionProfileName(FName("Ragdoll", EFindName.FNAME_Find), true)
    mesh:SetCollisionEnabled(3)
    -- Local physics presents the fall. The host retains death, loot, AI and saves.
    mesh:SetCollisionResponseToChannel(2, 2) -- Stock LookTrace must be able to select the dead skeletal body.
    mesh:SetCollisionResponseToChannel(3, 2)
    mesh:SetSimulatePhysics(true)
    mesh:SetAnimationMode(2, true)
    actor.Alive = false
    actor.Agro = false
    actor.Tags = {FName("Dead", EFindName.FNAME_Find)}
    return true
end

local function mirror_positions(world, pawn)
    for address, mirror in pairs(mirrors) do
        local marker = mirror.marker
        local actor = valid(marker) and marker:GetOwner()
        if not valid(actor) or not same(marker:GetWorld(), world) or actor:IsActorBeingDestroyed() then
            mirrors[address] = nil
        elseif actor:GetLocalRole() == 1 and actor.Alive == false then
            local mesh = actor.Mesh
            if valid(mesh) and mesh:IsAnySimulatingPhysics() then
                local grip = valid(pawn) and same(pawn.HeldObject, actor) and pawn.PhysicsHandle
                -- The local stock grip already presents this carrier's movement.
                if not valid(grip) or not same(grip:GetGrabbedComponent(), mesh) then
                    local target = marker:K2_GetActorLocation()
                    local offset, error_squared = between(corpses.position(actor), target)
                    if error_squared > 15^2 then mesh:K2_AddWorldOffset(offset, false, {}, true) end
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
                local point, anchor = corpses.position(actor)
                local rotation = marker:K2_GetActorRotation()
                local _, distance_squared = between(marker:K2_GetActorLocation(), point)
                if distance_squared > 3^2 or anchor_code(rotation.Pitch) ~= (anchor or -1) + 1 then
                    rotation.Pitch = anchor_pitch(anchor)
                    marker:K2_SetActorLocationAndRotation(point, rotation, false, {}, true)
                    marker:ForceNetUpdate()
                end
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
        if role() ~= "client" then return end
        local marker = context:get()
        if valid(marker) and same(marker:GetClass(), marker_class) and valid(marker:GetOwner()) then
            pending[marker:GetAddress()] = marker
        end
    end)
    RegisterEndPlayPreHook(function(context)
        if next(markers) == nil and next(mirrors) == nil and next(pending) == nil then return end
        local actor = context:get()
        if not valid(actor) then return end
        local address = actor:GetAddress()
        mirrors[address], anchors[address], pending[address] = nil, nil, nil
        local marker = markers[address]
        if valid(marker) and same(marker:GetOwner(), actor) then
            numbered[marker_number(marker)] = nil
            ended[address], markers[address] = marker, nil
        end
    end)
end

function corpses.sync(state, phase)
    if not valid(marker_class) or not valid(clock) then return end
    local hosting = phase == "hosting" and state.listening and not state.server
    local world = (hosting or phase == "connected" and state.server) and state.world or nil
    if not same(world, active_world) then
        active_world, markers, numbered, mirrors, ended, anchors, next_sample, corpse_number =
            world, {}, {}, {}, {}, {}, 0, 0
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
    if next(hosting and markers or mirrors) ~= nil or not hosting and next(pending) ~= nil then
        local now = clock:GetRealTimeSeconds(world)
        if now >= next_sample then
            next_sample = now + sample_period
            if hosting then
                publish_positions(world)
            else
                for address, marker in pairs(pending) do
                    if apply(marker, world) then pending[address] = nil end
                end
                mirror_positions(world, state.local_pawn and state.local_pawn.pawn)
            end
        end
    end
end

return corpses
