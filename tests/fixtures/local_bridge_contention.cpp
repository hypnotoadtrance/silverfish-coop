#include "../../native/local_bridge.cpp"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <future>
#include <string_view>
#include <thread>
#include <vector>

int main()
{
    using namespace std::chrono_literals;
    auto& lua = RC::LuaMadeSimple::new_state();
    auto* stack = lua.get_lua_state();
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
        std::uint64_t seen{};
        while (!done || seen != SilverfishBridgeStatusRevisionV1())
        {
            std::uint64_t revision{};
            const auto size = SilverfishBridgeCopyStatusV1(seen, output.data(), packet_size, &revision);
            if (!size) continue;
            assert(size == packet_size && revision > seen);
            const char expected = static_cast<char>('A' + (revision - 1) % 26);
            assert(std::ranges::all_of(output, [expected](char value) { return value == expected; }));
            seen = revision;
        }
        assert(seen == 200);
    });
    for (int i = 0; i < 200; ++i)
    {
        lua.set_string(std::string(packet_size, static_cast<char>('A' + i % 26)));
        assert(publish_status(lua) == 1 && lua_toboolean(stack, 1));
        lua_settop(stack, 0);
    }
    done = true;
    reader.join();
    const auto ticket = SilverfishBridgeSubmitV1("test", 4);
    assert(ticket > 0 && take(lua) == 3);
    lua_settop(stack, 0);
    lua.set_integer(ticket);
    assert(claim_command(lua) == 1 && lua_toboolean(stack, 1));
    lua_settop(stack, 0);
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
