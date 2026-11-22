#include "local_bridge.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>
#include <string>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>

namespace
{
    using Lua = RC::LuaMadeSimple::Lua;
    constexpr std::uint32_t command_limit = 32 * 1024;
    constexpr std::uint32_t reply_limit = 2 * 1024 * 1024;
    constexpr std::uint32_t input_limit = 1024;
    constexpr std::size_t input_capacity = 64;

    enum class State { idle, queued, running, replied, too_large };
    std::mutex mutex;
    std::condition_variable changed;
    bool ready{};
    bool attached{};
    std::int64_t last_ticket{};
    std::int64_t active_ticket{};
    State state{State::idle};
    std::string command;
    std::string reply;
    std::deque<std::string> input;

    void clear()
    {
        active_ticket = 0;
        state = State::idle;
        command.clear();
        reply.clear();
        changed.notify_all();
    }

    int take(const Lua& lua)
    {
        std::string pending;
        std::int64_t ticket{};
        bool active{};
        {
            std::lock_guard lock(mutex);
            ready = true;
            active = attached;
            if (state == State::queued)
            {
                state = State::running;
                ticket = active_ticket;
                pending = std::move(command);
            }
        }
        lua.set_bool(active);
        if (!ticket) return 1;
        lua.set_integer(ticket);
        lua.set_string(pending);
        return 3;
    }

    int enqueue_input(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto record = lua.get_string();
        bool queued = false;
        {
            std::lock_guard lock(mutex);
            if (attached && !record.empty() && record.size() <= input_limit && input.size() < input_capacity)
            {
                input.emplace_back(record);
                queued = true;
            }
        }
        lua.set_bool(queued);
        return 1;
    }

    int publish(const Lua& lua)
    {
        if (lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2)) return 0;
        const auto ticket = lua.get_integer(1);
        const auto frame = lua.get_string(2);
        std::lock_guard lock(mutex);
        if (state != State::running || ticket != active_ticket) return 0;
        if (frame.empty() || frame.size() > reply_limit)
        {
            state = State::too_large;
        }
        else
        {
            reply.assign(frame);
            state = State::replied;
        }
        changed.notify_all();
        return 0;
    }
}

extern "C" __declspec(dllexport) int SilverfishBridgeOpenV1()
{
    std::lock_guard lock(mutex);
    if (!ready || attached) return 0;
    attached = true;
    return 1;
}

extern "C" __declspec(dllexport) void SilverfishBridgeCloseV1()
{
    std::lock_guard lock(mutex);
    attached = false;
    input.clear();
    clear();
}

extern "C" __declspec(dllexport) int SilverfishBridgeInputReadyV1()
{
    std::lock_guard lock(mutex);
    return !input.empty();
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeReadInputV1(char* buffer, std::uint32_t capacity)
{
    std::lock_guard lock(mutex);
    if (!buffer || input.empty() || capacity < input.front().size()) return 0;
    const auto& record = input.front();
    const auto size = static_cast<std::uint32_t>(record.size());
    std::memcpy(buffer, record.data(), size);
    input.pop_front();
    return size;
}

extern "C" __declspec(dllexport) std::int64_t SilverfishBridgeSubmitV1(const char* data, std::uint32_t size)
{
    if (!data || !size || size > command_limit) return 0;
    std::lock_guard lock(mutex);
    if (!attached || state != State::idle || last_ticket == std::numeric_limits<std::int64_t>::max()) return 0;
    command.assign(data, size);
    active_ticket = ++last_ticket;
    state = State::queued;
    return active_ticket;
}

extern "C" __declspec(dllexport) std::int32_t SilverfishBridgeWaitV1(std::int64_t ticket, std::uint32_t milliseconds)
{
    if (milliseconds > 120000) return -1;
    std::unique_lock lock(mutex);
    changed.wait_for(lock, std::chrono::milliseconds(milliseconds), [&] {
        return ticket != active_ticket || state == State::replied || state == State::too_large;
    });
    if (ticket != active_ticket || state == State::too_large) return -1;
    return state == State::replied ? static_cast<std::int32_t>(reply.size()) : 0;
}

extern "C" __declspec(dllexport) int SilverfishBridgeCopyV1(std::int64_t ticket, char* buffer, std::uint32_t size)
{
    std::lock_guard lock(mutex);
    if (!buffer || ticket != active_ticket || state != State::replied || size != reply.size()) return 0;
    std::memcpy(buffer, reply.data(), size);
    return 1;
}

extern "C" __declspec(dllexport) void SilverfishBridgeCancelV1(std::int64_t ticket)
{
    std::lock_guard lock(mutex);
    if (ticket == active_ticket) clear();
}

namespace silverfish::local_bridge
{
    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        lua.register_function("SilverfishTakeLocalBridgeCommand", take);
        lua.register_function("SilverfishPublishLocalBridgeReply", publish);
        lua.register_function("SilverfishEnqueueLocalBridgeInput", enqueue_input);
    }

    void stop()
    {
        std::lock_guard lock(mutex);
        ready = false;
        attached = false;
        input.clear();
        clear();
    }
}
