local mushrooms = {}
local transport = require("native_transport")
local guest_save = require("guest_save")
local input = require("native_authored_pickup_input_fixture")

local patch_class = "/Game/Assets/PCG_Brushes/MushroomPatch_BP.MushroomPatch_BP_C"
local item_class = "/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"
local saved_children = "Mushrooms_7_FAE4078E4D8D1EC7433B08A841D5B914"
local saved_child_guid = "ID_7_18CBAA0D44A4F0AA53DF3C9CF2E59420"
local saved_child_class = "Class_6_B3FAD7924D55642D659E8C891A0A53C6"
local saved_child_transform = "Transform_8_BD0C0F504E8E7DC44D04E59C402D9537"
local guid_words = {"A", "B", "C", "D"}
local scope, world, patches, samples, proof, next_sample, sweep
local status_row, capability_row
local title_fixtures = os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"
local fixtures = os.getenv("SILVERFISH_COOP_HEADLESS") == "1" and
    (title_fixtures or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1")

local function valid(object) return object and object:IsValid() end
local function same(left, right)
    return valid(left) and valid(right) and left:GetAddress() == right:GetAddress()
end

local function actor_path(actor)
    return actor:GetFullName():match("^%S+ (/Game/[%w_./:%-]+)$")
end

local function qualified(state, actor, class, exact)
    return valid(class) and valid(actor) and actor:IsA(class) and (not exact or same(actor:GetClass(), class))
        and actor:HasAuthority() and not actor:IsActorBeingDestroyed()
        and same(actor:GetWorld(), state.world) and same(actor:GetOuter(), state.world.PersistentLevel)
end

local function guid_hex(id)
    if not id then return end
    local a, b, c, d = math.tointeger(id.A), math.tointeger(id.B), math.tointeger(id.C), math.tointeger(id.D)
    if not a or not b or not c or not d or (a | b | c | d) == 0 then return end
    return string.format("%08X%08X%08X%08X", a & 0xffffffff, b & 0xffffffff, c & 0xffffffff, d & 0xffffffff)
end

local function patch_children(patch, visit)
    local map = patch.SpawnedShrooms
    if not map then return end
    local seen = 0
    map:ForEach(function(key, value)
        seen = seen + 1
        local id = value:get()
        if seen > 128 or visit(key:get(), guid_hex(id), id) == true then return true end
    end)
end

local function exact_source(state, path, guid)
    local patch = patches and patches[path]
    local base = StaticFindObject(item_class)
    if not valid(base) or not qualified(state, patch, StaticFindObject(patch_class), true) then return end
    local source
    patch_children(patch, function(child, id)
        if id ~= guid or not qualified(state, child, base, false) then return end
        source = child
        return true
    end)
    return source, patch
end

local function saved(patch, expected)
    local list = patch.PatchData and patch.PatchData[saved_children]
    local count = list and list:GetArrayNum()
    if not count or count > 128 then return end
    local found = 0
    for index = 1, count do
        if guid_hex(list[index][saved_child_guid]) == expected then found = found + 1 end
    end
    return found
end

local function assign_patch_ids(state, patch, pending, used)
    local instance = state.world.OwningGameInstance
    local list = patch.PatchData and patch.PatchData[saved_children]
    local count = list and list:GetArrayNum()
    if not count or count == 0 or count > 128 or not valid(instance) or not same(patch.Save, instance.SaveSlot_Ref) then return end
    local library = StaticFindObject("/Script/Engine.Default__KismetGuidLibrary")
    if not valid(library) then return end
    local matched, saved_by_class = {}, {}
    for index = 1, count do
        local stored = list[index]
        if not guid_hex(stored[saved_child_guid]) then
            local item_class = stored[saved_child_class]
            local kind = item_class and item_class:GetObjectID():GetAssetPathName():ToString()
            local position = stored[saved_child_transform].Translation
            if kind and position then
                local group = saved_by_class[kind]
                if not group then
                    group = {}
                    saved_by_class[kind] = group
                end
                group[#group + 1] = {index = index, X = position.X, Y = position.Y, Z = position.Z}
            end
        end
    end
    for _, entry in ipairs(pending) do
        local item, selected = entry.item, nil
        local name = actor_path(item:GetClass())
        local point = item:K2_GetActorLocation()
        local candidates = name and saved_by_class[name]
        if not candidates then return end
        for _, position in ipairs(candidates) do
            if (point.X - position.X)^2 + (point.Y - position.Y)^2 + (point.Z - position.Z)^2 <= 0.05^2 then
                if selected or matched[position.index] then return end
                selected = position.index
            end
        end
        if not selected then return end
        local id = library:NewGuid()
        local value = guid_hex(id)
        if not value or used[value] then return end
        matched[selected], used[value] = true, true
        entry.saved_id, entry.new_id, entry.hex = list[selected][saved_child_guid], id, value
    end

    -- The unmodified grow graph writes zero to each child. Keep the engine's own
    -- two copies paired so its OnDestroyed callback removes the right saved row.
    for _, entry in ipairs(pending) do
        for _, word in ipairs(guid_words) do
            entry.live_id[word] = entry.new_id[word]
            entry.saved_id[word] = entry.new_id[word]
        end
    end
    local counts = {}
    for index = 1, count do
        local id = guid_hex(list[index][saved_child_guid])
        if id then counts[id] = (counts[id] or 0) + 1 end
    end
    for _, entry in ipairs(pending) do
        if guid_hex(entry.live_id) ~= entry.hex or counts[entry.hex] ~= 1 then
            for _, rollback in ipairs(pending) do
                for _, word in ipairs(guid_words) do rollback.live_id[word], rollback.saved_id[word] = 0, 0 end
            end
            return
        end
    end
    patch:SavePatch()
    return true
end

local function census(state)
    local class = StaticFindObject(patch_class)
    local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    if not valid(statics) or not valid(class) then return end
    local out = {}
    statics:GetAllActorsOfClass(state.world, class, out)
    local found = {}
    for _, actor in ipairs(out.OutActors or out) do
        if actor and type(actor.type) == "function" and actor:type() == "RemoteUnrealParam" then actor = actor:get() end
        local path = qualified(state, actor, class, true) and actor_path(actor)
        if path then found[path] = actor end
    end
    patches = found
end

local function begin_sample(state, radius)
    local base = StaticFindObject(item_class)
    if not valid(base) then return end
    local roster, points = {}, {}
    for _, member in ipairs(state.pawns) do
        if valid(member.pawn) and member.pawn.Dead == false then
            roster[#roster + 1] = {X = member.x, Y = member.y, Z = member.z}
            points[#points + 1], points[#points + 2], points[#points + 3] = member.x, member.y, member.z
        end
    end
    return {state = state, base = base, roster = roster, points = points, world_address = state.world:GetAddress(),
        radius_squared = radius^2, values = {}}
end

local function sample_native(search, path)
    local scan = SilverfishSampleNativeMushroomRows
    if type(scan) ~= "function" then return false end
    local rows = scan(search.world_address, path, search.state.map, search.radius_squared, table.unpack(search.points))
    if type(rows) ~= "string" then return false end
    for distance, row, guid, child_path in rows:gmatch(
        "([^\t\n]+)\t(native%-mushroom\t[^\t\n]+\t[^\t\n]+\t([^\t\n]+)\t([^\t\n]+)\t[^\n]+)\n") do
        local actor = StaticFindObject(child_path)
        if valid(actor) then
            search.values[#search.values + 1] = {distance = tonumber(distance), row = row,
                path = path, guid = guid, actor = actor}
        end
    end
    return true
end

local function sample_lua(search, path, patch)
    if not valid(patch) or patch:IsActorBeingDestroyed() then return end

    local function collect(child, guid)
        local position = child:K2_GetActorLocation()
        local distance = math.huge
        for _, point in ipairs(search.roster) do
            distance = math.min(distance, (position.X - point.X)^2 + (position.Y - point.Y)^2 + (position.Z - point.Z)^2)
        end
        if distance > search.radius_squared then return end
        local class = child:GetClass():GetFullName():match("^%S+ (/Game/Classes/[%w_./%-]+)$")
        local child_path = actor_path(child)
        if not class or not child_path then return end
        local rotation, scale = child:K2_GetActorRotation(), child:GetActorScale3D()
        local row = status_row("native-mushroom", search.state.map, path, guid, child_path, class, child.Ammo,
            position.X, position.Y, position.Z, rotation.Pitch, rotation.Yaw, rotation.Roll, scale.X, scale.Y, scale.Z)
        search.values[#search.values + 1] = {distance = distance, row = row, path = path, guid = guid, actor = child}
    end

    local pending, used = {}, {}
    patch_children(patch, function(child, guid, id)
        if guid then used[guid] = true end
        if not qualified(search.state, child, search.base, false) then return end
        if guid then
            collect(child, guid)
        elseif id and id.A == 0 and id.B == 0 and id.C == 0 and id.D == 0 then
            pending[#pending + 1] = {item = child, live_id = id}
        end
    end)
    if #pending > 0 and assign_patch_ids(search.state, patch, pending, used) then
        for _, entry in ipairs(pending) do collect(entry.item, entry.hex) end
    end
end

local function sample_patch(search, path, patch)
    if not sample_native(search, path) then sample_lua(search, path, patch) end
end

local function publish_sample(search)
    table.sort(search.values, function(left, right) return left.distance < right.distance end)
    samples = search.values
end

local function sample_fixture(state)
    local search = begin_sample(state, 2e7)
    if not search then return end
    sweep = nil
    for path, patch in pairs(patches or {}) do sample_patch(search, path, patch) end
    publish_sample(search)
end

function mushrooms.start(row)
    status_row = row
    capability_row = row("native-mushroom-capability", 1)
end

function mushrooms.sync(state, phase)
    if phase ~= "hosting" or not state.listening or state.server ~= false or not valid(state.world)
        or type(SilverfishCaptureNativeMushroom) ~= "function" then
        scope, world, patches, samples, proof, next_sample, sweep = nil, nil, nil, nil, nil, nil, nil
        return
    end
    if not scope or scope.map ~= state.map or not same(world, state.world) then
        world, patches, samples, proof, next_sample, sweep = state.world, nil, {}, nil, 0, nil
    end
    scope = state
end

function mushrooms.update(current_world)
    if not scope then return end
    local now = os.clock()
    if not sweep and now < next_sample then return end
    if not same(world, current_world()) then
        scope, sweep = nil, nil
        return
    end
    if not sweep then
        next_sample = now + 0.3
        if not patches then
            census(scope)
            if patches and next(patches) then next_sample = now end
            return
        end
        if not next(patches) then return end
        sweep = begin_sample(scope, 2300)
        if not sweep then return end
    end
    local search = sweep
    for _ = 1, 8 do
        local path, patch = next(patches, search.cursor)
        if not path then
            publish_sample(search)
            sweep = nil
            return
        end
        search.cursor = path
        sample_patch(search, path, patch)
        if os.clock() - now >= 0.002 then break end
    end
end

local function remove_from_sample(path, guid)
    sweep = nil
    for index = #samples, 1, -1 do
        local entry = samples[index]
        if entry.path == path and entry.guid == guid then table.remove(samples, index) end
    end
end

function mushrooms.rows(state, phase, row)
    if not state.listening or not valid(state.world) then return {} end
    if phase == "connected" and state.server == true and type(SilverfishReadNativeMushroomSkips) == "function" then
        return {row("native-mushroom-suppression", SilverfishReadNativeMushroomSkips(state.world:GetAddress()))}
    end
    if phase ~= "hosting" or state.server ~= false or not scope or scope.map ~= state.map
        or not same(world, state.world) then return {} end
    local rows = {capability_row}
    for _, entry in ipairs(samples) do
        -- Stock pickup can destroy an actor while the next sample is assembling.
        if valid(entry.actor) and not entry.actor:IsActorBeingDestroyed() then
            rows[#rows + 1] = entry.row
            if #rows == 129 then break end
        end
    end
    if proof then rows[#rows + 1] = proof end
    return rows
end

local function claim(fields, state, phase)
    proof = nil
    local path, guid, requested = fields[3], fields[4], fields[5]
    local host, fresh = transport.scoped_host(state, phase)
    if #fields ~= 5 or not fresh or not host or not same(world, state.world) then
        error("native_mushroom_claim_scope_unavailable", 0)
    end
    local source, patch = exact_source(state, path, guid)
    if not source or saved(patch, guid) ~= 1 then error("native_mushroom_source_unavailable", 0) end
    local ready, pawn, save = pcall(guest_save.exact_item_context, state, phase, requested)
    if not ready or not valid(pawn) or not valid(save) or not input.stock_item_sight(pawn, source) then
        error("native_mushroom_claimant_out_of_reach_or_unavailable", 0)
    end
    local api = StaticFindObject("/Script/Engine.Default__GameplayStatics")
    local save_class = StaticFindObject("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")
    local stock = host.pawn.Save
    local capture, transfer = SilverfishCaptureNativeMushroom, SilverfishClaimNativeLootEscrow
    if not valid(api) or not valid(save_class) or not valid(stock) or not same(patch.Save, stock)
        or type(capture) ~= "function" or type(transfer) ~= "function" then
        error("native_mushroom_claim_scope_unavailable", 0)
    end
    local scratch = api:CreateSaveGameObject(save_class)
    if not valid(scratch) then error("native_mushroom_claim_scope_unavailable", 0) end
    local child_path = actor_path(source)
    local ok, status, item_guid, units = capture(pawn:GetAddress(), save:GetAddress(), stock:GetAddress(), scratch:GetAddress(),
        path, child_path, guid)
    if not ok then error("native_mushroom_capture:" .. tostring(status), 0) end
    local accepted, outcome, amount, before, after, inventory_guid = transfer(pawn:GetAddress(), save:GetAddress(),
        stock:GetAddress(), scratch:GetAddress(), item_guid)
    if not accepted then
        local reason = tostring(outcome)
        if reason:match("^native_loot_[%w_]+$") then reason = "native_mushroom_transfer:" .. reason end
        error(reason, 0)
    end
    if outcome ~= "claimed" and outcome ~= "merged" then
        error("mutation_unverified_native_mushroom_transfer", 0)
    end
    -- Stock OnDestroyed removes the exact child from SpawnedShrooms and saves the patch's regrowth time.
    source:K2_DestroyActor()
    if exact_source(state, path, guid) or saved(patch, guid) ~= 0 then
        error("mutation_unverified_native_mushroom_removal", 0)
    end
    proof = status_row("native-mushroom-claim", state.map, path, guid, requested, child_path, item_guid, units,
        outcome, amount or 0, before or 0, after or 0, inventory_guid or "-")
    remove_from_sample(path, guid)
    next_sample = 0
    return "stock mushroom harvested from the host patch"
end

local function fixture(fields, state, phase)
    local command, path, guid, requested = fields[2], fields[3], fields[4], fields[5]
    local source, patch = exact_source(state, path, guid)
    local pawn
    for _, member in ipairs(state.pawns) do
        if member.name == requested then pawn = member.pawn break end
    end
    if not fixtures or #fields ~= 5 or phase ~= "hosting" or not source or not valid(pawn) then
        error("native_mushroom_fixture_unavailable", 0)
    end
    if command == "native-mushroom-approach-fixture" then
        input.approach_world_loot(state, phase, pawn, source, source.Box, title_fixtures)
        return "stock mushroom sight verified"
    end
    local name = "native_mushroom_fixture_interaction"
    return input.press_world_loot(state, pawn, source, source.Box, function()
        local ok, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
        if exact_source(state, path, guid) or saved(patch, guid) ~= 0 then error(name .. ":" .. tostring(status), 0) end
        remove_from_sample(path, guid)
        next_sample = 0
        return "stock player harvested the exact mushroom"
    end)
end

local function stage_empty_patch(state, pawn)
    local canonical = pawn.Save
    if not valid(canonical) then return "canonical_save_unavailable" end
    local origin = pawn:K2_GetActorLocation()
    local empty, bound, live_count = {}, 0, 0
    for _, patch in pairs(patches or {}) do
        local list = valid(patch) and patch.PatchData and patch.PatchData[saved_children]
        if same(patch.Save, canonical) then bound = bound + 1 end
        if list then live_count = live_count + list:GetArrayNum() end
        if list and list:GetArrayNum() == 0 and same(patch.Save, canonical) then
            local position = patch:K2_GetActorLocation()
            empty[#empty + 1] = {patch, (position.X - origin.X)^2 + (position.Y - origin.Y)^2 + (position.Z - origin.Z)^2}
        end
    end
    table.sort(empty, function(left, right) return left[2] < right[2] end)
    local staged, stock_grown = 0, 0
    for index = 1, math.min(6, #empty) do
        local patch = empty[index][1]
        local children = 0
        patch_children(patch, function() children = children + 1 end)
        if children == 0 then
            -- A fresh fixture starts before natural growth. Shorten the empty patch's growth delay for one stock pass.
            local field = "DaysToGrow_Min/Max"
            local growth = patch[field]
            local original = {X = growth.X, Y = growth.Y}
            patch[field] = {X = 0.1, Y = 0.2}
            if math.abs(patch[field].X - 0.1) < 0.01 then staged = staged + 1 end
            local ok, failure = pcall(function() patch:ReceiveBeginPlay() end)
            patch[field] = original
            if not ok then error(failure, 0) end
            stock_grown = stock_grown + patch.PatchData[saved_children]:GetArrayNum()
            sample_fixture(state)
            if #samples >= 3 then return end
        end
    end
    local diagnostic = ""
    for _, entry in ipairs(empty) do
        patch_children(entry[1], function(child, id, raw)
            if valid(child) then
                diagnostic = string.format(":seen=%s:class=%s:authority=%s:level=%s:guid=%s:%s,%s,%s,%s", child:GetFullName(),
                    child:GetClass():GetFullName(), tostring(child:HasAuthority()), tostring(same(child:GetOuter(), state.world.PersistentLevel)),
                    tostring(id), tostring(raw.A), tostring(raw.B), tostring(raw.C), tostring(raw.D))
                return true
            end
        end)
        if diagnostic ~= "" then break end
    end
    return string.format("bound=%d:empty=%d:saved=%d:staged=%d:grown=%d%s", bound, #empty, live_count, staged, stock_grown, diagnostic)
end

function mushrooms.command(fields, state, phase)
    if fields[2] == "native-mushroom-claim" then return claim(fields, state, phase) end
    if fields[2] == "native-mushroom-locate-fixture" then
        local host, fresh = transport.scoped_host(state, phase)
        if #fields ~= 2 or not fixtures or not fresh or not host or not same(world, state.world) then
            error("native_mushroom_fixture_unavailable", 0)
        end
        sample_fixture(state)
        local detail = #samples == 0 and stage_empty_patch(state, host.pawn)
        local paths = {}
        for path in pairs(patches or {}) do paths[#paths + 1] = path end
        local nearest = samples[1]
        if not nearest then error("native_mushroom_fixture_no_growth:patches=" .. #paths .. ":" .. tostring(detail), 0) end
        local source = exact_source(state, nearest.path, nearest.guid)
        input.approach_world_loot(state, phase, host.pawn, source, source.Box, title_fixtures)
        next_sample = 0
        return "stock player found the nearest populated mushroom patch"
    end
    if fields[2] == "native-mushroom-approach-fixture" or fields[2] == "native-mushroom-harvest-fixture" then
        return fixture(fields, state, phase)
    end
end

return mushrooms
