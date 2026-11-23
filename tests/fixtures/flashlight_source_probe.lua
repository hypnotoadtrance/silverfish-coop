-- Private source qualification for equipped stock handheld flashlights.
local mailbox = os.getenv("SILVERFISH_COOP_BRIDGE")
if os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" or os.getenv("SILVERFISH_COOP_FLASHLIGHT_PROBE") ~= "1"
    or not mailbox or not mailbox:match("[/\\]avatar%-%x+[/\\]run[/\\]game%-bridge[/\\]%x+$") then return end

local map = "World /Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"
local guest_source = os.getenv("SILVERFISH_COOP_GUEST_FLASHLIGHT_PROBE") == "1"
local guest_offhand = os.getenv("SILVERFISH_COOP_GUEST_OFFHAND_PROBE") == "1"
local offhand_trace = guest_offhand and os.getenv("SILVERFISH_COOP_GUEST_OFFHAND_TRACE") == "1"
local offhand = guest_offhand or not guest_source and os.getenv("SILVERFISH_COOP_OFFHAND_FLASHLIGHT_PROBE") == "1"
local offhand_view = guest_offhand or offhand and os.getenv("SILVERFISH_COOP_OFFHAND_FLASHLIGHT_VIEW_PROBE") == "1"
local heavy = os.getenv("SILVERFISH_COOP_HEAVY_FLASHLIGHT_PROBE") == "1"
local heavy_view = heavy and os.getenv("SILVERFISH_COOP_HEAVY_FLASHLIGHT_VIEW_PROBE") == "1"
local equipment_asset = heavy and "Eqp_Heavy_Flashlight" or "Eqp_Flashlight"
local mesh_asset = heavy and "SM_Prop_Flashlight_02" or "SM_Prop_Flashlight_01"
local equipment_path = "/Game/Classes/Equipment/" .. equipment_asset .. "." .. equipment_asset .. "_C"
local mesh_name = "StaticMesh /Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/" .. mesh_asset .. "." .. mesh_asset
local source_path = "/Game/Classes/Equipment/Equipment_Assets/FlashLight_Source_BP.FlashLight_Source_BP_C"
local click_path = "SoundCue /Game/Assets/Sounds/CUEs/Mechanical_Cues/Switch_Reset_Cue.Switch_Reset_Cue"
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local equip_factory = "/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C"
local pistol_path = "/Game/Classes/Weapons/Wep_Pistol.Wep_Pistol_C"
local source, cue_calls, power_calls, hooked, selected_guid = nil, 0, 0, false, nil
local offhand_pawn, primary, seeded_slots
local guest_hold, host_guard
local trace_pawn, trace_instance, trace_hooks, trace_rows

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
    local function owns_source(emitter)
        if same(emitter, source) then return true end
        local item = valid(offhand_pawn) and offhand_pawn.OffhandEquipment
        local child = valid(item) and item.FlashLight_Source_BP
        return valid(child) and same(emitter, child.ChildActor)
    end
    pcall(RegisterHook, source_path .. ":Send_PowerSignal", function(context)
        local ok, emitter = pcall(function() return context:get() end)
        if ok and owns_source(emitter) then power_calls = power_calls + 1 end
    end)
    pcall(RegisterHook, "/Script/Engine.GameplayStatics:PlaySoundAtLocation", function(_, context, sound)
        pcall(function()
            local emitter, cue = context:get(), sound:get()
            if owns_source(emitter) and valid(cue) and cue:GetFullName() == click_path then cue_calls = cue_calls + 1 end
        end)
    end)
    hooked = true
end

local function source_from(equipped, world)
    local child = equipped.FlashLight_Source_BP
    local actor = valid(child) and child.ChildActor
    local spot = valid(actor) and actor.SpotLight
    assert(valid(actor) and same(actor:GetClass(), asset(source_path)) and same(actor:GetWorld(), world)
        and same(child:GetOwner(), equipped) and same(actor:GetParentActor(), equipped)
        and valid(spot) and same(spot:GetOwner(), actor), "flashlight_probe_child_light_unavailable")
    return equipped, actor, spot
end

local function owned_source(pawn, world)
    local equipped = pawn.EquipedItem
    assert(valid(equipped) and same(equipped:GetClass(), asset(equipment_path)) and same(equipped.User, pawn)
        and same(equipped:GetOwner(), pawn) and same(equipped:GetWorld(), world), "flashlight_probe_equipment_unbound")
    return source_from(equipped, world)
end

local function item_identity(row)
    local guid = row[item_guid]
    return string.format("%08x%08x%08x%08x", guid.A & 0xffffffff, guid.B & 0xffffffff,
        guid.C & 0xffffffff, guid.D & 0xffffffff)
end

local function trace_event(event, argument)
    if not trace_rows or not valid(trace_pawn) or not trace_pawn:IsLocallyControlled()
        or trace_pawn:HasAuthority() or not same(trace_pawn:GetWorld().OwningGameInstance, trace_instance) then return end
    local save, equipment, torch = trace_pawn.Save, trace_pawn.EquipedItem, trace_pawn.OffhandEquipment
    local slots = valid(save) and save.PlayerEquipment
    local offhand_slot = slots and slots:GetArrayNum() >= 5 and slots[5]
    local gear_slot = slots and slots:GetArrayNum() >= 2 and slots[2]
    local function named(object) return valid(object) and object:GetFullName() or "-" end
    local hand_class, item_type, two_handed = "-", "-", false
    if valid(equipment) then
        hand_class, item_type = named(equipment:GetClass()), tostring(equipment.ItemType)
        local ok, tagged = pcall(function()
            for _, tag in ipairs(equipment.Tags) do
                if tag:ToString() == "TwoHanded" then return true end
            end
            return false
        end)
        if ok then two_handed = tagged else two_handed = "unavailable" end
    end
    if #trace_rows >= 80 then table.remove(trace_rows, 1) end
    trace_rows[#trace_rows + 1] = table.concat({event, os.clock(), tostring(argument), named(save),
        tostring(same(save, trace_instance.SaveSlot_Ref)), tostring(valid(save) and save.PlayerOffhandingFlashlight),
        tostring(trace_pawn.ForceOffhandLower), tostring(valid(save) and save.PlayerEquipIndex), hand_class, item_type,
        tostring(two_handed), named(equipment), named(torch), gear_slot and named(gear_slot[item_class]) or "-",
        gear_slot and item_identity(gear_slot) or "-", offhand_slot and named(offhand_slot[item_class]) or "-",
        offhand_slot and item_identity(offhand_slot) or "-"}, "\t")
end

local function start_offhand_trace(pawn, instance)
    assert(not trace_rows, "flashlight_probe_trace_already_started")
    trace_pawn, trace_instance, trace_hooks, trace_rows = pawn, instance, {}, {}
    local function observe(path, object, names)
        for name, arg in pairs(names) do
            -- UE4SS runs script UFunction hooks after their Blueprint body.
            local ok, handle = pcall(RegisterHook, path .. ":" .. name, function(context, parameter)
                local read_ok, detail = pcall(function()
                    if same(context:get(), object) then
                        local value = "-"
                        if arg then value = parameter:get() end
                        trace_event(name, value)
                    end
                end)
                if not read_ok then trace_hooks[name] = "callback error: " .. tostring(detail):gsub("[%c]", " ") end
            end)
            trace_hooks[name] = ok and "registered" or tostring(handle):gsub("[%c]", " ")
        end
    end
    observe("/Game/Classes/Player/Player_FP.Player_FP_C", pawn, {
        OffhandFlashlight = true, LowerOffhand = true, Equip_NextItem = true,
        CanOffhandFlashlight = true, ReEquipFlashlight = false, PassivesUpdate = false,
    })
    observe("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C", instance, {
        Compile_PassiveEffects = false, Calc_CarryWeight = false,
    })
    trace_event("fixture_trace_started", "-")
    return tostring(os.clock())
end

local function stop_offhand_trace()
    assert(trace_rows, "flashlight_probe_trace_not_started")
    trace_event("fixture_trace_stopped", "-")
    local fields = {#trace_rows}
    for _, row in ipairs(trace_rows) do fields[#fields + 1] = row end
    for name, result in pairs(trace_hooks) do fields[#fields + 1] = name .. "\t" .. result end
    trace_pawn, trace_instance, trace_rows = nil, nil, nil
    return table.concat(fields, "\t")
end

local function slot_guid(pawn)
    local rows = valid(pawn.Save) and pawn.Save.PlayerEquipment
    local row = rows and rows:GetArrayNum() >= 1 and rows[1]
    if not row or not same(row[item_class], asset(equipment_path)) then return "-" end
    return item_identity(row)
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

local function measure_local_beam(spot, mesh, color)
    local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
    local relative = math_api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), mesh:K2_GetComponentToWorld())
    local point, aim, scale = relative.Translation, math_api:Quat_Rotator(relative.Rotation), relative.Scale3D
    return string.format("%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f\t%.9f",
        point.X, point.Y, point.Z, aim.Pitch, aim.Yaw, aim.Roll,
        scale.X, scale.Y, scale.Z, color.R, color.G, color.B)
end

local function torch_measurement(equipment, actor, spot)
    local mesh = equipment.SM_Prop_Torch_01
    assert(valid(mesh) and same(mesh:GetOwner(), equipment) and valid(mesh.StaticMesh)
        and mesh.StaticMesh:GetFullName() == mesh_name, "flashlight_probe_stock_torch_unavailable")
    return table.concat({mesh.StaticMesh:GetFullName(), tostring(actor.Type),
        measure_local_beam(spot, mesh, actor.Color)}, "\t")
end

local function transform_row(value, math_api)
    local p, r, s = value.Translation, math_api:Quat_Rotator(value.Rotation), value.Scale3D
    return string.format("%.7f\t%.7f\t%.7f\t%.7f\t%.7f\t%.7f\t%.7f\t%.7f\t%.7f",
        p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll, s.X, s.Y, s.Z)
end

local function seed_loadout(pawn, world, class)
    local output, result = {}, {}
    asset(equip_factory):MakeDefaultItem(class, pawn, output)
    local row = output.Item or output
    assert(same(row[item_class], class), "flashlight_probe_stock_item_factory_failed")
    local guid = item_identity(row)
    assert(guid ~= string.rep("0", 32), "flashlight_probe_stock_guid_unavailable")
    world.OwningGameInstance:EquipItemToLoadout(row, result)
    assert(result.EquippedSuccessfully == true or result[1] == true, "flashlight_probe_stock_loadout_failed")
    for index = 1, 8 do
        local saved = pawn.Save.PlayerEquipment[index]
        if same(saved[item_class], class) and item_identity(saved) == guid then return {index = index, guid = guid} end
    end
    error("flashlight_probe_stock_slot_readback_failed", 0)
end

local function stock_row(pawn, class)
    local output = {}
    asset(equip_factory):MakeDefaultItem(class, pawn, output)
    local row = output.Item or output
    assert(same(row[item_class], class) and item_identity(row) ~= string.rep("0", 32),
        "flashlight_probe_guest_offhand_stock_factory_failed")
    return row
end

local function seed_guest_offhand(world, host)
    local guest
    for _, candidate in ipairs(FindAllOf("Player_FP_C") or {}) do
        if valid(candidate) and candidate:HasAuthority() and not candidate:IsLocallyControlled()
            and same(candidate:GetWorld(), world) and not candidate:IsActorBeingDestroyed() then
            assert(not guest, "flashlight_probe_duplicate_guest")
            guest = candidate
        end
    end
    assert(guest and valid(guest.Save) and not same(guest.Save, host.Save), "flashlight_probe_detached_guest_unavailable")
    local rows = guest.Save.PlayerEquipment
    assert(rows:GetArrayNum() >= 8 and not valid(rows[2][item_class]) and not valid(rows[5][item_class]),
        "flashlight_probe_guest_offhand_slots_occupied")
    local pistol = stock_row(guest, asset(pistol_path))
    local torch = stock_row(guest, asset(equipment_path))
    local pistol_id, torch_id = item_identity(pistol), item_identity(torch)
    assert(pistol_id ~= torch_id, "flashlight_probe_duplicate_offhand_guid")
    rows[2], rows[5] = pistol, torch
    assert(item_identity(rows[2]) == pistol_id and item_identity(rows[5]) == torch_id,
        "flashlight_probe_guest_offhand_slots_unverified")
    return table.concat({guest:GetFullName(), pistol_id, torch_id}, "\t")
end

local function host_offhand_save_boundary(pawn)
    local save = pawn.Save
    local rows = save.PlayerEquipment
    local state = {save:GetFullName(), tostring(save.PlayerEquipIndex), tostring(save.PlayerOffhandingFlashlight)}
    assert(rows:GetArrayNum() >= 8, "flashlight_probe_host_save_slots_unavailable")
    for index = 1, 8 do
        state[#state + 1] = valid(rows[index][item_class]) and item_identity(rows[index]) or "-"
    end
    local fingerprint = table.concat(state, "\t")
    if host_guard then assert(host_guard == fingerprint, "flashlight_probe_host_save_boundary_changed")
    else host_guard = fingerprint end
    return fingerprint
end

local function stock_c_edge(operation, pawn)
    local controller = pawn.Controller
    local input = asset("/Script/EnhancedInput.Default__EnhancedInputLibrary")
    local action = asset("/Game/Classes/Player/Player_Inputs/IA_ModeToggle.IA_ModeToggle")
    assert(valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn)
        and not pawn:HasAuthority(), "flashlight_probe_guest_offhand_c_scope_unavailable")
    local before = pawn.Save.PlayerOffhandingFlashlight
    if operation == "coffhandstart" then
        assert(not guest_hold, "flashlight_probe_guest_offhand_c_already_pressed")
        guest_hold = {since = os.clock(), before = before}
        trace_event(operation, "-")
        -- The cooked C binding has no Started delegate; the initial pressed sample enters Ongoing.
        pawn:InpActEvt_IA_ModeToggle_K2Node_EnhancedInputActionEvent_28(input:MakeInputActionValueOfType(1, 0, 0, 0), 0, 0, action)
        assert(pawn.Save.PlayerOffhandingFlashlight == before, "flashlight_probe_guest_offhand_short_hold_changed")
        return "ongoing_start\t0\t" .. tostring(before)
    end
    assert(guest_hold, "flashlight_probe_guest_offhand_c_not_pressed")
    local elapsed = os.clock() - guest_hold.since
    if operation == "coffhandhold" then
        if elapsed < .32 then error("avatar_probe_rig_pending", 0) end
        assert(not guest_hold.held and before == guest_hold.before, "flashlight_probe_guest_offhand_hold_scope_changed")
        trace_event(operation, elapsed)
        pawn:InpActEvt_IA_ModeToggle_K2Node_EnhancedInputActionEvent_28(
            input:MakeInputActionValueOfType(1, 0, 0, 0), elapsed, elapsed, action)
        assert(pawn.Save.PlayerOffhandingFlashlight == not before, "flashlight_probe_guest_offhand_stock_hold_unverified")
        guest_hold.held = true
        return "ongoing_hold\t" .. elapsed .. "\t" .. tostring(not before)
    end
    assert(operation == "coffhandrelease" and guest_hold.held, "flashlight_probe_guest_offhand_c_hold_missing")
    trace_event(operation, elapsed)
    pawn:InpActEvt_IA_ModeToggle_K2Node_EnhancedInputActionEvent_29(input:MakeInputActionValueOfType(0, 0, 0, 0), elapsed, elapsed, action)
    guest_hold = nil
    assert(pawn.Save.PlayerOffhandingFlashlight == before, "flashlight_probe_guest_offhand_stock_release_changed")
    return "completed_release\t" .. elapsed .. "\t" .. tostring(before)
end

local function offhand_operation(operation, pawn, world)
    local save = pawn.Save
    if operation == "seedoffhand" then
        assert(valid(save) and save.PlayerEquipment:GetArrayNum() >= 8 and not valid(pawn.EquipedItem)
            and not valid(pawn.OffhandEquipment) and save.PlayerOffhandingFlashlight == false,
            "flashlight_probe_offhand_loadout_occupied")
        local flashlight_class, pistol = asset(equipment_path), asset(pistol_path)
        for index = 1, 8 do
            assert(not valid(save.PlayerEquipment[index][item_class]), "flashlight_probe_offhand_fixture_already_owned")
        end
        seeded_slots = {seed_loadout(pawn, world, flashlight_class), seed_loadout(pawn, world, pistol)}
        if save.PlayerEquipIndex ~= seeded_slots[2].index - 1 then pawn:Equip_NextItem(seeded_slots[2].index - 1, false) end
        primary, offhand_pawn = pawn.EquipedItem, pawn
        assert(valid(primary) and same(primary:GetClass(), pistol) and same(primary.User, pawn),
            "flashlight_probe_offhand_primary_unavailable")
        count_dispatch()
    end
    assert(seeded_slots and same(offhand_pawn, pawn), "flashlight_probe_offhand_scope_unavailable")
    for _, slot in ipairs(seeded_slots) do
        assert(item_identity(save.PlayerEquipment[slot.index]) == slot.guid, "flashlight_probe_offhand_slot_changed")
    end
    assert(same(pawn.EquipedItem, primary) and save.PlayerEquipIndex == seeded_slots[2].index - 1,
        "flashlight_probe_offhand_primary_changed")
    if guest_offhand and operation:match("^coffhand") then return stock_c_edge(operation, pawn) end
    if operation == "offhandon" then
        local eligible = {}
        pawn:CanOffhandFlashlight(eligible)
        assert(eligible.CanEquip == true or eligible[1] == true, "flashlight_probe_stock_offhand_ineligible")
        pawn:OffhandFlashlight(true)
    elseif operation == "turnoffhand" and offhand_view then
        assert(save.PlayerOffhandingFlashlight == true and valid(pawn.Controller), "flashlight_probe_offhand_turn_unavailable")
        trace_event(operation, "-")
        local rot = pawn.Controller:GetControlRotation()
        pawn.Controller:SetControlRotation({Pitch = rot.Pitch < 0 and 25 or -25, Yaw = rot.Yaw, Roll = 0})
    elseif operation == "offhandoff" or operation == "waitoffhand" or operation == "clearoffhand" then
        if operation == "offhandoff" and save.PlayerOffhandingFlashlight then pawn:OffhandFlashlight(false) end
        assert(save.PlayerOffhandingFlashlight == false, "flashlight_probe_stock_offhand_flag_clear_failed")
        -- Stock ManipulateOffhand destroys the actor after it reaches the lowered position.
        local item = pawn.OffhandEquipment
        if operation ~= "offhandoff" and valid(item) and not item:IsActorBeingDestroyed() then
            error("avatar_probe_rig_pending", 0)
        end
        if operation == "clearoffhand" then
            for _, slot in ipairs(seeded_slots) do
                world.OwningGameInstance:RemoveItemFromLoadout(save.PlayerEquipment[slot.index])
                assert(not valid(save.PlayerEquipment[slot.index][item_class]), "flashlight_probe_offhand_slot_clear_failed")
            end
            offhand_pawn, primary, seeded_slots = nil, nil, nil
            return "restored"
        end
    else
        assert(operation == "seedoffhand" or operation == "readoffhand", "flashlight_probe_offhand_unknown_operation")
    end
    assert(same(pawn.EquipedItem, primary), "flashlight_probe_offhand_primary_changed_after_input")
    local item = pawn.OffhandEquipment
    local live_item = valid(item) and not item:IsActorBeingDestroyed()
    local row = table.concat({assert(player_id(pawn)), primary:GetFullName(), seeded_slots[2].index - 1, seeded_slots[2].guid,
        seeded_slots[1].index - 1, seeded_slots[1].guid, tostring(save.PlayerOffhandingFlashlight),
        live_item and item:GetFullName() or "-"}, "\t")
    if not save.PlayerOffhandingFlashlight then return row end
    local camera = pawn.Camera
    assert(live_item and same(item:GetClass(), asset(equipment_path)) and same(item.User, pawn) and valid(camera)
        and valid(item.Box) and same(item.Box:GetAttachParent(), camera), "flashlight_probe_stock_offhand_mount_unavailable")
    local _, actor, spot = source_from(item, world)
    assert(actor.Powered == true and spot:IsVisible(), "flashlight_probe_stock_offhand_dark")
    local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
    local box = math_api:MakeRelativeTransform(item.Box:K2_GetComponentToWorld(), camera:K2_GetComponentToWorld())
    local torch = math_api:MakeRelativeTransform(item.SM_Prop_Torch_01:K2_GetComponentToWorld(), pawn:GetTransform())
    local item_owner = item:GetOwner()
    return table.concat({row, tostring(item.bReplicates), camera:GetFullName(),
        valid(item_owner) and item_owner:GetFullName() or "-",
        transform_row(box, math_api), transform_row(pawn.OffhandTransform, math_api), transform_row(torch, math_api),
        owner_row(pawn, actor, spot), torch_measurement(item, actor, spot)}, "\t")
end

-- Read-only test mirror of the released guest offhand capture checks.
-- Mirror the released stock capture checks in the probe VM. The live
-- SilverfishCoop Lua scope and its downed registry belong to a different VM.
local function diagnose_offhand(world, pawn, driver)
    local function exact(object, path)
        return valid(object) and same(object:GetClass(), StaticFindObject(path))
    end
    local scope, values = "none", {}
    local function metric(name, value)
        values[#values + 1] = name
        values[#values + 1] = tostring(value)
    end
    local function require_gate(name, condition)
        if not condition then scope = name; error(name, 0) end
    end
    local ok, detail = pcall(function()
        local connection, controller = driver.ServerConnection, pawn.Controller
        require_gate("scope.driver_world", same(driver.World, world))
        require_gate("scope.server_connection", valid(connection) and same(connection.Driver, driver))
        require_gate("scope.controller", valid(controller) and controller:IsLocalController() and same(controller.Pawn, pawn))
        require_gate("scope.local_pawn", not pawn:IsActorBeingDestroyed() and pawn:IsLocallyControlled()
            and same(pawn:GetWorld(), world) and pawn:HasAuthority() == false)
        metric("stock_dead", pawn.Dead)
        local instance, save = world.OwningGameInstance, pawn.Save
        require_gate("capture.guest_save_slot", valid(instance) and same(save, instance.SaveSlot_Ref))
        metric("stock_offhand_flag", save.PlayerOffhandingFlashlight)
        require_gate("stock_offhand.flag", save.PlayerOffhandingFlashlight == true)

        local pistol, slots = pawn.EquipedItem, save.PlayerEquipment
        require_gate("loadout.pistol_class", exact(pistol, pistol_path))
        require_gate("loadout.pistol_authority", pistol:HasAuthority())
        require_gate("loadout.pistol_user", same(pistol.User, pawn))
        local index = math.tointeger(save.PlayerEquipIndex)
        metric("selected_slot", index)
        require_gate("loadout.selected_stock", index and slots and index >= 0 and index < slots:GetArrayNum()
            and same(slots[index + 1][item_class], pistol:GetClass()))
        local torch_class = StaticFindObject(equipment_path)
        require_gate("loadout.torch_class_available", valid(torch_class))
        local torch_index
        for n = 1, math.min(slots:GetArrayNum(), 8) do
            if same(slots[n][item_class], torch_class) then torch_index = n - 1; break end
        end
        metric("stock_torch_slot", torch_index)
        require_gate("loadout.owned_stock_torch", torch_index ~= nil)

        local item, camera = pawn.OffhandEquipment, pawn.Camera
        require_gate("stock_offhand.item_class", exact(item, equipment_path))
        metric("stock_offhand_item", item:GetFullName())
        require_gate("stock_offhand.item_lifetime", not item:IsActorBeingDestroyed())
        require_gate("stock_offhand.item_replication", item.bReplicates == false)
        require_gate("stock_offhand.item_user", same(item.User, pawn))
        require_gate("stock_offhand.item_world", same(item:GetWorld(), world))
        require_gate("stock_offhand.camera_owner", valid(camera) and same(camera:GetOwner(), pawn))
        require_gate("stock_offhand.camera_attachment", valid(item.Box) and same(item.Box:GetAttachParent(), camera))
        require_gate("capture.equipment_authority", item:HasAuthority())

        local child = item.FlashLight_Source_BP
        local emitter = valid(child) and child.ChildActor
        require_gate("stock_beam.source_class", exact(emitter, source_path))
        require_gate("stock_beam.source_lifetime", not emitter:IsActorBeingDestroyed() and emitter.bReplicates == false)
        require_gate("stock_beam.source_ownership", same(child:GetOwner(), item) and same(emitter:GetParentActor(), item)
            and same(emitter:GetWorld(), world))
        local spot = emitter.SpotLight
        require_gate("stock_beam.spotlight_class", exact(spot, "/Script/Engine.SpotLightComponent"))
        require_gate("stock_beam.spotlight_owner", same(spot:GetOwner(), emitter))
        metric("spot_intensity", spot.Intensity)
        metric("spot_radius", spot.AttenuationRadius)
        metric("spot_inner", spot.InnerConeAngle)
        metric("spot_outer", spot.OuterConeAngle)
        require_gate("stock_beam.shape", spot.Intensity == 17 and spot.AttenuationRadius == 5000
            and spot.InnerConeAngle == 15 and spot.OuterConeAngle == 40)
        local cookie = spot.LightFunctionMaterial
        require_gate("stock_beam.cookie", valid(cookie) and cookie:GetFullName() ==
            "Material /Game/Assets/Materials/Mat_Misc/M_LightCookie.M_LightCookie")
        require_gate("capture.source_authority", emitter:HasAuthority())
        metric("stock_source_powered", emitter.Powered)
        metric("stock_spot_visible", spot:IsVisible())
        require_gate("capture.source_power_visibility", type(emitter.Powered) == "boolean" and emitter.Powered == spot:IsVisible())
        local model = item.SM_Prop_Torch_01
        require_gate("capture.torch_mesh", valid(model) and same(model:GetOwner(), item) and valid(model.StaticMesh)
            and model.StaticMesh:GetFullName() == mesh_name)
        require_gate("capture.player_id", player_id(pawn) ~= nil)
        local api = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
        require_gate("capture.math_library", valid(api))
        local relative = api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), model:K2_GetComponentToWorld())
        local p, r, c = relative.Translation, api:Quat_Rotator(relative.Rotation), emitter.Color
        local actual = {p.X, p.Y, p.Z, r.Pitch, r.Yaw, r.Roll, c.R, c.G, c.B}
        local expected = {0, 21.66062, 0, 0, 90, 0, 1, .970104, .947917}
        local keys = {"x", "y", "z", "pitch", "yaw", "roll", "r", "g", "b"}
        local function bounds(numbers, label)
            for n, key in ipairs(keys) do
                local value = numbers[n]
                metric(label .. "." .. key, value)
                local limit = n <= 3 and 250 or n <= 6 and 180 or 1
                require_gate(label .. ".bounds." .. key,
                    type(value) == "number" and value >= (n <= 6 and -limit or 0) and value <= limit)
            end
        end
        bounds(actual, "mesh_relative")
        for n, key in ipairs(keys) do
            require_gate("mesh_relative.stock_pose." .. key, math.abs(actual[n] - expected[n]) <= .05)
        end
        local aim = api:Quat_Rotator(api:MakeRelativeTransform(spot:K2_GetComponentToWorld(), pawn:GetTransform()).Rotation)
        metric("aim_pitch_raw", aim.Pitch)
        metric("aim_yaw_raw", aim.Yaw)
        metric("aim_roll_raw", aim.Roll)
        actual[4], actual[5], actual[6] = math.floor(aim.Pitch * 10 + .5) / 10,
            math.floor(aim.Yaw * 10 + .5) / 10, math.floor(aim.Roll * 10 + .5) / 10
        bounds(actual, "pawn_aim")
    end)
    if not ok and scope == "none" then metric("exception", tostring(detail):gsub("[%c]", " ")) end
    return table.concat({ok and "qualified" or scope == "none" and "exception" or scope, table.concat(values, "\t")}, "\t")
end

local function offhand_rig(pawn)
    local meshes = pawn:K2_GetComponentsByClass(asset("/Script/Engine.SkeletalMeshComponent"))
    local display
    for _, object in ipairs(meshes) do
        local body = type(object.type) == "function" and object:type() == "RemoteUnrealParam" and object:get() or object
        if valid(body) and same(body:GetOwner(), pawn) and same(body:GetAttachParent(), pawn.Mesh)
            and valid(body.SkinnedAsset) and body.SkinnedAsset:GetFullName() ==
                "SkeletalMesh /Game/Classes/NPCs/NPC_Assets/CharacterModels/Player_NoArmor.Player_NoArmor" then
            if display then return "-" end
            display = body
        end
    end
    if not display then return "-" end
    local math_api = asset("/Script/Engine.Default__KismetMathLibrary")
    local root, samples = pawn:GetTransform(), {}
    for _, name in ipairs({"LeftHand", "RightHand", "LeftShoulder", "RightShoulder", "Weapon_Socket01"}) do
        local bone = FName(name, EFindName.FNAME_Find)
        if display:DoesSocketExist(bone) then
            local relative = math_api:MakeRelativeTransform(display:GetSocketTransform(bone, 0), root)
            samples[#samples + 1] = name .. "\t" .. transform_row(relative, math_api)
        end
    end
    return #samples > 0 and table.concat(samples, "\t") or "-"
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
    local visible_component, avatar_torch, visible_stock, stock_torch
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
                    local torch = parent.SM_Prop_Torch_01
                    if heavy_view and valid(actor.SpotLight) and actor.SpotLight:IsVisible()
                        and valid(torch) and same(torch:GetOwner(), parent) and valid(torch.StaticMesh)
                        and torch.StaticMesh:GetFullName() == mesh_name then
                        visible_stock, stock_torch = actor.SpotLight, torch
                    end
                end
                if actor.Powered then powered = powered + 1 end
                if valid(actor.SpotLight) and actor.SpotLight:IsVisible() then visible = visible + 1 end
            end
        end
    end
    for _, parameter in ipairs(remote:K2_GetComponentsByClass(asset("/Script/Engine.SpotLightComponent"))) do
        local spot = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
            and parameter:get() or parameter
        if valid(spot) and same(spot:GetOwner(), remote) and spot:IsVisible() then
            components, visible_component = components + 1, spot
        end
    end
    for _, parameter in ipairs(remote:K2_GetComponentsByClass(asset("/Script/Engine.StaticMeshComponent"))) do
        local component = type(parameter.type) == "function" and parameter:type() == "RemoteUnrealParam"
            and parameter:get() or parameter
        local mesh = valid(component) and component.StaticMesh
        if valid(mesh) and mesh:GetFullName() == mesh_name and same(component:GetOwner(), remote) then
            display_meshes, avatar_torch = display_meshes + 1, component
        end
    end
    local census = string.format("%s\t%d\t%d\t%d\t%d\t%d\t%d", remote:GetFullName(), sources, powered, visible, components,
        held_sources, display_meshes)
    if offhand then
        local row = census .. "\t" .. offhand_rig(remote)
        if not offhand_view then return row end
        row = row .. "\tbeam"
        if not visible_component then return row .. "\t-" end
        assert(components == 1 and display_meshes == 1 and same(visible_component:GetAttachParent(), avatar_torch)
            and avatar_torch:GetAttachSocketName():ToString() == "LeftHand", "flashlight_probe_offhand_display_mount_unavailable")
        local body = avatar_torch:GetAttachParent()
        local function joint(name) return body:GetSocketLocation(FName(name, EFindName.FNAME_Find)) end
        local wrist, i, r = joint("LeftHand"), joint("LeftHandIndex1"), joint("LeftHandRing1")
        local palm = {X = (2*wrist.X + i.X + r.X)/4, Y = (2*wrist.Y + i.Y + r.Y)/4, Z = (2*wrist.Z + i.Z + r.Z)/4}
        local api = asset("/Script/Engine.Default__KismetMathLibrary")
        local torch_transform = avatar_torch:K2_GetComponentToWorld()
        local grip = api:TransformLocation(torch_transform, {X = 2.1321736, Y = -9.03324555, Z = -6.46371144})
        local error_cm = math.sqrt((grip.X-palm.X)^2 + (grip.Y-palm.Y)^2 + (grip.Z-palm.Z)^2)
        local spot_transform = visible_component:K2_GetComponentToWorld()
        local relative = api:MakeRelativeTransform(spot_transform, remote:GetTransform())
        local lens = api:InverseTransformLocation(torch_transform, spot_transform.Translation)
        return table.concat({row, error_cm, lens.X, lens.Y, lens.Z, transform_row(relative, api)}, "\t")
    end
    local row = census
    if guest_source then
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
        row = table.concat({row, slot_guid(remote), held_class, mode, stock_power}, "\t")
    end
    if not heavy_view then return row end
    local spot, mount = visible_component, avatar_torch
    if not spot and not guest_source then spot, mount = visible_stock, stock_torch end
    if not spot then return row .. "\t-" end
    assert(components + visible == 1 and display_meshes == 1
        and (not visible_component or same(spot:GetAttachParent(), mount)), "flashlight_probe_heavy_observer_mount_unavailable")
    local cookie = spot.LightFunctionMaterial
    local geometry = measure_local_beam(spot, mount, spot:GetLightColor())
    return table.concat({row, mount.StaticMesh:GetFullName(), spot:GetFullName(),
        valid(cookie) and cookie:GetFullName() or "-", spot.InnerConeAngle, spot.OuterConeAngle,
        spot.Intensity, spot.AttenuationRadius, geometry}, "\t")
end

local function run(operation)
    local viewport = FindFirstOf("GameViewportClient")
    local world = valid(viewport) and viewport.World
    local driver = valid(world) and world.NetDriver
    assert(valid(driver) and world:GetFullName() == map, "flashlight_probe_world_unavailable")
    local pawn = asset("/Script/Engine.Default__GameplayStatics"):GetPlayerPawn(world, 0)
    assert(valid(pawn) and pawn:IsLocallyControlled() and same(pawn:GetWorld(), world), "flashlight_probe_local_pawn_unavailable")
    local connected = valid(driver.ServerConnection)
    if connected and guest_offhand and operation == "diagnoseoffhand" then return diagnose_offhand(world, pawn, driver) end
    if connected and not guest_source and not guest_offhand then
        return observe(world, pawn, assert(math.tointeger(operation:match("^observe(%d+)$"))))
    end
    assert(same(pawn.Save, world.OwningGameInstance.SaveSlot_Ref), "flashlight_probe_local_save_unavailable")
    if not connected then
        assert(pawn:HasAuthority() and #driver.ClientConnections == 1 and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1",
            "flashlight_probe_host_scope_unavailable")
    end
    if guest_offhand then
        if not connected then
            if operation == "guestoffhandboundary" then return host_offhand_save_boundary(pawn) end
            if operation == "guestoffhandseed" then return seed_guest_offhand(world, pawn) end
            return observe(world, pawn, assert(math.tointeger(operation:match("^observe(%d+)$"))))
        end
        assert(not pawn:HasAuthority(), "flashlight_probe_guest_offhand_not_autonomous")
        if offhand_trace and same(offhand_pawn, pawn) then
            if operation == "traceoffhandstart" then return start_offhand_trace(pawn, world.OwningGameInstance) end
            if operation == "traceoffhandstop" then return stop_offhand_trace() end
        end
        local identities = operation:match("^selectoffhandgun(%x+)$")
        if identities then
            assert(#identities == 64 and not seeded_slots, "flashlight_probe_guest_offhand_invalid_selection")
            local gun, torch = identities:sub(1, 32), identities:sub(33)
            local rows = pawn.Save.PlayerEquipment
            if not same(rows[2][item_class], asset(pistol_path)) or not same(rows[5][item_class], asset(equipment_path))
                or item_identity(rows[2]) ~= gun or item_identity(rows[5]) ~= torch then error("avatar_probe_rig_pending", 0) end
            assert(pawn.Save.PlayerOffhandingFlashlight == false and not valid(pawn.OffhandEquipment),
                "flashlight_probe_guest_offhand_already_active")
            pawn:Equip_NextItem(1, false)
            primary, offhand_pawn = pawn.EquipedItem, pawn
            assert(valid(primary) and same(primary:GetClass(), asset(pistol_path)) and same(primary.User, pawn),
                "flashlight_probe_guest_offhand_primary_unavailable")
            seeded_slots = {{index = 5, guid = torch}, {index = 2, guid = gun}}
            count_dispatch()
            operation = "readoffhand"
        end
        return offhand_operation(operation, pawn, world)
    end
    if offhand then return offhand_operation(operation, pawn, world) end

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
    end
    if heavy then row = row .. "\t" .. torch_measurement(equipment, actor, spot) end
    return row
end

local last
LoopInGameThreadWithDelay(100, function()
    local file = io.open(mailbox .. "/avatar-probe-command.tsv", "rb")
    if not file then return end
    local text = file:read(160) or ""
    file:close()
    local ticket, operation = text:match("^(%x+)\t([a-z0-9]+)\n$")
    if not ticket or #ticket ~= 32 or ticket == last then return end
    last = ticket
    local ok, value = pcall(run, operation)
    local reply = assert(io.open(mailbox .. "/avatar-probe-reply.tsv", "wb"))
    reply:write(ticket, "\t", ok and "ok\t" or "", value, "\n")
    reply:close()
end)
