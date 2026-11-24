local pickup = {}
local guest_save = require("guest_save")
local stock_input_fixture = require("native_authored_pickup_input_fixture")
local drill_head = require("native_prototype_drill_pickup")
local loot_projection = require("native_guest_drop_projection")
local ground_stack = require("native_ground_stack")
local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
local host_fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local title_fixtures = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
local statics_path = "/Script/Engine.Default__GameplayStatics"
local save_path = "/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C"
local controller_path = "/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"

local observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
local crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
local savlo_estate = "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P"
local city = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
local crash_site = "/Game/Classes/Maps/Maps_Dungeons/CrashSite_01_P"
local canned_beans = "/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"
local borough = "/Game/Classes/Maps/Maps_SafeZones/Borough"
local dead_end = "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P"
local scav_bar = "/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P"
local gold_bag = {
    map = scav_bar, source = "scav-bar-gold-bag",
    path = scav_bar .. ".Shop_ScavBar_P:PersistentLevel.Bag_GoldRush_C_1",
    class = "/Game/Classes/Items/Key_Items/Bag_GoldRush.Bag_GoldRush_C",
    name = "Bag_GoldRush_C", callback = true, authority_sight = true, explicit_source = true,
    approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
}
local depot = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
local lab_e206 = "/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P"
local scientist = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
local northeast_rail = "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P"
local trench = "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P"
local cistern = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
local fort_leto = "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P"
local dead_drop = {
    map = crossroads,
    source = "crossroads-dead-drop-bag",
    path = crossroads .. ".CrossRoads_P:PersistentLevel.Bag_DeadDrop_CrossRoads_C_1",
    class = "/Game/Classes/Items/Key_Items/Bag_DeadDrop_CrossRoads.Bag_DeadDrop_CrossRoads_C",
    name = "Bag_DeadDrop_CrossRoads_C",
    authority_sight = true,
    quest_source = true,
}
local profiles = {
    dead_drop,
    drill_head.profile,
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
    gold_bag,
    {
        map = dead_end,
        source = "dead-end-coup-report",
        path = dead_end .. ".DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1",
        class = "/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C",
        name = "Note_DeadEnd_01_C", callback = true, authority_sight = true,
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
        map = lab_e206,
        source = "lab-e206-blank-data-tape",
        path = lab_e206 .. ".Lab_E206_P:PersistentLevel.DataTape_Blank_C_1",
        class = "/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C",
        name = "DataTape_Blank_C",
        authority_sight = true,
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
        map = crossroads,
        source = "crossroads-lockpick",
        path = crossroads .. ".CrossRoads_P:PersistentLevel.Eqp_LockPick_C_1",
        class = "/Game/Classes/Equipment/Eqp_LockPick.Eqp_LockPick_C",
        name = "Eqp_LockPick_C",
        authority_sight = true,
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
        map = cistern,
        source = "cistern-old-nfta-keycard",
        path = cistern .. ".Cistern_P:PersistentLevel.KeyCard_Refinery_Sec_BP_C_1",
        class = "/Game/Classes/Items/Key_Items/KeyCard_NFTA_Old_BP.KeyCard_NFTA_Old_BP_C",
        name = "KeyCard_NFTA_Old_BP_C",
        authority_sight = true,
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
        map = "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P",
        source = "gorge-insurrectionist-recorder",
        path = "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P.Gorge_01_P:PersistentLevel.Item_InniRadio_01_C_3",
        class = "/Game/Classes/Items/Key_Items/Item_InniRadio_01.Item_InniRadio_01_C",
        name = "Item_InniRadio_01_C",
        approach = { { -100, 0 }, { 100, 0 }, { 0, -100 }, { 0, 100 } },
        authority_sight = true,
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
    {
        map = city,
        source = "city-canned-beans",
        path = city .. ".City_01_P:PersistentLevel.Item_CannedBeans_C_1",
        class = canned_beans,
        name = "Item_CannedBeans_C",
        explicit_source = true,
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = crash_site,
        source = "crash-site-canned-beans",
        path = crash_site .. ".CrashSite_01_P:PersistentLevel.Item_CannedBeans_C_1",
        class = canned_beans,
        name = "Item_CannedBeans_C",
        explicit_source = true,
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
    {
        map = crossroads,
        source = "crossroads-brown-mushroom",
        path = crossroads .. ".CrossRoads_P:PersistentLevel.Item_BrownMushroom_C_3",
        class = "/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C",
        name = "Item_BrownMushroom_C",
        explicit_source = true,
        approach = { { -115, 0 }, { 115, 0 }, { 0, -115 }, { 0, 115 } },
    },
}
local map_profiles, empty_map, cash_limits = {}, {}, {}
for _, profile in ipairs(require("authored_cash_catalog")) do
    cash_limits[profile.class] = {profile.cash_min, profile.cash_max}
    profiles[#profiles + 1] = profile
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

local binding, report, loose_check

local function valid(value)
    return value and value:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function exact_slot(slot)
    return type(slot) == "string" and #slot == 39 and slot:match("^SFCoop_[0-9a-f]+$")
end

local function cash_amount(profile, amount)
    return profile.stock_cash == true and math.type(amount) == "integer"
        and amount >= profile.cash_min and amount <= profile.cash_max
end

local function guid(value)
    return type(value) == "string" and #value == 32 and value:match("^[0-9A-F]+$") and value ~= string.rep("0", 32)
end

local function gold_bag_step(save)
    local key = FName("Trans_Gold_BigIron", EFindName.FNAME_Find)
    local active = valid(save) and save.ActiveQuests
    if not active or not key or key:GetComparisonIndex() == 0 then return end
    local found = active:Contains(key)
    if found == false then return 0, key end
    if found ~= true then return end
    local row = active:Find(key):get()
    return row and math.tointeger(row.Current_Step_40_D0772E7742BFEF4B5AFE66AECB42B01A), key
end

function pickup.clear()
    binding, report, loose_check = nil, nil, nil
    if enabled and type(SilverfishClearNativeAuthoredPickup) == "function" then SilverfishClearNativeAuthoredPickup() end
end

function pickup.update(state, phase)
    local detail = state.local_pawn
    local pawn = detail and detail.pawn
    local host = phase == "hosting" and state.server == false
    local placements = map_profiles[state.map] or empty_map
    if not enabled or not state.listening or not valid(pawn)
        or (not host and (phase ~= "connected" or state.server ~= true))
        or type(SilverfishSetNativeAuthoredPickupScope) ~= "function" then
        if binding then pickup.clear() end
        return false
    end
    if not binding or binding.placements ~= placements or binding.host ~= host
        or not same(binding.pawn, pawn) or not same(binding.world, state.world) then
        binding = {pawn = pawn, world = state.world, host = host, placements = placements}
        report, loose_check = nil, nil
    end
    if type(SilverfishDiscoverLoosePickups) == "function" then
        local count, eligible, generation = SilverfishDiscoverLoosePickups(pawn:GetAddress(), host)
        binding.loose_count, binding.loose_generation = count, generation
        if host and type(eligible) == "string" and eligible ~= "" then
            local carry = require("native_prop_carry")
            for path in eligible:gmatch("[^\n]+") do
                local actor = StaticFindObject(path)
                if carry.enroll_loose(actor) and type(SilverfishAcknowledgeLooseCarry) == "function" then
                    SilverfishAcknowledgeLooseCarry(path, actor:GetAddress())
                end
            end
        end
    end
    local active, unavailable, reason = {}, {}, nil
    for _, profile in ipairs(placements) do
        local allowed = profile ~= gold_bag or not host or gold_bag_step(pawn.Save) == 1
        local ok, detail
        if allowed then ok, detail = SilverfishSetNativeAuthoredPickupScope(pawn:GetAddress(), host, profile.source) end
        if ok == true then
            active[profile.source] = profile
        elseif type(detail) == "string" then
            unavailable[profile.source] = detail
            reason = detail
        end
    end
    binding.active = active
    binding.unavailable = unavailable
    return next(active) ~= nil or binding.loose_count ~= nil, reason
end

local function select_profile(requested)
    if requested == nil then
        local sole
        for _, profile in ipairs(binding.placements) do
            if not profile.explicit_source and not profile.fixture_only and not profile.stock_supply
                and not profile.stock_cash and not profile.quest_source then
                if sole then sole = nil; break end
                sole = profile.source
            end
        end
        requested = sole
    end
    local profile = binding.active[requested]
    if not profile then error(binding.unavailable[requested] or "authored_pickup_exact_source_required", 0) end
    return profile
end

local function loose_profile(path, state)
    if not binding or not binding.loose_count or type(path) ~= "string" or #path > 384
        or type(SilverfishDescribeLoosePickup) ~= "function" then return end
    local class, mode, claimed, cash = SilverfishDescribeLoosePickup(path)
    local actor = class and StaticFindObject(path)
    local name = valid(actor) and actor:GetFullName():match("^(%S+) ")
    local leaf = path:match(":PersistentLevel%.([%w_]+)$")
    local area_name = state.map:match("([^/]+)$")
    local limits = class and cash_limits[class]
    if not name or not leaf or not area_name or (cash and not limits)
        or not same(actor:GetWorld(), state.world) then return end
    return {loose = true, source = path, path = path, name = name, class = class, map = state.map,
        mode = mode, claimed = claimed, stock_cash = cash, cash_min = limits and limits[1], cash_max = limits and limits[2],
        cash_flag = leaf .. "-" .. area_name .. "-"}
end

local function temporary_mark(actor)
    local library = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C")
    if not valid(library) then return end
    local result = {}
    library:Find_ActorOnTempActivations(actor, actor, result)
    if type(result.Found) == "boolean" then return result.Found end
end

local function verify_stock_mark(profile, actor)
    if profile.mode == 0 then return true end
    if profile.mode == 1 then return temporary_mark(actor) == true end
    local result = {}
    local statics = StaticFindObject(statics_path)
    local instance = valid(statics) and statics:GetGameInstance(actor)
    if not valid(instance) then return false end
    instance:Check_SaveFlag(FName(profile.cash_flag), result, result)
    return result.Found == true and result.Value == 1
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
    if name == info.name then return info.pawn, stock, statics, stock end
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not valid(pawn) or not valid(save) or same(save, stock) or not same(save, pawn.Save) then return end
    return pawn, save, statics, stock
end

local function claim(fields, state, phase, profile)
    local name, slot = fields[3], fields[4]
    if phase ~= "hosting" or not exact_slot(slot) then
        error("authored_pickup_claim_arguments", 0)
    end
    report = {source = profile.source, pawn = name, slot = slot, ok = false, applied = false, loose = profile.loose and profile}
    local pawn, save, statics, stock = owner(state, phase, name)
    local sight = profile.loose and stock_input_fixture.loose_sight or stock_input_fixture.authority_sight
    local stock_input = fields[6] == "stock-input" and state.local_pawn and same(pawn, state.local_pawn.pawn)
        and type(SilverfishReadNativeAuthoredPickup) == "function"
    if stock_input then
        local active, claimed, sequence, fixture = SilverfishReadNativeAuthoredPickup(profile.source)
        stock_input = active == true and claimed == false and math.type(sequence) == "integer" and sequence > 0
            and fixture == false
    end
    if profile.stock_cash then
        local cash = SilverfishClaimNativeAuthoredCash
        local actor = StaticFindObject(profile.path)
        if type(cash) ~= "function" or not valid(pawn) or not valid(actor)
            or not stock_input and not sight(state, phase, profile, pawn, actor) then
            report.status = "authored_cash_exact_authority_sight_required"
            return "authored cash pickup refused"
        end
        local ok, reason, amount, before, after, flag = cash(pawn:GetAddress(), save:GetAddress(), profile.source)
        report.applied = ok == true or type(reason) == "string" and reason:match("^mutation_unverified_") ~= nil
        report.ok = ok == true and reason == "cash_credited" and cash_amount(profile, amount)
            and math.type(before) == "integer" and math.type(after) == "integer" and before >= 0 and after <= 0x7fffffff
            and after == before + amount and flag == profile.cash_flag
        report.status = report.ok and "cash_credited" or (ok and "mutation_unverified_authored_cash_abi" or tostring(reason))
        if report.ok then report.cash = {amount = amount, before = before, after = after, flag = flag} end
        return "authored cash pickup reported"
    end
    if type(SilverfishCaptureNativeAuthoredPickup) ~= "function" or type(SilverfishVerifyNativeAuthoredPickup) ~= "function"
        or type(SilverfishRecoverNativeInventoryBag) ~= "function" then error("authored_pickup_claim_arguments", 0) end
    local save_class = StaticFindObject(save_path)
    local memory = type(SilverfishVerifyNativeStashSave) == "function"
    if not valid(pawn) or not valid(save_class) or not memory and statics:DoesSaveGameExist(slot, 0) then
        report.status = "authored_pickup_claimant_or_slot_unavailable"
        return "authored loose pickup reported"
    end
    local drill_before
    if profile == drill_head.profile then
        local checked, scoped = pcall(drill_head.read, state, phase, true)
        if not checked or not scoped then
            report.status = "authored_pickup_prototype_drill_inactive"
            return "authored loose pickup reported"
        end
        drill_before = scoped
    end
    if profile.stock_supply then
        if type(SilverfishMergeNativeAuthoredSupply) ~= "function" then
            report.status = "authored_supply_merge_bridge_unavailable"
            return "authored loose pickup reported"
        end
    end
    if not stock_input and (profile.stock_supply or profile.authority_sight or profile.loose) then
        local checked, visible = pcall(sight, state, phase, profile, pawn, StaticFindObject(profile.path))
        if not checked or visible ~= true then
            report.status = "authored_pickup_exact_authority_sight_required"
            return "authored loose pickup reported"
        end
    end
    if profile.callback then
        local check = SilverfishStockAuthoredPickupCallback
        if type(check) ~= "function" then error("authored_pickup_callback_unavailable", 0) end
        local ready, detail = check(pawn:GetAddress(), save:GetAddress(), false, profile.source)
        if not ready then
            report.status = tostring(detail)
            return "authored level pickup callback unavailable"
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
    local source = scratch
    if not memory then
        if not statics:SaveGameToSlot(scratch, slot, 0) then
            report.status = "authored_pickup_escrow_write_failed"
            return "authored loose pickup reported"
        end
        source = statics:LoadGameFromSlot(slot, 0)
        if not valid(source) or not SilverfishVerifyNativeAuthoredPickup(scratch:GetAddress(), source:GetAddress(), profile.source) then
            report.status = "authored_pickup_escrow_roundtrip_failed"
            return "authored loose pickup reported"
        end
    end
    report.guid = item
    report.memory = memory == true
    if profile.stock_supply then
        local accepted, reason, stack, before, after = SilverfishMergeNativeAuthoredSupply(
            pawn:GetAddress(), save:GetAddress(), source:GetAddress(), profile.source, item)
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
    local status, merged = "append", nil
    if memory and profile.loose then status, merged = ground_stack.merge(pawn, save, stock, source, item) end
    if status == "append" then
        local applied, outcome, count = SilverfishRecoverNativeInventoryBag(source:GetAddress(), save:GetAddress(), pawn:GetAddress())
        report.applied = applied == true or tostring(outcome):match("^mutation_unverified_") ~= nil
        report.ok = applied == true and outcome == "recovered" and count == 1
        report.status = report.ok and "claimed" or (applied and "mutation_unverified_authored_pickup_count" or tostring(outcome))
    else
        report.ok, report.status, report.stack_merge = merged ~= nil, status, merged
        report.applied = report.ok or status:match("^mutation_unverified_") ~= nil
    end
    if report.ok and profile.loose then
        local actor = StaticFindObject(profile.path)
        if not valid(actor) then error("mutation_unverified_loose_pickup_source_lost", 0) end
        actor:ResolveSaveMode()
        if not verify_stock_mark(profile, actor) then error("mutation_unverified_loose_pickup_stock_persistence", 0) end
        if not SilverfishProjectNativeAuthoredPickup(true, profile.source) then
            error("mutation_unverified_loose_pickup_retirement", 0)
        end
        pcall(loot_projection.refresh_stock_inventory, state, pawn:IsLocallyControlled())
    end
    if report.ok and profile.callback then
        local delivered, effect = SilverfishStockAuthoredPickupCallback(pawn:GetAddress(), save:GetAddress(), true, profile.source)
        if not delivered then error(tostring(effect), 0) end
        local actor = StaticFindObject(profile.path)
        if not valid(actor) then error("mutation_unverified_authored_callback_source_unavailable", 0) end
        local mode = math.tointeger(actor.Item_RespawnMode)
        local leaf = profile.path:match(":PersistentLevel%.([%w_]+)$")
        actor:ResolveSaveMode()
        if not mode or not leaf or not verify_stock_mark({
                mode = mode, cash_flag = leaf .. "-" .. state.map:match("([^/]+)$") .. "-"
            }, actor) or not SilverfishProjectNativeAuthoredPickup(true, profile.source) then
            error("mutation_unverified_authored_callback_retirement", 0)
        end
        report.effect = effect
    end
    if drill_before then
        local checked, after = pcall(drill_head.read, state, phase, true)
        if not checked or not drill_head.unchanged(drill_before, after) then
            report.ok, report.applied = false, true
            report.status = "mutation_unverified_authored_prototype_drill_progress"
        elseif report.ok then
            report.drill = {before = drill_before.topic, after = after.topic}
        end
    end
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
    if action == "native-cistern-nfta-locker-fixture" then
        return stock_input_fixture.cistern_nfta_locker(fields, state, phase)
    end
    if action == "native-gold-bag-quest-fixture" then
        if phase ~= "hosting" or state.map ~= scav_bar or #fields ~= 2 or not (fixtures and host_fixtures) then
            error("gold_bag_quest_fixture_unavailable", 0)
        end
        local local_pawn = state.local_pawn
        local pawn, save, statics
        if local_pawn then pawn, save, statics = owner(state, phase, local_pawn.name) end
        local before, key = gold_bag_step(save)
        if not valid(pawn) or before ~= 0 then error("gold_bag_quest_fixture_scope_unavailable", 0) end
        local instance = statics:GetGameInstance(pawn)
        instance:AddQuest_ByName(key)
        if gold_bag_step(save) ~= 1 then error("mutation_unverified_gold_bag_quest_fixture", 0) end
        local library = StaticFindObject("/Game/Classes/Libraries/Fuctions_Lib/FL_CodeLib01.Default__FL_CodeLib01_C")
        if not valid(library) then error("gold_bag_quest_fixture_library_unavailable", 0) end
        library:GlobalPing("Ping_NewQuest", pawn, instance)
        pickup.update(state, phase)
        if not binding.active[gold_bag.source] then
            local actor = StaticFindObject(gold_bag.path)
            local category = valid(actor) and actor.ItemType
            error((binding.unavailable[gold_bag.source] or "gold_bag_quest_fixture_source_unavailable")
                .. " category=" .. tostring(category), 0)
        end
        return "stock gold transport quest reached bag pickup step"
    end
    if action == "native-gold-bag-view" then
        local detail = state.local_pawn
        if phase ~= "connected" or state.map ~= scav_bar or not state.listening or state.server ~= true
            or #fields ~= 3 or (fields[3] ~= "show" and fields[3] ~= "hide")
            or not detail or not valid(detail.pawn) or not same(detail.pawn:GetWorld(), state.world)
            or not detail.pawn:IsLocallyControlled() then error("gold_bag_view_scope_unavailable", 0) end
        local actor = StaticFindObject(gold_bag.path)
        if not valid(actor) then return "stock gold bag actor absent" end
        if not same(actor:GetClass(), StaticFindObject(gold_bag.class)) or not same(actor:GetWorld(), state.world)
            or not same(actor:GetOuter(), state.world.PersistentLevel) or actor:IsActorBeingDestroyed()
            or not valid(actor.Box) or not same(actor.Box, actor.RootComponent) or not same(actor.Box:GetOwner(), actor) then
            error("gold_bag_view_source_unavailable", 0)
        end
        if fields[3] == "show" then
            local placed = actor:K2_SetActorTransform(actor.OriginalTrans, false, {}, true)
            if not placed then error("gold_bag_view_placement_unverified", 0) end
            actor:SetActorEnableCollision(true)
            actor:SetActorHiddenInGame(false)
        else
            actor:SetActorEnableCollision(false)
            actor:SetActorHiddenInGame(true)
        end
        local shown = fields[3] == "show"
        if actor.bHidden ~= (not shown) or actor:GetActorEnableCollision() ~= shown then
            error("gold_bag_view_readback_unverified", 0)
        end
        pickup.update(state, phase)
        if shown and not binding.active[gold_bag.source] then error("gold_bag_view_interaction_unverified", 0) end
        return "stock gold bag view synchronized"
    end
    if action == "native-dead-drop-project" then
        local detail = state.local_pawn
        if #fields ~= 4 or phase ~= "connected" or not state.listening or state.server ~= true
            or state.map ~= dead_drop.map or not detail or detail.name ~= fields[4] or not valid(detail.pawn)
            or type(SilverfishProjectNativeCrossroadsDeadDrop) ~= "function" then
            error("dead_drop_projection_scope_unavailable", 0)
        end
        local applied, status = SilverfishProjectNativeCrossroadsDeadDrop(detail.pawn:GetAddress(), fields[3])
        if applied ~= true then error(status, 0) end
        return "verified local stock Dead Drop presentation"
    end
    if action == "native-dead-drop-clear" then
        if #fields ~= 2 or phase ~= "connected" or not state.listening or state.server ~= true
            or state.map ~= dead_drop.map or type(SilverfishClearNativeCrossroadsDeadDrop) ~= "function" then
            error("dead_drop_projection_scope_unavailable", 0)
        end
        local cleared, status = SilverfishClearNativeCrossroadsDeadDrop()
        if cleared ~= true then error(status, 0) end
        return "cleared local stock Dead Drop presentation"
    end
    if action == "native-loose-pickup-expire-fixture" then
        if phase ~= "hosting" or not (fixtures and host_fixtures) or #fields ~= 3 then
            error("loose_pickup_fixture_scope_unavailable", 0)
        end
        local days = require("native_loot").expire_loose_fixture(state, phase, fields[3])
        return "stock temporary loot expired after " .. tostring(days) .. " daily updates"
    end
    if action == "native-loose-pickup-claim" then
        pickup.update(state, phase)
        local valid_args = #fields == 5 or (#fields == 6 and fields[6] == "stock-input")
        local profile = valid_args and loose_profile(fields[5], state)
        if not profile or profile.claimed or require("native_prop_carry").item_is_held(StaticFindObject(profile.path)) then
            error("loose_pickup_source_unavailable", 0)
        end
        return claim(fields, state, phase, profile)
    end
    if action == "native-loose-pickup-check" then
        pickup.update(state, phase)
        if phase ~= "hosting" or not binding or not binding.loose_count or #fields < 3 or #fields > 66 then
            error("loose_pickup_check_arguments", 0)
        end
        loose_check = {}
        for index = 3, #fields do
            local profile = loose_profile(fields[index], state)
            local available = profile and not profile.claimed and (profile.mode == 0
                or profile.mode == 1 and temporary_mark(StaticFindObject(profile.path)) == false)
            loose_check[#loose_check + 1] = {fields[index], available and 1 or 0}
        end
        return "stock loose pickup availability verified"
    end
    if action == "native-loose-pickup-project" then
        pickup.update(state, phase)
        local generation, mode = math.tointeger(tonumber(fields[3])), fields[4]
        if phase ~= "connected" or not binding or not generation or generation ~= binding.loose_generation or
            #fields < 5 or #fields > 68 or (mode ~= "hide" and mode ~= "show") or
            type(SilverfishSetGuestLoosePickupView) ~= "function" then
            error("loose_pickup_projection_arguments", 0)
        end
        local accepted, issue = SilverfishSetGuestLoosePickupView(generation, mode == "hide", table.unpack(fields, 5))
        if not accepted then error(issue, 0) end
        pcall(loot_projection.refresh_stock_inventory, state, false)
        return "shared level item visibility synchronized"
    end
    if action == "native-loose-pickup-nearby-fixture" then
        pickup.update(state, phase)
        local profile = #fields == 3 and loose_profile(fields[3], state)
        local controller = state.controller
        local class = StaticFindObject("/Game/Classes/GUI/New/InventoryScreen.InventoryScreen_C")
        if not host_fixtures or phase ~= "hosting" or not profile or profile.claimed
            or not valid(controller) or not valid(class) or controller.MenuStack:GetArrayNum() ~= 0 then
            error("loose_pickup_nearby_fixture_unavailable", 0)
        end
        local created = {}
        controller:OpenMenu(class, created)
        local screen = created.CreatedMenu
        if not valid(screen) or not same(screen:GetClass(), class) then error("loose_pickup_nearby_menu_unavailable", 0) end
        local target = StaticFindObject(profile.path)
        screen:FindFloorItems()
        for index = 1, screen.FloorActors:GetArrayNum() do
            if same(screen.FloorActors[index], target) then
                local guid = screen.FloorItems[index].GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A
                local result = {}
                screen:RemoveItem({A = guid.A, B = guid.B, C = guid.C, D = guid.D}, 0, {}, result)
                if result.Success == true or not valid(target) or target.bHidden then
                    error("loose_pickup_nearby_bypassed_authority", 0)
                end
                return "stock Nearby Items pickup requested"
            end
        end
        error("loose_pickup_nearby_card_unavailable", 0)
    end
    if action == "native-loose-pickup-approach" or action == "native-loose-pickup-fixture" then
        pickup.update(state, phase)
        local approach = action == "native-loose-pickup-approach"
        local profile = #fields == (approach and 4 or 3) and loose_profile(fields[3], state)
        local fixture_host = host_fixtures or (approach and title_fixtures)
        if not profile or profile.claimed or not fixtures
            or (approach and phase ~= "hosting") or (phase == "hosting" and not fixture_host) then
            local diagnose = SilverfishDiagnoseLoosePickup
            local reason = fixtures and type(diagnose) == "function" and diagnose(fields[3]) or "scope"
            error("loose_pickup_fixture_scope_unavailable:" .. reason, 0)
        end
        local actor = StaticFindObject(profile.path)
        local pawn
        if approach then
            pawn = owner(state, phase, fields[4])
        else
            pawn = state.local_pawn and state.local_pawn.pawn
        end
        if not valid(pawn) or not valid(actor) then error("loose_pickup_fixture_scope_unavailable", 0) end
        if approach then
            stock_input_fixture.approach_world_loot(state, phase, pawn, actor, actor.Box, title_fixtures)
            return "shared level pickup approach verified"
        end
        return stock_input_fixture.press_world_loot(state, pawn, actor, actor.Box, function()
            local ok, reason = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
            if not ok or reason ~= "stock_started_consumed" then error("loose_pickup_fixture_input_unverified", 0) end
            return reason
        end)
    end
    local count = arguments[action]
    if not count then return end
    local stock_claim = action == "native-authored-pickup-claim" and #fields == 6 and fields[6] == "stock-input"
    if #fields ~= count and #fields ~= count + 1 and not stock_claim then error("authored_pickup_arguments", 0) end
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
        local progress
        if profile == drill_head.profile and phase == "hosting" and state_word == "claimed" then
            progress = drill_head.read(state, phase, false)
            if not progress then error("authored_prototype_drill_projection_scope_unavailable", 0) end
        end
        local projected, detail = SilverfishProjectNativeAuthoredPickup(state_word == "claimed", profile.source)
        if not projected then error(detail, 0) end
        if progress and not drill_head.unchanged(progress, drill_head.read(state, phase, false)) then
            error("mutation_unverified_authored_prototype_drill_projection", 0)
        end
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
    if type(source) == "string" and source:match("^/Game/Classes/Maps/[%w_/]+%.[%w_]+:PersistentLevel%.[%w_]+$") then
        return "loose:" .. source
    end
end

function pickup.rows(state, phase, status_row)
    local rows = {}
    local host = phase == "hosting" and state.server == false
    local client = phase == "connected" and state.server == true
    if host and type(SilverfishVerifyNativeStashSave) == "function" then
        rows[#rows + 1] = status_row("native-authored-pickup-memory", 1)
    end
    local detail = state.local_pawn
    if state.map == dead_drop.map and state.listening and (host or client) and detail and valid(detail.pawn) then
        local observe = SilverfishObserveNativeCrossroadsDeadDrop
        if type(observe) ~= "function" then
            rows[#rows + 1] = status_row("native-dead-drop-diagnostic", state.map, "native_observer_unavailable")
        else
            local shown, epoch, fault = observe(detail.pawn:GetAddress(), host)
            if shown == "diagnostic" then
                rows[#rows + 1] = status_row("native-dead-drop-diagnostic", state.map, fault)
            elseif shown then
                rows[#rows + 1] = status_row("native-dead-drop", shown, state.map, dead_drop.name .. " " .. dead_drop.path, detail.name, epoch)
            end
        end
    end
    local qualified = pickup.update(state, phase)
    if not qualified or type(SilverfishReadNativeAuthoredPickup) ~= "function" then return rows end
    for _, profile in ipairs(binding.placements) do
        if binding.active[profile.source] then
            local active, claimed, sequence, fixture, amount = SilverfishReadNativeAuthoredPickup(profile.source)
            if active and not claimed and profile == gold_bag then
                local actor = StaticFindObject(profile.path)
                active = valid(actor) and actor.bHidden == false and actor:GetActorEnableCollision() == true
            end
            if active then
                local fields = {"native-authored-pickup", profile.source, state.map,
                    profile.name .. " " .. profile.path, profile.class, claimed and "claimed" or "available"}
                if binding.host and cash_amount(profile, amount) then
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
    if binding.loose_count then
        rows[#rows + 1] = status_row("native-loose-pickup-capability", 1, binding.loose_count)
        if phase == "hosting" and type(SilverfishReadHostLooseRespawnHidden) == "function" then
            local hidden = SilverfishReadHostLooseRespawnHidden()
            if type(hidden) == "string" then
                for path in hidden:gmatch("[^\n]+") do
                    rows[#rows + 1] = status_row("native-loose-pickup-respawn-hidden", state.map, path)
                end
            end
        end
        if phase == "connected" and binding.loose_generation then
            rows[#rows + 1] = status_row("native-loose-pickup-view-scope", binding.loose_generation)
        end
        local list = SilverfishNearbyLoosePickups()
        if type(list) == "string" then
            for entry in list:gmatch("[^\n]+") do
                local path, class, mode, availability, x, y, z = entry:match(
                    "^([^|]+)|([^|]+)|([012])|([^|]+)|([^|]+)|([^|]+)|([^|]+)$")
                if path then
                    rows[#rows + 1] = status_row("native-loose-pickup", state.map, path, class, mode, availability, x, y, z)
                end
            end
        end
    end
    if loose_check and phase == "hosting" then
        for _, checked in ipairs(loose_check) do
            rows[#rows + 1] = status_row("native-loose-pickup-check-result", state.map, checked[1], checked[2])
        end
    end
    if report and phase == "hosting" and report.loose then
        local profile, cash, merge = report.loose, report.cash, report.stack_merge
        local evidence = merge and {merge.source, merge.destination, merge.amount, merge.before, merge.after} or {}
        rows[#rows + 1] = status_row("native-loose-pickup-claim", state.map, profile.source, profile.class,
            profile.mode, report.pawn, report.slot, report.status, report.ok and 1 or 0, report.applied and 1 or 0,
            report.guid or "-", cash and cash.amount or 0, cash and cash.before or 0, cash and cash.after or 0,
            report.memory and "memory" or "disk", table.unpack(evidence))
    elseif report and phase == "hosting" then
        rows[#rows + 1] = status_row("native-authored-pickup-claim", report.source, state.map, report.pawn,
            report.ok and 1 or 0, report.applied and 1 or 0, report.status, report.guid or "-", report.slot,
            (report.memory or report.cash) and "memory" or "disk")
        if report.effect then
            rows[#rows + 1] = status_row("native-authored-pickup-effect", report.source, state.map, report.pawn,
                report.slot, report.effect)
        end
        if report.stack then
            rows[#rows + 1] = status_row("native-authored-supply-merge", report.source, state.map, report.pawn,
                report.slot, report.guid, report.stack, report.before, report.after)
        end
        if report.cash then
            local cash = report.cash
            rows[#rows + 1] = status_row("native-authored-cash-credit", report.source, state.map, report.pawn, report.slot,
                cash.amount, cash.before, cash.after, cash.flag)
        end
        if report.drill then
            rows[#rows + 1] = status_row("native-authored-prototype-drill", report.source, state.map, report.pawn, report.slot,
                report.guid, drill_head.quest, 1, report.drill.before, report.drill.after)
        end
    end
    return rows
end

return pickup
