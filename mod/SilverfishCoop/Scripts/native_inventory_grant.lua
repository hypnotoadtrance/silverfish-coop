local grant = {}
local content_paths = require("content_paths")
local scope = require("native_save_scope")
local guest_save = require("guest_save")

function grant.pickup_row(pawn, save, guid, status_row)
    if type(SilverfishExportDetachedInventoryItem) ~= "function" or type(guid) ~= "string"
        or #guid ~= 32 or not guid:match("^[0-9A-F]+$") then return end
    local called, exported, text = pcall(SilverfishExportDetachedInventoryItem,
        pawn:GetAddress(), save:GetAddress(), guid)
    if called and exported and type(text) == "string" and #text <= 8192 and not text:find("[%z\t\r\n]") then
        return status_row("native-pickup-inventory-row", guid, text)
    end
end

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

function grant.project_pickup(state, phase, epoch, guid, item_class, text, previous)
    if type(SilverfishImportLocalInventoryPickup) ~= "function" or not content_paths.item_class(item_class, 512)
        or grant.pending(state, phase) then return end
    local save, pawn = scope.bind_guest(state, phase)
    local view = require("native_inventory_view")
    if not save or not view.accept_layout_projection(pawn) then return end
    local before = previous ~= "-" and math.tointeger(tonumber(previous)) or nil
    if previous ~= "-" and not before then return end
    local args = before and {before} or {}
    local ok, outcome = SilverfishImportLocalInventoryPickup(pawn:GetAddress(), save:GetAddress(),
        guid, item_class, text, table.unpack(args))
    if not ok then return end
    state.world.OwningGameInstance:Calc_CarryWeight()
    view.refresh_projected(state, phase, epoch)
    return outcome
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
    if (#fields ~= 6 and #fields ~= 7) or phase ~= "hosting" or not state.listening or state.server ~= false
        or type(SilverfishImportGuestInventoryAddition) ~= "function" then
        error("native_inventory_grant_scope_changed", 0)
    end
    local pawn, save = guest_save.exact_item_context(state, phase, fields[3])
    if not pawn or not save then error("native_inventory_grant_scope_changed", 0) end
    local item_class = fields[5]
    if not content_paths.item_class(item_class, 512) then
        error("native_inventory_grant_refused_class", 0)
    end
    local asset = StaticFindObject(item_class)
    if not asset or not asset:IsValid() then LoadAsset(item_class) end
    local ok, detail
    if #fields == 7 then
        local units = math.tointeger(tonumber(fields[6]:match("^stack:([1-9]%d*)$")))
        if not units or units > 2147483647 or #fields[7] ~= 32 or not fields[7]:match("^%x+$")
            or type(SilverfishIncreaseGuestInventoryStack) ~= "function" then
            error("native_inventory_grant_refused_encoding", 0)
        end
        ok, detail = SilverfishIncreaseGuestInventoryStack(pawn:GetAddress(), save:GetAddress(), fields[4],
            item_class, units, fields[7]:upper())
    else
        ok, detail = SilverfishImportGuestInventoryAddition(pawn:GetAddress(), save:GetAddress(), fields[4], item_class, fields[6])
    end
    if not ok then error(detail, 0) end
    return detail
end

return grant
