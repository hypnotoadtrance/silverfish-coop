local armor = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local jacket = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
local hazop = "/Game/Classes/Armor_Items/Armor_HazOp.Armor_HazOp_C"
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local projected, observation = {}, nil

function armor.is_body_path(path)
    return path == jacket or path == hazop
end

local function guid_text(parts)
    if not parts then return end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(parts[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return end
        words[#words + 1] = word & 0xffffffff
    end
    local text = string.format("%08X%08X%08X%08X", table.unpack(words))
    return text:find("[^0]") and text or nil
end

local function native_report(map, pawn, guid, action, ok, status, before, after, carry_before, carry_after, minimum, slash, acid,
                             profile, replaced_profile, replaced_guid)
    return {map = map, pawn = pawn, guid = guid, action = action, ok = ok == true, status = status,
        before = before, after = after, carry_before = carry_before, carry_after = carry_after,
        minimum = minimum, slash = slash, acid = acid, profile = profile, replaced_profile = replaced_profile, replaced_guid = replaced_guid}
end

function armor.command(fields, state, phase)
    local action = fields[2]
    if action ~= "native-guest-armor-equip" and action ~= "native-guest-armor-fixture" then return end
    observation = nil
    local seed = action == "native-guest-armor-fixture"
    local pawn_name, guid = fields[3], fields[4]
    if not enabled or phase ~= "hosting" or #fields ~= 4 or not pawn_name
        or not guid or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]")
        or (seed and not fixtures) then error("native_guest_armor_exact_context_required", 0) end
    local invoke
    if seed then invoke = SilverfishSeedNativeGuestArmor else invoke = SilverfishEquipNativeGuestArmor end
    if type(invoke) ~= "function" then error("native_guest_armor_unavailable", 0) end
    local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, pawn_name)
    if not qualified or not pawn or not save then error("native_guest_armor_exact_owner_required", 0) end
    if seed then
        local class = StaticFindObject(jacket)
        if not class or not class:IsValid() then LoadAsset(jacket) end
    end
    observation = native_report(state.map, pawn_name, guid, seed and "seed" or "equip",
        invoke(pawn:GetAddress(), save:GetAddress(), guid))
    return "native guest armor operation reported"
end

local function exact_worn(state, phase, name)
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not pawn or not save then return end
    local row = save.PlayerArmor
    local class = row and row[class_field]
    if not class or not class:IsValid() or not armor.is_body_path(class:GetFullName():match("^%S+ (%S+)$")) then return end
    return pawn:GetAddress(), save:GetAddress(), guid_text(row[guid_field])
end

function armor.sync(state, phase)
    local current = {}
    if enabled and phase == "hosting" and type(SilverfishProjectNativeGuestArmor) == "function" then
        for _, detail in ipairs(state.guests) do
            local name = detail.name
            local ok, pawn, save, guid = pcall(exact_worn, state, phase, name)
            if ok and pawn and save and guid then
                local prior = projected[name]
                if prior and prior.map == state.map and prior.pawn_address == pawn and prior.save_address == save
                    and prior.guid == guid and (prior.ok or os.time() < prior.retry) then
                    current[name] = prior
                else
                    local called, result = pcall(function()
                        return native_report(state.map, name, guid, "project", SilverfishProjectNativeGuestArmor(pawn, save, guid))
                    end)
                    if called then
                        result.pawn_address, result.save_address, result.retry = pawn, save, os.time() + 1
                        current[name] = result
                    end
                end
            end
        end
    end
    projected = current
end

local function row(tag, report, status_row)
    local fields = {tag, report.map, report.pawn, report.guid, report.action, report.ok and 1 or 0,
        report.status, report.before, report.after, report.carry_before, report.carry_after, report.minimum, report.slash, report.acid}
    if report.profile then
        fields[15], fields[16], fields[17] = report.profile, report.replaced_profile, report.replaced_guid
    end
    return status_row(table.unpack(fields))
end

function armor.rows(state, phase, status_row)
    if not enabled or phase ~= "hosting" then return {} end
    local rows = {}
    if observation and observation.map == state.map then
        rows[#rows + 1] = row("native-guest-armor", observation, status_row)
    end
    for _, effect in pairs(projected) do
        if effect.map == state.map then rows[#rows + 1] = row("native-guest-armor-projection", effect, status_row) end
    end
    return rows
end

return armor
