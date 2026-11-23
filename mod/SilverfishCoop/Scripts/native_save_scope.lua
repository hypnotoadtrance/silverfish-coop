local scope = {}

local active
local registered = {}
local statics = "/Script/Engine.GameplayStatics:"
local async = "/Script/Engine.AsyncActionHandleSaveGame:"
local find_files = "/Script/BlueprintFileUtils.BlueprintFileUtilsBPLibrary:FindFiles"
local save_class = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_class = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local title_world = "World /Game/Classes/Maps/MainMenu_P.MainMenu_P"
local max_ms = 922337203685477

local function text(value)
    return type(value) == "userdata" and value:ToString() or value
end

local function normalized(path)
    return (path:gsub("\\", "/"):gsub("//+", "/"):gsub("/+$", ""):lower())
end

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function install_guest(guest)
    local instance = guest.instance
    instance.SaveSlot_Ref = guest.save
    instance.CurrentSaveSlot = guest.slot
    instance.SaveAfterTransition = false
    if not same(instance.SaveSlot_Ref, guest.save) or text(instance.CurrentSaveSlot) ~= guest.slot
        or instance.SaveAfterTransition ~= false then
        error("The game did not retain the isolated co-op guest view", 0)
    end
end

function scope.prepare_guest(state)
    if not active or not valid(state.world) or state.listening and not state.server then
        error("The guest save view requires a private co-op scope", 0)
    end
    local instance = state.world.OwningGameInstance
    local class, api = StaticFindObject(save_class), StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(instance) or not same(instance:GetClass(), StaticFindObject(instance_class))
        or not valid(class) or not valid(api) then
        error("The guest save view requires the stock game instance", 0)
    end
    if active.guest and not same(active.guest.instance, instance) then
        error("The game instance changed during the co-op guest session", 0)
    end
    if not active.guest then
        local save = api:CreateSaveGameObject(class)
        if not valid(save) or not same(save:GetClass(), class) or same(save, instance.SaveSlot_Ref) then
            error("The game could not allocate the isolated co-op guest view", 0)
        end
        active.guest = {instance = instance, save = save, slot = "SFCoopGuest_" .. active.id,
            previous_save = instance.SaveSlot_Ref, previous_slot = text(instance.CurrentSaveSlot),
            previous_autosave = instance.SaveAfterTransition}
    end
    install_guest(active.guest)
end

local function bounded_integer(value, low, high)
    local integer = type(value) == "string" and #value <= 16 and value:match("^%-?%d+$") and math.tointeger(tonumber(value))
    return integer and integer >= low and integer <= high and integer or nil
end

function scope.prepare_guest_world(state, phase, session, visit, map, seed_text, time_text)
    local guest = active and active.guest
    if guest then guest.world_start = nil end
    if seed_text == nil and time_text == nil then return end
    local seed = bounded_integer(seed_text, -2147483648, 2147483647)
    local milliseconds = bounded_integer(time_text, 1, max_ms)
    local world, controller = state.world, state.controller
    if not seed or not milliseconds or not guest or phase ~= "ready" or state.listening or state.server
        or not valid(world) or world:GetFullName() ~= title_world or not valid(controller)
        or not same(controller:GetWorld(), world) or not same(world.OwningGameInstance, guest.instance)
        or not same(guest.instance.SaveSlot_Ref, guest.save) or same(guest.save, guest.previous_save)
        or text(guest.instance.CurrentSaveSlot) ~= guest.slot or guest.instance.SaveAfterTransition ~= false then
        return
    end
    if type(SilverfishSetNativeGuestWorldStart) ~= "function" then return end
    local result, observed_seed, observed_ms, previous_step =
        SilverfishSetNativeGuestWorldStart(controller:GetAddress(), guest.save:GetAddress(), active.id, seed, milliseconds)
    if type(result) == "string" and result:match("^mutation_unverified_") then error(result, 0) end
    if result ~= "applied" then return end
    if guest.previous_travel_step == nil and type(previous_step) == "number" then
        guest.previous_travel_step = previous_step
    end
    if observed_seed ~= seed or observed_ms ~= milliseconds or type(previous_step) ~= "number"
        or guest.instance.Travel_TimeStep ~= 0 then error("mutation_unverified_guest_world_receipt", 0) end
    guest.world_start = {session = session, visit = visit, map = map, seed = observed_seed, milliseconds = observed_ms}
end

function scope.guest_world_start_row(state, phase, status_row)
    local guest = active and active.guest
    local start = guest and guest.world_start
    if not start or not valid(state.world) or not same(state.world.OwningGameInstance, guest.instance)
        or (phase ~= "joining" and (phase ~= "connected" or state.map ~= start.map)) then return end
    return status_row("native-guest-world-start", start.session, start.visit, start.map, start.seed, start.milliseconds)
end

function scope.bind_guest(state, phase)
    if not active or not active.guest or phase ~= "connected" or state.server ~= true
        or not valid(state.world) or not same(state.world.OwningGameInstance, active.guest.instance) then return end
    local pawn = state.local_pawn and state.local_pawn.pawn
    if not valid(pawn) or pawn:HasAuthority() or not pawn:IsLocallyControlled()
        or not same(pawn:GetWorld(), state.world) or not same(pawn.Controller, state.controller) then return end
    local guest = active.guest
    if not same(guest.instance.SaveSlot_Ref, guest.save) or text(guest.instance.CurrentSaveSlot) ~= guest.slot
        or guest.instance.SaveAfterTransition ~= false then install_guest(guest) end
    if not same(pawn.Save, guest.save) then
        pawn.Save = guest.save
        if not same(pawn.Save, guest.save) then error("The local co-op pawn did not adopt its view", 0) end
    end
    return guest.save, pawn, active.id
end

function scope.guest_health(state, phase)
    local guest = active and active.guest
    local pawn = state.local_pawn and state.local_pawn.pawn
    if phase == "connected" and state.server and guest and valid(state.world) and valid(pawn)
        and same(state.world.OwningGameInstance, guest.instance) and same(guest.instance.SaveSlot_Ref, guest.save)
        and same(pawn.Save, guest.save) then return guest.save.PlayerHealth end
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
    local guest = active.guest
    if guest and valid(guest.instance) then
        local instance = guest.instance
        if same(instance.SaveSlot_Ref, guest.save) then
            instance.SaveSlot_Ref = valid(guest.previous_save) and guest.previous_save or nil
        end
        if text(instance.CurrentSaveSlot) == guest.slot then instance.CurrentSaveSlot = guest.previous_slot end
        instance.SaveAfterTransition = guest.previous_autosave
        if guest.previous_travel_step ~= nil and instance.Travel_TimeStep == 0 then
            instance.Travel_TimeStep = guest.previous_travel_step
        end
    end
    active = nil
end

function scope.selected()
    return active and active.id
end

return scope
