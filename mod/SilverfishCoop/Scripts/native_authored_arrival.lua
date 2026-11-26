local arrival = {}
local bed = require("native_bed")

local spawn_class = "LevelExit_Spawn_BP_C"
local spawn_type = "/Game/Classes/ScriptingActors/LevelExit_Spawn_BP.LevelExit_Spawn_BP_C"
local door_class = "Dungeon_TransitionDoor_BP_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local main_menu = "/Game/Classes/Maps/MainMenu_P"
local max_placement_error = 200

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function stock(object, class)
    if not valid(object) then return false end
    local name = object:GetFullName()
    return name:match("^" .. class .. " ") and not name:find("Default__", 1, true)
end

local function canonical_instance(controller)
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(controller)
    if not stock(instance, "GameInstance_FP_C") or not stock(instance.SaveSlot_Ref, "SaveGame_BP_C") then
        error("The canonical native host GameInstance and save are unavailable", 0)
    end
    return instance
end

local function owning_controller(controller, world)
    return stock(controller, "PlayerControler_FP_C") and controller:HasAuthority()
        and controller:IsLocalController() and not controller:IsActorBeingDestroyed()
        and same(controller:GetWorld(), world)
end

local function host_context(state, phase, destination)
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    local world = state.world
    if phase ~= "hosting" or not state.listening or state.server or not valid(world)
        or world:GetFullName() ~= "World " .. destination
        or not stock(pawn, "Player_FP_C") or not pawn:HasAuthority() or pawn:IsActorBeingDestroyed()
        or not owning_controller(controller, world) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), world) then
        error("The authoritative host is not in the persistent authored destination", 0)
    end

    local instance = canonical_instance(controller)
    if not same(pawn.Save, instance.SaveSlot_Ref) then
        error("The authored arrival requires the canonical native host save", 0)
    end
    return pawn, controller, world, instance
end

function arrival.prepare_time(state, phase, step)
    if type(step) ~= "number" or not math.tointeger(step) or step < 0 or step > 2147483647 then
        error("Expected a nonnegative signed 32-bit stock travel time step", 0)
    end
    local map = state.map
    local basename = type(map) == "string" and map:match("^/Game/Classes/Maps/[%w_/]+$")
        and map:match("/([%w_]+)$")
    if not basename then error("Expected a stock travel source map", 0) end
    local destination = map .. "." .. basename
    local instance
    if phase == "ready" and step == 0 and map == main_menu and not state.listening and not state.server
        and valid(state.world) and state.world:GetFullName() == "World " .. destination
        and owning_controller(state.controller, state.world) then
        instance = canonical_instance(state.controller)
    else
        local _, _, _, host_instance = host_context(state, phase, destination)
        instance = host_instance
    end
    local previous = instance.Travel_TimeStep
    if type(previous) ~= "number" then error("The canonical stock travel time step is unavailable", 0) end
    instance.Travel_TimeStep = step
    if instance.Travel_TimeStep ~= step then
        instance.Travel_TimeStep = previous
        error("The canonical stock travel time step could not be read back", 0)
    end
    return step
end

local function same_world_actor(actor, world)
    return valid(actor) and same(actor:GetWorld(), world) and not actor:IsActorBeingDestroyed()
end

local function marker_level(statics, marker, world)
    local level = marker:GetOuter()
    if same(level, world.PersistentLevel) then return "arrow" end
    if not valid(level) or level.bIsVisible ~= true then return end
    local subworld = level:GetOuter()
    local package = valid(subworld) and subworld:GetOuter()
    local name = valid(package) and package:GetFullName():match("^Package (/.+)$")
    local streaming = name and statics:GetStreamingLevel(world, FName(name))
    if valid(streaming) and streaming:IsLevelVisible() and same(streaming:GetLoadedLevel(), level) then
        return "streamed-arrow"
    end
end

local function marker_for_tag(world, tag, kind)
    local match, placement
    local statics, class = StaticFindObject(statics_path), StaticFindObject(spawn_type)
    if not valid(statics) or not valid(class) then error("The stock entrance marker class is unavailable", 0) end
    local found = {}
    statics:GetAllActorsOfClass(world, class, found)
    for _, candidate in ipairs(found.OutActors or found) do
        local marker = candidate
        if type(marker.type) == "function" and marker:type() == "RemoteUnrealParam" then marker = marker:get() end
        if valid(marker) and not marker:IsActorBeingDestroyed() and marker:IsA(class) then
            local name = marker.EntranceTag
            if type(name) ~= "string" and name then name = name:ToString() end
            if name == tag and name ~= "None" then
                local position
                if kind == "door" then
                    position = same_world_actor(marker, world) and "arrow"
                else
                    position = marker_level(statics, marker, world)
                end
                if position then
                    if match then return nil, "duplicate-entrance-tag" end
                    match, placement = marker, position
                end
            end
        end
    end
    if not match or not match:HasAuthority() then return nil, "entrance-unavailable" end
    return match, placement
end

local function door_for_marker(world, expected, marker, tag)
    local match
    for _, door in ipairs(FindAllOf(door_class) or {}) do
        if same_world_actor(door, world) and door:GetFullName() == expected then
            if match then error("The authored arrival door is ambiguous", 0) end
            match = door
        end
    end
    local component = match and match.LevelExit_Spawn_BP
    local parent_tag = match and match["Entrance Tag"]
    if type(parent_tag) ~= "string" and parent_tag then parent_tag = parent_tag:ToString() end
    if not match or not match:HasAuthority() or parent_tag ~= tag or not valid(component)
        or not same(component:GetOwner(), match) or not same(component.ChildActor, marker)
        or not same(marker:GetParentComponent(), component) then
        error("The exact authored door does not own this stock arrival marker", 0)
    end
    return match
end

local function keep_stock_spawn(map, pawn, reason)
    local location = pawn:K2_GetActorLocation()
    local axes = {location.X, location.Y, location.Z}
    for index = 1, 3 do
        local value = axes[index]
        if type(value) ~= "number" or value ~= value or math.abs(value) > 100000000 then
            error("The stock spawn has no valid host position", 0)
        end
    end
    return {destination = map, actor = pawn:GetFullName(), placement = "stock-spawn:" .. reason,
        x = location.X, y = location.Y, z = location.Z}
end

function arrival.place(state, phase, tag, kind, actor_name)
    local map = state.map
    local basename = type(map) == "string" and map:match("^/Game/Classes/Maps/[%w_/]+$")
        and map:match("/([%w_]+)$")
    local class = kind == "overlap" and spawn_class or kind == "door" and door_class
    if not basename or type(tag) ~= "string" or #tag > 64 or not tag:match("^[A-Za-z0-9_%-]+$")
        or not class or type(actor_name) ~= "string"
        or (kind == "door" and not actor_name:match("^" .. door_class .. "_%d+$"))
        or (kind == "overlap" and actor_name ~= "-" and not actor_name:match("^" .. spawn_class .. "_%d+$")) then
        error("Expected a stock entrance tag and a qualified arrival kind", 0)
    end

    local destination = map .. "." .. basename
    local pawn, controller, world = host_context(state, phase, destination)
    local expected = class .. " " .. destination .. ":PersistentLevel." .. actor_name
    local marker, placement = marker_for_tag(world, tag, kind)
    if not marker then
        if kind == "overlap" then return keep_stock_spawn(map, pawn, placement) end
        error("The door arrival marker is unavailable or ambiguous", 0)
    end
    local actor = marker
    if kind == "door" then
        actor = door_for_marker(world, expected, marker, tag)
    elseif valid(marker:GetParentComponent()) then
        return keep_stock_spawn(map, pawn, "entrance-not-standalone")
    end
    local arrow = marker.Arrow
    if not valid(arrow) or not same(arrow:GetClass(), StaticFindObject("/Script/Engine.ArrowComponent"))
        or not same(arrow:GetOwner(), marker) then
        if kind == "overlap" then return keep_stock_spawn(map, pawn, "arrival-arrow-unavailable") end
        error("The exact authored entrance has no stock Arrow component", 0)
    end

    local target, rotation = arrow:K2_GetComponentLocation(), arrow:K2_GetComponentRotation()
    local location = bed.place_host(pawn, controller, world, target, rotation, "arrow")
    local x, y, z = location.X - target.X, location.Y - target.Y, location.Z - target.Z
    if x * x + y * y + z * z > max_placement_error * max_placement_error then
        error("The stock arrival could not be placed within two meters of its Arrow", 0)
    end
    return { destination = map, actor = actor:GetFullName(), placement = placement,
        x = location.X, y = location.Y, z = location.Z }
end

return arrival
