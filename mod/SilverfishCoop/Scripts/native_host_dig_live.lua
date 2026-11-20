local dig = {}
local wire = require("native_wire_quest")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_HOST_DIG") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local world_name = "World " .. map .. ".CrossRoads_P"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local quest_name = "Note_Farmhouse"
local step_field = "Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A"
local commands = {
    ["native-host-dig-live-escrow"] = "escrow",
    ["native-host-dig-live-restore"] = "restore",
    ["native-host-dig-live-ack"] = "ack",
}
local phases = {ready = true, contact = true, unclaimed = true, escrowed = true, collecting = true,
    collected = true, owned = true, failed = true}
local durable = {ready = true, escrowed = true, owned = true}
local geometry = {X = 1665.2142939210705, Y = 5811.305953931523, Z = 339.15110679941967}
local allowed_material = {}
for _, name in ipairs({"Dirt", "Debris", "Grass", "MossyDirt", "Mud", "Sand"}) do
    local kind = "PM_" .. name
    allowed_material["PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/" .. kind .. "." .. kind] = true
end

local heartbeat, observed, captured

local function valid(object) return object and object:IsValid() end
local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end
local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end
local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) <= 10000000
end
local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value:find("[^0]")
end
local function slot(value)
    return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$")
end

local function scope(state, phase)
    local details = state.local_pawn
    local pawn, world = details and details.pawn, state.world
    if not enabled or phase ~= "hosting" or state.map ~= map or state.listening ~= true or state.server ~= false
        or not valid(world) or world:GetFullName() ~= world_name or not exact(pawn, pawn_path)
        or details.name ~= pawn:GetFullName() or not pawn:HasAuthority() or not same(pawn:GetWorld(), world) then return end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not exact(instance, instance_path) or not exact(save, save_path) or not same(save, pawn.Save)
        or not same(pawn, instance["As Player FP"]) then return end
    return {world = world, pawn = pawn, name = details.name, save = save, instance = instance, statics = statics}
end

local function fresh()
    if not heartbeat then return false end
    local file = io.open(heartbeat, "rb")
    if not file then return false end
    local stamp = tonumber(file:read("*l"))
    file:close()
    local now = os.time()
    return stamp and math.tointeger(stamp) and now >= stamp and now - stamp <= 2
end

local function quest_eligible(ctx)
    local key = FName(quest_name, EFindName.FNAME_Find)
    if key:GetComparisonIndex() == 0 or key:ToString():lower() ~= quest_name:lower() then return false end
    local row = wire.stock_row(ctx.save.ActiveQuests, key)
    return row == nil or row[step_field] == 1
end

local function read(ctx)
    if type(SilverfishReadNativeHostDigScope) ~= "function" then return end
    local phase, serial, spade, actor, package, x, y, z, material, incidental, escrow, fault =
        SilverfishReadNativeHostDigScope(ctx.world:GetAddress(), ctx.pawn:GetAddress())
    if not phases[phase] or math.type(serial) ~= "integer" or serial < 0 or serial >= 2^53 then return end
    return {phase = phase, serial = serial, spade = spade, actor = actor, package = package, x = x, y = y, z = z,
        material = material, incidental = incidental, escrow = escrow, fault = fault, pawn = ctx.name, world = ctx.world}
end

function dig.start(path)
    heartbeat = path .. "native-host-dig-heartbeat"
end

function dig.sync(state, phase)
    local ctx = scope(state, phase)
    if not ctx or type(SilverfishRenewNativeHostDigScope) ~= "function" then return end
    if fresh() and quest_eligible(ctx) then
        SilverfishRenewNativeHostDigScope(ctx.world:GetAddress(), ctx.pawn:GetAddress())
    end
    local report = read(ctx)
    if report then observed = report end
end

local function require_true(value, reason)
    if not value then error(reason, 0) end
    return value
end

function dig.command(fields, state, phase)
    local operation = commands[fields[2]]
    if not operation then return end
    local ctx = require_true(scope(state, phase), "stock_live_dig_owner_unqualified")
    require_true(fields[3] == ctx.name, "stock_live_dig_owner_unqualified")
    if operation == "escrow" then
        local serial, generation = math.tointeger(tonumber(fields[4])), fields[5]
        require_true(#fields == 5 and serial and serial > 0 and slot(generation)
            and type(SilverfishCaptureNativeHostDigEscrow) == "function"
            and type(SilverfishVerifyNativeHostDigEscrow) == "function", "stock_live_dig_escrow_arguments")
        local save_class = StaticFindObject(save_path)
        local scratch = valid(save_class) and ctx.statics:CreateSaveGameObject(save_class)
        require_true(exact(scratch, save_path) and not same(scratch, ctx.save), "stock_live_dig_scratch_unavailable")
        local accepted, reason, identity = SilverfishCaptureNativeHostDigEscrow(
            ctx.world:GetAddress(), ctx.pawn:GetAddress(), serial, scratch:GetAddress())
        require_true(accepted == true, reason or "stock_live_dig_escrow_capture_unavailable")
        if reason == "collected" then return "stock host already collected the sealed package" end
        require_true(reason == "captured" and guid(identity) and ctx.statics:SaveGameToSlot(scratch, generation, 0) == true,
            "stock_live_dig_escrow_save_unverified")
        local readback = ctx.statics:LoadGameFromSlot(generation, 0)
        require_true(exact(readback, save_path) and not same(readback, scratch) and not same(readback, ctx.save)
            and SilverfishVerifyNativeHostDigEscrow(ctx.world:GetAddress(), ctx.pawn:GetAddress(), serial,
                readback:GetAddress(), identity) == true, "stock_live_dig_escrow_payload_unverified")
        captured = {world = ctx.world, pawn = ctx.pawn, serial = serial, identity = identity, slot = generation}
        return "the exact loose sealed package survived typed escrow serialization"
    end
    if operation == "ack" then
        local number, status, identity = math.tointeger(tonumber(fields[4])), fields[5], fields[6]
        require_true(#fields == 6 and number and number > 0 and (status == "escrowed" or status == "owned")
            and guid(identity) and type(SilverfishAcknowledgeNativeHostDig) == "function", "stock_live_dig_commit_arguments")
        local accepted, detail = SilverfishAcknowledgeNativeHostDig(
            ctx.world:GetAddress(), ctx.pawn:GetAddress(), number, status, identity)
        if accepted ~= true then
            local current = read(ctx)
            require_true(status == "escrowed" and current and current.serial == number
                and (current.phase == "collected" or current.phase == "collecting")
                and current.escrow == identity, detail or "stock_live_dig_commit_changed")
        end
        return "sealed package durable receipt acknowledged"
    end
    local generation, identity, spade = fields[4], fields[5], fields[6]
    local x, y, z, material = tonumber(fields[7]), tonumber(fields[8]), tonumber(fields[9]), fields[10]
    require_true(#fields == 10 and slot(generation) and guid(identity) and guid(spade) and allowed_material[material]
        and finite(x) and finite(y) and finite(z) and (x - geometry.X)^2 + (y - geometry.Y)^2 + (z - geometry.Z)^2 <= 45.1^2
        and type(SilverfishRestoreNativeHostDigEscrow) == "function" and quest_eligible(ctx), "stock_live_dig_restore_arguments")
    local scratch = ctx.statics:LoadGameFromSlot(generation, 0)
    require_true(exact(scratch, save_path) and not same(scratch, ctx.save), "stock_live_dig_escrow_restore_unavailable")
    local accepted, detail = SilverfishRestoreNativeHostDigEscrow(ctx.world:GetAddress(), ctx.pawn:GetAddress(),
        scratch:GetAddress(), identity, spade, x, y, z, material)
    require_true(accepted == true, detail or "stock_live_dig_escrow_restore_unavailable")
    return "the exact sealed package is present at its persisted stock contact"
end

function dig.capture_allowed(state, phase, token)
    local ctx = scope(state, phase)
    local report = ctx and read(ctx)
    if not report then return token == nil and (not observed or durable[observed.phase] == true) end
    observed = report
    if durable[report.phase] then return token == nil end
    if report.phase == "collected" then
        return quest_eligible(ctx) and token == ("dig@%d@owned@%s"):format(report.serial, report.package)
    end
    return report.phase == "unclaimed" and captured and same(captured.world, ctx.world)
        and same(captured.pawn, ctx.pawn) and captured.serial == report.serial and captured.identity == report.escrow
        and quest_eligible(ctx)
        and token == ("dig@%d@escrowed@%s"):format(report.serial, report.escrow)
end

function dig.rows(state, phase, status_row)
    local ctx = scope(state, phase)
    local report = ctx and read(ctx)
    if not report then return {} end
    local generation = captured and same(captured.world, ctx.world) and same(captured.pawn, ctx.pawn)
        and captured.serial == report.serial and captured.identity == report.escrow and captured.slot
    local contact = report.material ~= "" and report.phase ~= "ready"
    local function present(value) return value ~= "" and value or "-" end
    return {status_row("native-host-dig-live", map, ctx.name, report.phase, report.serial, present(report.spade),
        present(report.actor), present(report.package), contact and report.x or "-", contact and report.y or "-",
        contact and report.z or "-", present(report.material), report.incidental, present(report.escrow),
        generation or "-", present(report.fault))}
end

function dig.clear()
    observed, captured = nil, nil
end

return dig
