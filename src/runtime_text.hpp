#pragma once
#include <array>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>

namespace brq {
struct RuntimeText {
    std::string_view segment, source, target;
};
struct RuntimeTextHook {
    std::string_view segment;
    std::array<std::uintptr_t, 2> rvas;
    std::array<std::array<std::uint8_t, 16>, 2> signatures;
};

inline std::string translate_runtime_text(std::string_view text, std::string_view segment,
                                          std::span<const RuntimeText> translations) {
    for (const auto& row : translations) {
        if (row.segment != segment) continue;
        const auto marker = row.source.find("%d");
        if (marker == std::string_view::npos) {
            if (text == row.source) return std::string{row.target};
            continue;
        }
        const auto tail = row.source.substr(marker + 2);
        if (text.size() <= marker + tail.size() || !text.starts_with(row.source.substr(0, marker)) ||
            !text.ends_with(tail)) continue;
        const auto number = text.substr(marker, text.size() - marker - tail.size());
        auto digits = number;
        if (digits.starts_with('-')) digits.remove_prefix(1);
        if (digits.empty() || digits.find_first_not_of("0123456789") != std::string_view::npos) continue;
        const auto target_marker = row.target.find("%d");
        if (target_marker == std::string_view::npos) continue;
        return std::string{row.target.substr(0, target_marker)} + std::string{number} +
               std::string{row.target.substr(target_marker + 2)};
    }
    return {};
}
}  // namespace brq
