local inventory = {}
local statics_path = "/Script/Engine.Default__GameplayStatics"
local observation
local page_size = 16

function inventory.command(fields, state, phase)
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
    observation = nil
end

function inventory.rows(state, phase, status_row)
    local report = observation
    if not report or phase ~= "hosting" or report.map ~= state.map then return {} end
    local rows = {status_row("native-inventory-census", report.slot, report.map, report.total, report.offset, #report.rows)}
    for _, row in ipairs(report.rows) do rows[#rows + 1] = status_row("native-inventory-census-item", table.unpack(row)) end
    return rows
end

return inventory
