local world_path = {}
local stream_world, known_levels, visible_levels, next_stream_check

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

function world_path.owned_actor_level(actor, world)
    if not valid(actor) or not valid(world) then return end
    local level = actor:GetOuter()
    if same(level, world.PersistentLevel) then
        if same(actor:GetWorld(), world) then return level end
    elseif valid(level) and same(level.OwningWorld, world) then
        return level
    end
end

function world_path.actor_paths(world, kind)
    if not valid(world) or type(SilverfishSnapshotNativeWorldActorPaths) ~= "function" then return end
    local name = world:GetFullName():match("^World (.+)$")
    local encoded = name and SilverfishSnapshotNativeWorldActorPaths(world:GetAddress(), name, kind)
    if type(encoded) ~= "string" then return end
    local paths = {}
    for path in encoded:gmatch("[^\n]+") do paths[#paths + 1] = path end
    return paths
end

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

function world_path.visible_actor_level(actor, world, streams)
    if not valid(actor) or not valid(world) or actor:IsActorBeingDestroyed() then return end
    local level = actor:GetOuter()
    if same(level, world.PersistentLevel) then
        if same(actor:GetWorld(), world) then return level end
        return
    end
    if streams then
        if valid(level) and level.bIsVisible == true and same(level, streams[level:GetAddress()]) then return level end
        return
    end
    return world_path.visible_streaming_level(level, world)
end

function world_path.start_stream_tracking()
    if type(RegisterBeginPlayPostHook) ~= "function" then return end
    RegisterBeginPlayPostHook(function(context)
        if not valid(stream_world) then return end
        local actor = context:get()
        local level = valid(actor) and actor:GetOuter()
        local address = valid(level) and level:GetAddress()
        if address and not known_levels[address] and not same(level, stream_world.PersistentLevel) then
            known_levels[address] = level
        end
    end)
end

function world_path.remember_level(world, level)
    if same(stream_world, world) and valid(level) and not same(level, world.PersistentLevel) then
        known_levels[level:GetAddress()] = level
    end
end

-- Return the same table until stream visibility changes so each census can reuse its results.
function world_path.visible_streams(world)
    if not same(stream_world, world) then
        stream_world, known_levels, visible_levels, next_stream_check = world, {}, {}, 0
    end
    local now = os.clock()
    if now < next_stream_check then return visible_levels end
    next_stream_check = now + .25
    local current, changed = {}, false
    for address, level in pairs(known_levels) do
        if not valid(level) then
            known_levels[address] = nil
        elseif world_path.visible_streaming_level(level, world) then
            current[address] = level
            if not same(level, visible_levels[address]) then changed = true end
        end
    end
    for address in pairs(visible_levels) do
        if not current[address] then changed = true end
    end
    if changed then visible_levels = current end
    return visible_levels
end

return world_path
