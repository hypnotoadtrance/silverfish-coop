local props = {}
local carry = require("native_prop_carry")

local spawner_path = "/Game/Classes/Actors/Lootables/Loot_Spawner.Loot_Spawner_C"
local container_path = "/Game/Classes/Actors/Lootables/LootContainer_Master_BP.LootContainer_Master_BP_C"
local prop_path = "/Game/Classes/Actors/PhysicsProp_Master_BP.PhysicsProp_Master_BP_C"
local fuel_class_path = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Standing_FuelTank.Prop_Standing_FuelTank_C"
local barrel_types = {
    Prop_PlasticBarrel = {"Prop_PlasticBarrel"},
    Prop_FuelDrum = {"Prop_FuelDrum", "Prop_FeulDrum"},
}
local fuel_maps = {
    ["/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"] = true,
    ["/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P"] = true,
    ["/Game/Classes/Maps/Maps_Dungeons/Anex_01_P"] = true,
}
local world, spawner_class, container_class, prop_class, hooked
local placed, barrels
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function root_mesh(actor)
    local mesh = actor.PropMesh
    if valid(mesh) and same(mesh, actor.RootComponent) and same(mesh:GetOwner(), actor) then return mesh end
end

local function authored_index(name, labels)
    for _, label in ipairs(labels) do
        local index = name:match(":PersistentLevel%." .. label .. "_C_(%d+)$")
            or name:match(":PersistentLevel%." .. label .. "_GEN_VARIABLE_" .. label .. "_C_CAT_(%d+)$")
        if index then return tonumber(index) end
    end
end

local function publish_motion(actor)
    if actor.bReplicates == true and actor.bReplicateMovement == true then return true end
    actor.NetUpdateFrequency = math.min(actor.NetUpdateFrequency, 15)
    if actor.bReplicates ~= true then actor:SetReplicates(true) end
    if actor.bReplicateMovement ~= true then actor:SetReplicateMovement(true) end
    actor:ForceNetUpdate()
    return actor.bReplicates == true and actor.bReplicateMovement == true
end

local function publish(spawner, actor)
    if not valid(world) or not valid(spawner) or not same(spawner:GetClass(), spawner_class)
        or not valid(actor) or not actor:IsA(prop_class) or not actor:HasAuthority()
        or actor:IsActorBeingDestroyed() or not same(actor:GetWorld(), world) then return end
    local owner = spawner:GetOwner()
    if not valid(owner) or not owner:IsA(container_class) or not same(spawner.Container, owner)
        or not owner:HasAuthority() or not same(owner:GetWorld(), world) then return end
    if root_mesh(actor) and publish_motion(actor) then carry.enroll(actor) end
end

local function placed_tanks(state, phase)
    local hosting = phase == "hosting"
    if not fuel_maps[state.map] or not valid(state.world) or not state.listening
        or (not hosting and phase ~= "connected") or state.server ~= (not hosting) then
        placed = nil
        return
    end
    if placed and placed.phase == phase and same(placed.world, state.world) then return end
    local class = StaticFindObject(fuel_class_path)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local base = StaticFindObject(prop_path)
    if not valid(class) or not valid(statics) or not valid(base) then return end
    local out = {}
    statics:GetAllActorsOfClass(state.world, class, out)
    local actors = out.OutActors or out
    local found = {}
    -- An authored map has at most six of these exact tanks. Never enroll arbitrary spawned or fixed explosives.
    if #actors <= 12 then
        for _, actor in ipairs(actors) do
            if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then
                actor = actor:get()
            end
            if valid(actor) and same(actor:GetClass(), class) and actor:IsA(base) and root_mesh(actor)
                and actor.bNetLoadOnClient == true and not actor:IsActorBeingDestroyed()
                and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
                and (not hosting or actor:HasAuthority() and publish_motion(actor)) then
                found[actor:GetFullName()] = actor
            end
        end
    end
    placed = {world = state.world, phase = phase, actors = found}
end

local function placed_barrels(state, phase)
    if phase ~= "hosting" or not valid(state.world) or not state.listening or state.server ~= false then
        barrels = nil
        return
    end
    if barrels and same(barrels.world, state.world) then return end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(api) then return end
    local active = {}
    local bone = FName("None", EFindName.FNAME_Find)
    for stem, labels in pairs(barrel_types) do
        local class = StaticFindObject("/Game/Assets/StaticMeshes/Mesh_Props/" .. stem .. "." .. stem .. "_C")
        if valid(class) then
            local result = {}
            api:GetAllActorsOfClass(state.world, class, result)
            local matches = result.OutActors or result
            if #matches <= 96 then
                for _, actor in ipairs(matches) do
                    if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
                    local mesh = valid(actor) and root_mesh(actor)
                    local name = mesh and actor:GetFullName()
                    local authored = name and authored_index(name, labels)
                    -- Runtime-spawned copies follow their crate's enrollment.
                    if authored and authored < 10000 and same(actor:GetClass(), class)
                        and actor.bNetLoadOnClient == true and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
                        and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
                        and mesh:IsSimulatingPhysics(bone) == true and publish_motion(actor) and carry.enroll(actor) then
                        active[name] = actor
                    end
                end
            end
        end
    end
    barrels = {world = state.world, actors = active}
end

function props.rows(state, phase, row)
    local result = {}
    local tanks = placed and placed.phase == phase and same(placed.world, state.world) and placed.actors
    for name, actor in pairs(tanks or {}) do
        local mesh = valid(actor) and not actor:IsActorBeingDestroyed() and root_mesh(actor)
        if mesh then
            local rotation = actor:K2_GetActorRotation()
            if rotation then
                result[#result + 1] = row("native-standing-fuel-tank", state.map, name,
                    actor.bReplicateMovement == true and 1 or 0,
                    mesh:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find)) and 1 or 0,
                    string.format("%.2f", rotation.Pitch), string.format("%.2f", rotation.Yaw),
                    string.format("%.2f", rotation.Roll))
            end
        end
    end
    if fixtures and barrels and same(barrels.world, state.world) then
        for name, actor in pairs(barrels.actors) do
            local mesh = valid(actor) and not actor:IsActorBeingDestroyed() and root_mesh(actor)
            if mesh then result[#result + 1] = row("native-authored-physics-barrel", state.map, name, mesh:GetMass()) end
        end
    end
    return result
end

function props.command(fields, state, phase)
    if fields[2] ~= "native-standing-fuel-tank-tip-fixture" then return end
    local mode = fields[4]
    local actor = placed and #fields == 4 and placed.actors[fields[3]]
    if phase ~= "hosting" or not valid(actor) or not actor:HasAuthority() or actor.Broken ~= false
        or not same(actor:GetWorld(), state.world) or not root_mesh(actor)
        or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then
        error("native_standing_fuel_tank_fixture_unavailable", 0)
    end
    if mode == "prepare" then
        local center, pawn = actor:K2_GetActorLocation(), state.local_pawn and state.local_pawn.pawn
        if not center or not valid(pawn) or not pawn:IsLocallyControlled() or not pawn:HasAuthority()
            or pawn:K2_TeleportTo({X = center.X + 1000, Y = center.Y - 1000, Z = center.Z + 100},
                {Pitch = 0, Yaw = 135, Roll = 0}) ~= true then
            error("native_standing_fuel_tank_fixture_approach_refused", 0)
        end
        return "Host staged within standing fuel tank relevancy"
    end
    if mode ~= "tip" then error("native_standing_fuel_tank_fixture_unavailable", 0) end
    local before = actor:K2_GetActorRotation()
    if actor:K2_SetActorRotation({Pitch = before.Pitch, Yaw = before.Yaw, Roll = before.Roll + 55}, true) ~= true then
        error("native_standing_fuel_tank_fixture_rotation_refused", 0)
    end
    actor:ForceNetUpdate()
    return "Standing fuel tank rotation dispatched"
end

function props.sync(state, phase)
    placed_tanks(state, phase)
    placed_barrels(state, phase)
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
