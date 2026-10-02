#pragma once

#include <cstdint>
#include <string_view>

namespace brq {

inline constexpr std::uint32_t b0_index_hash_offset = 2166136261U;
inline constexpr std::uint32_t b0_index_hash_prime = 16777619U;
inline constexpr std::uint32_t b0_index_bucket_mask = 127U;

constexpr std::uint32_t b0_index_hash(std::string_view path) noexcept {
    std::uint32_t hash = b0_index_hash_offset;
    for (const char value : path) {
        hash ^= static_cast<std::uint8_t>(value);
        hash *= b0_index_hash_prime;
    }
    return hash;
}

constexpr std::uint32_t b0_index_bucket(std::string_view path) noexcept {
    return b0_index_hash(path) & b0_index_bucket_mask;
}

inline std::uint32_t b0_index_bucket_c_string(const char* path) noexcept {
    std::uint32_t hash = b0_index_hash_offset;
    if (path != nullptr) {
        while (*path != '\0') {
            hash ^= static_cast<std::uint8_t>(*path++);
            hash *= b0_index_hash_prime;
        }
    }
    return hash & b0_index_bucket_mask;
}

}  // namespace brq
