local bed = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_TRANSPORT") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local gameplay_statics = "/Script/Engine.Default__GameplayStatics"
local code_library = "/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C"
local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local profiles = {
    [observatory] = {
        level = "Observitory_P", actor = "SavePoint_BP_C_3", alternate = "LevelExit_Spawn_BP_C_1",
        faction = 8, min_safe_rep = -90,
    },
    ["/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"] = {
        level = "DeadBridge_P", actor = "SavePoint_BP_C_1", alternate = "LevelExit_Spawn_BP_C_4",
        faction = 4, min_safe_rep = 300,
    },
}

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function is_int32(value)
    return type(value) == "number" and value == math.floor(value) and value >= -2147483648 and value <= 2147483647
end

local function finite(value)
    return type(value) == "number" and value == value and math.abs(value) <= 100000000
end

local function text(value)
    if type(value) == "userdata" then value = value:ToString() end
    if type(value) ~= "string" or #value > 256 or value:find("[%c]") then
        error("The stock rest save value is invalid", 0)
    end
    return value
end

local function host_context(state, phase)
    local details = state.local_pawn
    local pawn = details and details.pawn
    local controller = details and details.controller_object
    local world = state.world
    if not enabled or phase ~= "hosting" or not state.listening or state.server or not valid(world)
        or not valid(pawn) or not pawn:HasAuthority() or not pawn:GetFullName():match("^Player_FP_C ")
        or not valid(controller) or not controller:IsLocalController() or not same(controller.Pawn, pawn)
        or not same(world, pawn:GetWorld()) or not same(world, controller:GetWorld())
        or world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map then
        error("The active authoritative listen host is unavailable", 0)
    end

    local statics = StaticFindObject(gameplay_statics)
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(instance) or not instance:GetFullName():match("^GameInstance_FP_C ")
        or not same(pawn.Save, save) or not save:GetFullName():match("^SaveGame_BP_C ") then
        error("The canonical native host save is unavailable", 0)
    end
    return pawn, controller, world, instance, save
end

local function expected_id(profile)
    return profile.actor .. "-" .. profile.level .. "-"
end

local function actor_name(map, profile, class, name)
    return class .. " " .. map .. "." .. profile.level .. ":PersistentLevel." .. name
end

local function rest_actor(map, world, profile)
    local name = actor_name(map, profile, "SavePoint_BP_C", profile.actor)
    local match
    for _, actor in ipairs(FindAllOf("SavePoint_BP_C") or {}) do
        if valid(actor) and actor:GetFullName() == name and same(actor:GetWorld(), world) then
            if match then error("The qualified stock rest actor is ambiguous", 0) end
            match = actor
        end
    end
    if not valid(match) or not valid(match.Arrow) or match.Faction ~= profile.faction
        or match.MinSafeRep ~= profile.min_safe_rep then
        error("The qualified stock rest actor is unavailable or has changed", 0)
    end
    return match
end

local function stock_id(actor)
    local library = StaticFindObject(code_library)
    if not valid(library) then error("The stock rest ID resolver is unavailable", 0) end
    local out = {}
    library:Calc_UniqueID(actor, actor, out)
    return text(out.ID)
end

local function qualify_id(actor, profile)
    local id = stock_id(actor)
    if id ~= expected_id(profile) then error("The qualified stock rest actor ID did not match", 0) end
    return id
end

function bed.source(state, phase)
    local _, _, world, _, save = host_context(state, phase)
    local level = text(save.RespawnLevel)
    if level == "" then
        local profile = profiles[observatory]
        if state.map == observatory then qualify_id(rest_actor(observatory, world, profile), profile) end
        return { source = state.map, destination = observatory, bed_id = "", actor = profile.actor, fallback = true }
    end

    for map, profile in pairs(profiles) do
        if level == profile.level or level == map then
            local id = text(save.Bed)
            if id ~= expected_id(profile) then error("The host's saved rest ID is outside the qualified catalog", 0) end
            if state.map == map then qualify_id(rest_actor(map, world, profile), profile) end
            return { source = state.map, destination = map, bed_id = id, actor = profile.actor, fallback = false }
        end
    end
    error("The host's saved rest map is outside the qualified catalog", 0)
end

local function stock_placement(map, world, actor, profile, instance)
    local out = {}
    instance:Find_PlayerReputation(actor.Faction, out, out, out)
    local points = out.Points
    if not is_int32(points) then error("The canonical host's stock faction reputation is unavailable", 0) end
    if points >= actor.MinSafeRep then
        return actor.Arrow:K2_GetComponentLocation(), actor.Arrow:K2_GetComponentRotation(), "arrow", points
    end

    local alternate = actor.Safe_AltSpawn
    if not valid(alternate) or alternate:GetFullName() ~= actor_name(map, profile, "LevelExit_Spawn_BP_C", profile.alternate)
        or not same(alternate:GetWorld(), world) then
        error("The stock reputation alternate spawn is unavailable", 0)
    end
    local location = alternate:K2_GetActorLocation()
    return { X = location.X, Y = location.Y, Z = location.Z + 50 }, alternate:K2_GetActorRotation(), "alternate", points
end

function bed.place_host(pawn, controller, world, target, rotation, placement)
    if not finite(target.X) or not finite(target.Y) or not finite(target.Z)
        or not finite(rotation.Pitch) or not finite(rotation.Yaw) or not finite(rotation.Roll) then
        error("The stock rest arrival transform is invalid", 0)
    end
    local aim = { Pitch = rotation.Pitch, Yaw = rotation.Yaw, Roll = rotation.Roll }
    local heights = placement == "alternate" and { 0, 50, 100 } or { 0 }
    local placed = false
    for _, offset in ipairs(heights) do
        if pawn:K2_TeleportTo({ X = target.X, Y = target.Y, Z = target.Z + offset }, aim) then
            placed = true
            break
        end
    end
    if not placed then
        error("The stock rest marker has no safe host placement", 0)
    end
    controller:SetControlRotation(aim)
    pawn:ForceNetUpdate()
    local location = pawn:K2_GetActorLocation()
    if not same(pawn:GetWorld(), world) or not same(controller.Pawn, pawn)
        or not finite(location.X) or not finite(location.Y) or not finite(location.Z) then
        error("The stock rest arrival position could not be read back", 0)
    end
    return location
end

function bed.arrival(state, phase, rest)
    local pawn, controller, world, instance = host_context(state, phase)
    local profile = profiles[state.map]
    if type(rest) ~= "table" or rest.destination ~= state.map or not profile or type(rest.fallback) ~= "boolean"
        or (rest.fallback and (rest.destination ~= observatory or rest.bed_id ~= ""))
        or (not rest.fallback and rest.bed_id ~= expected_id(profile)) then
        error("The requested arrival is outside the qualified stock rest catalog", 0)
    end
    local actor = rest_actor(state.map, world, profile)
    local id = qualify_id(actor, profile)
    local target, rotation, placement, points = stock_placement(state.map, world, actor, profile, instance)
    local location = bed.place_host(pawn, controller, world, target, rotation, placement)
    return { destination = state.map, bed_id = id, fallback = rest.fallback, actor = actor:GetFullName(),
        placement = placement, points = points, min_safe_rep = profile.min_safe_rep,
        x = location.X, y = location.Y, z = location.Z }
end

function bed.restore(state, phase, destination, id)
    local _, _, world, _, save = host_context(state, phase)
    local profile = profiles[destination]
    if not profile or id ~= expected_id(profile) then error("The saved rest is outside the qualified catalog", 0) end
    if destination == state.map then qualify_id(rest_actor(destination, world, profile), profile) end
    local original_level, original_bed = text(save.RespawnLevel), text(save.Bed)
    local ok, result = pcall(function()
        save.RespawnLevel = profile.level
        save.Bed = id
        if text(save.RespawnLevel) ~= profile.level or text(save.Bed) ~= id then
            error("The staged stock bed fields could not be read back", 0)
        end
        return bed.source(state, phase)
    end)
    if not ok then
        save.RespawnLevel = original_level
        save.Bed = original_bed
        error(result, 0)
    end
    return result
end

function bed.select_fixture_rest(state, phase)
    if not fixtures then error("Selecting a fixture rest requires the staged fixture host", 0) end
    local profile = profiles[state.map]
    if not profile then error("The current map has no qualified fixture rest actor", 0) end
    return bed.restore(state, phase, state.map, expected_id(profile))
end

return bed
