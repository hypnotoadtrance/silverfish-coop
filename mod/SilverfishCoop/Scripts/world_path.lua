local world_path = {}

function world_path.leaf(package)
    if type(package) ~= "string" or package == "/Game/Classes/Maps/MainMenu_P" or package:find("//", 1, true) then return end
    local leaf = package:match("^/[A-Za-z0-9_/]+/([A-Za-z0-9_]+)$")
    if leaf and #package + #leaf + 1 <= 180 then return leaf end
end

function world_path.actor_package(path)
    if type(path) ~= "string" or #path > 384 then return end
    local package, level = path:match("^(/[A-Za-z0-9_/]+)%.([A-Za-z0-9_]+):PersistentLevel%.[A-Za-z0-9_%-]+$")
    if package and world_path.leaf(package) == level then return package end
end

return world_path
