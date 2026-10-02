#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace brq {

enum class AssetKind : std::uint8_t {
    none,
    event,
    font,
    image,
    text,
    font_bundle,
};

enum class FontOpenPlan : std::uint8_t {
    original,
    replacement,
    fail_closed,
};

constexpr FontOpenPlan font_open_plan(bool replacement_committed, bool replacement_open_succeeded) noexcept {
    if (replacement_open_succeeded) {
        return FontOpenPlan::replacement;
    }
    return replacement_committed ? FontOpenPlan::fail_closed : FontOpenPlan::original;
}

constexpr bool korean_text_committed_after_open(bool committed, AssetKind kind, bool japanese_namespace,
                                                bool replacement_open_succeeded) noexcept {
    return committed || (replacement_open_succeeded && japanese_namespace &&
                         (kind == AssetKind::event || kind == AssetKind::text));
}

constexpr bool uses_root_file_bridge(std::string_view segment) noexcept {
    return segment == "B2" || segment == "B3" || segment == "B4";
}

constexpr bool original_fits_buffer(std::uint32_t original_size, std::uint32_t buffer_size) noexcept {
    return original_size != 0 && original_size <= buffer_size;
}

std::string clean_path(std::string_view raw);
std::string make_lookup_key(std::string_view clean);
bool is_safe_manifest_path(std::string_view path);
bool is_graphics_path(std::string_view lookup_key, std::string_view segment);
bool is_japanese_event_namespace(std::string_view lookup_key, std::string_view segment);
bool is_segment_text_namespace(std::string_view lookup_key, std::string_view segment);
bool is_japanese_text_namespace(std::string_view lookup_key, std::string_view segment);
std::uint8_t font_slot_for_request(std::string_view raw_path, std::string_view segment) noexcept;
AssetKind classify_lookup_key(std::string_view lookup_key, std::string_view segment);

}  // namespace brq
