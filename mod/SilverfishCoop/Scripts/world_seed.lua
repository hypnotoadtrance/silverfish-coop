local world_seed = {}

local routes = require("zone_maps")
local main_menu = "/Game/Classes/Maps/MainMenu_P"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local day_cycle_class = "DayCycle_SunControler_BP_C"
local receipt

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_int32(value)
    return type(value) == "number" and math.tointeger(value) ~= nil
        and value >= -2147483648 and value <= 2147483647
end

function world_seed.static_placement_seed(save)
    if not valid(save) or not save.Flags then return end
    local key = FName("Static_WorldSeed", EFindName.FNAME_Find)
    local flags = save.Flags
    if key:GetComparisonIndex() == 0 or not flags:Contains(key) then return "-" end
    local value = flags:Find(key):get()
    if is_int32(value) then return math.tointeger(value) end
end

function world_seed.set_static_placement_seed(save, value)
    local prior = world_seed.static_placement_seed(save)
    if prior == nil or (value ~= "-" and not is_int32(value)) then return false end
    if prior == value then return true end
    local flags, key = save.Flags, FName("Static_WorldSeed")
    local function write(seed)
        if seed == "-" then
            flags:Remove(key)
        elseif flags:Contains(key) then
            flags:Find(key):Set(seed)
        else
            flags:Add(key, seed)
        end
    end
    local applied, verified = pcall(function()
        write(value)
        return world_seed.static_placement_seed(save) == value
    end)
    if applied and verified then return true end
    local restored, unchanged = pcall(function()
        if world_seed.static_placement_seed(save) ~= prior then write(prior) end
        return world_seed.static_placement_seed(save) == prior
    end)
    if not restored or not unchanged then error("mutation_unverified_guest_static_world_seed", 0) end
    return false
end

local function stock_object(object, class)
    if not valid(object) then return false end
    local name = object:GetFullName()
    return name:match("^" .. class .. " ") ~= nil and not name:find("Default__", 1, true)
end

local function host_save(state, phase)
    local controller, world = state.controller, state.world
    if not valid(world) or not stock_object(controller, "PlayerControler_FP_C")
        or not controller:IsLocalController() or not controller:HasAuthority()
        or not same(controller:GetWorld(), world)
        or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map or state.server then
        error("The local authoritative seed owner is unavailable", 0)
    end

    if not ((phase == "ready" and state.map == main_menu and not state.listening)
        or (phase == "hosting" and state.listening and routes[state.map])) then
        error("World seed preparation requires the staged menu or listen host", 0)
    end

    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(controller)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not stock_object(instance, "GameInstance_FP_C") or not stock_object(save, "SaveGame_BP_C")
        or not is_int32(save.InstanceSeed) then
        error("The canonical stock SaveSlot_Ref seed is unavailable", 0)
    end

    if phase == "hosting" then
        local pawn = state.local_pawn and state.local_pawn.pawn
        if not stock_object(pawn, "Player_FP_C") or not pawn:HasAuthority() or not same(controller.Pawn, pawn)
            or not same(pawn:GetWorld(), world) or not same(pawn.Save, save) then
            error("The authoritative pawn is not using the canonical stock seed", 0)
        end
    end
    return instance, save
end

local function prepare(state, destination, instance, save, seed)
    receipt = { source = state.map, destination = destination, expected = seed,
        instance = instance:GetAddress(), save = save:GetAddress() }
end

function world_seed.prepare_seed(state, phase, destination, seed_text)
    if seed_text == nil then
        receipt = nil
        return
    end
    if phase ~= "ready" or not routes[destination] or type(seed_text) ~= "string" or #seed_text > 11
        or not seed_text:match("^%-?%d+$") then
        error("Expected a signed 32-bit campaign seed before first host travel", 0)
    end
    local seed = tonumber(seed_text)
    if not is_int32(seed) then error("Campaign seed is out of range", 0) end
    local instance, save = host_save(state, phase)
    local previous = save.InstanceSeed
    save.InstanceSeed = seed
    if save.InstanceSeed ~= seed then
        save.InstanceSeed = previous
        error("The stock world seed could not be restored", 0)
    end
    prepare(state, destination, instance, save, seed)
end

function world_seed.prepare_travel_seed(state, phase, destination)
    if phase ~= "hosting" or state.map == destination or not routes[destination] then
        error("World seed travel requires a different qualified listen map", 0)
    end
    local instance, save = host_save(state, phase)
    prepare(state, destination, instance, save, save.InstanceSeed)
end

function world_seed.prepare_loaded_seed(state, phase, destination)
    if phase ~= "ready" or not routes[destination] then error("A loaded co-op save requires the staged title menu", 0) end
    local instance, save = host_save(state, phase)
    prepare(state, destination, instance, save, save.InstanceSeed)
end

local function initial_stream_seed(world, instance)
    local actor
    for _, candidate in ipairs(FindAllOf(day_cycle_class) or {}) do
        if stock_object(candidate, day_cycle_class) and candidate:HasAuthority()
            and not candidate:IsActorBeingDestroyed() and same(candidate:GetWorld(), world)
            and same(candidate["Game Instance"], instance) then
            if actor then return nil, "stock_day_cycle_ambiguous" end
            actor = candidate
        end
    end
    if not actor then return nil, "stock_day_cycle_missing" end
    local stream = actor["Random Stream"]
    local seed = stream and stream.InitialSeed
    if not is_int32(seed) then return nil, "stock_stream_unavailable" end
    return seed
end

local function destination_proof(state, phase)
    local waiting = os.time() - receipt.arrival_seen < 3
    local ok, instance, save = pcall(host_save, state, phase)
    if not ok then
        return waiting and "verifying" or "unavailable", nil, nil, "host_save_unavailable"
    end
    local observed = save.InstanceSeed
    if instance:GetAddress() ~= receipt.instance or save:GetAddress() ~= receipt.save then
        return "changed", observed, nil, "canonical_save_replaced"
    end

    local stream_ok, seed, reason = pcall(initial_stream_seed, state.world, instance)
    if not stream_ok then reason, seed = "stock_stream_unavailable", nil end
    if seed == nil then
        if waiting then return "verifying", observed, nil, reason end
        if observed ~= receipt.expected then return "changed", observed, nil, "stock_save_seed_changed" end
        return "save_only", observed, nil, reason
    end
    if seed ~= receipt.expected then return "changed", observed, seed, "stock_stream_seed_differs" end
    if observed ~= receipt.expected then return "changed", observed, seed, "stock_live_seed_advanced" end
    return "verified", observed, seed, "stock_stream_matches"
end

function world_seed.row(state, phase, status_row)
    if not receipt then return end
    if not receipt.result and state.map == receipt.source then
        return status_row("native-seed", "prepared", receipt.source, receipt.destination, receipt.expected,
            "-", "-", "before_open_level")
    end
    if phase ~= "hosting" or state.map ~= receipt.destination or not valid(state.world) then return end
    local world_address = state.world:GetAddress()
    if receipt.world and receipt.world ~= world_address then return end
    receipt.world = world_address
    receipt.arrival_seen = receipt.arrival_seen or os.time()
    local result = receipt.result or { destination_proof(state, phase) }
    if result[1] ~= "verifying" then receipt.result = result end
    return status_row("native-seed", result[1], receipt.source, receipt.destination, receipt.expected,
        result[2] or "-", result[3] or "-", result[4])
end

return world_seed
