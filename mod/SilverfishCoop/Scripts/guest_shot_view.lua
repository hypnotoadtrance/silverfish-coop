local shot_view = {}

function shot_view.format(x, y, z, ex, ey, ez, tx, ty, tz, mx, my, mz)
    if not x then return end
    local view = ("%.6f,%.6f,%.6f"):format(x, y, z)
    if ex then view = view .. (";%.3f,%.3f,%.3f;%.3f,%.3f,%.3f"):format(ex, ey, ez, tx, ty, tz) end
    if mx then view = view .. (";%.3f,%.3f,%.3f"):format(mx, my, mz) end
    return view
end

function shot_view.parse(view)
    if type(view) ~= "string" or #view > 160 then return end
    local vectors, values = {}, {}
    for vector in view:gmatch("[^;]+") do
        local x, y, z = vector:match("^(%-?%d+%.%d+),(%-?%d+%.%d+),(%-?%d+%.%d+)$")
        if not x or #vectors == 4 then return end
        vectors[#vectors + 1] = vector
        values[#values + 1], values[#values + 2], values[#values + 3] = tonumber(x), tonumber(y), tonumber(z)
    end
    if (#vectors == 1 or #vectors == 3 or #vectors == 4) and table.concat(vectors, ";") == view then return values end
end

return shot_view
