local objects = {}

function objects.install()
    local exact = SilverfishFindObjectByPath
    if type(exact) ~= "function" then return end
    local engine_lookup = StaticFindObject
    StaticFindObject = function(name, ...)
        if type(name) == "string" and name:sub(1, 1) == "/" then
            -- A hash miss returns an invalid UObject, like the engine lookup.
            -- No value means the hash tables are unavailable, such as at startup.
            local object = exact(name)
            if object ~= nil then return object end
        end
        return engine_lookup(name, ...)
    end
end

return objects
