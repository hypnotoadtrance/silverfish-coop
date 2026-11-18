local inventory = {}
local vitals = require("vitals")

local cpp_lab = os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
local playtest_maps = {
    ["/Game/Classes/Maps/PlaytestMap_01"] = true,
    ["/Game/Classes/Maps/PlaytestMap_02"] = true,
}
local last_result

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function guest_in(state)
    for _, details in ipairs(state.pawns) do
        if details.kind == "guest" then return details.pawn end
    end
end

local function active(state, phase)
    return cpp_lab and phase == "hosting" and playtest_maps[state.map] and state.clients == 1
        and state.local_pawn and valid(state.local_pawn.pawn)
end

function inventory.command(fields, state, phase)
    if fields[2] ~= "native-inventory-probe" then return end
    last_result = nil
    if #fields ~= 2 or not active(state, phase) or vitals.active() then
        error("Inventory proof requires an active C++ lab with stock pawn bindings", 0)
    end
    if type(SilverfishCopyNativeGuestSave) ~= "function" or type(SilverfishProbeNativeInventory) ~= "function" then
        error("The compiled native inventory helpers are unavailable", 0)
    end
    local host, guest = state.local_pawn.pawn, guest_in(state)
    if not valid(guest) or same(host, guest) or not host:HasAuthority() or not guest:HasAuthority() then
        error("Two distinct authoritative pawns are required", 0)
    end
    local original = host.Save
    if not same(original, guest.Save) then
        error("The stock host and guest save bindings are unavailable", 0)
    end
    local ok, detail = pcall(function()
        vitals.command({"native-inventory", "vitals-probe"}, state, phase)
        local detached = guest.Save
        if not valid(detached) or same(detached, original) or not same(host.Save, original) then
            error("Could not verify independent receiver ownership", 0)
        end
        local copied, why = SilverfishCopyNativeGuestSave(original:GetAddress(), detached:GetAddress())
        if not copied then error(tostring(why), 0) end
        local verified, result = SilverfishProbeNativeInventory(original:GetAddress(), detached:GetAddress())
        if not verified and result == "no_populated_native_item_with_guid"
            and type(SilverfishProbeNativeInventoryFixture) == "function" then
            verified, result = SilverfishProbeNativeInventoryFixture(original:GetAddress(), detached:GetAddress())
        end
        if not verified then error(tostring(result), 0) end
        return result
    end)
    vitals.reset()
    if not same(host.Save, original) or not same(guest.Save, original) then
        error("The stock save binding could not be verified after the inventory probe", 0)
    end
    if not ok then error(detail, 0) end
    last_result = { guest = guest, detail = detail }
    return "native inventory ownership verified and stock binding restored"
end

function inventory.rows(state, phase, status_row)
    if active(state, phase) and last_result and same(guest_in(state), last_result.guest) then
        return { status_row("native-inventory-canary", "copied", 1, last_result.detail) }
    end
    last_result = nil
    return {}
end

return inventory
