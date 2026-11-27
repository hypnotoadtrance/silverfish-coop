#pragma once

#include <cstdint>
#include <string_view>

namespace silverfish::local_catalog_ticket
{
    inline constexpr std::uint32_t stash_body_limit = 2'500'000;
    inline constexpr std::uint32_t shop_body_limit = 90'000;
    inline constexpr std::uint32_t header_limit = 1'024;

    inline bool accepts(std::string_view request)
    {
        constexpr std::string_view stash_verb = "\tnative-stash-view-render-memory\t";
        constexpr std::string_view shop_verb = "\tnative-shop-view-render-stock\t";
        constexpr auto id_size = 32u;
        if (request.size() <= id_size) return false;
        const auto command = request.substr(id_size);
        const bool stash = command.starts_with(stash_verb);
        if (!stash && !command.starts_with(shop_verb)) return false;
        const auto verb = stash ? stash_verb : shop_verb;
        const auto body_limit = stash ? stash_body_limit : shop_body_limit;
        if (request.size() > body_limit + header_limit) return false;
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
        return separators == (stash ? 3u : 5u) && !empty_field;
    }
}
