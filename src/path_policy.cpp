#include "path_policy.hpp"

#include <array>
#include <cctype>

namespace brq {
namespace {

char lower_ascii(char value) {
    if (value >= 'A' && value <= 'Z') {
        return static_cast<char>(value + ('a' - 'A'));
    }
    return value;
}

bool starts_with_ci(std::string_view value, std::string_view prefix) {
    if (value.size() < prefix.size()) {
        return false;
    }
    for (std::size_t index = 0; index < prefix.size(); ++index) {
        if (lower_ascii(value[index]) != lower_ascii(prefix[index])) {
            return false;
        }
    }
    return true;
}

bool ends_with(std::string_view value, std::string_view suffix) {
    return value.size() >= suffix.size() && value.substr(value.size() - suffix.size()) == suffix;
}

bool path_equals_ci(std::string_view value, std::string_view expected) noexcept {
    if (value.size() != expected.size()) {
        return false;
    }
    for (std::size_t index = 0; index < value.size(); ++index) {
        const char character = value[index] == '\\' ? '/' : lower_ascii(value[index]);
        if (character != expected[index]) {
            return false;
        }
    }
    return true;
}

bool ends_with_ci(std::string_view value, std::string_view suffix) {
    if (value.size() < suffix.size()) {
        return false;
    }
    return path_equals_ci(value.substr(value.size() - suffix.size()), suffix);
}

bool has_safe_segments(std::string_view path) {
    if (path.empty() || path.front() == '/' || path.back() == '/') {
        return false;
    }

    std::size_t begin = 0;
    while (begin < path.size()) {
        const auto end = path.find('/', begin);
        const auto part = path.substr(begin, end == std::string_view::npos ? path.size() - begin : end - begin);
        if (part.empty() || part == "." || part == ".." || part.back() == '.' || part.back() == ' ') {
            return false;
        }
        if (end == std::string_view::npos) {
            return true;
        }
        begin = end + 1;
    }
    return true;
}

bool valid_segment(std::string_view segment) {
    return segment.size() == 2 && (segment[0] == 'B' || segment[0] == 'b') && segment[1] >= '0' && segment[1] <= '4';
}

std::string event_prefix(std::string_view segment) {
    std::string result = "event/b";
    result.push_back(segment[1]);
    result += "/event_jp/";
    return result;
}

std::string text_prefix(std::string_view segment) {
    std::string result = "saves/b";
    result.push_back(segment[1]);
    result += "/text_";
    return result;
}

}  // namespace

std::string clean_path(std::string_view raw) {
    for (const auto prefix : {std::string_view{"disc:"}, std::string_view{"res:"}, std::string_view{"root:"}}) {
        if (starts_with_ci(raw, prefix)) {
            raw.remove_prefix(prefix.size());
            break;
        }
    }
    while (!raw.empty() && (raw.front() == '/' || raw.front() == '\\')) {
        raw.remove_prefix(1);
    }

    std::string result{raw};
    for (auto& value : result) {
        if (value == '\\') {
            value = '/';
        }
    }
    if (starts_with_ci(result, "res_jp/font/")) {
        result.erase(0, 7);
    }
    if (path_equals_ci(result, "res_jp/layout/default.g1n") ||
        path_equals_ci(result, "data/b1/x64/res_jp/layout/default.g1n")) {
        return "Layout/default.g1n";
    }
    if (ends_with_ci(result, ".kscl")) {
        if (starts_with_ci(result, "layout/")) {
            return "data/b1/x64/res_jp/" + result;
        }
        if (starts_with_ci(result, "res_jp/layout/")) {
            return "data/b1/x64/" + result;
        }
    }
    return result;
}

std::string make_lookup_key(std::string_view clean) {
    std::string result{clean};
    for (auto& value : result) {
        value = lower_ascii(value);
    }
    return result;
}

bool is_safe_manifest_path(std::string_view path) {
    if (!has_safe_segments(path)) {
        return false;
    }
    for (const unsigned char value : path) {
        if (value < 0x20 || value > 0x7e || value == '\\' || value == ':' || value == '"' || value == '*' ||
            value == '?' || value == '<' || value == '>' || value == '|') {
            return false;
        }
    }
    return true;
}

bool is_graphics_path(std::string_view lookup_key, std::string_view segment) {
    if (!valid_segment(segment) || !is_safe_manifest_path(lookup_key) || !ends_with_ci(lookup_key, ".g1t")) {
        return false;
    }

    const auto matches_prefix = [lookup_key](char resource_segment, std::string_view resource_root) {
        std::string prefix = "data/b0/x64/";
        prefix[6] = resource_segment;
        prefix += resource_root;
        return starts_with_ci(lookup_key, prefix);
    };
    if (segment[1] == '0') {
        return path_equals_ci(lookup_key, "res_jp/ipu/logo_kt.g1t") ||
               matches_prefix('4', "res_cmn/ui/") || matches_prefix('4', "res_jp/ui/");
    }
    return matches_prefix(segment[1], "res_cmn/ui/") || matches_prefix(segment[1], "res_jp/ui/") ||
           matches_prefix(segment[1], "res_jp/ipu/") || matches_prefix(segment[1], "res_jp/layout/");
}

bool is_japanese_event_namespace(std::string_view lookup_key, std::string_view segment) {
    return valid_segment(segment) && (lookup_key.starts_with(event_prefix(segment)) ||
        (segment[1] == '1' && lookup_key == "event/b1/balloonsel/balloonseldata_jp.bsb"));
}

bool is_segment_text_namespace(std::string_view lookup_key, std::string_view segment) {
    return valid_segment(segment) && lookup_key.starts_with(text_prefix(segment));
}

bool is_japanese_text_namespace(std::string_view lookup_key, std::string_view segment) {
    return is_segment_text_namespace(lookup_key, segment) &&
           lookup_key.starts_with(text_prefix(segment) + "jp/");
}

std::uint8_t font_slot_for_request(std::string_view raw_path, std::string_view segment) noexcept {
    if (!valid_segment(segment)) {
        return 0xff;
    }
    for (const auto prefix : {std::string_view{"disc:"}, std::string_view{"res:"}, std::string_view{"root:"}}) {
        if (starts_with_ci(raw_path, prefix)) {
            raw_path.remove_prefix(prefix.size());
            break;
        }
    }
    while (!raw_path.empty() && (raw_path.front() == '/' || raw_path.front() == '\\')) {
        raw_path.remove_prefix(1);
    }
    if (starts_with_ci(raw_path, "res_jp/") || starts_with_ci(raw_path, "res_jp\\")) {
        raw_path.remove_prefix(7);
    }

    if (segment[1] == '1') {
        return path_equals_ci(raw_path, "font/mainfonthd720_0.g1t") ? 0 : 0xff;
    }
    constexpr std::array<std::string_view, 3> font_paths{
        "font/mainfont00_hd1080_0.g1t",
        "font/mainfont01_hd1080_0.g1t",
        "font/mainfont02_hd1080_0.g1t",
    };
    for (std::uint8_t slot = 0; slot < font_paths.size(); ++slot) {
        if (path_equals_ci(raw_path, font_paths[slot])) {
            return slot;
        }
    }
    return 0xff;
}

AssetKind classify_lookup_key(std::string_view lookup_key, std::string_view segment) {
    if (!valid_segment(segment) || !has_safe_segments(lookup_key)) {
        return AssetKind::none;
    }
    if (segment[1] == '1' && lookup_key.starts_with("data/b1/x64/res_jp/layout/") &&
        lookup_key.ends_with(".kscl")) {
        return AssetKind::text;
    }
    std::string saves_prefix{"saves/b0/"};
    saves_prefix[7] = segment[1];
    if (lookup_key.starts_with(saves_prefix)) {
        const auto relative = lookup_key.substr(saves_prefix.size());
        const bool xml = relative.ends_with(".xml") || relative.ends_with(".xml.e");
        if ((xml && (relative.starts_with("text_jp/") || relative.starts_with("ui/") ||
                     relative.starts_with("ui_cmn/"))) ||
            (segment[1] != '0' && (relative == "event/eventglossarykeyword.xml" ||
                                   relative == "event/eventglossarykeyword.xml.e"))) {
            return AssetKind::text;
        }
    }
    if (segment[1] == '1' && lookup_key == "data/b1/x64/res_jp/ipu/headsup.g1t") {
        return AssetKind::image;
    }
    if (segment[1] == '1' && lookup_key == "layout/default.g1n") {
        return AssetKind::font_bundle;
    }
    if (segment[1] == '0' && lookup_key == "res_jp/ipu/logo_kt.g1t") {
        return AssetKind::image;
    }
    if (is_graphics_path(lookup_key, segment)) {
        return AssetKind::image;
    }
    if (is_japanese_event_namespace(lookup_key, segment) &&
        (ends_with(lookup_key, ".ebm") || lookup_key == "event/b1/balloonsel/balloonseldata_jp.bsb")) {
        return AssetKind::event;
    }
    if (!lookup_key.starts_with("font/")) {
        return AssetKind::none;
    }

    return font_slot_for_request(lookup_key, segment) < 3 ? AssetKind::font : AssetKind::none;
}

}  // namespace brq
