local stash = {}
local guest_save = require("guest_save")
local host_save = require("native_host_save")
local statics_path = "/Script/Engine.Default__GameplayStatics"
local stash_class = "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C"
local world, site_time, sites, report

local function valid(object)
    return object and object:IsValid()
end

local function same(first, second)
    return valid(first) and valid(second) and first:GetAddress() == second:GetAddress()
end

local function identifier(value)
    if not value then return end
    local a, b, c, d = math.tointeger(value.A), math.tointeger(value.B), math.tointeger(value.C), math.tointeger(value.D)
    if not a or not b or not c or not d then return end
    local id = string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
    if id:find("[^0]") then return id end
end

local function selected_sites(state, phase)
    if phase ~= "hosting" or not state.listening or state.server ~= false
        or type(SilverfishReadNativeStashCensus) ~= "function" then return end
    local instance, pawn = host_save.canonical_host(state, phase)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(pawn) or not valid(save) or not same(pawn.Save, save) or type(save.CombineStashes) ~= "boolean" then return end
    local clock = os.time()
    if not same(world, state.world) or site_time ~= clock then
        local base = StaticFindObject(stash_class)
        local leaf = state.map:match("([^/]+)$")
        sites = {}
        if valid(base) and leaf then
            local prefix = state.map .. "." .. leaf .. ":PersistentLevel."
            for _, actor in ipairs(FindAllOf("Stash_Master_BP_C") or {}) do
                if valid(actor) and actor:IsA(base) and same(actor:GetWorld(), state.world)
                    and actor:HasAuthority() and not actor:IsActorBeingDestroyed() and same(actor.GameInstanceFP, instance) then
                    local name = actor:GetFullName()
                    local path = name:match("^%S+ (/Game/%S+)$")
                    local id = identifier(actor.Stash_ID)
                    if id and path and path:sub(1, #prefix) == prefix then
                        sites[id] = sites[id] == nil and {id = id, name = name, actor = actor} or false
                    end
                end
            end
        end
        world, site_time = state.world, clock
    end
    return sites, pawn, save.CombineStashes
end

local function close_to(pawn, actor)
    local origin, target = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()
    local reach = pawn.InteractionRange
    return origin and target and type(reach) == "number" and reach > 0 and reach <= 1000
        and (origin.X - target.X)^2 + (origin.Y - target.Y)^2 + (origin.Z - target.Z)^2 <= reach^2
end

local function participant(state, phase, host, name)
    if host:GetFullName() == name then return host end
    return guest_save.exact_item_context(state, phase, name)
end

local function approach_fixture(state, phase, name, id)
    local available, host = selected_sites(state, phase)
    local site = available and available[id]
    local pawn = site and participant(state, phase, host, name)
    local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
    if not site or not valid(site.actor) or not valid(pawn) or pawn.Dead ~= false or not valid(trace) then
        error("native_stash_approach_fixture_unavailable", 0)
    end
    local center, facing = site.actor:K2_GetActorLocation(), pawn:K2_GetActorRotation()
    local capsule = pawn.CapsuleComponent
    local height = valid(capsule) and capsule:GetScaledCapsuleHalfHeight()
    local color = {R = 0, G = 0, B = 0, A = 1}
    if not center or not facing or type(height) ~= "number" or height <= 0 or height > 150 then
        error("native_stash_approach_fixture_cannot_measure_floor", 0)
    end
    for _, offset in ipairs({{120, 0}, {-120, 0}, {0, 120}, {0, -120}, {165, 0}, {0, 165}}) do
        local x, y = center.X + offset[1], center.Y + offset[2]
        local hit = {}
        local floor = trace:LineTraceSingle(pawn, {X = x, Y = y, Z = center.Z + 160},
            {X = x, Y = y, Z = center.Z - 170}, 0, false, {pawn, site.actor}, 0, hit, true, color, color, 0)
        hit = hit.OutHit or hit
        if floor == true and hit.ImpactPoint and hit.ImpactNormal and hit.ImpactNormal.Z >= .7
            and pawn:K2_TeleportTo({X = x, Y = y, Z = hit.ImpactPoint.Z + height + 3}, facing)
            and close_to(pawn, site.actor) then
            pawn:ForceNetUpdate()
            return "stock stash fixture is within interaction range"
        end
    end
    error("native_stash_approach_fixture_no_floor", 0)
end

function stash.command(fields, state, phase)
    if fields[2] == "native-stash-approach-fixture" or fields[2] == "native-stash-setting-fixture" then
        if phase ~= "hosting" or os.getenv("SILVERFISH_COOP_HEADLESS") ~= "1"
            or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then
            error("native_stash_fixture_unavailable", 0)
        end
        if fields[2] == "native-stash-approach-fixture" and #fields == 4 then
            return approach_fixture(state, phase, fields[3], fields[4])
        end
        local instance = host_save.canonical_host(state, phase)
        if fields[2] ~= "native-stash-setting-fixture" or #fields ~= 3 or not valid(instance)
            or (fields[3] ~= "0" and fields[3] ~= "1") or not valid(instance.SaveSlot_Ref) then
            error("native_stash_setting_fixture_unavailable", 0)
        end
        instance.SaveSlot_Ref.CombineStashes = fields[3] == "1"
        return "stock stash setting set in isolated fixture"
    end
    if fields[2] ~= "native-stash-census" then return end
    local name, slot, id, count = fields[3], fields[4], fields[5], fields[6]
    local offset = math.tointeger(tonumber(count))
    local available, host, combined = selected_sites(state, phase)
    local site = available and available[id]
    if #fields ~= 6 or not site or not valid(site.actor) or site.actor:IsActorBeingDestroyed()
        or identifier(site.actor.Stash_ID) ~= id or not same(site.actor:GetWorld(), state.world)
        or not slot or #slot ~= 39 or not slot:match("^SFCoop_[0-9a-f]+$") or not offset or offset < 0 or offset > 4096 then
        error("native_stash_exact_site_unavailable", 0)
    end
    local pawn = participant(state, phase, host, name)
    if not valid(pawn) or pawn.Dead ~= false or not close_to(pawn, site.actor) then
        error("native_stash_participant_out_of_reach", 0)
    end
    local statics = StaticFindObject(statics_path)
    local snapshot = valid(statics) and statics:LoadGameFromSlot(slot, 0)
    if not valid(snapshot) then error("native_stash_census_save_unavailable", 0) end
    local ok, status, source, total = SilverfishReadNativeStashCensus(snapshot:GetAddress(), combined, id)
    if ok ~= true or status ~= "complete" or type(source) ~= "string" or math.type(total) ~= "integer"
        or total < 0 or total > 4096 then error(tostring(status), 0) end
    local items, seen = {}, 0
    for line in source:gmatch("[^\n]+") do
        seen = seen + 1
        if seen > offset and seen <= offset + 16 then
            local item = {}
            for value in line:gmatch("[^\t]+") do item[#item + 1] = value end
            if #item ~= 9 or item[2] ~= "stash" then error("native_stash_census_item_invalid", 0) end
            items[#items + 1] = item
        end
    end
    if seen ~= total then error("native_stash_census_count_changed", 0) end
    report = {map = state.map, pawn = name, slot = slot, site = id, combined = combined,
        total = total, offset = offset, items = items}
    return "isolated stock stash census verified"
end

function stash.rows(state, phase, row)
    local available, _, combined = selected_sites(state, phase)
    if not available then return {} end
    local rows = {row("native-stash-setting", state.map, combined and 1 or 0)}
    for id, site in pairs(available) do
        if site and valid(site.actor) and not site.actor:IsActorBeingDestroyed() then
            local position = site.actor:K2_GetActorLocation()
            if position then
                rows[#rows + 1] = row("native-stash-site", state.map, id, site.name, position.X, position.Y, position.Z)
            end
        end
    end
    if report and report.map == state.map then
        rows[#rows + 1] = row("native-stash-census", report.map, report.pawn, report.slot, report.site,
            report.combined and 1 or 0, report.total, report.offset, #report.items)
        for _, item in ipairs(report.items) do
            rows[#rows + 1] = row("native-stash-census-item", table.unpack(item))
        end
    end
    return rows
end

function stash.reset()
    world, site_time, sites, report = nil, nil, nil, nil
end

return stash
