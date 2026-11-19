local primary = {}

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_INPUT") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_PRIMARY_FIXTURES") == "1"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local camera_path = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
local camera_map = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
local camera_quest = "Photo_DB_Turrets"
local kinds = { smg = true, ["light-pistol"] = true, ["nagant-scoped"] = true, camera = true }
local event_path, is_downed, binding, fixture_proof

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function exact(object, path)
    return valid(object) and same(object:GetClass(), StaticFindObject(path))
end

local function valid_guid(guid)
    return type(guid) == "string" and #guid == 32 and not guid:find("[^0-9A-F]") and guid:find("[^0]") ~= nil
end

local function valid_sequence(sequence)
    return type(sequence) == "number" and math.tointeger(sequence) and sequence > 0 and sequence < 2 ^ 53
end

local function context(state, phase)
    if not enabled or not event_path or phase ~= "connected" or not state.listening or state.server ~= true
        or not valid(state.world) or type(state.map) ~= "string"
        or not state.map:match("^/Game/Classes/Maps/[%w_/]+$")
        or state.world:GetFullName():match("^World (/[^%.:]+)%.") ~= state.map then return end
    local detail, controller = state.local_pawn, state.controller
    local pawn = detail and detail.pawn
    if not exact(pawn, pawn_path) or not exact(controller, controller_path) or not same(detail.controller_object, controller)
        or pawn:HasAuthority() or not pawn:IsLocallyControlled() or not controller:IsLocalController()
        or not same(pawn.Controller, controller) or not same(controller.Pawn, pawn)
        or not same(pawn:GetWorld(), state.world) or not same(controller:GetWorld(), state.world)
        or pawn:IsActorBeingDestroyed() or controller:IsActorBeingDestroyed()
        or type(detail.name) ~= "string" or #detail.name > 512 or detail.name:find("%c")
        or detail.name ~= pawn:GetFullName() then return end
    return {world = state.world, map = state.map, name = detail.name, pawn = pawn, controller = controller}
end

local function same_context(left, right)
    return left and right and left.map == right.map and left.name == right.name
        and same(left.world, right.world) and same(left.controller, right.controller) and same(left.pawn, right.pawn)
end

local function standing(current)
    return current and current.controller.Loading == false and current.pawn.Dead == false
        and current.pawn.IsWeaponBlocked == false and type(is_downed) == "function" and is_downed(current.pawn) == false
end

local function emit(current, sequence, guid, kind)
    local file = io.open(event_path, "ab")
    if not file then return false end
    local written = file:write(table.concat({"native-guest-primary-intent", sequence, current.map, current.name,
        guid, kind}, "\t"), "\n")
    local closed = file:close()
    return written and closed
end

function primary.reset()
    if binding and type(SilverfishClearNativeGuestPrimaryScope) == "function" then
        SilverfishClearNativeGuestPrimaryScope()
    end
    binding, fixture_proof = nil, nil
end

function primary.sync(state, phase)
    if not binding then return end
    local current = context(state, phase)
    if not same_context(current, binding) or not standing(current) then
        primary.reset()
        return
    end
    local address = current.pawn:GetAddress()
    local emitted = 0
    for _ = 1, 8 do
        local sequence, owner, guid, kind = SilverfishTakeNativeGuestPrimaryIntent(address)
        if not valid_sequence(sequence) or owner ~= address or guid ~= binding.guid or kind ~= binding.kind then break end
        if not emit(current, sequence, guid, kind) then break end
        emitted = emitted + 1
    end
    return emitted
end

function primary.command(fields, state, phase)
    local action = fields[2]
    if action == "native-guest-primary-clear" and #fields == 2 then
        primary.reset()
        return "stock guest primary scope cleared"
    end
    if action ~= "native-guest-primary-scope" and action ~= "native-guest-primary-input-fixture" then return end
    local current = context(state, phase)
    if not standing(current) then
        primary.reset()
        error("Stock primary input requires the exact standing connected guest", 0)
    end
    if action == "native-guest-primary-input-fixture" then
        if not fixtures or #fields ~= 3 or fields[3] ~= current.name or not same_context(current, binding)
            or type(SilverfishProbeNativeGuestPrimaryInput) ~= "function" then
            error("The stock primary input fixture requires the exact scoped headless client pawn", 0)
        end
        local ok, status, sequence, wrappers = SilverfishProbeNativeGuestPrimaryInput(current.pawn:GetAddress())
        if ok ~= true or status ~= "stock_pressed" or not valid_sequence(sequence) or wrappers ~= 2 then
            error("The stock primary input fixture did not verify (" .. tostring(status) .. ")", 0)
        end
        if primary.sync(state, phase) ~= 1 then error("The stock primary fixture intent could not be published", 0) end
        fixture_proof = {context = current, guid = binding.guid, sequence = sequence, wrappers = wrappers}
        return "stock guest primary input verified"
    end

    local guid, kind, quest = fields[5], fields[6], fields[7]
    local camera = kind == "camera"
    if #fields ~= (camera and 7 or 6) or fields[3] ~= current.map or fields[4] ~= current.name or not valid_guid(guid)
        or (camera and (current.map ~= camera_map or quest ~= camera_quest))
        or not kinds[kind] or type(SilverfishSetNativeGuestPrimaryScope) ~= "function"
        or type(SilverfishTakeNativeGuestPrimaryIntent) ~= "function" then
        primary.reset()
        error("The authenticated stock equipment selection is unavailable", 0)
    end
    if binding and (not same_context(current, binding) or binding.guid ~= guid or binding.kind ~= kind) then
        primary.reset()
    end
    local accepted, status
    if camera then
        if not valid(StaticFindObject(camera_path)) then LoadAsset(camera_path) end
        accepted, status = SilverfishSetNativeGuestPrimaryScope(current.pawn:GetAddress(),
            current.controller:GetAddress(), current.world:GetAddress(), guid, kind, quest)
    else
        accepted, status = SilverfishSetNativeGuestPrimaryScope(current.pawn:GetAddress(),
            current.controller:GetAddress(), current.world:GetAddress(), guid, kind)
    end
    if not accepted or (status ~= "stock_held" and status ~= "host_equipped") then
        primary.reset()
        error(tostring(status or "stock_guest_primary_scope_unavailable"), 0)
    end
    current.guid, current.kind, current.mode = guid, kind, status
    binding = current
    return "stock guest primary scope refreshed"
end

function primary.rows(state, phase, status_row)
    local current = context(state, phase)
    if not same_context(current, binding) then return {} end
    local rows = { status_row("native-guest-primary-scope", current.map, current.name, binding.guid,
        binding.kind, binding.mode) }
    if fixture_proof and same_context(current, fixture_proof.context) and fixture_proof.guid == binding.guid then
        rows[#rows + 1] = status_row("native-guest-primary-fixture", current.map, current.name, fixture_proof.guid,
            fixture_proof.sequence, fixture_proof.wrappers)
    end
    return rows
end

function primary.start(bridge, downed)
    event_path = enabled and bridge and bridge ~= "" and bridge .. "/native-guest-primary-events" or nil
    is_downed = downed
end

return primary
