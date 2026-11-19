local pickup = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local bandage_path = "/Game/Classes/Items/Item_Bandage.Item_Bandage_C"
local pickup_radius = 250
local fixture
local last

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_guid(value)
    if type(value) ~= "string" or #value ~= 32 or value:find("[^0-9A-F]") or not value:find("[^0]") then
        error("Expected a nonzero native pickup GUID in uppercase hexadecimal", 0)
    end
    return value
end

local function enrolled(state, phase, name)
    if phase ~= "hosting" or state.clients ~= 1 or #state.pawns ~= 2
        or not state.local_pawn or not valid(state.world) then
        error("Native pickup requires an active two-player host", 0)
    end
    for _, details in ipairs(state.pawns) do
        local pawn = details.pawn
        if details.kind == "guest" and details.name == name and guest_save.matches(pawn) then
            return pawn, state.local_pawn.pawn.Save
        end
    end
    error("The exact guest does not own an independent native save", 0)
end

local function live_source(world)
    if not fixture or not same(fixture.world, world) then return false end
    local actor = fixture.actor
    return valid(actor) and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
        and actor:GetFullName() == fixture.name and same(actor:GetWorld(), world)
end

local function distance_squared(first, second)
    local x, y, z = first.X - second.X, first.Y - second.Y, first.Z - second.Z
    return x * x + y * y + z * z
end

local function standing(pawn)
    return pawn.Dead == false and pawn.Save.PlayerHealth > 0
end

local function report(state, guid, record, source_name)
    last = { state = state, guid = guid, item = record, actor = source_name }
end

local function spawn(world, pawn, guid)
    if fixture and fixture.guid == guid and fixture.state == "committed" then
        error("This pickup transaction is already committed", 0)
    end
    if live_source(world) then
        if fixture.guid ~= guid or fixture.state ~= "spawned" then
            error("Another native pickup source is already active", 0)
        end
        report("spawned", guid, nil, fixture.name)
        return "native pickup source already exists"
    end
    if not standing(pawn) then error("Native pickup requires a standing guest", 0) end
    local class = StaticFindObject(bandage_path)
    if not valid(class) then
        LoadAsset(bandage_path)
        class = StaticFindObject(bandage_path)
    end
    if not valid(class) then error("The stock Bandage class is unavailable", 0) end
    local position = pawn:K2_GetActorLocation()
    local placement = { X = position.X + 125, Y = position.Y, Z = position.Z + 10 }
    local actor = world:SpawnActor(class, placement, { Pitch = 0, Yaw = 0, Roll = 0 })
    if not valid(actor) then error("Could not spawn the stock Bandage", 0) end
    local ok, failure = pcall(function()
        if not actor:HasAuthority() or not same(actor:GetWorld(), world) then
            error("The stock Bandage did not spawn under host authority", 0)
        end
        -- The stock item simulates its collision Box. Disable it before removing collision.
        local box = actor.Box
        if not valid(box) then error("The stock Bandage collision Box is unavailable", 0) end
        actor.Physics = false
        box:SetSimulatePhysics(false)
        box:SetEnableGravity(false)
        box:SetCollisionEnabled(0)
        actor:SetActorEnableCollision(false)
        if valid(actor.DisplayMesh) then
            actor.DisplayMesh:SetSimulatePhysics(false)
            actor.DisplayMesh:SetIsReplicated(true)
        end
        actor:K2_SetActorLocation(placement, false, {}, true)
        if not (distance_squared(actor:K2_GetActorLocation(), placement) <= 1) then
            error("The stock Bandage moved away from its staged location", 0)
        end
        actor.bAlwaysRelevant = true
        actor:SetReplicates(true)
        actor:SetReplicateMovement(true)
        actor:ForceNetUpdate()
    end)
    if not ok then
        actor:K2_DestroyActor()
        error(tostring(failure), 0)
    end
    fixture = { actor = actor, name = actor:GetFullName(), world = world,
        guid = guid, pawn = pawn:GetFullName(), state = "spawned" }
    report("spawned", guid, nil, fixture.name)
    return "stock Bandage spawned"
end

local function prepare(world, pawn, source_name, guid)
    if not live_source(world) or fixture.guid ~= guid or fixture.name ~= source_name
        or fixture.pawn ~= pawn:GetFullName() then
        error("The exact staged native pickup source is unavailable", 0)
    end
    if fixture.state == "prepared" and not fixture.applied then
        report("prepared", guid, fixture.item, source_name)
        return "native pickup source already reserved"
    end
    if fixture.state ~= "spawned" then error("The native pickup source cannot be reserved", 0) end
    if not standing(pawn) then error("Native pickup requires a living guest with positive HP", 0) end
    local distance = distance_squared(pawn:K2_GetActorLocation(), fixture.actor:K2_GetActorLocation())
    if not (distance <= pickup_radius * pickup_radius) then
        error(string.format("Native pickup source is %.1f cm from the guest (maximum %d cm)",
            math.sqrt(distance), pickup_radius), 0)
    end
    local ok, ammo, weight, value, never_drop = SilverfishReadNativePickup(fixture.actor:GetAddress())
    if not ok then error("Native pickup metadata unavailable (" .. tostring(ammo) .. ")", 0) end
    local record = { ammo = ammo, weight = weight, value = value, never_drop = never_drop }
    fixture.actor:SetActorHiddenInGame(true)
    fixture.item, fixture.state = record, "prepared"
    report("prepared", guid, record, source_name)
    return "native pickup source reserved"
end

local function apply(stock, pawn, guid, record)
    local ok, result = SilverfishAppendNativePickup(stock:GetAddress(), pawn:GetAddress(), pawn.Save:GetAddress(),
        guid, record.ammo, record.weight, record.value, record.never_drop)
    if not ok then error("Native guest inventory rejected the pickup (" .. tostring(result) .. ")", 0) end
end

local function commit(world, pawn, stock, guid)
    if not fixture or not same(fixture.world, world) or fixture.guid ~= guid
        or fixture.pawn ~= pawn:GetFullName() then
        error("The requested native pickup transaction is unavailable", 0)
    end
    if fixture.state == "committed" then
        report("committed", guid, fixture.item, fixture.name)
        return "native pickup transaction already committed"
    end
    if fixture.state ~= "prepared" or (not fixture.applied and not live_source(world)) then
        error("The native pickup source must be reserved before commit", 0)
    end
    apply(stock, pawn, guid, fixture.item)
    fixture.applied = true
    if live_source(world) then
        fixture.actor:K2_DestroyActor()
        if live_source(world) then error("The awarded native pickup actor could not be consumed", 0) end
    end
    fixture.state = "committed"
    report("committed", guid, fixture.item, fixture.name)
    return "native pickup awarded to the guest"
end

local function abort(world, pawn, guid)
    if not live_source(world) or fixture.guid ~= guid or fixture.pawn ~= pawn:GetFullName()
        or fixture.state == "committed" or fixture.applied then
        error("The native pickup reservation cannot be released", 0)
    end
    fixture.actor:SetActorHiddenInGame(false)
    fixture.state, fixture.item = "spawned", nil
    report("spawned", guid, nil, fixture.name)
    return "native pickup source reservation released"
end

local function restore(pawn, stock, guid, fields)
    local ammo, weight, value, never_drop = math.tointeger(fields[5]), tonumber(fields[6]),
        math.tointeger(fields[7]), fields[8]
    if not ammo or not weight or not value or (never_drop ~= "0" and never_drop ~= "1") then
        error("Invalid saved native pickup metadata", 0)
    end
    local record = { ammo = ammo, weight = weight, value = value, never_drop = never_drop == "1" }
    apply(stock, pawn, guid, record)
    report("restored", guid, record)
    return "native pickup restored to the guest"
end

local function remove(pawn, stock, guid)
    if type(SilverfishRemoveNativePickup) ~= "function" then
        error("Native pickup removal is unavailable", 0)
    end
    local ok, detail = SilverfishRemoveNativePickup(stock:GetAddress(), pawn:GetAddress(), pawn.Save:GetAddress(), guid)
    if not ok then error("Native guest inventory rejected pickup removal (" .. tostring(detail) .. ")", 0) end
    report("removed", guid)
    return detail == "pickup_already_absent" and "native pickup was already absent" or "native pickup removed"
end

function pickup.command(fields, state, phase)
    local action = fields[2]
    if type(action) ~= "string" or not action:match("^native%-pickup%-") then return end
    if not enabled or type(SilverfishReadNativePickup) ~= "function"
        or type(SilverfishAppendNativePickup) ~= "function" then
        error("The isolated native pickup fixture is unavailable", 0)
    end
    local pawn, stock = enrolled(state, phase, fields[3])
    if action == "native-pickup-prepare" and #fields == 5 then
        return prepare(state.world, pawn, fields[4], exact_guid(fields[5]))
    end
    local guid = exact_guid(fields[4])
    if action == "native-pickup-restore" and #fields == 8 then return restore(pawn, stock, guid, fields) end
    if #fields ~= 4 then error("Invalid native pickup command arguments", 0) end
    if action == "native-pickup-spawn" then return spawn(state.world, pawn, guid) end
    if action == "native-pickup-commit" then return commit(state.world, pawn, stock, guid) end
    if action == "native-pickup-abort" then return abort(state.world, pawn, guid) end
    if action == "native-pickup-remove" then return remove(pawn, stock, guid) end
    error("Unknown native pickup operation", 0)
end

function pickup.rows(state, phase, status_row)
    if not enabled or phase ~= "hosting" then return {} end
    if fixture and not same(fixture.world, state.world) then fixture, last = nil, nil end
    if not last then return {} end
    local item = last.item
    return { status_row("native-pickup", last.state, last.guid, bandage_path,
        item and item.ammo or "-", item and string.format("%.17g", item.weight) or "-",
        item and item.value or "-", item and (item.never_drop and 1 or 0) or "-", last.actor or "-") }
end

return pickup
