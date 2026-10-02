#pragma once
#include <cstdint>
#include <string>
#include <string_view>
namespace brq {
struct SourcePack { const char* segment; const char* path; const char* sha256; };
inline bool source_pack_matches(std::string_view digest, const SourcePack& expected) noexcept {
    return !digest.empty() && digest == expected.sha256;
}

// Identity of an opened PAK. A replaced or rewritten file changes its file index, size or change time;
// unlike the write time, the change time cannot be set back by ordinary programs.
struct SourcePackStamp {
    std::uint64_t size{}, last_write{}, change{}, creation{}, file_index{};
    std::uint32_t volume_serial{};
};

// One cache line records that this exact file once hashed to the expected digest.
inline std::string source_pack_cache_line(const SourcePack& expected, const SourcePackStamp& stamp) {
    return std::string{expected.path} + '|' + expected.sha256 + '|' + std::to_string(stamp.size) + '|' +
           std::to_string(stamp.last_write) + '|' + std::to_string(stamp.change) + '|' + std::to_string(stamp.creation) + '|' +
           std::to_string(stamp.volume_serial) + '|' + std::to_string(stamp.file_index);
}

inline bool source_pack_cached(std::string_view cache, const SourcePack& expected, const SourcePackStamp& stamp) {
    if (expected.sha256[0] == '\0' || stamp.size == 0 || stamp.change == 0) return false;
    const std::string line = source_pack_cache_line(expected, stamp);
    for (std::size_t begin = 0; begin < cache.size();) {
        auto end = cache.find('\n', begin);
        if (end == std::string_view::npos) end = cache.size();
        auto row = cache.substr(begin, end - begin);
        if (!row.empty() && row.back() == '\r') row.remove_suffix(1);
        if (row == line) return true;
        begin = end + 1;
    }
    return false;
}
}
