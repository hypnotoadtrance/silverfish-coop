local menu_drop = {}
local host_save = require("native_host_save")
local drop_map_leaf = require("native_guest_drop_maps")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local binding, pending
local entries = { ctrl_click = true, context_menu = true, drag_to_floor = true, split_drag_to_floor = true,
    equipped_ctrl_click = true, equipped_context_menu = true, equipped_drag_to_floor = true }

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function token(value, characters)
    return type(value) == "string" and #value == 32 and value:match(characters) ~= nil
end

local function standing_host(state, phase)
    if not enabled or not drop_map_leaf(state.map) or phase ~= "hosting" or not state.listening or state.server
        or type(SilverfishSetNativeHostDropScope) ~= "function"
        or type(SilverfishClearNativeHostDropScope) ~= "function"
        or type(SilverfishTakeNativeHostDropRequest) ~= "function"
        or type(SilverfishFinishNativeHostDropRequest) ~= "function" then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if valid(pawn) and pawn.Dead == false and not players.is_downed(pawn) then return pawn end
end

local function context(state, phase)
    if not standing_host(state, phase) then return end
    local _, pawn = host_save.canonical_host(state, phase)
    return pawn
end

function menu_drop.clear()
    if binding then SilverfishClearNativeHostDropScope() end
    binding, pending = nil, nil
end

function menu_drop.sync(state, phase)
    state.status_host_drop = nil
    if not binding then return end
    -- The native refresh checks the active controller, driver, and canonical save.
    local pawn = standing_host(state, phase)
    if not same(pawn, binding.pawn) or not same(state.world, binding.world) or state.map ~= binding.map
        or SilverfishSetNativeHostDropScope(pawn:GetAddress(), binding.epoch) ~= true then
        menu_drop.clear()
        return
    end
    state.status_host_drop = binding
    if not pending then
        local sequence, guid, count, entry = SilverfishTakeNativeHostDropRequest(pawn:GetAddress(), binding.epoch)
        if math.type(sequence) == "integer" and sequence > 0 and sequence < 2 ^ 53
            and token(guid, "^[0-9A-F]+$") and guid:find("[^0]")
            and math.type(count) == "integer" and count >= 1 and count <= 200
            and entries[entry] then
            pending = {sequence = sequence, guid = guid, count = count, entry = entry}
        end
    end
end

function menu_drop.command(fields, state, phase)
    local verb, epoch = fields[2], fields[3]
    if verb ~= "native-host-stack-drop-bind" and verb ~= "native-host-stack-drop-finish" then return end
    local pawn = context(state, phase)
    if not valid(pawn) or not token(epoch, "^[0-9a-f]+$") then error("host_stock_drop_scope_unavailable", 0) end
    if verb == "native-host-stack-drop-bind" then
        if #fields ~= 5 or fields[4] ~= pawn:GetFullName() or fields[5] ~= state.map then
            error("host_stock_drop_scope_unavailable", 0)
        end
        if binding and (binding.epoch ~= epoch or not same(binding.pawn, pawn) or not same(binding.world, state.world)) then
            menu_drop.clear()
        end
        if SilverfishSetNativeHostDropScope(pawn:GetAddress(), epoch) ~= true then error("host_stock_drop_scope_unavailable", 0) end
        binding = {pawn = pawn, world = state.world, map = state.map, epoch = epoch}
        return "stock host drop controls bound"
    end
    if #fields ~= 4 or not binding or not pending or epoch ~= binding.epoch or not same(pawn, binding.pawn)
        or tostring(pending.sequence) ~= fields[4] then error("host_stock_drop_intent_stale", 0) end
    local ok, status = SilverfishFinishNativeHostDropRequest(pawn:GetAddress(), epoch, pending.sequence)
    pending = nil
    if type(ok) ~= "boolean" or type(status) ~= "string" or not status:match("^[a-z_]+$") or #status > 60 then
        error("host_stock_drop_finish_unverified", 0)
    end
    return status
end

function menu_drop.rows(state, phase, row, command)
    -- The native refresh already checked the same seat for this observation.
    -- Command replies must requalify because a command can replace the save.
    local refreshed = not command and phase == "hosting" and binding and state.status_host_drop == binding
    local pawn = refreshed and binding.pawn or context(state, phase)
    if not valid(pawn) then return {} end
    local rows = {row("native-host-stack-drop-menu-capability", 1)}
    if binding and same(pawn, binding.pawn) and binding.map == state.map and same(state.world, binding.world) then
        local name = refreshed and state.local_pawn.name or pawn:GetFullName()
        rows[#rows + 1] = row("native-host-stack-drop-scope", state.map, name, binding.epoch)
        if pending then
            rows[#rows + 1] = row("native-host-stack-drop-intent", state.map, name, binding.epoch,
                pending.sequence, pending.guid, pending.count, pending.entry)
        end
    end
    return rows
end

return menu_drop
