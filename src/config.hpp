#pragma once

#include <cstddef>
#include <windows.h>

#ifndef BRQ_ENABLE_DIAGNOSTICS
#define BRQ_ENABLE_DIAGNOSTICS 0
#endif

namespace brq {

struct Config final {
    static constexpr bool diagnostics = BRQ_ENABLE_DIAGNOSTICS != 0;
    static constexpr wchar_t asset_root[] = L"BRQuarteTranslateHelper-assets";
    static constexpr wchar_t manifest_name[] = L"manifest.txt";
    static constexpr wchar_t log_name[] = L"BRQuarteTranslateHelper.log";
    static constexpr wchar_t pack_cache_prefix[] = L"BRQuarteTranslateHelper.";
    static constexpr wchar_t pack_cache_suffix[] = L".pak-verified";
    static constexpr DWORD module_poll_ms = 10;
    static constexpr std::size_t max_internal_path = 1'024;
    static constexpr DWORD max_manifest_bytes = 4 * 1024 * 1024;
    static constexpr std::size_t max_manifest_entries = 65'536;
};

}  // namespace brq
