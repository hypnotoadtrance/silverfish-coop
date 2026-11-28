local dig = {}
local wire = require("native_wire_quest")
local guest_save = require("guest_save")
local host_save = require("native_host_save")
local players = require("players")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_HOST_DIG") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1" and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local black_market = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01"
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
local phases = {ready = true, refused = true, contact = true, unclaimed = true, escrowed = true, collecting = true,
    collected = true, owned = true, failed = true}
local durable = {ready = true, refused = true, escrowed = true, owned = true}
local geometry = {X = 1665.2142939210705, Y = 5811.305953931523, Z = 339.15110679941967}
local allowed_material = {}
for _, name in ipairs({"Dirt", "Debris", "Grass", "MossyDirt", "Mud", "Sand"}) do
    local kind = "PM_" .. name
    allowed_material["PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/" .. kind .. "." .. kind] = true
end

local heartbeat, observed, captured, diagnostic
local contact_world, contact_receipts, contact_order = nil, {}, {}
local memory_heartbeat_seen = false

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
    if not enabled or phase ~= "hosting" or state.map ~= map then return nil, "outside_scope" end
    if state.listening ~= true or state.server ~= false then return nil, "listen_host_unavailable" end
    if not valid(world) or world:GetFullName() ~= world_name then return nil, "world_unqualified" end
    if not exact(pawn, pawn_path) or details.name ~= pawn:GetFullName() or not pawn:HasAuthority()
        or not same(pawn:GetWorld(), world) then return nil, "pawn_unqualified" end
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local instance = valid(statics) and statics:GetGameInstance(pawn)
    if not exact(instance, instance_path) then return nil, "game_instance_unqualified" end
    local save = instance.SaveSlot_Ref
    if not exact(save, save_path) or not same(save, pawn.Save) then return nil, "canonical_save_unqualified" end
    if not same(pawn, instance["As Player FP"]) then return nil, "canonical_pawn_unqualified" end
    return {world = world, pawn = pawn, name = details.name, save = save, instance = instance, statics = statics}
end

local function fresh()
    if not heartbeat then return false, "not_started" end
    local active, stamp
    if type(SilverfishReadLocalHostDigHeartbeat) == "function" then
        active, stamp = SilverfishReadLocalHostDigHeartbeat()
    end
    if active then
        memory_heartbeat_seen = true
        if stamp == nil then return false, "missing" end
    else
        if memory_heartbeat_seen then return false, "missing" end
        local file = io.open(heartbeat, "rb")
        if not file then return false, "missing" end
        stamp = math.tointeger(tonumber(file:read("*l")))
        file:close()
    end
    if not stamp then return false, "invalid_timestamp" end
    local now = os.time()
    if stamp > now then return false, "future_timestamp" end
    if now - stamp > 2 then return false, "stale" end
    return true, "fresh"
end

local function quest_eligible(ctx)
    local existing = FName(quest_name, EFindName.FNAME_Find)
    local registered = existing:GetComparisonIndex() ~= 0
    -- CrossRoads can load before the Black Market quest board interns this key.
    -- Intern the one authored name only and inspect the canonical TMap as usual.
    local key = registered and existing or FName(quest_name, EFindName.FNAME_Add)
    if key:GetComparisonIndex() == 0 or key:ToString():lower() ~= quest_name:lower() then
        return false, "quest_name_unqualified"
    end
    local ok, row = pcall(wire.stock_row, ctx.save.ActiveQuests, key)
    if not ok then return false, "quest_map_unqualified" end
    if row == nil then return true, registered and "inactive" or "inactive_interned" end
    if row[step_field] == 1 then return true, "accepted" end
    return false, "quest_step_ineligible"
end

local function read(ctx)
    if type(SilverfishReadNativeHostDigScope) ~= "function" then return nil, "native_read_unavailable" end
    local phase, serial, spade, actor, package, x, y, z, material, incidental, escrow, fault, collector =
        SilverfishReadNativeHostDigScope(ctx.world:GetAddress(), ctx.pawn:GetAddress())
    if phase == nil then return nil, "native_read_empty" end
    if not phases[phase] then return nil, "native_phase_unqualified" end
    if math.type(serial) ~= "integer" or serial < 0 or serial >= 2^53 then return nil, "native_serial_unqualified" end
    return {phase = phase, serial = serial, spade = spade, actor = actor, package = package, x = x, y = y, z = z,
        material = material, incidental = incidental, escrow = escrow, fault = fault, collector = collector,
        pawn = ctx.name, world = ctx.world}
end

function dig.start(path)
    heartbeat = path .. "/native-host-dig-live-heartbeat"
end

function dig.sync(state, phase)
    local ctx, context_reason = scope(state, phase)
    diagnostic = nil
    if not enabled or phase ~= "hosting" or state.map ~= map then return end
    diagnostic = {context = context_reason or "qualified", heartbeat = "-", quest = "-", arm = "-", read = "-"}
    if not ctx then return end
    local available, heartbeat_reason = fresh()
    local eligible, quest_reason = quest_eligible(ctx)
    diagnostic.heartbeat, diagnostic.quest = heartbeat_reason, quest_reason
    if type(SilverfishRenewNativeHostDigScope) ~= "function" then
        diagnostic.arm = "native_arm_unavailable"
    elseif available and eligible then
        local accepted, reason = SilverfishRenewNativeHostDigScope(ctx.world:GetAddress(), ctx.pawn:GetAddress())
        diagnostic.arm = accepted == true and "armed"
            or type(reason) == "string" and reason:match("^stock_[a-z0-9_]+$") or "native_arm_refused"
    end
    local report, reason = read(ctx)
    diagnostic.read = report and report.phase or reason
    if report then observed = report end
end

local function require_true(value, reason)
    if not value then error(reason, 0) end
    return value
end

local function handoff(fields, state, phase)
    local refused = "stock_missing_contact_handoff_"
    local guest_name, character, identity = fields[4], fields[5], fields[6]
    require_true(enabled and #fields == 6 and guid(identity) and type(SilverfishTransferMissingContactPackage) == "function"
        and (state.map == map or state.map == black_market), refused .. "unavailable")
    local instance, pawn = host_save.canonical_host(state, phase)
    require_true(instance and valid(pawn) and pawn:GetFullName() == fields[3] and pawn.Dead == false
        and players.is_downed(pawn) == false, refused .. "host_unavailable")
    local bound, guest, save, owner = pcall(guest_save.exact_item_context, state, phase, guest_name)
    require_true(bound and valid(guest) and valid(save) and owner == character and guest.Dead == false
        and players.is_standing_guest(guest, guest_name), refused .. "guest_unavailable")
    local accepted, reason = SilverfishTransferMissingContactPackage(state.world:GetAddress(), pawn:GetAddress(),
        guest:GetAddress(), save:GetAddress(), identity)
    require_true(accepted == true and reason == "transferred", accepted == false and reason
        or "mutation_unverified_missing_contact_handoff_readback")
    return "stock sealed package handed off " .. identity
end

local function collect(fields, state, phase)
    local refused = "stock_missing_contact_collect_"
    local name, serial, identity = fields[3], math.tointeger(tonumber(fields[4])), fields[5]
    local ctx = require_true(scope(state, phase), refused .. "unavailable")
    require_true(#fields == 5 and serial and serial > 0 and serial < 2^53 and guid(identity)
        and type(SilverfishCollectNativeGuestDigPackage) == "function" and quest_eligible(ctx), refused .. "unavailable")
    local bound, guest, save = pcall(guest_save.exact_item_context, state, phase, name)
    require_true(bound and valid(guest) and valid(save) and guest.Dead == false
        and players.is_standing_guest(guest, name), refused .. "guest_unavailable")
    local accepted, reason = SilverfishCollectNativeGuestDigPackage(ctx.world:GetAddress(), ctx.pawn:GetAddress(),
        guest:GetAddress(), save:GetAddress(), serial, identity)
    require_true(accepted == true and reason == "collect_requested", accepted == false and reason
        or "mutation_unverified_missing_contact_collect_readback")
    return "stock sealed package collect requested"
end

function dig.command(fields, state, phase)
    if fields[2] == "native-missing-contact-handoff" then return handoff(fields, state, phase) end
    if fields[2] == "native-missing-contact-collect" then return collect(fields, state, phase) end
    if fields[2] == "native-guest-dig" then
        local name, identity = fields[3], fields[4]
        require_true(enabled and phase == "hosting" and (#fields == 4 or #fields == 5 or #fields == 6) and guid(identity) and
            type(SilverfishDispatchNativeGuestDig) == "function", "stock_guest_dig_unavailable")
        local ok, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
        require_true(ok and valid(pawn) and valid(save) and players.is_standing_guest(pawn, name),
            "stock_guest_dig_owner_unqualified")
        local path = pawn:GetFullName():match("^Player_FP_C (%S+)$")
        require_true(path, "stock_guest_dig_owner_unqualified")
        local aim = {}
        local component = "(%-?%d+%.%d+)"
        local triple = component .. "," .. component .. "," .. component
        if fields[6] then
            local report, operation = fields[5], fields[6]
            require_true(type(operation) == "string" and #operation == 32 and operation:match("^[0-9a-f]+$"),
                "stock_guest_dig_operation_unqualified")
            if not same(contact_world, state.world) then
                contact_world, contact_receipts, contact_order = state.world, {}, {}
            end
            local prior = contact_receipts[operation]
            if prior then
                require_true(prior.pawn == pawn:GetAddress() and prior.identity == identity and prior.report == report,
                    "stock_guest_dig_operation_conflict")
                return prior.status
            end
            local x, y, z, nx, ny, nz, material = report:match("^contact:" .. triple .. "," .. triple .. ":(/[%w_/%.-]+)$")
            require_true(x, "stock_guest_dig_contact_unqualified")
            local accepted, status = SilverfishDispatchNativeGuestDig(path, save:GetAddress(), identity,
                tonumber(x), tonumber(y), tonumber(z), tonumber(nx), tonumber(ny), tonumber(nz), material)
            require_true(accepted == true, status or "stock_guest_dig_unavailable")
            contact_receipts[operation] = {pawn = pawn:GetAddress(), identity = identity, report = report, status = status}
            contact_order[#contact_order + 1] = operation
            if #contact_order > 128 then contact_receipts[table.remove(contact_order, 1)] = nil end
            return status
        end
        if fields[5] then
            local pitch, yaw, roll, x, y, z = fields[5]:match("^" .. triple .. "," .. triple .. "$")
            if not pitch then pitch, yaw, roll = fields[5]:match("^" .. triple .. "$") end
            require_true(pitch, "stock_guest_dig_camera_unqualified")
            aim = {tonumber(pitch), tonumber(yaw), tonumber(roll), tonumber(x), tonumber(y), tonumber(z)}
        end
        local accepted, status = SilverfishDispatchNativeGuestDig(path, save:GetAddress(), identity, table.unpack(aim))
        require_true(accepted == true, status or "stock_guest_dig_unavailable")
        return status
    end
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
    local rows = {}
    if diagnostic and enabled and phase == "hosting" and state.map == map then
        local pawn = state.local_pawn and state.local_pawn.name or "-"
        rows[1] = status_row("native-host-dig-live-diagnostic", map, pawn, diagnostic.context,
            diagnostic.heartbeat, diagnostic.quest, diagnostic.arm, diagnostic.read)
    end
    local ctx = scope(state, phase)
    local report = ctx and read(ctx)
    if not report then return rows end
    local generation = captured and same(captured.world, ctx.world) and same(captured.pawn, ctx.pawn)
        and captured.serial == report.serial and captured.identity == report.escrow and captured.slot
    local contact = report.material ~= "" and report.phase ~= "ready"
    local collector = {}
    if math.type(report.collector) == "integer" and report.collector > 0 then
        for _, participant in ipairs(state.pawns) do
            if participant.kind == "guest" and valid(participant.pawn) and participant.pawn:GetAddress() == report.collector then
                collector[1] = participant.name
                break
            end
        end
    end
    local function present(value) return value ~= "" and value or "-" end
    rows[#rows + 1] = status_row("native-host-dig-live", map, ctx.name, report.phase, report.serial, present(report.spade),
        present(report.actor), present(report.package), contact and report.x or "-", contact and report.y or "-",
        contact and report.z or "-", present(report.material), report.incidental, present(report.escrow),
        generation or "-", present(report.fault), table.unpack(collector))
    return rows
end

function dig.clear()
    observed, captured, diagnostic = nil, nil, nil
end

return dig
