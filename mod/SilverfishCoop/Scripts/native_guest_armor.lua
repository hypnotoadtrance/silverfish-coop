local armor = {}
local guest_save = require("guest_save")

local enabled = os.getenv("SILVERFISH_COOP_NATIVE_ZONE") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_GUEST_SAVE") == "1"
local fixtures = enabled and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"
local jacket = "/Game/Classes/Armor_Items/Armor_BomberJacket.Armor_BomberJacket_C"
local hunter_jacket = "/Game/Classes/Armor_Items/Armor_HunterJacket.Armor_HunterJacket_C"
local hazard_suit = "/Game/Classes/Armor_Items/Armor_HazzardSuit.Armor_HazzardSuit_C"
local dust_filter = "/Game/Classes/Armor_Items/Helm_DustFilter.Helm_DustFilter_C"
local basic_helmet = "/Game/Classes/Armor_Items/Helm_Basic.Helm_Basic_C"
local goggles = "/Game/Classes/Armor_Items/Helm_Goggles.Helm_Goggles_C"
local conscript_uniform = "/Game/Classes/Armor_Items/Armor_ConscriptUniform.Armor_ConscriptUniform_C"
local standard_infantry_helmet = "/Game/Classes/Armor_Items/Helm_StandardInf.Helm_StandardInf_C"
local seed_profiles = {
    ["hunter-jacket"] = hunter_jacket, ["hazard-suit"] = hazard_suit, ["basic-helmet"] = basic_helmet, goggles = goggles,
    ["dust-filter"] = dust_filter, ["conscript-uniform"] = conscript_uniform, ["standard-infantry-helmet"] = standard_infantry_helmet,
}
local class_field = "Item_Class_3_2813641E42AA35FC0693029E4EE49F81"
local guid_field = "GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"
local projected, observation, disguise_observation = {body = {}, head = {}}, nil, nil
local disguise_probe = "native-guest-disguise-probe-fixture"
local dead_bridge = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"

function armor.is_body_path(path)
    return type(path) == "string" and path:match("^/Game/Classes/Armor_Items/[%w_/]*[/]?(Armor_[%w_]+)%.%1_C$") ~= nil
end

function armor.is_helmet_path(path)
    return type(path) == "string" and path:match("^/Game/Classes/Armor_Items/[%w_/]*[/]?(Helm_[%w_]+)%.%1_C$") ~= nil
end

local function guid_text(parts)
    if not parts then return end
    local words = {}
    for _, name in ipairs({"A", "B", "C", "D"}) do
        local word = math.tointeger(parts[name])
        if not word or word < -0x80000000 or word > 0xffffffff then return end
        words[#words + 1] = word & 0xffffffff
    end
    local text = string.format("%08X%08X%08X%08X", table.unpack(words))
    return text:find("[^0]") and text or nil
end

local function native_report(map, pawn, guid, action, ok, status, before, after, carry_before, carry_after, minimum, slash, acid,
                             profile, replaced_profile, replaced_guid, torso_profile, combined_slash, combined_acid, combined_poison,
                             voice_filter)
    return {map = map, pawn = pawn, guid = guid, action = action, ok = ok == true, status = status,
        before = before, after = after, carry_before = carry_before, carry_after = carry_after,
        minimum = minimum, slash = slash, acid = acid, profile = profile, replaced_profile = replaced_profile, replaced_guid = replaced_guid,
        torso_profile = torso_profile, combined_slash = combined_slash, combined_acid = combined_acid,
        combined_poison = combined_poison, voice_filter = voice_filter}
end

local function recognition(actor, pawn)
    local result, npc = {}, {}
    pawn:Identify_Faction(actor.Faction, actor, false, result, result, result)
    actor:IdentifyRelation(pawn, false, npc, npc)
    if not math.tointeger(result.TargetFaction) or not math.tointeger(npc.Relationship)
        or type(pawn.Disguise_Compromised) ~= "boolean" then error("native_guest_disguise_outputs_unqualified", 0) end
    return {configured = pawn.Disguise, compromised = pawn.Disguise_Compromised,
        recognized = result.TargetFaction, relation = npc.Relationship}
end

local function distance_squared(first, second)
    return (first.X - second.X)^2 + (first.Y - second.Y)^2 + (first.Z - second.Z)^2
end

local function observe_disguise(fields, state, phase)
    disguise_observation = nil
    if not fixtures or phase ~= "hosting" or state.map ~= dead_bridge or #fields ~= 3
        or not state.local_pawn then error("native_guest_disguise_fixture_unavailable", 0) end
    local guest = guest_save.exact_item_context(state, phase, fields[3])
    if not guest then error("native_guest_disguise_exact_owner_required", 0) end
    local host = state.local_pawn.pawn
    local host_at, guest_at = host:K2_GetActorLocation(), guest:K2_GetActorLocation()
    local profile = require("native_enemy_loot_registry")[state.map]
    for _, name in ipairs(profile.names) do
        local actor = StaticFindObject(profile.paths[name])
        if actor and actor:IsValid() and actor:GetFullName() == name and actor:HasAuthority()
            and actor:GetWorld():GetAddress() == state.world:GetAddress() and actor.Faction == 4 then
            local at = actor:K2_GetActorLocation()
            if distance_squared(at, host_at) > 250^2 and distance_squared(at, guest_at) > 250^2 then
                disguise_observation = {map = state.map, pawn = fields[3], actor = name,
                    host = recognition(actor, host), guest = recognition(actor, guest)}
                return "stock State disguise recognition reported"
            end
        end
    end
    error("native_guest_disguise_distant_state_npc_required", 0)
end

function armor.command(fields, state, phase)
    local action = fields[2]
    if action == disguise_probe then return observe_disguise(fields, state, phase) end
    local head = action == "native-guest-helmet-equip"
    if action ~= "native-guest-armor-equip" and action ~= "native-guest-armor-fixture" and not head then return end
    observation = nil
    local seed = action == "native-guest-armor-fixture"
    local pawn_name, guid, option = fields[3], fields[4], fields[5]
    local seed_profile = seed and option or nil
    local reported = not seed and option == "reported"
    local valid_count = #fields == 4 or (#fields == 5 and (reported or seed_profiles[seed_profile]))
    if not enabled or phase ~= "hosting" or not valid_count or not pawn_name
        or not guid or #guid ~= 32 or guid:find("[^0-9A-F]") or not guid:find("[^0]")
        or (seed and not fixtures) then error("native_guest_armor_exact_context_required", 0) end
    local invoke
    if seed then invoke = SilverfishSeedNativeGuestArmor
    elseif head then invoke = SilverfishEquipNativeGuestHelmet
    else invoke = SilverfishEquipNativeGuestArmor end
    if type(invoke) ~= "function" then error("native_guest_armor_unavailable", 0) end
    local qualified, pawn, save = pcall(guest_save.exact_item_context, state, phase, pawn_name)
    if not qualified or not pawn or not save then error("native_guest_armor_exact_owner_required", 0) end
    if seed then
        local path = seed_profile and seed_profiles[seed_profile] or jacket
        local class = StaticFindObject(path)
        if not class or not class:IsValid() then LoadAsset(path) end
    end
    local arguments = {pawn:GetAddress(), save:GetAddress(), guid, reported and true or seed_profile}
    observation = native_report(state.map, pawn_name, guid, seed and "seed" or "equip",
        invoke(table.unpack(arguments)))
    observation.head = head
    return "native guest armor operation reported"
end

local function worn_guid(row, profile)
    local class = row and row[class_field]
    if not class or not class:IsValid() or not profile(class:GetFullName():match("^%S+ (%S+)$")) then return end
    return guid_text(row[guid_field])
end

local function exact_worn(state, phase, name)
    local pawn, save = guest_save.exact_item_context(state, phase, name)
    if not pawn or not save then return end
    return pawn:GetAddress(), save:GetAddress(),
        {body = worn_guid(save.PlayerArmor, armor.is_body_path), head = worn_guid(save.PlayerHelmet, armor.is_helmet_path)}
end

function armor.sync(state, phase)
    local current = {body = {}, head = {}}
    local project = {body = SilverfishProjectNativeGuestArmor, head = SilverfishProjectNativeGuestHelmet}
    if enabled and phase == "hosting" and type(SilverfishProjectNativeGuestArmor) == "function" then
        for _, detail in ipairs(state.guests) do
            local name = detail.name
            local ok, pawn, save, worn = pcall(exact_worn, state, phase, name)
            if ok and pawn and save and worn then
                for _, slot in ipairs({"body", "head"}) do
                    local guid = worn[slot]
                    local invoke = project[slot]
                    local companion = worn[slot == "body" and "head" or "body"]
                    if guid and type(invoke) == "function" then
                        local prior = projected[slot][name]
                        if prior and prior.map == state.map and prior.pawn_address == pawn and prior.save_address == save
                            and prior.guid == guid and prior.companion == companion and (prior.ok or os.time() < prior.retry) then
                            current[slot][name] = prior
                        else
                            local called, result = pcall(function()
                                return native_report(state.map, name, guid, "project", invoke(pawn, save, guid))
                            end)
                            if called then
                                result.pawn_address, result.save_address = pawn, save
                                result.companion, result.retry, result.head = companion, os.time() + 1, slot == "head"
                                current[slot][name] = result
                            end
                        end
                    end
                end
            end
        end
    end
    projected = current
end

local function row(tag, report, status_row)
    local fields = {tag, report.map, report.pawn, report.guid, report.action, report.ok and 1 or 0,
        report.status, report.before, report.after, report.carry_before, report.carry_after, report.minimum, report.slash, report.acid}
    if report.profile then
        fields[15], fields[16], fields[17] = report.profile, report.replaced_profile, report.replaced_guid
    end
    if report.head then
        fields[18] = report.torso_profile
        fields[19], fields[20], fields[21] = report.combined_slash, report.combined_acid, report.combined_poison
        if type(report.voice_filter) == "boolean" then fields[22] = report.voice_filter and 1 or 0 end
    end
    return status_row(table.unpack(fields))
end

function armor.rows(state, phase, status_row)
    if not enabled or phase ~= "hosting" then return {} end
    local rows = {}
    if observation and observation.map == state.map then
        rows[#rows + 1] = row(observation.head and "native-guest-helmet" or "native-guest-armor", observation, status_row)
    end
    if disguise_observation and disguise_observation.map == state.map then
        local detail = disguise_observation
        local host, guest = detail.host, detail.guest
        rows[#rows + 1] = status_row(disguise_probe, detail.map, detail.pawn, detail.actor,
            host.configured, host.compromised and 1 or 0, host.recognized, host.relation,
            guest.configured, guest.compromised and 1 or 0, guest.recognized, guest.relation)
    end
    for _, effect in pairs(projected.body) do
        if effect.map == state.map then rows[#rows + 1] = row("native-guest-armor-projection", effect, status_row) end
    end
    for _, effect in pairs(projected.head) do
        if effect.map == state.map then rows[#rows + 1] = row("native-guest-helmet-projection", effect, status_row) end
    end
    return rows
end

return armor
