#include "source_pack.hpp"
#include <cassert>
#include <string>
int main() {
    const brq::SourcePack installed{"B1", "Data/PACK11.PAK", "current-file-hash"};
    assert(brq::source_pack_matches("current-file-hash", installed));
    assert(!brq::source_pack_matches("different-file-hash", installed));
    assert(!brq::source_pack_matches("", installed));
    const brq::SourcePack missing{"B1", "Data/PACK11.PAK", ""};
    assert(!brq::source_pack_matches("", missing));

    const brq::SourcePackStamp stamp{1'223'469'944, 134'000'000'000'000'000, 134'000'000'000'000'500, 133'999'000'000'000'000, 77, 0x1234};
    const std::string cache = "Data/PACK12.PAK|other|1|2|3|4|5\r\n" + brq::source_pack_cache_line(installed, stamp) + "\r\n";
    assert(brq::source_pack_cached(cache, installed, stamp));
    assert(brq::source_pack_cached(brq::source_pack_cache_line(installed, stamp), installed, stamp));

    // Any identity change, or a new expected digest from a rebuilt ASI, forces a full hash.
    auto changed = stamp;
    changed.last_write += 1;
    assert(!brq::source_pack_cached(cache, installed, changed));
    changed = stamp;
    changed.change += 1;
    assert(!brq::source_pack_cached(cache, installed, changed));
    changed = stamp;
    changed.change = 0;
    assert(!brq::source_pack_cached(brq::source_pack_cache_line(installed, changed), installed, changed));
    changed = stamp;
    changed.size -= 1;
    assert(!brq::source_pack_cached(cache, installed, changed));
    changed = stamp;
    changed.file_index += 1;
    assert(!brq::source_pack_cached(cache, installed, changed));
    changed = stamp;
    changed.volume_serial += 1;
    assert(!brq::source_pack_cached(cache, installed, changed));
    const brq::SourcePack rebuilt{"B1", "Data/PACK11.PAK", "next-file-hash"};
    assert(!brq::source_pack_cached(cache, rebuilt, stamp));
    const brq::SourcePack moved{"B1", "Data/PACK12.PAK", "current-file-hash"};
    assert(!brq::source_pack_cached(cache, moved, stamp));

    // Partial lines and unhashable inputs never count as verified.
    const std::string line = brq::source_pack_cache_line(installed, stamp);
    assert(!brq::source_pack_cached(line.substr(0, line.size() - 1), installed, stamp));
    assert(!brq::source_pack_cached(line + "9", installed, stamp));
    assert(!brq::source_pack_cached("", installed, stamp));
    assert(!brq::source_pack_cached(brq::source_pack_cache_line(missing, stamp), missing, stamp));
    assert(!brq::source_pack_cached(brq::source_pack_cache_line(installed, {}), installed, {}));
}
