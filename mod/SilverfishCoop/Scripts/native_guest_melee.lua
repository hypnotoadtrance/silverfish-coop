local melee = {}
local guest_save = require("guest_save")

local map = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local actions = {
    ["native-guest-melee-canary-equip"] = true,
    ["native-guest-melee-canary-start"] = true,
    ["native-guest-melee-canary-observe"] = true,
    ["native-guest-melee-canary-abort"] = true,
}
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local latest, equipment

local function qualified_host(state, phase)
    return enabled and phase == "hosting" and state.listening and not state.server and state.map == map
        and type(SilverfishStartNativeGuestMeleeCanary) == "function"
        and type(SilverfishReadNativeGuestMeleeCanary) == "function"
        and type(SilverfishAbortNativeGuestMeleeCanary) == "function"
        and type(SilverfishEquipNativeGuestOldPipe) == "function"
end

local function exact_guest(state, phase, name, standing)
    if type(standing) ~= "function" or type(name) ~= "string" then return end
    local called, pawn, save = pcall(guest_save.exact_item_context, state, phase, name)
    if not called or not pawn or not pawn:IsValid() or not save or not save:IsValid() then return end
    local projected, ready = pcall(standing, pawn, name)
    if projected and ready == true and pawn:GetFullName() == name then return pawn, save end
end

local function observation(ok, status, serial, presses, releases, contacts, qualified, settled, passives)
    local dirty = type(status) == "string" and status:match("^mutation_unverified_guest_melee_") ~= nil
    if (ok ~= true and not dirty) or (dirty and ok ~= false)
        or math.type(serial) ~= "integer" or serial < 1 or serial >= 2 ^ 53
        or presses ~= 1 or (releases ~= 0 and releases ~= 1)
        or math.type(contacts) ~= "integer" or contacts < 0 or contacts > 64
        or (qualified ~= 0 and qualified ~= 1) or qualified > contacts
        or type(settled) ~= "boolean" or type(passives) ~= "boolean"
        or settled ~= (status == "surface_verified")
        or (settled and (releases ~= 1 or contacts ~= 1 or qualified ~= 1 or not passives))
        or (not dirty and status ~= "pending" and status ~= "surface_verified" and status ~= "aborted_contact_fenced") then
        error("mutation_unverified_guest_melee_abi", 0)
    end
    return {status = status, serial = serial, presses = presses, releases = releases,
        contacts = contacts, qualified = qualified, settled = settled, passives = passives}
end

local function read(scope, abort)
    local native = abort and SilverfishAbortNativeGuestMeleeCanary or SilverfishReadNativeGuestMeleeCanary
    local proof = observation(native(scope.proof.serial))
    if proof.serial ~= scope.proof.serial then error("mutation_unverified_guest_melee_owner", 0) end
    scope.proof = proof
end

function melee.command(fields, state, phase, standing)
    local action = fields[2]
    if not actions[action] then return end
    if not qualified_host(state, phase) then error("native_guest_melee_fixture_unavailable", 0) end
    if action == "native-guest-melee-canary-start" or action == "native-guest-melee-canary-equip" then
        local name, guid = fields[3], fields[4]
        if #fields ~= 4 or type(guid) ~= "string" or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]") then
            error("native_guest_melee_exact_guid_required", 0)
        end
        local pawn, save = exact_guest(state, phase, name, standing)
        if not pawn then error("native_guest_melee_exact_owner_required", 0) end
        if action == "native-guest-melee-canary-equip" then
            if latest and latest.proof.status == "pending" then error("native_guest_melee_canary_pending", 0) end
            local ok, status = SilverfishEquipNativeGuestOldPipe(pawn:GetAddress(), save:GetAddress(), guid)
            if type(ok) ~= "boolean" or type(status) ~= "string" or (ok and status ~= "old_pipe_equipped") then
                error("mutation_unverified_guest_melee_equip_abi", 0)
            end
            equipment = {pawn = name, guid = guid, ok = ok, status = status}
            return "stock guest Old Pipe equip observed"
        end
        local ok, status, serial, presses, releases, contacts, qualified, settled, passives =
            SilverfishStartNativeGuestMeleeCanary(pawn:GetAddress(), save:GetAddress(), guid)
        if ok == false and serial == 0 and type(status) == "string" and status:match("^native_guest_melee_") then
            error(status, 0)
        end
        latest = {pawn = name, guid = guid, address = pawn:GetAddress(),
            proof = observation(ok, status, serial, presses, releases, contacts, qualified, settled, passives)}
    else
        local serial = math.tointeger(fields[3])
        if #fields ~= 3 or not latest or serial ~= latest.proof.serial then
            error("native_guest_melee_exact_serial_required", 0)
        end
        read(latest, action == "native-guest-melee-canary-abort")
    end
    return "stock guest Old Pipe animation observed"
end

function melee.rows(state, phase, status_row, standing)
    if not qualified_host(state, phase) then return {} end
    local rows = {}
    if equipment then
        rows[#rows + 1] = status_row("native-guest-melee-equip", map, equipment.pawn, equipment.guid,
            equipment.ok and 1 or 0, equipment.status)
    end
    if not latest then return rows end
    local pawn = exact_guest(state, phase, latest.pawn, standing)
    local invalid = not pawn or pawn:GetAddress() ~= latest.address
    read(latest, invalid and latest.proof.status == "pending")
    local proof = latest.proof
    rows[#rows + 1] = status_row("native-guest-melee-canary", map, latest.pawn, latest.guid, proof.serial, proof.status,
        proof.presses, proof.releases, proof.contacts, proof.qualified, proof.settled and 1 or 0, proof.passives and 1 or 0)
    return rows
end

return melee
