local stack_drop = {}
local guest_save = require("guest_save")
local host_save = require("native_host_save")
local players = require("players")
local maps = require("native_guest_drop_maps")
local drop_items = require("native_guest_drop_items")

local action = "native-guest-stack-drop-probe"
local birth = "native-guest-stack-drop-birth"
local settle = "native-guest-stack-drop-settle"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local settings = "/Script/Engine.Default__GameplayStatics"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local observation, settlement

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
    local leaf = maps[state.map]
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

local function transfer(fields, state, phase)
    local operation, name, id, source, replacement = table.unpack(fields, 3, 7)
    local debit = operation == "debit"
    local x, y, z = tonumber(fields[8]), tonumber(fields[9]), tonumber(fields[10])
    local apply = SilverfishRecoverNativeInventoryBag
    if debit then apply = SilverfishApplyPreparedNativeInventory end
    if #fields ~= 11 or fields[11] ~= state.map or not hosting_map(state, phase)
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
    local frozen = valid(statics) and statics:LoadGameFromSlot(source, 0)
    local prepared = debit and valid(frozen) and statics:LoadGameFromSlot(replacement, 0)
    if not valid(frozen) or (debit and not valid(prepared)) or (not debit and frozen.PlayerBackpack:GetArrayNum() ~= 1) then
        error("native_guest_stack_drop_settlement_slot_unavailable", 0)
    end
    local called, ok, status, count
    if debit then
        called, ok, status = pcall(apply, frozen:GetAddress(), prepared:GetAddress(), save:GetAddress(), pawn:GetAddress())
        count = ok == true and 1 or 0
    else
        called, ok, status, count = pcall(apply, frozen:GetAddress(), save:GetAddress(), pawn:GetAddress())
        count = count or 0
    end
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z0-9_]+$")
        or count ~= (ok and 1 or 0) then error("mutation_unverified_native_guest_stack_drop_settlement_call", 0) end
    settlement = {map = state.map, operation = operation, pawn = name, id = id, source = source,
        replacement = replacement, ok = ok, status = status, count = count}
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
    local expected_map = command == action and observatory or fields[7]
    local expected_count = command == action and 6 or 7
    if #fields ~= expected_count or not hosting_map(state, phase) or expected_map ~= state.map
        or type(name) ~= "string" or name == "" or not slot_name(slot) or not item_guid(guid)
        or not count or count < 1 or count > 200 or tostring(count) ~= raw_count
        or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then
        error("native_guest_stack_drop_fixture_unavailable", 0)
    end
    local qualify = command == action and qualified_owner or qualified_participant
    local pawn, save = qualify(state, phase, name)
    if not pawn then error("native_guest_stack_drop_exact_dropper_unavailable", 0) end
    local statics = StaticFindObject(settings)
    local frozen = valid(statics) and statics:LoadGameFromSlot(slot, 0)
    if not valid(frozen) then error("native_guest_stack_drop_frozen_frame_unavailable", 0) end
    local called, ok, status, actor, new_guid, actual_count, x, y, z = pcall(SilverfishProbeNativeGuestStackDrop,
        pawn:GetAddress(), save:GetAddress(), frozen:GetAddress(), guid, count)
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z0-9_]+$") then
        error("mutation_unverified_native_guest_stack_drop_receipt", 0)
    end
    if ok then
        local qualified
        for class, profile in pairs(drop_items.profiles) do
            if count <= profile.maximum and drop_items.actor_path(class, state.map, maps[state.map], actor) then
                qualified = true
                break
            end
        end
        if not qualified or status ~= "stock_spawn_verified_and_retired" or actual_count ~= count
            or not item_guid(new_guid) or new_guid == guid or not coordinate(x) or not coordinate(y) or not coordinate(z) then
            error("mutation_unverified_native_guest_stack_drop_readback", 0)
        end
    end
    observation = {command = command, map = state.map, pawn = name, slot = slot, guid = guid, count = count, ok = ok, status = status,
        actor = ok and actor or "-", new_guid = ok and new_guid or "-", location = ok and {x, y, z}}
    return "isolated stock inventory drop probe reported"
end

function stack_drop.rows(state, phase, status_row)
    if not hosting_map(state, phase) or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then return {} end
    local rows = {status_row(birth .. "-capability", 1)}
    if fixtures and state.map == observatory then rows[#rows + 1] = status_row(action .. "-capability", 1) end
    local row = observation
    if row then
        rows[#rows + 1] = status_row(row.command, row.map, row.pawn, row.slot, row.guid, row.count, row.ok and 1 or 0, row.status,
            row.actor, row.new_guid, table.unpack(row.location or {}))
    end
    if settlement then
        local proof = settlement
        rows[#rows + 1] = status_row(settle, proof.map, proof.operation, proof.pawn, proof.id, proof.source,
            proof.replacement, proof.ok and 1 or 0, proof.status, proof.count)
    end
    return rows
end

function stack_drop.reset()
    observation, settlement = nil, nil
end

return stack_drop
