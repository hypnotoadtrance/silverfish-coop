-- Private source qualification for an equipped stock Eqp_Flashlight.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_FLASHLIGHT_PROBE") ~= "1"
    or not mailbox or not mailbox:match("[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local equipment_path = "/Game/Classes/Equipment/Eqp_Flashlight.Eqp_Flashlight_C"
local mesh_name = "StaticMesh /Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_Flashlight_01.SM_Prop_Flashlight_01"
local source_path = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local click_path = "SoundCue /Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local equip_factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local source, cue_calls, power_calls, hooked = nil, 0, 0, false

local function valid(value) return value and value:IsValid() end
local function same(a, b) return valid(a) and valid(b) and a:GetAddress() == b:GetAddress() end

local function asset(path)
    local object = StaticFindObject(path)
    if not valid(object) then LoadAsset(path); object = StaticFindObject(path) end
    assert(valid(object), "flashlight_probe_asset_unavailable:" .. path)
    return object
end

local function player_id(pawn)
    local state = pawn.PlayerState
    if not valid(state) and valid(pawn.Controller) then state = pawn.Controller.PlayerState end
    if not valid(state) then return nil end
    local ok, value = pcall(function() return state:GetPlayerId() end)
    return math.tointeger(ok and value or state.PlayerId)
end

local function count_dispatch()
    if hooked then return end
    pcall(RegisterHook, source_path .. ":Send_PowerSignal", function(context)
        local ok, emitter = pcall(function() return context:get() end)
        if ok and same(emitter, source) then power_calls = power_calls + 1 end
    end)
    pcall(RegisterHook, "/Script/Engine.GameplayStatics:PlaySoundAtLocation", function(_, context, sound)
        pcall(function()
            local emitter, cue = context:get(), sound:get()
            if same(emitter, source) and valid(cue) and cue:GetFullName() == click_path then cue_calls = cue_calls + 1 end
        end)
    end)
    hooked = true
end

local function owned_source(pawn, world)
    local equipped = pawn.EquipedItem
    assert(valid(equipped) and same(equipped:GetClass(), asset(equipment_path)) and same(equipped.User, pawn)
        and same(equipped:GetOwner(), pawn) and same(equipped:GetWorld(), world), "flashlight_probe_equipment_unbound")
    local child = equipped.FlashLight_Source_BP
    local actor = valid(child) and child.ChildActor
    local spot = valid(actor) and actor.SpotLight
    assert(valid(actor) and same(actor:GetClass(), asset(source_path)) and same(actor:GetWorld(), world)
        and same(child:GetOwner(), equipped) and same(actor:GetParentActor(), equipped)
        and valid(spot) and same(spot:GetOwner(), actor), "flashlight_probe_child_light_unavailable")
    return equipped, actor, spot
end

local function owner_row(pawn, actor, spot)
    local point, aim = spot:K2_GetComponentLocation(), spot:K2_GetComponentRotation()
    local cookie = spot.LightFunctionMaterial
    return string.format("%d\t%s\t%s\t%s\t%s\t%s\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%.4f\t%s\t%d\t%d",
        assert(player_id(pawn)), actor:GetFullName(), tostring(actor:GetLocalRole()), tostring(actor.bReplicates),
        tostring(actor.Powered), tostring(spot:IsVisible()), point.X, point.Y, point.Z, aim.Pitch, aim.Yaw, aim.Roll,
        spot.Intensity, spot.AttenuationRadius, spot.InnerConeAngle, spot.OuterConeAngle,
        valid(cookie) and cookie:GetFullName() or "-", power_calls, cue_calls)
end

local function observe(world, local_pawn, id)
    local remote
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and not same(candidate, local_pawn) and same(candidate:GetWorld(), world)
            and not candidate:IsActorBeingDestroyed() and player_id(candidate) == id then
            assert(not remote, "flashlight_probe_duplicate_remote")
            remote = candidate
        end
    end
    assert(remote, "flashlight_probe_remote_not_ready")
    local sources, powered, visible, components, held_sources, display_meshes = 0, 0, 0, 0, 0, 0
    for _, actor in ipairs(FindAllOf("FlashLight_Source_BP_C") or {}) do
        if valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed() then
            local parent = actor:GetParentActor()
            local attachment = actor:GetAttachParentActor()
            if same(actor.Instigator, remote) or same(parent, remote) or same(attachment, remote)
                or valid(parent) and same(parent:GetOwner(), remote)
                or valid(attachment) and same(attachment:GetOwner(), remote) then
                sources = sources + 1
                if valid(parent) and parent:GetClass():GetFullName() == "BlueprintGeneratedClass " .. equipment_path then
                    held_sources = held_sources + 1
                end
                if actor.Powered then powered = powered + 1 end
                if valid(actor.SpotLight) and actor.SpotLight:IsVisible() then visible = visible + 1 end
            end
        end
    end
    for _, parameter in ipairs(remote:K2_GetComponentsByClass(asset("/Script/Engine.SpotLightComponent"))) do
        local spot = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
            and parameter:get() or parameter
        if valid(spot) and same(spot:GetOwner(), remote) and spot:IsVisible() then components = components + 1 end
    end
    for _, parameter in ipairs(remote:K2_GetComponentsByClass(asset("/Script/Engine.StaticMeshComponent"))) do
        local component = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
            and parameter:get() or parameter
        local mesh = valid(component) and component.StaticMesh
        if valid(mesh) and mesh:GetFullName() == mesh_name and same(component:GetOwner(), remote) then
            display_meshes = display_meshes + 1
        end
    end
    return string.format("%s\t%d\t%d\t%d\t%d\t%d\t%d", remote:GetFullName(), sources, powered, visible, components,
        held_sources, display_meshes)
end

local function run(operation)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(driver) and world:GetFullName() == map, "flashlight_probe_world_unavailable")
    local pawn = asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "flashlight_probe_local_pawn_unavailable")
    if valid(driver.ServerConnection) then
        return observe(world, pawn, assert(math.tointeger(operation:match("^observe(%d+)$"))))
    end
    assert(pawn:HasAuthority() and #driver.ClientConnections == 1 and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
        and same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref), "flashlight_probe_host_scope_unavailable")

    if operation == "seedlight" then
        local type = asset(equipment_path)
        assert(not valid(pawn.EquipedItem), "flashlight_probe_expected_empty_active_slot")
        local output = {}
        asset(equip_factory):MakeDefaultItem(type, pawn, output)
        local row = output.Item or output
        assert(same(row[item_class], type), "flashlight_probe_stock_item_factory_failed")
        local result = {}
        world.OwningGameInstance:EquipItemToLoadout(row, result)
        assert(result.EquippedSuccessfully == true or result[1] == true, "flashlight_probe_stock_loadout_failed")
    end
    local equipment, actor, spot = owned_source(pawn, world)
    source = actor
    count_dispatch()
    if operation == "lighton" or operation == "lightoff" then
        local requested = operation == "lighton"
        assert(actor.Powered == not requested, "flashlight_probe_unexpected_initial_power")
        equipment:Use_Item_Primary(true, 0)
        equipment:Use_Item_Primary(false, 0)
        assert(actor.Powered == requested and spot:IsVisible() == requested, "flashlight_probe_stock_power_readback_failed")
    elseif operation == "clearlight" then
        if actor.Powered then equipment:Use_Item_Primary(true, 0) end
        local index = math.tointeger(pawn.Save.PlayerEquipIndex)
        local row = index and pawn.Save.PlayerEquipment[index + 1]
        assert(row and same(row[item_class], asset(equipment_path)), "flashlight_probe_removal_scope_unavailable")
        world.OwningGameInstance:RemoveItemFromLoadout(row)
        assert(not valid(pawn.Save.PlayerEquipment[index + 1][item_class]), "flashlight_probe_removal_failed")
        return "restored"
    else
        assert(operation == "seedlight" or operation == "readlight", "flashlight_probe_unknown_action")
    end
    return owner_row(pawn, actor, spot)
end

local last
LoopInGameThreadWithDelay(100, function()
    local file = io.open(mailbox .. "/avatar-probe-command.tsv", "rb")
    if not file then return end
    local text = file:read(100) or ""
    file:close()
    local ticket, operation = text:match("^(%x+)\t([a-z0-9]+)\n$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, value = pcall(run, operation)
    local reply = assert(io.open(mailbox .. "/avatar-probe-reply.tsv", "wb"))
    reply:write(ticket, "\t", ok and "ok\t" or "", value, "\n")
    reply:close()
end)
