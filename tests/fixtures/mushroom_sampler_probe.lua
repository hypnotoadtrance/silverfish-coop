local function compare_mushroom_samplers(fields, state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    local path = fields[3]
    local patch = patches and patches[path]
    if not fixtures or #fields ~= 3 or not fresh or not host or not valid(patch)
        or not same(world, state.world) then error("mushroom_sampler_fixture_unavailable", 0) end

    local search = assert(begin_sample(state, 2e7))
    local scan_rows, scan_raw = SilverfishSampleNativeMushroomRows, SilverfishSampleNativeMushroomPatchRaw
    local direct = type(scan_rows) == "function"
    if direct then
        assert(type(scan_raw) == "function", "Expected the previous raw mushroom sampler for this fixture")
        assert(scan_rows(search.world_address + 1, path, state.map, search.radius_squared, table.unpack(search.points)) == nil
            and scan_raw(search.world_address + 1, path, state.map, search.radius_squared, table.unpack(search.points)) == nil,
            "The two native samplers must refuse a different world")
        assert(scan_rows(search.world_address, path, "/Game/Classes/Maps/PlaytestMap_01", search.radius_squared,
            table.unpack(search.points)) == nil, "The direct sampler must refuse a different map")
    else
        local scan = assert(SilverfishSampleNativeMushroomPatch)
        assert(scan(search.world_address + 1, path, search.radius_squared, table.unpack(search.points)) == nil,
            "The sampler must refuse a different world")
    end

    local function read_direct()
        search.values = {}
        assert(sample_native(search, path), "Populated mushroom patch needs the native reader")
    end

    local function read_raw_then_lua()
        search.values = {}
        local rows = assert(scan_raw(search.world_address, path, state.map, search.radius_squared, table.unpack(search.points)))
        for line in rows:gmatch("[^\n]+") do
            local entry = {}
            for value in line:gmatch("[^\t]+") do entry[#entry + 1] = value end
            local actor = StaticFindObject(entry[3])
            if valid(actor) then
                entry[1], entry.actor = tonumber(entry[1]), actor
                table.insert(entry, 2, path)
                entry.row = status_row("native-mushroom", state.map, table.unpack(entry, 2))
                search.values[#search.values + 1] = entry
            end
        end
    end

    local function read_lua()
        search.values = {}
        sample_lua(search, path, patch)
        -- Preserve the production status formatting cost for the earlier Lua fallback.
        if not direct then
            for _, entry in ipairs(search.values) do
                entry.row = status_row("native-mushroom", state.map, table.unpack(entry, 2))
            end
        end
    end

    local function collect_rows(reader)
        reader()
        local results, bytes = {}, 0
        for _, entry in ipairs(search.values) do
            local guid = entry.guid or entry[3]
            local row = entry.row or status_row("native-mushroom", state.map, table.unpack(entry, 2))
            assert(not results[guid], "Stock mushroom ID was repeated")
            results[guid] = {actor = entry.actor, distance = entry.distance or entry[1], row = row}
            bytes = bytes + #row
        end
        return results, #search.values, bytes
    end

    local native, count, bytes = collect_rows(read_direct)
    local fallback, fallback_count = collect_rows(read_lua)
    assert(count >= 2 and count == fallback_count, "The two samplers need the same populated stock patch")
    local function split(row)
        local values = {}
        for value in row:gmatch("[^\t]+") do values[#values + 1] = value end
        assert(#values == 16, "Unexpected stock mushroom status row")
        return values
    end
    local function same_number(first, second)
        local left, right = tonumber(first), tonumber(second)
        return left and right and math.abs(left - right) <= 1e-7 * math.max(1, math.abs(left))
    end
    for guid, entry in pairs(native) do
        local expected = assert(fallback[guid])
        assert(same(entry.actor, expected.actor) and same_number(entry.distance, expected.distance),
            "Mushroom object or ordering distance differs")
        local actual_fields, expected_fields = split(entry.row), split(expected.row)
        for index, value in ipairs(actual_fields) do
            assert((index < 7 and value == expected_fields[index])
                or (index >= 7 and same_number(value, expected_fields[index])), "Mushroom row field differs: " .. index)
        end
    end
    if direct then
        local previous, previous_count, previous_bytes = collect_rows(read_raw_then_lua)
        assert(previous_count == count and previous_bytes == bytes, "Mushroom byte total differs")
        for guid, entry in pairs(native) do
            local legacy = assert(previous[guid])
            assert(entry.row == legacy.row and same(entry.actor, legacy.actor), "Native and prior Lua row bytes differ")
        end
    end

    local function measure(reader)
        local previous, start = collectgarbage("count"), SilverfishBridgeProfileStamp()
        for _ = 1, 32 do reader() end
        return (SilverfishBridgeProfileStamp() - start) / 1000, collectgarbage("count") - previous
    end

    local running = collectgarbage("isrunning")
    collectgarbage("stop")
    local ok, result = pcall(function()
        local native_first, native_first_kib = measure(read_direct)
        local raw_first, raw_first_kib = 0, 0
        if direct then raw_first, raw_first_kib = measure(read_raw_then_lua) end
        local lua_first, lua_first_kib = measure(read_lua)
        local lua_second, lua_second_kib = measure(read_lua)
        local raw_second, raw_second_kib = 0, 0
        if direct then raw_second, raw_second_kib = measure(read_raw_then_lua) end
        local native_second, native_second_kib = measure(read_direct)
        return ("mushroom samplers %d %d %d %d %.3f %.3f %.3f %.3f %.3f %.3f"):format(
            count, 64, bytes, direct and 1 or 0, native_first + native_second, raw_first + raw_second, lua_first + lua_second,
            native_first_kib + native_second_kib, raw_first_kib + raw_second_kib, lua_first_kib + lua_second_kib)
    end)
    if running then collectgarbage("restart") end
    if not ok then error(result, 0) end
    return result
end
