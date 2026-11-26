local shot_view = {}

local vector_pattern = "^(%-?%d+%.%d+),(%-?%d+%.%d+),(%-?%d+%.%d+)$"

function shot_view.format(x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz, actor, hx, hy, hz)
    if not x then return end
    local view = ("%.6f,%.6f,%.6f"):format(x, y, z)
    if ex then view = view .. (";%.3f,%.3f,%.3f;%.3f,%.3f,%.3f"):format(ex, ey, ez, tx, ty, tz) end
    if mx then view = view .. (";%.3f,%.3f,%.3f"):format(mx, my, mz) end
    if actor then view = view .. ("|%s|%.3f,%.3f,%.3f"):format(actor, hx, hy, hz) end
    return view
end

function shot_view.parse(view)
    if type(view) ~= "string" or #view > 460 then return end
    local geometry, actor, contact = view:match("^([^|]+)|([^|]+)|([^|]+)$")
    if not geometry and view:find("|", 1, true) then return end
    view = geometry or view
    local vectors, values = {}, {}
    for vector in view:gmatch("[^;]+") do
        local x, y, z = vector:match(vector_pattern)
        if not x or #vectors == 4 then return end
        vectors[#vectors + 1] = vector
        values[#values + 1], values[#values + 2], values[#values + 3] = tonumber(x), tonumber(y), tonumber(z)
    end
    if (#vectors ~= 1 and #vectors ~= 3 and #vectors ~= 4) or table.concat(vectors, ";") ~= view then return end
    if actor then
        local x, y, z = contact:match(vector_pattern)
        local number = actor:match("^npc:([1-9]%d*)$")
        local dynamic = number and #number <= 5 and tonumber(number) <= 65535
        local placed = #actor <= 256
            and actor:match("^/[A-Za-z0-9_]+/[A-Za-z0-9_/.]+:PersistentLevel%.[A-Za-z0-9_.]+$")
        if #vectors ~= 4 or not x or not (placed or dynamic) then return end
        values[13], values[14], values[15], values[16] = actor, tonumber(x), tonumber(y), tonumber(z)
    end
    return values
end

return shot_view
