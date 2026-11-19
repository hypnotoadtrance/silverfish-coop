local guest_save = {}
local vitals = require("vitals")
local zones = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_class_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local initial_lease_seconds = 3
local heartbeat_path
local clear_death_scope
local binding

local function valid(object)
    return object ~= nil and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function heartbeat_fresh()
    local file = io.open(heartbeat_path, "rb")
    if not file then return false end
    local contents = file:read(32)
    file:close()
    local timestamp = tonumber(contents and contents:match("^([0-9]+)%s*$"))
    if not timestamp then return false end
    local age = os.time() - timestamp
    return age >= 0 and age <= 2
end

local function controlled(pawn, controller, world, local_controller)
    return valid(pawn) and valid(controller) and pawn:HasAuthority()
        and controller:IsLocalController() == local_controller
        and same(controller.Pawn, pawn) and same(controller:GetWorld(), world)
        and same(pawn:GetWorld(), world)
end

local function participants(state, phase)
    local route = zones[state.map]
    if phase ~= "hosting" or not route or (route.entrance and not authored_routes) or state.clients ~= 1
        or state.server or not state.listening or #state.pawns ~= 2
        or not valid(state.world) then return end

    local host = state.local_pawn
    if not host or not same(state.controller, host.controller_object)
        or not controlled(host.pawn, host.controller_object, state.world, true) then return end
    for _, guest in ipairs(state.pawns) do
        if guest.kind == "guest" and not same(host.pawn, guest.pawn)
            and controlled(guest.pawn, guest.controller_object, state.world, false) then
            return host, guest
        end
    end
end

function guest_save.active()
    return binding ~= nil
end

function guest_save.matches(pawn)
    return binding ~= nil and not binding.revoked and same(pawn, binding.pawn)
        and pawn:GetFullName() == binding.name
        and controlled(pawn, binding.controller, binding.world, false)
        and controlled(binding.host, binding.host_controller, binding.world, true)
        and same(pawn.Save, binding.detached) and valid(binding.owner)
        and same(binding.owner.SaveSlot_Ref, binding.stock)
        and same(binding.host.Save, binding.stock)
end

function guest_save.clear()
    if not binding then return false end
    binding.revoked = true
    if not clear_death_scope() then
        error("Could not revoke native death scope before guest teardown", 0)
    end

    local pawn = binding.pawn
    if same(valid(pawn) and pawn.Save or nil, binding.detached) then
        local statics = StaticFindObject(statics_path)
        local owner = valid(statics) and statics:GetGameInstance(pawn) or nil
        local stock = valid(owner) and owner.SaveSlot_Ref or nil
        if not same(owner, binding.owner) or not same(pawn:GetWorld(), binding.world)
            or not valid(stock) or same(stock, binding.detached)
            or (not same(stock, binding.stock)
                and not same(valid(binding.host) and binding.host.Save or nil, stock)) then
            error("Could not verify the current stock save for guest teardown", 0)
        end
        pawn.Save = stock
        if not same(pawn.Save, stock) then
            error("Could not restore the guest's stock save binding", 0)
        end
    end

    binding = nil
    return true
end

function guest_save.sync(state, phase)
    if not binding then return false end
    local host, guest = participants(state, phase)
    if not guest or state.map ~= binding.map or not same(state.world, binding.world)
        or not same(host.pawn, binding.host) or not same(host.controller_object, binding.host_controller)
        or not same(guest.controller_object, binding.controller) or not guest_save.matches(guest.pawn) then
        return guest_save.clear()
    end

    if heartbeat_fresh() then
        binding.lease_seen = true
    elseif binding.lease_seen or os.time() - binding.created > initial_lease_seconds then
        return guest_save.clear()
    end
    return false
end

local function bind(state, phase, name, raw_health)
    local host, guest = participants(state, phase)
    if not guest or guest.name ~= name or guest.pawn:GetFullName() ~= name then
        error("An exact authoritative playtest guest is required", 0)
    end
    local maximum = guest.pawn.MaxHealth
    local restore_health = raw_health ~= nil and raw_health ~= "-"
    local health = tonumber(raw_health)
    if restore_health and (not finite(health) or not finite(maximum) or health < 0 or health > maximum) then
        error("Saved guest health is outside the pawn's native range", 0)
    end
    if binding then
        if guest_save.matches(guest.pawn) then return "native guest save is already bound" end
        error("The existing native guest save must be cleared first", 0)
    end
    if vitals.active() or type(SilverfishCopyNativeGuestSave) ~= "function"
        or type(SilverfishNativeDeathReady) ~= "function" or not SilverfishNativeDeathReady()
        or type(SilverfishClearNativeDeathScope) ~= "function" then
        error("Guest save binding requires a ready scoped native bridge", 0)
    end

    local statics, class = StaticFindObject(statics_path), StaticFindObject(save_class_path)
    if not valid(statics) or not valid(class) then error("Native save allocation is unavailable", 0) end
    local owner = statics:GetGameInstance(host.pawn)
    local stock = valid(owner) and owner.SaveSlot_Ref or nil
    if not same(host.pawn.Save, stock) or not same(guest.pawn.Save, stock)
        or not same(statics:GetGameInstance(guest.pawn), owner) then
        error("The active host and guest must share the canonical stock save", 0)
    end
    if not finite(stock.PlayerHealth) or stock.PlayerHealth <= 0 or not finite(maximum) or maximum <= 0 then
        error("The stock guest needs positive finite vitals before detaching", 0)
    end

    local detached = statics:CreateSaveGameObject(class)
    if not valid(detached) or same(detached, stock) then error("Native save allocation failed", 0) end
    local copied, detail = SilverfishCopyNativeGuestSave(stock:GetAddress(), detached:GetAddress())
    if not copied or detached.PlayerHealth ~= stock.PlayerHealth then
        error("Native guest save copy failed (" .. tostring(detail) .. ")", 0)
    end
    local was_downed = restore_health and health == 0
    if restore_health then
        -- Keep a restored downed pawn above stock death before scope is ready.
        if was_downed then health = math.min(1, maximum) end
        detached.PlayerHealth = health
        if detached.PlayerHealth ~= health then error("Could not restore saved guest health", 0) end
    end

    binding = {
        pawn = guest.pawn, controller = guest.controller_object, name = name,
        host = host.pawn, host_controller = host.controller_object,
        world = state.world, map = state.map, owner = owner, stock = stock, detached = detached,
        created = os.time(), lease_seen = heartbeat_fresh(), was_downed = was_downed,
    }
    guest.pawn.Save = detached
    if not guest_save.matches(guest.pawn) then
        guest_save.clear()
        error("Could not verify the detached guest save binding", 0)
    end
    return "native guest save bound"
end

function guest_save.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-guest-bind" and command ~= "native-guest-clear" then return end
    if not enabled then error("Native guest save fixtures are disabled", 0) end
    if command == "native-guest-clear" then
        if #fields ~= 2 then error("Expected native-guest-clear", 0) end
        guest_save.clear()
        return "native guest save cleared"
    end
    if #fields ~= 3 and #fields ~= 4 then
        error("Expected the exact guest pawn name and optional saved health", 0)
    end
    return bind(state, phase, fields[3], fields[4])
end

function guest_save.project(state, phase, next_state)
    if not binding then return end
    local _, guest = participants(state, phase)
    if not guest or not guest_save.matches(guest.pawn) then return end
    if next_state == "downed" then
        binding.was_downed = true
    elseif binding.was_downed then
        local maximum = guest.pawn.MaxHealth
        if not finite(maximum) or maximum <= 0 then error("Guest revive health is unavailable", 0) end
        local health = math.min(maximum, math.max(1, maximum * 0.10))
        binding.detached.PlayerHealth = health
        if binding.detached.PlayerHealth ~= health then error("Guest revive health write failed", 0) end
        binding.was_downed = false
        guest.pawn:ForceNetUpdate()
    end
end

function guest_save.restored(pawn)
    if guest_save.matches(pawn) then binding.was_downed = false end
end

function guest_save.row(status_row)
    if not enabled then return end
    if binding and not binding.revoked then
        return status_row("native-guest-save", "bound", binding.name)
    end
    return status_row("native-guest-save", binding and "blocked" or "stock")
end

function guest_save.start(bridge, clear_scope)
    if not enabled then return end
    heartbeat_path = bridge .. "/native-events-heartbeat"
    clear_death_scope = clear_scope
end

return guest_save
