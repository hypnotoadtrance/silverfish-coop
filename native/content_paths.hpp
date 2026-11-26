#pragma once

#include <algorithm>
#include <string_view>

namespace silverfish::content_paths
{
    template <typename Char>
    [[nodiscard]] constexpr bool item_class(std::basic_string_view<Char> path, std::size_t limit = 256)
    {
        constexpr std::string_view suffix = "_C";
        if (path.size() < 8 || path.size() > limit || path.front() != '/') return false;
        const auto dot = path.find('.');
        if (dot == path.npos || dot + suffix.size() + 1 >= path.size() ||
            path.find('.', dot + 1) != path.npos ||
            !std::ranges::equal(path.substr(path.size() - suffix.size()), suffix)) return false;
        const auto package = path.substr(1, dot - 1);
        const auto slash = package.find_last_of('/');
        if (slash == path.npos || package.front() == '/' || package.back() == '/' ||
            std::adjacent_find(package.begin(), package.end(), [](Char a, Char b) {
                return a == '/' && b == '/';
            }) != package.end()) return false;
        const auto name = package.substr(slash + 1);
        return path.substr(dot + 1, path.size() - dot - 1 - suffix.size()) == name &&
            std::ranges::all_of(package, [](Char c) {
                return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '/';
            });
    }
}
