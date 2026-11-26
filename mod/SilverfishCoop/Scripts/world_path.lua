local world_path = {}

function world_path.leaf(package)
    if type(package) ~= "string" or package == "/Game/Classes/Maps/MainMenu_P" or package:find("//", 1, true) then return end
    local leaf = package:match("^/[A-Za-z0-9_/]+/([A-Za-z0-9_]+)$")
    if leaf and #package + #leaf + 1 <= 180 then return leaf end
end

function world_path.level_actor_package(path)
    if type(path) ~= "string" or #path > 384 then return end
    local package, export = path:match("^(/[A-Za-z0-9_/]+)%.([A-Za-z0-9_]+):PersistentLevel%.[A-Za-z0-9_%-]+$")
    if package and world_path.leaf(package) then return package, export end
end

function world_path.actor_package(path)
    local package, export = world_path.level_actor_package(path)
    if package and world_path.leaf(package) == export then return package end
end

local function valid(object) return object and object:IsValid() end
local function same(left, right) return valid(left) and valid(right) and left:GetAddress() == right:GetAddress() end

function world_path.visible_streaming_level(level, world)
    if not valid(level) or level.bIsVisible ~= true then return end
    local subworld = level:GetOuter()
    local package = valid(subworld) and subworld:GetOuter()
    local name = valid(package) and package:GetFullName():match("^Package (/.+)$")
    local statics = name and StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local streaming = valid(statics) and statics:GetStreamingLevel(world, FName(name))
    if valid(streaming) and streaming:IsLevelVisible() and same(streaming:GetLoadedLevel(), level) then
        return level
    end
end

function world_path.visible_actor_level(actor, world)
    if not valid(actor) or not valid(world) or actor:IsActorBeingDestroyed() then return end
    local level = actor:GetOuter()
    if same(level, world.PersistentLevel) then
        if same(actor:GetWorld(), world) then return level end
        return
    end
    return world_path.visible_streaming_level(level, world)
end

return world_path
