#pragma once

#include <string_view>

namespace silverfish::world_path
{
    // Canonical Unreal long package names. Asset existence is checked separately
    // through each seat's Asset Registry before admitting an unlisted destination.
    template <class Char>
    constexpr bool package(std::basic_string_view<Char> path)
    {
        if (path.empty() || path.front() != '/' || path.back() == '/') return false;
        const auto separator = path.rfind('/');
        if (!separator || path.size() + 1 + path.substr(separator + 1).size() > 180) return false;
        bool after_slash = true;
        for (const auto c : path.substr(1))
        {
            if (c == '/')
            {
                if (after_slash) return false;
                after_slash = true;
                continue;
            }
            if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') || c == '_')) return false;
            after_slash = false;
        }
        return true;
    }

    template <class Char>
    constexpr bool object(std::basic_string_view<Char> path)
    {
        const auto dot = path.find('.');
        if (dot == path.npos) return false;
        const auto name = path.substr(0, dot);
        return package(name) && name.substr(name.rfind('/') + 1) == path.substr(dot + 1);
    }
}
