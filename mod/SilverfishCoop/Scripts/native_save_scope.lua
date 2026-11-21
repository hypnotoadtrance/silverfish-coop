local scope = {}

local active
local registered = {}
local statics = "/Script/Engine.GameplayStatics:"
local async = "/Script/Engine.AsyncActionHandleSaveGame:"
local find_files = "/Script/BlueprintFileUtils.BlueprintFileUtilsBPLibrary:FindFiles"

local function text(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function normalized(path)
    return (path:gsub("\\", "/"):gsub("//+", "/"):gsub("/+$", ""):lower())
end

local function route(parameter)
    if not active then return end
    local slot = text(parameter:get())
    if type(slot) ~= "string" or slot:lower() == "config" then return end

    -- A slot is a filename. Refuse paths and Windows filename metacharacters
    -- without passing the original string to the save system.
    if slot == "" or slot == "." or slot == ".." or slot:find('[/\\:%c<>|?*"]') then
        slot = "|InvalidSaveSlot"
    end
    parameter:set(active.prefix .. "/" .. slot)
end

local function route_directory(_, parameter)
    if not active then return end
    local value = text(parameter:get())
    if type(value) == "string" and active.roots[normalized(value)] then
        parameter:set(active.directory .. "/")
    end
end

local hooks = {
    [statics .. "SaveGameToSlot"] = function(_, _, slot) route(slot) end,
    [statics .. "LoadGameFromSlot"] = function(_, slot) route(slot) end,
    [statics .. "DoesSaveGameExist"] = function(_, slot) route(slot) end,
    [statics .. "DeleteGameInSlot"] = function(_, slot) route(slot) end,
    [async .. "AsyncLoadGameFromSlot"] = function(_, _, slot) route(slot) end,
    [async .. "AsyncSaveGameToSlot"] = function(_, _, _, slot) route(slot) end,
    [find_files] = route_directory,
}

local function install()
    for path, callback in pairs(hooks) do
        if not registered[path] then
            local target = StaticFindObject(path)
            if not target or not target:IsValid() then error("Stock co-op save routing is unavailable", 0) end
            RegisterHook(path, callback)
            registered[path] = true
        end
    end
end

function scope.select(id)
    if type(id) ~= "string" or #id ~= 32 or not id:match("^[a-f0-9]+$") or not id:find("[1-9a-f]") then
        error("Expected a co-op save scope ID", 0)
    end
    if active then error("Release the current co-op save scope first", 0) end
    local system = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    local paths = StaticFindObject("/Script/Engine.Default__BlueprintPathsLibrary")
    if not system or not system:IsValid() or not paths or not paths:IsValid() then
        error("The game save directory is unavailable", 0)
    end
    local saved = text(system:GetProjectSavedDirectory())
    if type(saved) ~= "string" or saved == "" or saved:find("%c") then
        error("The game save directory is unavailable", 0)
    end
    local raw_root = saved:gsub("[/\\]+$", "") .. "/SaveGames"
    local root = text(paths:ConvertRelativePathToFull(raw_root, ""))
    if type(root) ~= "string" or not root:match("^%a:[/\\]") or root:find("%c") then
        error("The game save directory is unavailable", 0)
    end
    local prefix = "SilverfishCoop/" .. id
    local directory = root .. "/" .. prefix
    if paths:DirectoryExists(directory) ~= true then
        error("The private co-op save directory is unavailable", 0)
    end
    install()
    active = {id = id, prefix = prefix, directory = directory,
              roots = {[normalized(raw_root)] = true, [normalized(root)] = true}}
end

function scope.release(id)
    if not active or active.id ~= id then error("The co-op save scope did not match", 0) end
    active = nil
end

function scope.selected()
    return active and active.id
end

return scope
