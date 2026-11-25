local function compare_mushroom_samplers(fields, state, phase)
    local host, fresh = transport.scoped_host(state, phase)
    local path = fields[3]
    local patch = patches and patches[path]
    if not fixtures or #fields ~= 3 or not fresh or not host or not valid(patch)
        or not same(world, state.world) then error("mushroom_sampler_fixture_unavailable", 0) end

    local search = assert(begin_sample(state, 2e7))
    local scan = assert(SilverfishSampleNativeMushroomPatch)
    assert(scan(search.world_address + 1, path, search.radius_squared, table.unpack(search.points)) == nil,
        "The sampler must refuse a different world")

    local function snapshot(reader)
        search.values = {}
        reader(search, path, patch)
        local results = {}
        for _, entry in ipairs(search.values) do
            assert(not results[entry[3]], "Stock mushroom ID was repeated")
            results[entry[3]] = entry
        end
        return results, #search.values
    end

    local native, count = snapshot(function(...)
        assert(sample_native(...), "Populated mushroom patch needs the native reader")
    end)
    local legacy, legacy_count = snapshot(sample_lua)
    assert(count >= 2 and count == legacy_count, "The two samplers need the same populated stock patch")
    for guid, entry in pairs(native) do
        local expected = assert(legacy[guid])
        assert(same(expected.actor, entry.actor), "Mushroom actor did not match")
        for index, actual in ipairs(entry) do
            local old = expected[index]
            if type(old) == "number" then
                assert(tonumber(actual) and math.abs(tonumber(actual) - old) <= 1e-7 * math.max(1, math.abs(old)),
                    "Mushroom numeric field did not match")
            else
                assert(actual == old, "Mushroom identity field did not match")
            end
        end
    end

    local iterations = 64
    local function measure(reader)
        local start = SilverfishBridgeProfileStamp()
        for _ = 1, iterations do
            search.values = {}
            reader(search, path, patch)
        end
        return (SilverfishBridgeProfileStamp() - start) / 1000
    end
    local native_ms, lua_ms = measure(sample_native), measure(sample_lua)
    return string.format("mushroom samplers %d %d %.3f %.3f", count, iterations, native_ms, lua_ms)
end
