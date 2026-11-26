local content_paths = {}

function content_paths.quest_id(name)
    return type(name) == "string" and #name <= 96 and name:lower() ~= "none"
        and name:match("^[A-Za-z0-9_][A-Za-z0-9_.%-]*$") ~= nil
end

function content_paths.item_class(path, limit)
    if type(path) ~= "string" or #path > (limit or 256) or path:find("//", 1, true) then return end
    local package, actor = path:match("^/([A-Za-z0-9_%-]+/[A-Za-z0-9_/%-]+)%.([A-Za-z0-9_%-]+_C)$")
    local asset = package and package:match("([A-Za-z0-9_%-]+)$")
    if asset and actor == asset .. "_C" then return actor end
end

return content_paths
