#include "local_bridge.hpp"
#include "local_stash_ticket.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

namespace
{
    using Lua = RC::LuaMadeSimple::Lua;
    constexpr std::uint32_t command_limit = 32 * 1024;
    constexpr std::uint32_t reply_limit = 2 * 1024 * 1024;
    // Match the ctypes reader buffer in tools/silverfish/local_bridge.py.
    constexpr std::uint32_t input_limit = 1536;
    constexpr std::size_t input_capacity = 64;
    constexpr std::uint32_t prop_poses_limit = 4 * 257;
    constexpr std::uint32_t avatar_poses_limit = 512;
    constexpr std::uint32_t local_prop_pose_limit = 768;
    constexpr std::uint32_t remote_footsteps_limit = 2048;
    constexpr std::uint32_t weapon_cues_limit = 16 * 1024;
    constexpr std::size_t weapon_cue_capacity = 36;
    constexpr std::size_t weapon_cue_limit = 1024;
    constexpr std::size_t weapon_cue_area_limit = 160;
    constexpr std::int64_t weapon_cue_ttl_ms = 250;
    constexpr std::uint32_t zone_roster_limit = 8192;
    constexpr std::uint32_t guest_interaction_features = 0x0f;
    constexpr std::uint32_t guest_interaction_area_limit = 160;
    constexpr std::uint32_t guest_interaction_pawn_limit = 512;

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
    std::shared_ptr<const std::string> reply;
    std::shared_ptr<const std::string> status_frame;
    std::uint64_t status_revision{};
    std::deque<std::string> input;
    std::string prop_poses;
    std::string local_prop_pose;
    std::string local_avatar_pose;
    std::string remote_avatar_poses;
    std::string remote_footsteps;
    std::chrono::steady_clock::time_point remote_footsteps_expires;
    struct WeaponCue
    {
        std::string record;
        std::chrono::steady_clock::time_point expires;
    };
    std::deque<WeaponCue> weapon_cues;
    std::string weapon_cue_area;
    std::size_t weapon_cue_bytes{};
    bool weapon_cues_enabled{};
    std::int64_t native_events_heartbeat{}, stock_board_heartbeat{}, host_dig_heartbeat{};
    std::int64_t zone_heartbeat{};
    std::string zone_roster;

    struct GuestInteractionLease
    {
        std::int64_t stamp{};
        std::uint32_t features{};
        std::string area;
        std::string pawn;
    };
    GuestInteractionLease guest_interaction_lease;

    void clear()
    {
        active_ticket = 0;
        state = State::idle;
        command.clear();
        reply.reset();
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

    int stash_body_limit(const Lua& lua)
    {
        lua.set_integer(silverfish::local_stash_ticket::body_limit);
        return 1;
    }

    bool timing_fixture_enabled()
    {
        wchar_t value[2]{};
        return GetEnvironmentVariableW(L"SILVERFISH_COOP_HEADLESS", value, 2) == 1 && value[0] == L'1' &&
            GetEnvironmentVariableW(L"SILVERFISH_COOP_TITLE_FIXTURES", value, 2) == 1 && value[0] == L'1';
    }

    int profile_stamp(const Lua& lua)
    {
        ULONG64 cycles{};
        if (!QueryThreadCycleTime(GetCurrentThread(), &cycles)) return 0;
        const auto now = std::chrono::steady_clock::now().time_since_epoch();
        lua.set_integer(std::chrono::duration_cast<std::chrono::microseconds>(now).count());
        lua.set_integer(static_cast<std::int64_t>(cycles));
        return 2;
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
        const auto snapshot = !frame.empty() && frame.size() <= reply_limit
            ? std::make_shared<const std::string>(frame) : nullptr;
        std::lock_guard lock(mutex);
        if (state != State::running || ticket != active_ticket) return 0;
        if (!snapshot)
        {
            state = State::too_large;
        }
        else
        {
            reply = snapshot;
            state = State::replied;
        }
        changed.notify_all();
        return 0;
    }

    int publish_status(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto frame = lua.get_string();
        auto snapshot = !frame.empty() && frame.size() <= reply_limit
            ? std::make_shared<const std::string>(frame) : nullptr;
        bool published{};
        {
            std::lock_guard lock(mutex);
            if (status_enabled)
            {
                published = snapshot != nullptr;
                // Retire the previous buffer after releasing the bridge lock.
                status_frame.swap(snapshot);
                if (published) ++status_revision;
            }
        }
        lua.set_bool(published);
        return 1;
    }

    int read_heartbeat(const Lua& lua, const std::int64_t& mailbox)
    {
        bool active{};
        std::int64_t stamp{};
        {
            std::lock_guard lock(mutex);
            active = attached;
            stamp = mailbox;
        }
        lua.set_bool(active);
        if (!active || !stamp) return 1;
        lua.set_integer(stamp);
        return 2;
    }

    int read_native_events_heartbeat(const Lua& lua)
    {
        return read_heartbeat(lua, native_events_heartbeat);
    }

    int read_stock_board_heartbeat(const Lua& lua)
    {
        return read_heartbeat(lua, stock_board_heartbeat);
    }

    int read_host_dig_heartbeat(const Lua& lua)
    {
        return read_heartbeat(lua, host_dig_heartbeat);
    }

    int read_zone_heartbeat(const Lua& lua)
    {
        return read_heartbeat(lua, zone_heartbeat);
    }

    int read_zone_roster(const Lua& lua)
    {
        bool active{};
        std::string roster;
        {
            std::lock_guard lock(mutex);
            active = attached;
            if (active) roster = zone_roster;
        }
        lua.set_bool(active);
        if (roster.empty()) return 1;
        lua.set_string(roster);
        return 2;
    }

    int read_guest_interaction_lease(const Lua& lua)
    {
        if (lua.get_stack_size() != 2 || !lua.is_string(1) || !lua.is_string(2)) return 0;
        const auto area = lua.get_string();
        const auto pawn = lua.get_string();
        bool active{};
        std::int64_t stamp{};
        std::uint32_t features{};
        {
            std::lock_guard lock(mutex);
            active = attached;
            if (active && guest_interaction_lease.area == area && guest_interaction_lease.pawn == pawn)
            {
                stamp = guest_interaction_lease.stamp;
                features = guest_interaction_lease.features;
            }
        }
        lua.set_bool(active);
        if (!stamp) return 1;
        lua.set_integer(stamp);
        lua.set_integer(features);
        return 3;
    }

    int publish_heartbeat(std::int64_t stamp, std::int64_t& mailbox, bool accept_clear = false)
    {
        if (stamp < 0 || (!stamp && !accept_clear)) return 0;
        std::lock_guard lock(mutex);
        if (!attached) return 0;
        mailbox = stamp;
        return 1;
    }

    int take_presentation(const Lua& lua, std::string& mailbox)
    {
        bool active{};
        std::string frame;
        {
            // A missed sample stays queued until the next update or until replaced.
            std::unique_lock lock(mutex, std::try_to_lock);
            if (!lock) return 0;
            active = attached;
            frame.swap(mailbox);
        }
        lua.set_bool(active);
        if (frame.empty()) return 1;
        lua.set_string(frame);
        return 2;
    }

    int take_prop_poses(const Lua& lua)
    {
        return take_presentation(lua, prop_poses);
    }

    int take_avatar_poses(const Lua& lua)
    {
        return take_presentation(lua, remote_avatar_poses);
    }

    int take_remote_footsteps(const Lua& lua)
    {
        std::string frame;
        {
            std::unique_lock lock(mutex, std::try_to_lock);
            if (!lock) return 0;
            if (attached && std::chrono::steady_clock::now() < remote_footsteps_expires)
                frame.swap(remote_footsteps);
            else
                remote_footsteps.clear();
        }
        if (frame.empty()) return 0;
        lua.set_string(frame);
        return 1;
    }

    int publish_presentation(const Lua& lua, std::string& mailbox, std::uint32_t limit)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto frame = lua.get_string();
        bool published = false;
        {
            std::unique_lock lock(mutex, std::try_to_lock);
            if (lock && attached && !frame.empty() && frame.size() <= limit)
            {
                mailbox.assign(frame);
                published = true;
            }
        }
        lua.set_bool(published);
        return 1;
    }

    int publish_avatar_pose(const Lua& lua)
    {
        return publish_presentation(lua, local_avatar_pose, avatar_poses_limit);
    }

    int publish_local_prop_pose(const Lua& lua)
    {
        return publish_presentation(lua, local_prop_pose, local_prop_pose_limit);
    }

    void clear_weapon_cues()
    {
        weapon_cues.clear();
        weapon_cue_area.clear();
        weapon_cue_bytes = 0;
    }

    int publish_weapon_cue(const Lua& lua)
    {
        if (lua.get_stack_size() != 3 || !lua.is_string(1) || !lua.is_string(2) || !lua.is_integer(3)) return 0;
        const auto area = lua.get_string();
        const auto record = lua.get_string();
        const auto remaining = lua.get_integer();
        if (area.empty() || area.size() > weapon_cue_area_limit || area.find_first_of("\r\n") != area.npos ||
            record.empty() || record.size() > weapon_cue_limit || record.find_first_of("\r\n") != record.npos ||
            remaining <= 0 || remaining > weapon_cue_ttl_ms) return 0;
        const auto expires = std::chrono::steady_clock::now() + std::chrono::milliseconds(remaining);
        bool active{};
        {
            std::lock_guard lock(mutex);
            active = attached && weapon_cues_enabled;
            if (active)
            {
                if (weapon_cue_area != area)
                {
                    clear_weapon_cues();
                    weapon_cue_area.assign(area);
                }
                const auto cost = record.size() + 1;
                while (weapon_cues.size() >= weapon_cue_capacity ||
                       area.size() + 1 + weapon_cue_bytes + cost > weapon_cues_limit)
                {
                    weapon_cue_bytes -= weapon_cues.front().record.size() + 1;
                    weapon_cues.pop_front();
                }
                weapon_cues.push_back({std::string(record), expires});
                weapon_cue_bytes += cost;
            }
        }
        lua.set_bool(active);
        return 1;
    }

    int clear_local_weapon_cues(const Lua&)
    {
        std::lock_guard lock(mutex);
        clear_weapon_cues();
        return 0;
    }

    std::uint32_t take_local_pose(std::string& mailbox, char* buffer, std::uint32_t capacity)
    {
        std::lock_guard lock(mutex);
        if (!attached || !buffer || mailbox.empty() || capacity < mailbox.size()) return 0;
        const auto size = static_cast<std::uint32_t>(mailbox.size());
        std::memcpy(buffer, mailbox.data(), size);
        mailbox.clear();
        return size;
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
    status_frame.reset();
    input.clear();
    prop_poses.clear();
    local_prop_pose.clear();
    local_avatar_pose.clear();
    remote_avatar_poses.clear();
    remote_footsteps.clear();
    weapon_cues_enabled = false;
    clear_weapon_cues();
    native_events_heartbeat = stock_board_heartbeat = host_dig_heartbeat = 0;
    guest_interaction_lease = {};
    zone_heartbeat = 0;
    zone_roster.clear();
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
    return status_frame ? status_revision : 0;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeCopyStatusV1(
    std::uint64_t previous, char* buffer, std::uint32_t capacity, std::uint64_t* revision)
{
    if (!buffer || !revision) return 0;
    std::shared_ptr<const std::string> snapshot;
    std::uint64_t version{};
    {
        std::lock_guard lock(mutex);
        if (!status_frame || status_revision == previous) return 0;
        snapshot = status_frame;
        version = status_revision;
    }
    const auto size = static_cast<std::uint32_t>(snapshot->size());
    if (capacity >= size)
    {
        std::memcpy(buffer, snapshot->data(), size);
        *revision = version;
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

extern "C" __declspec(dllexport) int SilverfishBridgePublishNativeEventsHeartbeatV1(std::int64_t stamp)
{
    return publish_heartbeat(stamp, native_events_heartbeat);
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishStockBoardHeartbeatV1(std::int64_t stamp)
{
    return publish_heartbeat(stamp, stock_board_heartbeat);
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishHostDigHeartbeatV1(std::int64_t stamp)
{
    return publish_heartbeat(stamp, host_dig_heartbeat, true);
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishNativeZoneLeaseV1(
    std::int64_t stamp, const char* roster, std::uint32_t size)
{
    if (stamp <= 0 || size >= zone_roster_limit || (size && !roster)) return 0;
    std::lock_guard lock(mutex);
    if (!attached) return 0;
    // A one-off rest or transport command only renews the stamp.
    if (roster) zone_roster.assign(roster, size);
    zone_heartbeat = stamp;
    return 1;
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishGuestInteractionLeaseV1(
    std::int64_t stamp, std::uint32_t features, const char* area, std::uint32_t area_size,
    const char* pawn, std::uint32_t pawn_size)
{
    if (features & ~guest_interaction_features) return 0;
    std::lock_guard lock(mutex);
    if (!attached) return 0;
    if (!features)
    {
        if (stamp || area_size || pawn_size) return 0;
        guest_interaction_lease = {};
        return 1;
    }
    if (stamp <= 0 || !area || !area_size || area_size > guest_interaction_area_limit ||
        !pawn || !pawn_size || pawn_size > guest_interaction_pawn_limit) return 0;
    guest_interaction_lease.stamp = stamp;
    guest_interaction_lease.features = features;
    guest_interaction_lease.area.assign(area, area_size);
    guest_interaction_lease.pawn.assign(pawn, pawn_size);
    return 1;
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishPropPosesV1(const char* data, std::uint32_t size)
{
    if (!data || !size || size > prop_poses_limit) return 0;
    std::lock_guard lock(mutex);
    if (!attached) return 0;
    prop_poses.assign(data, size);
    return 1;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeTakeAvatarPoseV1(char* buffer, std::uint32_t capacity)
{
    return take_local_pose(local_avatar_pose, buffer, capacity);
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeTakeLocalPropPoseV1(char* buffer, std::uint32_t capacity)
{
    return take_local_pose(local_prop_pose, buffer, capacity);
}

extern "C" __declspec(dllexport) int SilverfishBridgeEnableLocalWeaponCuesV1()
{
    std::lock_guard lock(mutex);
    weapon_cues_enabled = attached;
    return weapon_cues_enabled;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeTakeLocalWeaponCuesV1(char* buffer, std::uint32_t capacity)
{
    if (!buffer || capacity < weapon_cues_limit) return 0;
    std::deque<WeaponCue> pending;
    std::string area;
    {
        std::lock_guard lock(mutex);
        if (!weapon_cues_enabled || weapon_cues.empty()) return 0;
        pending.swap(weapon_cues);
        area.swap(weapon_cue_area);
        weapon_cue_bytes = 0;
    }
    const auto now = std::chrono::steady_clock::now();
    std::size_t size{};
    auto append = [&](const std::string& row) {
        std::memcpy(buffer + size, row.data(), row.size());
        size += row.size();
        buffer[size++] = '\n';
    };
    for (const auto& entry : pending)
    {
        if (entry.expires <= now) continue;
        if (!size) append(area);
        append(entry.record);
    }
    return static_cast<std::uint32_t>(size);
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishAvatarPosesV1(const char* data, std::uint32_t size)
{
    if (!data || !size || size > avatar_poses_limit) return 0;
    std::lock_guard lock(mutex);
    if (!attached) return 0;
    remote_avatar_poses.assign(data, size);
    return 1;
}

extern "C" __declspec(dllexport) int SilverfishBridgePublishFootstepsV1(const char* data, std::uint32_t size)
{
    if (!data || !size || size > remote_footsteps_limit) return 0;
    std::lock_guard lock(mutex);
    if (!attached) return 0;
    remote_footsteps.assign(data, size);
    remote_footsteps_expires = std::chrono::steady_clock::now() + std::chrono::milliseconds(150);
    return 1;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeStashBodyLimitV1()
{
    return silverfish::local_stash_ticket::body_limit;
}

extern "C" __declspec(dllexport) std::int64_t SilverfishBridgeSubmitV1(const char* data, std::uint32_t size)
{
    if (!data || !size) return 0;
    std::string large_stash;
    if (size > command_limit)
    {
        if (!silverfish::local_stash_ticket::accepts({data, size})) return 0;
        large_stash.assign(data, size);
    }
    std::lock_guard lock(mutex);
    if (!attached || state != State::idle || last_ticket == std::numeric_limits<std::int64_t>::max()) return 0;
    if (large_stash.empty()) command.assign(data, size);
    else command = std::move(large_stash);
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
    return state == State::replied ? static_cast<std::int32_t>(reply->size()) : 0;
}

extern "C" __declspec(dllexport) int SilverfishBridgeCopyV1(std::int64_t ticket, char* buffer, std::uint32_t size)
{
    if (!buffer) return 0;
    std::shared_ptr<const std::string> snapshot;
    {
        std::lock_guard lock(mutex);
        if (ticket != active_ticket || state != State::replied || size != reply->size()) return 0;
        snapshot = reply;
    }
    std::memcpy(buffer, snapshot->data(), size);
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
        lua.register_function("SilverfishLocalBridgeStashBodyLimit", stash_body_limit);
        lua.register_function("SilverfishPublishLocalBridgeReply", publish);
        lua.register_function("SilverfishPublishLocalBridgeStatus", publish_status);
        lua.register_function("SilverfishEnqueueLocalBridgeInput", enqueue_input);
        lua.register_function("SilverfishTakeLocalPropPoses", take_prop_poses);
        lua.register_function("SilverfishPublishLocalPropPose", publish_local_prop_pose);
        lua.register_function("SilverfishPublishLocalWeaponCue", publish_weapon_cue);
        lua.register_function("SilverfishClearLocalWeaponCues", clear_local_weapon_cues);
        lua.register_function("SilverfishPublishLocalAvatarPose", publish_avatar_pose);
        lua.register_function("SilverfishTakeRemoteAvatarPoses", take_avatar_poses);
        lua.register_function("SilverfishTakeRemoteFootsteps", take_remote_footsteps);
        lua.register_function("SilverfishReadLocalNativeEventsHeartbeat", read_native_events_heartbeat);
        lua.register_function("SilverfishReadLocalStockBoardHeartbeat", read_stock_board_heartbeat);
        lua.register_function("SilverfishReadLocalHostDigHeartbeat", read_host_dig_heartbeat);
        lua.register_function("SilverfishReadLocalNativeZoneHeartbeat", read_zone_heartbeat);
        lua.register_function("SilverfishReadLocalNativeZoneRoster", read_zone_roster);
        lua.register_function("SilverfishReadLocalGuestInteractionLease", read_guest_interaction_lease);
        if (timing_fixture_enabled()) lua.register_function("SilverfishBridgeProfileStamp", profile_stamp);
    }

    void stop()
    {
        std::lock_guard lock(mutex);
        ready = false;
        attached = false;
        status_enabled = false;
        status_frame.reset();
        input.clear();
        prop_poses.clear();
        local_prop_pose.clear();
        local_avatar_pose.clear();
        remote_avatar_poses.clear();
        remote_footsteps.clear();
        weapon_cues_enabled = false;
        clear_weapon_cues();
        native_events_heartbeat = stock_board_heartbeat = host_dig_heartbeat = 0;
        guest_interaction_lease = {};
        zone_heartbeat = 0;
        zone_roster.clear();
        clear();
    }
}
