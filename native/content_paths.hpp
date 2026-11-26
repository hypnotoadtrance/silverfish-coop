#pragma once

#include <algorithm>
#include <string_view>

namespace silverfish::content_paths
{
    template <typename Char>
    [[nodiscard]] bool item_class(std::basic_string_view<Char> path)
    {
        constexpr std::string_view root = "/Game/";
        constexpr std::string_view suffix = "_C";
        if (path.size() <= root.size() || path.size() > 256 ||
            !std::ranges::equal(path.substr(0, root.size()), root)) return false;
        const auto dot = path.find('.');
        if (dot == path.npos || path.find('.', dot + 1) != path.npos ||
            !std::ranges::equal(path.substr(path.size() - suffix.size()), suffix)) return false;
        const auto package = path.substr(root.size(), dot - root.size());
        if (package.empty() || package.front() == '/' || package.back() == '/' ||
            std::adjacent_find(package.begin(), package.end(), [](Char a, Char b) {
                return a == '/' && b == '/';
            }) != package.end()) return false;
        const auto slash = package.find_last_of('/');
        const auto name = slash == package.npos ? package : package.substr(slash + 1);
        return path.substr(dot + 1, path.size() - dot - 1 - suffix.size()) == name &&
            std::ranges::all_of(package, [](Char c) {
                return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '/';
            });
    }
}
