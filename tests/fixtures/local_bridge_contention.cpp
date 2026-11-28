#include "../../native/local_bridge.cpp"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <future>
#include <string_view>
#include <thread>
#include <vector>

int main()
{
    using namespace std::chrono_literals;
    auto& lua = RC::LuaMadeSimple::new_state();
    auto* stack = lua.get_lua_state();
    const std::string repeated = "native-loot" + std::string(96, 'x');
    push_status_row(stack, repeated);
    push_status_row(stack, repeated);
    assert(lua_gettop(stack) == 2 && lua_tostring(stack, 1) == lua_tostring(stack, 2));

    const auto bucket = std::hash<std::string_view>{}(repeated) % status_row_slots;
    std::string collision;
    for (int i = 0; i < status_row_slots * 32; ++i)
    {
        auto row = "native-door" + std::string(96, 'x') + std::to_string(i);
        if (std::hash<std::string_view>{}(row) % status_row_slots == bucket)
        {
            collision = std::move(row);
            break;
        }
    }
    assert(!collision.empty());
    push_status_row(stack, collision);
    push_status_row(stack, repeated);
    assert(lua_gettop(stack) == 4 && std::string_view(lua_tostring(stack, 1)) == repeated);
    assert(std::string_view(lua_tostring(stack, 3)) == collision && std::string_view(lua_tostring(stack, 4)) == repeated);
    const std::string oversized_row(status_row_reuse_limit + 1, 'y');
    push_status_row(stack, oversized_row);
    push_status_row(stack, oversized_row);
    assert(lua_gettop(stack) == 6 && lua_tostring(stack, 5) != lua_tostring(stack, 6));
    lua_settop(stack, 0);

    assert(take(lua) == 1 && !lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    assert(SilverfishBridgeOpenV1() == 1 && SilverfishBridgeEnableStatusV1() == 1);

    assert(SilverfishBridgePublishAvatarPosesV1("pose", 4) == 1);
    assert(SilverfishBridgePublishPropPosesV1("prop", 4) == 1);
    std::promise<void> locked, release;
    auto lock_ready = locked.get_future();
    auto unlock = release.get_future();
    std::jthread worker([&] {
        std::lock_guard lock(mutex);
        locked.set_value();
        assert(unlock.wait_for(5s) == std::future_status::ready);
    });
    lock_ready.get();
    assert(take_avatar_poses(lua) == 0);
    assert(take_prop_poses(lua) == 0);
    assert(take_remote_footsteps(lua) == 0);
    lua.set_string("busy");
    assert(publish_avatar_pose(lua) == 1 && !lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    constexpr std::string_view concurrent_status = "status while command queue is busy\n";
    lua.set_string(concurrent_status);
    assert(publish_status(lua) == 1 && lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    char status_buffer[concurrent_status.size()]{};
    std::uint64_t first_revision{};
    assert(SilverfishBridgeCopyStatusV1(0, status_buffer, sizeof status_buffer, &first_revision) == sizeof status_buffer);
    assert(first_revision == 1 && SilverfishBridgeStatusRevisionV1() == first_revision);
    assert(std::string_view(status_buffer, sizeof status_buffer) == concurrent_status);
    release.set_value();
    worker.join();

    assert(take_avatar_poses(lua) == 2 && lua_toboolean(stack, 1));
    assert(std::string_view(lua_tostring(stack, 2)) == "pose");
    lua_settop(stack, 0);
    assert(take_prop_poses(lua) == 2 && lua_toboolean(stack, 1));
    assert(std::string_view(lua_tostring(stack, 2)) == "prop");
    lua_settop(stack, 0);
    lua.set_string("retry");
    assert(publish_avatar_pose(lua) == 1 && lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    char avatar_buffer[512]{};
    assert(SilverfishBridgeTakeAvatarPoseV1(avatar_buffer, sizeof avatar_buffer) == 5);
    assert(std::string_view(avatar_buffer, 5) == "retry");

    auto enqueue = [&](const std::string& record) {
        lua.set_string(record);
        assert(enqueue_input(lua) == 1 && lua_toboolean(stack, 1));
        lua_settop(stack, 0);
    };
    const std::string large_record(input_limit, 'X');
    for (std::size_t index = 0; index < input_capacity; ++index)
        enqueue(index == 0 ? large_record : std::to_string(index) + "\n");
    std::array<char, input_limit * input_capacity> batch{};
    std::array<std::uint16_t, input_capacity> lengths{};
    assert(!SilverfishBridgeReadInputBatchV1(batch.data(), input_limit - 1, lengths.data(), 1));
    assert(!SilverfishBridgeReadInputBatchV1(batch.data(), batch.size(), lengths.data(), 0));
    assert(!SilverfishBridgeReadInputBatchV1(batch.data(), batch.size(), lengths.data(), input_capacity + 1));
    assert(SilverfishBridgeReadInputBatchV1(batch.data(), batch.size(), lengths.data(), 1) == 1);
    assert(lengths[0] == input_limit && std::string_view(batch.data(), lengths[0]) == large_record);
    const auto count = SilverfishBridgeReadInputBatchV1(batch.data(), batch.size(), lengths.data(), 62);
    assert(count == 62);
    std::size_t offset{};
    for (std::uint32_t index = 0; index < count; ++index)
    {
        assert(std::string_view(batch.data() + offset, lengths[index]) == std::to_string(index + 1) + "\n");
        offset += lengths[index];
    }
    assert(SilverfishBridgeReadInputV1(batch.data(), batch.size()) == 3);
    assert(std::string_view(batch.data(), 3) == "63\n" && !SilverfishBridgeInputReadyV1());

    constexpr std::size_t packet_size = 64 * 1024;
    std::atomic<bool> done{};
    std::vector<char> output(packet_size);
    std::jthread reader([&] {
        std::uint64_t seen = first_revision;
        while (!done || seen != SilverfishBridgeStatusRevisionV1())
        {
            std::uint64_t revision{};
            const auto size = SilverfishBridgeCopyStatusV1(seen, output.data(), packet_size, &revision);
            if (!size) continue;
            assert(size == packet_size && revision > seen);
            const char expected = static_cast<char>('A' + (revision - first_revision - 1) % 26);
            assert(std::ranges::all_of(output, [expected](char value) { return value == expected; }));
            seen = revision;
        }
        assert(seen == first_revision + 200);
    });
    for (int i = 0; i < 200; ++i)
    {
        lua.set_string(std::string(packet_size, static_cast<char>('A' + i % 26)));
        assert(publish_status(lua) == 1 && lua_toboolean(stack, 1));
        lua_settop(stack, 0);
    }
    done = true;
    reader.join();
    lua_newtable(stack);
    for (const auto& row : {std::string("schema\t1"), std::string("opaque\t\0data", 12), std::string{}})
    {
        lua_pushlstring(stack, row.data(), row.size());
        lua_rawseti(stack, 1, static_cast<lua_Integer>(lua_rawlen(stack, 1) + 1));
    }
    assert(publish_status_rows(lua) == 1 && lua_toboolean(stack, -1));
    lua_settop(stack, 0);
    std::uint64_t version{};
    const auto row_bytes = SilverfishBridgeCopyStatusV1(first_revision + 200, output.data(), packet_size, &version);
    assert(version == first_revision + 201 && std::string_view(output.data(), row_bytes) == std::string_view("schema\t1\nopaque\t\0data\n", 22));

    lua_newtable(stack);
    lua_pushboolean(stack, true);
    lua_rawseti(stack, 1, 1);
    assert(publish_status_rows(lua) == 1 && !lua_toboolean(stack, -1));
    assert(SilverfishBridgeStatusRevisionV1() == 0);
    lua_settop(stack, 0);

    lua_newtable(stack);
    const auto oversized = std::string(reply_limit + 1, 'X');
    lua_pushlstring(stack, oversized.data(), oversized.size());
    lua_rawseti(stack, 1, 1);
    assert(publish_status_rows(lua) == 1 && !lua_toboolean(stack, -1));
    assert(SilverfishBridgeStatusRevisionV1() == 0);
    lua_settop(stack, 0);

    luaL_openlibs(stack);
    constexpr int measured_count = 128;
    lua_createtable(stack, 233, 0);
    for (int index = 1; index < 233; ++index)
    {
        const auto text = "native-item\t" + std::to_string(index) + '\t' + std::string(125, 'X');
        lua_pushlstring(stack, text.data(), text.size());
        lua_rawseti(stack, 1, index);
    }
    lua_pushliteral(stack, "");
    lua_rawseti(stack, 1, 233);
    const auto rows = luaL_ref(stack, LUA_REGISTRYINDEX);
    const auto allocated = [&] { return lua_gc(stack, LUA_GCCOUNT) * 1024 + lua_gc(stack, LUA_GCCOUNTB); };
    const auto growth = [&](bool native) {
        lua_gc(stack, LUA_GCCOLLECT);
        lua_gc(stack, LUA_GCSTOP);
        const auto before = allocated();
        for (int index = 0; index < measured_count; ++index)
        {
            if (native)
            {
                lua_rawgeti(stack, LUA_REGISTRYINDEX, rows);
                assert(publish_status_rows(lua) == 1);
            }
            else
            {
                lua_getglobal(stack, "table");
                lua_getfield(stack, -1, "concat");
                lua_remove(stack, -2);
                lua_rawgeti(stack, LUA_REGISTRYINDEX, rows);
                lua_pushliteral(stack, "\n");
                lua_call(stack, 2, 1);
                assert(publish_status(lua) == 1);
            }
            assert(lua_toboolean(stack, -1));
            lua_settop(stack, 0);
        }
        const auto bytes = allocated() - before;
        lua_gc(stack, LUA_GCRESTART);
        return bytes;
    };
    const auto lua_bytes = growth(false);
    const auto native_bytes = growth(true);
    assert(lua_bytes > 30 * 1024 * measured_count && native_bytes < lua_bytes / 100);
    std::printf("Snapshot assembly across %d frames: %d Lua bytes through concat, %d directly through C++\n",
                measured_count, lua_bytes, native_bytes);
    luaL_unref(stack, LUA_REGISTRYINDEX, rows);

    int dispatched{};
    lua_newtable(stack);
    lua_pushboolean(stack, true);
    lua_setfield(stack, 1, "fast-inspect");
    lua_pushlightuserdata(stack, &dispatched);
    lua_pushcclosure(stack, [](lua_State* callback) {
        ++*static_cast<int*>(lua_touserdata(callback, lua_upvalueindex(1)));
        return 0;
    }, 1);
    assert(make_fast_command_callback(lua) == 1 && lua_iscfunction(stack, -1));
    const auto fast_callback = luaL_ref(stack, LUA_REGISTRYINDEX);
    lua_settop(stack, 0);
    const auto pump_fast = [&] {
        lua_rawgeti(stack, LUA_REGISTRYINDEX, fast_callback);
        assert(lua_pcall(stack, 0, 0, 0) == LUA_OK);
    };
    const auto submit = [](std::string_view text) {
        return SilverfishBridgeSubmitV1(text.data(), static_cast<std::uint32_t>(text.size()));
    };
    {
        // An empty fast check does not acquire the native queue lock.
        std::lock_guard lock(mutex);
        pump_fast();
    }
    assert(dispatched == 0 && take_fast_command(lua) == 0);
    for (const auto text : std::array<std::string_view, 4>{"id\tordinary\t1\n", "id\tfast-inspect\t1\nbody\n",
                                                         "bad id\tfast-inspect\tx", {"id\tfast-inspect\t\0x\n", 19}})
    {
        const auto ordinary = submit(text);
        assert(ordinary > 0 && take_fast_command(lua) == 0);
        pump_fast();
        assert(dispatched == 0 && take(lua) == 3 && lua_tointeger(stack, 2) == ordinary);
        std::size_t size{};
        const auto* copied = lua_tolstring(stack, 3, &size);
        assert(std::string_view(copied, size) == text);
        lua_settop(stack, 0);
        assert(SilverfishBridgeWaitV1(ordinary, 0) == -2);
    }

    const auto fast = submit("test.id\tfast-inspect\t\tone\t\r\n\r\n");
    assert(fast > 0 && !submit("another\tfast-inspect"));
    pump_fast();
    assert(dispatched == 1 && take_fast_command(lua) == 2 && lua_tointeger(stack, 1) == fast);
    const std::array<std::string_view, 5> fields{"test.id", "fast-inspect", "", "one", ""};
    assert(lua_rawlen(stack, 2) == fields.size());
    for (std::size_t index = 0; index < fields.size(); ++index)
    {
        assert(lua_rawgeti(stack, 2, static_cast<lua_Integer>(index + 1)) == LUA_TSTRING);
        assert(fields[index] == lua_tostring(stack, -1));
        lua_pop(stack, 1);
    }
    lua_settop(stack, 0);
    pump_fast();
    assert(dispatched == 1 && take_fast_command(lua) == 0);
    assert(take(lua) == 2 && lua_tointeger(stack, 2) == fast);
    lua_settop(stack, 0);
    assert(SilverfishBridgeWaitV1(fast, 0) == -2);
    lua.set_integer(fast);
    assert(claim_command(lua) == 1 && !lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    luaL_unref(stack, LUA_REGISTRYINDEX, fast_callback);

    const auto queued = SilverfishBridgeSubmitV1("queued", 6);
    assert(queued > 0 && SilverfishBridgeWaitV1(queued, 0) == -2);
    assert(take(lua) == 1);
    lua_settop(stack, 0);

    const auto taken = SilverfishBridgeSubmitV1("taken", 5);
    assert(taken > queued && take(lua) == 3);
    lua_settop(stack, 0);
    assert(SilverfishBridgeWaitV1(taken, 0) == -2);
    lua.set_integer(taken);
    assert(claim_command(lua) == 1 && !lua_toboolean(stack, 1));
    lua_settop(stack, 0);

    const auto ticket = SilverfishBridgeSubmitV1("test", 4);
    assert(ticket > 0 && take(lua) == 3);
    lua_settop(stack, 0);
    lua.set_integer(ticket);
    assert(claim_command(lua) == 1 && lua_toboolean(stack, 1));
    lua_settop(stack, 0);
    assert(SilverfishBridgeWaitV1(ticket, 0) == 0);
    lua.set_integer(ticket);
    lua.set_string(std::string(packet_size, 'R'));
    assert(publish(lua) == 0);
    assert(SilverfishBridgeWaitV1(ticket, 0) == packet_size);
    assert(SilverfishBridgeCopyV1(ticket, output.data(), packet_size));
    assert(std::ranges::all_of(output, [](char value) { return value == 'R'; }));
    SilverfishBridgeCancelV1(ticket);
    assert(!SilverfishBridgeCopyV1(ticket, output.data(), packet_size));
    SilverfishBridgeCloseV1();
    assert(SilverfishBridgeStatusRevisionV1() == 0);
}
