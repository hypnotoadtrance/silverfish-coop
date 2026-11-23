#include "../../native/local_stash_ticket.hpp"

#include <cassert>
#include <string>

int main()
{
    using namespace silverfish::local_stash_ticket;
    const std::string id(32, 'a');
    const std::string header = id + "\tnative-stash-view-render-memory\t1\t/Game/Observatory\t" +
        std::string(32, 'A') + "\t511\n";
    const auto body = std::string(32 * 1024, 'x') + "\n";
    assert(accepts(header + body));
    assert(accepts(header + std::string(body_limit - 1, 'x') + "\n"));
    assert(!accepts(header + std::string(body_limit, 'x') + "\n"));
    assert(!accepts(header + std::string(32 * 1024, 'x')));
    assert(!accepts(id + "\tnative-stash-view-render-memory-extra\t1\t/map\tA\t1\n" + body));
    assert(!accepts(id + "\tordinary-command\t1\t/map\tA\t1\n" + body));
    assert(!accepts(id + "\tnative-stash-view-render-memory\t\t/map\tA\t1\n" + body));
    assert(!accepts(id + "\tnative-stash-view-render-memory\t1\t/map\tA\t1\tsecond\n" + body));
    assert(!accepts(id + "\tnative-stash-view-render-memory\t1\t" + std::string(header_limit, 'M') + "\tA\t1\n" + body));
    assert(!accepts("X" + header.substr(1) + body));
}
