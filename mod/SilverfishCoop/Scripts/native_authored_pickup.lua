local pickup = {}
local guest_save = require("guest_save")
local stock_input_fixture = require("native_authored_pickup_input_fixture")
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local savlo_estate = "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P"
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local dead_end = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"
local depot = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local northeast_rail = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P"
local trench = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
local fort_leto = "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P"
local profiles = {
    {
        map = observatory,
        source = "observatory-comp-board",
        path = observatory .. ".Observitory_P:PersistentLevel.Item_CompBoard_C_2",
        class = "/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C",
        name = "Item_CompBoard_C",
        approach = { { -115, 0 }, { 115, 0 } },
    },
    {
        map = city,
        source = "city-copper-wire",
        path = city .. ".City_01_P:PersistentLevel.Item_CopperWire_C_3",
        class = "/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C",
        name = "Item_CopperWire_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = city,
        source = "city-paladin-brandy",
        path = city .. ".City_01_P:PersistentLevel.Item_VintageWine_C_1",
        class = "/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C",
        name = "Item_VintageWine_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = dead_end,
        source = "dead-end-coup-report",
        path = dead_end .. ".DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1",
        class = "/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C",
        name = "Note_DeadEnd_01_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = depot,
        source = "railyard-depot-documents",
        path = depot .. ".RailYard_P:PersistentLevel.Documents_DepotOffice_BP_C_1",
        class = "/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C",
        name = "Documents_DepotOffice_BP_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = scientist,
        source = "scientist-blank-data-tape",
        path = scientist .. ".Shop_Scientist_01:PersistentLevel.DataTape_Blank_C_3",
        class = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C",
        name = "DataTape_Blank_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = crossroads,
        source = "crossroads-pocket-watch",
        path = crossroads .. ".CrossRoads_P:PersistentLevel.Item_PocketWatch_C_1",
        class = "/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C",
        name = "Item_PocketWatch_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = crossroads,
        source = "crossroads-old-pipe",
        path = crossroads .. ".CrossRoads_P:PersistentLevel.MeleeWep_OldPipe_C_1",
        class = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C",
        name = "MeleeWep_OldPipe_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
        fixture_only = true,
    },
    {
        map = savlo_estate,
        source = "savlo-estate-evil-eye-ring",
        path = savlo_estate .. ".SavloEstate_P:PersistentLevel.Art_EvilEyeRing_C_1",
        class = "/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C",
        name = "Art_EvilEyeRing_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = borough,
        source = "borough-mre",
        path = borough .. ".Borough:PersistentLevel.Item_MRE_C_1",
        class = "/Game/Classes/Items/Item_MRE.Item_MRE_C",
        name = "Item_MRE_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = depot,
        source = "railyard-dust-filter",
        path = depot .. ".RailYard_P:PersistentLevel.Helm_DustFilter_C_1",
        class = "/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C",
        name = "Helm_DustFilter_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = northeast_rail,
        source = "northeast-rail-molasses",
        path = northeast_rail .. ".NorthEast_Rail_P:PersistentLevel.Item_Molasis_C_1",
        class = "/Game/Classes/Items/Item_Molasis.Item_Molasis_C",
        name = "Item_Molasis_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = trench,
        source = "trench-savlo-keycard",
        path = trench .. ".Trench_01_P:PersistentLevel.KeyCard_Savlo_BP_C_1",
        class = "/Game/Classes/Items/Key_Items/KeyCard_Savlo_BP.KeyCard_Savlo_BP_C",
        name = "KeyCard_Savlo_BP_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = fort_leto,
        source = "fort-leto-locker-key-543",
        path = fort_leto .. ".FortLeto_01_P:PersistentLevel.LockerKey_543_C_1",
        class = "/Game/Classes/Items/Key_Items/LockerKey_543.LockerKey_543_C",
        name = "LockerKey_543_C",
        approach = { { 140, 0 }, { 115, 115 }, { 115, -115 }, { -115, 0 } },
    },
    {
        map = "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P",
        source = "forest-brokers-artifacts-bag",
        path = "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P:PersistentLevel.Brokers_ArtifactsBag_C_1",
        class = "/Game/Classes/Items/Key_Items/Brokers_ArtifactsBag.Brokers_ArtifactsBag_C",
        name = "Brokers_ArtifactsBag_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
        authority_sight = true,
    },
    {
        map = city,
        source = "city-canned-peaches",
        path = city .. ".City_01_P:PersistentLevel.Item_CannedPeaches_C_1",
        class = "/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C",
        name = "Item_CannedPeaches_C",
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
}
local map_profiles = {}
for _, number in ipairs({7, 8}) do
    profiles[#profiles + 1] = {
        map = city,
        source = "city-couple-bills-" .. number,
        path = city .. ".City_01_P:PersistentLevel.Item_Money_CoupleBills_C_" .. number,
        class = "/Game/Classes/Items/Item_Money_CoupleBills.Item_Money_CoupleBills_C",
        name = "Item_Money_CoupleBills_C",
        stock_cash = true,
    }
end
for _, profile in ipairs(require("authored_supply_catalog")) do
    profiles[#profiles + 1] = profile
end
for _, profile in ipairs(profiles) do
    if not profile.fixture_only or (os.getenv("SILVERFISH_COOP_NATIVE_GUEST_MELEE_CANARY") == "1"
        and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1") then
        local entries = map_profiles[profile.map] or {}
        entries[#entries + 1] = profile
        map_profiles[profile.map] = entries
    end
end

local binding, report

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$")
end

local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value ~= string.rep("0", 32)
end

function pickup.clear()
    binding, report = nil, nil
    if enabled and type(SilverfishClearNativeAuthoredPickup) == "function" then SilverfishClearNativeAuthoredPickup() end
end

function pickup.update(state, phase)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    local host = phase == "hosting" and state.server == false
    local placements = map_profiles[state.map]
    if not enabled or not placements or not state.listening or not valid(pawn)
        or (not host and (phase ~= "connected" or state.server ~= true))
        or type(SilverfishSetNativeAuthoredPickupScope) ~= "function" then
        if binding then pickup.clear() end
        return false
    end
    if not binding or binding.placements ~= placements or binding.host ~= host
        or not same(binding.pawn, pawn) or not same(binding.world, state.world) then
        binding = {pawn = pawn, world = state.world, host = host, placements = placements}
        report = nil
    end
    local active, reason = {}, nil
    for _, profile in ipairs(placements) do
        local ok, detail = SilverfishSetNativeAuthoredPickupScope(pawn:GetAddress(), host, profile.source)
        if ok == true then active[profile.source] = profile else reason = detail end
    end
    binding.active = active
    return next(active) ~= nil, reason
end

local function select_profile(requested)
    if requested == nil then
        local sole
        for _, profile in ipairs(binding.placements) do
            if not profile.fixture_only and not profile.stock_supply and not profile.stock_cash then
                if sole then sole = nil; break end
                sole = profile.source
            end
        end
        requested = sole
    end
    local profile = binding.active[requested]
    if not profile then error("authored_pickup_exact_source_required", 0) end
    return profile
end

local function owner(state, phase, name)
    local info, controller = state.local_pawn, state.controller
    local statics = StaticFindObject(statics_path)
    if not valid(statics) or not info or not valid(info.pawn) or not valid(controller)
        or not same(controller:GetClass(), StaticFindObject(controller_path))
        or not controller:HasAuthority() or not controller:IsLocalController()
        or not same(info.pawn, controller.Pawn) or not same(info.pawn:GetWorld(), state.world) then return end
    local instance = statics:GetGameInstance(info.pawn)
    local stock = valid(instance) and instance.SaveSlot_Ref
    if not valid(stock) or not same(info.pawn.Save, stock) then return end
    if name == info.name then return info.pawn, stock, statics end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(save) or same(save, stock) or not same(save, pawn.Save) then return end
    return pawn, save, statics
end

local function claim(fields, state, phase, profile)
    local name, slot = fields[3], fields[4]
    if phase ~= "hosting" or not exact_slot(slot) then
        error("authored_pickup_claim_arguments", 0)
    end
    report = {source = profile.source, pawn = name, slot = slot, ok = false, applied = false}
    local pawn, save, statics = owner(state, phase, name)
    if profile.stock_cash then
        local cash = SilverfishClaimNativeAuthoredCash
        local actor = StaticFindObject(profile.path)
        if type(cash) ~= "function" or not valid(pawn) or not valid(actor)
            or not stock_input_fixture.authority_sight(state, phase, profile, pawn, actor) then
            report.status = "authored_cash_exact_authority_sight_required"
            return "authored cash pickup refused"
        end
        local ok, reason, amount, before, after, flag = cash(pawn:GetAddress(), save:GetAddress(), profile.source)
        report.applied = ok == true or type(reason) == "string" and reason:match("^mutation_unverified_") ~= nil
        report.ok = ok == true and reason == "cash_credited" and math.type(amount) == "integer" and amount >= 6 and amount <= 10
            and math.type(before) == "integer" and math.type(after) == "integer" and before >= 0 and after <= 0x7fffffff
            and after == before + amount and flag == profile.path:match("([^%.:]+)$") .. "-City_01_P-"
        report.status = report.ok and "cash_credited" or (ok and "mutation_unverified_authored_cash_abi" or tostring(reason))
        if report.ok then report.cash = {amount = amount, before = before, after = after, flag = flag} end
        return "authored cash pickup reported"
    end
    if type(SilverfishCaptureNativeAuthoredPickup) ~= "function" or type(SilverfishVerifyNativeAuthoredPickup) ~= "function"
        or type(SilverfishRecoverNativeInventoryBag) ~= "function" then error("authored_pickup_claim_arguments", 0) end
    local save_class = StaticFindObject(save_path)
    if not valid(pawn) or not valid(save_class) or statics:DoesSaveGameExist(slot, 0) then
        report.status = "authored_pickup_claimant_or_slot_unavailable"
        return "authored loose pickup reported"
    end
    if profile.stock_supply then
        if type(SilverfishMergeNativeAuthoredSupply) ~= "function" then
            report.status = "authored_supply_merge_bridge_unavailable"
            return "authored loose pickup reported"
        end
    end
    if profile.stock_supply or profile.authority_sight then
        local checked, visible = pcall(stock_input_fixture.authority_sight, state, phase, profile, pawn, StaticFindObject(profile.path))
        if not checked or visible ~= true then
            report.status = "authored_pickup_exact_authority_sight_required"
            return "authored loose pickup reported"
        end
    end
    local scratch = statics:CreateSaveGameObject(save_class)
    if not valid(scratch) then
        report.status = "authored_pickup_escrow_create_failed"
        return "authored loose pickup reported"
    end
    local ok, reason, item = SilverfishCaptureNativeAuthoredPickup(pawn:GetAddress(), save:GetAddress(), scratch:GetAddress(), profile.source)
    if not ok or not guid(item) then
        report.status = tostring(reason)
        return "authored loose pickup reported"
    end
    if not statics:SaveGameToSlot(scratch, slot, 0) then
        report.status = "authored_pickup_escrow_write_failed"
        return "authored loose pickup reported"
    end
    local restored = statics:LoadGameFromSlot(slot, 0)
    if not valid(restored) or not SilverfishVerifyNativeAuthoredPickup(scratch:GetAddress(), restored:GetAddress(), profile.source) then
        report.status = "authored_pickup_escrow_roundtrip_failed"
        return "authored loose pickup reported"
    end
    report.guid = item
    if profile.stock_supply then
        local accepted, reason, stack, before, after = SilverfishMergeNativeAuthoredSupply(
            pawn:GetAddress(), save:GetAddress(), restored:GetAddress(), profile.source, item)
        if accepted == true and reason == "merged" then
            report.applied = true
            report.ok = guid(stack) and stack ~= item and math.type(before) == "integer" and before > 0
                and math.type(after) == "integer" and after > before and after <= 0x7fffffff
            report.status = report.ok and "merged" or "mutation_unverified_authored_supply_merge_proof"
            if report.ok then report.stack, report.before, report.after = stack, before, after end
            return "authored loose pickup reported"
        end
        if accepted ~= true or reason ~= "append" then
            report.status = accepted == false and type(reason) == "string" and reason
                or "mutation_unverified_authored_supply_merge_abi"
            report.applied = report.status:match("^mutation_unverified_") ~= nil
            return "authored loose pickup reported"
        end
    end
    local applied, status, count = SilverfishRecoverNativeInventoryBag(restored:GetAddress(), save:GetAddress(), pawn:GetAddress())
    report.applied = applied == true or tostring(status):match("^mutation_unverified_") ~= nil
    report.ok = applied == true and status == "recovered" and count == 1
    report.status = report.ok and "claimed" or (applied and "mutation_unverified_authored_pickup_count" or tostring(status))
    return "authored loose pickup reported"
end

local arguments = {
    ["native-authored-pickup-project"] = 3,
    ["native-authored-pickup-claim"] = 4,
    ["native-authored-pickup-fixture"] = 2,
    ["native-authored-pickup-stock-fixture"] = 2,
    ["native-authored-pickup-approach"] = 3,
}

function pickup.command(fields, state, phase)
    local action = fields[2]
    local count = arguments[action]
    if not count then return end
    if #fields ~= count and #fields ~= count + 1 then error("authored_pickup_arguments", 0) end
    local ok, reason = pickup.update(state, phase)
    if not ok then error(reason or "authored_pickup_scope_unavailable", 0) end
    local profile = select_profile(fields[count + 1])
    if action == "native-authored-pickup-claim" then return claim(fields, state, phase, profile) end
    if action == "native-authored-pickup-approach" then
        if phase ~= "hosting" or not fixtures or not host_fixtures then
            error("The stock loose pickup positioning fixture is unavailable", 0)
        end
        local pawn = owner(state, phase, fields[3])
        local actor = StaticFindObject(profile.path)
        if not valid(pawn) or not valid(actor) or actor:GetFullName() ~= profile.name .. " " .. profile.path
            or not same(actor:GetWorld(), state.world) then
            error("The stock loose pickup fixture did not match its placed actor", 0)
        end
        local precision = stock_input_fixture.precise_approach(state, phase, profile, pawn, actor)
        if precision then return precision end
        local point = actor:K2_GetActorLocation()
        for _, offset in ipairs(profile.approach) do
            local candidate = { X = point.X + offset[1], Y = point.Y + offset[2], Z = point.Z + 75 }
            if pawn:K2_TeleportTo(candidate, pawn:K2_GetActorRotation()) then
                pawn:ForceNetUpdate()
                return "exact stock loose pickup approached"
            end
        end
        error("The stock loose pickup fixture did not find safe ground", 0)
    end
    if action == "native-authored-pickup-project" then
        local state_word = fields[3]
        if (state_word ~= "available" and state_word ~= "claimed")
            or type(SilverfishProjectNativeAuthoredPickup) ~= "function" then error("authored_pickup_project_arguments", 0) end
        local projected, detail = SilverfishProjectNativeAuthoredPickup(state_word == "claimed", profile.source)
        if not projected then error(detail, 0) end
        return "authored loose pickup projection verified"
    end
    if not fixtures or (phase == "hosting" and not host_fixtures) then error("authored_pickup_fixture_unavailable", 0) end
    if action == "native-authored-pickup-stock-fixture" then return stock_input_fixture.press(state, phase, profile) end
    if type(SilverfishProbeNativeAuthoredPickup) ~= "function" then error("authored_pickup_fixture_unavailable", 0) end
    local intercepted, detail = SilverfishProbeNativeAuthoredPickup(profile.source)
    if not intercepted then error(detail, 0) end
    return "authored loose pickup stock interaction intercepted"
end

function pickup.take_press(pawn)
    if not binding or not same(pawn, binding.pawn) or type(SilverfishTakeNativeAuthoredPickupPress) ~= "function" then return end
    local source = SilverfishTakeNativeAuthoredPickupPress(pawn:GetAddress())
    if binding.active[source] then return source end
end

function pickup.rows(state, phase, status_row)
    local qualified = pickup.update(state, phase)
    if not qualified or type(SilverfishReadNativeAuthoredPickup) ~= "function" then return {} end
    local rows = {}
    for _, profile in ipairs(binding.placements) do
        if binding.active[profile.source] then
            local active, claimed, sequence, fixture, amount = SilverfishReadNativeAuthoredPickup(profile.source)
            if active then
                local fields = {"native-authored-pickup", profile.source, state.map,
                    profile.name .. " " .. profile.path, profile.class, claimed and "claimed" or "available"}
                if profile.stock_cash and binding.host and math.type(amount) == "integer" and amount >= 6 and amount <= 10 then
                    fields[7] = amount
                end
                rows[#rows + 1] = status_row(table.unpack(fields))
                if sequence > 0 then
                    rows[#rows + 1] = status_row("native-authored-pickup-interaction", profile.source, state.map,
                        state.local_pawn.name, sequence, fixture and "fixture" or "stock")
                end
            end
        end
    end
    if report and phase == "hosting" then
        rows[#rows + 1] = status_row("native-authored-pickup-claim", report.source, state.map, report.pawn,
            report.ok and 1 or 0, report.applied and 1 or 0, report.status, report.guid or "-", report.slot)
        if report.stack then
            rows[#rows + 1] = status_row("native-authored-supply-merge", report.source, state.map, report.pawn,
                report.slot, report.guid, report.stack, report.before, report.after)
        end
        if report.cash then
            local cash = report.cash
            rows[#rows + 1] = status_row("native-authored-cash-credit", report.source, state.map, report.pawn, report.slot,
                cash.amount, cash.before, cash.after, cash.flag)
        end
    end
    return rows
end

return pickup
