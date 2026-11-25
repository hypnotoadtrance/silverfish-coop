local guest_save = {}
local heartbeat = require("native_event_heartbeat")
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
local max_guests = 4
local max_int32 = 2147483647
local xp_flag = "Player_Xp"
local heartbeat_path
local clear_death_scope
local bindings = {}
local retained = {}
local slot_owners = {}
local collection_probe

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
    return heartbeat.fresh(heartbeat_path)
end

local function controlled(pawn, controller, world, local_controller)
    return valid(pawn) and valid(controller) and pawn:HasAuthority()
        and controller:IsLocalController() == local_controller
        and same(controller.Pawn, pawn) and same(controller:GetWorld(), world)
        and same(pawn:GetWorld(), world)
end

local function participants(state, phase)
    local route = zones[state.map]
    if phase ~= "hosting" or not route or (route.entrance and not authored_routes) or state.clients > max_guests
        or state.server or not state.listening
        or not valid(state.world) then return end

    local host = state.local_pawn
    if not host or not same(state.controller, host.controller_object)
        or not controlled(host.pawn, host.controller_object, state.world, true) then return end
    local guests, pawns, controllers = {}, {}, {}
    local count = 0
    for _, guest in ipairs(state.pawns) do
        if guest.kind == "guest" then
            count = count + 1
            if not same(host.pawn, guest.pawn) and controlled(guest.pawn, guest.controller_object, state.world, false) then
                local pawn_address = guest.pawn:GetAddress()
                local controller_address = guest.controller_object:GetAddress()
                if guests[guest.name] or pawns[pawn_address] or controllers[controller_address] then return end
                guests[guest.name], pawns[pawn_address], controllers[controller_address] = guest, true, true
            end
        end
    end
    if count > state.clients then return end
    return host, guests
end

local function sorted_names()
    local names = {}
    for name in pairs(bindings) do names[#names + 1] = name end
    table.sort(names)
    return names
end

local function stock_name(value)
    return type(value) == "string" and #value > 0 and #value <= 512
        and not value:find("[%c]") and not value:find("Default__", 1, true)
end

local function character_uuid(value)
    if type(value) ~= "string" or #value ~= 36 then return false end
    local first, second, third, fourth, fifth = value:match(
        "^([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)%-([0-9a-f]+)$")
    return first and #first == 8 and #second == 4 and #third == 4 and #fourth == 4 and #fifth == 12
end

local function exact_connection(world, pawn, controller, controller_name, connection_name)
    local driver = world.NetDriver
    local connection = controller.Player
    local connection_class = StaticFindObject("/Script/Engine.NetConnection")
    if not valid(driver) or not same(driver.World, world) or valid(driver.ServerConnection)
        or not valid(connection) or not valid(connection_class) or not connection:IsA(connection_class)
        or controller:GetFullName() ~= controller_name or not controller:HasAuthority()
        or controller:IsActorBeingDestroyed() or pawn:IsActorBeingDestroyed() or not same(pawn.Controller, controller)
        or not same(connection.Driver, driver) or connection:GetFullName() ~= connection_name
        or not same(connection.PlayerController, controller) or not same(connection.OwningActor, controller)
        or (valid(controller.NetConnection) and not same(controller.NetConnection, connection)) then return end

    local connections = driver.ClientConnections
    local count = math.tointeger(connections:GetArrayNum())
    if not count or count < 1 or count > max_guests then return end
    local found = false
    for index = 1, count do
        local current = connections[index]
        if valid(current) and current:GetFullName() == connection_name then
            if found or not same(current, connection) then return end
            found = true
        end
    end
    if found then return driver, connection end
end

local function managed_save(save, except)
    for _, binding in pairs(bindings) do
        if binding ~= except and same(save, binding.detached) then return true end
    end
    return false
end

local function matches(binding, pawn)
    if not binding or binding.revoked or not (same(pawn, binding.pawn)
        and pawn:GetFullName() == binding.name
        and controlled(pawn, binding.controller, binding.world, false)
        and controlled(binding.host, binding.host_controller, binding.world, true)
        and same(pawn.Save, binding.detached) and valid(binding.owner)
        and same(binding.owner.SaveSlot_Ref, binding.stock)
        and same(binding.host.Save, binding.stock) and not managed_save(binding.detached, binding)) then return false end
    if not binding.character then return true end
    local driver, connection = exact_connection(binding.world, pawn, binding.controller,
        binding.controller_name, binding.connection_name)
    return same(driver, binding.driver) and same(connection, binding.connection)
end

function guest_save.active()
    return next(bindings) ~= nil
end

function guest_save.matches(pawn)
    return valid(pawn) and matches(bindings[pawn:GetFullName()], pawn)
end

local function clear_binding(binding)
    if not binding then return false end
    binding.revoked = true
    if not clear_death_scope(binding.name) then
        error("Could not revoke native death scope before guest teardown", 0)
    end

    local pawn = binding.pawn
    local current = valid(pawn) and pawn.Save or nil
    if managed_save(current, binding) then error("The guest is using a different managed save", 0) end
    if same(current, binding.detached) then
        for _, other in pairs(bindings) do
            if other ~= binding and valid(other.pawn) and same(other.pawn.Save, binding.detached) then
                error("The detached save is also in use by another guest", 0)
            end
        end
        local statics = StaticFindObject(statics_path)
        local owner = valid(statics) and statics:GetGameInstance(pawn) or nil
        local stock = valid(owner) and owner.SaveSlot_Ref or nil
        local controller = pawn.Controller
        if pawn:GetFullName() ~= binding.name or (valid(controller) and not same(controller, binding.controller))
            or not same(owner, binding.owner) or not same(pawn:GetWorld(), binding.world)
            or not valid(stock) or managed_save(stock)
            or (not same(stock, binding.stock)
                and not same(valid(binding.host) and binding.host.Save or nil, stock)) then
            error("Could not verify the current stock save for guest teardown", 0)
        end
        pawn.Save = stock
        if not same(pawn.Save, stock) then
            error("Could not restore the guest's stock save binding", 0)
        end
    end

    bindings[binding.name] = nil
    return true
end

local function release_retained(character)
    local binding = retained[character]
    if not binding then return end
    if valid(binding.owner) and valid(binding.detached) then
        local stock = binding.owner.SaveSlot_Ref
        if not valid(stock) or not SilverfishReferenceNativeDetachedSave(
            stock:GetAddress(), binding.detached:GetAddress(), false) then
            error("Could not release the retained co-op guest save", 0)
        end
    end
    retained[character] = nil
end

function guest_save.clear_bindings()
    local changed, failure = false, nil
    for _, candidate in ipairs(sorted_names()) do
        local cleared, result = pcall(clear_binding, bindings[candidate])
        if cleared then changed = result or changed else failure = failure or result end
    end
    if failure then error(failure, 0) end
    return changed
end

function guest_save.clear(name)
    if name then return clear_binding(bindings[name]) end
    local changed = guest_save.clear_bindings()
    for character in pairs(retained) do release_retained(character) end
    return changed
end

local function verified_binding(binding, state, host, guest)
    if not guest or state.map ~= binding.map or not same(state.world, binding.world)
        or not same(host.pawn, binding.host) or not same(host.controller_object, binding.host_controller)
        or not same(guest.controller_object, binding.controller) or not matches(binding, guest.pawn) then return false end
    for _, other in ipairs(state.pawns) do
        if not same(other.pawn, binding.pawn) and valid(other.pawn) and same(other.pawn.Save, binding.detached) then
            return false
        end
    end
    return true
end

local function live_binding(binding, state, host, guest, fresh, now)
    if not verified_binding(binding, state, host, guest) then return false end
    if fresh then binding.lease_seen = true end
    return fresh or (not binding.lease_seen and now - binding.created <= initial_lease_seconds)
end

function guest_save.sync(state, phase)
    if not guest_save.active() then return false end
    local host, guests = participants(state, phase)
    local fresh = heartbeat_fresh()
    local changed = false
    for _, name in ipairs(sorted_names()) do
        local binding = bindings[name]
        -- A momentarily late coordinator must not attach this player to the host inventory.
        local checked, live = pcall(verified_binding, binding, state, host, guests and guests[name])
        if checked and live and fresh then binding.lease_seen = true end
        if not checked or not live then
            local was_revoked = binding.revoked
            local cleared, removed = pcall(clear_binding, binding)
            if not cleared then binding.revoked = true end
            changed = changed or not was_revoked or (cleared and removed)
        end
    end
    return changed
end

local function exact_guest(state, phase, name, legacy)
    local host, guests = participants(state, phase)
    local guest = guests and guests[name]
    if not guest or not stock_name(name) or guest.pawn:GetFullName() ~= name then
        error("An exact authoritative co-op guest is required", 0)
    end
    if legacy and (state.clients ~= 1 or #state.pawns ~= 2) then
        error("Multiple guests require a verified native connection binding", 0)
    end
    return host, guest
end

local function stock_save_objects()
    local statics, class = StaticFindObject(statics_path), StaticFindObject(save_class_path)
    if not valid(statics) or not valid(class) then error("The native guest save class is unavailable", 0) end
    return statics, class
end

function guest_save.exact_item_context(state, phase, name)
    if not enabled then return end
    local host, guest = exact_guest(state, phase, name)
    local binding = bindings[name]
    if not binding or not binding.character or
        not live_binding(binding, state, host, guest, heartbeat_fresh(), os.time()) then return end
    return guest.pawn, binding.detached, binding.character
end

function guest_save.retain(state, phase, name)
    if type(SilverfishReferenceNativeDetachedSave) ~= "function" then return false end
    local pawn, detached, character = guest_save.exact_item_context(state, phase, name)
    if not pawn then return false end
    local binding = bindings[name]
    if retained[character] then return retained[character] == binding end
    if not SilverfishReferenceNativeDetachedSave(binding.stock:GetAddress(), detached:GetAddress(), true) then return false end
    retained[character] = binding
    return true, character
end

function guest_save.cancel_retention(character)
    if character then release_retained(character) end
end

local function flag_value(flags, key)
    if not valid(flags) or flags:type() ~= "TMap" then error("guest_quest_xp_flags_unavailable", 0) end
    local found = flags:Contains(key)
    if found == false then return 0 end
    if found ~= true then error("guest_quest_xp_flags_unavailable", 0) end
    -- UE4SS v4.0.0 Find returns a RemoteUnrealParam and errors for absent keys.
    local entry = flags:Find(key)
    local value = entry:Get()
    if type(value) ~= "number" or not math.tointeger(value) or value < -2147483648 or value > max_int32 then
        error("guest_quest_xp_value_unqualified", 0)
    end
    return math.tointeger(value), entry
end

function guest_save.add_intoxication(pawn, detached, stock, amount)
    if not guest_save.matches(pawn) or not same(pawn.Save, detached) or same(detached, stock) then return end
    local key = FName("Player_Intox", EFindName.FNAME_Find)
    if key:ToString() ~= "Player_Intox" then return end
    local flags = detached.Flags
    local before, entry = flag_value(flags, key)
    local after = math.min(math.max(before + amount, 0), 100)
    if entry then entry:Set(after) else flags:Add(key, after) end
    pawn.Intoxication = after
end

function guest_save.quest_xp(state, phase, pawn_full_name, delta)
    if not enabled then error("guest_quest_xp_disabled", 0) end
    if type(delta) ~= "number" or not math.tointeger(delta) or delta < 0 or delta > max_int32 then
        error("guest_quest_xp_delta_out_of_range", 0)
    end
    delta = math.tointeger(delta)
    local host, guest = exact_guest(state, phase, pawn_full_name)
    local binding = bindings[pawn_full_name]
    if not binding or not live_binding(binding, state, host, guest, heartbeat_fresh(), os.time()) then
        error("guest_quest_xp_binding_unavailable", 0)
    end
    local _, class = stock_save_objects()
    if not exact_save(binding.detached, class) or same(binding.detached, binding.stock) then
        error("guest_quest_xp_save_not_detached", 0)
    end
    local key = FName(xp_flag, EFindName.FNAME_Find)
    if key:GetComparisonIndex() == 0 or key:ToString() ~= xp_flag then
        error("guest_quest_xp_flag_unavailable", 0)
    end
    local flags = binding.detached.Flags
    local before, entry = flag_value(flags, key)
    if before > max_int32 - delta then error("guest_quest_xp_balance_out_of_range", 0) end
    local after = before + delta
    if delta > 0 then
        local host_before = flag_value(binding.stock.Flags, key)
        if entry then entry:Set(after) else flags:Add(key, after) end
        local observed = flag_value(binding.detached.Flags, key)
        if observed ~= after or not guest_save.matches(guest.pawn) or flag_value(binding.stock.Flags, key) ~= host_before then
            error("guest_quest_xp_write_unverified", 0)
        end
    end
    return { pawn = pawn_full_name, map = state.map, before = before, after = after }
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

local function bind(state, phase, name, raw_health, slot, proof)
    local host, guest = exact_guest(state, phase, name, not proof)
    local driver, connection, character
    if proof then
        if not stock_name(proof.controller) or not stock_name(proof.connection) or not character_uuid(proof.character) then
            error("An exact native controller, connection, and character identifier are required", 0)
        end
        driver, connection = exact_connection(state.world, guest.pawn, guest.controller_object,
            proof.controller, proof.connection)
        if not driver then error("The guest's exact authoritative connection could not be verified", 0) end
        character = proof.character
    end
    local maximum = guest.pawn.MaxHealth
    if not finite(maximum) or maximum <= 0 then error("Native guest maximum health is unavailable", 0) end
    if slot and not generation_slot(slot) then error("An exact co-op guest save generation is required", 0) end
    local override = raw_health ~= nil and raw_health ~= "-"
    local health = tonumber(raw_health)
    if override and (not finite(health) or health < 0 or health > maximum) then
        error("Saved guest health is outside the pawn's native range", 0)
    end
    local existing = bindings[name]
    if existing then
        if matches(existing, guest.pawn) and existing.character == character
            and (not slot or existing.restore_slot == slot) then
            return "native guest save is already bound"
        end
        error("The existing native guest save must be cleared first", 0)
    end
    if slot and slot_owners[slot] and slot_owners[slot] ~= character then
        error("The native guest save generation belongs to a different character", 0)
    end
    local previous = retained[character]
    local count = 0
    for _, other in pairs(bindings) do
        count = count + 1
        if character and other.character == character then
            error("The character already has a managed native save", 0)
        end
        if slot and (other.restore_slot == slot or (other.frame and other.frame.slot == slot)) then
            error("The native guest save generation is in use by a different pawn", 0)
        end
    end
    if count >= max_guests then error("Native guest save capacity reached", 0) end
    if vitals.active() or type(SilverfishNativeDeathReady) ~= "function" or not SilverfishNativeDeathReady()
        or type(SilverfishClearNativeDeathScope) ~= "function" then
        error("Guest save binding requires a ready scoped native bridge", 0)
    end

    local statics, class = stock_save_objects()
    local owner = statics:GetGameInstance(host.pawn)
    local stock = valid(owner) and owner.SaveSlot_Ref or nil
    if not valid(owner) or not same(owner:GetClass(), StaticFindObject(instance_path)) or not exact_save(stock, class)
        or managed_save(stock)
        or not same(host.pawn.Save, stock) or not same(guest.pawn.Save, stock)
        or not same(statics:GetGameInstance(guest.pawn), owner) then
        error("The active host and guest must share the canonical stock save before detaching", 0)
    end

    local detached, backpack, equipment
    if previous then
        detached = previous.detached
        if not (same(previous.world, state.world) and same(previous.host, host.pawn)
            and same(previous.owner, owner) and same(previous.stock, stock)) then
            error("The retained guest save belongs to a different live campaign", 0)
        end
        if not exact_save(detached, class) or same(detached, stock) or not finite(detached.PlayerHealth) then
            error("The retained guest save is unavailable", 0)
        end
        health = math.min(maximum, detached.PlayerHealth)
    else
        detached, backpack, equipment = detached_save(statics, class, stock, slot)
        if slot and not finite(detached.PlayerHealth) then error("The native guest save generation has invalid health", 0) end
        if not override then health = slot and math.min(maximum, detached.PlayerHealth) or maximum end
    end
    if managed_save(detached) then error("The detached native save is already in use", 0) end
    if not previous and proof and proof.appearance then
        require("native_avatar_character_view").install(detached.Character, proof.appearance)
        if proof.profile then
            require("native_guest_creator").install(detached, proof.profile, proof.name, guest.pawn)
        end
    end
    local was_downed = health <= 0 or previous and previous.was_downed
    -- Keep a restored downed pawn above stock death before scope is ready.
    if was_downed then health = math.min(1, maximum) end
    detached.PlayerHealth = health
    if detached.PlayerHealth ~= health then error("Could not restore saved guest health", 0) end

    local binding = {
        pawn = guest.pawn, controller = guest.controller_object, name = name,
        character = character, driver = driver, connection = connection,
        controller_name = proof and proof.controller or nil, connection_name = proof and proof.connection or nil,
        host = host.pawn, host_controller = host.controller_object,
        world = state.world, map = state.map, owner = owner, stock = stock, detached = detached,
        created = os.time(), lease_seen = heartbeat_fresh(), was_downed = was_downed,
        restore_slot = slot,
        rebound = previous ~= nil,
        frame = not previous and slot and { kind = "restored", slot = slot, backpack = backpack, equipment = equipment } or nil,
    }
    bindings[name] = binding
    guest.pawn.Save = detached
    if not matches(binding, guest.pawn) then
        guest_save.clear(name)
        error("Could not verify the detached guest save binding", 0)
    end
    if previous then retained[character] = binding end
    if slot or previous or (proof and proof.profile) then guest.pawn:Compile_VoxSetup() end
    if character and not was_downed then guest.pawn:ApplyDynamicStats() end
    if slot and character then slot_owners[slot] = character end
    return "native guest save bound"
end

local function capture_binding(binding, slot, typed_inventory, still_authoritative)
    if not generation_slot(slot) then error("An exact co-op guest save generation is required", 0) end
    if not still_authoritative() then
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
    if slot_owners[slot] or statics:DoesSaveGameExist(slot, 0) ~= false then
        error("The co-op guest save generation already exists", 0)
    end
    if binding.character then slot_owners[slot] = binding.character end
    if statics:SaveGameToSlot(detached, slot, 0) ~= true then error("Stock native guest save serialization failed", 0) end

    local verified = statics:LoadGameFromSlot(slot, 0)
    if not exact_save(verified, class) or same(verified, detached) or same(verified, binding.stock)
        or managed_save(verified) or not still_authoritative() then
        error("The co-op guest save generation failed stock readback", 0)
    end
    local checked_backpack, checked_equipment = inventory_counts(verified)
    if verified.PlayerHealth ~= health or checked_backpack ~= backpack or checked_equipment ~= equipment then
        error("The co-op guest save summary did not survive stock serialization", 0)
    end
    local verify = SilverfishVerifyNativeStashSave
    if typed_inventory and type(verify) ~= "function"
        or type(verify) == "function" and (typed_inventory or detached.Stashes:GetArrayNum() > 0 or verified.Stashes:GetArrayNum() > 0)
        and verify(detached:GetAddress(), verified:GetAddress()) ~= true then
        error("The detached stash and backpack did not survive stock serialization", 0)
    end
    binding.frame = { kind = "captured", slot = slot, backpack = checked_backpack, equipment = checked_equipment, health = health }
    return "detached stock guest save captured"
end

function guest_save.capture(state, phase, name, slot, typed_inventory)
    if not enabled then error("Native guest saves are disabled", 0) end
    local _, guest = exact_guest(state, phase, name)
    local binding = bindings[name]
    return capture_binding(binding, slot, typed_inventory, function()
        return matches(binding, guest.pawn) and binding.map == state.map and same(binding.world, state.world)
    end)
end

local function capture_retained(state, phase, character, slot)
    local binding = retained[character]
    local host = participants(state, phase)
    return capture_binding(binding, slot, true, function()
        return binding and host and binding.map == state.map and same(binding.world, state.world)
            and same(host.pawn, binding.host) and same(host.controller_object, binding.host_controller)
            and same(host.pawn.Save, binding.stock) and valid(binding.owner)
            and same(binding.owner.SaveSlot_Ref, binding.stock)
            and not managed_save(binding.detached, binding)
    end)
end

local function verify_retention_fixture(state, phase, step)
    local host = participants(state, phase)
    local fixtures = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
        or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
    if not enabled or not host or not fixtures or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1" then
        error("The retained save fixture is unavailable", 0)
    end
    local statics, class = stock_save_objects()
    local stock = host.pawn.Save
    if step == "start" and not collection_probe then
        local collector = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
        local detached, canary = statics:CreateSaveGameObject(class), statics:CreateSaveGameObject(class)
        detached.PlayerHealth = 73
        local function object_path(value) return assert(value:GetFullName():match("^%S+ (.+)$")) end
        local paths = {detached = object_path(detached), canary = object_path(canary)}
        assert(SilverfishReferenceNativeDetachedSave(stock:GetAddress(), detached:GetAddress(), true))
        collection_probe = paths
        collector:CollectGarbage()
        return "requested save collection"
    end
    if step ~= "status" or not collection_probe then error("No running save collection fixture", 0) end
    if valid(StaticFindObject(collection_probe.canary)) then return "save collection pending" end
    local detached = StaticFindObject(collection_probe.detached)
    assert(exact_save(detached, class) and detached.PlayerHealth == 73, "The detached save was lost during collection")
    assert(SilverfishReferenceNativeDetachedSave(stock:GetAddress(), detached:GetAddress(), false))
    collection_probe = nil
    return "detached save retained through collection and released"
end

function guest_save.command(fields, state, phase)
    local command = fields[2]
    if command == "native-guest-save-collection-fixture" and #fields == 3 then
        return verify_retention_fixture(state, phase, fields[3])
    end
    if command ~= "native-guest-bind" and command ~= "native-guest-bind-exact" and command ~= "native-guest-clear"
        and command ~= "native-guest-save-capture" and command ~= "native-guest-save-capture-retained"
        and command ~= "native-guest-save-release-retained" and command ~= "native-guest-save-retain" then return end
    if not enabled then error("Native guest saves are disabled", 0) end
    if command == "native-guest-clear" then
        if #fields > 3 or (#fields == 3 and not stock_name(fields[3])) then
            error("Expected native-guest-clear with an optional exact pawn name", 0)
        end
        guest_save.clear(fields[3])
        return "native guest save cleared"
    end
    if command == "native-guest-save-capture" then
        if #fields ~= 4 then error("Expected the exact guest pawn name and generation slot", 0) end
        return guest_save.capture(state, phase, fields[3], fields[4])
    end
    if command == "native-guest-save-capture-retained" then
        if #fields ~= 4 or not character_uuid(fields[3]) then error("Expected the retained guest and generation slot", 0) end
        return capture_retained(state, phase, fields[3], fields[4])
    end
    if command == "native-guest-save-retain" then
        local binding = bindings[fields[3]]
        if #fields ~= 4 or not binding or binding.character ~= fields[4]
            or not guest_save.retain(state, phase, fields[3]) then
            error("The exact guest inventory could not be retained", 0)
        end
        return "retained exact co-op guest inventory"
    end
    if command == "native-guest-save-release-retained" then
        if phase ~= "hosting" or #fields < 4 or #fields > 10 or #fields % 2 ~= 0 then
            error("Expected the committed retained guest generations", 0)
        end
        for index = 3, #fields, 2 do
            local character, slot = fields[index], fields[index + 1]
            local binding = retained[character]
            if not character_uuid(character) or not generation_slot(slot)
                or binding and (not binding.frame or binding.frame.slot ~= slot or binding.frame.kind ~= "captured") then
                error("The retained guest save does not match the committed generation", 0)
            end
        end
        for index = 3, #fields, 2 do release_retained(fields[index]) end
        return "committed retained guest saves released"
    end
    if command == "native-guest-bind-exact" then
        if #fields < 7 or (#fields > 8 and ((#fields ~= 18 and #fields ~= 20) or fields[8] ~= "-")) then
            error("Expected exact guest identity, health, and optional slot or stock appearance", 0)
        end
        local appearance = #fields >= 18 and table.concat(fields, "|", 9, 18) or nil
        local slot = fields[8] ~= "-" and fields[8] or nil
        return bind(state, phase, fields[3], fields[7], slot,
            {controller = fields[4], connection = fields[5], character = fields[6], appearance = appearance,
                profile = fields[19], name = fields[20]})
    end
    if #fields < 3 or #fields > 5 then
        error("Expected the exact guest pawn name with optional saved health and generation slot", 0)
    end
    return bind(state, phase, fields[3], fields[4], fields[5])
end

function guest_save.project(state, phase, next_state, name)
    if not guest_save.active() then return end
    local _, guests = participants(state, phase)
    if not guests then return end
    if not name and state.clients == 1 then name = next(guests) end
    local guest, binding = guests[name], bindings[name]
    if not guest or not matches(binding, guest.pawn) then return end
    if next_state == "downed" then
        binding.was_downed = true
        binding.revive = nil
    elseif binding.was_downed then
        local maximum = guest.pawn.MaxHealth
        if not finite(maximum) or maximum <= 0 then error("Guest revive health is unavailable", 0) end
        local health = math.min(maximum, math.max(1, maximum * 0.10))
        binding.detached.PlayerHealth = health
        if binding.detached.PlayerHealth ~= health then error("Guest revive health write failed", 0) end
        binding.was_downed = false
        guest.pawn:ForceNetUpdate()
        binding.revive = {health = health, maximum = maximum}
    end
end

function guest_save.restored(pawn)
    if guest_save.matches(pawn) then bindings[pawn:GetFullName()].was_downed = false end
end

local function save_row(status_row, kind, binding)
    return status_row(kind, binding.revoked and "blocked" or "bound", binding.name)
end

local function frame_row(status_row, kind, binding)
    local frame = binding.frame
    if not frame or not matches(binding, binding.pawn) then return end
    return status_row(kind, frame.kind, frame.slot, binding.map, binding.name, frame.backpack, frame.equipment)
end

function guest_save.row(status_row)
    if not enabled then return end
    local name = next(bindings)
    if not name then return status_row("native-guest-save", "stock") end
    if next(bindings, name) then return status_row("native-guest-save", "multiple") end
    local binding = bindings[name]
    if binding.revoked then return status_row("native-guest-save", "blocked") end
    return save_row(status_row, "native-guest-save", binding)
end

function guest_save.frame_row(status_row)
    local name = next(bindings)
    if name and not next(bindings, name) then
        return frame_row(status_row, "native-guest-save-frame", bindings[name])
    end
end

function guest_save.rows(status_row)
    local rows = {}
    for _, name in ipairs(sorted_names()) do
        local binding = bindings[name]
        rows[#rows + 1] = save_row(status_row, "native-guest-save-member", binding)
        local frame = frame_row(status_row, "native-guest-save-member-frame", binding)
        if frame then rows[#rows + 1] = frame end
        if binding.rebound and not binding.revoked then
            rows[#rows + 1] = status_row("native-guest-save-rebound", binding.character, binding.name)
        end
        if binding.revive and not binding.revoked then
            rows[#rows + 1] = status_row("native-guest-revive", binding.map, binding.name, binding.character or "-",
                binding.revive.health, binding.revive.maximum)
        end
    end
    for character, binding in pairs(retained) do
        local frame = binding.frame
        if frame and frame.kind == "captured" then
            rows[#rows + 1] = status_row("native-guest-save-retained-frame", character, frame.slot, binding.map,
                binding.name, frame.health)
        end
    end
    return rows
end

function guest_save.start(bridge, clear_scope)
    if not enabled then return end
    heartbeat_path = bridge .. "/native-events-heartbeat"
    clear_death_scope = clear_scope
end

return guest_save
