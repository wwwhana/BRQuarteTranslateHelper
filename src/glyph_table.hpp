#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <type_traits>
#include <utility>
#include <vector>

namespace brq {

struct GlyphRecord48 final {
    std::uint32_t key;
    std::uint32_t reserved;
    std::array<double, 8> metrics;
};

struct GlyphRecord28 final {
    std::uint32_t key;
    std::uint16_t x;
    std::uint16_t y;
    std::uint16_t width;
    std::uint16_t height;
    std::int32_t left;
    std::int32_t top;
    std::int32_t advance;
    std::int32_t bottom;
};

static_assert(std::endian::native == std::endian::little);
static_assert(sizeof(GlyphRecord48) == 0x48 && alignof(GlyphRecord48) >= alignof(double));
static_assert(sizeof(GlyphRecord28) == 0x1c);
static_assert(offsetof(GlyphRecord28, x) == 0x04 && offsetof(GlyphRecord28, height) == 0x0a);
static_assert(offsetof(GlyphRecord28, left) == 0x0c && offsetof(GlyphRecord28, advance) == 0x14);
static_assert(offsetof(GlyphRecord28, bottom) == 0x18);
static_assert(std::is_trivially_copyable_v<GlyphRecord48> && std::is_trivially_copyable_v<GlyphRecord28>);

inline bool take_glyph_probe(std::atomic<std::uint8_t>& count) noexcept {
    auto seen = count.load(std::memory_order_relaxed);
    while (seen < 16) {
        if (count.compare_exchange_weak(seen, static_cast<std::uint8_t>(seen + 1), std::memory_order_relaxed)) {
            return true;
        }
    }
    return false;
}

template <typename Record>
bool parse_glyph_table(std::span<const std::byte> bytes, std::vector<Record>& output) {
    static_assert(std::is_same_v<Record, GlyphRecord48> || std::is_same_v<Record, GlyphRecord28>);
    if (bytes.empty() || bytes.size() % sizeof(Record) != 0) {
        return false;
    }

    std::vector<Record> parsed(bytes.size() / sizeof(Record));
    std::memcpy(parsed.data(), bytes.data(), bytes.size());
    for (std::size_t index = 1; index < parsed.size(); ++index) {
        if (parsed[index - 1].key >= parsed[index].key) {
            return false;
        }
    }
    output = std::move(parsed);
    return true;
}

template <typename Record>
const Record* find_glyph(const std::vector<Record>& table, std::uint32_t key) noexcept {
    const auto found = std::lower_bound(table.begin(), table.end(), key,
                                        [](const Record& record, std::uint32_t value) { return record.key < value; });
    return found != table.end() && found->key == key ? &*found : nullptr;
}

constexpr std::uint32_t compatible_glyph_key(std::uint32_t key) noexcept {
    switch (key) {
        case 0x0000c389:  // É -> E
            return 0x00000045;
        case 0x0000cb96:  // ˖ -> +
            return 0x0000002b;
        case 0x00e296bf:  // ▿ -> ▽
            return 0x00e296bd;
        case 0x00e2979d:  // ◝ -> (
            return 0x00000028;
        case 0x00e2979c:  // ◜ -> )
            return 0x00000029;
        case 0x00e29ca7:  // ✧ -> ☆
            return 0x00e29886;
        case 0x00e28094:  // — -> ―
        case 0x00e383bc:  // ー -> ―
            return 0x00e28095;
        case 0x00e38087:  // 〇 -> ○
            return 0x00e2978b;
        case 0x00e3809c:  // 〜 -> ～
            return 0x00efbd9e;
        case 0x00e28892:  // − -> －
            return 0x00efbc8d;
        case 0x00efbdb0:  // ｰ -> -
            return 0x0000002d;
        default:
            return 0;
    }
}

constexpr bool is_packed_hangul_syllable(std::uint32_t key) noexcept {
    return key >= 0x00eab080 && key <= 0x00ed9ea3;
}

template <typename Record>
const Record* find_compatible_glyph(const std::vector<Record>& table, std::uint32_t key) noexcept {
    const auto compatible_key = compatible_glyph_key(key);
    return compatible_key != 0 ? find_glyph(table, compatible_key) : nullptr;
}

}  // namespace brq
