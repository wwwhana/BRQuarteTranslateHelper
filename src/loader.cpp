#include <windows.h>
#include <bcrypt.h>

#include <MinHook.h>

#if defined(_MSC_VER)
#include <intrin.h>
#define BRQ_RETURN_ADDRESS() _ReturnAddress()
#define BRQ_NOINLINE __declspec(noinline)
#elif defined(__GNUC__)
#define BRQ_RETURN_ADDRESS() __builtin_return_address(0)
#define BRQ_NOINLINE __attribute__((noinline))
#else
#define BRQ_RETURN_ADDRESS() nullptr
#define BRQ_NOINLINE
#endif

#include "b0_index_hash.hpp"
#include "config.hpp"
#include "source_pack_data.hpp"
#include "runtime_text_data.hpp"
#include "glyph_table.hpp"
#include "module_specs.hpp"
#include "path_policy.hpp"
#include "replacement_log.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifndef BRQ_EMBED_ASSETS
#define BRQ_EMBED_ASSETS 0
#endif

#if BRQ_EMBED_ASSETS
#include "embedded_assets.hpp"
#endif

namespace brq {
namespace {

using OpenFn = bool (*)(void*, const char*, int, int, int);
using FileSizeFn = std::uint32_t (*)(const char*);
using ReadWholeFn = bool (*)(const char*, void*, std::uint32_t, std::uint32_t*);
using LoadLibraryAFn = decltype(&::LoadLibraryA);
using LoadLibraryWFn = decltype(&::LoadLibraryW);
using FreeLibraryFn = decltype(&::FreeLibrary);
using CreateFileAFn = decltype(&::CreateFileA);
using CreateFileWFn = decltype(&::CreateFileW);
using GetFileAttributesWFn = decltype(&::GetFileAttributesW);
using GetFileAttributesExWFn = decltype(&::GetFileAttributesExW);
using GlyphLookup48Fn = const void* (*)(std::uint32_t, int, int);
using GlyphLookup28Fn = const void* (*)(void*, std::uint32_t, int, int);
using B1StringLookupFn = const char* (*)(void*, std::int32_t);
using RuntimeTextFn = std::uintptr_t (*)(void*, const char*);
using B1TextSetFn = void (*)(void*, const char*, std::uint8_t);
using B1MenuGlyphFn = bool (*)(void*, std::uint16_t, int);
using PackLoadFn = bool (*)(int, const char*);
using PackIndexFn = void (*)(void*);
using PackLookupFn = void* (*)(void*, const char*);

struct Asset final {
    AssetKind kind{};
    std::uint8_t glyph_slot{0xff};
    bool b1_main_font{};
    std::string game_path;
    std::string bridge_path;
    std::wstring bridge_suffix_key;
    std::array<std::wstring, 1> bridge_files;
    std::wstring physical_path;
};

struct State final {
    HMODULE self{};
    HMODULE game_module{};
    const ModuleSpec* spec{};
    std::wstring game_root;
    std::wstring log_path;
    std::string bridge_run_id;
    std::unordered_map<std::string, Asset> assets_by_key;
    std::unordered_map<std::string, const Asset*> assets_by_bridge_key;
    std::unordered_map<std::wstring, const Asset*> assets_by_bridge_suffix;
    std::unordered_set<std::string> logged_replacement_paths;
    std::unordered_set<std::string> logged_file_sizes;
    std::unordered_set<std::string> logged_font_requests;
    std::unordered_set<std::string> logged_glyph_mappings;
    std::array<std::vector<GlyphRecord48>, 3> glyph_tables48;
    std::vector<GlyphRecord28> glyph_table28;
    std::vector<std::wstring> temporary_files;
    std::vector<HANDLE> verified_pack_handles;
    bool source_packs_match{true};
    std::atomic_bool korean_text_committed{false};
    std::atomic<std::uint8_t> opened_font_slots{0};
    std::atomic_bool open_probe_logged{false};
    std::array<std::atomic_bool, 2> runtime_display_logged{};
    std::atomic_bool replacement_candidate_probe_logged{false};
    std::atomic_bool bridge_open_probe_logged{false};
    std::atomic_bool bridge_file_probe_logged{false};
    std::atomic_bool replacements_disabled{false};
    std::atomic_bool hooks_ready{false};
    std::atomic<std::uint8_t> glyph_probe_count{0};
    std::atomic<std::uint8_t> glyph_diagnostic_probe_count{0};
    std::atomic_bool b1_binding_probe_logged{false};
    std::array<std::atomic<std::uint16_t>, 2> b1_text_probe_count{};
    std::atomic<std::uint8_t> b1_menu_glyph_probe_count{};
    ULONGLONG worker_started_ms{};
    ULONGLONG hook_enable_started_ms{};
    bool glyph_tables_ready{};
    bool owns_game_module_reference{};
    bool published_to_hook_slot{};

    ~State() {
        for (HANDLE handle : verified_pack_handles) {
            ::CloseHandle(handle);
        }
        for (const auto& path : temporary_files) {
            ::DeleteFileW(path.c_str());
        }
        if (game_module != nullptr && owns_game_module_reference) {
            ::FreeLibrary(game_module);
        }
    }
};

struct PublishedStateLifetime final {
    explicit PublishedStateLifetime(std::unique_ptr<State>& owner) noexcept : owner{owner} {}
    ~PublishedStateLifetime() {
        if (owner != nullptr && owner->published_to_hook_slot) {
            owner->hooks_ready.store(true, std::memory_order_release);
            static_cast<void>(owner.release());
        }
    }

    std::unique_ptr<State>& owner;
};

struct HookSlot final {
    std::atomic<State*> state{};
    std::atomic<std::uint32_t> detour_gate{};
    std::atomic_bool worker_running{};
    OpenFn original_open{};
    FileSizeFn original_file_size{};
    ReadWholeFn original_read_whole{};
    GlyphLookup48Fn original_glyph_lookup48{};
    GlyphLookup28Fn original_glyph_lookup28{};
    B1StringLookupFn original_b1_string_lookup{};
    B1TextSetFn original_b1_text_set{};
    std::array<RuntimeTextFn, 2> original_runtime_text{};
    B1MenuGlyphFn original_b1_menu_glyph{};
    LPVOID open_target{};
    LPVOID file_size_target{};
    LPVOID read_whole_target{};
    LPVOID glyph_lookup_target{};
    LPVOID b1_string_lookup_target{};
    LPVOID b1_text_set_target{};
    LPVOID b1_menu_glyph_target{};
    // Segment hooks (up to 7: file, glyph, B1 text or runtime text) plus 3 PAK hooks.
    std::array<LPVOID, 10> created_targets{};
    std::array<LPVOID, 10> enabled_targets{};
    std::size_t created_target_count{};
    std::size_t enabled_target_count{};
};

struct ActiveDetour final {
    static constexpr std::uint32_t retiring = 0x80000000U;
    static constexpr std::uint32_t active_mask = ~retiring;

    explicit ActiveDetour(HookSlot& slot) noexcept : slot{slot} {
        auto observed = slot.detour_gate.load(std::memory_order_acquire);
        while ((observed & retiring) == 0 &&
               !slot.detour_gate.compare_exchange_weak(observed, observed + 1, std::memory_order_acq_rel)) {
        }
        entered = (observed & retiring) == 0;
    }
    ~ActiveDetour() {
        if (entered) {
            slot.detour_gate.fetch_sub(1, std::memory_order_acq_rel);
        }
    }
    explicit operator bool() const noexcept { return entered; }
    HookSlot& slot;
    bool entered{};
};

static_assert(module_specs.size() == 5);
std::array<HookSlot, module_specs.size()> hook_slots{};
struct PendingModuleRelease final {
    State* state{};
    std::size_t index{module_specs.size()};
};
thread_local PendingModuleRelease pending_module_release{};
bool retire_module_release(State& state, std::size_t index) noexcept;
void start_module_worker(std::size_t index) noexcept;
std::atomic_bool bootstrap_started{};
std::atomic_bool transition_lifecycle_ready{};
HMODULE self_module{};
LoadLibraryAFn original_load_library_a{};
LoadLibraryWFn original_load_library_w{};
FreeLibraryFn original_free_library{};
CreateFileAFn original_create_file_a{};
CreateFileWFn original_create_file_w{};
GetFileAttributesWFn original_get_file_attributes_w{};
GetFileAttributesExWFn original_get_file_attributes_ex_w{};
LPVOID load_library_a_target{};
LPVOID load_library_w_target{};
LPVOID free_library_target{};
LPVOID create_file_a_target{};
LPVOID create_file_target{};
LPVOID get_attributes_target{};
LPVOID get_attributes_ex_target{};
enum class IndexPatchState : std::uint8_t {
    disabled,
    pending,
    installing,
    enabled,
    failed,
};
struct PackIndexRuntime final {
    PackLoadFn original_load{};
    PackIndexFn original_index{};
    PackLookupFn original_lookup{};
    LPVOID load_target{};
    LPVOID index_target{};
    LPVOID lookup_target{};
    std::atomic<IndexPatchState> patch_state{IndexPatchState::disabled};
    std::atomic_bool result_logged{};
    std::uint8_t* module_base{};
};
std::array<PackIndexRuntime, module_specs.size()> pack_indexes{};
thread_local std::array<int, module_specs.size()> active_pack_slots{-1, -1, -1, -1, -1};
SRWLOCK log_lock = SRWLOCK_INIT;
SRWLOCK bridge_file_lock = SRWLOCK_INIT;
SRWLOCK hook_install_lock = SRWLOCK_INIT;
// ponytail: font opens are rare; one lock keeps the atlas and glyph state coherent across every slot.
SRWLOCK font_open_lock = SRWLOCK_INIT;

template <typename Call, typename Fallback>
decltype(auto) game_call_noexcept(Call&& call, Fallback&& fallback) noexcept {
    try {
        return std::forward<Call>(call)();
    } catch (...) {
        return std::forward<Fallback>(fallback)();
    }
}

struct ExclusiveSrwGuard final {
    explicit ExclusiveSrwGuard(SRWLOCK& lock) noexcept : lock{lock} {
        ::AcquireSRWLockExclusive(&lock);
    }

    ~ExclusiveSrwGuard() {
        ::ReleaseSRWLockExclusive(&lock);
    }

    ExclusiveSrwGuard(const ExclusiveSrwGuard&) = delete;
    ExclusiveSrwGuard& operator=(const ExclusiveSrwGuard&) = delete;

    SRWLOCK& lock;
};

std::wstring join_path(std::wstring_view left, std::wstring_view right) {
    std::wstring result{left};
    if (!result.empty() && result.back() != L'\\' && result.back() != L'/') {
        result.push_back(L'\\');
    }
    result.append(right);
    return result;
}

std::wstring widen_ascii(std::string_view value) {
    std::wstring result;
    result.reserve(value.size());
    for (const unsigned char byte : value) {
        result.push_back(static_cast<wchar_t>(byte));
    }
    return result;
}

std::wstring lower_ascii(std::wstring value) {
    for (auto& character : value) {
        if (character >= L'A' && character <= L'Z') {
            character = static_cast<wchar_t>(character + (L'a' - L'A'));
        } else if (character == L'/') {
            character = L'\\';
        }
    }
    return value;
}

constexpr std::wstring_view bridge_suffix_from_key(std::wstring_view full_path_key) {
    constexpr std::wstring_view marker{L"\\brquartetkorean\\"};
    const auto position = full_path_key.rfind(marker);
    return position == std::wstring_view::npos ? std::wstring_view{} : full_path_key.substr(position);
}

static_assert(bridge_suffix_from_key(L"c:\\game\\data\\x64\\brquartetkorean\\b0\\00000001.bin") ==
              L"\\brquartetkorean\\b0\\00000001.bin");
static_assert(bridge_suffix_from_key(L"c:\\game\\data\\b0\\x64\\brquartetkorean\\b0\\00000001.bin") ==
              L"\\brquartetkorean\\b0\\00000001.bin");
static_assert(bridge_suffix_from_key(L"c:\\game\\data\\x64\\other\\00000001.bin").empty());

bool get_full_path(std::wstring_view path, std::wstring& output) {
    const std::wstring input{path};
    const DWORD needed = ::GetFullPathNameW(input.c_str(), 0, nullptr, nullptr);
    if (needed == 0 || needed >= 32'768) {
        return false;
    }
    std::vector<wchar_t> buffer(static_cast<std::size_t>(needed) + 1);
    const DWORD written = ::GetFullPathNameW(input.c_str(), static_cast<DWORD>(buffer.size()), buffer.data(), nullptr);
    if (written == 0 || written >= buffer.size()) {
        return false;
    }
    output.assign(buffer.data(), written);
    for (auto& character : output) {
        if (character == L'/') {
            character = L'\\';
        }
    }
    while (output.size() > 3 && output.back() == L'\\') {
        output.pop_back();
    }
    return true;
}

bool equal_prefix_ci(std::wstring_view value, std::wstring_view prefix) {
    if (value.size() < prefix.size()) {
        return false;
    }
    return ::CompareStringOrdinal(value.data(), static_cast<int>(prefix.size()), prefix.data(),
                                  static_cast<int>(prefix.size()), TRUE) == CSTR_EQUAL;
}

bool is_under_root(std::wstring_view path, std::wstring_view root) {
    return path.size() > root.size() && equal_prefix_ci(path, root) && path[root.size()] == L'\\';
}

bool write_log_line(const State& state, std::string_view message) noexcept {
    if (state.log_path.empty()) {
        return false;
    }
    const auto create_file = original_create_file_w != nullptr ? original_create_file_w : &::CreateFileW;
    const HANDLE file = game_call_noexcept(
        [&] {
            return create_file(state.log_path.c_str(), FILE_APPEND_DATA,
                               FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL, nullptr);
        },
        []() noexcept { return INVALID_HANDLE_VALUE; });
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    SYSTEMTIME time{};
    ::GetLocalTime(&time);
    std::array<char, 96> prefix{};
    const int size = std::snprintf(prefix.data(), prefix.size(), "[%04u-%02u-%02u %02u:%02u:%02u] ",
                                   time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute, time.wSecond);
    DWORD prefix_written{};
    DWORD message_written{};
    DWORD newline_written{};
    constexpr char newline[] = "\r\n";
    const bool prefix_ok = size > 0 &&
                           ::WriteFile(file, prefix.data(), static_cast<DWORD>(size), &prefix_written, nullptr) != FALSE &&
                           prefix_written == static_cast<DWORD>(size);
    const bool message_ok = ::WriteFile(file, message.data(), static_cast<DWORD>(message.size()), &message_written,
                                        nullptr) != FALSE &&
                            message_written == static_cast<DWORD>(message.size());
    const bool newline_ok = ::WriteFile(file, newline, 2, &newline_written, nullptr) != FALSE && newline_written == 2;
    ::CloseHandle(file);
    return prefix_ok && message_ok && newline_ok;
}

void log_line(const State& state, std::string_view message) noexcept {
    if (state.log_path.empty()) {
        return;
    }
    ::AcquireSRWLockExclusive(&log_lock);
    static_cast<void>(write_log_line(state, message));
    ::ReleaseSRWLockExclusive(&log_lock);
}

void try_log_transition_line(const State& state, std::string_view message) noexcept {
    if (state.log_path.empty() || ::TryAcquireSRWLockExclusive(&log_lock) == FALSE) {
        return;
    }
    static_cast<void>(write_log_line(state, message));
    ::ReleaseSRWLockExclusive(&log_lock);
}

void log_elapsed(const State& state, std::string_view step, ULONGLONG started) noexcept {
    try {
        log_line(state, "Preparation time: " + std::string{step} + "=" +
                            std::to_string(::GetTickCount64() - started) + "ms");
    } catch (...) {
    }
}

void log_pack_elapsed(std::size_t index, std::uintptr_t rva, std::string_view label, int pack_slot, ULONGLONG started) noexcept {
    State* const state = hook_slots[index].state.load(std::memory_order_acquire);
    if (state == nullptr) {
        return;
    }
    std::array<char, 192> message{};
    const auto elapsed = static_cast<unsigned long long>(::GetTickCount64() - started);
    const int size = pack_slot >= 0
                         ? std::snprintf(message.data(), message.size(),
                                         "Execution time: %s.dll+0x%08llx (%.*s), PAK[%d]=%llums",
                                         state->spec->segment, static_cast<unsigned long long>(rva), static_cast<int>(label.size()),
                                         label.data(), pack_slot, elapsed)
                         : std::snprintf(message.data(), message.size(),
                                         "Execution time: %s.dll+0x%08llx (%.*s)=%llums",
                                         state->spec->segment, static_cast<unsigned long long>(rva), static_cast<int>(label.size()),
                                         label.data(), elapsed);
    if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
        log_line(*state, std::string_view{message.data(), static_cast<std::size_t>(size)});
    }
}

struct PackTimingScope final {
    PackTimingScope(std::size_t index, std::uintptr_t rva, std::string_view label, int pack_slot, bool set_active_slot) noexcept
        : index{index}, rva{rva}, label{label}, pack_slot{pack_slot}, previous_slot{active_pack_slots[index]},
          set_active_slot{set_active_slot}, started{::GetTickCount64()} {
        if (set_active_slot) {
            active_pack_slots[index] = pack_slot;
        }
    }

    ~PackTimingScope() {
        log_pack_elapsed(index, rva, label, pack_slot, started);
        if (set_active_slot) {
            active_pack_slots[index] = previous_slot;
        }
    }

    std::size_t index;
    std::uintptr_t rva;
    std::string_view label;
    int pack_slot;
    int previous_slot;
    bool set_active_slot;
    ULONGLONG started;
};

void log_replacement_once(State& state, const Asset& asset) noexcept {
    if constexpr (!Config::diagnostics) return;
    std::string message;
    try {
        message = replacement_log_message(state.spec->segment, asset.game_path, BRQ_EMBED_ASSETS != 0);
    } catch (...) {
        return;
    }

    ::AcquireSRWLockExclusive(&log_lock);
    try {
        const auto [position, inserted] = state.logged_replacement_paths.emplace(asset.game_path);
        if (inserted && !write_log_line(state, message)) {
            state.logged_replacement_paths.erase(position);
        }
    } catch (...) {
    }
    ::ReleaseSRWLockExclusive(&log_lock);
}

[[maybe_unused]] void log_font_request_once(State& state, std::string_view raw_path, std::string_view key) noexcept {
    if constexpr (!Config::diagnostics) return;
    ::AcquireSRWLockExclusive(&log_lock);
    try {
        const auto [position, inserted] = state.logged_font_requests.emplace(key);
        if (inserted && !write_log_line(state, "Font request: " + std::string{raw_path} + " => " + std::string{key})) {
            state.logged_font_requests.erase(position);
        }
    } catch (...) {
    }
    ::ReleaseSRWLockExclusive(&log_lock);
}

void log_file_size_once(State& state, const Asset& asset, std::uint32_t original_size,
                        std::uint32_t replacement_size) noexcept {
    if constexpr (!Config::diagnostics) return;
    ::AcquireSRWLockExclusive(&log_lock);
    try {
        const auto [position, inserted] = state.logged_file_sizes.emplace(asset.game_path);
        if (inserted) {
            std::string message = "File size check: " +
                                  replacement_log_message(state.spec->segment, asset.game_path,
                                                          BRQ_EMBED_ASSETS != 0) +
                                  "; original=" + std::to_string(original_size) + " bytes, replacement=" +
                                  std::to_string(replacement_size) + " bytes";
            if (!write_log_line(state, message)) {
                state.logged_file_sizes.erase(position);
            }
        }
    } catch (...) {
    }
    ::ReleaseSRWLockExclusive(&log_lock);
}

void log_original_fallback(State& state, const Asset& asset, std::string_view reason, DWORD error) noexcept {
    try {
        std::string message{reason};
        message += " (error ";
        message += std::to_string(error == ERROR_SUCCESS ? ERROR_GEN_FAILURE : error);
        message += "), reading the original: ";
        message += asset.game_path;
        message += " => ";
        message += asset.game_path;
        message += "; replacement candidate: ";
        message += replacement_log_message(state.spec->segment, asset.game_path, BRQ_EMBED_ASSETS != 0);
        log_line(state, message);
    } catch (...) {
    }
}

void use_original_files(State& state) noexcept {
    state.replacements_disabled.store(true, std::memory_order_release);
    state.korean_text_committed.store(false, std::memory_order_release);
    state.opened_font_slots.store(0, std::memory_order_release);
}

struct GlyphText final {
    std::array<char, 5> bytes{};
    int size{};
};

constexpr bool continuation_byte(std::uint8_t value) noexcept {
    return (value & 0xc0U) == 0x80U;
}

GlyphText glyph_text(std::uint32_t key) noexcept {
    GlyphText result{};
    std::array<std::uint8_t, 4> packed{
        static_cast<std::uint8_t>(key >> 24), static_cast<std::uint8_t>(key >> 16),
        static_cast<std::uint8_t>(key >> 8), static_cast<std::uint8_t>(key),
    };
    std::size_t first{};
    while (first < packed.size() - 1 && packed[first] == 0) {
        ++first;
    }
    const auto length = packed.size() - first;
    const bool packed_utf8 =
        (length == 1 && packed[first] < 0x80U) ||
        (length == 2 && packed[first] >= 0xc2U && packed[first] <= 0xdfU && continuation_byte(packed[first + 1])) ||
        (length == 3 && packed[first] >= 0xe0U && packed[first] <= 0xefU && continuation_byte(packed[first + 1]) &&
         continuation_byte(packed[first + 2]) && !(packed[first] == 0xe0U && packed[first + 1] < 0xa0U) &&
         !(packed[first] == 0xedU && packed[first + 1] >= 0xa0U)) ||
        (length == 4 && packed[first] >= 0xf0U && packed[first] <= 0xf4U && continuation_byte(packed[first + 1]) &&
         continuation_byte(packed[first + 2]) && continuation_byte(packed[first + 3]) &&
         !(packed[first] == 0xf0U && packed[first + 1] < 0x90U) &&
         !(packed[first] == 0xf4U && packed[first + 1] >= 0x90U));
    if (packed_utf8) {
        result.size = static_cast<int>(length);
        for (std::size_t index = 0; index < length; ++index) {
            result.bytes[index] = static_cast<char>(packed[first + index]);
        }
        return result;
    }

    result.size = 1;
    result.bytes[0] = '?';
    return result;
}

bool glyph_record_key(const void* record, std::uint32_t& key) noexcept {
    if (record == nullptr) {
        return false;
    }
    SIZE_T read{};
    return ::ReadProcessMemory(::GetCurrentProcess(), record, &key, sizeof(key), &read) != FALSE &&
           read == sizeof(key);
}

void log_glyph_message_once(State& state, std::string_view message) noexcept {
    if constexpr (!Config::diagnostics) return;
    ::AcquireSRWLockExclusive(&log_lock);
    try {
        const auto [position, inserted] = state.logged_glyph_mappings.emplace(message);
        if (inserted && !write_log_line(state, message)) {
            state.logged_glyph_mappings.erase(position);
        }
    } catch (...) {
    }
    ::ReleaseSRWLockExclusive(&log_lock);
}

template <typename T>
bool read_font_field(std::uintptr_t base, std::uintptr_t offset, T& value) noexcept {
    static_assert(std::is_trivially_copyable_v<T>);
    if (base == 0 || offset > UINTPTR_MAX - base || sizeof(T) - 1 > UINTPTR_MAX - (base + offset)) {
        return false;
    }
    SIZE_T read{};
    return ::ReadProcessMemory(::GetCurrentProcess(), reinterpret_cast<const void*>(base + offset),
                               &value, sizeof(value), &read) != FALSE && read == sizeof(value);
}

void log_b1_font_binding_once(State& state, const void* context = nullptr, int requested_mode = -1) noexcept {
    if constexpr (!Config::diagnostics) return;
    if (state.spec == nullptr || std::string_view{state.spec->segment} != "B1" || state.game_module == nullptr ||
        b1_font_manager_rva + sizeof(std::uintptr_t) > state.spec->image_size) {
        return;
    }
    const DWORD saved_error = ::GetLastError();
    const auto base = reinterpret_cast<std::uintptr_t>(state.game_module);
    std::uintptr_t manager{};
    const bool manager_read = read_font_field(base, b1_font_manager_rva, manager);
    // Read only the verified global chain, never dereference a caller-supplied context.
    const int context_matches = context == nullptr || !manager_read || manager == 0
                                    ? -1
                                    : context == reinterpret_cast<const void*>(manager);
    for (unsigned mode = 0; mode < 2; ++mode) {
        std::uintptr_t wrapper{}, control{}, entry{}, texture{};
        std::int32_t count{};
        std::array<std::uint16_t, 2> descriptor{}, entry_size{};
        const bool descriptor_read = read_font_field(base, b1_font_descriptor_rva + mode * 0x18, descriptor);
        const bool chain_read = manager_read &&
            read_font_field(manager, 8 + mode * 8, wrapper) && read_font_field(wrapper, 0x10, control) &&
            read_font_field(control, 0x20, count) && count > 0 &&
            read_font_field(control, 0x28, entry) &&
            read_font_field(entry, 0, texture) && read_font_field(entry, 0x30, entry_size);
        std::array<char, 768> message{};
        const int size = std::snprintf(message.data(), message.size(),
            "B1 font binding: %s, requested mode=%d, context match=%d, "
            "glyph table size=%ux%u(readable=%d), texture entry size=%ux%u, chain read=%d, "
            "entry count=%d, manager=%p wrapper=%p control=%p entry=%p texture=%p, "
            "Hangul glyph table=%zu, replacement font=%u, Korean text=%d",
            mode == 0 ? "Main" : "Sub", requested_mode, context_matches,
            descriptor[0], descriptor[1], descriptor_read, entry_size[0], entry_size[1],
            chain_read, count, reinterpret_cast<void*>(manager), reinterpret_cast<void*>(wrapper),
            reinterpret_cast<void*>(control), reinterpret_cast<void*>(entry), reinterpret_cast<void*>(texture),
            state.glyph_table28.size(), state.opened_font_slots.load(std::memory_order_acquire),
            state.korean_text_committed.load(std::memory_order_acquire));
        if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
            log_glyph_message_once(state, {message.data(), static_cast<std::size_t>(size)});
        }
    }
    ::SetLastError(saved_error);
}

void log_b1_text(State& state, bool lookup, void* object, std::int32_t id,
                 const char* text, const void* caller) noexcept {
    if constexpr (!Config::diagnostics) return;
    const DWORD saved_error = ::GetLastError();
    auto& counter = state.b1_text_probe_count[lookup ? 0 : 1];
    auto count = counter.load(std::memory_order_relaxed);
    while (count < 256 && !counter.compare_exchange_weak(count, count + 1, std::memory_order_relaxed)) {
    }
    if (count >= 256) {
        ::SetLastError(saved_error);
        return;
    }
    try {
        // Diagnostic only: never change or retain the game's string; read at most 192 bytes.
        std::string visible, bytes;
        bool readable{true}, terminated{};
        constexpr char hex[]{"0123456789abcdef"};
        for (std::size_t index = 0; index < 192; ++index) {
            unsigned char byte{};
            if (!read_font_field(reinterpret_cast<std::uintptr_t>(text), index, byte)) {
                readable = false;
                break;
            }
            if (byte == 0) {
                terminated = true;
                break;
            }
            if (!bytes.empty()) {
                bytes += ' ';
            }
            bytes += hex[byte >> 4];
            bytes += hex[byte & 15];
            if (byte < 0x20 || byte == 0x7f || byte == '\\') {
                visible += "\\x";
                visible += hex[byte >> 4];
                visible += hex[byte & 15];
            } else {
                visible += static_cast<char>(byte);
            }
        }
        const auto base = reinterpret_cast<std::uintptr_t>(state.game_module);
        const auto address = reinterpret_cast<std::uintptr_t>(caller);
        const auto rva = state.spec != nullptr && address >= base && address - base < state.spec->image_size
                             ? address - base : 0;
        std::array<char, 256> header{};
        std::snprintf(header.data(), header.size(),
            "B1 string %s: id=%d, thread=%lu, object=%p, caller=%p(B1+0x%08llx), readable=%d, truncated=%d, text=",
            lookup ? "lookup" : "display", id, ::GetCurrentThreadId(), object, caller, static_cast<unsigned long long>(rva),
            readable, readable && !terminated);
        log_line(state, std::string{header.data()} + visible + "; bytes=" + bytes);
        if (!lookup) {
            std::uintptr_t vtable{}, consumer{};
            const bool target_read = read_font_field(reinterpret_cast<std::uintptr_t>(object), 0, vtable) &&
                                     read_font_field(vtable, 0x1c0, consumer);
            const auto consumer_rva = state.spec != nullptr && consumer >= base &&
                                      consumer - base < state.spec->image_size ? consumer - base : 0;
            std::snprintf(header.data(), header.size(),
                "B1 menu text delivery: object=%p, vtable=%p, UTF16 consumer=%p(B1+0x%08llx), readable=%d",
                object, reinterpret_cast<void*>(vtable), reinterpret_cast<void*>(consumer),
                static_cast<unsigned long long>(consumer_rva), target_read);
            log_glyph_message_once(state, header.data());
        }
        log_b1_font_binding_once(state);
    } catch (...) {
    }
    ::SetLastError(saved_error);
}

const char* hooked_b1_string_lookup_impl(State* state, B1StringLookupFn original,
                                         void* table, std::int32_t id, const void* caller) noexcept {
    if (original == nullptr) {
        return nullptr;
    }
    const char* result = original(table, id);
    if (state != nullptr) {
        log_b1_text(*state, true, table, id, result, caller);
    }
    return result;
}

void hooked_b1_text_set_impl(State* state, B1TextSetFn original, void* object,
                             const char* text, std::uint8_t flag, const void* caller) noexcept {
    if (state != nullptr) {
        log_b1_text(*state, false, object, -1, text, caller);
    }
    if (original != nullptr) {
        original(object, text, flag);
    }
}

bool hooked_b1_menu_glyph_impl(State* state, B1MenuGlyphFn original, void* object,
                               std::uint16_t code, int font) noexcept {
    const DWORD incoming_error = ::GetLastError();
    if (Config::diagnostics && state != nullptr && code >= 0xac00 && code <= 0xd7a3 &&
        take_glyph_probe(state->b1_menu_glyph_probe_count)) {
        const auto base = reinterpret_cast<std::uintptr_t>(object);
        int selected_font = font;
        std::uintptr_t resource{}, table{};
        std::uint32_t offset{}, header{};
        std::uint16_t glyph{}, fallback{}, exempt{};
        const bool font_read = font != -1 || read_font_field(base, 0x3a88, selected_font);
        const bool fallback_read = read_font_field(base, 0x3aa6, fallback) &&
                                   read_font_field(base, 0x3aa8, exempt);
        const bool map_read = font_read && selected_font >= 0 && selected_font <= 255 &&
            read_font_field(base, 0x3a08, resource) && read_font_field(resource, 0x18, table) &&
            read_font_field(table, 0, header) &&
            read_font_field(table, 0x20 + static_cast<std::uintptr_t>(selected_font) * 4, offset) &&
            read_font_field(table, static_cast<std::uintptr_t>(offset) + code * 2, glyph);
        const auto selected = map_read && fallback_read && glyph == 0 && code != exempt && fallback != 0
                                  ? fallback : code;
        std::array<char, 480> message{};
        const int written = std::snprintf(message.data(), message.size(),
            "B1 menu glyph selection: input=U+%04X, selected=U+%04X, font=%d(requested=%d), glyph ID=%u, "
            "fallback=U+%04X, exempt=U+%04X, map read=%d, fallback read=%d, "
            "object=%p, font resource=%p, glyph table=%p, map offset=0x%X, map header=0x%08X",
            static_cast<unsigned>(code), static_cast<unsigned>(selected), selected_font, font,
            static_cast<unsigned>(glyph), static_cast<unsigned>(fallback), static_cast<unsigned>(exempt),
            map_read ? 1 : 0, fallback_read ? 1 : 0, object, reinterpret_cast<void*>(resource),
            reinterpret_cast<void*>(table), offset, header);
        if (written > 0 && static_cast<std::size_t>(written) < message.size()) {
            log_glyph_message_once(*state, {message.data(), static_cast<std::size_t>(written)});
        }
    }
    ::SetLastError(incoming_error);
    return original != nullptr && original(object, code, font);
}

void log_glyph_decision_once(State& state, std::uint32_t read_key, const void* selected_record,
                             bool korean_record, std::uint8_t slot, int source_mode, int dynamic_table_index,
                             const GlyphRecord48* metrics, const GlyphRecord28* metrics28 = nullptr) noexcept {
    if constexpr (!Config::diagnostics) return;
    std::uint32_t selected_key{};
    const bool record_present = selected_record != nullptr;
    const bool selected_key_read = glyph_record_key(selected_record, selected_key);
    const auto read_text = glyph_text(read_key);
    const auto selected_text = selected_key_read ? glyph_text(selected_key) : GlyphText{{'?', 0, 0, 0, 0}, 1};
    std::array<char, 512> message{};
    const char* const source = !record_present
                                   ? "no glyph"
                                   : !selected_key_read ? "glyph read failed"
                                                        : korean_record ? "Korean glyph table" : "original glyph table";
    const int size = metrics28 != nullptr
                         ? std::snprintf(message.data(), message.size(),
                                         "Glyph selection: B1 input glyph='%.*s' (UTF8 0x%08x) => selected glyph='%.*s' "
                                         "(UTF8 0x%08x), %s, source mode=%d, dynamic glyph table=%d, "
                                         "x=%u y=%u w=%u h=%u left=%d top=%d advance=%d, record=%p",
                                         read_text.size, read_text.bytes.data(), read_key, selected_text.size,
                                         selected_text.bytes.data(), selected_key_read ? selected_key : 0, source,
                                         source_mode, dynamic_table_index, metrics28->x, metrics28->y,
                                         metrics28->width, metrics28->height, metrics28->left, metrics28->top,
                                         metrics28->advance, selected_record)
                     : metrics != nullptr
                         ? std::snprintf(message.data(), message.size(),
                                         "Glyph selection: input glyph='%.*s' (UTF8 0x%08x) => selected glyph='%.*s' "
                                         "(UTF8 0x%08x), %s, font=%u, dynamic glyph table=%d, x=%.0f y=%.0f w=%.0f h=%.0f "
                                         "left=%.0f top=%.0f advance=%.0f",
                                         read_text.size, read_text.bytes.data(), read_key, selected_text.size,
                                         selected_text.bytes.data(), selected_key_read ? selected_key : 0, source,
                                         slot, dynamic_table_index, metrics->metrics[0], metrics->metrics[1],
                                         metrics->metrics[2], metrics->metrics[3], metrics->metrics[4],
                                         metrics->metrics[5], metrics->metrics[6])
                         : std::snprintf(message.data(), message.size(),
                                         "Glyph selection: input glyph='%.*s' (UTF8 0x%08x) => selected glyph='%.*s' "
                                         "(UTF8 0x%08x), %s, source mode=%d, dynamic glyph table=%d",
                                         read_text.size, read_text.bytes.data(), read_key, selected_text.size,
                                         selected_text.bytes.data(), selected_key_read ? selected_key : 0, source,
                                         source_mode, dynamic_table_index);
    if (size <= 0 || static_cast<std::size_t>(size) >= message.size()) {
        return;
    }
    log_glyph_message_once(state, {message.data(), static_cast<std::size_t>(size)});
}

bool game_root_and_log(State& state) {
    std::array<wchar_t, 32'768> buffer{};
    const DWORD size = ::GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (size == 0 || size >= buffer.size()) {
        return false;
    }
    std::wstring executable{buffer.data(), size};
    const auto separator = executable.find_last_of(L"\\/");
    if (separator == std::wstring::npos || separator == 0) {
        return false;
    }
    if (!get_full_path(executable.substr(0, separator), state.game_root)) {
        return false;
    }
    state.log_path = join_path(state.game_root, Config::log_name);
    state.bridge_run_id = std::to_string(::GetCurrentProcessId());
    return true;
}

bool signature_at(const std::uint8_t* address, std::span<const std::uint8_t> signature,
                  std::span<const std::uint8_t> mask) {
    for (std::size_t index = 0; index < signature.size(); ++index) {
        if (mask[index] != 0 && address[index] != signature[index]) {
            return false;
        }
    }
    return true;
}

bool compatible_module_header(const IMAGE_NT_HEADERS64& nt, const ModuleSpec& spec) {
    if (nt.Signature != IMAGE_NT_SIGNATURE || nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt.FileHeader.TimeDateStamp != spec.pe_timestamp || nt.FileHeader.NumberOfSections == 0 ||
        nt.FileHeader.NumberOfSections > 96) {
        return false;
    }
    if (nt.OptionalHeader.SizeOfImage == spec.image_size) {
        return true;
    }
    if (std::string_view{spec.segment} != "B1" || nt.OptionalHeader.SizeOfImage != 0x1a8e000 ||
        nt.FileHeader.NumberOfSections != 9 ||
        nt.FileHeader.SizeOfOptionalHeader != sizeof(IMAGE_OPTIONAL_HEADER64)) {
        return false;
    }
    const auto& section = IMAGE_FIRST_SECTION(&nt)[8];
    return std::memcmp(section.Name, ".br4cos", 8) == 0 && section.VirtualAddress == 0x1a8c000 &&
           section.Misc.VirtualSize == 0x1a1f && section.SizeOfRawData == 0x1c00 &&
           section.Characteristics == 0xe0000040;
}

bool validate_module(HMODULE module, const ModuleSpec& spec, LPVOID& target, LPVOID& size_target) {
    const auto* base = reinterpret_cast<const std::uint8_t*>(module);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 0x10'000) {
        return false;
    }
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (!compatible_module_header(*nt, spec)) {
        return false;
    }

    const auto* expected = base + spec.open_rva;
    if (spec.open_rva < 16 || spec.open_rva + extended_signature.size() > spec.image_size ||
        spec.open_rva + 48 > spec.image_size ||
        !signature_at(expected, extended_signature, extended_signature_mask) ||
        !std::equal(spec.open_context.begin(), spec.open_context.end(), expected - 16)) {
        return false;
    }
    target = const_cast<std::uint8_t*>(expected);

    if (spec.file_size_rva + spec.file_size_context.size() > spec.image_size ||
        !std::equal(spec.file_size_context.begin(), spec.file_size_context.end(), base + spec.file_size_rva)) {
        return false;
    }
    size_target = const_cast<std::uint8_t*>(base + spec.file_size_rva);

    constexpr char rtti_name[] = ".?AVEXBufferedFileInputStreamMemory@elixir@@";
    constexpr std::size_t rtti_header_size = 16;
    if (spec.rtti_type_descriptor_rva + rtti_header_size + sizeof(rtti_name) > spec.image_size ||
        std::memcmp(base + spec.rtti_type_descriptor_rva + rtti_header_size, rtti_name, sizeof(rtti_name)) != 0) {
        return false;
    }
    return true;
}

const GlyphLookupSpec* glyph_lookup_spec(std::string_view segment) {
    for (const auto& spec : glyph_lookup_specs) {
        if (segment == spec.segment) {
            return &spec;
        }
    }
    return nullptr;
}

const ReadWholeSpec* read_whole_spec(std::string_view segment) {
    for (const auto& spec : read_whole_specs) {
        if (segment == spec.segment) {
            return &spec;
        }
    }
    return nullptr;
}

bool validate_read_whole(HMODULE module, const ModuleSpec& module_spec, LPVOID& target) {
    const auto* spec = read_whole_spec(module_spec.segment);
    if (spec == nullptr) {
        target = nullptr;
        return true;
    }
    if (spec->rva + spec->signature.size() > module_spec.image_size) {
        return false;
    }
    const auto* expected = reinterpret_cast<const std::uint8_t*>(module) + spec->rva;
    if (!std::equal(spec->signature.begin(), spec->signature.end(), expected)) {
        return false;
    }
    target = const_cast<std::uint8_t*>(expected);
    return true;
}

bool validate_glyph_lookup(HMODULE module, const ModuleSpec& module_spec, LPVOID& target) {
    const auto* lookup_spec = glyph_lookup_spec(module_spec.segment);
    if (lookup_spec == nullptr) {
        return false;
    }
    const auto* base = reinterpret_cast<const std::uint8_t*>(module);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 0x10'000) {
        return false;
    }
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(base + dos->e_lfanew);
    if (!compatible_module_header(*nt, module_spec)) {
        return false;
    }

    if (lookup_spec->rva >= module_spec.image_size) {
        return false;
    }
    const auto* expected = base + lookup_spec->rva;
    bool valid{};
    if (lookup_spec->record_size == sizeof(GlyphRecord48)) {
        auto signature = glyph_signature_0x48;
        std::copy(lookup_spec->signature_patch.begin(), lookup_spec->signature_patch.end(), signature.begin() + 42);
        valid = lookup_spec->rva + signature.size() <= module_spec.image_size &&
                signature_at(expected, signature, glyph_signature_mask_0x48);
    } else {
        valid = lookup_spec->record_size == sizeof(GlyphRecord28) &&
                lookup_spec->rva + glyph_signature_0x1c.size() <= module_spec.image_size &&
                signature_at(expected, glyph_signature_0x1c, glyph_signature_mask_0x1c);
    }
    if (valid) {
        target = const_cast<std::uint8_t*>(expected);
    }
    return valid;
}

bool validate_b1_text_hooks(HMODULE module, const ModuleSpec& spec, LPVOID& lookup, LPVOID& setter) {
    lookup = nullptr;
    setter = nullptr;
    if (std::string_view{spec.segment} != "B1") {
        return true;
    }
    const auto* base = reinterpret_cast<const std::uint8_t*>(module);
    if (base == nullptr || b1_string_lookup_rva + b1_string_lookup_signature.size() > spec.image_size ||
        b1_text_set_rva + b1_text_set_signature.size() > spec.image_size ||
        !std::equal(b1_string_lookup_signature.begin(), b1_string_lookup_signature.end(), base + b1_string_lookup_rva) ||
        !std::equal(b1_text_set_signature.begin(), b1_text_set_signature.end(), base + b1_text_set_rva)) {
        return false;
    }
    lookup = const_cast<std::uint8_t*>(base + b1_string_lookup_rva);
    setter = const_cast<std::uint8_t*>(base + b1_text_set_rva);
    return true;
}

enum class ReadResult {
    ok,
    missing,
    failed,
};

ReadResult read_small_file(const std::wstring& path, std::vector<char>& output) {
    const HANDLE file = ::CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                      nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        const DWORD error = ::GetLastError();
        return (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) ? ReadResult::missing : ReadResult::failed;
    }
    LARGE_INTEGER size{};
    if (!::GetFileSizeEx(file, &size) || size.QuadPart < 0 || size.QuadPart > Config::max_manifest_bytes) {
        ::CloseHandle(file);
        return ReadResult::failed;
    }
    output.resize(static_cast<std::size_t>(size.QuadPart));
    std::size_t offset{};
    while (offset < output.size()) {
        DWORD read{};
        const DWORD wanted = static_cast<DWORD>(std::min<std::size_t>(output.size() - offset, 1U << 20));
        if (!::ReadFile(file, output.data() + offset, wanted, &read, nullptr) || read == 0) {
            ::CloseHandle(file);
            return ReadResult::failed;
        }
        offset += read;
    }
    ::CloseHandle(file);
    return ReadResult::ok;
}

std::string_view trim_manifest_line(std::string_view line) {
    while (!line.empty() && (line.front() == ' ' || line.front() == '\t')) {
        line.remove_prefix(1);
    }
    while (!line.empty() && (line.back() == ' ' || line.back() == '\t' || line.back() == '\r')) {
        line.remove_suffix(1);
    }
    return line;
}

bool safe_regular_file(std::wstring_view root_input, std::wstring_view file_input, std::wstring& full_file) {
    std::wstring root;
    if (!get_full_path(root_input, root) || !get_full_path(file_input, full_file) || !is_under_root(full_file, root)) {
        return false;
    }

    DWORD attributes = ::GetFileAttributesW(root.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
        (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        return false;
    }

    std::wstring current = root;
    std::size_t begin = root.size() + 1;
    while (begin < full_file.size()) {
        const auto end = full_file.find(L'\\', begin);
        current.push_back(L'\\');
        current.append(full_file, begin, end == std::wstring::npos ? full_file.size() - begin : end - begin);
        attributes = ::GetFileAttributesW(current.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            return false;
        }
        if (end == std::wstring::npos) {
            WIN32_FILE_ATTRIBUTE_DATA information{};
            return (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
                   ::GetFileAttributesExW(full_file.c_str(), GetFileExInfoStandard, &information) != FALSE &&
                   (information.nFileSizeHigh != 0 || information.nFileSizeLow != 0);
        }
        if ((attributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
            return false;
        }
        begin = end + 1;
    }
    return false;
}

bool add_asset(State& state, std::string game_path, std::wstring physical_path, AssetKind kind) {
    const std::string key = make_lookup_key(game_path);
    if (state.assets_by_key.contains(key)) {
        return false;
    }

    std::array<char, 24> file_name{};
    const int written = std::snprintf(file_name.data(), file_name.size(), "%08zu.bin", state.assets_by_key.size() + 1);
    if (written <= 0 || static_cast<std::size_t>(written) >= file_name.size()) {
        return false;
    }

    Asset asset{};
    asset.kind = kind;
    asset.glyph_slot = font_slot_for_request(key, state.spec->segment);
    asset.b1_main_font = is_b1_main_font(state.spec->segment, key);
    asset.game_path = std::move(game_path);
    const std::string bridge_relative_ascii = "BRQuartetKorean/" + state.bridge_run_id + "/" +
                                              std::string{state.spec->segment} + "/" + file_name.data();
    asset.bridge_path = uses_root_file_bridge(state.spec->segment) ? bridge_relative_ascii
                                                                   : "res:" + bridge_relative_ascii;
    const std::wstring bridge_relative = L"BRQuartetKorean\\" + widen_ascii(state.bridge_run_id) + L"\\" +
                                         widen_ascii(state.spec->segment) + L"\\" + widen_ascii(file_name.data());
    asset.bridge_suffix_key = lower_ascii(L"\\" + bridge_relative);
    if (uses_root_file_bridge(state.spec->segment)) {
        asset.bridge_files = {join_path(state.game_root, bridge_relative)};
    } else {
        const std::string_view loose_data_segment = std::string_view{state.spec->segment} == "B1" ? "B1" : "B4";
        asset.bridge_files = {
            join_path(join_path(join_path(join_path(state.game_root, L"Data"), widen_ascii(loose_data_segment)), L"x64"),
                      bridge_relative),
        };
    }
    asset.physical_path = std::move(physical_path);
    state.assets_by_key.emplace(key, std::move(asset));
    return true;
}

void log_manifest_error(const State& state, std::size_t line) {
    log_line(state, "Replacement manifest line " + std::to_string(line) + " is invalid; using original game files.");
}

[[maybe_unused]] bool load_external_assets(State& state) {
    const std::wstring segment_root = join_path(join_path(state.game_root, Config::asset_root), widen_ascii(state.spec->segment));
    std::wstring manifest_path;
    if (!safe_regular_file(state.game_root, join_path(segment_root, Config::manifest_name), manifest_path)) {
        log_line(state, "Replacement manifest is missing or unsafe; using original game files.");
        return false;
    }
    std::vector<char> manifest;
    const auto result = read_small_file(manifest_path, manifest);
    if (result == ReadResult::missing) {
        log_line(state, "Replacement manifest not found; using original game files.");
        return false;
    }
    if (result != ReadResult::ok) {
        log_line(state, "Could not read the replacement manifest; using original game files.");
        return false;
    }

    std::string_view contents{manifest.data(), manifest.size()};
    if (contents.starts_with(std::string_view{"\xef\xbb\xbf", 3})) {
        contents.remove_prefix(3);
    }
    std::size_t line_number{};
    while (!contents.empty()) {
        ++line_number;
        const auto newline = contents.find('\n');
        auto line = trim_manifest_line(contents.substr(0, newline));
        if (newline == std::string_view::npos) {
            contents = {};
        } else {
            contents.remove_prefix(newline + 1);
        }
        if (line.empty() || line.front() == '#') {
            continue;
        }
        if (state.assets_by_key.size() >= Config::max_manifest_entries || line.size() > Config::max_internal_path ||
            !is_safe_manifest_path(line)) {
            log_manifest_error(state, line_number);
            return false;
        }

        const std::string game_path{line};
        const std::string key = make_lookup_key(game_path);
        const auto kind = classify_lookup_key(key, state.spec->segment);
        std::wstring relative = widen_ascii(game_path);
        std::replace(relative.begin(), relative.end(), L'/', L'\\');
        std::wstring physical;
        if (kind == AssetKind::none || !safe_regular_file(state.game_root, join_path(segment_root, relative), physical) ||
            !add_asset(state, game_path, std::move(physical), kind)) {
            log_manifest_error(state, line_number);
            return false;
        }
    }
    return true;
}

template <typename Record>
bool load_external_glyph_table(State& state, std::wstring_view relative_path, std::vector<Record>& output) {
    const std::wstring segment_root =
        join_path(join_path(state.game_root, Config::asset_root), widen_ascii(state.spec->segment));
    std::wstring physical;
    if (!safe_regular_file(state.game_root, join_path(segment_root, relative_path), physical)) {
        return false;
    }
    std::vector<char> bytes;
    if (read_small_file(physical, bytes) != ReadResult::ok) {
        return false;
    }
    return parse_glyph_table(
        std::span<const std::byte>{reinterpret_cast<const std::byte*>(bytes.data()), bytes.size()}, output);
}

[[maybe_unused]] bool load_external_glyph_tables(State& state) {
    if (std::string_view{state.spec->segment} == "B1") {
        return load_external_glyph_table(state, L"Font\\ko-glyph-table-0x1c.bin", state.glyph_table28);
    }
    constexpr std::array<std::wstring_view, 3> names{
        L"Font\\main00-glyph-table-0x48.bin",
        L"Font\\main01-glyph-table-0x48.bin",
        L"Font\\main02-glyph-table-0x48.bin",
    };
    for (std::size_t slot = 0; slot < names.size(); ++slot) {
        if (!load_external_glyph_table(state, names[slot], state.glyph_tables48[slot])) {
            return false;
        }
    }
    return true;
}

#if BRQ_EMBED_ASSETS
bool embedded_resource_bytes(const State& state, std::uint16_t resource_id, std::span<const std::byte>& output) {
    const HRSRC resource = ::FindResourceW(state.self, MAKEINTRESOURCEW(resource_id), RT_RCDATA);
    if (resource == nullptr) {
        return false;
    }
    const HGLOBAL loaded = ::LoadResource(state.self, resource);
    const DWORD size = ::SizeofResource(state.self, resource);
    const void* bytes = loaded == nullptr ? nullptr : ::LockResource(loaded);
    if (bytes == nullptr || size == 0) {
        return false;
    }
    output = {static_cast<const std::byte*>(bytes), size};
    return true;
}

bool write_resource_to_temporary_file(State& state, const EmbeddedAsset& descriptor, std::wstring& output) {
    std::span<const std::byte> bytes;
    if (!embedded_resource_bytes(state, descriptor.resource_id, bytes)) {
        return false;
    }

    std::array<wchar_t, 32'768> temp_root{};
    const DWORD root_size = ::GetTempPathW(static_cast<DWORD>(temp_root.size()), temp_root.data());
    if (root_size == 0 || root_size >= temp_root.size()) {
        return false;
    }
    std::array<wchar_t, 96> file_name{};
    const int name_size = std::swprintf(file_name.data(), file_name.size(), L"BRQuartetKorean-%lu-%llu-%u.tmp",
                                        ::GetCurrentProcessId(), static_cast<unsigned long long>(::GetTickCount64()),
                                        descriptor.resource_id);
    if (name_size <= 0 || static_cast<std::size_t>(name_size) >= file_name.size()) {
        return false;
    }
    output = join_path(std::wstring_view{temp_root.data(), root_size}, file_name.data());

    const auto create_file = original_create_file_w != nullptr ? original_create_file_w : &::CreateFileW;
    HANDLE writer = game_call_noexcept(
        [&] {
            return create_file(output.c_str(), GENERIC_WRITE,
                               FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, CREATE_NEW,
                               FILE_ATTRIBUTE_TEMPORARY, nullptr);
        },
        []() noexcept { return INVALID_HANDLE_VALUE; });
    if (writer == INVALID_HANDLE_VALUE) {
        return false;
    }
    DWORD written{};
    const bool write_ok = ::WriteFile(writer, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr) != FALSE &&
                          written == bytes.size();
    ::CloseHandle(writer);
    if (!write_ok) {
        ::DeleteFileW(output.c_str());
        return false;
    }

    try {
        state.temporary_files.push_back(output);
    } catch (...) {
        ::DeleteFileW(output.c_str());
        return false;
    }
    return true;
}

bool load_embedded_assets(State& state) {
    for (const auto& descriptor : embedded_assets) {
        if (std::strcmp(descriptor.segment, state.spec->segment) != 0) {
            continue;
        }
        const std::string game_path{descriptor.game_path};
        const std::string key = make_lookup_key(game_path);
        const auto kind = classify_lookup_key(key, state.spec->segment);
        std::wstring physical;
        if (!is_safe_manifest_path(game_path) || kind == AssetKind::none ||
            !write_resource_to_temporary_file(state, descriptor, physical) ||
            !add_asset(state, game_path, std::move(physical), kind)) {
            log_line(state, "Could not prepare embedded replacement files; using original game files.");
            return false;
        }
    }
    return true;
}

bool load_embedded_glyph_tables(State& state) {
    const bool is_b1 = std::string_view{state.spec->segment} == "B1";
    std::array<bool, 3> found{};
    std::size_t found_count{};
    for (const auto& descriptor : embedded_glyph_tables) {
        if (std::strcmp(descriptor.segment, state.spec->segment) != 0) {
            continue;
        }
        if (descriptor.slot >= found.size() || found[descriptor.slot]) {
            return false;
        }
        std::span<const std::byte> bytes;
        if (!embedded_resource_bytes(state, descriptor.resource_id, bytes)) {
            return false;
        }
        if (!is_b1 && descriptor.record_size == sizeof(GlyphRecord48)) {
            if (!parse_glyph_table(bytes, state.glyph_tables48[descriptor.slot])) {
                return false;
            }
        } else if (is_b1 && descriptor.record_size == sizeof(GlyphRecord28) && descriptor.slot == 0) {
            if (!parse_glyph_table(bytes, state.glyph_table28)) {
                return false;
            }
        } else {
            return false;
        }
        found[descriptor.slot] = true;
        ++found_count;
    }
    return is_b1 ? found_count == 1 && found[0] : found_count == 3;
}
#endif

HANDLE open_source_pack(const std::wstring& path) {
    const auto create = original_create_file_w != nullptr ? original_create_file_w : &::CreateFileW;
    // Keep verified PAKs open without write/delete sharing for the lifetime of their hooks.
    return create(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN,
                  nullptr);
}

bool source_pack_stamp(HANDLE file, SourcePackStamp& stamp) {
    BY_HANDLE_FILE_INFORMATION information{};
    if (::GetFileInformationByHandle(file, &information) == FALSE) {
        return false;
    }
    const auto join = [](DWORD high, DWORD low) { return (std::uint64_t{high} << 32) | low; };
    stamp.size = join(information.nFileSizeHigh, information.nFileSizeLow);
    stamp.last_write = join(information.ftLastWriteTime.dwHighDateTime, information.ftLastWriteTime.dwLowDateTime);
    stamp.creation = join(information.ftCreationTime.dwHighDateTime, information.ftCreationTime.dwLowDateTime);
    stamp.file_index = join(information.nFileIndexHigh, information.nFileIndexLow);
    stamp.volume_serial = information.dwVolumeSerialNumber;
    FILE_BASIC_INFO basic{};
    if (::GetFileInformationByHandleEx(file, FileBasicInfo, &basic, sizeof(basic)) == FALSE) {
        return false;
    }
    stamp.change = static_cast<std::uint64_t>(basic.ChangeTime.QuadPart);
    return true;
}

std::string handle_sha256(HANDLE file) {
    BCRYPT_ALG_HANDLE algorithm{};
    BCRYPT_HASH_HANDLE hash{};
    std::array<unsigned char, 32> digest{};
    bool ok = ::BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0;
    if (ok) {
        ok = ::BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0;
    }
    std::vector<unsigned char> buffer(4U << 20);
    while (ok) {
        DWORD read{};
        if (!::ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr)) {
            ok = false;
            break;
        }
        if (read == 0) {
            break;
        }
        ok = ::BCryptHashData(hash, buffer.data(), read, 0) >= 0;
    }
    if (ok) {
        ok = ::BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0;
    }
    if (hash != nullptr) { ::BCryptDestroyHash(hash); }
    if (algorithm != nullptr) { ::BCryptCloseAlgorithmProvider(algorithm, 0); }
    if (!ok) {
        return {};
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result(64, '0');
    for (std::size_t i = 0; i < digest.size(); ++i) {
        result[2 * i] = hex[digest[i] >> 4];
        result[2 * i + 1] = hex[digest[i] & 15];
    }
    return result;
}

std::wstring source_pack_cache_path(const State& state) {
    return join_path(state.game_root, std::wstring{Config::pack_cache_prefix} + widen_ascii(state.spec->segment) +
                                          Config::pack_cache_suffix);
}

void write_source_pack_cache(const State& state, std::string_view contents) {
    const std::wstring path = source_pack_cache_path(state);
    const std::wstring temporary = path + L".tmp";
    const auto create = original_create_file_w != nullptr ? original_create_file_w : &::CreateFileW;
    const HANDLE file = create(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    DWORD written{};
    const bool ok = ::WriteFile(file, contents.data(), static_cast<DWORD>(contents.size()), &written, nullptr) != FALSE &&
                    written == contents.size() && ::FlushFileBuffers(file) != FALSE;
    ::CloseHandle(file);
    if (!ok || ::MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) == FALSE) {
        ::DeleteFileW(temporary.c_str());
    }
}

void verify_source_packs(State& state) {
    state.source_packs_match = true;
    // Hashing B1's 1.2 GB PAK takes seconds. Only a file whose identity and size are unchanged since a
    // successful hash is trusted without rehashing; the handle stays open without write sharing.
    std::vector<char> cache_bytes;
    if (read_small_file(source_pack_cache_path(state), cache_bytes) != ReadResult::ok) {
        cache_bytes.clear();
    }
    const std::string_view cache{cache_bytes.data(), cache_bytes.size()};
    std::string verified_lines;
    bool hashed_any{};
    std::size_t reused{};
    for (const auto& expected : source_pack_fingerprints) {
        if (std::strcmp(expected.segment, state.spec->segment) != 0) { continue; }
        HANDLE handle{INVALID_HANDLE_VALUE};
        std::wstring path;
        bool matches{};
        SourcePackStamp stamp{};
        if (safe_regular_file(state.game_root, join_path(state.game_root, widen_ascii(expected.path)), path)) {
            handle = open_source_pack(path);
        }
        const bool stamped = handle != INVALID_HANDLE_VALUE && source_pack_stamp(handle, stamp);
        if (stamped && source_pack_cached(cache, expected, stamp)) {
            matches = true;
            ++reused;
        } else if (handle != INVALID_HANDLE_VALUE) {
            matches = source_pack_matches(handle_sha256(handle), expected);
            hashed_any = true;
        }
        if (matches && stamped) {
            verified_lines += source_pack_cache_line(expected, stamp);
            verified_lines += '\n';
        }
        if (handle != INVALID_HANDLE_VALUE) {
            if (matches) { state.verified_pack_handles.push_back(handle); }
            else { ::CloseHandle(handle); }
        }
        state.source_packs_match &= matches;
        if (!matches) {
            log_line(state, std::string{"Build input changed or unavailable: "} + expected.path);
        }
    }
    if (hashed_any && verified_lines != cache) {
        write_source_pack_cache(state, verified_lines);
    }
    if (reused != 0) {
        log_line(state, "Unchanged source PAKs reused from the verification cache: " + std::to_string(reused));
    }
    if (!state.source_packs_match) {
        std::erase_if(state.assets_by_key, [](const auto& item) {
            return item.second.kind == AssetKind::event || item.second.kind == AssetKind::text;
        });
        log_line(state, "Source PAKs differ from the build inputs; rebuild translations to replace text.");
    }
}

void build_bridge_index(State& state) {
    state.assets_by_bridge_key.reserve(state.assets_by_key.size());
    state.assets_by_bridge_suffix.reserve(state.assets_by_key.size());
    for (const auto& [key, asset] : state.assets_by_key) {
        static_cast<void>(key);
        state.assets_by_bridge_key.emplace(make_lookup_key(clean_path(asset.bridge_path)), &asset);
        state.assets_by_bridge_suffix.emplace(asset.bridge_suffix_key, &asset);
    }
}

struct BridgeMatch final {
    State* state{};
    const Asset* asset{};
};

bool bridge_match_active(const BridgeMatch& match) noexcept {
    return match.state != nullptr && match.asset != nullptr &&
           !match.state->replacements_disabled.load(std::memory_order_acquire);
}

BridgeMatch bridge_asset_any(LPCWSTR requested_path) {
    if (requested_path == nullptr) {
        return {};
    }
    std::size_t length{};
    while (length < 32'767 && requested_path[length] != L'\0') {
        ++length;
    }
    if (length == 0 || length == 32'767) {
        return {};
    }

    State* root_state{};
    for (auto& slot : hook_slots) {
        if (State* const state = slot.state.load(std::memory_order_acquire); state != nullptr) {
            root_state = state;
            break;
        }
    }
    if (root_state == nullptr) {
        return {};
    }

    std::wstring full;
    if (!get_full_path(std::wstring_view{requested_path, length}, full) ||
        !is_under_root(full, root_state->game_root)) {
        return {};
    }
    auto key = lower_ascii(std::move(full));
    const auto suffix = bridge_suffix_from_key(key);
    if (suffix.empty()) {
        return {};
    }
    const std::wstring suffix_key{suffix};
    for (auto& slot : hook_slots) {
        State* const state = slot.state.load(std::memory_order_acquire);
        if (state == nullptr || state->replacements_disabled.load(std::memory_order_acquire)) {
            continue;
        }
        const auto found = state->assets_by_bridge_suffix.find(suffix_key);
        if (found != state->assets_by_bridge_suffix.end()) {
            return {state, found->second};
        }
    }
    return {};
}

BridgeMatch bridge_asset_any_a(LPCSTR requested_path) {
    if (requested_path == nullptr) {
        return {};
    }
    std::size_t length{};
    while (length < 32'767 && requested_path[length] != '\0') {
        ++length;
    }
    if (length == 0 || length == 32'767) {
        return {};
    }
    const int needed = ::MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, requested_path,
                                              static_cast<int>(length), nullptr, 0);
    if (needed <= 0) {
        return {};
    }
    std::vector<wchar_t> wide(static_cast<std::size_t>(needed) + 1);
    if (::MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, requested_path, static_cast<int>(length),
                              wide.data(), needed) != needed) {
        return {};
    }
    wide[static_cast<std::size_t>(needed)] = L'\0';
    return bridge_asset_any(wide.data());
}

bool ensure_directory_tree(std::wstring_view root_input, std::wstring_view directory_input) noexcept {
    try {
        std::wstring root;
        std::wstring directory;
        if (!get_full_path(root_input, root) || !get_full_path(directory_input, directory) ||
            !is_under_root(directory, root)) {
            ::SetLastError(ERROR_INVALID_NAME);
            return false;
        }
        DWORD attributes = ::GetFileAttributesW(root.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
            (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
            if (attributes != INVALID_FILE_ATTRIBUTES) {
                ::SetLastError((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 ? ERROR_ACCESS_DENIED
                                                                                : ERROR_DIRECTORY);
            }
            return false;
        }
        std::wstring current = root;
        std::size_t begin = root.size() + 1;
        while (begin < directory.size()) {
            const auto end = directory.find(L'\\', begin);
            current.push_back(L'\\');
            current.append(directory, begin, end == std::wstring::npos ? directory.size() - begin : end - begin);
            attributes = ::GetFileAttributesW(current.c_str());
            if (attributes == INVALID_FILE_ATTRIBUTES) {
                if (::CreateDirectoryW(current.c_str(), nullptr) == FALSE && ::GetLastError() != ERROR_ALREADY_EXISTS) {
                    return false;
                }
                attributes = ::GetFileAttributesW(current.c_str());
            }
            if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 ||
                (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
                if (attributes != INVALID_FILE_ATTRIBUTES) {
                    ::SetLastError((attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 ? ERROR_ACCESS_DENIED
                                                                                    : ERROR_DIRECTORY);
                }
                return false;
            }
            if (end == std::wstring::npos) {
                ::SetLastError(ERROR_SUCCESS);
                return true;
            }
            begin = end + 1;
        }
    } catch (...) {
        ::SetLastError(ERROR_UNHANDLED_EXCEPTION);
    }
    if (::GetLastError() == ERROR_SUCCESS) {
        ::SetLastError(ERROR_DIRECTORY);
    }
    return false;
}

bool physical_file_available(const Asset& asset);
bool call_open_noexcept(OpenFn call_original, void* self, const char* path, int a3, int a4, int a5) noexcept;

bool create_bridge_files(State& state, const Asset& asset) noexcept {
    const auto create_file = original_create_file_w;
    if (create_file == nullptr) {
        return false;
    }
    std::size_t created{};
    DWORD failure{ERROR_SUCCESS};
    for (const auto& bridge_file : asset.bridge_files) {
        const auto separator = bridge_file.find_last_of(L"\\/");
        if (separator == std::wstring::npos) {
            failure = ERROR_INVALID_NAME;
            break;
        }
        if (!ensure_directory_tree(state.game_root, std::wstring_view{bridge_file}.substr(0, separator))) {
            failure = ::GetLastError();
            break;
        }
        const HANDLE handle = game_call_noexcept(
            [&] {
                return create_file(bridge_file.c_str(), GENERIC_WRITE,
                                   FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, CREATE_ALWAYS,
                                   FILE_ATTRIBUTE_NORMAL, nullptr);
            },
            []() noexcept { return INVALID_HANDLE_VALUE; });
        if (handle == INVALID_HANDLE_VALUE) {
            failure = ::GetLastError();
            break;
        }
        ::CloseHandle(handle);
        ++created;
    }
    if (created == asset.bridge_files.size()) {
        return true;
    }
    for (std::size_t index = 0; index < created; ++index) {
        ::DeleteFileW(asset.bridge_files[index].c_str());
    }
    ::SetLastError(failure == ERROR_SUCCESS ? ERROR_CANNOT_MAKE : failure);
    return false;
}

void remove_bridge_files(const Asset& asset) noexcept {
    for (const auto& bridge_file : asset.bridge_files) {
        ::DeleteFileW(bridge_file.c_str());
        auto directory = bridge_file.substr(0, bridge_file.find_last_of(L"\\/"));
        for (int level = 0; level < 3 && !directory.empty(); ++level) {
            ::RemoveDirectoryW(directory.c_str());
            const auto separator = directory.find_last_of(L"\\/");
            directory.resize(separator == std::wstring::npos ? 0 : separator);
        }
    }
}

bool call_replacement_open(State& state, const Asset& asset, OpenFn call_original, void* self,
                           int a3, int a4, int a5, DWORD& failure) noexcept {
    failure = ERROR_SUCCESS;
    if (!physical_file_available(asset)) {
        failure = ::GetLastError();
        return false;
    }
    ExclusiveSrwGuard guard{bridge_file_lock};
    if (!create_bridge_files(state, asset)) {
        failure = ::GetLastError();
        try {
            log_line(state, "Failed to prepare replacement path (error " + std::to_string(failure) + "): " +
                                replacement_log_message(state.spec->segment, asset.game_path, BRQ_EMBED_ASSETS != 0));
        } catch (...) {
        }
        return false;
    }
    if (Config::diagnostics && !state.bridge_open_probe_logged.exchange(true, std::memory_order_acq_rel)) {
        try {
            log_line(state, "Replacement path for the game: " + asset.game_path + " => " + asset.bridge_path +
                                "; actual file: " +
                                replacement_log_message(state.spec->segment, asset.game_path, BRQ_EMBED_ASSETS != 0));
        } catch (...) {
        }
    }
    ::SetLastError(ERROR_SUCCESS);
    const bool opened = call_open_noexcept(call_original, self, asset.bridge_path.c_str(), a3, a4, a5);
    if (!opened) {
        failure = ::GetLastError();
    }
    remove_bridge_files(asset);
    if (!opened) {
        if (failure == ERROR_SUCCESS) {
            failure = ERROR_OPEN_FAILED;
        }
        try {
            log_line(state, "The game could not open the replacement path (error " + std::to_string(failure) + "): " +
                                asset.game_path + " => " + asset.bridge_path);
        } catch (...) {
        }
        ::SetLastError(failure);
    }
    return opened;
}

bool physical_file_size(const Asset& asset, std::uint32_t& size) {
    WIN32_FILE_ATTRIBUTE_DATA information{};
    const auto get_attributes = original_get_file_attributes_ex_w != nullptr ? original_get_file_attributes_ex_w
                                                                              : &::GetFileAttributesExW;
    if (game_call_noexcept(
            [&] { return get_attributes(asset.physical_path.c_str(), GetFileExInfoStandard, &information); },
            []() noexcept { return FALSE; }) == FALSE) {
        return false;
    }
    if ((information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0 ||
        information.nFileSizeHigh != 0 || information.nFileSizeLow == 0) {
        ::SetLastError(ERROR_FILE_INVALID);
        return false;
    }
    size = information.nFileSizeLow;
    return true;
}

bool physical_file_available(const Asset& asset) {
    std::uint32_t size{};
    return physical_file_size(asset, size);
}

const Asset* asset_for_path(State& state, std::string_view raw_path, bool include_bridge) {
    const auto key = make_lookup_key(clean_path(raw_path));
    const auto kind = classify_lookup_key(key, state.spec->segment);
    if (const auto found = state.assets_by_key.find(key);
        kind != AssetKind::none && found != state.assets_by_key.end() && found->second.kind == kind) {
        return &found->second;
    }
    if (include_bridge) {
        if (const auto found = state.assets_by_bridge_key.find(key); found != state.assets_by_bridge_key.end()) {
            return found->second;
        }
    }
    return nullptr;
}

bool read_physical_file(const Asset& asset, void* destination, std::uint32_t requested_size,
                        DWORD& failure) noexcept {
    failure = ERROR_SUCCESS;
    if (destination == nullptr || requested_size == 0) {
        failure = ERROR_INVALID_PARAMETER;
        return false;
    }
    const auto create_file = original_create_file_w != nullptr ? original_create_file_w : &::CreateFileW;
    const HANDLE file = game_call_noexcept(
        [&] {
            return create_file(asset.physical_path.c_str(), GENERIC_READ,
                               FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
                               FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
        },
        []() noexcept { return INVALID_HANDLE_VALUE; });
    if (file == INVALID_HANDLE_VALUE) {
        failure = ::GetLastError();
        return false;
    }

    LARGE_INTEGER size{};
    if (::GetFileSizeEx(file, &size) == FALSE || size.QuadPart != requested_size) {
        failure = ::GetLastError();
        if (failure == ERROR_SUCCESS) {
            failure = ERROR_BAD_LENGTH;
        }
        ::CloseHandle(file);
        return false;
    }

    auto* output = static_cast<std::uint8_t*>(destination);
    std::uint32_t offset{};
    while (offset < requested_size) {
        DWORD read{};
        if (::ReadFile(file, output + offset, requested_size - offset, &read, nullptr) == FALSE || read == 0) {
            failure = ::GetLastError();
            if (failure == ERROR_SUCCESS) {
                failure = ERROR_HANDLE_EOF;
            }
            ::CloseHandle(file);
            return false;
        }
        offset += read;
    }
    if (::CloseHandle(file) == FALSE) {
        failure = ::GetLastError();
        return false;
    }
    return true;
}

struct B1FontDescriptorValues final {
    std::uint16_t width{};
    std::uint16_t height{};
    std::uint16_t glyph_count{};
};

std::uint8_t* b1_font_descriptor_address(State& state) noexcept {
    if (state.game_module == nullptr || state.spec == nullptr || std::string_view{state.spec->segment} != "B1" ||
        b1_font_descriptor_rva + b1_font_descriptor_count_offset + sizeof(std::uint16_t) > state.spec->image_size) {
        return nullptr;
    }
    return reinterpret_cast<std::uint8_t*>(state.game_module) + b1_font_descriptor_rva;
}

bool read_b1_font_descriptor(State& state, B1FontDescriptorValues& values) noexcept {
    const auto* address = b1_font_descriptor_address(state);
    if (address == nullptr) {
        return false;
    }
    std::memcpy(&values.width, address, sizeof(values.width));
    std::memcpy(&values.height, address + sizeof(values.width), sizeof(values.height));
    std::memcpy(&values.glyph_count, address + b1_font_descriptor_count_offset, sizeof(values.glyph_count));
    return true;
}

bool b1_font_descriptor_is_read_only(const void* address) noexcept {
    MEMORY_BASIC_INFORMATION information{};
    return ::VirtualQuery(address, &information, sizeof(information)) == sizeof(information) &&
           information.State == MEM_COMMIT && (information.Protect & 0xffU) == PAGE_READONLY;
}

bool set_b1_font_descriptor_height(State& state, std::uint16_t desired_height) noexcept {
    auto* address = b1_font_descriptor_address(state);
    B1FontDescriptorValues before{};
    if (address == nullptr || !read_b1_font_descriptor(state, before) ||
        b1_font_descriptor_plan("B1", b1_main_font_key, before.width, before.height, before.glyph_count) ==
            B1FontDescriptorPlan::reject ||
        (desired_height != b1_font_descriptor_original_height &&
         desired_height != b1_font_descriptor_replacement_height)) {
        return false;
    }
    if (before.height == desired_height) {
        return b1_font_descriptor_is_read_only(address);
    }
    if (!b1_font_descriptor_is_read_only(address)) {
        return false;
    }

    DWORD original_protection{};
    if (::VirtualProtect(address, 4, PAGE_READWRITE, &original_protection) == FALSE) {
        return false;
    }
    const std::array<std::uint16_t, 2> replacement{b1_font_descriptor_width, desired_height};
    std::memcpy(address, replacement.data(), sizeof(replacement));
    DWORD ignored{};
    const bool protection_restored = ::VirtualProtect(address, 4, original_protection, &ignored) != FALSE;

    B1FontDescriptorValues after{};
    if (protection_restored && read_b1_font_descriptor(state, after) &&
        after.width == replacement[0] && after.height == replacement[1] &&
        after.glyph_count == b1_font_descriptor_glyph_count && b1_font_descriptor_is_read_only(address)) {
        return true;
    }

    DWORD rollback_protection{};
    if (::VirtualProtect(address, 4, PAGE_READWRITE, &rollback_protection) != FALSE) {
        const std::array<std::uint16_t, 2> original{before.width, before.height};
        std::memcpy(address, original.data(), sizeof(original));
        ::VirtualProtect(address, 4, original_protection, &ignored);
    }
    return false;
}

bool glyph_lookup_enabled(const State& state, std::uint8_t slot) {
    return state.glyph_tables_ready && state.korean_text_committed.load(std::memory_order_acquire) &&
           (state.opened_font_slots.load(std::memory_order_acquire) & (1U << slot)) != 0;
}

bool call_open_noexcept(OpenFn call_original, void* self, const char* path, int a3, int a4, int a5) noexcept {
    return call_original != nullptr &&
           game_call_noexcept([&] { return call_original(self, path, a3, a4, a5); },
                              []() noexcept { return false; });
}

std::uint32_t call_file_size_noexcept(FileSizeFn call_original, const char* path) noexcept {
    return call_original != nullptr
               ? game_call_noexcept([&] { return call_original(path); }, []() noexcept { return std::uint32_t{}; })
               : 0;
}

bool call_read_whole_noexcept(ReadWholeFn call_original, const char* path, void* destination,
                              std::uint32_t requested_size, std::uint32_t* actual_size) noexcept {
    return call_original != nullptr &&
           game_call_noexcept([&] { return call_original(path, destination, requested_size, actual_size); },
                              [&]() noexcept {
                                  if (actual_size != nullptr) {
                                      *actual_size = 0;
                                  }
                                  return false;
                              });
}

const void* hooked_glyph_lookup48_impl(State* state, GlyphLookup48Fn call_original, std::uint32_t key, int slot,
                                       int dynamic_table_index) noexcept {
    if (call_original == nullptr) {
        return nullptr;
    }
    const bool valid_slot = state != nullptr && slot >= 0 && slot < static_cast<int>(state->glyph_tables48.size());
    const bool enabled = valid_slot && glyph_lookup_enabled(*state, static_cast<std::uint8_t>(slot));
    const auto* table = valid_slot ? &state->glyph_tables48[static_cast<std::size_t>(slot)] : nullptr;
    const auto* replacement = enabled ? find_glyph(*table, key) : nullptr;
    const bool replace_original_hangul = replacement != nullptr && is_packed_hangul_syllable(key);
    bool korean_selected = replace_original_hangul;
    const void* selected = replace_original_hangul
                               ? replacement
                               : game_call_noexcept([&] { return call_original(key, slot, dynamic_table_index); },
                                                    []() noexcept -> const void* { return nullptr; });
    if (selected == nullptr) {
        selected = replacement;
        korean_selected = replacement != nullptr;
    }
    const auto compatible_key = enabled && selected == nullptr ? compatible_glyph_key(key) : 0;
    const GlyphRecord48* compatible_replacement{};
    if (compatible_key != 0) {
        selected = game_call_noexcept([&] { return call_original(compatible_key, slot, dynamic_table_index); },
                                      []() noexcept -> const void* { return nullptr; });
        korean_selected = false;
        if (selected == nullptr) {
            compatible_replacement = find_compatible_glyph(*table, key);
            selected = compatible_replacement;
            korean_selected = compatible_replacement != nullptr;
        }
    }
    if (Config::diagnostics && state != nullptr) {
        const DWORD saved_error = ::GetLastError();
        const bool probe = take_glyph_probe(state->glyph_probe_count);
        const bool maybe_hangul = (key >= 0xac00 && key <= 0xd7a3) ||
                                  ((key >> 16) >= 0xea && (key >> 16) <= 0xed) ||
                                  ((key & 0xff) >= 0xea && (key & 0xff) <= 0xed);
        const bool diagnostic_candidate = maybe_hangul || replacement != nullptr || compatible_key != 0;
        const bool diagnostic_probe =
            diagnostic_candidate && take_glyph_probe(state->glyph_diagnostic_probe_count);
        if (probe || diagnostic_probe) {
            const auto* korean_record = korean_selected ? static_cast<const GlyphRecord48*>(selected) : nullptr;
            log_glyph_decision_once(*state, key, selected, korean_selected,
                                    valid_slot ? static_cast<std::uint8_t>(slot) : 0xff, -1,
                                    dynamic_table_index, korean_record);
        }
        ::SetLastError(saved_error);
    }
    return selected;
}

const void* hooked_glyph_lookup28_impl(State* state, GlyphLookup28Fn call_original, void* context, std::uint32_t key,
                                       int source_mode, int dynamic_table_index) noexcept {
    const bool probe = Config::diagnostics && state != nullptr && take_glyph_probe(state->glyph_probe_count);
    if (probe) {
        const DWORD saved_error = ::GetLastError();
        std::array<char, 256> message{};
        const int size = std::snprintf(message.data(), message.size(),
            "B1 glyph lookup entry: key=0x%08x, source mode=%d, dynamic glyph table=%d, context=%p, original=%p",
            key, source_mode, dynamic_table_index, context, reinterpret_cast<void*>(call_original));
        if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
            log_glyph_message_once(*state, {message.data(), static_cast<std::size_t>(size)});
        }
        if (!state->b1_binding_probe_logged.exchange(true, std::memory_order_relaxed)) {
            log_b1_font_binding_once(*state, context, source_mode);
        }
        ::SetLastError(saved_error);
    }
    if (call_original == nullptr) {
        return nullptr;
    }
    const bool enabled = state != nullptr && source_mode == 0 && glyph_lookup_enabled(*state, 0);
    const auto* replacement = enabled ? find_glyph(state->glyph_table28, key) : nullptr;
    const bool replace_original_hangul = replacement != nullptr && is_packed_hangul_syllable(key);
    bool korean_selected = replace_original_hangul;
    const void* selected =
        replace_original_hangul
            ? replacement
            : game_call_noexcept([&] { return call_original(context, key, source_mode, dynamic_table_index); },
                                 []() noexcept -> const void* { return nullptr; });
    if (selected == nullptr) {
        selected = replacement;
        korean_selected = replacement != nullptr;
    }
    const auto compatible_key = enabled && selected == nullptr ? compatible_glyph_key(key) : 0;
    const GlyphRecord28* compatible_replacement{};
    if (compatible_key != 0) {
        selected = game_call_noexcept(
            [&] { return call_original(context, compatible_key, source_mode, dynamic_table_index); },
            []() noexcept -> const void* { return nullptr; });
        korean_selected = false;
        if (selected == nullptr) {
            compatible_replacement = find_compatible_glyph(state->glyph_table28, key);
            selected = compatible_replacement;
            korean_selected = compatible_replacement != nullptr;
        }
    }
    if (Config::diagnostics && state != nullptr) {
        const DWORD saved_error = ::GetLastError();
        const bool maybe_hangul = (key >= 0xac00 && key <= 0xd7a3) ||
                                  ((key >> 16) >= 0xea && (key >> 16) <= 0xed) ||
                                  ((key & 0xff) >= 0xea && (key & 0xff) <= 0xed);
        const bool diagnostic_candidate = maybe_hangul || replacement != nullptr || compatible_key != 0;
        const bool diagnostic_probe =
            diagnostic_candidate && take_glyph_probe(state->glyph_diagnostic_probe_count);
        if (probe || diagnostic_probe) {
            GlyphRecord28 record{};
            SIZE_T read{};
            const bool readable = selected != nullptr &&
                ::ReadProcessMemory(::GetCurrentProcess(), selected, &record, sizeof(record), &read) != FALSE &&
                read == sizeof(record);
            log_glyph_decision_once(*state, key, selected, korean_selected, 0, source_mode,
                                    dynamic_table_index, nullptr, readable ? &record : nullptr);
        }
        ::SetLastError(saved_error);
    }
    return selected;
}

bool open_b1_main_font_locked(State& state, const Asset& asset, OpenFn call_original, void* self,
                              const char* original_path, int a3, int a4, int a5) noexcept {
    constexpr std::uint8_t slot_bit = 1U;
    const auto open_original = [&](std::string_view reason, DWORD error) noexcept {
        use_original_files(state);
        state.opened_font_slots.fetch_and(static_cast<std::uint8_t>(~slot_bit), std::memory_order_release);
        static_cast<void>(set_b1_font_descriptor_height(state, b1_font_descriptor_original_height));
        log_original_fallback(state, asset, reason, error);
        return call_open_noexcept(call_original, self, original_path, a3, a4, a5);
    };
    if (!physical_file_available(asset)) {
        return open_original("Replacement font not found", ::GetLastError());
    }
    B1FontDescriptorValues values{};
    if (!read_b1_font_descriptor(state, values)) {
        return open_original("Could not read font dimensions", ERROR_INVALID_DATA);
    }
    const auto descriptor_plan = b1_font_descriptor_plan(state.spec->segment, b1_main_font_key, values.width,
                                                         values.height, values.glyph_count);
    if (descriptor_plan == B1FontDescriptorPlan::not_applicable || descriptor_plan == B1FontDescriptorPlan::reject) {
        return open_original("Font dimensions differ from expected values", ERROR_INVALID_DATA);
    }

    DWORD failure{ERROR_INVALID_DATA};
    const bool replacement_succeeded = set_b1_font_descriptor_height(state, b1_font_descriptor_replacement_height) &&
                                       call_replacement_open(state, asset, call_original, self, a3, a4, a5, failure);
    if (replacement_succeeded) {
        state.opened_font_slots.fetch_or(slot_bit, std::memory_order_release);
        log_replacement_once(state, asset);
        return true;
    }
    return open_original("Could not open replacement font", failure);
}

bool open_regular_font_locked(State& state, const Asset& asset, OpenFn call_original, void* self,
                              const char* original_path, int a3, int a4, int a5) noexcept {
    if (asset.glyph_slot >= 3) {
        return call_open_noexcept(call_original, self, original_path, a3, a4, a5);
    }
    const auto slot_bit = static_cast<std::uint8_t>(1U << asset.glyph_slot);
    if (!physical_file_available(asset)) {
        use_original_files(state);
        state.opened_font_slots.fetch_and(static_cast<std::uint8_t>(~slot_bit), std::memory_order_release);
        log_original_fallback(state, asset, "Replacement font not found", ::GetLastError());
        return call_open_noexcept(call_original, self, original_path, a3, a4, a5);
    }
    DWORD failure{};
    if (call_replacement_open(state, asset, call_original, self, a3, a4, a5, failure)) {
        state.opened_font_slots.fetch_or(slot_bit, std::memory_order_release);
        log_replacement_once(state, asset);
        return true;
    }
    use_original_files(state);
    state.opened_font_slots.fetch_and(static_cast<std::uint8_t>(~slot_bit), std::memory_order_release);
    log_original_fallback(state, asset, "Could not open replacement font", failure);
    return call_open_noexcept(call_original, self, original_path, a3, a4, a5);
}

bool open_font_request(State& state, OpenFn call_original, void* self, const char* original_path,
                       std::string_view raw_path, std::uint8_t requested_slot, int a3, int a4, int a5) noexcept {
    ExclusiveSrwGuard guard{font_open_lock};
    const auto slot_bit = static_cast<std::uint8_t>(1U << requested_slot);
    const auto open_original = [&]() noexcept {
        state.opened_font_slots.fetch_and(static_cast<std::uint8_t>(~slot_bit), std::memory_order_release);
        return call_open_noexcept(call_original, self, original_path, a3, a4, a5);
    };
    if (!state.glyph_tables_ready) {
        return open_original();
    }

    try {
        const auto clean = clean_path(raw_path);
        const auto key = make_lookup_key(clean);
        if (classify_lookup_key(key, state.spec->segment) != AssetKind::font) {
            return open_original();
        }
        const auto found = state.assets_by_key.find(key);
        if (found == state.assets_by_key.end() || found->second.kind != AssetKind::font ||
            found->second.glyph_slot != requested_slot) {
            return open_original();
        }
        return found->second.b1_main_font
                   ? open_b1_main_font_locked(state, found->second, call_original, self, original_path, a3, a4, a5)
                   : open_regular_font_locked(state, found->second, call_original, self, original_path, a3, a4, a5);
    } catch (...) {
        return open_original();
    }
}

bool hooked_open_impl(State* state, OpenFn call_original, void* self, const char* path, int a3, int a4,
                      int a5) noexcept {
    if (call_original == nullptr || state == nullptr || path == nullptr) {
        return call_open_noexcept(call_original, self, path, a3, a4, a5);
    }
    if (state->replacements_disabled.load(std::memory_order_acquire)) {
        return call_open_noexcept(call_original, self, path, a3, a4, a5);
    }
    std::size_t length{};
    while (length <= Config::max_internal_path && path[length] != '\0') {
        ++length;
    }
    if (length == 0 || length > Config::max_internal_path) {
        return call_open_noexcept(call_original, self, path, a3, a4, a5);
    }
    const std::string_view raw_path{path, length};
    if (!state->open_probe_logged.exchange(true, std::memory_order_acq_rel)) {
        try {
            log_line(*state, std::string{"Loading time: "} + state->spec->segment + " first file request=" +
                                 std::to_string(::GetTickCount64() - state->hook_enable_started_ms) + "ms");
        } catch (...) {
        }
    }
    if constexpr (Config::diagnostics) try {
        const auto key = make_lookup_key(clean_path(raw_path));
        if (key.starts_with("font/") || key.ends_with(".g1n")) {
            log_font_request_once(*state, raw_path, key);
        }
    } catch (...) {
    }
    const auto requested_font_slot = font_slot_for_request(raw_path, state->spec->segment);
    if (requested_font_slot < 3) {
        return open_font_request(*state, call_original, self, path, raw_path, requested_font_slot, a3, a4, a5);
    }

    try {
        const auto clean = clean_path(raw_path);
        const auto key = make_lookup_key(clean);
        const auto kind = classify_lookup_key(key, state->spec->segment);
        if (kind == AssetKind::none) {
            return call_open_noexcept(call_original, self, path, a3, a4, a5);
        }
        const auto found = state->assets_by_key.find(key);
        if (found == state->assets_by_key.end() || found->second.kind != kind) {
            return call_open_noexcept(call_original, self, path, a3, a4, a5);
        }
        if (kind == AssetKind::font) {
            return call_open_noexcept(call_original, self, path, a3, a4, a5);
        }
        if (Config::diagnostics && !state->replacement_candidate_probe_logged.exchange(true, std::memory_order_acq_rel)) {
            try {
                log_line(*state, "Replacement candidate found: " +
                         replacement_log_message(state->spec->segment, found->second.game_path, BRQ_EMBED_ASSETS != 0));
            } catch (...) {
            }
        }
        if (!physical_file_available(found->second)) {
            use_original_files(*state);
            log_original_fallback(*state, found->second, "Replacement file not found", ::GetLastError());
            return call_open_noexcept(call_original, self, path, a3, a4, a5);
        }
        const bool japanese_namespace = is_japanese_event_namespace(key, state->spec->segment) ||
                                        is_japanese_text_namespace(key, state->spec->segment);
        DWORD failure{};
        if (call_replacement_open(*state, found->second, call_original, self, a3, a4, a5, failure)) {
            const bool committed = state->korean_text_committed.load(std::memory_order_acquire);
            if (korean_text_committed_after_open(committed, kind, japanese_namespace, true)) {
                state->korean_text_committed.store(true, std::memory_order_release);
            }
            log_replacement_once(*state, found->second);
            log_b1_font_binding_once(*state);
            return true;
        }
        use_original_files(*state);
        log_original_fallback(*state, found->second, "Could not open replacement file", failure);
        return call_open_noexcept(call_original, self, path, a3, a4, a5);
    } catch (...) {
        return call_open_noexcept(call_original, self, path, a3, a4, a5);
    }
}

std::uint32_t hooked_file_size_impl(State* state, FileSizeFn call_original, const char* path) noexcept {
    if (call_original == nullptr || state == nullptr || path == nullptr) {
        return call_file_size_noexcept(call_original, path);
    }
    if (state->replacements_disabled.load(std::memory_order_acquire)) {
        return call_file_size_noexcept(call_original, path);
    }
    std::size_t length{};
    while (length <= Config::max_internal_path && path[length] != '\0') {
        ++length;
    }
    if (length == 0 || length > Config::max_internal_path) {
        return call_file_size_noexcept(call_original, path);
    }
    const std::uint32_t original_size = call_file_size_noexcept(call_original, path);
    try {
        const Asset* asset = asset_for_path(*state, std::string_view{path, length}, true);
        std::uint32_t replacement_size{};
        if (asset != nullptr) {
            if (physical_file_size(*asset, replacement_size)) {
                log_file_size_once(*state, *asset, original_size, replacement_size);
                return replacement_size;
            }
            const DWORD failure = ::GetLastError();
            use_original_files(*state);
            log_original_fallback(*state, *asset, "Could not determine replacement file size", failure);
        }
    } catch (...) {
        use_original_files(*state);
    }
    return original_size;
}

bool read_original_after_failure(State& state, FileSizeFn get_original_size, ReadWholeFn read_original,
                                 const Asset& asset, const char* path, void* destination,
                                 std::uint32_t buffer_size, std::uint32_t* actual_size,
                                 std::string_view reason, DWORD failure) noexcept {
    use_original_files(state);
    *actual_size = 0;
    const std::uint32_t original_size = call_file_size_noexcept(get_original_size, path);
    if (!original_fits_buffer(original_size, buffer_size)) {
        try {
            log_line(state, std::string{reason} + " (error " +
                                std::to_string(failure == ERROR_SUCCESS ? ERROR_GEN_FAILURE : failure) +
                                "), original size exceeds the buffer; aborting read: " + asset.game_path +
                                "; original=" + std::to_string(original_size) + " bytes, buffer=" +
                                std::to_string(buffer_size) + " bytes");
        } catch (...) {
        }
        return false;
    }

    log_original_fallback(state, asset, reason, failure);
    const bool read = call_read_whole_noexcept(read_original, path, destination, original_size, actual_size);
    if (!read || *actual_size != original_size) {
        const std::uint32_t read_size = *actual_size;
        *actual_size = 0;
        try {
            log_line(state, "Could not fully read the original file either: " + asset.game_path +
                                "; requested=" + std::to_string(original_size) + " bytes, read=" +
                                std::to_string(read_size) + " bytes");
        } catch (...) {
        }
        return false;
    }
    if constexpr (Config::diagnostics) try {
        log_line(state, "Original read check: " + asset.game_path + " => " + asset.game_path +
                            "; requested=" + std::to_string(original_size) + " bytes, read=" +
                            std::to_string(*actual_size) + " bytes");
    } catch (...) {
    }
    return true;
}

bool hooked_read_whole_impl(State* state, FileSizeFn get_original_size, ReadWholeFn read_original,
                            const char* path, void* destination, std::uint32_t requested_size,
                            std::uint32_t* actual_size) noexcept {
    if (actual_size != nullptr) {
        *actual_size = 0;
    }
    if (read_original == nullptr || path == nullptr || destination == nullptr || actual_size == nullptr ||
        requested_size == 0) {
        return false;
    }
    if (state == nullptr) {
        return call_read_whole_noexcept(read_original, path, destination, requested_size, actual_size);
    }
    if (state->replacements_disabled.load(std::memory_order_acquire)) {
        const std::uint32_t original_size = call_file_size_noexcept(get_original_size, path);
        if (!original_fits_buffer(original_size, requested_size)) {
            return false;
        }
        const bool read = call_read_whole_noexcept(read_original, path, destination, original_size, actual_size);
        if (!read || *actual_size != original_size) {
            *actual_size = 0;
            return false;
        }
        return true;
    }

    std::size_t length{};
    while (length <= Config::max_internal_path && path[length] != '\0') {
        ++length;
    }
    if (length == 0 || length > Config::max_internal_path) {
        return call_read_whole_noexcept(read_original, path, destination, requested_size, actual_size);
    }

    const Asset* asset{};
    try {
        asset = asset_for_path(*state, std::string_view{path, length}, false);
        if (asset == nullptr || asset->kind == AssetKind::font) {
            return call_read_whole_noexcept(read_original, path, destination, requested_size, actual_size);
        }

        DWORD failure{};
        if (!read_physical_file(*asset, destination, requested_size, failure)) {
            return read_original_after_failure(*state, get_original_size, read_original, *asset, path,
                                               destination, requested_size, actual_size,
                                               "Could not fully read replacement file", failure);
        }

        *actual_size = requested_size;
        const auto key = make_lookup_key(asset->game_path);
        const bool japanese_namespace = is_japanese_event_namespace(key, state->spec->segment) ||
                                        is_japanese_text_namespace(key, state->spec->segment);
        if (korean_text_committed_after_open(state->korean_text_committed.load(std::memory_order_acquire),
                                             asset->kind, japanese_namespace, true)) {
            state->korean_text_committed.store(true, std::memory_order_release);
        }
        log_replacement_once(*state, *asset);
        log_b1_font_binding_once(*state);
        return true;
    } catch (...) {
        if (asset != nullptr) {
            return read_original_after_failure(*state, get_original_size, read_original, *asset, path,
                                               destination, requested_size, actual_size,
                                               "An error occurred while processing replacement file", ERROR_UNHANDLED_EXCEPTION);
        }
        use_original_files(*state);
        *actual_size = 0;
        const std::uint32_t original_size = call_file_size_noexcept(get_original_size, path);
        if (!original_fits_buffer(original_size, requested_size)) {
            return false;
        }
        const bool read = call_read_whole_noexcept(read_original, path, destination, original_size, actual_size);
        if (!read || *actual_size != original_size) {
            *actual_size = 0;
            return false;
        }
        return true;
    }
}

template <std::size_t Index>
bool hooked_open_for(void* self, const char* path, int a3, int a4, int a5) noexcept {
    ActiveDetour active{hook_slots[Index]};
    if (!active) {
        return false;
    }
    auto& slot = hook_slots[Index];
    State* const state = slot.state.load(std::memory_order_acquire);
    return hooked_open_impl(state, slot.original_open, self, path, a3, a4, a5);
}

template <std::size_t Index>
std::uint32_t hooked_file_size_for(const char* path) noexcept {
    ActiveDetour active{hook_slots[Index]};
    if (!active) {
        return 0;
    }
    auto& slot = hook_slots[Index];
    State* const state = slot.state.load(std::memory_order_acquire);
    return hooked_file_size_impl(state, slot.original_file_size, path);
}

template <std::size_t Index>
bool hooked_read_whole_for(const char* path, void* destination, std::uint32_t requested_size,
                           std::uint32_t* actual_size) noexcept {
    ActiveDetour active{hook_slots[Index]};
    if (!active) {
        if (actual_size != nullptr) {
            *actual_size = 0;
        }
        return false;
    }
    auto& slot = hook_slots[Index];
    State* const state = slot.state.load(std::memory_order_acquire);
    return hooked_read_whole_impl(state, slot.original_file_size, slot.original_read_whole, path, destination,
                                  requested_size, actual_size);
}

template <std::size_t Index>
const void* hooked_glyph_lookup48_for(std::uint32_t key, int slot_index, int dynamic_table_index) noexcept {
    ActiveDetour active{hook_slots[Index]};
    if (!active) {
        return nullptr;
    }
    auto& slot = hook_slots[Index];
    State* const state = slot.state.load(std::memory_order_acquire);
    return hooked_glyph_lookup48_impl(state, slot.original_glyph_lookup48, key, slot_index, dynamic_table_index);
}

template <std::size_t Index>
const void* hooked_glyph_lookup28_for(void* context, std::uint32_t key, int source_mode,
                                      int dynamic_table_index) noexcept {
    ActiveDetour active{hook_slots[Index]};
    if (!active) {
        return nullptr;
    }
    auto& slot = hook_slots[Index];
    State* const state = slot.state.load(std::memory_order_acquire);
    return hooked_glyph_lookup28_impl(state, slot.original_glyph_lookup28, context, key, source_mode,
                                      dynamic_table_index);
}

template <std::size_t Index, std::size_t Kind>
BRQ_NOINLINE std::uintptr_t hooked_runtime_text(void* object, const char* text) noexcept {
    auto& slot = hook_slots[Index];
    ActiveDetour active{slot};
    const auto original = slot.original_runtime_text[Kind];
    if (!active || original == nullptr) return 0;
    const DWORD incoming_error = ::GetLastError();
    std::string translated;
    try {
        State* state = slot.state.load(std::memory_order_acquire);
        if (state != nullptr && state->glyph_tables_ready &&
            !state->replacements_disabled.load(std::memory_order_acquire) && text != nullptr) {
            std::array<char, 3> prefix{};
            bool candidate = read_font_field(reinterpret_cast<std::uintptr_t>(text), 0, prefix);
            candidate = candidate && std::any_of(runtime_texts.begin(), runtime_texts.end(), [&](const auto& row) {
                return row.segment == state->spec->segment && row.source.starts_with(std::string_view{prefix.data(), prefix.size()});
            });
            if (candidate) {
                std::string source;
                for (std::size_t i = 0; i < 512; ++i) {
                    char value{};
                    if (!read_font_field(reinterpret_cast<std::uintptr_t>(text), i, value)) break;
                    if (value == 0) {
                        translated = translate_runtime_text(source, state->spec->segment, runtime_texts);
                        break;
                    }
                    source += value;
                }
                if (!translated.empty()) {
                    state->korean_text_committed.store(true, std::memory_order_release);
                    if (!state->runtime_display_logged[Kind].exchange(true, std::memory_order_acq_rel)) {
                        log_line(*state, std::string{"DLL display translated: "} + state->spec->segment +
                                        (Kind == 0 ? " message" : " label"));
                    }
                }
            }
        }
    } catch (...) {
        translated.clear();
    }
    ::SetLastError(incoming_error);
    return original(object, translated.empty() ? text : translated.c_str());
}

inline constexpr std::array<std::array<RuntimeTextFn, 2>, 5> runtime_text_detours{{
    {&hooked_runtime_text<0, 0>, &hooked_runtime_text<0, 1>},
    {&hooked_runtime_text<1, 0>, &hooked_runtime_text<1, 1>},
    {&hooked_runtime_text<2, 0>, &hooked_runtime_text<2, 1>},
    {&hooked_runtime_text<3, 0>, &hooked_runtime_text<3, 1>},
    {&hooked_runtime_text<4, 0>, &hooked_runtime_text<4, 1>},
}};

BRQ_NOINLINE const char* hooked_b1_string_lookup(void* table, std::int32_t id) noexcept {
    auto& slot = hook_slots[1];
    ActiveDetour active{slot};
    return active ? hooked_b1_string_lookup_impl(slot.state.load(std::memory_order_acquire),
                     slot.original_b1_string_lookup, table, id, BRQ_RETURN_ADDRESS()) : nullptr;
}

BRQ_NOINLINE void hooked_b1_text_set(void* object, const char* text, std::uint8_t flag) noexcept {
    auto& slot = hook_slots[1];
    ActiveDetour active{slot};
    if (active) {
        hooked_b1_text_set_impl(slot.state.load(std::memory_order_acquire), slot.original_b1_text_set,
                                object, text, flag, BRQ_RETURN_ADDRESS());
    }
}

BRQ_NOINLINE bool hooked_b1_menu_glyph(void* object, std::uint16_t code, int font) noexcept {
    auto& slot = hook_slots[1];
    ActiveDetour active{slot};
    return active && hooked_b1_menu_glyph_impl(slot.state.load(std::memory_order_acquire),
                                               slot.original_b1_menu_glyph, object, code, font);
}

inline constexpr std::array<OpenFn, module_specs.size()> open_detours{
    &hooked_open_for<0>, &hooked_open_for<1>, &hooked_open_for<2>, &hooked_open_for<3>, &hooked_open_for<4>,
};
inline constexpr std::array<FileSizeFn, module_specs.size()> file_size_detours{
    &hooked_file_size_for<0>, &hooked_file_size_for<1>, &hooked_file_size_for<2>, &hooked_file_size_for<3>,
    &hooked_file_size_for<4>,
};
inline constexpr std::array<ReadWholeFn, module_specs.size()> read_whole_detours{
    &hooked_read_whole_for<0>, &hooked_read_whole_for<1>, &hooked_read_whole_for<2>,
    &hooked_read_whole_for<3>, &hooked_read_whole_for<4>,
};
inline constexpr std::array<GlyphLookup48Fn, module_specs.size()> glyph48_detours{
    &hooked_glyph_lookup48_for<0>, &hooked_glyph_lookup48_for<1>, &hooked_glyph_lookup48_for<2>,
    &hooked_glyph_lookup48_for<3>, &hooked_glyph_lookup48_for<4>,
};
inline constexpr std::array<GlyphLookup28Fn, module_specs.size()> glyph28_detours{
    &hooked_glyph_lookup28_for<0>, &hooked_glyph_lookup28_for<1>, &hooked_glyph_lookup28_for<2>,
    &hooked_glyph_lookup28_for<3>, &hooked_glyph_lookup28_for<4>,
};

std::uint32_t b0_index_bucket_runtime(const char* path) noexcept {
    return b0_index_bucket_c_string(path);
}

void prepare_index_patch(std::size_t index, int pack_slot) noexcept;
void reject_pending_index_patch(std::size_t index) noexcept;

void wait_for_index_patch(std::size_t index) noexcept {
    while (pack_indexes[index].patch_state.load(std::memory_order_acquire) == IndexPatchState::installing) {
        ::Sleep(0);
    }
}

template <std::size_t index>
bool hooked_pack_load(int pack_slot, const char* path) noexcept {
    ActiveDetour active{hook_slots[index]};
    if (!active) {
        return false;
    }
    const auto call_original = pack_indexes[index].original_load;
    if (call_original == nullptr) {
        return false;
    }
    prepare_index_patch(index, pack_slot);
    wait_for_index_patch(index);
    if (pack_indexes[index].patch_state.load(std::memory_order_acquire) == IndexPatchState::failed) {
        return false;
    }
    PackTimingScope timing{index, pack_index_specs[index].pack_load_rva, "PAK load", pack_slot, true};
    return game_call_noexcept([&] { return call_original(pack_slot, path); }, []() noexcept { return false; });
}

template <std::size_t index>
void hooked_pack_index(void* self) noexcept {
    ActiveDetour active{hook_slots[index]};
    if (!active) {
        return;
    }
    const auto call_original = pack_indexes[index].original_index;
    if (call_original == nullptr) {
        return;
    }
    reject_pending_index_patch(index);
    wait_for_index_patch(index);
    if (pack_indexes[index].patch_state.load(std::memory_order_acquire) == IndexPatchState::failed) {
        return;
    }
    const int pack_slot = active_pack_slots[index];
    PackTimingScope timing{index, pack_index_specs[index].pack_index_rva, "PAK index build", pack_slot, false};
    game_call_noexcept([&] { call_original(self); }, []() noexcept {});
}

template <std::size_t index>
void* hooked_pack_lookup(void* self, const char* path) noexcept {
    ActiveDetour active{hook_slots[index]};
    if (!active) {
        return nullptr;
    }
    const auto call_original = pack_indexes[index].original_lookup;
    if (call_original == nullptr) {
        return nullptr;
    }
    reject_pending_index_patch(index);
    wait_for_index_patch(index);
    if (pack_indexes[index].patch_state.load(std::memory_order_acquire) == IndexPatchState::failed) {
        return nullptr;
    }
    return game_call_noexcept([&] { return call_original(self, path); }, []() noexcept -> void* { return nullptr; });
}

inline constexpr std::array<PackLoadFn, module_specs.size()> pack_load_detours{
    &hooked_pack_load<0>, &hooked_pack_load<1>, &hooked_pack_load<2>, &hooked_pack_load<3>, &hooked_pack_load<4>,
};
inline constexpr std::array<PackIndexFn, module_specs.size()> pack_index_detours{
    &hooked_pack_index<0>, &hooked_pack_index<1>, &hooked_pack_index<2>, &hooked_pack_index<3>, &hooked_pack_index<4>,
};
inline constexpr std::array<PackLookupFn, module_specs.size()> pack_lookup_detours{
    &hooked_pack_lookup<0>, &hooked_pack_lookup<1>, &hooked_pack_lookup<2>, &hooked_pack_lookup<3>,
    &hooked_pack_lookup<4>,
};

void log_bridge_file_result(const BridgeMatch& match, HANDLE result, std::string_view api) noexcept {
    if constexpr (!Config::diagnostics) return;
    if (match.asset == nullptr || match.state == nullptr ||
        match.state->bridge_file_probe_logged.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    try {
        std::string message = result != INVALID_HANDLE_VALUE ? "File hook opened replacement file ("
                                                               : "File hook failed to open replacement file (";
        message += api;
        message += "): ";
        message += replacement_log_message(match.state->spec->segment, match.asset->game_path,
                                           BRQ_EMBED_ASSETS != 0);
        log_line(*match.state, message);
    } catch (...) {
    }
}

template <typename Character>
std::size_t transition_module_index(const Character* path) noexcept {
    if (path == nullptr) {
        return module_specs.size();
    }
    std::size_t length{};
    std::size_t basename{};
    while (length < 32767 && path[length] != 0) {
        if (path[length] == static_cast<Character>('\\') || path[length] == static_cast<Character>('/')) {
            basename = length + 1;
        }
        ++length;
    }
    if (length == 0 || length == 32767 || length - basename != 6) {
        return module_specs.size();
    }
    const Character* const name = path + basename;
    const bool is_b = name[0] == static_cast<Character>('B') || name[0] == static_cast<Character>('b');
    const bool is_dll = (name[2] == static_cast<Character>('.')) &&
                        (name[3] == static_cast<Character>('d') || name[3] == static_cast<Character>('D')) &&
                        (name[4] == static_cast<Character>('l') || name[4] == static_cast<Character>('L')) &&
                        (name[5] == static_cast<Character>('l') || name[5] == static_cast<Character>('L'));
    if (!is_b || !is_dll || name[1] < static_cast<Character>('0') || name[1] > static_cast<Character>('4')) {
        return module_specs.size();
    }
    return static_cast<std::size_t>(name[1] - static_cast<Character>('0'));
}

State* transition_logger_state() noexcept {
    for (auto& slot : hook_slots) {
        if (State* const state = slot.state.load(std::memory_order_acquire); state != nullptr) {
            return state;
        }
    }
    return nullptr;
}

State* published_state_for_module(HMODULE module, std::size_t& index) noexcept {
    if (module == nullptr) {
        return nullptr;
    }
    for (std::size_t candidate = 0; candidate < hook_slots.size(); ++candidate) {
        auto& slot = hook_slots[candidate];
        State* const state = slot.state.load(std::memory_order_acquire);
        if (state != nullptr &&
            (slot.detour_gate.load(std::memory_order_acquire) & ActiveDetour::retiring) == 0 &&
            state->game_module == module) {
            index = candidate;
            return state;
        }
    }
    return nullptr;
}

struct CallerInfo final {
    void* address{};
    void* module_base{};
    std::uintptr_t rva{};
    DWORD thread_id{};
    bool main_exe{};
};

CallerInfo inspect_caller(void* address) noexcept {
    CallerInfo caller{};
    caller.address = address;
    caller.thread_id = ::GetCurrentThreadId();
    MEMORY_BASIC_INFORMATION information{};
    if (address != nullptr && ::VirtualQuery(address, &information, sizeof(information)) != 0) {
        caller.module_base = information.AllocationBase;
        const auto address_value = reinterpret_cast<std::uintptr_t>(address);
        const auto base_value = reinterpret_cast<std::uintptr_t>(caller.module_base);
        if (address_value >= base_value) {
            caller.rva = address_value - base_value;
        }
        caller.main_exe = caller.module_base == reinterpret_cast<void*>(::GetModuleHandleW(nullptr));
    }
    return caller;
}

void log_load_library_result(State& state, std::size_t index, HMODULE result, DWORD error,
                             ULONGLONG elapsed_ms, void* caller_address) noexcept {
    std::array<char, 384> message{};
    if (!Config::diagnostics && result != nullptr) {
        const int written = std::snprintf(message.data(), message.size(), "Loading time: %s DLL load=%llums",
            module_specs[index].segment, static_cast<unsigned long long>(elapsed_ms));
        if (written > 0 && static_cast<std::size_t>(written) < message.size()) {
            try_log_transition_line(state, {message.data(), static_cast<std::size_t>(written)});
        }
        return;
    }
    const HMODULE remaining_b0 = ::GetModuleHandleW(module_specs[0].file_name);
    const CallerInfo caller = inspect_caller(caller_address);
    const int written = result != nullptr
                            ? std::snprintf(message.data(), message.size(),
                                            "DLL load: %s.dll, result=%p, remaining B0.dll=%p, "
                                            "elapsed=%llums, return_address=%p, return_base=%p, "
                                            "return_rva=0x%llx, main_exe=%d, thread_id=%lu",
                                            module_specs[index].segment, reinterpret_cast<void*>(result),
                                            reinterpret_cast<void*>(remaining_b0),
                                            static_cast<unsigned long long>(elapsed_ms), caller.address,
                                            caller.module_base, static_cast<unsigned long long>(caller.rva),
                                            caller.main_exe ? 1 : 0, static_cast<unsigned long>(caller.thread_id))
                            : std::snprintf(message.data(), message.size(),
                                            "DLL load failed: %s.dll, result=%p, error=%lu, "
                                            "remaining B0.dll=%p, elapsed=%llums, return_address=%p, "
                                            "return_base=%p, return_rva=0x%llx, main_exe=%d, thread_id=%lu",
                                            module_specs[index].segment, reinterpret_cast<void*>(result),
                                            static_cast<unsigned long>(error), reinterpret_cast<void*>(remaining_b0),
                                            static_cast<unsigned long long>(elapsed_ms), caller.address,
                                            caller.module_base, static_cast<unsigned long long>(caller.rva),
                                            caller.main_exe ? 1 : 0, static_cast<unsigned long>(caller.thread_id));
    if (written > 0 && static_cast<std::size_t>(written) < message.size()) {
        try_log_transition_line(state, std::string_view{message.data(), static_cast<std::size_t>(written)});
    }
}

template <typename Function, typename Character>
HMODULE hooked_load_library(Function call_original, const Character* path, void* caller_address) noexcept {
    if (call_original == nullptr) {
        ::SetLastError(ERROR_PROC_NOT_FOUND);
        return nullptr;
    }
    const DWORD incoming_error = ::GetLastError();
    if (!transition_lifecycle_ready.load(std::memory_order_acquire)) {
        ::SetLastError(incoming_error);
        const HMODULE result = call_original(path);
        const DWORD call_error = ::GetLastError();
        ::SetLastError(call_error);
        return result;
    }
    const std::size_t index = transition_module_index(path);
    State* const original_logger = index < module_specs.size() ? transition_logger_state() : nullptr;
    const CallerInfo caller = index < module_specs.size() ? inspect_caller(caller_address) : CallerInfo{};
    if (caller.main_exe && pending_module_release.state != nullptr &&
        pending_module_release.index != index &&
        retire_module_release(*pending_module_release.state, pending_module_release.index)) {
        pending_module_release = {};
    }
    const ULONGLONG started = index < module_specs.size() ? ::GetTickCount64() : 0;
    ::SetLastError(incoming_error);
    const HMODULE result = call_original(path);
    const DWORD call_error = ::GetLastError();
    if (index < module_specs.size()) {
        if (caller.main_exe && result != nullptr && pending_module_release.state != nullptr &&
            pending_module_release.index == index) {
            pending_module_release = {};
        }
        if (result != nullptr && ::GetModuleHandleW(module_specs[index].file_name) == result) {
            start_module_worker(index);
            // The main EXE starts reading PAKs immediately after LoadLibrary returns.
            // Wait outside the original loader call so asset preparation cannot lose PAK[0].
            // Never wait on DLL-originated loads, which may still hold the loader lock.
            if (caller.main_exe && pack_index_specs[index].pack_load_rva != 0) {
                const ULONGLONG wait_started = ::GetTickCount64();
                while (hook_slots[index].worker_running.load(std::memory_order_acquire)) {
                    if (::GetTickCount64() - wait_started >= 5'000) {
                        if (original_logger != nullptr) {
                            try_log_transition_line(*original_logger,
                                "Timed out waiting for loading hooks; original calculation may remain active.");
                        }
                        break;
                    }
                    ::Sleep(1);
                }
            }
        }
        State* const state = transition_logger_state();
        if (state != nullptr || original_logger != nullptr) {
            log_load_library_result(*(state != nullptr ? state : original_logger), index, result, call_error,
                                    ::GetTickCount64() - started,
                                    caller_address);
        }
    }
    ::SetLastError(call_error);
    return result;
}

BRQ_NOINLINE HMODULE WINAPI hooked_load_library_a(LPCSTR path) noexcept {
    return hooked_load_library(original_load_library_a, path, BRQ_RETURN_ADDRESS());
}

BRQ_NOINLINE HMODULE WINAPI hooked_load_library_w(LPCWSTR path) noexcept {
    return hooked_load_library(original_load_library_w, path, BRQ_RETURN_ADDRESS());
}

BOOL hooked_free_library_impl(HMODULE module, void* caller_address) noexcept {
    const auto call_original = original_free_library;
    if (call_original == nullptr) {
        ::SetLastError(ERROR_PROC_NOT_FOUND);
        return FALSE;
    }
    const DWORD incoming_error = ::GetLastError();
    if (!transition_lifecycle_ready.load(std::memory_order_acquire)) {
        ::SetLastError(incoming_error);
        const BOOL result = call_original(module);
        const DWORD call_error = ::GetLastError();
        ::SetLastError(call_error);
        return result;
    }
    std::size_t index = module_specs.size();
    State* const state = published_state_for_module(module, index);
    ::SetLastError(incoming_error);
    const BOOL result = call_original(module);
    const DWORD call_error = ::GetLastError();
    if (state != nullptr) {
        const HMODULE remaining = ::GetModuleHandleW(module_specs[index].file_name);
        const CallerInfo caller = inspect_caller(caller_address);
        if (result != FALSE && caller.main_exe) {
            pending_module_release = {state, index};
        }
        if (!Config::diagnostics && result != FALSE) {
            ::SetLastError(call_error);
            return result;
        }
        std::array<char, 320> message{};
        const int written = result != FALSE
                                ? std::snprintf(message.data(), message.size(),
                                                "DLL unload: %s.dll, original result=1, address after unload=%p, "
                                                "return_address=%p, return_base=%p, return_rva=0x%llx, "
                                                "main_exe=%d, thread_id=%lu",
                                                module_specs[index].segment, reinterpret_cast<void*>(remaining),
                                                caller.address, caller.module_base,
                                                static_cast<unsigned long long>(caller.rva),
                                                caller.main_exe ? 1 : 0,
                                                static_cast<unsigned long>(caller.thread_id))
                                : std::snprintf(message.data(), message.size(),
                                                "DLL unload failed: %s.dll, original result=0, error=%lu, "
                                                "address after unload=%p, return_address=%p, return_base=%p, "
                                                "return_rva=0x%llx, main_exe=%d, thread_id=%lu",
                                                module_specs[index].segment, static_cast<unsigned long>(call_error),
                                                reinterpret_cast<void*>(remaining), caller.address,
                                                caller.module_base, static_cast<unsigned long long>(caller.rva),
                                                caller.main_exe ? 1 : 0,
                                                static_cast<unsigned long>(caller.thread_id));
        if (written > 0 && static_cast<std::size_t>(written) < message.size()) {
            try_log_transition_line(*state,
                                    std::string_view{message.data(), static_cast<std::size_t>(written)});
        }
    }
    ::SetLastError(call_error);
    return result;
}

BRQ_NOINLINE BOOL WINAPI hooked_free_library(HMODULE module) noexcept {
    return hooked_free_library_impl(module, BRQ_RETURN_ADDRESS());
}

#undef BRQ_RETURN_ADDRESS
#undef BRQ_NOINLINE

HANDLE WINAPI hooked_create_file_a(LPCSTR path, DWORD desired_access, DWORD share_mode,
                                    LPSECURITY_ATTRIBUTES security_attributes, DWORD creation_disposition,
                                    DWORD flags_and_attributes, HANDLE template_file) noexcept {
    const auto call_original = original_create_file_a;
    if (call_original == nullptr) {
        return INVALID_HANDLE_VALUE;
    }
    BridgeMatch match{};
    try {
        match = bridge_asset_any_a(path);
    } catch (...) {
        return game_call_noexcept(
            [&] {
                return call_original(path, desired_access, share_mode, security_attributes, creation_disposition,
                                     flags_and_attributes, template_file);
            },
            []() noexcept { return INVALID_HANDLE_VALUE; });
    }
    if (bridge_match_active(match) && original_create_file_w != nullptr) {
        const HANDLE result = game_call_noexcept(
            [&] {
                return original_create_file_w(match.asset->physical_path.c_str(), desired_access, share_mode,
                                              security_attributes, creation_disposition, flags_and_attributes,
                                              template_file);
            },
            []() noexcept { return INVALID_HANDLE_VALUE; });
        log_bridge_file_result(match, result, "CreateFileA");
        return result;
    }
    return game_call_noexcept(
        [&] {
            return call_original(path, desired_access, share_mode, security_attributes, creation_disposition,
                                 flags_and_attributes, template_file);
        },
        []() noexcept { return INVALID_HANDLE_VALUE; });
}

HANDLE WINAPI hooked_create_file_w(LPCWSTR path, DWORD desired_access, DWORD share_mode,
                                    LPSECURITY_ATTRIBUTES security_attributes, DWORD creation_disposition,
                                    DWORD flags_and_attributes, HANDLE template_file) noexcept {
    const auto call_original = original_create_file_w;
    if (call_original == nullptr) {
        return INVALID_HANDLE_VALUE;
    }
    BridgeMatch match{};
    try {
        match = bridge_asset_any(path);
    } catch (...) {
        return game_call_noexcept(
            [&] {
                return call_original(path, desired_access, share_mode, security_attributes, creation_disposition,
                                     flags_and_attributes, template_file);
            },
            []() noexcept { return INVALID_HANDLE_VALUE; });
    }
    if (bridge_match_active(match)) {
        const HANDLE result = game_call_noexcept(
            [&] {
                return call_original(match.asset->physical_path.c_str(), desired_access, share_mode,
                                     security_attributes, creation_disposition, flags_and_attributes, template_file);
            },
            []() noexcept { return INVALID_HANDLE_VALUE; });
        log_bridge_file_result(match, result, "CreateFileW");
        return result;
    }
    return game_call_noexcept(
        [&] {
            return call_original(path, desired_access, share_mode, security_attributes, creation_disposition,
                                 flags_and_attributes, template_file);
        },
        []() noexcept { return INVALID_HANDLE_VALUE; });
}

DWORD WINAPI hooked_get_file_attributes_w(LPCWSTR path) noexcept {
    const auto call_original = original_get_file_attributes_w;
    if (call_original == nullptr) {
        return INVALID_FILE_ATTRIBUTES;
    }
    BridgeMatch match{};
    try {
        match = bridge_asset_any(path);
    } catch (...) {
        return game_call_noexcept([&] { return call_original(path); },
                                  []() noexcept { return INVALID_FILE_ATTRIBUTES; });
    }
    if (bridge_match_active(match)) {
        return game_call_noexcept([&] { return call_original(match.asset->physical_path.c_str()); },
                                  []() noexcept { return INVALID_FILE_ATTRIBUTES; });
    }
    return game_call_noexcept([&] { return call_original(path); },
                              []() noexcept { return INVALID_FILE_ATTRIBUTES; });
}

BOOL WINAPI hooked_get_file_attributes_ex_w(LPCWSTR path, GET_FILEEX_INFO_LEVELS level, LPVOID information) noexcept {
    const auto call_original = original_get_file_attributes_ex_w;
    if (call_original == nullptr) {
        return FALSE;
    }
    BridgeMatch match{};
    try {
        match = bridge_asset_any(path);
    } catch (...) {
        return game_call_noexcept([&] { return call_original(path, level, information); },
                                  []() noexcept { return FALSE; });
    }
    if (bridge_match_active(match)) {
        return game_call_noexcept(
            [&] { return call_original(match.asset->physical_path.c_str(), level, information); },
            []() noexcept { return FALSE; });
    }
    return game_call_noexcept([&] { return call_original(path, level, information); },
                              []() noexcept { return FALSE; });
}

bool ensure_minhook_initialized() noexcept {
    const auto status = ::MH_Initialize();
    return status == MH_OK || status == MH_ERROR_ALREADY_INITIALIZED;
}

template <std::size_t Size>
std::size_t remove_created_hooks(std::array<LPVOID, Size>& targets, std::size_t count) {
    std::size_t remaining{};
    for (std::size_t index = 0; index < count; ++index) {
        if (targets[index] != nullptr) {
            ::MH_DisableHook(targets[index]);
            const MH_STATUS status = ::MH_RemoveHook(targets[index]);
            if (status != MH_OK && status != MH_ERROR_NOT_CREATED) {
                targets[remaining++] = targets[index];
            }
        }
    }
    std::fill(targets.begin() + static_cast<std::ptrdiff_t>(remaining), targets.end(), nullptr);
    return remaining;
}

template <std::size_t Size>
void disable_queued_hooks(const std::array<LPVOID, Size>& targets, std::size_t count) noexcept {
    for (std::size_t index = 0; index < count; ++index) {
        static_cast<void>(::MH_QueueDisableHook(targets[index]));
    }
    static_cast<void>(::MH_ApplyQueued());
}

bool install_api_hooks() {
    ExclusiveSrwGuard guard{hook_install_lock};
    if (!ensure_minhook_initialized()) {
        return false;
    }
    std::array<LPVOID, 4> targets{};
    std::size_t count{};

    if (::MH_CreateHookApiEx(L"kernel32.dll", "CreateFileA", reinterpret_cast<LPVOID>(&hooked_create_file_a),
                             reinterpret_cast<LPVOID*>(&original_create_file_a), &create_file_a_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = create_file_a_target;

    if (::MH_CreateHookApiEx(L"kernel32.dll", "CreateFileW", reinterpret_cast<LPVOID>(&hooked_create_file_w),
                             reinterpret_cast<LPVOID*>(&original_create_file_w), &create_file_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = create_file_target;

    if (::MH_CreateHookApiEx(L"kernel32.dll", "GetFileAttributesW",
                             reinterpret_cast<LPVOID>(&hooked_get_file_attributes_w),
                             reinterpret_cast<LPVOID*>(&original_get_file_attributes_w), &get_attributes_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = get_attributes_target;

    if (::MH_CreateHookApiEx(L"kernel32.dll", "GetFileAttributesExW",
                             reinterpret_cast<LPVOID>(&hooked_get_file_attributes_ex_w),
                             reinterpret_cast<LPVOID*>(&original_get_file_attributes_ex_w),
                             &get_attributes_ex_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = get_attributes_ex_target;

    for (std::size_t index = 0; index < count; ++index) {
        if (::MH_QueueEnableHook(targets[index]) != MH_OK) {
            remove_created_hooks(targets, count);
            return false;
        }
    }
    if (::MH_ApplyQueued() != MH_OK) {
        // Keep the trampolines alive: another thread may already be inside a partially enabled API detour.
        disable_queued_hooks(targets, count);
        return false;
    }
    return true;
}

bool install_transition_hooks() {
    ExclusiveSrwGuard guard{hook_install_lock};
    if (!ensure_minhook_initialized()) {
        return false;
    }
    std::array<LPVOID, 3> targets{};
    std::size_t count{};

    if (::MH_CreateHookApiEx(L"kernel32.dll", "LoadLibraryA", reinterpret_cast<LPVOID>(&hooked_load_library_a),
                             reinterpret_cast<LPVOID*>(&original_load_library_a), &load_library_a_target) != MH_OK) {
        return false;
    }
    targets[count++] = load_library_a_target;

    if (::MH_CreateHookApiEx(L"kernel32.dll", "LoadLibraryW", reinterpret_cast<LPVOID>(&hooked_load_library_w),
                             reinterpret_cast<LPVOID*>(&original_load_library_w), &load_library_w_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = load_library_w_target;

    if (::MH_CreateHookApiEx(L"kernel32.dll", "FreeLibrary", reinterpret_cast<LPVOID>(&hooked_free_library),
                             reinterpret_cast<LPVOID*>(&original_free_library), &free_library_target) != MH_OK) {
        remove_created_hooks(targets, count);
        return false;
    }
    targets[count++] = free_library_target;

    for (std::size_t index = 0; index < count; ++index) {
        if (::MH_QueueEnableHook(targets[index]) != MH_OK) {
            remove_created_hooks(targets, count);
            return false;
        }
    }
    if (::MH_ApplyQueued() != MH_OK) {
        disable_queued_hooks(targets, count);
        return false;
    }
    transition_lifecycle_ready.store(true, std::memory_order_release);
    return true;
}

bool install_segment_hooks(State& state, std::size_t index, LPVOID target_open, LPVOID target_file_size,
                           LPVOID target_read_whole, LPVOID target_glyph_lookup,
                           LPVOID target_b1_lookup = nullptr, LPVOID target_b1_setter = nullptr,
                           LPVOID target_b1_menu_glyph = nullptr) {
    if (index >= hook_slots.size() || target_open == nullptr || target_file_size == nullptr ||
        ((target_b1_lookup != nullptr || target_b1_setter != nullptr || target_b1_menu_glyph != nullptr) && index != 1)) {
        return false;
    }
    ExclusiveSrwGuard guard{hook_install_lock};
    if (!ensure_minhook_initialized()) {
        return false;
    }

    auto& slot = hook_slots[index];
    if (slot.state.load(std::memory_order_acquire) != nullptr) {
        return false;
    }
    std::array<LPVOID, 7> targets{};
    std::size_t count{};
    const auto fail_install = [&]() {
        const std::size_t remaining = remove_created_hooks(targets, count);
        if (remaining != 0) {
            std::copy_n(targets.begin(), remaining, slot.created_targets.begin());
            slot.created_target_count = remaining;
            slot.enabled_target_count = 0;
            slot.detour_gate.store(0, std::memory_order_release);
            use_original_files(state);
            state.published_to_hook_slot = true;
            slot.state.store(&state, std::memory_order_release);
        }
        return false;
    };

    slot.open_target = target_open;
    if (::MH_CreateHook(slot.open_target, reinterpret_cast<LPVOID>(open_detours[index]),
                        reinterpret_cast<LPVOID*>(&slot.original_open)) != MH_OK) {
        return false;
    }
    targets[count++] = slot.open_target;

    slot.file_size_target = target_file_size;
    if (::MH_CreateHook(slot.file_size_target, reinterpret_cast<LPVOID>(file_size_detours[index]),
                        reinterpret_cast<LPVOID*>(&slot.original_file_size)) != MH_OK) {
        return fail_install();
    }
    targets[count++] = slot.file_size_target;

    if (target_read_whole != nullptr) {
        slot.read_whole_target = target_read_whole;
        if (::MH_CreateHook(slot.read_whole_target, reinterpret_cast<LPVOID>(read_whole_detours[index]),
                            reinterpret_cast<LPVOID*>(&slot.original_read_whole)) != MH_OK) {
            return fail_install();
        }
        targets[count++] = slot.read_whole_target;
    }

    if (target_glyph_lookup != nullptr) {
        const auto* lookup_spec = glyph_lookup_spec(state.spec->segment);
        if (lookup_spec == nullptr) {
            return fail_install();
        }
        slot.glyph_lookup_target = target_glyph_lookup;
        MH_STATUS status{};
        if (lookup_spec->record_size == sizeof(GlyphRecord48)) {
            status = ::MH_CreateHook(slot.glyph_lookup_target, reinterpret_cast<LPVOID>(glyph48_detours[index]),
                                     reinterpret_cast<LPVOID*>(&slot.original_glyph_lookup48));
        } else if (lookup_spec->record_size == sizeof(GlyphRecord28)) {
            status = ::MH_CreateHook(slot.glyph_lookup_target, reinterpret_cast<LPVOID>(glyph28_detours[index]),
                                     reinterpret_cast<LPVOID*>(&slot.original_glyph_lookup28));
        } else {
            return fail_install();
        }
        if (status != MH_OK) {
            return fail_install();
        }
        targets[count++] = slot.glyph_lookup_target;
    }

    if (target_b1_lookup != nullptr) {
        slot.b1_string_lookup_target = target_b1_lookup;
        if (::MH_CreateHook(target_b1_lookup, reinterpret_cast<LPVOID>(&hooked_b1_string_lookup),
                            reinterpret_cast<LPVOID*>(&slot.original_b1_string_lookup)) != MH_OK) {
            return fail_install();
        }
        targets[count++] = target_b1_lookup;
    }
    if (target_b1_setter != nullptr) {
        slot.b1_text_set_target = target_b1_setter;
        if (::MH_CreateHook(target_b1_setter, reinterpret_cast<LPVOID>(&hooked_b1_text_set),
                            reinterpret_cast<LPVOID*>(&slot.original_b1_text_set)) != MH_OK) {
            return fail_install();
        }
        targets[count++] = target_b1_setter;
    }
    if (target_b1_menu_glyph != nullptr) {
        slot.b1_menu_glyph_target = target_b1_menu_glyph;
        if (::MH_CreateHook(target_b1_menu_glyph, reinterpret_cast<LPVOID>(&hooked_b1_menu_glyph),
                            reinterpret_cast<LPVOID*>(&slot.original_b1_menu_glyph)) != MH_OK) {
            return fail_install();
        }
        targets[count++] = target_b1_menu_glyph;
    }

    for (const auto& display : runtime_text_hooks) {
        if (display.segment != state.spec->segment) continue;
        auto* base = reinterpret_cast<std::uint8_t*>(state.game_module);
        bool verified = true;
        for (std::size_t kind = 0; kind < 2; ++kind) {
            const auto& signature = display.signatures[kind];
            verified = verified && display.rvas[kind] <= state.spec->image_size &&
                signature.size() <= state.spec->image_size - display.rvas[kind] &&
                std::equal(signature.begin(), signature.end(), base + display.rvas[kind]);
        }
        if (!verified) {
            log_line(state, "DLL display signatures differ; preserving original display functions.");
            continue;
        }
        for (std::size_t kind = 0; kind < 2; ++kind) {
            LPVOID display_target = base + display.rvas[kind];
            if (count == targets.size() ||
                ::MH_CreateHook(display_target, reinterpret_cast<LPVOID>(runtime_text_detours[index][kind]),
                                reinterpret_cast<LPVOID*>(&slot.original_runtime_text[kind])) != MH_OK) {
                return fail_install();
            }
            targets[count++] = display_target;
        }
    }

    for (std::size_t target_index = 0; target_index < count; ++target_index) {
        if (::MH_QueueEnableHook(targets[target_index]) != MH_OK) {
            return fail_install();
        }
    }

    // A detour can run as soon as the queued hooks are applied, so publish the fully prepared state first.
    state.hook_enable_started_ms = ::GetTickCount64();
    std::copy_n(targets.begin(), count, slot.created_targets.begin());
    slot.created_target_count = count;
    slot.detour_gate.store(0, std::memory_order_release);
    state.published_to_hook_slot = true;
    slot.state.store(&state, std::memory_order_release);
    if (::MH_ApplyQueued() != MH_OK) {
        // Keep state and trampolines alive even if MinHook enabled only part of this group.
        use_original_files(state);
        disable_queued_hooks(targets, count);
        return false;
    }
    std::copy_n(targets.begin(), count, slot.enabled_targets.begin());
    slot.enabled_target_count = count;
    return true;
}

template <std::size_t Size>
bool pack_code_matches(const std::uint8_t* address, const std::array<std::uint8_t, Size>& expected) noexcept {
    return address != nullptr && std::memcmp(address, expected.data(), expected.size()) == 0;
}

std::array<std::uint8_t, 69> builder_hash_patch() noexcept {
    std::array<std::uint8_t, 69> patch{};
    patch.fill(0x90);
    constexpr std::array<std::uint8_t, 9> prefix{
        0x49, 0x8b, 0x75, 0x00,  // mov rsi, [r13]
        0x49, 0x8b, 0xcf,        // mov rcx, r15
        0x48, 0xb8,              // mov rax, helper
    };
    constexpr std::array<std::uint8_t, 8> suffix{
        0xff, 0xd0,        // call rax
        0x48, 0x63, 0xd8,  // movsxd rbx, eax
        0x49, 0x8b, 0xcf,  // mov rcx, r15 for the original strlen call
    };
    std::copy(prefix.begin(), prefix.end(), patch.begin());
    const auto helper = reinterpret_cast<std::uintptr_t>(&b0_index_bucket_runtime);
    std::memcpy(patch.data() + prefix.size(), &helper, sizeof(helper));
    std::copy(suffix.begin(), suffix.end(), patch.begin() + prefix.size() + sizeof(helper));
    return patch;
}

std::array<std::uint8_t, 61> lookup_hash_patch() noexcept {
    std::array<std::uint8_t, 61> patch{};
    patch.fill(0x90);
    constexpr std::array<std::uint8_t, 5> prefix{
        0x48, 0x8b, 0xcf,  // mov rcx, rdi
        0x48, 0xb8,        // mov rax, helper
    };
    constexpr std::array<std::uint8_t, 8> suffix{
        0xff, 0xd0,        // call rax
        0x48, 0x63, 0xd8,  // movsxd rbx, eax
        0x48, 0x8b, 0xcf,  // mov rcx, rdi for the original strlen call
    };
    std::copy(prefix.begin(), prefix.end(), patch.begin());
    const auto helper = reinterpret_cast<std::uintptr_t>(&b0_index_bucket_runtime);
    std::memcpy(patch.data() + prefix.size(), &helper, sizeof(helper));
    std::copy(suffix.begin(), suffix.end(), patch.begin() + prefix.size() + sizeof(helper));
    return patch;
}

bool pack_read_exact(const void* source, void* destination, std::size_t size) noexcept {
    SIZE_T read{};
    return ::ReadProcessMemory(::GetCurrentProcess(), source, destination, size, &read) != FALSE && read == size;
}

bool index_is_pristine(std::size_t index) noexcept {
    if (pack_indexes[index].module_base == nullptr || pack_index_specs[index].pack_slots_rva > module_specs[index].image_size ||
        pack_index_specs[index].pack_slot_count * sizeof(std::uintptr_t) >
            module_specs[index].image_size - pack_index_specs[index].pack_slots_rva) {
        return false;
    }
    std::array<std::uintptr_t, 9> slots{};
    if (pack_index_specs[index].pack_slot_count != slots.size()) return false;
    if (!pack_read_exact(pack_indexes[index].module_base + pack_index_specs[index].pack_slots_rva, slots.data(), sizeof(slots))) {
        return false;
    }
    for (const auto object : slots) {
        if (object == 0) {
            continue;
        }
        std::array<std::uintptr_t, 4> fields{};
        if (!pack_read_exact(reinterpret_cast<const void*>(object), fields.data(), sizeof(fields)) ||
            fields[0] != 0 || fields[2] != 0 || fields[3] != 0) {
            return false;
        }
    }
    return true;
}

bool patch_protection_is(std::size_t index, const std::uint8_t* builder, const std::uint8_t* lookup,
                            DWORD expected) noexcept {
    MEMORY_BASIC_INFORMATION builder_info{};
    MEMORY_BASIC_INFORMATION lookup_info{};
    return ::VirtualQuery(builder, &builder_info, sizeof(builder_info)) == sizeof(builder_info) &&
           ::VirtualQuery(lookup, &lookup_info, sizeof(lookup_info)) == sizeof(lookup_info) &&
           builder_info.State == MEM_COMMIT && lookup_info.State == MEM_COMMIT &&
           builder_info.AllocationBase == pack_indexes[index].module_base && lookup_info.AllocationBase == pack_indexes[index].module_base &&
           builder_info.Protect == expected && lookup_info.Protect == expected;
}

bool restore_hash_bytes(std::size_t index, std::uint8_t* builder, std::uint8_t* lookup, std::size_t span_size,
                           DWORD original_protection) noexcept {
    DWORD writable_protection{};
    if (::VirtualProtect(builder, span_size, PAGE_EXECUTE_READWRITE, &writable_protection) == FALSE) {
        return false;
    }
    std::memcpy(builder, pack_index_specs[index].builder_hash_bytes.data(), pack_index_specs[index].builder_hash_bytes.size());
    std::memcpy(lookup, pack_index_specs[index].lookup_hash_bytes.data(), pack_index_specs[index].lookup_hash_bytes.size());
    const bool flushed = ::FlushInstructionCache(::GetCurrentProcess(), builder, span_size) != FALSE;
    DWORD ignored{};
    const bool protection_restored =
        ::VirtualProtect(builder, span_size, original_protection, &ignored) != FALSE;
    return flushed && protection_restored && pack_code_matches(builder, pack_index_specs[index].builder_hash_bytes) &&
           pack_code_matches(lookup, pack_index_specs[index].lookup_hash_bytes) &&
           patch_protection_is(index, builder, lookup, original_protection);
}

enum class HashPatchResult : std::uint8_t {
    applied,
    original,
    unsafe,
};

HashPatchResult install_hash_patch(std::size_t index, std::uint8_t* base) noexcept {
    auto* const builder = base + pack_index_specs[index].builder_hash_rva;
    auto* const lookup = base + pack_index_specs[index].lookup_hash_rva;
    if (!pack_code_matches(builder, pack_index_specs[index].builder_hash_bytes) ||
        !pack_code_matches(lookup, pack_index_specs[index].lookup_hash_bytes) || !index_is_pristine(index)) {
        return HashPatchResult::original;
    }
#if BRQ_DISABLE_B0_HASH
    if (index == 0) return HashPatchResult::original;
#endif
    const auto builder_patch = builder_hash_patch();
    const auto lookup_patch = lookup_hash_patch();
    const auto span_size = static_cast<std::size_t>(lookup + lookup_patch.size() - builder);
    constexpr DWORD original_protection = PAGE_EXECUTE_READ;
    if (!patch_protection_is(index, builder, lookup, original_protection)) {
        return HashPatchResult::original;
    }
    DWORD observed_protection{};
    if (::VirtualProtect(builder, span_size, PAGE_EXECUTE_READWRITE, &observed_protection) == FALSE) {
        return HashPatchResult::original;
    }
    const auto restore_without_changes = [&]() noexcept {
        DWORD ignored{};
        return ::VirtualProtect(builder, span_size, original_protection, &ignored) != FALSE &&
               pack_code_matches(builder, pack_index_specs[index].builder_hash_bytes) &&
               pack_code_matches(lookup, pack_index_specs[index].lookup_hash_bytes) &&
               patch_protection_is(index, builder, lookup, original_protection);
    };
    if (!pack_code_matches(builder, pack_index_specs[index].builder_hash_bytes) ||
        !pack_code_matches(lookup, pack_index_specs[index].lookup_hash_bytes) ||
        observed_protection != original_protection || !index_is_pristine(index)) {
        return restore_without_changes() ? HashPatchResult::original : HashPatchResult::unsafe;
    }

    std::memcpy(builder, builder_patch.data(), builder_patch.size());
    std::memcpy(lookup, lookup_patch.data(), lookup_patch.size());
    const bool flushed = ::FlushInstructionCache(::GetCurrentProcess(), builder, span_size) != FALSE;
    DWORD ignored{};
    const bool protection_restored =
        ::VirtualProtect(builder, span_size, original_protection, &ignored) != FALSE;
    if (flushed && protection_restored && pack_code_matches(builder, builder_patch) &&
        pack_code_matches(lookup, lookup_patch) &&
        patch_protection_is(index, builder, lookup, original_protection) && index_is_pristine(index)) {
        return HashPatchResult::applied;
    }
    return restore_hash_bytes(index, builder, lookup, span_size, original_protection)
               ? HashPatchResult::original
               : HashPatchResult::unsafe;
}

void log_index_patch_result(std::size_t index, IndexPatchState result) noexcept {
    if (pack_indexes[index].result_logged.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    State* const state = hook_slots[index].state.load(std::memory_order_acquire);
    if (state == nullptr) {
        return;
    }
    try {
        if (result == IndexPatchState::enabled) {
            log_line(*state, "PAK index name calculation optimization enabled: " + std::string{state->spec->segment} + ", FNV-1a, batches of 128");
        } else if (result == IndexPatchState::failed) {
            log_line(*state, std::string{state->spec->segment} + " PAK index code restoration could not be verified; aborting read.");
        } else {
#if BRQ_DISABLE_B0_HASH
            if (index == 0) {
                log_line(*state, "B0 PAK index optimization is disabled in this build; using original calculation.");
                return;
            }
#endif
            log_line(*state,
                     "Could not safely enable optimization before building the PAK index; using original calculation.");
        }
    } catch (...) {
    }
}

void prepare_index_patch(std::size_t index, int pack_slot) noexcept {
    for (;;) {
        const auto state = pack_indexes[index].patch_state.load(std::memory_order_acquire);
        if (state == IndexPatchState::installing) {
            ::Sleep(0);
            continue;
        }
        if (state != IndexPatchState::pending) {
            return;
        }
        if (pack_slot != 0) {
            auto expected = IndexPatchState::pending;
            if (pack_indexes[index].patch_state.compare_exchange_weak(expected, IndexPatchState::disabled,
                                                           std::memory_order_acq_rel)) {
                log_index_patch_result(index, IndexPatchState::disabled);
                return;
            }
            continue;
        }

        auto expected = IndexPatchState::pending;
        if (!pack_indexes[index].patch_state.compare_exchange_weak(expected, IndexPatchState::installing,
                                                        std::memory_order_acq_rel)) {
            continue;
        }
        HashPatchResult result{HashPatchResult::original};
        {
            ExclusiveSrwGuard guard{hook_install_lock};
            if (pack_indexes[index].module_base != nullptr) {
                result = install_hash_patch(index, pack_indexes[index].module_base);
            }
        }
        const auto final_state = result == HashPatchResult::applied
                                     ? IndexPatchState::enabled
                                     : result == HashPatchResult::original ? IndexPatchState::disabled
                                                                              : IndexPatchState::failed;
        pack_indexes[index].patch_state.store(final_state, std::memory_order_release);
        log_index_patch_result(index, final_state);
        return;
    }
}

void reject_pending_index_patch(std::size_t index) noexcept {
    for (;;) {
        const auto state = pack_indexes[index].patch_state.load(std::memory_order_acquire);
        if (state == IndexPatchState::installing) {
            ::Sleep(0);
            continue;
        }
        if (state != IndexPatchState::pending) {
            return;
        }
        auto expected = IndexPatchState::pending;
        if (pack_indexes[index].patch_state.compare_exchange_weak(expected, IndexPatchState::disabled,
                                                       std::memory_order_acq_rel)) {
            log_index_patch_result(index, IndexPatchState::disabled);
            return;
        }
    }
}

struct PackHookInstallResult final {
    bool timing_installed{};
    bool index_patch_armed{};
};

PackHookInstallResult install_pack_hooks(State& state, std::size_t index) {
    if (index >= module_specs.size() || pack_index_specs[index].pack_load_rva == 0 ||
        state.spec != &module_specs[index] || state.game_module == nullptr) {
        return {};
    }
    auto* const base = reinterpret_cast<std::uint8_t*>(state.game_module);
    const auto resolve = [&](std::uintptr_t rva, const auto& signature, LPVOID& target) {
        if (rva > state.spec->image_size || signature.size() > state.spec->image_size - rva ||
            !std::equal(signature.begin(), signature.end(), base + rva)) {
            return false;
        }
        target = const_cast<std::uint8_t*>(base + rva);
        return true;
    };
    if (!resolve(pack_index_specs[index].pack_load_rva, pack_index_specs[index].pack_load_signature, pack_indexes[index].load_target) ||
        !resolve(pack_index_specs[index].pack_index_rva, pack_index_specs[index].pack_index_signature, pack_indexes[index].index_target)) {
        return {};
    }
    const bool optimization_supported =
        resolve(pack_index_specs[index].pack_lookup_rva, pack_index_specs[index].pack_lookup_signature, pack_indexes[index].lookup_target) &&
        pack_index_specs[index].builder_hash_rva <= state.spec->image_size &&
        pack_index_specs[index].builder_hash_bytes.size() <= state.spec->image_size - pack_index_specs[index].builder_hash_rva &&
        pack_index_specs[index].lookup_hash_rva <= state.spec->image_size &&
        pack_index_specs[index].lookup_hash_bytes.size() <= state.spec->image_size - pack_index_specs[index].lookup_hash_rva &&
        pack_code_matches(base + pack_index_specs[index].builder_hash_rva, pack_index_specs[index].builder_hash_bytes) &&
        pack_code_matches(base + pack_index_specs[index].lookup_hash_rva, pack_index_specs[index].lookup_hash_bytes);

    ExclusiveSrwGuard guard{hook_install_lock};
    if (!ensure_minhook_initialized()) {
        return {};
    }
    std::array<LPVOID, 3> targets{};
    std::size_t count{};
    auto& slot = hook_slots[index];
    const auto discard_unpublished_targets = [&]() {
        const std::size_t remaining = remove_created_hooks(targets, count);
        if (remaining != 0 && slot.created_target_count + remaining <= slot.created_targets.size()) {
            std::copy_n(targets.begin(), remaining, slot.created_targets.begin() + slot.created_target_count);
            slot.created_target_count += remaining;
        }
    };
    if (::MH_CreateHook(pack_indexes[index].load_target, reinterpret_cast<LPVOID>(pack_load_detours[index]),
                        reinterpret_cast<LPVOID*>(&pack_indexes[index].original_load)) != MH_OK) {
        discard_unpublished_targets();
        return {};
    }
    targets[count++] = pack_indexes[index].load_target;
    if (::MH_CreateHook(pack_indexes[index].index_target, reinterpret_cast<LPVOID>(pack_index_detours[index]),
                        reinterpret_cast<LPVOID*>(&pack_indexes[index].original_index)) != MH_OK) {
        discard_unpublished_targets();
        return {};
    }
    targets[count++] = pack_indexes[index].index_target;
    if (optimization_supported) {
        if (::MH_CreateHook(pack_indexes[index].lookup_target, reinterpret_cast<LPVOID>(pack_lookup_detours[index]),
                            reinterpret_cast<LPVOID*>(&pack_indexes[index].original_lookup)) != MH_OK) {
            discard_unpublished_targets();
            return {};
        }
        targets[count++] = pack_indexes[index].lookup_target;
    }
    for (const auto target : targets) {
        if (target == nullptr) {
            continue;
        }
        if (::MH_QueueEnableHook(target) != MH_OK) {
            discard_unpublished_targets();
            return {};
        }
    }
    if (slot.created_target_count + count > slot.created_targets.size() ||
        slot.enabled_target_count + count > slot.enabled_targets.size()) {
        discard_unpublished_targets();
        return {};
    }
    std::copy_n(targets.begin(), count, slot.created_targets.begin() + slot.created_target_count);
    slot.created_target_count += count;
    pack_indexes[index].module_base = optimization_supported ? base : nullptr;
    pack_indexes[index].result_logged.store(false, std::memory_order_release);
    pack_indexes[index].patch_state.store(optimization_supported ? IndexPatchState::installing
                                                      : IndexPatchState::disabled,
                               std::memory_order_release);
    if (::MH_ApplyQueued() != MH_OK) {
        pack_indexes[index].patch_state.store(IndexPatchState::disabled, std::memory_order_release);
        // Keep the trampolines alive: a loading thread may already be inside a partially enabled timing detour.
        disable_queued_hooks(targets, count);
        return {};
    }
    std::copy_n(targets.begin(), count, slot.enabled_targets.begin() + slot.enabled_target_count);
    slot.enabled_target_count += count;
    pack_indexes[index].patch_state.store(optimization_supported ? IndexPatchState::pending
                                                      : IndexPatchState::disabled,
                               std::memory_order_release);
    return {true, optimization_supported};
}

bool set_module_hooks_enabled(HookSlot& slot, bool enabled) noexcept {
    ExclusiveSrwGuard guard{hook_install_lock};
    const auto& targets = enabled ? slot.enabled_targets : slot.created_targets;
    const std::size_t count = enabled ? slot.enabled_target_count : slot.created_target_count;
    for (std::size_t index = 0; index < count; ++index) {
        const MH_STATUS status = enabled ? ::MH_QueueEnableHook(targets[index])
                                         : ::MH_QueueDisableHook(targets[index]);
        if (status != MH_OK) {
            return false;
        }
    }
    return ::MH_ApplyQueued() == MH_OK;
}

void log_module_retirement(State& state, std::string_view result, DWORD error = ERROR_SUCCESS) noexcept {
    std::array<char, 192> message{};
    const int written = error == ERROR_SUCCESS
                            ? std::snprintf(message.data(), message.size(), "DLL transition preparation: %s.dll, result=%.*s",
                                            state.spec->segment, static_cast<int>(result.size()), result.data())
                            : std::snprintf(message.data(), message.size(),
                                            "DLL transition preparation failed: %s.dll, result=%.*s, error=%lu",
                                            state.spec->segment, static_cast<int>(result.size()), result.data(),
                                            static_cast<unsigned long>(error));
    if (written > 0 && static_cast<std::size_t>(written) < message.size()) {
        try_log_transition_line(state, std::string_view{message.data(), static_cast<std::size_t>(written)});
    }
}

bool retire_module_release(State& state, std::size_t index) noexcept {
    if (index >= hook_slots.size()) {
        return true;
    }
    auto& slot = hook_slots[index];
    if (slot.state.load(std::memory_order_acquire) != &state) {
        return true;
    }
    constexpr ULONGLONG drain_timeout_ms = 5'000;
    ULONGLONG started = ::GetTickCount64();
    while (!state.hooks_ready.load(std::memory_order_acquire) ||
           slot.worker_running.load(std::memory_order_acquire)) {
        if (::GetTickCount64() - started >= drain_timeout_ms) {
            log_module_retirement(state, "Timed out waiting for hook preparation");
            return false;
        }
        ::Sleep(1);
    }
    const std::uint32_t previous_gate = slot.detour_gate.fetch_or(ActiveDetour::retiring,
                                                                  std::memory_order_acq_rel);
    if ((previous_gate & ActiveDetour::retiring) != 0) {
        return false;
    }
    if (!set_module_hooks_enabled(slot, false)) {
        const bool restored = set_module_hooks_enabled(slot, true);
        if (restored) {
            slot.detour_gate.fetch_and(ActiveDetour::active_mask, std::memory_order_release);
        }
        log_module_retirement(state, restored ? "Could not disable hooks"
                                              : "Could not disable or restore hooks");
        return false;
    }
    started = ::GetTickCount64();
    while ((slot.detour_gate.load(std::memory_order_acquire) & ActiveDetour::active_mask) != 0) {
        if (::GetTickCount64() - started >= drain_timeout_ms) {
            const bool restored = set_module_hooks_enabled(slot, true);
            if (restored) {
                slot.detour_gate.fetch_and(ActiveDetour::active_mask, std::memory_order_release);
            }
            log_module_retirement(state, restored ? "Timed out waiting for active hooks"
                                                  : "Could not re-enable hooks after timeout");
            return false;
        }
        ::Sleep(1);
    }

    const auto release_reference = original_free_library;
    if (release_reference == nullptr || !state.owns_game_module_reference) {
        const bool restored = set_module_hooks_enabled(slot, true);
        if (restored) {
            slot.detour_gate.fetch_and(ActiveDetour::active_mask, std::memory_order_release);
        }
        log_module_retirement(state, restored ? "No loader reference to release"
                                              : "Could not retain loader reference and existing hooks");
        return false;
    }
    const HMODULE old_module = state.game_module;
    const BOOL released = release_reference(old_module);
    const DWORD release_error = ::GetLastError();
    if (released != FALSE) {
        state.owns_game_module_reference = false;
    }
    const HMODULE remaining = ::GetModuleHandleW(state.spec->file_name);
    if (released == FALSE || remaining == old_module) {
        if (released != FALSE) {
            HMODULE retained{};
            if (::GetModuleHandleExW(0, state.spec->file_name, &retained) == FALSE || retained != old_module) {
                log_module_retirement(state, "Could not reacquire remaining module reference", ::GetLastError());
                return false;
            }
            state.owns_game_module_reference = true;
        }
        if (!set_module_hooks_enabled(slot, true)) {
            log_module_retirement(state, "Could not re-enable existing hooks");
            return false;
        }
        slot.detour_gate.fetch_and(ActiveDetour::active_mask, std::memory_order_release);
        log_module_retirement(state, released == FALSE ? "Failed to release loader reference" : "Other references remain",
                              released == FALSE ? release_error : ERROR_SUCCESS);
        return false;
    }

    // Process-wide file detours are not part of this slot's active count. Make any reader that already
    // captured this State fall back before the retired slot disappears.
    use_original_files(state);
    bool removed{true};
    {
        ExclusiveSrwGuard guard{hook_install_lock};
        for (std::size_t target_index = 0; target_index < slot.created_target_count; ++target_index) {
            const MH_STATUS status = ::MH_RemoveHook(slot.created_targets[target_index]);
            removed = (status == MH_OK || status == MH_ERROR_NOT_CREATED) && removed;
        }
        if (removed) {
            slot.original_open = nullptr;
            slot.original_file_size = nullptr;
            slot.original_read_whole = nullptr;
            slot.original_glyph_lookup48 = nullptr;
            slot.original_glyph_lookup28 = nullptr;
            slot.original_b1_string_lookup = nullptr;
            slot.original_b1_text_set = nullptr;
            slot.original_runtime_text.fill(nullptr);
            slot.original_b1_menu_glyph = nullptr;
            slot.open_target = nullptr;
            slot.file_size_target = nullptr;
            slot.read_whole_target = nullptr;
            slot.glyph_lookup_target = nullptr;
            slot.b1_string_lookup_target = nullptr;
            slot.b1_text_set_target = nullptr;
            slot.b1_menu_glyph_target = nullptr;
            slot.created_targets.fill(nullptr);
            slot.enabled_targets.fill(nullptr);
            slot.created_target_count = 0;
            slot.enabled_target_count = 0;
            if (pack_index_specs[index].pack_load_rva != 0) {
                pack_indexes[index].module_base = nullptr;
                pack_indexes[index].load_target = nullptr;
                pack_indexes[index].index_target = nullptr;
                pack_indexes[index].lookup_target = nullptr;
                pack_indexes[index].original_load = nullptr;
                pack_indexes[index].original_index = nullptr;
                pack_indexes[index].original_lookup = nullptr;
                pack_indexes[index].patch_state.store(IndexPatchState::disabled, std::memory_order_release);
                pack_indexes[index].result_logged.store(false, std::memory_order_release);
            }
            // Publish availability only after every field for the old generation has been cleared.
            slot.state.store(nullptr, std::memory_order_release);
        }
    }
    if (!removed) {
        // Keep the disabled generation quarantined. Clearing this tracking would let a later image at the
        // same address collide with a MinHook entry whose trampoline still belongs to the old generation.
        if (pack_index_specs[index].pack_load_rva != 0) {
            pack_indexes[index].module_base = nullptr;
            pack_indexes[index].load_target = nullptr;
            pack_indexes[index].index_target = nullptr;
            pack_indexes[index].lookup_target = nullptr;
            pack_indexes[index].original_load = nullptr;
            pack_indexes[index].original_index = nullptr;
            pack_indexes[index].original_lookup = nullptr;
            pack_indexes[index].patch_state.store(IndexPatchState::disabled, std::memory_order_release);
            pack_indexes[index].result_logged.store(false, std::memory_order_release);
        }
        log_module_retirement(state, "Module unloaded, but hook record removal failed");
        return true;
    }
    // ponytail: retired states remain alive because file hooks hold raw pointers.
    // This costs one State plus its asset indexes per revisit and therefore grows linearly; add shared ownership
    // if repeated transitions make that process-lifetime cost measurable.
    if constexpr (Config::diagnostics) log_module_retirement(state, "Previous module unloaded");
    return true;
}

HMODULE wait_and_retain_module(const ModuleSpec& spec) noexcept {
    for (;;) {
        HMODULE retained{};
        if (::GetModuleHandleExW(0, spec.file_name, &retained) != FALSE) {
            return retained;
        }
        ::Sleep(Config::module_poll_ms);
    }
}

void log_installed_hooks(State& state) noexcept {
    if constexpr (!Config::diagnostics) return;
    try {
        std::array<char, 192> message{};
        int size = std::snprintf(message.data(), message.size(),
                                 "File open hook: %s.dll+0x%08llx => BRQuarteTranslateHelper.asi",
                                 state.spec->segment, static_cast<unsigned long long>(state.spec->open_rva));
        if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
            log_line(state, std::string_view{message.data(), static_cast<std::size_t>(size)});
        }
        size = std::snprintf(message.data(), message.size(),
                             "File size hook: %s.dll+0x%08llx => BRQuarteTranslateHelper.asi",
                             state.spec->segment, static_cast<unsigned long long>(state.spec->file_size_rva));
        if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
            log_line(state, std::string_view{message.data(), static_cast<std::size_t>(size)});
        }
        if (const auto* read = read_whole_spec(state.spec->segment); read != nullptr) {
            size = std::snprintf(message.data(), message.size(),
                                 "Full file read hook: %s.dll+0x%08llx => BRQuarteTranslateHelper.asi",
                                 state.spec->segment, static_cast<unsigned long long>(read->rva));
            if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
                log_line(state, std::string_view{message.data(), static_cast<std::size_t>(size)});
            }
        }
        if (const auto* lookup = glyph_lookup_spec(state.spec->segment); lookup != nullptr) {
            size = std::snprintf(message.data(), message.size(),
                                 "Glyph lookup hook: %s.dll+0x%08llx => BRQuarteTranslateHelper.asi",
                                 state.spec->segment, static_cast<unsigned long long>(lookup->rva));
            if (size > 0 && static_cast<std::size_t>(size) < message.size()) {
                log_line(state, std::string_view{message.data(), static_cast<std::size_t>(size)});
            }
        }
    } catch (...) {
    }
}

DWORD module_worker_impl(HMODULE self, std::size_t index) {
    if (index >= module_specs.size()) {
        return 0;
    }
    auto state = std::make_unique<State>();
    PublishedStateLifetime published_state_lifetime{state};
    state->worker_started_ms = ::GetTickCount64();
    state->self = self;
    state->spec = &module_specs[index];
    if (!game_root_and_log(*state)) {
        return 0;
    }
    log_line(*state, std::string{state->spec->segment} + " preparation started.");
    ULONGLONG step_started = ::GetTickCount64();
    ULONGLONG preparation_started = step_started;

#if BRQ_EMBED_ASSETS
    state->game_module = wait_and_retain_module(*state->spec);
    state->owns_game_module_reference = true;
    log_elapsed(*state, std::string{state->spec->segment} + ".dll wait", step_started);
    step_started = ::GetTickCount64();
    preparation_started = step_started;
#endif

#if BRQ_EMBED_ASSETS
    const bool assets_ok = load_embedded_assets(*state);
#else
    const bool assets_ok = load_external_assets(*state);
#endif
    log_elapsed(*state, "Replacement file preparation", step_started);
    step_started = ::GetTickCount64();
    if (!assets_ok || state->assets_by_key.empty()) {
        if (assets_ok) {
            log_line(*state, "No replacement files; using original game files.");
        }
        return 0;
    }

    verify_source_packs(*state);
    log_elapsed(*state, "DLC file check", step_started);
    step_started = ::GetTickCount64();

#if BRQ_EMBED_ASSETS
    const bool glyph_tables_ok = load_embedded_glyph_tables(*state);
#else
    const bool glyph_tables_ok = load_external_glyph_tables(*state);
#endif
    log_elapsed(*state, "Glyph table preparation", step_started);
    step_started = ::GetTickCount64();
    if (!glyph_tables_ok) {
        log_line(*state, "Korean glyph table is missing, empty, or invalid; using original fonts.");
    }
    state->glyph_tables_ready = glyph_tables_ok;
    build_bridge_index(*state);
    log_elapsed(*state, "File mapping preparation", step_started);
    step_started = ::GetTickCount64();

#if !BRQ_EMBED_ASSETS
    state->game_module = wait_and_retain_module(*state->spec);
    state->owns_game_module_reference = true;
    log_elapsed(*state, std::string{state->spec->segment} + ".dll wait", step_started);
    step_started = ::GetTickCount64();
#endif

    LPVOID target{};
    LPVOID size_target{};
    if (!validate_module(state->game_module, *state->spec, target, size_target)) {
        log_line(*state, std::string{state->spec->segment} +
                             " module version is unsupported; using original game files.");
        return 0;
    }

    LPVOID target_read_whole{};
    if (!validate_read_whole(state->game_module, *state->spec, target_read_whole)) {
        log_line(*state, "Could not verify the full file read function; using original game files.");
        return 0;
    }

    LPVOID target_glyph_lookup{};
    if (state->glyph_tables_ready &&
        !validate_glyph_lookup(state->game_module, *state->spec, target_glyph_lookup)) {
        log_line(*state, "Could not verify the glyph lookup function; using original glyph lookup.");
        state->glyph_tables_ready = false;
    }

    const std::string success_message = std::string{state->spec->segment} + " Korean loader started.";
    LPVOID target_b1_lookup{}, target_b1_setter{};
    if (Config::diagnostics &&
        !validate_b1_text_hooks(state->game_module, *state->spec, target_b1_lookup, target_b1_setter)) {
        log_line(*state, "B1 string function code differs from the original; skipping string tracing hooks.");
    }
    LPVOID target_b1_menu_glyph{};
    if (Config::diagnostics && index == 1) {
        auto* base = reinterpret_cast<std::uint8_t*>(state->game_module);
        if (b1_menu_glyph_rva + b1_menu_glyph_signature.size() <= state->spec->image_size &&
            std::equal(b1_menu_glyph_signature.begin(), b1_menu_glyph_signature.end(), base + b1_menu_glyph_rva)) {
            target_b1_menu_glyph = base + b1_menu_glyph_rva;
        } else {
            log_line(*state, "B1 menu glyph selection function code differs from the original; skipping selection tracing hook.");
        }
    }
    const bool installed =
        install_segment_hooks(*state, index, target, size_target, target_read_whole, target_glyph_lookup,
                               target_b1_lookup, target_b1_setter, target_b1_menu_glyph);
    log_elapsed(*state, "Hook installation", step_started);
    if (installed && pack_index_specs[index].pack_load_rva != 0) {
        const PackHookInstallResult pack_hooks = install_pack_hooks(*state, index);
        log_line(*state, pack_hooks.timing_installed
                             ? std::string{state->spec->segment} + " PAK loading time measurement started."
                             : "Could not verify PAK loading time measurement functions; skipping measurement.");
        if (!pack_hooks.index_patch_armed) {
            log_index_patch_result(index, IndexPatchState::disabled);
        }
    }
    state->hooks_ready.store(true, std::memory_order_release);
    log_elapsed(*state, "Total preparation", preparation_started);
    State* const logger = state.get();
    if (installed) {
        log_installed_hooks(*logger);
        if (target_b1_lookup != nullptr && target_b1_setter != nullptr) {
            log_line(*logger, "B1 string hooks installed: lookup=+0x00207260, menu display=+0x003cd560; logging first 256 calls each");
        }
        if (target_b1_menu_glyph != nullptr) {
            log_line(*logger, "B1 menu glyph selection hook installed: +0x008e6c30; logging first 16 Hangul calls");
        }
    }
    log_line(*logger, installed ? success_message : "Could not install hooks; using original game files.");
    return 0;
}

DWORD WINAPI module_worker(LPVOID parameter) noexcept {
    const auto index = static_cast<std::size_t>(reinterpret_cast<std::uintptr_t>(parameter));
    DWORD result{};
    try {
        result = module_worker_impl(self_module, index);
    } catch (...) {
    }
    if (index < hook_slots.size()) {
        hook_slots[index].worker_running.store(false, std::memory_order_release);
    }
    return result;
}

void start_module_worker(std::size_t index) noexcept {
    if (index >= hook_slots.size() || hook_slots[index].state.load(std::memory_order_acquire) != nullptr) {
        return;
    }
    bool expected{};
    if (!hook_slots[index].worker_running.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) {
        return;
    }
    const HANDLE thread = ::CreateThread(nullptr, 0, &module_worker,
                                         reinterpret_cast<LPVOID>(static_cast<std::uintptr_t>(index)), 0, nullptr);
    if (thread == nullptr) {
        hook_slots[index].worker_running.store(false, std::memory_order_release);
        return;
    }
    ::CloseHandle(thread);
}

DWORD bootstrap_impl(HMODULE self) {
    if (!install_api_hooks()) {
        return 0;
    }
    if (!install_transition_hooks()) {
        return 0;
    }
    for (std::size_t index = 1; index < module_specs.size(); ++index) {
        start_module_worker(index);
    }
    static_cast<void>(module_worker_impl(self, 0));
    return 0;
}

DWORD WINAPI bootstrap(LPVOID parameter) noexcept {
    try {
        return bootstrap_impl(static_cast<HMODULE>(parameter));
    } catch (...) {
        return 0;
    }
}

void initialize_asi() noexcept {
    if (self_module == nullptr || bootstrap_started.exchange(true, std::memory_order_acq_rel)) {
        return;
    }
    HMODULE pinned_module{};
    if (::GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                             reinterpret_cast<LPCWSTR>(&self_module), &pinned_module) == FALSE) {
        bootstrap_started.store(false, std::memory_order_release);
        return;
    }
    const HANDLE thread = ::CreateThread(nullptr, 0, &bootstrap, self_module, 0, nullptr);
    if (thread == nullptr) {
        bootstrap_started.store(false, std::memory_order_release);
    } else {
        ::CloseHandle(thread);
    }
}

}  // namespace
}  // namespace brq

extern "C" __declspec(dllexport) void InitializeASI() {
    brq::initialize_asi();
}

extern "C" BOOL WINAPI DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        ::DisableThreadLibraryCalls(module);
        brq::self_module = module;
    }
    return TRUE;
}
