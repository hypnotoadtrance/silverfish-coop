-- Private source qualification for equipped stock handheld flashlights.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_FLASHLIGHT_PROBE") ~= "1"
    or not mailbox or not mailbox:match("[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local guest_source = os.getenv("SILVERFISH_COOP_GUEST_FLASHLIGHT_PROBE") == "1"
local heavy = guest_source and os.getenv("SILVERFISH_COOP_HEAVY_FLASHLIGHT_PROBE") == "1"
local equipment_asset = heavy and "Eqp_Heavy_Flashlight" or "Eqp_Flashlight"
local mesh_asset = heavy and "SM_Prop_Flashlight_02" or "SM_Prop_Flashlight_01"
local equipment_path = "/Game/Classes/Equipment/" .. equipment_asset .. "." .. equipment_asset .. "_C"
local mesh_name = "StaticMesh /Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/" .. mesh_asset .. "." .. mesh_asset
local source_path = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local click_path = "SoundCue /Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local equip_factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local source, cue_calls, power_calls, hooked, selected_guid = nil, 0, 0, false, nil

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

local function slot_guid(pawn)
    local rows = valid(pawn.Save) and pawn.Save.PlayerEquipment
    local row = rows and rows:GetArrayNum() >= 1 and rows[1]
    if not row or not same(row[item_class], asset(equipment_path)) then return "-" end
    local guid = row[item_guid]
    return string.format("%08x%08x%08x%08x", guid.A & 0xffffffff, guid.B & 0xffffffff,
        guid.C & 0xffffffff, guid.D & 0xffffffff)
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

local function heavy_measurement(equipment, actor, spot)
    local mesh = equipment.SM_Prop_Torch_01
    assert(valid(mesh) and same(mesh:GetOwner(), equipment) and valid(mesh.StaticMesh)
        and mesh.StaticMesh:GetFullName() == mesh_name, "flashlight_probe_heavy_torch_unavailable")
    local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
    local relative = math_api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), mesh:K2_GetComponentToWorld())
    local point, aim, scale, color = relative.Translation, math_api:Quat_Rotator(relative.Rotation), relative.Scale3D, actor.Color
    return string.format("%s\t%s\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f",
        mesh.StaticMesh:GetFullName(), tostring(actor.Type), point.X, point.Y, point.Z, aim.Pitch, aim.Yaw, aim.Roll,
        scale.X, scale.Y, scale.Z, color.R, color.G, color.B)
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
    local census = string.format("%s\t%d\t%d\t%d\t%d\t%d\t%d", remote:GetFullName(), sources, powered, visible, components,
        held_sources, display_meshes)
    if not guest_source then return census end
    assert(remote:HasAuthority() and valid(remote.Save) and not same(remote.Save, local_pawn.Save),
        "flashlight_probe_observer_guest_authority_unavailable")
    local held = remote.EquipedItem
    local held_class = valid(held) and held:GetClass():GetFullName() or "-"
    local mode, stock_power = "-", "-"
    if held_class == "BlueprintGeneratedClass " .. equipment_path then
        local equipment, actor = owned_source(remote, world)
        assert(equipment:HasAuthority(), "flashlight_probe_observer_equipment_authority_unavailable")
        mode, stock_power = tostring(equipment.Tertiary_Mode), tostring(actor.Powered)
    end
    return table.concat({census, slot_guid(remote), held_class, mode, stock_power}, "\t")
end

local function run(operation)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(driver) and world:GetFullName() == map, "flashlight_probe_world_unavailable")
    local pawn = asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "flashlight_probe_local_pawn_unavailable")
    local connected = valid(driver.ServerConnection)
    if connected and not guest_source then
        return observe(world, pawn, assert(math.tointeger(operation:match("^observe(%d+)$"))))
    end
    assert(same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref), "flashlight_probe_local_save_unavailable")
    if not connected then
        assert(pawn:HasAuthority() and #driver.ClientConnections == 1 and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1",
            "flashlight_probe_host_scope_unavailable")
    end

    if guest_source and not connected then
        if operation ~= "seedguest" then
            return observe(world, pawn, assert(math.tointeger(operation:match("^observe(%d+)$"))))
        end
        local guest
        for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
            if valid(candidate) and candidate:HasAuthority() and not candidate:IsLocallyControlled()
                and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then
                assert(not guest, "flashlight_probe_duplicate_guest")
                guest = candidate
            end
        end
        assert(guest and valid(guest.Save) and not same(guest.Save, pawn.Save), "flashlight_probe_detached_guest_unavailable")
        local rows = guest.Save.PlayerEquipment
        assert(rows:GetArrayNum() >= 1 and not valid(rows[1][item_class]), "flashlight_probe_guest_slot_occupied")
        local output = {}
        local type = asset(equipment_path)
        asset(equip_factory):MakeDefaultItem(type, guest, output)
        local row = output.Item or output
        assert(same(row[item_class], type), "flashlight_probe_stock_item_factory_failed")
        rows[1] = row
        local guid = slot_guid(guest)
        assert(guid ~= "-" and guid ~= string.rep("0", 32), "flashlight_probe_detached_guest_slot_readback")
        return guid
    end

    local selected = guest_source and operation:match("^selectlight(%x+)$")
    if selected then
        assert(#selected == 32, "flashlight_probe_guest_slot_scope_invalid")
        if slot_guid(pawn) ~= selected then error("avatar_probe_rig_pending", 0) end
        assert(not valid(pawn.EquipedItem), "flashlight_probe_expected_empty_active_slot")
        pawn:Equip_NextItem(0, false)
        assert(pawn.Save.PlayerEquipIndex == 0, "flashlight_probe_guest_selection_failed")
        selected_guid = selected
    end

    if operation == "seedlight" and not connected then
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
    elseif operation == "clearlight" and (not connected or guest_source and selected_guid == slot_guid(pawn)) then
        if actor.Powered then equipment:Use_Item_Primary(true, 0) end
        local index = math.tointeger(pawn.Save.PlayerEquipIndex)
        local row = index and pawn.Save.PlayerEquipment[index + 1]
        assert(row and same(row[item_class], asset(equipment_path)), "flashlight_probe_removal_scope_unavailable")
        world.OwningGameInstance:RemoveItemFromLoadout(row)
        assert(not valid(pawn.Save.PlayerEquipment[index + 1][item_class]), "flashlight_probe_removal_failed")
        if connected then selected_guid = nil end
        return "restored"
    else
        assert(selected or operation == "seedlight" and not connected or operation == "readlight",
            "flashlight_probe_unknown_action")
    end
    local row = owner_row(pawn, actor, spot)
    if guest_source then
        row = table.concat({row, slot_guid(pawn), tostring(equipment.Stacks), tostring(equipment.Tertiary_Mode)}, "\t")
        if heavy then row = row .. "\t" .. heavy_measurement(equipment, actor, spot) end
    end
    return row
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
