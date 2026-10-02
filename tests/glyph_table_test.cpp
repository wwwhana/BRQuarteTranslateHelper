#ifdef NDEBUG
#undef NDEBUG
#endif

#include "glyph_table.hpp"

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

namespace {

template <typename Record, std::size_t Size>
std::span<const std::byte> bytes_of(const std::array<Record, Size>& records) {
    return std::as_bytes(std::span{records});
}

}  // namespace

int main() {
    std::atomic<std::uint8_t> probes{0};
    std::atomic<std::uint8_t> diagnostic_probes{0};
    unsigned accepted{};
    unsigned diagnostic_accepted{};
    for (unsigned call = 0; call < 1024; ++call) {
        accepted += brq::take_glyph_probe(probes);
        diagnostic_accepted += brq::take_glyph_probe(diagnostic_probes);
    }
    assert(accepted == 16 && probes.load() == 16);
    assert(diagnostic_accepted == 16 && diagnostic_probes.load() == 16);

    const std::array<brq::GlyphRecord48, 3> source48{{{1, 0, {}}, {8, 0, {}}, {0xfedcba98, 0, {}}}};
    std::vector<brq::GlyphRecord48> table48;
    assert(brq::parse_glyph_table(bytes_of(source48), table48));
    assert(brq::find_glyph(table48, 1) == &table48[0]);
    assert(brq::find_glyph(table48, 7) == nullptr);
    assert(brq::find_glyph(table48, 0xfedcba98) == &table48[2]);

    const std::array<brq::GlyphRecord28, 2> source28{{{3, 1, 1024, 20, 24, -1, 2, 23, 0},
                                                   {9, 32, 1050, 21, 25, 0, 1, 22, 0}}};
    std::vector<brq::GlyphRecord28> table28;
    assert(brq::parse_glyph_table(bytes_of(source28), table28));
    assert(brq::find_glyph(table28, 9) == &table28[1]);
    assert(table28[0].x == 1 && table28[0].y == 1024 && table28[0].width == 20 &&
           table28[0].height == 24 && table28[0].left == -1 && table28[0].top == 2 &&
           table28[0].advance == 23);

    const std::array<brq::GlyphRecord48, 1> compatible_source{{{0x00e383bb, 0, {}}}};
    std::vector<brq::GlyphRecord48> compatible_table;
    assert(brq::parse_glyph_table(bytes_of(compatible_source), compatible_table));
    assert(brq::compatible_glyph_key(0x0000c2b7) == 0);
    assert(brq::compatible_glyph_key(0x00e383bb) == 0);
    assert(brq::compatible_glyph_key(0x0000c389) == 0x00000045);
    assert(brq::compatible_glyph_key(0x0000cb96) == 0x0000002b);
    assert(brq::compatible_glyph_key(0x00e296bf) == 0x00e296bd);
    assert(brq::compatible_glyph_key(0x00e29ca7) == 0x00e29886);
    assert(brq::compatible_glyph_key(0x00e38087) == 0x00e2978b);
    assert(brq::compatible_glyph_key(0x00e28094) == 0x00e28095);
    assert(brq::compatible_glyph_key(0x00000041) == 0);
    assert(brq::find_compatible_glyph(compatible_table, 0x0000c2b7) == nullptr);
    assert(brq::find_compatible_glyph(compatible_table, 0x00000041) == nullptr);
    assert(brq::is_packed_hangul_syllable(0x00eab080));
    assert(brq::is_packed_hangul_syllable(0x00ed9ea3));
    assert(!brq::is_packed_hangul_syllable(0x00e383bb));

    auto duplicate = source28;
    duplicate[1].key = duplicate[0].key;
    assert(!brq::parse_glyph_table(bytes_of(duplicate), table28));
    assert(table28.size() == 2 && table28[0].key == 3 && table28[1].key == 9);
    assert(!brq::parse_glyph_table(std::span<const std::byte>{}, table28));
    assert(!brq::parse_glyph_table(bytes_of(source28).first(sizeof(source28) - 1), table28));
    return 0;
}
