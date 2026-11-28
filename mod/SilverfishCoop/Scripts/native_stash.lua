local stash = {}
local content_paths = require("content_paths")
local guest_save = require("guest_save")
local host_save = require("native_host_save")
local statics_path = "/Script/Engine.Default__GameplayStatics"
local stash_class = "/Game/Classes/Actors/Stash_Master_BP.Stash_Master_BP_C"
local site_cache, construction_notifications, report, moved
local snapshots = {}
local snapshot_lifetime, max_snapshots = 45, 5
local page_size, snapshot_page_size, max_items = 16, 32, 4096

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

local function queue_site(cache, actor, now)
    if valid(actor) then
        cache.actors[actor:GetAddress()] = {actor = actor, expires = now + 2}
    end
end

local function watch_site_construction()
    if construction_notifications ~= nil then return end
    -- Construction is earlier than the stock BeginPlay that binds GameInstanceFP and initializes Stash_ID.
    construction_notifications = type(NotifyOnNewObject) == "function" and pcall(NotifyOnNewObject, stash_class,
        function(actor)
            if not site_cache then
                construction_notifications = nil
                return true
            end
            queue_site(site_cache, actor, os.clock())
        end)
end

local function seed_sites(cache, now)
    if (cache.seeded and construction_notifications) or now < cache.retry_at then return end
    cache.retry_at = now + 2
    cache.base = StaticFindObject(stash_class)
    if not valid(cache.base) then
        -- A derived actor cannot exist before the base class loads. Its construction will wake discovery.
        cache.seeded = construction_notifications
        return
    end
    local statics = StaticFindObject(statics_path)
    if not valid(statics) then return end
    local found = {}
    statics:GetAllActorsOfClass(cache.world, cache.base, found)
    if not construction_notifications then cache.actors = {} end
    for _, actor in ipairs(found.OutActors or found) do
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        queue_site(cache, actor, now)
    end
    cache.seeded = true
end

local function qualify_site(cache, candidate)
    local actor = candidate.actor
    local actor_world = actor:GetWorld()
    if not valid(actor_world) then return end
    if not same(actor_world, cache.world) or not actor:IsA(cache.base) or not actor:HasAuthority() then return false end
    local name = actor:GetFullName()
    local path = name:match("^%S+ (/Game/%S+)$")
    if not path or path:sub(1, #cache.prefix) ~= cache.prefix then return false end
    candidate.placed = true
    local id = identifier(actor.Stash_ID)
    if id and same(actor.GameInstanceFP, cache.instance) then
        local position = actor:K2_GetActorLocation()
        if position then return {id = id, name = name, actor = actor, position = position} end
    end
end

local function current_sites(cache)
    local now = os.clock()
    seed_sites(cache, now)
    if next(cache.actors) and not valid(cache.base) then cache.base = StaticFindObject(stash_class) end
    local sites = {}
    for address, candidate in pairs(cache.actors) do
        local actor = candidate.actor
        if not valid(actor) or actor:IsActorBeingDestroyed() then
            cache.actors[address] = nil
        else
            local site = candidate.site
            if not site and valid(cache.base) then
                site = qualify_site(cache, candidate)
                candidate.site = site
            end
            if site then
                -- Keep both actors enrolled so removing a duplicate can restore the remaining site.
                sites[site.id] = sites[site.id] == nil and site or false
            elseif site == false or not candidate.placed and now >= candidate.expires then
                cache.actors[address] = nil
            end
        end
    end
    return sites
end

local function selected_sites(state, phase)
    if phase ~= "hosting" or not state.listening or state.server ~= false
        or type(SilverfishReadNativeStashCensus) ~= "function" then
        if next(snapshots) then snapshots = {} end
        site_cache = nil
        return
    end
    local instance, pawn = host_save.canonical_host(state, phase)
    local save = valid(instance) and instance.SaveSlot_Ref
    if not valid(pawn) or not valid(save) or not same(pawn.Save, save) or type(save.CombineStashes) ~= "boolean" then
        if next(snapshots) then snapshots = {} end
        return
    end
    local combined = save.CombineStashes
    local context_changed = not site_cache or not same(site_cache.world, state.world)
        or site_cache.map ~= state.map or not same(site_cache.instance, instance)
    if next(snapshots) then
        local now = os.time()
        for address, snapshot in pairs(snapshots) do
            if context_changed or now >= snapshot.expires or snapshot.combined ~= combined then snapshots[address] = nil end
        end
    end
    if context_changed then
        local leaf = state.map:match("([^/]+)$")
        if not leaf then return end
        site_cache = {world = state.world, map = state.map, instance = instance, actors = {}, retry_at = 0,
            prefix = state.map .. "." .. leaf .. ":PersistentLevel."}
        watch_site_construction()
    end
    return current_sites(site_cache), pawn, combined, instance, save
end

local function close_to(pawn, actor)
    local origin, target = pawn:K2_GetActorLocation(), actor:K2_GetActorLocation()
    local reach = pawn.InteractionRange
    return origin and target and type(reach) == "number" and reach > 0 and reach <= 1000
        and (origin.X - target.X)^2 + (origin.Y - target.Y)^2 + (origin.Z - target.Z)^2 <= reach^2
end

local function participant(state, phase, host, name)
    if host:GetFullName() == name then return host, host.Save, "host" end
    return guest_save.exact_item_context(state, phase, name)
end

local function stash_context(state, phase, name, id)
    local available, host, combined = selected_sites(state, phase)
    local site = available and available[id]
    if not site or not valid(site.actor) or site.actor:IsActorBeingDestroyed()
        or identifier(site.actor.Stash_ID) ~= id or not same(site.actor:GetWorld(), state.world)
        or not site.actor:HasAuthority() or not same(site.actor.GameInstanceFP, site_cache.instance) then
        error("native_stash_exact_site_unavailable", 0)
    end
    local pawn, save, owner = participant(state, phase, host, name)
    if not valid(pawn) or pawn.Dead ~= false or not close_to(pawn, site.actor) then
        error("native_stash_participant_out_of_reach", 0)
    end
    return pawn, combined, host, save, owner, site.actor
end

local function census_context(state, phase, name, slot, id)
    if not slot or #slot ~= 39 or not slot:match("^SFCoop_[0-9a-f]+$") then
        error("native_stash_exact_site_unavailable", 0)
    end
    local pawn, combined, host = stash_context(state, phase, name, id)
    local statics = StaticFindObject(statics_path)
    local snapshot = valid(statics) and statics:LoadGameFromSlot(slot, 0)
    if not valid(snapshot) then error("native_stash_census_save_unavailable", 0) end
    return pawn, combined, snapshot, host
end

local function move(fields, state, phase)
    moved = nil
    local name, slot, site, item, direction, expected, extra, quantity, stack_size = table.unpack(fields, 3)
    local live = slot == "-"
    local reorder = direction == "reorder"
    local split = direction == "split-deposit" or direction == "split-withdraw"
    local deposit = direction == "deposit" or direction == "split-deposit"
    local transfer
    if reorder then transfer = SilverfishReorderLiveNativeStashItem
    elseif split then transfer = SilverfishSplitMergeLiveNativeStashItem or SilverfishSplitLiveNativeStashItem
    else transfer = live and SilverfishMoveLiveNativeStashItem or SilverfishMoveNativeStashItem end
    if type(transfer) ~= "function" or (expected ~= "0" and expected ~= "1")
        or (reorder and (not live or #fields ~= 9))
        or (split and (not live or #fields ~= 11 or not math.tointeger(tonumber(quantity))
            or not math.tointeger(tonumber(stack_size))))
        or (not reorder and not split and (#fields ~= 8 or (not deposit and direction ~= "withdraw"))) then
        error("native_stash_move_unavailable", 0)
    end
    local pawn, combined, snapshot, host
    if live then
        pawn, combined, host = stash_context(state, phase, name, site)
    else
        pawn, combined, snapshot, host = census_context(state, phase, name, slot, site)
    end
    if combined ~= (expected == "1") then error("native_stash_move_setting_changed", 0) end
    if same(pawn, host) then
        if reorder or split then error("native_stash_move_unavailable", 0) end
        local controller = state.local_pawn and state.local_pawn.controller_object
        if not valid(controller) or controller.MenuStack:GetArrayNum() ~= 0 then
            error("native_stash_move_stock_menu_open", 0)
        end
    end
    local ok, status, class, moved_quantity, remaining, destination, destination_before, destination_after
    if reorder then ok, status, class, moved_quantity = transfer(pawn:GetAddress(), 0, combined, site, item, false, extra)
    elseif split then ok, status, class, moved_quantity, remaining, destination, destination_before, destination_after =
        transfer(pawn:GetAddress(), 0, combined, site, item, deposit, extra,
            math.tointeger(tonumber(quantity)), math.tointeger(tonumber(stack_size)))
    else ok, status, class, moved_quantity = transfer(pawn:GetAddress(), live and 0 or snapshot:GetAddress(),
        combined, site, item, deposit) end
    if ok ~= true then error(tostring(status), 0) end
    snapshots[pawn:GetAddress()] = nil
    local merged = status == "merged"
    if (status ~= "moved" and not (split and merged)) or not content_paths.item_class(class, 512)
        or math.type(moved_quantity) ~= "integer" or moved_quantity < 1
        or (split and (moved_quantity ~= tonumber(quantity) or remaining ~= tonumber(stack_size) - moved_quantity
            or (not merged and destination ~= extra)))
        or (merged and (type(destination) ~= "string" or #destination ~= 32 or not destination:match("^[0-9A-F]+$")
            or not destination:find("[^0]") or destination == item or destination == extra
            or math.type(destination_before) ~= "integer" or destination_before < 1
            or destination_after ~= destination_before + moved_quantity or destination_after > 2147483647)) then
        error("mutation_unverified_native_stash_receipt", 0)
    end
    moved = {map = state.map, pawn = name, slot = slot, site = site, item = item,
        direction = direction, combined = combined, class = class, quantity = moved_quantity,
        before = reorder and extra or nil, remaining = remaining, destination = destination,
        destination_before = destination_before, destination_after = destination_after}
    return "stock stash item moved"
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

local function census_items(reader, target, combined, id, live, offset, count)
    if type(reader) ~= "function" then error("native_stash_census_unavailable", 0) end
    local ok, status, source, total = reader(target:GetAddress(), combined, id)
    if ok ~= true or status ~= "complete" or type(source) ~= "string" or math.type(total) ~= "integer"
        or total < 0 or total > max_items then error(tostring(status), 0) end
    local items, seen = {}, 0
    for line in source:gmatch("[^\n]+") do
        seen = seen + 1
        if seen > offset and seen <= offset + count then
            local item = {}
            for value in line:gmatch("[^\t]+") do item[#item + 1] = value end
            if #item ~= (live and 13 or 9) or item[2] ~= "stash" then error("native_stash_census_item_invalid", 0) end
            items[#items + 1] = item
        end
    end
    if seen ~= total then error("native_stash_census_count_changed", 0) end
    return items, total
end

local function remember_snapshot(address, snapshot)
    if not snapshots[address] then
        local count, oldest = 0, nil
        for candidate, previous in pairs(snapshots) do
            count = count + 1
            if not oldest or previous.expires < snapshots[oldest].expires then oldest = candidate end
        end
        if count >= max_snapshots then snapshots[oldest] = nil end
    end
    snapshots[address] = snapshot
end

local function snapshot_page(state, phase, name, id, offset, token)
    local pawn, combined, _, save, owner, actor = stash_context(state, phase, name, id)
    if type(owner) ~= "string" or not same(pawn.Save, save) then error("native_stash_census_save_unavailable", 0) end
    local address = pawn:GetAddress()
    local snapshot = snapshots[address]
    if not snapshot or snapshot.token ~= token or snapshot.name ~= name or snapshot.owner ~= owner
        or snapshot.map ~= state.map or not same(snapshot.world, state.world) or not same(snapshot.pawn, pawn)
        or not same(snapshot.save, save) or snapshot.site ~= id or not same(snapshot.actor, actor)
        or snapshot.combined ~= combined then
        if offset ~= 0 then error("native_stash_census_snapshot_unavailable", 0) end
        -- Freeze one verified stock census so that each page reads the same order and counts.
        local items = census_items(SilverfishReadLiveNativeStashCensus, pawn, combined, id, true, 0, max_items)
        snapshot = {token = token, name = name, owner = owner, world = state.world, map = state.map,
            pawn = pawn, save = save, site = id, actor = actor, combined = combined,
            expires = os.time() + snapshot_lifetime, items = items}
        remember_snapshot(address, snapshot)
    else
        snapshot.expires = os.time() + snapshot_lifetime
    end
    local items, total = {}, #snapshot.items
    for index = offset + 1, math.min(offset + snapshot_page_size, total) do
        items[#items + 1] = snapshot.items[index]
    end
    return items, total, combined
end

function stash.command(fields, state, phase)
    if fields[2] == "native-stash-move" then return move(fields, state, phase) end
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
    local name, slot, id, count, token = fields[3], fields[4], fields[5], fields[6], fields[7]
    local offset = math.tointeger(tonumber(count))
    local live = slot == "-"
    if (#fields ~= 6 and #fields ~= 7) or not offset or offset < 0 or offset > max_items
        or (#fields == 7 and (not live or type(token) ~= "string" or #token ~= 32 or not token:match("^[0-9a-f]+$"))) then
        error("native_stash_exact_site_unavailable", 0)
    end
    local items, total, combined
    if token then
        items, total, combined = snapshot_page(state, phase, name, id, offset, token)
    elseif live then
        local pawn
        pawn, combined = stash_context(state, phase, name, id)
        items, total = census_items(SilverfishReadLiveNativeStashCensus, pawn, combined, id, true, offset, page_size)
    else
        local _, saved_combined, snapshot = census_context(state, phase, name, slot, id)
        combined = saved_combined
        items, total = census_items(SilverfishReadNativeStashCensus, snapshot, combined, id, false, offset, page_size)
    end
    report = {map = state.map, pawn = name, slot = slot, site = id, combined = combined,
        total = total, offset = offset, items = items}
    return "isolated stock stash census verified"
end

function stash.rows(state, phase, row)
    local available, pawn, combined, instance, save = selected_sites(state, phase)
    if not available then return {} end
    local rows = {row("native-stash-setting", state.map, combined and 1 or 0)}
    if type(SilverfishReadLiveNativeStashCensus) == "function" then
        rows[#rows + 1] = row("native-stash-live-census", 1)
        rows[#rows + 1] = row("native-stash-census-snapshot", 1)
    end
    if type(SilverfishMoveLiveNativeStashItem) == "function" then
        rows[#rows + 1] = row("native-stash-live-move", 1)
    end
    if type(SilverfishReorderLiveNativeStashItem) == "function" then
        rows[#rows + 1] = row("native-stash-live-reorder", 1)
    end
    if type(SilverfishSplitLiveNativeStashItem) == "function" then
        rows[#rows + 1] = row("native-stash-live-split", 1)
    end
    if type(SilverfishSplitMergeLiveNativeStashItem) == "function" then
        rows[#rows + 1] = row("native-stash-live-split-merge", 1)
    end
    for id, site in pairs(available) do
        if site then
            local position = site.position
            rows[#rows + 1] = row("native-stash-site", state.map, id, site.name, position.X, position.Y, position.Z)
        end
    end
    if report and report.map == state.map then
        rows[#rows + 1] = row("native-stash-census", report.map, report.pawn, report.slot, report.site,
            report.combined and 1 or 0, report.total, report.offset, #report.items)
        for _, item in ipairs(report.items) do
            rows[#rows + 1] = row("native-stash-census-item", table.unpack(item))
        end
    end
    if moved and moved.map == state.map then
        local fields = {moved.map, moved.pawn, moved.slot, moved.site, moved.item,
            moved.direction, moved.combined and 1 or 0, moved.class, moved.quantity, moved.before}
        if moved.destination then
            fields[10], fields[11] = moved.remaining, moved.destination
            fields[12], fields[13] = moved.destination_before, moved.destination_after
        end
        rows[#rows + 1] = row("native-stash-move", table.unpack(fields))
    end
    return rows, {state = state, world = state.world, map = state.map, controller = state.controller,
        instance = instance, pawn = pawn, save = save}
end

function stash.reset()
    report, moved = nil, nil
end

return stash
