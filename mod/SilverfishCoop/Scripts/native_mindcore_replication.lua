local replication = {}
local mindcore = require("native_mindcore")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local map = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
local world_path = map .. ".DataSilo_01_P"
local persistent = world_path .. ":PersistentLevel."
local core_path = persistent .. "MindCore_BP_C_1"
local cradle_path = persistent .. "MindCore_Cradle_BP_C_1"
local lift_path = persistent .. "RailLift_BP_C_1"
local deck_name = "StaticMeshComponent " .. lift_path .. ".SM_Prop_Elevator_01"
local core_name = "MindCore_BP_C " .. core_path
local cradle_name = "MindCore_Cradle_BP_C " .. cradle_path
local core_class = "/Game/Classes/Actors/MindCore_BP.MindCore_BP_C"
local cradle_class = "/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C"
local capsule_class = "/Script/Engine.CapsuleComponent"
local mesh_class = "/Script/Engine.StaticMeshComponent"
local client_role, host_scope, client_scope

local function valid(object) return object and object:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end
local function exact(object, path) return valid(object) and same(object:GetClass(), StaticFindObject(path)) end

local function actor(value, class, name, world)
    return exact(value, class) and value:GetFullName() == name and not value:IsActorBeingDestroyed()
        and same(value:GetWorld(), world) and same(value:GetOuter(), world.PersistentLevel)
end

local function scene(world, core, cradle)
    if not valid(world) or world:GetFullName() ~= "World " .. world_path
        or not actor(core, core_class, core_name, world)
        or not actor(cradle, cradle_class, cradle_name, world)
        or not same(cradle.MindCore_Ref, core) then return end
    local mesh, capsule, mount = core.PropMesh, cradle.Capsule, cradle.SM_MindCoffin_Cradle
    if not exact(mesh, mesh_class) or not same(mesh:GetOwner(), core) or not same(core.RootComponent, mesh)
        or not exact(capsule, capsule_class) or not same(capsule:GetOwner(), cradle)
        or capsule:GetFullName() ~= "CapsuleComponent " .. cradle_path .. ".Capsule"
        or not exact(mount, mesh_class) or not same(mount:GetOwner(), cradle)
        or not same(capsule:GetAttachParent(), mount) then return end
    return { world = world, core = core, frame = mesh, cradle = cradle, capsule = capsule }
end

local function context_matches(a, b)
    return a and b and same(a.world, b.world) and same(a.core, b.core) and same(a.frame, b.frame) and same(a.cradle, b.cradle)
        and same(a.capsule, b.capsule)
end

local function client_world(world)
    if not client_role or not client_role() or not valid(world)
        or world:GetFullName() ~= "World " .. world_path then return end
    local driver = world.NetDriver
    local connection = valid(driver) and driver.ServerConnection
    return valid(connection) and same(driver.World, world) and same(connection.Driver, driver)
end

local function guard_client(world, origin, known_cradle)
    if not client_world(world) then return end
    local current = scene(world, StaticFindObject(core_path), known_cradle or StaticFindObject(cradle_path))
    if not current then return end
    if not context_matches(current, client_scope) then
        current.origin = origin
        client_scope = current
    end
    local capsule = current.capsule
    if capsule:GetGenerateOverlapEvents() == true then
        -- Only the client's authored cradle sensor is disabled. Tape/terminal input use separate Boxes.
        capsule:SetGenerateOverlapEvents(false)
    end
    if capsule:GetGenerateOverlapEvents() ~= false then
        client_scope.state = "guard_unverified"
    else
        client_scope.state = current.cradle.Status == 0 and "guarded" or "guest_late"
    end
end

local function activate_host(state, phase)
    local accepted, stock = pcall(mindcore.scene, state, phase)
    if not accepted then return end
    local current = scene(stock.world, stock.core, stock.cradle)
    if not current or not current.core:HasAuthority() then return end
    if not context_matches(current, host_scope) then
        current.origin = "authority"
        host_scope = current
    end
    local active = host_scope
    local core = current.core
    if active.state == "replication_unverified" then return end
    if active.state ~= "active" then
        local prior_replication, prior_movement = core.bReplicates, core.bReplicateMovement
        if core.bNetLoadOnClient ~= true or type(prior_replication) ~= "boolean" or type(prior_movement) ~= "boolean" then
            active.state = "replication_unverified"
            return
        end
        local applied = pcall(function()
            if not prior_replication then core:SetReplicates(true) end
            if core.bReplicates ~= true then return end
            if not prior_movement then core:SetReplicateMovement(true) end
        end)
        if not applied or core.bReplicates ~= true or core.bReplicateMovement ~= true then
            pcall(function()
                if core.bReplicateMovement ~= prior_movement then core:SetReplicateMovement(prior_movement) end
                if core.bReplicates ~= prior_replication then core:SetReplicates(prior_replication) end
            end)
            active.state = "replication_unverified"
            return
        end
        active.state = "active"
    elseif core.bReplicates ~= true or core.bReplicateMovement ~= true then
        active.state = "replication_unverified"
        return
    end
    if active.installed ~= stock.installed then
        -- Activation can update the flag before the stock delayed physical mount completes.
        core:ForceNetUpdate()
        active.installed = stock.installed
    end
end

function replication.start(is_client)
    if not enabled or client_role or type(is_client) ~= "function" then return end
    client_role = is_client
    if type(RegisterLoadMapPostHook) == "function" then
        RegisterLoadMapPostHook(function(_, loaded_world)
            pcall(function() guard_client(loaded_world:get(), "load_map") end)
        end)
    end
    if type(RegisterBeginPlayPreHook) == "function" then
        RegisterBeginPlayPreHook(function(context)
            if not client_role() then return end
            pcall(function()
                local cradle = context:get()
                if valid(cradle) and cradle:GetFullName() == cradle_name then
                    guard_client(cradle:GetWorld(), "begin_play", cradle)
                end
            end)
        end)
    end
end

function replication.sync(state, phase)
    if not enabled or state.map ~= map or not state.listening then return end
    if phase == "hosting" and state.server == false then
        pcall(activate_host, state, phase)
    elseif state.server == true then
        pcall(guard_client, state.world, "tick")
    end
end

local function bit(value)
    if type(value) ~= "boolean" then return "-" end
    return value and 1 or 0
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < 10000000
end

local function parent_kind(parent)
    if not valid(parent) then return "free" end
    return parent:GetFullName() == deck_name and "deck" or "other"
end

function replication.rows(state, phase, row)
    if not enabled or state.map ~= map or not state.listening then return {} end
    local ok, result = pcall(function()
        local hosting = phase == "hosting" and state.server == false
        if not hosting and (phase ~= "connected" or state.server ~= true or not client_world(state.world)) then return {} end
        local active = hosting and host_scope or not hosting and client_scope
        if not active or not active.state or not same(state.world, active.world)
            or not context_matches(active, scene(active.world, active.core, active.cradle)) then return {} end
        local position, stage = active.core:K2_GetActorLocation(), math.tointeger(active.cradle.Status)
        if not position or not finite(position.X) or not finite(position.Y) or not finite(position.Z)
            or not stage or stage < 0 or stage > 2 then return {} end
        local replicates, movement = bit(active.core.bReplicates), bit(active.core.bReplicateMovement)
        local overlap = bit(active.capsule:GetGenerateOverlapEvents())
        local observed = active.state
        if hosting and (replicates ~= 1 or movement ~= 1) then observed = "replication_unverified" end
        if not hosting and overlap ~= 0 then observed = "guard_unverified" end
        local attachment = active.core.AttachmentReplication
        return { row("native-mindcore-replication", map, hosting and "host" or "guest", observed, core_name,
            replicates, movement, overlap,
            stage, string.format("%.3f", position.X), string.format("%.3f", position.Y),
            string.format("%.3f", position.Z), active.origin,
            bit(active.frame:IsSimulatingPhysics(FName("None", EFindName.FNAME_Find))),
            parent_kind(active.frame:GetAttachParent()), parent_kind(attachment and attachment.AttachComponent)) }
    end)
    return ok and result or {}
end

function replication.clear()
    -- Leave the old world's capsule quiet while its actors unload. A fresh world uses stock defaults.
    host_scope, client_scope = nil, nil
end

return replication
