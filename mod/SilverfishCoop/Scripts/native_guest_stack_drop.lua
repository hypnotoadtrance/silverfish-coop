local stack_drop = {}
local content_paths = require("content_paths")
local guest_save = require("guest_save")
local host_save = require("native_host_save")
local players = require("players")
local drop_map_leaf = require("native_guest_drop_maps")
local drop_items = require("native_guest_drop_items")
local ground_stack = require("native_ground_stack")
local escrow = require("native_loot_actions")

local action = "native-guest-stack-drop-probe"
local birth = "native-guest-stack-drop-birth"
local settle = "native-guest-stack-drop-settle"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local settings = "/Script/Engine.Default__GameplayStatics"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local observation, settlement
local item_ammo = "Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"

local function valid(value)
    return value and value:IsValid()
end

local function item_guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end

local function slot_name(value)
    return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$")
end

local function coordinate(value)
    return type(value) == "number" and value == value and math.abs(value) <= 1e7
end

local function hosting_map(state, phase)
    local leaf = drop_map_leaf(state.map)
    return enabled and phase == "hosting" and state.listening == true and state.server == false and leaf
        and valid(state.world) and state.world:GetFullName() == "World " .. state.map .. "." .. leaf
end

local function qualified_owner(state, phase, name)
    if not hosting_map(state, phase) then return end
    local checked, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if checked and valid(pawn) and valid(save) and players.is_standing_guest(pawn, name) then return pawn, save end
end

local function qualified_participant(state, phase, name)
    if state.local_pawn and name == state.local_pawn.name then
        local instance, pawn = host_save.canonical_host(state, phase)
        if valid(pawn) and pawn.Dead == false and players.is_downed(pawn) == false then
            return pawn, instance.SaveSlot_Ref
        end
        return
    end
    return qualified_owner(state, phase, name)
end

local function memory_available()
    return type(SilverfishReferenceNativeDetachedSave) == "function"
        and type(SilverfishReadNativeInventoryCensus) == "function"
        and type(SilverfishPrepareNativeInventoryWipe) == "function"
        and type(SilverfishApplyPreparedNativeInventory) == "function"
        and type(SilverfishRecoverNativeInventoryBag) == "function"
        and type(SilverfishVerifyNativeStashSave) == "function"
end

local function exact_row(save, guid, quantity, class, single)
    local ok, status, rows, total = SilverfishReadNativeInventoryCensus(save:GetAddress())
    if not ok or status ~= "complete" or type(rows) ~= "string"
        or math.type(total) ~= "integer" or total < 1 or total > 8194 or (single and total ~= 1) then
        return
    end
    local kept, found, equipment_slot, seen = {}, nil, nil, 0
    for line in rows:gmatch("[^\n]+") do
        local id, source, position, asset, count, important, droppable =
            line:match("^([0-9A-F]+)\t([a-z]+)\t(%d+)\t([^\t]+)\t(%d+)\t[01]\t([01])\t[01]\t([01])$")
        if not item_guid(id) then return end
        seen = seen + 1
        if id == guid then
            local slot = source == "helmet" and 8 or source == "armor" and 9
                or source == "equipment" and math.tointeger(tonumber(position)) or nil
            local debit_method = slot and (slot < 8 and SilverfishDebitNativeGuestQuickDrop or SilverfishDebitNativeGuestWornDrop)
            local sealed_package = asset == "/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C"
            if found or (source ~= "backpack" and (single or not slot or slot > 9 or quantity ~= 1
                    or type(debit_method) ~= "function"))
                or tonumber(count) ~= quantity or (important ~= "0" and not sealed_package)
                or droppable ~= "1" or (class and class ~= asset) then return end
            found, equipment_slot = asset, slot
        else
            kept[#kept + 1] = id
        end
    end
    if found and seen == total then return found, table.concat(kept, ","), total - 1, equipment_slot end
end

local function clone_save(save)
    local transient = StaticFindObject("/Engine/Transient")
    if valid(transient) then
        return StaticConstructObject(save:GetClass(), transient, 0, EObjectFlags.RF_Transient, 0, false, false, save)
    end
end

local function debit_pending(state, phase, pawn, save, frozen, slot, guid, count, class, kept, kept_count, equipment_slot, ammo)
    local instance = host_save.canonical_host(state, phase)
    if not valid(instance) then error("native_guest_stack_drop_exact_host_unavailable", 0) end
    local projected = clone_save(save)
    if not valid(projected) then error("native_guest_stack_drop_frozen_frame_unavailable", 0) end
    local pending = escrow.create_pending_escrow(instance.SaveSlot_Ref, instance, state.world, slot)
    local called, ok, outcome, kept_total, dropped_total, discarded_total = pcall(SilverfishPrepareNativeInventoryWipe,
        frozen:GetAddress(), projected:GetAddress(), pending:GetAddress(), kept, guid, "")
    if not called or ok ~= true or outcome ~= "prepared" or kept_total ~= kept_count
        or dropped_total ~= 1 or discarded_total ~= 0 or not exact_row(pending, guid, count, class, true) then
        escrow.release_escrow(slot)
        error("native_guest_stack_drop_pending_payload_unavailable", 0)
    end
    if equipment_slot then
        local quick = equipment_slot < 8
        local debit = quick and SilverfishDebitNativeGuestQuickDrop or SilverfishDebitNativeGuestWornDrop
        local arguments = {pawn:GetAddress(), save:GetAddress(), guid, equipment_slot}
        if quick then
            if math.type(ammo) ~= "integer" or ammo < -1 or ammo > 2147483647 then
                escrow.release_escrow(slot)
                error("native_guest_stack_drop_live_equipment_unqualified", 0)
            end
            -- The stock held actor may be ahead of its saved magazine count.
            pending.PlayerBackpack[1][item_ammo] = ammo
            if pending.PlayerBackpack[1][item_ammo] ~= ammo then
                escrow.release_escrow(slot)
                error("native_guest_stack_drop_pending_payload_unavailable", 0)
            end
            arguments[5] = ammo
        end
        local applied, status, actual_slot, actual_class, actual_ammo = debit(table.unpack(arguments))
        if applied == true and status == "debited" and actual_slot == equipment_slot and actual_class == class
            and (not quick or actual_ammo == ammo) then return end
        if applied == false and type(status) == "string"
            and (status:match("^native_guest_armor_") or status:match("^native_inventory_unequip_refused_")) then
            escrow.release_escrow(slot)
            error("native_guest_stack_drop_equipment_debit_refused:" .. status, 0)
        end
        error("mutation_unverified_native_guest_stack_drop_equipment_debit", 0)
    end
    local applied, status = SilverfishApplyPreparedNativeInventory(
        frozen:GetAddress(), projected:GetAddress(), save:GetAddress(), pawn:GetAddress())
    if applied ~= true or status ~= "applied" then
        if applied == false and type(status) == "string" and status:match("^native_inventory_wipe_") then
            escrow.release_escrow(slot)
            error("native_guest_stack_drop_pending_debit_refused", 0)
        end
        error("mutation_unverified_native_guest_stack_drop_pending_debit", 0)
    end
end

local function transfer(fields, state, phase)
    local operation, name, id, source, replacement = table.unpack(fields, 3, 7)
    local debit = operation == "debit"
    local reclaim_guid = fields[12]
    local pending_source = fields[15] == "pending"
    local memory = #fields == 15 and (pending_source or fields[15] == "memory") and not debit and item_guid(reclaim_guid)
    local quantity, class = memory and math.tointeger(tonumber(fields[13])), fields[14]
    local x, y, z = tonumber(fields[8]), tonumber(fields[9]), tonumber(fields[10])
    local apply = SilverfishRecoverNativeInventoryBag
    if debit then apply = SilverfishApplyPreparedNativeInventory end
    if (not memory and #fields ~= 11 and (#fields ~= 12 or debit or not item_guid(reclaim_guid)))
        or (memory and (not memory_available() or not quantity or quantity < 1 or quantity > 200
            or not content_paths.item_class(class)))
        or fields[11] ~= state.map or not hosting_map(state, phase)
        or (not debit and operation ~= "reclaim") or type(id) ~= "string" or #id ~= 32
        or not id:match("^[0-9a-f]+$") or not slot_name(source) or source == replacement
        or (debit and not slot_name(replacement)) or (not debit and replacement ~= "-")
        or not coordinate(x) or not coordinate(y) or not coordinate(z) or type(apply) ~= "function" then
        error("native_guest_stack_drop_settlement_unavailable", 0)
    end
    local pawn, save = qualified_participant(state, phase, name)
    if not pawn then error("native_guest_stack_drop_exact_recipient_unavailable", 0) end
    local origin = pawn:K2_GetActorLocation()
    if not origin or not coordinate(origin.X) or not coordinate(origin.Y) or not coordinate(origin.Z)
        or (origin.X - x)^2 + (origin.Y - y)^2 + (origin.Z - z)^2 > 250^2 then
        error("native_guest_stack_drop_out_of_range", 0)
    end
    local statics = StaticFindObject(settings)
    local pending = memory and escrow.pending_escrow(state.world, source)
    local frozen = pending or not pending_source and valid(statics) and statics:LoadGameFromSlot(source, 0)
    local prepared = debit and valid(frozen) and statics:LoadGameFromSlot(replacement, 0)
    if not valid(frozen) or (debit and not valid(prepared)) or (not debit and frozen.PlayerBackpack:GetArrayNum() ~= 1) then
        error("native_guest_stack_drop_settlement_slot_unavailable", 0)
    end
    if memory and not exact_row(frozen, reclaim_guid, quantity, class, true) then
        error("native_guest_stack_drop_settlement_payload_unavailable", 0)
    end
    local called, ok, status, count, merged
    if debit then
        called, ok, status = pcall(apply, frozen:GetAddress(), prepared:GetAddress(), save:GetAddress(), pawn:GetAddress())
        count = ok == true and 1 or 0
    else
        status = "append"
        if reclaim_guid and type(SilverfishVerifyNativeStashSave) == "function" then
            local instance = host_save.canonical_host(state, phase)
            local canonical = valid(instance) and instance.SaveSlot_Ref
            status, merged = ground_stack.merge(pawn, save, canonical, frozen, reclaim_guid)
        end
        if status == "append" then
            called, ok, status, count = pcall(apply, frozen:GetAddress(), save:GetAddress(), pawn:GetAddress())
            count = count or 0
        else
            called, ok, count = true, merged ~= nil, merged and 1 or 0
        end
    end
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z0-9_]+$")
        or count ~= (ok and 1 or 0) then error("mutation_unverified_native_guest_stack_drop_settlement_call", 0) end
    if pending and ok then escrow.release_escrow(source) end
    settlement = {map = state.map, operation = operation, pawn = name, id = id, source = source,
        replacement = replacement, ok = ok, status = status, count = count, stack_merge = merged}
    return "isolated stock stack settlement reported"
end

function stack_drop.command(fields, state, phase)
    local command = fields[2]
    if command == settle then return transfer(fields, state, phase) end
    if command ~= action and command ~= birth then return end
    if command == action and not fixtures then error("native_guest_stack_drop_fixture_unavailable", 0) end
    observation = nil
    local name, slot, guid, raw_count = fields[3], fields[4], fields[5], fields[6]
    local count = math.tointeger(tonumber(raw_count))
    local memory = command == birth and fields[8] == "memory"
    local expected_map = command == action and observatory or fields[7]
    local expected_count = command == action and 6 or 7
    if #fields ~= expected_count + (memory and 1 or 0) or (memory and not memory_available())
        or not hosting_map(state, phase) or expected_map ~= state.map
        or type(name) ~= "string" or name == "" or not slot_name(slot) or not item_guid(guid)
        or not count or count < 1 or count > 200 or tostring(count) ~= raw_count
        or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then
        error("native_guest_stack_drop_fixture_unavailable", 0)
    end
    local qualify = command == action and qualified_owner or qualified_participant
    local pawn, save = qualify(state, phase, name)
    if not pawn then error("native_guest_stack_drop_exact_dropper_unavailable", 0) end
    local statics = StaticFindObject(settings)
    local frozen
    if memory then frozen = clone_save(save)
    elseif valid(statics) then frozen = statics:LoadGameFromSlot(slot, 0) end
    if not valid(frozen) then error("native_guest_stack_drop_frozen_frame_unavailable", 0) end
    local expected_class, kept, kept_count, equipment_slot
    if memory then
        expected_class, kept, kept_count, equipment_slot = exact_row(frozen, guid, count, nil, false)
        if not expected_class then error("native_guest_stack_drop_stock_item_row_unqualified", 0) end
    end
    local arguments = {pawn:GetAddress(), save:GetAddress(), frozen:GetAddress(), guid, count, equipment_slot}
    local called, ok, status, actor, new_guid, actual_count, x, y, z, class, ammo = pcall(SilverfishProbeNativeGuestStackDrop,
        table.unpack(arguments))
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z0-9_]+$") then
        error("mutation_unverified_native_guest_stack_drop_receipt", 0)
    end
    if ok then
        local qualified = class and drop_items.actor_path(class, state.map, drop_map_leaf(state.map), actor)
        if not class then
            for known_class, profile in pairs(drop_items.profiles) do
                if count <= profile.maximum and drop_items.actor_path(known_class, state.map, drop_map_leaf(state.map), actor) then
                    qualified = true
                    break
                end
            end
        end
        if not qualified or status ~= "stock_spawn_verified_and_retired" or actual_count ~= count
            or not item_guid(new_guid) or new_guid == guid or not coordinate(x) or not coordinate(y) or not coordinate(z) then
            error("mutation_unverified_native_guest_stack_drop_readback", 0)
        end
        if memory then
            if class ~= expected_class then error("native_guest_stack_drop_stock_item_row_unqualified", 0) end
            debit_pending(state, phase, pawn, save, frozen, slot, guid, count, class, kept, kept_count, equipment_slot, ammo)
        end
    end
    observation = {command = command, map = state.map, pawn = name, slot = slot, guid = guid, count = count, ok = ok, status = status,
        actor = ok and actor or "-", new_guid = ok and new_guid or "-", location = ok and {x, y, z},
        class = ok and class or nil}
    return "isolated stock inventory drop probe reported"
end

local function birth_row(proof, row)
    local details = proof.location and {table.unpack(proof.location)} or {}
    if proof.class then details[#details + 1] = proof.class end
    return row(proof.command, proof.map, proof.pawn, proof.slot, proof.guid, proof.count, proof.ok and 1 or 0,
        proof.status, proof.actor, proof.new_guid, table.unpack(details))
end

local function settlement_row(proof, row)
    local merge = proof.stack_merge
    local evidence = merge and {merge.source, merge.destination, merge.amount, merge.before, merge.after} or {}
    return row(settle, proof.map, proof.operation, proof.pawn, proof.id, proof.source,
        proof.replacement, proof.ok and 1 or 0, proof.status, proof.count, table.unpack(evidence))
end

function stack_drop.compact(fields)
    return fields[2] == birth and fields[8] == "memory"
        or fields[2] == settle and (fields[15] == "memory" or fields[15] == "pending")
end

function stack_drop.reply(fields, state, phase, row)
    local old_observation, old_settlement = observation, settlement
    observation, settlement = nil, nil
    local ok, message = pcall(stack_drop.command, fields, state, phase)
    local proof, format = observation, birth_row
    if fields[2] == settle then proof, format = settlement, settlement_row end
    observation, settlement = old_observation, old_settlement
    local header = row("native-guest-stack-drop-reply", 1, fields[1], fields[2], phase, state.map,
        ok and proof and "accepted" or "error", message or "") .. "\n"
    return ok and proof and header .. format(proof, row) .. "\n" or header
end

function stack_drop.rows(state, phase, status_row)
    if not hosting_map(state, phase) or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then return {} end
    local rows = {status_row(birth .. "-capability", 1)}
    if memory_available() then rows[#rows + 1] = status_row("native-guest-stack-drop-memory", 1) end
    if type(SilverfishNativeGenericInventoryDrop) == "function" and SilverfishNativeGenericInventoryDrop() then
        rows[#rows + 1] = status_row("native-generic-inventory-drop", 1)
    end
    if fixtures and state.map == observatory then rows[#rows + 1] = status_row(action .. "-capability", 1) end
    if observation then rows[#rows + 1] = birth_row(observation, status_row) end
    if settlement then rows[#rows + 1] = settlement_row(settlement, status_row) end
    return rows
end

function stack_drop.reset()
    observation, settlement = nil, nil
end

return stack_drop
