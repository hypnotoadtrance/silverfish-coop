local routes = require("zone_maps")
local world_path = require("world_path")

return function(package)
    if routes[package] and not package:match("^/Game/Classes/Maps/PlaytestMap_") then
        return world_path.leaf(package)
    end
end
