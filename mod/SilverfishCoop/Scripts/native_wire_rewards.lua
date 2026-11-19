local rewards = {}
local guest_save = require("guest_save")
local zones = require("zone_maps")

local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local instance_path = "/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C"
local pawn_path = "/Game/Classes/Player/Player_FP.Player_FP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local expanded_zones = os.getenv("SILVERFISH_COOP_NATIVE_EXPANDED_ZONES") == "1"

local function valid(value)
    return value ~= nil and value ~= false and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact(value, path)
    return valid(value) and same(value:GetClass(), StaticFindObject(path)) and not value:GetFullName():find("Default__", 1, true)
end

local function require_true(value, reason)
    if not value then error(reason, 0) end
    return value
end

local function guid(value)
    return type(value) == "string" and #value == 32 and not value:find("[^0-9A-F]") and value:find("[^0]") ~= nil
end

local function slot_name(value)
    return type(value) == "string" and #value == 39 and value:match("^SFCoop_[0-9a-f]+$") ~= nil
end

local function scope(state, phase, borough_only)
    local map = state.map
    local route = zones[map]
    local leaf = route and map:match("/([^/]+)$")
    require_true(enabled and phase == "hosting" and state.listening and state.server == false and leaf
        and (not route.expanded or expanded_zones) and (not borough_only or map == borough)
        and valid(state.world) and state.world:GetFullName() == "World " .. map .. "." .. leaf, "wire_reward_stock_map_required")
    local host = state.local_pawn and state.local_pawn.pawn
    local controller = state.controller
    require_true(exact(host, pawn_path) and host:HasAuthority() and exact(controller, controller_path)
        and controller:HasAuthority() and controller:IsLocalController() and same(controller.Pawn, host)
        and same(host.Controller, controller) and same(host:GetWorld(), state.world), "wire_reward_canonical_host_required")
    local statics = StaticFindObject(statics_path)
    local save_class = StaticFindObject(save_path)
    local instance = valid(statics) and statics:GetGameInstance(controller)
    local stock = valid(instance) and instance.SaveSlot_Ref
    require_true(exact(instance, instance_path) and exact(stock, save_path) and same(host.Save, stock) and valid(save_class),
        "wire_reward_canonical_save_required")
    return host, stock, statics, save_class, map
end

local function standard_result(ok, detail, fallback)
    if ok == true then return end
    if type(detail) == "string" and (detail:match("^reward_[a-z_]+$") or detail:match("^mutation_unverified_[a-z_]+$")) then
        error(detail, 0)
    end
    error(fallback, 0)
end

local function mags(weapon_mag, ammo_units)
    return math.type(weapon_mag) == "integer" and weapon_mag >= 0 and weapon_mag <= 256 and ammo_units == 14
end

local function loaded_template(statics, slot, weapon_guid, ammo_guid, source)
    require_true(type(SilverfishVerifyWireRewards) == "function", "wire_reward_template_verifier_unavailable")
    local loaded = statics:LoadGameFromSlot(slot, 0)
    require_true(exact(loaded, save_path), "wire_reward_template_load_failed")
    local arguments = {loaded:GetAddress(), weapon_guid, ammo_guid}
    if source then arguments[4] = source:GetAddress() end
    local ok, detail, weapon, ammo, magazine, units = SilverfishVerifyWireRewards(table.unpack(arguments))
    standard_result(ok, detail, "wire_reward_template_verification_failed")
    require_true(detail == "verified" and weapon == weapon_guid and ammo == ammo_guid and mags(magazine, units),
        "wire_reward_template_readback_changed")
    return loaded, magazine, units
end

local function report(status, map, pawn, slot, weapon, ammo, magazine, units)
    return { state = status, map = map, pawn = pawn:GetFullName(), slot = slot,
        weapon_guid = weapon, ammo_guid = ammo, weapon_mag = magazine, ammo_units = units }
end

function rewards.capture(state, phase, slot, weapon_guid)
    require_true(slot_name(slot) and guid(weapon_guid) and type(SilverfishCaptureWireRewards) == "function",
        "wire_reward_capture_scope_invalid")
    local host, stock, statics, class, map = scope(state, phase, true)
    require_true(statics:DoesSaveGameExist(slot, 0) == false, "wire_reward_template_slot_exists")
    local scratch = statics:CreateSaveGameObject(class)
    require_true(exact(scratch, save_path) and not same(scratch, stock), "wire_reward_template_create_failed")
    local ok, detail, weapon, ammo, magazine, units =
        SilverfishCaptureWireRewards(host:GetAddress(), stock:GetAddress(), scratch:GetAddress(), weapon_guid)
    standard_result(ok, detail, "wire_reward_capture_failed")
    require_true(detail == "captured" and weapon == weapon_guid and guid(ammo) and weapon ~= ammo and mags(magazine, units),
        "wire_reward_captured_proof_invalid")
    require_true(statics:SaveGameToSlot(scratch, slot, 0) == true, "wire_reward_template_write_failed")
    local loaded, stored_magazine, stored_units = loaded_template(statics, slot, weapon, ammo, scratch)
    local active_host, active_stock = scope(state, phase, true)
    require_true(not same(loaded, scratch) and same(active_host, host) and same(active_stock, stock)
        and stored_magazine == magazine and stored_units == units, "wire_reward_template_save_readback_changed")
    return report(detail, map, host, slot, weapon, ammo, magazine, units)
end

function rewards.grant(state, phase, guest_name, slot, weapon_template, ammo_template, guest_weapon, guest_ammo)
    require_true(slot_name(slot) and guid(weapon_template) and guid(ammo_template) and weapon_template ~= ammo_template
        and guid(guest_weapon) and guid(guest_ammo) and guest_weapon ~= guest_ammo and type(guest_name) == "string"
        and type(SilverfishGrantWireRewards) == "function", "wire_reward_guest_grant_scope_invalid")
    local host, stock, statics, _, map = scope(state, phase)
    local pawn, save = guest_save.exact_item_context(state, phase, guest_name)
    require_true(exact(pawn, pawn_path) and pawn:GetFullName() == guest_name and exact(save, save_path)
        and same(pawn:GetWorld(), state.world) and same(pawn.Save, save) and not same(save, stock),
        "wire_reward_exact_guest_required")
    local escrow, expected_magazine, expected_units = loaded_template(statics, slot, weapon_template, ammo_template)
    local called, ok, detail, magazine, units = pcall(SilverfishGrantWireRewards, pawn:GetAddress(),
        save:GetAddress(), stock:GetAddress(), escrow:GetAddress(), guest_weapon, guest_ammo)
    if not called then error("mutation_unverified_wire_reward_call", 0) end
    standard_result(ok, detail, "wire_reward_guest_grant_failed")
    local host_verified, active_host, active_stock = pcall(scope, state, phase)
    local guest_verified, active_pawn, active_save = pcall(guest_save.exact_item_context, state, phase, guest_name)
    require_true(host_verified and same(active_host, host) and same(active_stock, stock) and state.map == map
        and guest_verified and same(active_pawn, pawn) and same(active_save, save) and same(pawn:GetWorld(), state.world)
        and (detail == "granted" or detail == "already_granted") and magazine == expected_magazine and units == expected_units,
        "mutation_unverified_wire_guest_reward_proof")
    return report(detail, map, pawn, slot, guest_weapon, guest_ammo, magazine, units)
end

function rewards.row(receipt, status_row)
    return status_row("native-wire-reward", receipt.state, receipt.map, receipt.pawn, receipt.slot,
        receipt.weapon_guid, receipt.ammo_guid, receipt.weapon_mag, receipt.ammo_units)
end

return rewards
