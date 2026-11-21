local maps = {}
for package in pairs(require("zone_maps")) do
    if not package:match("^/Game/Classes/Maps/PlaytestMap_") then
        maps[package] = package:match("[^/]+$")
    end
end
return maps
