local rewards = {}
local guest_save = require("guest_save")
local loot_escrow = require("native_loot_actions")
local zones = require("zone_maps")

local profiles = {
    wire = {map = "/Game/Classes/Maps/Maps_SafeZones/Borough", units = 14, native = "Wire"},
    meet_broker = {map = "/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01", units = 9, native = "MeetBroker"},
}
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

local function scope(state, phase, required_map)
    local map = state.map
    local route = zones[map]
    local leaf = route and map:match("/([^/]+)$")
    require_true(enabled and phase == "hosting" and state.listening and state.server == false and leaf
        and (not route.expanded or expanded_zones) and (not required_map or map == required_map)
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
    return host, stock, statics, save_class, map, instance
end

local function standard_result(ok, detail, fallback)
    if ok == true then return end
    if type(detail) == "string" and (detail:match("^reward_[a-z_]+$") or detail:match("^mutation_unverified_[a-z_]+$")) then
        error(detail, 0)
    end
    error(fallback, 0)
end

local function mags(weapon_mag, ammo_units, profile)
    return math.type(weapon_mag) == "integer" and weapon_mag >= 0 and weapon_mag <= 256 and ammo_units == profile.units
end

local function verified_template(profile, template, weapon_guid, ammo_guid, source)
    local verify = _G["SilverfishVerify" .. profile.native .. "Rewards"]
    require_true(type(verify) == "function", "wire_reward_template_verifier_unavailable")
    require_true(exact(template, save_path), "wire_reward_template_load_failed")
    local arguments = {template:GetAddress(), weapon_guid, ammo_guid}
    if source then arguments[4] = source:GetAddress() end
    local ok, detail, weapon, ammo, magazine, units = verify(table.unpack(arguments))
    standard_result(ok, detail, "wire_reward_template_verification_failed")
    require_true(detail == "verified" and weapon == weapon_guid and ammo == ammo_guid and mags(magazine, units, profile),
        "wire_reward_template_readback_changed")
    return magazine, units
end

local function report(status, map, pawn, slot, weapon, ammo, magazine, units)
    return { state = status, map = map, pawn = pawn:GetFullName(), slot = slot,
        weapon_guid = weapon, ammo_guid = ammo, weapon_mag = magazine, ammo_units = units }
end

function rewards.capture(state, phase, slot, weapon_guid, kind, memory)
    local profile = profiles[kind or "wire"]
    local capture = profile and _G["SilverfishCapture" .. profile.native .. "Rewards"]
    require_true(slot_name(slot) and guid(weapon_guid) and type(capture) == "function",
        "wire_reward_capture_scope_invalid")
    local host, stock, statics, class, map, instance = scope(state, phase, profile.map)
    local weapon, ammo, magazine, units
    local function verify_roundtrip(source, loaded)
        local stored_magazine, stored_units = verified_template(profile, loaded, weapon, ammo, source)
        return stored_magazine == magazine and stored_units == units
    end
    local scratch
    if memory then
        scratch = loot_escrow.create_pending_escrow(stock, instance, state.world, slot, verify_roundtrip)
    else
        require_true(statics:DoesSaveGameExist(slot, 0) == false, "wire_reward_template_slot_exists")
        scratch = statics:CreateSaveGameObject(class)
    end
    require_true(exact(scratch, save_path) and not same(scratch, stock), "wire_reward_template_create_failed")
    local ok, detail
    ok, detail, weapon, ammo, magazine, units = capture(host:GetAddress(), stock:GetAddress(), scratch:GetAddress(), weapon_guid)
    standard_result(ok, detail, "wire_reward_capture_failed")
    require_true(detail == "captured" and weapon == weapon_guid and guid(ammo) and weapon ~= ammo and mags(magazine, units, profile),
        "wire_reward_captured_proof_invalid")
    if not memory then
        require_true(statics:SaveGameToSlot(scratch, slot, 0) == true, "wire_reward_template_write_failed")
        require_true(verify_roundtrip(scratch, statics:LoadGameFromSlot(slot, 0)), "wire_reward_template_save_readback_changed")
    end
    local active_host, active_stock = scope(state, phase, profile.map)
    require_true(same(active_host, host) and same(active_stock, stock), "wire_reward_template_capture_scope_changed")
    return report(detail, map, host, slot, weapon, ammo, magazine, units)
end

function rewards.grant(state, phase, guest_name, slot, weapon_template, ammo_template, guest_weapon, guest_ammo, kind, memory)
    local profile = profiles[kind or "wire"]
    local grant = profile and _G["SilverfishGrant" .. profile.native .. "Rewards"]
    require_true(slot_name(slot) and guid(weapon_template) and guid(ammo_template) and weapon_template ~= ammo_template
        and guid(guest_weapon) and guid(guest_ammo) and guest_weapon ~= guest_ammo and type(guest_name) == "string"
        and type(grant) == "function", "wire_reward_guest_grant_scope_invalid")
    local host, stock, statics, _, map = scope(state, phase)
    local pawn, save = guest_save.exact_item_context(state, phase, guest_name)
    require_true(exact(pawn, pawn_path) and pawn:GetFullName() == guest_name and exact(save, save_path)
        and same(pawn:GetWorld(), state.world) and same(pawn.Save, save) and not same(save, stock),
        "wire_reward_exact_guest_required")
    local escrow
    if memory then
        escrow = loot_escrow.pending_escrow(state.world, slot)
    else
        escrow = statics:LoadGameFromSlot(slot, 0)
    end
    local expected_magazine, expected_units = verified_template(profile, escrow, weapon_template, ammo_template)
    local called, ok, detail, magazine, units = pcall(grant, pawn:GetAddress(),
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

function rewards.row(receipt, status_row, row_name)
    return status_row(row_name or "native-wire-reward", receipt.state, receipt.map, receipt.pawn, receipt.slot,
        receipt.weapon_guid, receipt.ammo_guid, receipt.weapon_mag, receipt.ammo_units)
end

return rewards
