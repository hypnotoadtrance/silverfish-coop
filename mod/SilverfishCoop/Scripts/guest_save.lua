local guest_save = {}
local vitals = require("vitals")
local zones = require("zone_maps")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_DEATH") == "cpp"
    and os.getenv("SILVERFISH_COOP_NATIVE_EVENTS") == "1"
local authored_routes = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local save_class_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local initial_lease_seconds = 3
local heartbeat_path
local clear_death_scope
local binding

local function valid(object)
    return object ~= nil and object ~= false and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function finite(number)
    return type(number) == "number" and number == number and math.abs(number) < math.huge
end

local function exact_save(object, class)
    return valid(object) and not object:GetFullName():find("Default__", 1, true) and same(object:GetClass(), class)
end

local function generation_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function inventory_counts(save)
    local backpack, equipment = save.PlayerBackpack, save.PlayerEquipment
    local backpack_count = backpack and math.tointeger(backpack:GetArrayNum())
    local equipment_count = equipment and math.tointeger(equipment:GetArrayNum())
    if not backpack_count or backpack_count < 0 or not equipment_count or equipment_count < 0 then
        error("The native guest inventory is unavailable", 0)
    end
    return backpack_count, equipment_count
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

local function exact_guest(state, phase, name)
    local host, guest = participants(state, phase)
    if not guest or guest.name ~= name or guest.pawn:GetFullName() ~= name then
        error("An exact authoritative co-op guest is required", 0)
    end
    return host, guest
end

local function stock_save_objects()
    local statics, class = StaticFindObject(statics_path), StaticFindObject(save_class_path)
    if not valid(statics) or not valid(class) then error("The native guest save class is unavailable", 0) end
    return statics, class
end

local function detached_save(statics, class, stock, slot)
    local detached = slot and statics:LoadGameFromSlot(slot, 0) or nil
    if not slot then detached = statics:CreateSaveGameObject(class) end
    if not exact_save(detached, class) or same(detached, stock) then
        error(slot and "The native guest save generation could not be restored" or "Native guest save allocation failed", 0)
    end
    local backpack, equipment = inventory_counts(detached)
    if not slot then
        local defaults = class:GetCDO()
        if not valid(defaults) or not same(defaults:GetClass(), class) then
            error("The stock guest save defaults are unavailable", 0)
        end
        local stock_backpack, stock_equipment = inventory_counts(defaults)
        if backpack ~= stock_backpack or equipment ~= stock_equipment then
            error("Fresh native guest inventory does not match the stock defaults", 0)
        end
    end
    return detached, backpack, equipment
end

local function bind(state, phase, name, raw_health, slot)
    local host, guest = exact_guest(state, phase, name)
    local maximum = guest.pawn.MaxHealth
    if not finite(maximum) or maximum <= 0 then error("Native guest maximum health is unavailable", 0) end
    if slot and not generation_slot(slot) then error("An exact co-op guest save generation is required", 0) end
    local override = raw_health ~= nil and raw_health ~= "-"
    local health = tonumber(raw_health)
    if override and (not finite(health) or health < 0 or health > maximum) then
        error("Saved guest health is outside the pawn's native range", 0)
    end
    if binding then
        local prior = binding.frame
        if guest_save.matches(guest.pawn) and (not slot or (prior and prior.kind == "restored" and prior.slot == slot)) then
            return "native guest save is already bound"
        end
        error("The existing native guest save must be cleared first", 0)
    end
    if vitals.active() or type(SilverfishNativeDeathReady) ~= "function" or not SilverfishNativeDeathReady()
        or type(SilverfishClearNativeDeathScope) ~= "function" then
        error("Guest save binding requires a ready scoped native bridge", 0)
    end

    local statics, class = stock_save_objects()
    local owner = statics:GetGameInstance(host.pawn)
    local stock = valid(owner) and owner.SaveSlot_Ref or nil
    if not valid(owner) or not same(owner:GetClass(), StaticFindObject(instance_path)) or not exact_save(stock, class)
        or not same(host.pawn.Save, stock) or not same(guest.pawn.Save, stock)
        or not same(statics:GetGameInstance(guest.pawn), owner) then
        error("The active host and guest must share the canonical stock save before detaching", 0)
    end

    local detached, backpack, equipment = detached_save(statics, class, stock, slot)
    if slot and not finite(detached.PlayerHealth) then error("The native guest save generation has invalid health", 0) end
    if not override then health = slot and math.min(maximum, detached.PlayerHealth) or maximum end
    local was_downed = health <= 0
    -- Keep a restored downed pawn above stock death before scope is ready.
    if was_downed then health = math.min(1, maximum) end
    detached.PlayerHealth = health
    if detached.PlayerHealth ~= health then error("Could not restore saved guest health", 0) end

    binding = {
        pawn = guest.pawn, controller = guest.controller_object, name = name,
        host = host.pawn, host_controller = host.controller_object,
        world = state.world, map = state.map, owner = owner, stock = stock, detached = detached,
        created = os.time(), lease_seen = heartbeat_fresh(), was_downed = was_downed,
        frame = slot and { kind = "restored", slot = slot, backpack = backpack, equipment = equipment } or nil,
    }
    guest.pawn.Save = detached
    if not guest_save.matches(guest.pawn) then
        guest_save.clear()
        error("Could not verify the detached guest save binding", 0)
    end
    return "native guest save bound"
end

local function capture(state, phase, name, slot)
    if not generation_slot(slot) then error("An exact co-op guest save generation is required", 0) end
    local _, guest = exact_guest(state, phase, name)
    if not guest_save.matches(guest.pawn) or binding.map ~= state.map or not same(binding.world, state.world) then
        error("The authoritative guest does not have a bound detached stock save", 0)
    end
    local statics, class = stock_save_objects()
    local detached = binding.detached
    if not exact_save(detached, class) or same(detached, binding.stock) then
        error("The bound native guest save is not isolated", 0)
    end
    local health = detached.PlayerHealth
    if not finite(health) then error("The native guest save health is unavailable", 0) end
    local backpack, equipment = inventory_counts(detached)
    if statics:DoesSaveGameExist(slot, 0) ~= false then error("The co-op guest save generation already exists", 0) end
    if statics:SaveGameToSlot(detached, slot, 0) ~= true then error("Stock native guest save serialization failed", 0) end

    local verified = statics:LoadGameFromSlot(slot, 0)
    if not exact_save(verified, class) or same(verified, detached) or same(verified, binding.stock)
        or not guest_save.matches(guest.pawn) then
        error("The co-op guest save generation failed stock readback", 0)
    end
    local checked_backpack, checked_equipment = inventory_counts(verified)
    if verified.PlayerHealth ~= health or checked_backpack ~= backpack or checked_equipment ~= equipment then
        error("The co-op guest save summary did not survive stock serialization", 0)
    end
    binding.frame = { kind = "captured", slot = slot, backpack = checked_backpack, equipment = checked_equipment }
    return "detached stock guest save captured"
end

function guest_save.command(fields, state, phase)
    local command = fields[2]
    if command ~= "native-guest-bind" and command ~= "native-guest-clear"
        and command ~= "native-guest-save-capture" then return end
    if not enabled then error("Native guest saves are disabled", 0) end
    if command == "native-guest-clear" then
        if #fields ~= 2 then error("Expected native-guest-clear", 0) end
        guest_save.clear()
        return "native guest save cleared"
    end
    if command == "native-guest-save-capture" then
        if #fields ~= 4 then error("Expected the exact guest pawn name and generation slot", 0) end
        return capture(state, phase, fields[3], fields[4])
    end
    if #fields < 3 or #fields > 5 then
        error("Expected the exact guest pawn name with optional saved health and generation slot", 0)
    end
    return bind(state, phase, fields[3], fields[4], fields[5])
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

function guest_save.frame_row(status_row)
    local frame = binding and binding.frame
    if not frame or not guest_save.matches(binding.pawn) then return end
    return status_row("native-guest-save-frame", frame.kind, frame.slot, binding.map, binding.name,
        frame.backpack, frame.equipment)
end

function guest_save.start(bridge, clear_scope)
    if not enabled then return end
    heartbeat_path = bridge .. "/native-events-heartbeat"
    clear_death_scope = clear_scope
end

return guest_save
