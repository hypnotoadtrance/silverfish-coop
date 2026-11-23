local wipe = {}
local guest_save = require("guest_save")
local guest_weapon = require("native_guest_weapon")
local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local max_partition_bytes = 8194 * 33 + 3
local retention_field = "Items_KeptOnDeath_14_9602AB054254974168B43BB0C2924307"
local extraction_field = "ExtractionMode_45_FC0B60E8470417127E7367B262395F41"
local shrink_field = "LoseSlot_PerDeath_41_60DB8A134132D666D7FD9E9714F51A77"
local observation

local function slot_name(value)
    return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$")
end

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function owner(state, phase, name)
    local info, controller = state.local_pawn, state.controller
    local statics = StaticFindObject(statics_path)
    if not valid(statics) or not info or not valid(info.pawn) or not valid(controller)
        or not same(controller:GetClass(), StaticFindObject(controller_path))
        or not controller:HasAuthority() or not controller:IsLocalController()
        or not same(info.pawn, controller.Pawn) or not same(info.pawn:GetWorld(), state.world) then return end
    local instance = statics:GetGameInstance(info.pawn)
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not valid(stock) or not same(info.pawn.Save, stock) then return end
    if name == info.name then return info.pawn, stock, statics end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(save) or same(save, stock) or not same(save, pawn.Save) then return end
    return pawn, save, statics
end

local function read_partition(slot)
    local file = io.open(bridge .. "/wipe-" .. slot .. ".tsv", "rb")
    if not file then error("The exact stock wipe partition is missing", 0) end
    local content = file:read(max_partition_bytes + 1)
    file:close()
    if not content or #content > max_partition_bytes then error("The stock wipe partition is too long", 0) end
    local kept, dropped, discarded = content:match("^([0-9A-F,]*)\t([0-9A-F,]*)\t([0-9A-F,]*)\r?\n$")
    if not kept then error("The stock wipe partition is invalid", 0) end
    return kept, dropped, discarded
end

function wipe.command(fields, state, phase)
    local action = fields[2]
    if action == "native-wipe-update-retention" then
        local previous, next_slots = math.tointeger(tonumber(fields[4])), math.tointeger(tonumber(fields[5]))
        local host = state.local_pawn
        if #fields ~= 5 or phase ~= "hosting" or not state.listening or not host or fields[3] ~= host.name
            or not previous or not next_slots or previous < 1 or previous > 10 or next_slots ~= previous - 1 then
            error("The stock wipe needs an exact host retention change", 0)
        end
        local pawn, save = owner(state, phase, host.name)
        local rules = valid(pawn) and save.GameRules
        if not rules or rules[extraction_field] ~= true or rules[shrink_field] ~= true
            or (rules[retention_field] ~= previous and rules[retention_field] ~= next_slots) then
            error("The host's stock retention rule changed during the wipe", 0)
        end
        rules[retention_field] = next_slots
        if save.GameRules[retention_field] ~= next_slots then error("Stock retention update did not verify", 0) end
        return "stock wipe retention committed"
    end
    if action ~= "native-wipe-prepare-inventory" and action ~= "native-wipe-apply-inventory"
        and action ~= "native-wipe-recover-bag" then return end
    observation = nil
    if phase ~= "hosting" or not state.listening then error("The stock wipe requires the authoritative host", 0) end
    if action ~= "native-wipe-prepare-inventory" then
        local name, source, projected = fields[3], fields[4], fields[5]
        local apply = action == "native-wipe-apply-inventory"
        local native = apply and SilverfishApplyPreparedNativeInventory or SilverfishRecoverNativeInventoryBag
        if #fields ~= (apply and 5 or 4) or not slot_name(source) or (apply and not slot_name(projected))
            or source == projected or type(native) ~= "function" then error("The exact stock wipe action is unavailable", 0) end
        local pawn, save, statics = owner(state, phase, name)
        local original = valid(pawn) and statics:LoadGameFromSlot(source, 0)
        local candidate = apply and valid(original) and statics:LoadGameFromSlot(projected, 0)
        if not valid(original) or (apply and not valid(candidate)) then error("The exact stock wipe generation is unavailable", 0) end
        local ok, status, count
        if apply then
            ok, status = native(original:GetAddress(), candidate:GetAddress(), save:GetAddress(), pawn:GetAddress())
            -- A same-map respawn reuses the pawn after its stock inventory changes.
            if ok then guest_weapon.clear_empty_slot_hold(pawn, save) end
        else
            ok, status, count = native(original:GetAddress(), save:GetAddress(), pawn:GetAddress())
        end
        observation = {map = state.map, action = apply and "apply" or "recover", pawn = name, source = source,
            replacement = projected or "-", ok = ok, status = status, count = count or 0}
        return "stock inventory wipe action reported"
    end
    local source, replacement, escrow = fields[3], fields[4], fields[5]
    if #fields ~= 5 or not slot_name(source)
        or not slot_name(replacement) or not slot_name(escrow) or source == replacement or source == escrow
        or replacement == escrow or type(SilverfishPrepareNativeInventoryWipe) ~= "function"
        or type(SilverfishVerifyPreparedNativeInventory) ~= "function" then
        error("The exact stock wipe inventory scope is unavailable", 0)
    end
    local statics, class = StaticFindObject(statics_path), StaticFindObject(save_path)
    if not valid(statics) or not valid(class) or statics:DoesSaveGameExist(replacement, 0)
        or statics:DoesSaveGameExist(escrow, 0) then error("The stock wipe generation slots are unavailable", 0) end
    local kept, dropped, discarded = read_partition(replacement)
    local frozen = statics:LoadGameFromSlot(source, 0)
    local projected = statics:LoadGameFromSlot(source, 0)
    local bag = statics:CreateSaveGameObject(class)
    if not valid(frozen) or not valid(projected) or not valid(bag) then
        error("The stock wipe generations could not be isolated", 0)
    end
    local ok, reason, kept_count, dropped_count, discarded_count = SilverfishPrepareNativeInventoryWipe(
        frozen:GetAddress(), projected:GetAddress(), bag:GetAddress(), kept, dropped, discarded)
    if not ok then error(reason, 0) end
    local function write_and_verify(value, slot)
        if statics:SaveGameToSlot(value, slot, 0) ~= true then error("The stock wipe generation could not be written", 0) end
        local readback = statics:LoadGameFromSlot(slot, 0)
        if not valid(readback) or not SilverfishVerifyPreparedNativeInventory(value:GetAddress(), readback:GetAddress()) then
            error("The typed stock wipe generation did not survive serialization", 0)
        end
    end
    write_and_verify(projected, replacement)
    if dropped_count > 0 then write_and_verify(bag, escrow) end
    observation = {map = state.map, source = source, replacement = replacement,
        escrow = dropped_count > 0 and escrow or "-", kept = kept_count, dropped = dropped_count, discarded = discarded_count}
    return "isolated stock wipe inventory prepared"
end

function wipe.rows(state, phase, status_row)
    local row = observation
    if not row or phase ~= "hosting" or row.map ~= state.map then return {} end
    if row.action then
        return { status_row("native-wipe-inventory-action", row.action, row.map, row.pawn, row.source,
            row.replacement, row.ok and 1 or 0, row.status, row.count) }
    end
    return { status_row("native-wipe-inventory", row.map, row.source, row.replacement, row.escrow,
        row.kept, row.dropped, row.discarded) }
end

function wipe.reset()
    observation = nil
end

return wipe
