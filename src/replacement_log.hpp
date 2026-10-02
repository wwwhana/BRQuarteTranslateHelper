#pragma once

#include <string>
#include <string_view>

namespace brq {

inline std::string replacement_log_message(std::string_view segment, std::string_view game_path, bool embedded) {
    std::string message{game_path};
    message += embedded ? " => BRQuarteTranslateHelper.asi!/" : " => BRQuarteTranslateHelper-assets/";
    message += segment;
    message += '/';
    message += game_path;
    return message;
}

}  // namespace brq
