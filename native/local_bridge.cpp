#include "local_bridge.hpp"
#include "local_catalog_ticket.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <clocale>
#include <condition_variable>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <LuaType/LuaUObject.hpp>
#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/UObject.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>

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
    constexpr std::uint32_t crate_outcome_input_limit = 4096;
    constexpr std::size_t input_capacity = 64;
    constexpr std::uint32_t prop_poses_limit = 4 * 257;
    constexpr std::uint32_t avatar_poses_limit = 512;
    constexpr std::uint32_t local_prop_pose_limit = 768;
    constexpr std::uint32_t stock_loot_motion_limit = 64 * 1024;
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
    std::mutex status_mutex;
    std::condition_variable changed;
    bool ready{};
    bool attached{};
    std::int64_t last_ticket{};
    std::int64_t active_ticket{};
    State state{State::idle};
    std::string command;
    struct FastHeader
    {
        std::size_t length{};
        int fields{};
    };
    FastHeader fast_header;
    std::vector<std::string> fast_actions;
    std::atomic<bool> fast_queued{};
    struct FastBridgeProfile
    {
        std::int64_t skips{}, invocations{}, takes{}, claims{};
        std::int64_t taken_ticket{}, claimed_ticket{};
    };
    thread_local FastBridgeProfile fast_profile;
    std::shared_ptr<const std::string> reply;
    bool status_enabled{};
    std::shared_ptr<const std::string> status_frame;
    std::uint64_t status_revision{};
    std::deque<std::string> input;
    bool crate_outcome_input_enabled{};
    std::string prop_poses;
    std::string local_prop_pose;
    std::string stock_loot_motion;
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

    bool timing_fixture_enabled();

    bool fast_profile_enabled()
    {
        static const bool enabled = timing_fixture_enabled();
        return enabled;
    }

    bool plain_bridge_token(std::string_view token)
    {
        return !token.empty() && token.size() <= 80 && std::ranges::all_of(token, [](char byte) {
            return (byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') ||
                (byte >= '0' && byte <= '9') || byte == '_' || byte == '.' || byte == '-';
        });
    }

    FastHeader classify_fast_header(std::string_view payload)
    {
        if (fast_actions.empty() || payload.empty() || payload.size() > command_limit) return {};
        const auto end = payload.find_last_not_of("\r\n");
        if (end == payload.npos) return {};
        const auto line = payload.substr(0, end + 1);
        const auto id_end = line.find('\t');
        if (id_end == line.npos || !plain_bridge_token(line.substr(0, id_end))) return {};
        const auto after_id = line.substr(id_end + 1);
        const auto action = after_id.substr(0, after_id.find('\t'));
        if (!std::binary_search(fast_actions.begin(), fast_actions.end(), action, std::less<>{}) ||
            line.find_first_of(std::string_view{"\r\n\0", 3}) != line.npos) return {};
        const auto fields = std::ranges::count(line, '\t') + 1;
        return fields <= 256 ? FastHeader{line.size(), static_cast<int>(fields)} : FastHeader{};
    }

    template <bool profile>
    int dispatch_fast_command(lua_State* stack)
    {
        if (!fast_queued.load(std::memory_order_acquire))
        {
            if constexpr (profile) ++fast_profile.skips;
            return 0;
        }
        const auto takes = profile ? fast_profile.takes : 0;
        if constexpr (profile) ++fast_profile.invocations;
        lua_pushvalue(stack, lua_upvalueindex(1));
        lua_call(stack, 0, 0);
        if constexpr (profile)
        {
            if (fast_profile.takes != takes && fast_profile.taken_ticket == fast_profile.claimed_ticket)
                ++fast_profile.claims;
        }
        return 0;
    }

    int make_fast_command_callback(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 2 || lua_type(stack, 1) != LUA_TTABLE || !lua_isfunction(stack, 2)) return 0;
        std::vector<std::string> actions;
        lua_pushnil(stack);
        while (lua_next(stack, 1))
        {
            if (lua_type(stack, -2) != LUA_TSTRING || !lua_isboolean(stack, -1) || !lua_toboolean(stack, -1) ||
                actions.size() >= 128)
            {
                lua_settop(stack, 2);
                return 0;
            }
            std::size_t size{};
            const auto* raw = lua_tolstring(stack, -2, &size);
            const std::string_view action{raw, size};
            if (!plain_bridge_token(action))
            {
                lua_settop(stack, 2);
                return 0;
            }
            actions.emplace_back(action);
            lua_pop(stack, 1);
        }
        if (actions.empty()) return 0;
        std::ranges::sort(actions);
        {
            std::lock_guard lock(mutex);
            fast_actions = std::move(actions);
            fast_header = state == State::queued ? classify_fast_header(command) : FastHeader{};
            fast_queued.store(fast_header.fields != 0, std::memory_order_release);
        }
        lua_pushvalue(stack, 2);
        lua_pushcclosure(stack, fast_profile_enabled() ? dispatch_fast_command<true> : dispatch_fast_command<false>, 1);
        return 1;
    }

    int find_object_by_path(const Lua& lua)
    {
        namespace Unreal = RC::Unreal;
        if (!Unreal::IsInGameThreadRaw() || !Unreal::UnrealInitializer::ShouldUseHashTables()) return 0;
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 1 || lua_type(stack, 1) != LUA_TSTRING) return 0;
        std::size_t size{};
        const auto* raw = lua_tolstring(stack, 1, &size);
        constexpr int path_limit = 1024;
        if (!raw || size == 0 || size > path_limit) return 0;
        const std::string_view path{raw, size};
        const auto mount_end = path.find('/', 1);
        const auto package_end = path.find('.');
        if (!path.starts_with('/') || mount_end == std::string_view::npos || mount_end < 2 ||
            (package_end != std::string_view::npos &&
                (package_end <= mount_end + 1 || path.find('/', package_end) != std::string_view::npos)) ||
            path.substr(0, package_end).find(':') != std::string_view::npos) return 0;

        bool has_component = false;
        for (const unsigned char byte : path.substr(1))
        {
            if (byte <= ' ' || byte == 0x7f || byte == '\\' || byte == '\'' || byte == '"') return 0;
            if (byte == '/' || byte == '.' || byte == ':')
            {
                if (!has_component) return 0;
                has_component = false;
            }
            else has_component = true;
        }
        if (!has_component) return 0;

        std::array<wchar_t, path_limit + 1> wide{};
        if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, raw, static_cast<int>(size), wide.data(), path_limit))
            return 0;
        // The general UE4SS FindObject walks all UObjects after a hash miss.
        // A path to an unloaded object may legitimately miss around travel.
        auto* object = Unreal::FUObjectHashTables::StaticFindObjectByPath(nullptr, wide.data(), false, true);
        constexpr auto destroyed = static_cast<Unreal::EObjectFlags>(Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        if (object && (object->HasAnyFlags(destroyed) || object->IsUnreachable())) object = nullptr;
        RC::LuaType::auto_construct_object(lua, object);
        return 1;
    }

    void clear()
    {
        active_ticket = 0;
        state = State::idle;
        fast_header = {};
        fast_queued.store(false, std::memory_order_release);
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
                fast_queued.store(false, std::memory_order_release);
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

    int take_fast_command(const Lua& lua)
    {
        if (lua.get_stack_size() != 0 || !fast_queued.load(std::memory_order_acquire)) return 0;
        std::string pending;
        FastHeader header;
        std::int64_t ticket{};
        {
            std::lock_guard lock(mutex);
            if (state != State::queued || !fast_header.fields) return 0;
            state = State::taken;
            fast_queued.store(false, std::memory_order_release);
            pending = std::move(command);
            header = fast_header;
            ticket = active_ticket;
        }
        if (fast_profile_enabled())
        {
            ++fast_profile.takes;
            fast_profile.taken_ticket = ticket;
        }
        lua.set_integer(ticket);
        auto* stack = lua.get_lua_state();
        lua_createtable(stack, header.fields, 0);
        const std::string_view line{pending.data(), header.length};
        std::size_t start{};
        for (int field = 1; field <= header.fields; ++field)
        {
            const auto end = line.find('\t', start);
            const auto value = line.substr(start, end == line.npos ? end : end - start);
            lua_pushlstring(stack, value.data(), value.size());
            lua_rawseti(stack, -2, field);
            if (end != line.npos) start = end + 1;
        }
        return 2;
    }

    int read_fast_profile(const Lua& lua)
    {
        if (lua.get_stack_size() != 0) return 0;
        lua.set_integer(fast_profile.skips);
        lua.set_integer(fast_profile.invocations);
        lua.set_integer(fast_profile.takes);
        lua.set_integer(fast_profile.claims);
        return 4;
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
        if (claimed && fast_profile_enabled()) fast_profile.claimed_ticket = ticket;
        lua.set_bool(claimed);
        return 1;
    }

    int stash_body_limit(const Lua& lua)
    {
        lua.set_integer(silverfish::local_catalog_ticket::stash_body_limit);
        return 1;
    }

    constexpr lua_Integer status_row_slots = 1024;
    constexpr std::size_t status_row_reuse_limit = 512;
    char status_row_cache_key;

    void push_status_row(lua_State* stack, std::string_view row)
    {
        // Lua already interns strings up to 40 bytes. Keep longer recurring rows per
        // Lua VM with a bounded array, and compare all bytes before reusing a match.
        if (row.size() <= 40 || row.size() > status_row_reuse_limit)
        {
            lua_pushlstring(stack, row.data(), row.size());
            return;
        }
        if (lua_rawgetp(stack, LUA_REGISTRYINDEX, &status_row_cache_key) != LUA_TTABLE)
        {
            lua_pop(stack, 1);
            lua_createtable(stack, status_row_slots, 0);
            lua_pushvalue(stack, -1);
            lua_rawsetp(stack, LUA_REGISTRYINDEX, &status_row_cache_key);
        }
        const auto slot = 1 + static_cast<lua_Integer>(std::hash<std::string_view>{}(row) % status_row_slots);
        std::size_t size{};
        const auto* existing = lua_rawgeti(stack, -1, slot) == LUA_TSTRING ? lua_tolstring(stack, -1, &size) : nullptr;
        if (!existing || std::string_view{existing, size} != row)
        {
            lua_pop(stack, 1);
            lua_pushlstring(stack, row.data(), row.size());
            lua_pushvalue(stack, -1);
            lua_rawseti(stack, -3, slot);
        }
        lua_remove(stack, -2);
    }

    std::string_view number_field(lua_State* stack, int index, std::array<char, 64>& buffer)
    {
        if (lua_isinteger(stack, index))
        {
            const auto size = std::snprintf(buffer.data(), buffer.size(), LUA_INTEGER_FMT,
                static_cast<LUAI_UACINT>(lua_tointeger(stack, index)));
            if (size > 0 && static_cast<std::size_t>(size) < buffer.size()) return {buffer.data(), std::size_t(size)};
            return {};
        }
        auto size = std::snprintf(buffer.data(), buffer.size(), LUA_NUMBER_FMT,
            static_cast<LUAI_UACNUMBER>(lua_tonumber(stack, index)));
        if (size <= 0 || static_cast<std::size_t>(size) >= buffer.size() - 2) return {};
        // Match Lua's stock presentation for integral floats, including negative zero.
        if (buffer[std::strspn(buffer.data(), "-0123456789")] == '\0')
        {
            buffer[size++] = lua_getlocaledecpoint();
            buffer[size++] = '0';
        }
        return {buffer.data(), std::size_t(size)};
    }

    template<bool reuse, bool stack_numbers = true>
    int format_status_row(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        const auto count = lua_gettop(stack);
        std::string row;
        for (int index = 1; index <= count; ++index)
        {
            if (index > 1) row.push_back('\t');
            const auto type = lua_type(stack, index);
            switch (type)
            {
            case LUA_TBOOLEAN:
                if (lua_toboolean(stack, index)) row.append("true");
                break;
            case LUA_TNUMBER:
            case LUA_TSTRING:
            {
                std::array<char, 64> buffer;
                std::string_view field;
                if constexpr (stack_numbers)
                {
                    if (type == LUA_TNUMBER)
                    {
                        field = number_field(stack, index, buffer);
                        if (field.empty()) return 0;
                    }
                }
                if (!field.data())
                {
                    std::size_t length{};
                    const auto* text = lua_tolstring(stack, index, &length);
                    field = {text, length};
                }
                const auto start = row.size();
                row.append(field);
                for (std::size_t field_index = start; field_index < row.size(); ++field_index)
                {
                    auto& byte = row[field_index];
                    if (std::iscntrl(static_cast<unsigned char>(byte))) byte = ' ';
                }
                break;
            }
            default:
                // Preserve Lua's handling of nils and custom tostring behavior.
                return 0;
            }
        }
        if constexpr (reuse)
            push_status_row(stack, row);
        else
            lua.set_string(row);
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

    namespace Hooks = RC::Unreal::Hook;

    struct TickMark
    {
        std::int64_t micros{};
        ULONG64 cycles{};
    };

    struct EngineInterval
    {
        std::int64_t sequence{}, micros{}, pre_micros{}, engine_micros{}, between_micros{};
        ULONG64 cycles{}, pre_cycles{}, engine_cycles{}, between_cycles{};
        std::int64_t viewport_calls{}, viewport_micros{};
        ULONG64 viewport_cycles{};
    };

    struct EngineTickProfile
    {
        bool active{};
        TickMark early, late, post;
        std::int64_t count{};
        EngineInterval longest, longest_pre_tick, longest_engine_tick;
        std::int64_t scheduler_inside{}, scheduler_outside{};
        RC::Unreal::UGameViewportClient* viewport{};
        TickMark viewport_start;
        bool viewport_unpaired{};
        std::int64_t viewport_calls{}, viewport_micros{};
        ULONG64 viewport_cycles{};
        std::int64_t viewport_frames{}, viewport_unpaired_frames{}, viewport_outside{};
    };

    thread_local EngineTickProfile engine_profile;
    Hooks::GlobalCallbackId engine_profile_early = Hooks::ERROR_ID;
    Hooks::GlobalCallbackId engine_profile_late = Hooks::ERROR_ID;
    Hooks::GlobalCallbackId engine_profile_post = Hooks::ERROR_ID;
    Hooks::GlobalCallbackId viewport_profile_pre = Hooks::ERROR_ID;
    Hooks::GlobalCallbackId viewport_profile_post = Hooks::ERROR_ID;
    constexpr auto engine_profile_owner = STR("SilverfishCoopNative");

    TickMark tick_mark()
    {
        TickMark mark{};
        if (QueryThreadCycleTime(GetCurrentThread(), &mark.cycles))
            mark.micros = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count();
        return mark;
    }

    void record_engine_interval()
    {
        auto& sample = engine_profile;
        if (!sample.active) return;
        const auto now = tick_mark();
        const auto [early, late, post] = std::tuple{sample.early, sample.late, sample.post};
        if (early.micros > 0 && late.micros >= early.micros && post.micros >= late.micros && now.micros >= post.micros)
        {
            EngineInterval interval{++sample.count, now.micros - early.micros, late.micros - early.micros,
                post.micros - late.micros, now.micros - post.micros, now.cycles - early.cycles,
                late.cycles - early.cycles, post.cycles - late.cycles, now.cycles - post.cycles};
            if (sample.viewport_unpaired || sample.viewport || sample.viewport_micros > interval.engine_micros ||
                sample.viewport_cycles > interval.engine_cycles)
            {
                ++sample.viewport_unpaired_frames;
                interval.viewport_calls = -1;
            }
            else if (sample.viewport_calls > 0)
            {
                ++sample.viewport_frames;
                interval.viewport_calls = sample.viewport_calls;
                interval.viewport_micros = sample.viewport_micros;
                interval.viewport_cycles = sample.viewport_cycles;
            }
            if (interval.micros > sample.longest.micros) sample.longest = interval;
            if (!sample.longest_pre_tick.sequence || interval.pre_micros > sample.longest_pre_tick.pre_micros)
                sample.longest_pre_tick = interval;
            if (!sample.longest_engine_tick.sequence || interval.engine_micros > sample.longest_engine_tick.engine_micros)
                sample.longest_engine_tick = interval;
        }
        sample.early = now;
        sample.late = sample.post = {};
        sample.viewport = nullptr;
        sample.viewport_start = {};
        sample.viewport_unpaired = false;
        sample.viewport_calls = sample.viewport_micros = 0;
        sample.viewport_cycles = 0;
    }

    void begin_viewport_tick(RC::Unreal::UGameViewportClient* viewport)
    {
        auto& sample = engine_profile;
        if (!sample.active) return;
        if (sample.late.micros <= 0 || sample.post.micros > 0)
        {
            ++sample.viewport_outside;
            return;
        }
        if (!viewport || sample.viewport)
        {
            sample.viewport_unpaired = true;
            return;
        }
        sample.viewport = viewport;
        sample.viewport_start = tick_mark();
    }

    void end_viewport_tick(RC::Unreal::UGameViewportClient* viewport)
    {
        auto& sample = engine_profile;
        if (!sample.active || sample.late.micros <= 0 || sample.post.micros > 0) return;
        const auto now = tick_mark();
        const auto start = sample.viewport_start;
        if (!viewport || sample.viewport != viewport || start.micros < sample.late.micros ||
            now.micros < start.micros || now.cycles < start.cycles)
        {
            sample.viewport_unpaired = true;
            return;
        }
        sample.viewport = nullptr;
        ++sample.viewport_calls;
        sample.viewport_micros += now.micros - start.micros;
        sample.viewport_cycles += now.cycles - start.cycles;
    }

    void register_early_engine_tick_profile()
    {
        if (engine_profile_early != Hooks::ERROR_ID) return;
        // The Lua mod has not loaded its script yet. Its delayed EngineTick
        // callbacks are installed later. A witness verifies the order at run time.
        engine_profile_early = Hooks::RegisterEngineTickPreCallback(
            [](auto&, RC::Unreal::UEngine*, float, bool) { record_engine_interval(); },
            {false, false, engine_profile_owner, STR("Measure before the diagnostic Lua schedule")});
    }

    void unregister_tick_profile_hook(Hooks::GlobalCallbackId& hook)
    {
        if (hook != Hooks::ERROR_ID) Hooks::UnregisterCallback(hook);
        hook = Hooks::ERROR_ID;
    }

    void stop_engine_tick_profile()
    {
        for (auto* hook : {&engine_profile_early, &engine_profile_late, &engine_profile_post,
                          &viewport_profile_pre, &viewport_profile_post}) unregister_tick_profile_hook(*hook);
    }

    int begin_engine_tick_profile(const Lua& lua)
    {
        if (lua.get_stack_size() != 0 || engine_profile.active || engine_profile_early == Hooks::ERROR_ID) return 0;
        if (engine_profile_late == Hooks::ERROR_ID)
        {
            engine_profile_late = Hooks::RegisterEngineTickPreCallback(
                [](auto&, RC::Unreal::UEngine*, float, bool) {
                    if (engine_profile.active) engine_profile.late = tick_mark();
                }, {false, false, engine_profile_owner, STR("Measure after the diagnostic Lua schedule")});
            engine_profile_post = Hooks::RegisterEngineTickPostCallback(
                [](auto&, RC::Unreal::UEngine*, float, bool) {
                    if (engine_profile.active) engine_profile.post = tick_mark();
                }, {false, false, engine_profile_owner, STR("Measure the end of a diagnostic engine tick")});
            if (engine_profile_late == Hooks::ERROR_ID || engine_profile_post == Hooks::ERROR_ID)
            {
                stop_engine_tick_profile();
                return 0;
            }
            viewport_profile_pre = Hooks::RegisterGameViewportClientTickPreCallback(
                [](auto&, RC::Unreal::UGameViewportClient* viewport, float) { begin_viewport_tick(viewport); },
                {false, false, engine_profile_owner, STR("Begin diagnostic viewport tick")});
            viewport_profile_post = Hooks::RegisterGameViewportClientTickPostCallback(
                [](auto&, RC::Unreal::UGameViewportClient* viewport, float) { end_viewport_tick(viewport); },
                {false, false, engine_profile_owner, STR("End diagnostic viewport tick")});
            if (viewport_profile_pre == Hooks::ERROR_ID || viewport_profile_post == Hooks::ERROR_ID)
            {
                unregister_tick_profile_hook(viewport_profile_pre);
                unregister_tick_profile_hook(viewport_profile_post);
            }
        }
        engine_profile = {};
        engine_profile.active = true;
        lua.set_bool(true);
        return 1;
    }

    int witness_engine_tick_profile(const Lua&)
    {
        auto& sample = engine_profile;
        if (sample.active && sample.early.micros > 0)
        {
            if (sample.late.micros == 0)
                ++sample.scheduler_inside;
            else
                ++sample.scheduler_outside;
        }
        return 0;
    }

    int finish_engine_tick_profile(const Lua& lua)
    {
        auto& sample = engine_profile;
        if (lua.get_stack_size() != 0 || !sample.active) return 0;
        sample.active = false;
        const auto& peak = sample.longest;
        lua.set_integer(sample.count);
        lua.set_integer(peak.micros);
        lua.set_integer(peak.pre_micros);
        lua.set_integer(peak.engine_micros);
        lua.set_integer(peak.between_micros);
        lua.set_integer(static_cast<std::int64_t>(peak.cycles));
        lua.set_integer(static_cast<std::int64_t>(peak.pre_cycles));
        lua.set_integer(static_cast<std::int64_t>(peak.engine_cycles));
        lua.set_integer(static_cast<std::int64_t>(peak.between_cycles));
        lua.set_integer(sample.scheduler_inside);
        lua.set_integer(sample.scheduler_outside);
        std::string phases = std::to_string(sample.viewport_frames);
        const auto append = [&phases](auto value) { phases += ','; phases += std::to_string(value); };
        append(sample.viewport_unpaired_frames);
        append(sample.viewport_outside);
        for (const auto* interval : std::array<const EngineInterval*, 3>{&peak, &sample.longest_pre_tick, &sample.longest_engine_tick})
        {
            phases += ';';
            phases += std::to_string(interval->sequence);
            append(interval->micros);
            append(interval->pre_micros);
            append(interval->engine_micros);
            append(interval->between_micros);
            append(interval->cycles);
            append(interval->pre_cycles);
            append(interval->engine_cycles);
            append(interval->between_cycles);
            append(interval->viewport_calls);
            append(interval->viewport_micros);
            append(interval->viewport_cycles);
        }
        lua.set_string(phases);
        return 12;
    }

    int enqueue_input(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        lua.set_bool(silverfish::local_bridge::queue_input(std::string{lua.get_string()}));
        return 1;
    }

    void publish_reply_frame(std::int64_t ticket, const std::shared_ptr<const std::string>& snapshot)
    {
        std::lock_guard lock(mutex);
        if (state != State::running || ticket != active_ticket) return;
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
    }

    int publish(const Lua& lua)
    {
        if (lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_string(2)) return 0;
        // UE4SS removes each argument as it is read.
        const auto ticket = lua.get_integer();
        const auto frame = lua.get_string();
        publish_reply_frame(ticket, !frame.empty() && frame.size() <= reply_limit
            ? std::make_shared<const std::string>(frame) : nullptr);
        return 0;
    }

    int publish_status_frame(const Lua& lua, std::shared_ptr<const std::string> snapshot)
    {
        bool published{};
        {
            // Snapshot publication should not wait for gameplay ticket or input queue work.
            std::lock_guard lock(status_mutex);
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

    int publish_status(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_string()) return 0;
        const auto frame = lua.get_string();
        return publish_status_frame(lua, !frame.empty() && frame.size() <= reply_limit
            ? std::make_shared<const std::string>(frame) : nullptr);
    }

    std::shared_ptr<const std::string> join_status_rows(lua_State* stack, int index)
    {
        const auto count = lua_rawlen(stack, index);
        if (count > reply_limit + 1) return {};
        std::string frame;
        frame.reserve((std::min)(count * 160, std::size_t{65536}));
        for (std::size_t row_index = 1; row_index <= count; ++row_index)
        {
            lua_rawgeti(stack, index, static_cast<lua_Integer>(row_index));
            std::size_t size{};
            const auto* row = lua_type(stack, -1) == LUA_TSTRING ? lua_tolstring(stack, -1, &size) : nullptr;
            const std::size_t separator = row_index != 1;
            if (!row || size + separator > reply_limit - frame.size())
            {
                lua_pop(stack, 1);
                return {};
            }
            if (separator) frame.push_back('\n');
            frame.append(row, size);
            lua_pop(stack, 1);
        }
        return !frame.empty() ? std::make_shared<const std::string>(std::move(frame)) : nullptr;
    }

    int publish_status_rows(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 1 || lua_type(stack, 1) != LUA_TTABLE) return 0;
        return publish_status_frame(lua, join_status_rows(stack, 1));
    }

    int publish_reply_rows(const Lua& lua)
    {
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 3 || !lua_isinteger(stack, 1) ||
            lua_type(stack, 2) != LUA_TTABLE || lua_type(stack, 3) != LUA_TBOOLEAN) return 0;
        const auto ticket = lua_tointeger(stack, 1);
        const auto periodic = lua_toboolean(stack, 3);
        auto frame = join_status_rows(stack, 2);
        publish_reply_frame(ticket, frame);
        if (periodic) return publish_status_frame(lua, std::move(frame));
        return 0;
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

    int publish_stock_loot_motion(const Lua& lua)
    {
        return publish_presentation(lua, stock_loot_motion, stock_loot_motion_limit);
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
    std::scoped_lock lock(mutex, status_mutex);
    attached = false;
    status_enabled = false;
    status_frame.reset();
    input.clear();
    crate_outcome_input_enabled = false;
    prop_poses.clear();
    local_prop_pose.clear();
    stock_loot_motion.clear();
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
    std::scoped_lock lock(mutex, status_mutex);
    status_enabled = attached;
    return status_enabled;
}

extern "C" __declspec(dllexport) std::uint64_t SilverfishBridgeStatusRevisionV1()
{
    std::lock_guard lock(status_mutex);
    return status_frame ? status_revision : 0;
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeCopyStatusV1(
    std::uint64_t previous, char* buffer, std::uint32_t capacity, std::uint64_t* revision)
{
    if (!buffer || !revision) return 0;
    std::shared_ptr<const std::string> snapshot;
    std::uint64_t version{};
    {
        std::lock_guard lock(status_mutex);
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

extern "C" __declspec(dllexport) int SilverfishBridgeEnableCrateOutcomeInputV1(std::uint32_t record_capacity)
{
    std::lock_guard lock(mutex);
    if (!attached || record_capacity < crate_outcome_input_limit) return 0;
    crate_outcome_input_enabled = true;
    return 1;
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

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeReadInputBatchV1(
    char* buffer, std::uint32_t capacity, std::uint16_t* lengths, std::uint32_t limit)
{
    if (!buffer || !lengths || !limit || limit > input_capacity || capacity < limit * input_limit) return 0;
    std::array<std::string, input_capacity> pending;
    std::uint32_t count{};
    {
        std::lock_guard lock(mutex);
        std::size_t remaining = capacity;
        while (count < limit && !input.empty() && input.front().size() <= remaining)
        {
            remaining -= input.front().size();
            pending[count++] = std::move(input.front());
            input.pop_front();
        }
    }
    for (std::uint32_t index = 0; index < count; ++index)
    {
        const auto& record = pending[index];
        lengths[index] = static_cast<std::uint16_t>(record.size());
        std::memcpy(buffer, record.data(), record.size());
        buffer += record.size();
    }
    return count;
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

extern "C" __declspec(dllexport) std::uint32_t SilverfishBridgeTakeStockLootMotionV1(char* buffer, std::uint32_t capacity)
{
    return take_local_pose(stock_loot_motion, buffer, capacity);
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
    return silverfish::local_catalog_ticket::stash_body_limit;
}

extern "C" __declspec(dllexport) std::int64_t SilverfishBridgeSubmitV1(const char* data, std::uint32_t size)
{
    if (!data || !size) return 0;
    std::string large_catalog;
    if (size > command_limit)
    {
        if (!silverfish::local_catalog_ticket::accepts({data, size})) return 0;
        large_catalog.assign(data, size);
    }
    std::lock_guard lock(mutex);
    if (!attached || state != State::idle || last_ticket == std::numeric_limits<std::int64_t>::max()) return 0;
    if (large_catalog.empty()) command.assign(data, size);
    else command = std::move(large_catalog);
    active_ticket = ++last_ticket;
    state = State::queued;
    fast_header = classify_fast_header(command);
    fast_queued.store(fast_header.fields != 0, std::memory_order_release);
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
    if (state == State::queued || state == State::taken)
    {
        // Retire the ticket while holding the same lock as claim_command.
        // A command cancelled here could not have reached a stock mutation.
        clear();
        return -2;
    }
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
    bool queue_input(std::string record)
    {
        std::lock_guard lock(mutex);
        const auto limit = crate_outcome_input_enabled && record.starts_with("loot-crate-open\t")
            ? crate_outcome_input_limit : input_limit;
        if (!attached || record.empty() || record.size() > limit || input.size() >= input_capacity) return false;
        input.emplace_back(std::move(record));
        return true;
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        lua.register_function("SilverfishFindObjectByPath", find_object_by_path);
        lua.register_function("SilverfishTakeLocalBridgeCommand", take);
        lua.register_function("SilverfishClaimLocalBridgeCommand", claim_command);
        lua.register_function("SilverfishMakeFastLocalBridgeCallback", make_fast_command_callback);
        lua.register_function("SilverfishTakeFastLocalBridgeCommand", take_fast_command);
        lua.register_function("SilverfishLocalBridgeStashBodyLimit", stash_body_limit);
        lua.register_function("SilverfishFormatLocalStatusRow", format_status_row<true>);
        lua.register_function("SilverfishPublishLocalBridgeReply", publish);
        lua.register_function("SilverfishPublishLocalBridgeStatus", publish_status);
        lua.register_function("SilverfishPublishLocalBridgeStatusRows", publish_status_rows);
        lua.register_function("SilverfishPublishLocalBridgeReplyRows", publish_reply_rows);
        lua.register_function("SilverfishEnqueueLocalBridgeInput", enqueue_input);
        lua.register_function("SilverfishTakeLocalPropPoses", take_prop_poses);
        lua.register_function("SilverfishPublishLocalPropPose", publish_local_prop_pose);
        lua.register_function("SilverfishPublishStockLootMotion", publish_stock_loot_motion);
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
        if (timing_fixture_enabled())
        {
            lua.register_function("SilverfishReadFastLocalBridgeProfile", read_fast_profile);
            lua.register_function("SilverfishBridgeProfileStamp", profile_stamp);
            register_early_engine_tick_profile();
            lua.register_function("SilverfishBeginEngineTickProfile", begin_engine_tick_profile);
            lua.register_function("SilverfishWitnessEngineTickProfile", witness_engine_tick_profile);
            lua.register_function("SilverfishFinishEngineTickProfile", finish_engine_tick_profile);
            lua.register_function("SilverfishFormatLocalStatusRowUncached", format_status_row<false>);
            lua.register_function("SilverfishFormatLocalStatusRowWithLuaNumbers", format_status_row<true, false>);
        }
    }

    void stop()
    {
        stop_engine_tick_profile();
        std::scoped_lock lock(mutex, status_mutex);
        ready = false;
        attached = false;
        status_enabled = false;
        status_frame.reset();
        input.clear();
        crate_outcome_input_enabled = false;
        prop_poses.clear();
        local_prop_pose.clear();
        stock_loot_motion.clear();
        local_avatar_pose.clear();
        remote_avatar_poses.clear();
        remote_footsteps.clear();
        weapon_cues_enabled = false;
        clear_weapon_cues();
        native_events_heartbeat = stock_board_heartbeat = host_dig_heartbeat = 0;
        guest_interaction_lease = {};
        zone_heartbeat = 0;
        zone_roster.clear();
        fast_actions.clear();
        clear();
    }
}
