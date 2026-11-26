local grant = {}
local scope = require("native_save_scope")
local guest_save = require("guest_save")

function grant.pending(state, phase)
    if phase ~= "connected" or type(SilverfishArmInventoryGrants) ~= "function" then return end
    local save, pawn = scope.bind_guest(state, phase)
    if save then return SilverfishArmInventoryGrants(pawn:GetAddress(), save:GetAddress()) end
end

function grant.rows(state, phase, status_row)
    local sequence = grant.pending(state, phase)
    if sequence then return {status_row("native-inventory-grant-ready", sequence)} end
    return {}
end

function grant.command(fields, state, phase)
    local action = fields[2]
    if action == "native-inventory-grant-read" or action == "native-inventory-grant-ack" then
        local sequence = math.tointeger(tonumber(fields[3]))
        local expected = action == "native-inventory-grant-read" and 3 or 4
        if #fields ~= expected or not sequence or grant.pending(state, phase) ~= sequence then
            error("native_inventory_grant_scope_changed", 0)
        end
        if action == "native-inventory-grant-read" then
            local guid, item_class, text = SilverfishReadInventoryGrant(sequence)
            if not text then error("native_inventory_grant_scope_changed", 0) end
            return table.concat({guid, item_class, text}, "@")
        end
        if fields[4] ~= "1" and fields[4] ~= "0" then error("Invalid inventory grant acknowledgement", 0) end
        local ok, detail = SilverfishAcknowledgeInventoryGrant(sequence, fields[4] == "1")
        if not ok then error(detail, 0) end
        return detail
    end
    if action ~= "native-inventory-grant-apply" then return end
    if #fields ~= 6 or phase ~= "hosting" or not state.listening or state.server ~= false
        or type(SilverfishImportGuestInventoryAddition) ~= "function" then
        error("native_inventory_grant_scope_changed", 0)
    end
    local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
    if not pawn or not save then error("native_inventory_grant_scope_changed", 0) end
    local item_class = fields[5]
    if not item_class:match("^/Game/[%w_./%-]+_C$") or item_class:find("..", 1, true) then
        error("native_inventory_grant_refused_class", 0)
    end
    local asset = StaticFindObject(item_class)
    if not asset or not asset:IsValid() then LoadAsset(item_class) end
    local ok, detail = SilverfishImportGuestInventoryAddition(pawn:GetAddress(), save:GetAddress(), fields[4], item_class, fields[6])
    if not ok then error(detail, 0) end
    return detail
end

return grant
