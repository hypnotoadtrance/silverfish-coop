local matrix = {}
local transport = require("native_transport")
local eather = require("native_matrix_eather")
local dream = require("native_dream")
local maps = require("zone_maps")

local bridge = os.getenv("SILVERFISH_COOP_BRIDGE")
local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local event_path = bridge and bridge .. "/native-matrix-events"
local matrix_class = "/Game/Classes/Items/Artifacts/Art_TeleportCube.Art_TeleportCube_C"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local canister_class = "/Game/Classes/Items/EatherCanister_BP.EatherCanister_BP_C"
local full_canister_ammo, full_canister_value = 1000, 5000
local item_class = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local item_guid = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local guid_words = { "A", "B", "C", "D" }
local position_limit, rotation_limit = 100000000, 360
local operations = {
    ["native-matrix-record"] = "record",
    ["native-matrix-inspect"] = "inspect",
    ["native-matrix-qualify"] = "qualify",
    ["native-matrix-charge"] = "charge",
    ["native-matrix-restore-remainder"] = "restore-remainder",
    ["native-matrix-place"] = "place",
    ["native-matrix-seed"] = "seed",
    ["native-matrix-equip"] = "equip",
    ["native-matrix-probe"] = "probe",
    ["native-matrix-fuel"] = "fuel",
}
local fixture_apis = {
    seed = "SilverfishSeedNativeMatrixTransport",
    equip = "SilverfishEquipNativeMatrixTransport",
    probe = "SilverfishProbeNativeMatrixTransport",
}
local sequence, pending, last_report = 0, nil, nil

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(value, limit)
    return type(value) == "number" and math.abs(value) <= limit
end

local function precise(value)
    return string.format("%.17g", value)
end

local function qualified_world(state)
    local map = state.map
    if type(map) ~= "string" or not maps[map] or dream.supports(map) or map:find("/PlaytestMap_", 1, true)
        or not valid(state.world) then return false end
    local level = map:match("/([^/]+)$")
    return level ~= nil and state.world:GetFullName() == "World " .. map .. "." .. level
end

local function owner(state, phase)
    if not qualified_world(state) then return end
    local host, fresh = transport.scoped_host(state, phase)
    if host and not host.pawn:IsActorBeingDestroyed() then return host, fresh end
end

local function scope_of(state, host)
    return { map = state.map, pawn = host.name, address = host.pawn:GetAddress(), world = state.world:GetAddress() }
end

local function same_scope(scope, state, host)
    return scope and scope.map == state.map and scope.pawn == host.name
        and scope.address == host.pawn:GetAddress() and scope.world == state.world:GetAddress()
end

local function append_pending()
    local file = event_path and io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(pending.line)
    local closed = file:close()
    return written and closed
end

function matrix.sync(state, phase)
    local host, fresh = owner(state, phase)
    if not host then
        pending, last_report = nil, nil
        return
    end
    if not same_scope(last_report, state, host) then last_report = nil end
    if not fresh then
        pending = nil
        return
    end
    if pending then
        if not same_scope(pending, state, host) or os.time() > pending.expires or append_pending() then
            pending = nil
        else
            return
        end
    end

    local take = SilverfishTakeNativeMatrixTransportRequest
    if not event_path or type(take) ~= "function" then return end
    local accepted, guid, action = take(host.pawn:GetAddress())
    if not accepted or not transport.valid_guid(guid) or (action ~= "record" and action ~= "activate") then return end

    sequence = sequence + 1
    pending = scope_of(state, host)
    pending.expires = os.time() + 2
    pending.line = table.concat({ "matrix-request", sequence, state.map, host.name, guid, action }, "\t") .. "\n"
    if append_pending() then pending = nil end
end

local function inspect_owned(host, guid)
    local inspect = SilverfishInspectNativeMatrixTransport
    if type(inspect) ~= "function" then error("matrix_native_inspector_unavailable", 0) end
    transport.load_class(matrix_class)
    local accepted, detail = inspect(host.pawn:GetAddress(), guid)
    if not accepted or detail ~= "present" then
        error("matrix_owned_guid_required (" .. tostring(detail) .. ")", 0)
    end
end

local function game_instance(host)
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(host.pawn)
    if not valid(instance) then error("matrix_canonical_instance_unavailable", 0) end
    return instance
end

local function expected_snapshot(fields)
    local remainder = tonumber(fields[6])
    if #fields < 6 or (#fields - 6) % 3 ~= 0 or not remainder or remainder < 0 or remainder >= 1 then
        error("matrix_expected_eather_snapshot_invalid", 0)
    end
    local canisters = {}
    for index = 7, #fields, 3 do
        local guid = fields[index]
        local ammo = math.tointeger(tonumber(fields[index + 1]))
        local value = math.tointeger(tonumber(fields[index + 2]))
        if not transport.valid_guid(guid) or canisters[guid] or not ammo or ammo < 0 or ammo > 2147483647
            or not value or value < -2147483648 or value > 2147483647 then
            error("matrix_expected_eather_snapshot_invalid", 0)
        end
        canisters[guid] = { ammo = ammo, value = value }
    end
    return { remainder = remainder, canisters = canisters }
end

local function transform(position, rotation)
    if not position or not rotation or not finite(position.X, position_limit) or not finite(position.Y, position_limit)
        or not finite(position.Z, position_limit) or not finite(rotation.Pitch, rotation_limit)
        or not finite(rotation.Yaw, rotation_limit) or not finite(rotation.Roll, rotation_limit) then
        error("matrix_anchor_transform_invalid", 0)
    end
    return { position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw, rotation.Roll }
end

local function angular_difference(first, second)
    return math.abs((first - second + 180) % 360 - 180)
end

local function place(fields, state, host)
    local target = { X = tonumber(fields[6]), Y = tonumber(fields[7]), Z = tonumber(fields[8]) }
    local rotation = { Pitch = tonumber(fields[9]), Yaw = tonumber(fields[10]), Roll = tonumber(fields[11]) }
    local expected = transform(target, rotation)
    local pawn, controller = host.pawn, host.controller_object
    if not pawn:K2_TeleportTo(target, rotation) then error("matrix_anchor_collision", 0) end
    controller:SetControlRotation(rotation)
    pawn:ForceNetUpdate()
    local actual = transform(pawn:K2_GetActorLocation(), controller:GetControlRotation())
    if not same(pawn:GetWorld(), state.world) or not same(controller.Pawn, pawn)
        or not pawn:HasAuthority() or not controller:IsLocalController() then
        error("matrix_anchor_relocation_unverified", 0)
    end
    for index = 1, 3 do
        if math.abs(actual[index] - expected[index]) > 1
            or angular_difference(actual[index + 3], expected[index + 3]) > 0.05 then
            error("matrix_anchor_relocation_unverified", 0)
        end
    end
    return actual
end

local function fixture_use(operation, host, guid, action)
    local invoke = _G[fixture_apis[operation]]
    if type(invoke) ~= "function" then error("matrix_fixture_native_api_unavailable", 0) end
    transport.load_class(matrix_class)
    local accepted, status
    if operation == "probe" then
        if action ~= "record" and action ~= "activate" then error("matrix_fixture_action_required", 0) end
        accepted, status = invoke(host.pawn:GetAddress(), guid, action)
    else
        accepted, status = invoke(host.pawn:GetAddress(), guid)
    end
    local expected = operation == "seed" and (status == "seeded" or status == "present")
        or operation == "equip" and status == "equipped"
        or operation == "probe" and status == "matrix_" .. action .. "_queued"
    if not accepted or not expected then error("matrix_fixture_" .. operation .. "_failed (" .. tostring(status) .. ")", 0) end
    return status
end

local function guid_parts(id)
    local parts = {}
    for index, name in ipairs(guid_words) do
        local word = tonumber(id:sub((index - 1) * 8 + 1, index * 8), 16)
        parts[name] = word >= 2147483648 and word - 4294967296 or word
    end
    return parts
end

local function guid_string(parts)
    if not parts then return end
    for _, name in ipairs(guid_words) do
        local value = parts[name]
        if type(value) ~= "number" or not math.tointeger(value) or value < -2147483648 or value > 2147483647 then
            return
        end
    end
    return string.format("%08X%08X%08X%08X", parts.A & 0xffffffff, parts.B & 0xffffffff,
        parts.C & 0xffffffff, parts.D & 0xffffffff)
end

local function inventory_count(items)
    local count = items and math.tointeger(items:GetArrayNum())
    if not count or count < 0 or count > 4096 then error("matrix_fixture_inventory_unavailable", 0) end
    return count
end

local function guid_matches(save, id)
    local matches, matched = 0, nil
    for _, field in ipairs({ "PlayerBackpack", "PlayerEquipment" }) do
        local inventory = save[field]
        for index = 1, inventory_count(inventory) do
            local item = inventory[index]
            if item and guid_string(item[item_guid]) == id then matches, matched = matches + 1, item end
        end
    end
    for _, field in ipairs({ "PlayerHelmet", "PlayerArmor" }) do
        local item = save[field]
        if not item then error("matrix_fixture_inventory_unavailable", 0) end
        if guid_string(item[item_guid]) == id then matches, matched = matches + 1, item end
    end
    return matches, matched
end

local function retire_source(source)
    if valid(source) and not source:IsActorBeingDestroyed() then source:K2_DestroyActor() end
    if valid(source) and not source:IsActorBeingDestroyed() then error("matrix_fixture_fuel_source_still_live", 0) end
end

local function fixture_fuel(state, host, id)
    if not transport.valid_guid(id) then error("matrix_fixture_fuel_guid_required", 0) end
    local pawn = host.pawn
    local instance = game_instance(host)
    local before = eather.snapshot(instance, pawn)
    local save = instance.SaveSlot_Ref
    local class = transport.load_class(canister_class)
    local stock = class:GetCDO()
    if not valid(stock) or stock.ItemType ~= 10 or stock.Ammo ~= full_canister_ammo
        or stock.Value ~= full_canister_value then
        error("matrix_fixture_full_canister_unavailable", 0)
    end

    local matches, matched = guid_matches(save, id)
    local existing = before.canisters[id]
    if matches == 1 and existing and same(matched[item_class], class)
        and existing.ammo == full_canister_ammo and existing.value == full_canister_value then
        return before, "present"
    end
    if matches ~= 0 or existing then error("matrix_fixture_fuel_guid_conflict", 0) end

    local before_count = inventory_count(save.PlayerBackpack)
    if before_count >= 4096 then error("matrix_fixture_inventory_unavailable", 0) end
    local location = pawn:K2_GetActorLocation()
    if not location or not finite(location.X, position_limit) or not finite(location.X + 125, position_limit)
        or not finite(location.Y, position_limit) or not finite(location.Z, position_limit)
        or not finite(location.Z + 10, position_limit) then error("matrix_fixture_fuel_spawn_position_invalid", 0) end
    local source = state.world:SpawnActor(class, { X = location.X + 125, Y = location.Y, Z = location.Z + 10 },
        { Pitch = 0, Yaw = 0, Roll = 0 })
    if not valid(source) then error("matrix_fixture_fuel_spawn_failed", 0) end
    local added, failure = pcall(function()
        if not same(source:GetClass(), class) or not same(source:GetWorld(), state.world) or not source:HasAuthority()
            or source:IsActorBeingDestroyed() or source.Ammo ~= full_canister_ammo
            or source.Value ~= full_canister_value then
            error("matrix_fixture_fuel_source_unqualified", 0)
        end
        instance:AddItemToInventory(source, false)
    end)
    retire_source(source)
    if not added then error(failure, 0) end

    local backpack = save.PlayerBackpack
    if not same(instance.SaveSlot_Ref, save) or not same(pawn.Save, save)
        or inventory_count(backpack) ~= before_count + 1 then error("matrix_fixture_fuel_stock_add_unverified", 0) end
    local row = backpack[before_count + 1]
    local native_guid = row and guid_string(row[item_guid])
    if not row or not same(row[item_class], class) or not transport.valid_guid(native_guid)
        or before.canisters[native_guid] or guid_matches(save, native_guid) ~= 1 then
        error("matrix_fixture_fuel_stock_add_unverified", 0)
    end
    -- The game conversion generated the row. Stamp only its GUID for fixture reconciliation.
    row[item_guid] = guid_parts(id)
    local after = eather.snapshot(instance, pawn)
    if not same(instance.SaveSlot_Ref, save) or not after.canisters[id] or after.canisters[id].ammo ~= full_canister_ammo
        or after.canisters[id].value ~= full_canister_value or after.remainder ~= before.remainder
        or guid_matches(save, id) ~= 1 then
        error("matrix_fixture_fuel_readback_unverified", 0)
    end
    for old_id, old in pairs(before.canisters) do
        local current = after.canisters[old_id]
        if not current or current.ammo ~= old.ammo or current.value ~= old.value then
            error("matrix_fixture_fuel_readback_unverified", 0)
        end
    end
    for new_id in pairs(after.canisters) do
        if new_id ~= id and not before.canisters[new_id] then error("matrix_fixture_fuel_readback_unverified", 0) end
    end
    return after, "fueled"
end

function matrix.command(fields, state, phase)
    local operation = operations[fields[2]]
    if not operation then return end
    last_report = nil
    local host, fresh = owner(state, phase)
    local guid = fields[5]
    if not host or not fresh or state.map ~= fields[3] or host.name ~= fields[4]
        or not transport.valid_guid(guid) then
        error("matrix_exact_active_host_required", 0)
    end

    local fixture = fixture_apis[operation] or operation == "fuel"
    if fixture and not fixtures then error("matrix_fixtures_disabled", 0) end
    local expected
    if operation == "charge" or operation == "restore-remainder" then
        expected = expected_snapshot(fields)
    else
        local count = operation == "place" and 11 or (operation == "probe" or operation == "fuel") and 6 or 5
        if #fields ~= count then error("matrix_command_arguments_invalid", 0) end
    end

    if operation ~= "seed" then inspect_owned(host, guid) end
    local report = scope_of(state, host)
    report.guid, report.operation = guid, operation
    if fixture_apis[operation] then
        report.result = fixture_use(operation, host, guid, fields[6])
    elseif operation == "fuel" then
        report.current, report.result = fixture_fuel(state, host, fields[6])
    elseif operation == "record" then
        report.anchor = transform(host.pawn:K2_GetActorLocation(), host.pawn:K2_GetActorRotation())
        report.result = "recorded"
    elseif operation == "inspect" then
        report.current = eather.snapshot(game_instance(host), host.pawn)
        report.result = "present"
    elseif operation == "qualify" then
        report.quote = eather.qualify(game_instance(host), host.pawn)
        report.before = report.quote.before
        report.result = "qualified"
    elseif operation == "charge" then
        report.debit = eather.spend(game_instance(host), host.pawn, expected)
        report.before, report.after = report.debit.before, report.debit.after
        report.result = "charged"
    elseif operation == "restore-remainder" then
        report.current = eather.restore_remainder(game_instance(host), host.pawn, expected)
        report.result = "restored"
    elseif operation == "place" then
        report.anchor = place(fields, state, host)
        report.result = "placed"
    end
    last_report = report
    return "matrix " .. operation .. " " .. report.result
end

function matrix.authorize_travel(guid, state, phase)
    local report = last_report
    if not report or report.travel_used or report.guid ~= guid or not transport.valid_guid(guid)
        or (report.operation ~= "charge" and report.operation ~= "restore-remainder") then return false end
    local host, fresh = owner(state, phase)
    if not host or not fresh or not same_scope(report, state, host) then return false end
    report.travel_used = true
    return true
end

function matrix.rows(state, phase, status_row)
    local report = last_report
    if not report then return {} end
    local host = owner(state, phase)
    if not host or not same_scope(report, state, host) then return {} end

    local function row(kind, ...)
        return status_row(kind, "host", report.map, report.pawn, report.guid, ...)
    end

    local rows = { row("native-matrix", report.operation, report.result) }
    if report.anchor then
        local anchor = {}
        for index, value in ipairs(report.anchor) do anchor[index] = precise(value) end
        rows[#rows + 1] = row("native-matrix-anchor", table.unpack(anchor))
    end
    if report.quote then
        local quote = report.quote
        rows[#rows + 1] = row("native-matrix-quote", 100, quote.charged, quote.available,
            quote.affinity_instances, precise(quote.after_remainder))
    end
    if report.debit then
        rows[#rows + 1] = row("native-matrix-charge", report.debit.nominal_cost, report.debit.charged)
    end
    for _, phase_name in ipairs({ "current", "before", "after" }) do
        local snapshot = report[phase_name]
        if snapshot then
            rows[#rows + 1] = row("native-matrix-eather", phase_name, precise(snapshot.remainder))
            local guids = {}
            for guid in pairs(snapshot.canisters) do guids[#guids + 1] = guid end
            table.sort(guids)
            for _, guid in ipairs(guids) do
                local canister = snapshot.canisters[guid]
                rows[#rows + 1] = row("native-matrix-canister", phase_name, guid, canister.ammo, canister.value)
            end
        end
    end
    return rows
end

return matrix
