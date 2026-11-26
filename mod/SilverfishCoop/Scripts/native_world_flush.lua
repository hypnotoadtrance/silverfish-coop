local native_world_flush = {}
local routes = require("zone_maps")

local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local enabled = bridge and bridge ~= "" and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local expanded = {}
local expanded_enabled = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"
if expanded_enabled then
    for _, map in ipairs(require("authored_zone_maps")) do expanded[map] = true end
end
local statics_path = "/Script/Engine.Default__GameplayStatics"
local math_path = "/Script/Engine.Default__KismetMathLibrary"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local antfarm_class = "AntfarmController_C"
local antfarm_path = "/Game/Classes/AntFarm/AntfarmController." .. antfarm_class
local level_class = "LevelControler_BP_C"
local level_path = "/Game/Classes/Actors/LevelControler_BP." .. level_class
local farm_generated = "AntfarmGenerated_8_17B022DD4F8D4A68231690B7DAC5A5C2"
local farm_cells = "Cells_24_967FE2B148197B668EE05CAE6F8874FB"
local farm_bases = "Bases_15_82D5ECED418ADB3FC4BBDC9B4278CEA9"
local map_items = "Items_3_42BDD59048DF86C357078E80512340F8"

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(object, class)
    return valid(object) and same(object:GetClass(), class)
        and not object:GetFullName():find("Default__", 1, true)
end

local function canonical_host(state)
    local world, controller = state.world, state.controller
    local host = state.local_pawn
    if not valid(world) or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map
        or not host or not same(host.controller_object, controller)
        or not exact(controller, StaticFindObject(controller_path)) or not controller:HasAuthority()
        or not controller:IsLocalController() or controller:IsActorBeingDestroyed()
        or not same(controller:GetWorld(), world) then return end

    local driver = world.NetDriver
    local pawn = host.pawn
    if not valid(driver) or valid(driver.ServerConnection) or not same(driver.World, world)
        or not exact(pawn, StaticFindObject(pawn_path)) or not pawn:HasAuthority() or pawn:IsActorBeingDestroyed()
        or not same(controller.Pawn, pawn) or not same(pawn.Controller, controller)
        or not same(pawn:GetWorld(), world) then return end

    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, StaticFindObject(instance_path)) or not exact(save, StaticFindObject(save_path))
        or not same(pawn.Save, save) or not same(statics:GetGameInstance(controller), instance) then return end
    return { world = world, driver = driver, controller = controller, pawn = pawn, statics = statics,
        instance = instance, save = save }
end

local function unique_manager(class, path, owner)
    local exact_class = StaticFindObject(path)
    if not valid(exact_class) then return nil, "missing" end

    local found
    for _, actor in ipairs(FindAllOf(class) or {}) do
        if exact(actor, exact_class) and same(actor:GetWorld(), owner.world) and not actor:IsActorBeingDestroyed() then
            if found then return nil, "ambiguous" end
            found = actor
        end
    end
    if not found then return nil, "missing" end
    if not found:HasAuthority() or not same(owner.statics:GetGameInstance(found), owner.instance) then
        return nil, "authority_unavailable"
    end
    return found
end

local function initialized_entries(entries)
    local count = entries and math.tointeger(entries:GetArrayNum())
    if not count or count < 1 then return end
    local members, definitions = {}, {}
    for index = 1, count do
        local item = entries[index]
        if not valid(item) or not valid(item.Data) or definitions[item.Data:GetAddress()] then return end
        members[item:GetAddress()] = true
        definitions[item.Data:GetAddress()] = true
    end
    return count, members
end

local function canonical_save(owner)
    return same(owner.instance.SaveSlot_Ref, owner.save) and same(owner.pawn.Save, owner.save)
        and same(owner.controller.Pawn, owner.pawn) and same(owner.pawn.Controller, owner.controller)
        and same(owner.world.NetDriver, owner.driver) and same(owner.pawn:GetWorld(), owner.world)
end

local function flush_antfarm(owner)
    local actor, problem = unique_manager(antfarm_class, antfarm_path, owner)
    if not actor then return problem end
    if not same(actor.GameInstance, owner.instance) or not same(owner.instance["As Player FP"], owner.pawn) then
        return "uninitialized"
    end

    local cells, members = initialized_entries(actor.CellQueue)
    local bases = initialized_entries(actor.Bases)
    local cell, step, index = actor.LoadedCell, math.tointeger(actor.SimulationStep), math.tointeger(actor.SimulationIndex)
    local math_library = StaticFindObject(math_path)
    local delay = valid(math_library) and math_library:GetTotalSeconds(actor.CycleDelay)
    if not cells or not bases or not step or step < 0 or step > 2
        or (step == 1 and (not index or index < 0 or index >= cells))
        or (step == 2 and (not index or index < 0 or index >= bases))
        or (valid(cell) and (not members[cell:GetAddress()] or cell.Loaded ~= true))
        or type(delay) ~= "number" or not (delay > 0 and delay < math.huge) then
        return "uninitialized"
    end
    if not canonical_save(owner) then return "scope_changed" end

    -- Stock Save_GameState calls this same entry point before writing its slot.
    -- It advances the simulation and records squads before serializing cells and bases.
    actor:Save()
    if not canonical_save(owner) or not same(actor.GameInstance, owner.instance) then return "scope_changed" end
    local stored = owner.save.Antfarm
    if not stored or stored[farm_generated] ~= true or #stored[farm_cells] ~= cells or #stored[farm_bases] ~= bases then
        return "readback_unverified"
    end
    return "flushed", { cells = cells, bases = bases }
end

local function map_explosives(save, level_name)
    local count
    save.PlayerExplosives:ForEach(function(raw_key, raw_value)
        local key = raw_key:get()
        if type(key) == "userdata" then key = key:ToString() end
        if key == level_name then
            local stored = raw_value:get()
            local items = stored and stored[map_items]
            count = items and math.tointeger(items:GetArrayNum())
            return true
        end
    end)
    if count and count >= 0 then return count end
end

local function flush_explosives(owner, map)
    local actor, problem = unique_manager(level_class, level_path, owner)
    if not actor then return problem end
    if not same(actor["As Game Instance FP"], owner.instance) or not same(actor["As Player FP"], owner.pawn)
        or owner.controller.Loading ~= false then return "uninitialized" end
    if not canonical_save(owner) then return "scope_changed" end

    -- Stock EndPlay saves this map's PlayerExplosive-tagged item actors.
    -- Capture the same entry while the outgoing world is still present.
    actor:SavePlayerExplosives()
    if not canonical_save(owner) or not same(actor["As Game Instance FP"], owner.instance) then
        return "scope_changed"
    end
    local level = map:match("([^/]+)$")
    local count = map_explosives(owner.save, level)
    if count == nil then return "readback_unverified" end
    return "flushed", { level = level, items = count }
end

local function apply(result, key, capture, ...)
    local ok, state, details = pcall(capture, ...)
    result[key] = ok and state or "failed"
    if ok and details then result[key .. "_details"] = details end
end

function native_world_flush.flush(state, phase)
    local result = { state = "skipped", antfarm = "skipped", explosives = "skipped" }
    local route = type(state) == "table" and routes[state.map]
    if not enabled or phase ~= "hosting" or not route or not state.listening or state.server ~= false
        or not (expanded[state.map] or route.entrance or expanded_enabled and route.expanded) then
        result.reason = "authored_listen_host_required"
        return result
    end
    result.map = state.map
    local inspected, owner = pcall(canonical_host, state)
    if not inspected or not owner then
        result.reason = "canonical_host_unavailable"
        return result
    end
    if owner.controller.Loading ~= false then
        result.reason = "stock_world_loading"
        return result
    end
    apply(result, "antfarm", flush_antfarm, owner)
    apply(result, "explosives", flush_explosives, owner, state.map)
    if result.antfarm == "flushed" and result.explosives == "flushed" then
        result.state = "flushed"
    elseif result.antfarm == "flushed" or result.explosives == "flushed" then
        result.state = "partial"
    else
        result.state = "unavailable"
    end
    return result
end

return native_world_flush
