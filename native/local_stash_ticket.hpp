#pragma once

#include <cstdint>
#include <string_view>

namespace silverfish::local_stash_ticket
{
    inline constexpr std::uint32_t body_limit = 2'500'000;
    inline constexpr std::uint32_t header_limit = 1'024;

    inline bool accepts(std::string_view request)
    {
        constexpr std::string_view verb = "\tnative-stash-view-render-memory\t";
        constexpr auto id_size = 32u;
        if (request.size() <= id_size + verb.size() || request.size() > body_limit + header_limit ||
            request.substr(id_size, verb.size()) != verb)
        {
            return false;
        }
        for (const char digit : request.substr(0, id_size))
        {
            if (!(digit >= '0' && digit <= '9') && !(digit >= 'a' && digit <= 'f')) return false;
        }

        const auto boundary = request.substr(0, header_limit).find('\n');
        const auto scope_start = id_size + verb.size();
        if (boundary == request.npos || boundary <= scope_start || boundary + 1 == request.size() ||
            request.size() - boundary - 1 > body_limit || request.back() != '\n')
        {
            return false;
        }

        unsigned separators{};
        bool empty_field = true;
        for (const char ch : request.substr(scope_start, boundary - scope_start))
        {
            if (ch == '\t')
            {
                if (empty_field) return false;
                ++separators;
                empty_field = true;
            }
            else
            {
                if (ch < ' ' || ch > '~') return false;
                empty_field = false;
            }
        }
        return separators == 3 && !empty_field;
    }
}
