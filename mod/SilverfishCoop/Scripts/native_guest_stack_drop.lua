local stack_drop = {}
local guest_save = require("guest_save")
local players = require("players")

local action = "native-guest-stack-drop-probe"
local map = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local stock_actor_prefix = "Ammo_Pistol_BP_C " .. map .. ".Observitory_P:PersistentLevel.Ammo_Pistol_BP_C_"
local settings = "/Script/Engine.Default__GameplayStatics"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local observation

local function valid(value)
    return value and value:IsValid()
end

local function item_guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end

local function slot_name(value)
    return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$")
end

function stack_drop.command(fields, state, phase)
    if fields[2] ~= action then return end
    observation = nil
    local name, slot, guid, raw_count = fields[3], fields[4], fields[5], fields[6]
    local count = math.tointeger(tonumber(raw_count))
    if not enabled or #fields ~= 6 or phase ~= "hosting" or not state.listening or state.server ~= false or state.map ~= map
        or type(name) ~= "string" or name == "" or not slot_name(slot) or not item_guid(guid)
        or not count or count < 1 or count > 200 or tostring(count) ~= raw_count
        or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then
        error("native_guest_stack_drop_fixture_unavailable", 0)
    end
    local checked, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not checked or not valid(pawn) or not valid(save) or not players.is_standing_guest(pawn, name) then
        error("native_guest_stack_drop_exact_guest_unavailable", 0)
    end
    local statics = StaticFindObject(settings)
    local frozen = valid(statics) and statics:LoadGameFromSlot(slot, 0)
    if not valid(frozen) then error("native_guest_stack_drop_frozen_frame_unavailable", 0) end
    local called, ok, status, actor, new_guid, actual_count = pcall(SilverfishProbeNativeGuestStackDrop,
        pawn:GetAddress(), save:GetAddress(), frozen:GetAddress(), guid, count)
    if not called or type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z0-9_]+$") then
        error("mutation_unverified_native_guest_stack_drop_receipt", 0)
    end
    if ok and (status ~= "stock_spawn_verified_and_retired" or actual_count ~= count or not item_guid(new_guid) or new_guid == guid
                or type(actor) ~= "string" or actor:sub(1, #stock_actor_prefix) ~= stock_actor_prefix
                or not actor:sub(#stock_actor_prefix + 1):match("^%d+$")) then
        error("mutation_unverified_native_guest_stack_drop_readback", 0)
    end
    observation = {map = state.map, pawn = name, slot = slot, guid = guid, count = count, ok = ok, status = status,
        actor = ok and actor or "-", new_guid = ok and new_guid or "-"}
    return "isolated stock ammo drop probe reported"
end

function stack_drop.rows(state, phase, status_row)
    if not enabled or phase ~= "hosting" or state.map ~= map or type(SilverfishProbeNativeGuestStackDrop) ~= "function" then return {} end
    local rows = {status_row(action .. "-capability", 1)}
    local row = observation
    if row then
        rows[#rows + 1] = status_row(action, row.map, row.pawn, row.slot, row.guid, row.count, row.ok and 1 or 0, row.status,
            row.actor, row.new_guid)
    end
    return rows
end

function stack_drop.reset()
    observation = nil
end

return stack_drop
