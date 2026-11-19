local inventory = {}
local guest_save = require("guest_save")
local statics_path = "/Script/Engine.Default__GameplayStatics"
local observation, split, sorted
local page_size = 16

local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end

local function split_stack(fields, state, phase)
    local name, source, created = fields[3], fields[4], fields[5]
    local take, expected = math.tointeger(tonumber(fields[6])), math.tointeger(tonumber(fields[7]))
    if #fields ~= 7 or phase ~= "hosting" or not state.listening or state.server ~= false
        or not guid(source) or not guid(created) or source == created or not take or not expected
        or take < 1 or expected <= take or expected > 2147483647
        or type(SilverfishSplitNativeGuestAmmoStack) ~= "function" then
        error("native_inventory_split_refused_scope", 0)
    end
    local exact, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not exact or not pawn or not save then error("native_inventory_split_refused_owner", 0) end
    local ok, status, before, remaining, taken, class = SilverfishSplitNativeGuestAmmoStack(
        pawn:GetAddress(), save:GetAddress(), source, created, take, expected)
    if ok ~= true then error(tostring(status), 0) end
    if status ~= "split" or before ~= expected or remaining ~= before - take or taken ~= take
        or type(class) ~= "string" or not class:match("^/Game/[%w_./%-]+$") then
        error("mutation_unverified_native_inventory_split_receipt", 0)
    end
    split = {map = state.map, pawn = name, source = source, created = created, class = class,
        before = before, remaining = remaining, taken = taken}
    return "native guest ammo stack split verified"
end

local function sort_backpack(fields, state, phase)
    sorted = nil
    if #fields ~= 3 or phase ~= "hosting" or not state.listening or state.server ~= false
        or type(SilverfishSortNativeGuestBackpack) ~= "function" then
        error("native_inventory_sort_refused_scope", 0)
    end
    local name = fields[3]
    local exact, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not exact or not pawn or not save then error("native_inventory_sort_refused_owner", 0) end
    local ok, status, count = SilverfishSortNativeGuestBackpack(pawn:GetAddress(), save:GetAddress())
    if ok ~= true then error(tostring(status), 0) end
    if (status ~= "sorted" and status ~= "already_sorted") or math.type(count) ~= "integer"
        or count < 0 or count > 4096 then
        error("mutation_unverified_native_inventory_sort_receipt", 0)
    end
    sorted = { map = state.map, pawn = name, status = status, count = count }
    return "native guest backpack sort verified"
end

function inventory.command(fields, state, phase)
    if fields[2] == "native-inventory-sort" then return sort_backpack(fields, state, phase) end
    if fields[2] == "native-inventory-split" then return split_stack(fields, state, phase) end
    if fields[2] ~= "native-inventory-census" then return end
    observation = nil
    local slot, offset = fields[3], math.tointeger(tonumber(fields[4]))
    if #fields ~= 4 or phase ~= "hosting" or not slot or #slot ~= 39 or not slot:match("^SFCoop_[0-9a-f]+$")
        or not offset or offset < 0 or offset > 8194 or type(SilverfishReadNativeInventoryCensus) ~= "function" then
        error("The stock inventory census requires a qualified isolated generation", 0)
    end
    local statics = StaticFindObject(statics_path)
    local snapshot = statics and statics:IsValid() and statics:LoadGameFromSlot(slot, 0)
    if not snapshot or not snapshot:IsValid() then error("The stock inventory census generation is unavailable", 0) end
    local ok, status, source, total = SilverfishReadNativeInventoryCensus(snapshot:GetAddress())
    if not ok or status ~= "complete" or type(source) ~= "string" or type(total) ~= "number"
        or total < 0 or total > 8194 then
        error("The stock inventory census could not read the exact generation (" .. tostring(status) .. ")", 0)
    end
    local rows, seen = {}, 0
    for line in source:gmatch("[^\n]+") do
        seen = seen + 1
        if seen > offset and seen <= offset + page_size then
            local fields = {}
            for value in line:gmatch("[^\t]+") do fields[#fields + 1] = value end
            if #fields ~= 8 then error("The stock inventory census row is incomplete", 0) end
            rows[#rows + 1] = fields
        end
    end
    if seen ~= total then error("The stock inventory census row count did not verify", 0) end
    observation = { slot = slot, total = total, offset = offset, rows = rows, map = state.map }
    return "isolated stock inventory census verified"
end

function inventory.reset()
    observation, split, sorted = nil, nil, nil
end

function inventory.rows(state, phase, status_row)
    if phase ~= "hosting" then return {} end
    local rows = {}
    if type(SilverfishSplitNativeGuestAmmoStack) == "function" then
        rows[#rows + 1] = status_row("native-inventory-split-capability", 1)
    end
    if split and split.map == state.map then
        rows[#rows + 1] = status_row("native-inventory-split", split.map, split.pawn, split.source, split.created,
            split.class, split.before, split.remaining, split.taken)
    end
    if type(SilverfishSortNativeGuestBackpack) == "function" then
        rows[#rows + 1] = status_row("native-inventory-sort-capability", 1)
    end
    if sorted and sorted.map == state.map then
        rows[#rows + 1] = status_row("native-inventory-sort", sorted.map, sorted.pawn, sorted.status, sorted.count)
    end
    local report = observation
    if not report or report.map ~= state.map then return rows end
    rows[#rows + 1] = status_row("native-inventory-census", report.slot, report.map, report.total, report.offset, #report.rows)
    for _, row in ipairs(report.rows) do rows[#rows + 1] = status_row("native-inventory-census-item", table.unpack(row)) end
    return rows
end

return inventory
