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

    enum class State { idle, queued, taken, running, replied, too_large };
    std::mutex mutex;
    std::condition_variable changed;
    bool ready{};
    bool attached{};
    bool status_enabled{};
    std::int64_t last_ticket{};
    std::int64_t active_ticket{};
    State state{State::idle};
    std::string command;
    std::string reply;
    std::string status_frame;
    std::uint64_t status_revision{};
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
                state = State::taken;
                pending = std::move(command);
            }
            if (state == State::taken || state == State::running) ticket = active_ticket;
        }
        lua.set_bool(active);
        if (!ticket) return 1;
        lua.set_integer(ticket);
        if (pending.empty()) return 2;
        lua.set_string(pending);
        return 3;
    }

    int claim_command(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_integer()) return 0;
        const auto ticket = lua.get_integer();
        bool claimed{};
        {
            std::lock_guard lock(mutex);
            claimed = state == State::taken && ticket == active_ticket;
            if (claimed) state = State::running;
        }
        lua.set_bool(claimed);
        return 1;
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
        // UE4SS removes each argument as it is read.
        const auto ticket = lua.get_integer();
        const auto frame = lua.get_string();
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

    int publish_status(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto frame = lua.get_string();
        bool published{};
        {
            std::lock_guard lock(mutex);
            if (status_enabled)
            {
                published = !frame.empty() && frame.size() <= reply_limit;
                if (published)
                {
                    status_frame.assign(frame);
                    ++status_revision;
                }
                else status_frame.clear();
            }
        }
        lua.set_bool(published);
        return 1;
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
    status_enabled = false;
    status_frame.clear();
    input.clear();
    clear();
}

extern "C" __declspec(dllexport) int SilverfishBridgeEnableStatusV1()
{
    std::lock_guard lock(mutex);
    status_enabled = attached;
    return status_enabled;
}

extern "C" __declspec(dllexport) std::uint64_t SilverfishBridgeStatusRevisionV1()
{
    std::lock_guard lock(mutex);
    return status_frame.empty() ? 0 : status_revision;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeCopyStatusV1(
    std::uint64_t previous, char* buffer, std::uint32_t capacity, std::uint64_t* revision)
{
    std::lock_guard lock(mutex);
    if (status_frame.empty() || status_revision == previous || !buffer || !revision) return 0;
    const auto size = static_cast<std::uint32_t>(status_frame.size());
    if (capacity >= size)
    {
        std::memcpy(buffer, status_frame.data(), size);
        *revision = status_revision;
    }
    return size;
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
        lua.register_function("SilverfishClaimLocalBridgeCommand", claim_command);
        lua.register_function("SilverfishPublishLocalBridgeReply", publish);
        lua.register_function("SilverfishPublishLocalBridgeStatus", publish_status);
        lua.register_function("SilverfishEnqueueLocalBridgeInput", enqueue_input);
    }

    void stop()
    {
        std::lock_guard lock(mutex);
        ready = false;
        attached = false;
        status_enabled = false;
        status_frame.clear();
        input.clear();
        clear();
    }
}
