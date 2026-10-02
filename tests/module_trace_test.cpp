#include "../src/loader.cpp"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <string>
#include <string_view>
#include <vector>

namespace {

int load_a_calls{};
int load_w_calls{};
int free_calls{};
DWORD load_a_input_error{};
DWORD load_w_input_error{};
DWORD free_input_error{};
DWORD stub_output_error{};
BOOL stub_free_result{TRUE};
int glyph28_calls{};
void* glyph28_context{};
std::uint32_t glyph28_key{};
int glyph28_source_mode{};
int glyph28_dynamic_table_index{};
DWORD glyph28_input_error{};
const brq::GlyphRecord28* glyph28_result{};
int b1_string_lookup_calls{};
void* b1_string_lookup_table{};
std::int32_t b1_string_lookup_id{};
DWORD b1_string_lookup_input_error{};
const char* b1_string_lookup_result{};
int b1_text_set_calls{};
void* b1_text_set_object{};
const char* b1_text_set_text{};
std::uint8_t b1_text_set_flag{};
DWORD b1_text_set_input_error{};
int b1_menu_glyph_calls{};
void* b1_menu_glyph_object{};
std::uint16_t b1_menu_glyph_code{};
int b1_menu_glyph_font{};
DWORD b1_menu_glyph_input_error{};
bool b1_menu_glyph_result{true};

HMODULE WINAPI stub_load_library_a(LPCSTR) {
    ++load_a_calls;
    load_a_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return reinterpret_cast<HMODULE>(static_cast<std::uintptr_t>(0x12340000));
}

HMODULE WINAPI stub_load_library_w(LPCWSTR) {
    ++load_w_calls;
    load_w_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return nullptr;
}

BOOL WINAPI stub_free_library(HMODULE) {
    ++free_calls;
    free_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return stub_free_result;
}

const void* stub_glyph_lookup28(void* context, std::uint32_t key, int source_mode, int dynamic_table_index) {
    ++glyph28_calls;
    glyph28_context = context;
    glyph28_key = key;
    glyph28_source_mode = source_mode;
    glyph28_dynamic_table_index = dynamic_table_index;
    glyph28_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return glyph28_result;
}

const char* stub_b1_string_lookup(void* table, std::int32_t id) {
    ++b1_string_lookup_calls;
    b1_string_lookup_table = table;
    b1_string_lookup_id = id;
    b1_string_lookup_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return b1_string_lookup_result;
}

void stub_b1_text_set(void* object, const char* text, std::uint8_t flag) {
    ++b1_text_set_calls;
    b1_text_set_object = object;
    b1_text_set_text = text;
    b1_text_set_flag = flag;
    b1_text_set_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
}

bool stub_b1_menu_glyph(void* object, std::uint16_t code, int font) {
    ++b1_menu_glyph_calls;
    b1_menu_glyph_object = object;
    b1_menu_glyph_code = code;
    b1_menu_glyph_font = font;
    b1_menu_glyph_input_error = ::GetLastError();
    ::SetLastError(stub_output_error);
    return b1_menu_glyph_result;
}

std::string read_log(const wchar_t* path) {
    const HANDLE file = ::CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                      nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return {};
    }
    std::array<char, 256 * 1024> buffer{};
    DWORD read{};
    const BOOL read_ok = ::ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr);
    ::CloseHandle(file);
    if (read_ok == FALSE) {
        return {};
    }
    return {buffer.data(), read};
}

std::size_t log_line_count(const wchar_t* path) {
    const std::string log = read_log(path);
    return static_cast<std::size_t>(std::count(log.begin(), log.end(), '\n'));
}

std::size_t count_text(std::string_view text, std::string_view needle) {
    std::size_t count{};
    for (std::size_t offset{}; (offset = text.find(needle, offset)) != std::string_view::npos;
         offset += needle.size()) {
        ++count;
    }
    return count;
}

std::string display_text;
DWORD display_input_error{};
std::uintptr_t stub_runtime_display(void*, const char* text) {
    display_input_error = ::GetLastError();
    display_text = text == nullptr ? "" : text;
    ::SetLastError(7654);
    return 0x1234;
}

}  // namespace

int main(int argc, char** argv) {
    int failures{};
    const auto check = [&](bool condition, const char* message) {
        std::printf("%s: %s\n", condition ? "PASS" : "FAIL", message);
        failures += !condition;
    };

    if (!brq::runtime_texts.empty()) {
        brq::State display_state{};
        display_state.spec = &brq::module_specs[4];
        display_state.glyph_tables_ready = true;
        auto& slot = brq::hook_slots[4];
        slot.state.store(&display_state);
        slot.original_runtime_text[0] = &stub_runtime_display;
        for (const auto& row : brq::runtime_texts) {
            if (row.segment != "B4") continue;
            std::string source{row.source}, target{row.target};
            if (const auto pos = source.find("%d"); pos != std::string::npos) {
                source.replace(pos, 2, "123");
                target.replace(target.find("%d"), 2, "123");
            }
            ::SetLastError(4567);
            const auto result = brq::hooked_runtime_text<4, 0>(nullptr, source.c_str());
            const DWORD display_return_error = ::GetLastError();
            check(result == 0x1234 && display_text == target, "DLL display translation reaches original setter");
            check(display_input_error == 4567 && display_return_error == 7654, "DLL display preserves LastError and return value");
        }
        brq::hooked_runtime_text<4, 0>(nullptr, "unmatched text");
        check(display_text == "unmatched text", "unmatched display text preserved");
        display_state.replacements_disabled.store(true);
        const auto row = std::find_if(brq::runtime_texts.begin(), brq::runtime_texts.end(),
                                     [](const auto& r) { return r.segment == "B4"; });
        if (row != brq::runtime_texts.end()) {
            brq::hooked_runtime_text<4, 0>(nullptr, row->source.data());
            check(display_text == row->source, "disabled replacements preserve original display text");
        }
        slot.state.store(nullptr);
        slot.original_runtime_text.fill(nullptr);
    }
    for (int argument = 1; argument < argc; ++argument) {
        const auto module_index = brq::transition_module_index(argv[argument]);
        check(module_index < brq::module_specs.size(), "known DLL display test module");
        if (module_index >= brq::module_specs.size()) continue;
        HMODULE module = ::LoadLibraryExA(argv[argument], nullptr, DONT_RESOLVE_DLL_REFERENCES);
        check(module != nullptr, "map game DLL without starting the game");
        if (module != nullptr) {
            check(::MH_Initialize() == MH_OK, "initialize DLL display hook check");
            for (const auto& spec : brq::runtime_text_hooks) {
                if (spec.segment != brq::module_specs[module_index].segment) continue;
                for (std::size_t kind = 0; kind < 2; ++kind) {
                    auto* target = reinterpret_cast<std::uint8_t*>(module) + spec.rvas[kind];
                    const auto before = spec.signatures[kind];
                    check(std::equal(before.begin(), before.end(), target), "actual DLL display signature matches");
                    LPVOID trampoline{};
                    const auto status = ::MH_CreateHook(target, reinterpret_cast<LPVOID>(brq::runtime_text_detours[module_index][kind]), &trampoline);
                    check(status == MH_OK, "create hook on actual DLL display function");
                    if (status == MH_OK) {
                        check(::MH_EnableHook(target) == MH_OK, "write actual DLL display hook");
                        check(!std::equal(before.begin(), before.end(), target), "actual DLL instructions changed");
                        check(::MH_DisableHook(target) == MH_OK, "disable actual DLL display hook");
                        check(std::equal(before.begin(), before.end(), target), "actual DLL instructions restored");
                        check(::MH_RemoveHook(target) == MH_OK, "remove actual DLL display hook");
                    }
                }
            }
            ::MH_Uninitialize();
            ::FreeLibrary(module);
        }
    }

    check(brq::transition_module_index("B0.dll") == 0, "LoadLibraryA exact name");
    check(brq::transition_module_index("C:\\game\\b2.DlL") == 2, "LoadLibraryA path basename");
    check(brq::transition_module_index(L"folder/B4.dll") == 4, "LoadLibraryW path basename");
    check(brq::transition_module_index("B5.dll") == brq::module_specs.size(), "reject out-of-range DLL");
    check(brq::transition_module_index(L"B0.dll.bak") == brq::module_specs.size(), "reject suffix");

    std::array<wchar_t, MAX_PATH> temp_path{};
    std::array<wchar_t, MAX_PATH> log_path{};
    check(::GetTempPathW(static_cast<DWORD>(temp_path.size()), temp_path.data()) != 0, "temporary path");
    check(::GetTempFileNameW(temp_path.data(), L"brq", 0, log_path.data()) != 0, "temporary log file");

    brq::State state{};
    state.log_path = log_path.data();
    state.game_module = reinterpret_cast<HMODULE>(static_cast<std::uintptr_t>(0x56780000));
    brq::hook_slots[0].state.store(&state, std::memory_order_release);
    brq::original_load_library_a = &stub_load_library_a;
    brq::original_load_library_w = &stub_load_library_w;
    brq::original_free_library = &stub_free_library;
    brq::transition_lifecycle_ready.store(true, std::memory_order_release);

    stub_output_error = 321;
    ::SetLastError(123);
    const HMODULE load_a_result = brq::hooked_load_library_a("C:\\game\\B2.dll");
    const DWORD load_a_return_error = ::GetLastError();
    check(load_a_result == reinterpret_cast<HMODULE>(static_cast<std::uintptr_t>(0x12340000)),
          "LoadLibraryA result preserved");
    check(load_a_calls == 1, "LoadLibraryA original called once");
    check(load_a_input_error == 123 && load_a_return_error == 321, "LoadLibraryA LastError preserved");
    check(log_line_count(log_path.data()) == 1, "target LoadLibraryA logged");

    stub_output_error = 654;
    ::SetLastError(456);
    const HMODULE load_w_result = brq::hooked_load_library_w(L"B4.dll");
    const DWORD load_w_return_error = ::GetLastError();
    check(load_w_result == nullptr, "LoadLibraryW failure result preserved");
    check(load_w_calls == 1, "LoadLibraryW original called once");
    check(load_w_input_error == 456 && load_w_return_error == 654, "LoadLibraryW LastError preserved");
    check(log_line_count(log_path.data()) == 2, "target LoadLibraryW logged");

    stub_output_error = 987;
    ::SetLastError(789);
    static_cast<void>(brq::hooked_load_library_a("unrelated.dll"));
    const DWORD unrelated_load_return_error = ::GetLastError();
    check(load_a_calls == 2, "non-target LoadLibraryA original called once");
    check(load_a_input_error == 789 && unrelated_load_return_error == 987,
          "non-target LoadLibraryA LastError preserved");
    check(log_line_count(log_path.data()) == 2, "non-target load not logged");

    stub_free_result = TRUE;
    stub_output_error = 741;
    ::SetLastError(147);
    const BOOL free_success = brq::hooked_free_library(state.game_module);
    const DWORD free_success_return_error = ::GetLastError();
    check(free_success == TRUE, "FreeLibrary success result preserved");
    check(free_calls == 1, "successful FreeLibrary original called once");
    check(free_input_error == 147 && free_success_return_error == 741,
          "successful FreeLibrary LastError preserved");
    check(log_line_count(log_path.data()) == 3, "matching FreeLibrary logged");

    stub_free_result = FALSE;
    stub_output_error = 852;
    ::SetLastError(258);
    const BOOL free_failure = brq::hooked_free_library(state.game_module);
    const DWORD free_failure_return_error = ::GetLastError();
    check(free_failure == FALSE, "FreeLibrary failure result preserved");
    check(free_calls == 2, "failed FreeLibrary original called once");
    check(free_input_error == 258 && free_failure_return_error == 852,
          "failed FreeLibrary LastError preserved");
    check(log_line_count(log_path.data()) == 4, "failed matching FreeLibrary logged");
    check(read_log(log_path.data()).find("error=852") != std::string::npos,
          "failed FreeLibrary error logged");

    stub_output_error = 963;
    ::SetLastError(369);
    static_cast<void>(brq::hooked_free_library(
        reinterpret_cast<HMODULE>(static_cast<std::uintptr_t>(0x99990000))));
    const DWORD unrelated_free_return_error = ::GetLastError();
    check(free_calls == 3, "non-matching FreeLibrary original called once");
    check(free_input_error == 369 && unrelated_free_return_error == 963,
          "non-matching FreeLibrary LastError preserved");
    check(log_line_count(log_path.data()) == 4, "non-matching FreeLibrary not logged");

    brq::pending_module_release = {};
    stub_output_error = 159;
    ::AcquireSRWLockExclusive(&brq::log_lock);
    const ULONGLONG locked_started = ::GetTickCount64();
    ::SetLastError(951);
    static_cast<void>(brq::hooked_load_library_a("B3.dll"));
    const DWORD locked_return_error = ::GetLastError();
    const ULONGLONG locked_elapsed = ::GetTickCount64() - locked_started;
    ::ReleaseSRWLockExclusive(&brq::log_lock);
    check(load_a_calls == 3, "locked log LoadLibraryA original called once");
    check(load_a_input_error == 951 && locked_return_error == 159,
          "locked log LoadLibraryA LastError preserved");
    check(locked_elapsed < 1000, "locked transition log returns without waiting");
    check(log_line_count(log_path.data()) == 4, "locked transition log dropped");

    const std::string log = read_log(log_path.data());
    check(count_text(log, "return_address=") == 4 && count_text(log, "return_base=") == 4 &&
              count_text(log, "return_rva=0x") == 4 && count_text(log, "main_exe=1") == 4 &&
              count_text(log, "thread_id=") == 4,
          "all transition logs contain main executable return-address fields");

    const brq::GlyphRecord28 glyph28{0x00eab080, 17, 1025, 22, 24, -1, 2, 23, -22};
    glyph28_result = &glyph28;
    void* const context = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x12340000));
    stub_output_error = 2468;
    ::SetLastError(1357);
    const void* const glyph_result = brq::hooked_glyph_lookup28_impl(
        &state, &stub_glyph_lookup28, context, glyph28.key, 1, 7);
    const DWORD glyph_return_error = ::GetLastError();
    const std::string glyph_log = read_log(log_path.data());
    check(glyph_result == &glyph28 && glyph28_calls == 1 && glyph28_context == context &&
              glyph28_key == glyph28.key && glyph28_source_mode == 1 && glyph28_dynamic_table_index == 7,
          "B1 glyph lookup calls the original once and returns its 28-byte record");
    check(glyph28_input_error == 1357 && glyph_return_error == 2468,
          "B1 glyph diagnostics preserve the original lookup LastError");
    check(count_text(glyph_log, "Glyph selection: B1") == 1 && count_text(glyph_log, "x=17 y=1025 w=22 h=24") == 1 &&
              count_text(glyph_log, "left=-1 top=2 advance=23, record=") == 1,
          "B1 glyph diagnostics log the selected 28-byte record coordinates and address");

    glyph28_result = reinterpret_cast<const brq::GlyphRecord28*>(static_cast<std::uintptr_t>(1));
    stub_output_error = 9753;
    ::SetLastError(3579);
    const void* const unreadable_glyph_result = brq::hooked_glyph_lookup28_impl(
        &state, &stub_glyph_lookup28, context, 0x42, 1, 8);
    const DWORD unreadable_glyph_error = ::GetLastError();
    const std::string unreadable_glyph_log = read_log(log_path.data());
    check(unreadable_glyph_result == glyph28_result && glyph28_calls == 2 && glyph28_key == 0x42 &&
              glyph28_dynamic_table_index == 8,
          "B1 glyph diagnostics return an unreadable original record without dereferencing it");
    check(glyph28_input_error == 3579 && unreadable_glyph_error == 9753,
          "B1 unreadable glyph diagnostics preserve the original lookup LastError");
    check(count_text(unreadable_glyph_log, "glyph read failed") == 1,
          "B1 glyph diagnostics report an unreadable record without coordinates");

    brq::GlyphRecord28 changing_glyph{0x00eab080, 0, 1025, 22, 24, -1, 2, 23, -22};
    glyph28_result = &changing_glyph;
    glyph28_calls = 0;
    state.glyph_probe_count.store(0, std::memory_order_relaxed);
    state.glyph_diagnostic_probe_count.store(0, std::memory_order_relaxed);
    state.b1_binding_probe_logged.store(true, std::memory_order_relaxed);
    state.logged_glyph_mappings.clear();
    bool all_glyph_results_preserved = true;
    for (unsigned call = 0; call < 1024; ++call) {
        changing_glyph.x = static_cast<std::uint16_t>(call);
        all_glyph_results_preserved =
            brq::hooked_glyph_lookup28_impl(&state, &stub_glyph_lookup28, context,
                                            changing_glyph.key, 1, 9) == &changing_glyph &&
            all_glyph_results_preserved;
    }
    check(all_glyph_results_preserved && glyph28_calls == 1024,
          "B1 glyph diagnostic limit still calls the original for all 1024 lookups");
    check(state.glyph_probe_count.load(std::memory_order_relaxed) == 16 &&
              state.glyph_diagnostic_probe_count.load(std::memory_order_relaxed) == 16,
          "B1 general and Hangul diagnostic counters both stop at 16");
    check(state.logged_glyph_mappings.size() == 17,
          "B1 changing glyph coordinates produce only one entry and 16 decision logs");

    std::uintptr_t unread_value{0xfeedface};
    check(!brq::read_font_field(1, 0, unread_value),
          "B1 font diagnostics reject an unreadable address");
    state.spec = &brq::module_specs[1];
    state.game_module = reinterpret_cast<HMODULE>(static_cast<std::uintptr_t>(1));
    ::SetLastError(8642);
    brq::log_b1_font_binding_once(state, context, 1);
    const DWORD binding_return_error = ::GetLastError();
    const std::string binding_log = read_log(log_path.data());
    check(binding_return_error == 8642, "B1 font binding diagnostics preserve LastError");
    check(count_text(binding_log, "B1 font binding:") == 2 && count_text(binding_log, "chain read=0") == 2 &&
              count_text(binding_log, "context match=-1") == 2,
          "B1 font binding diagnostics report both unreadable Main and Sub chains");

    constexpr char traced_text[]{"\xec\x95\x88\r\n\t"};
    void* const string_table = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x11112222));
    void* const text_object = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x33334444));
    const void* const text_caller = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x55556666));
    b1_string_lookup_result = traced_text;
    stub_output_error = 24680;
    ::SetLastError(13579);
    const char* const looked_up_text = brq::hooked_b1_string_lookup_impl(
        &state, &stub_b1_string_lookup, string_table, 42, text_caller);
    const DWORD string_lookup_return_error = ::GetLastError();
    check(looked_up_text == traced_text && b1_string_lookup_calls == 1 &&
              b1_string_lookup_table == string_table && b1_string_lookup_id == 42,
          "B1 string lookup calls the original once with unchanged arguments and result pointer");
    check(b1_string_lookup_input_error == 13579 && string_lookup_return_error == 24680,
          "B1 string lookup diagnostics preserve LastError before and after the original call");

    stub_output_error = 97531;
    ::SetLastError(86420);
    brq::hooked_b1_text_set_impl(&state, &stub_b1_text_set, text_object, traced_text, 0, text_caller);
    const DWORD text_set_return_error = ::GetLastError();
    check(b1_text_set_calls == 1 && b1_text_set_object == text_object && b1_text_set_text == traced_text &&
              b1_text_set_flag == 0,
          "B1 text display calls the original once with unchanged arguments");
    check(b1_text_set_input_error == 86420 && text_set_return_error == 97531,
          "B1 text display diagnostics preserve LastError before and after the original call");
    const std::string utf8_text_log = read_log(log_path.data());
    check(count_text(utf8_text_log, "B1 string lookup:") == 1 &&
              count_text(utf8_text_log, "B1 string display:") == 1 &&
              count_text(utf8_text_log,
                         "text=\xec\x95\x88\\x0d\\x0a\\x09; bytes=ec 95 88 0d 0a 09") == 2,
          "B1 text diagnostics retain UTF-8 bytes and escape CR, LF, and tab");

    const char* const unreadable_text = reinterpret_cast<const char*>(static_cast<std::uintptr_t>(1));
    b1_string_lookup_result = unreadable_text;
    stub_output_error = 2222;
    ::SetLastError(1111);
    const char* const unreadable_lookup_result = brq::hooked_b1_string_lookup_impl(
        &state, &stub_b1_string_lookup, string_table, 43, text_caller);
    const DWORD unreadable_lookup_error = ::GetLastError();
    stub_output_error = 4444;
    ::SetLastError(3333);
    brq::hooked_b1_text_set_impl(&state, &stub_b1_text_set, text_object, unreadable_text, 1, text_caller);
    const DWORD unreadable_set_error = ::GetLastError();
    const std::string unreadable_text_log = read_log(log_path.data());
    check(unreadable_lookup_result == unreadable_text && b1_string_lookup_calls == 2 &&
              b1_string_lookup_input_error == 1111 && unreadable_lookup_error == 2222,
          "B1 string lookup returns an unreadable original pointer without dereferencing it");
    check(b1_text_set_calls == 2 && b1_text_set_text == unreadable_text && b1_text_set_flag == 1 &&
              b1_text_set_input_error == 3333 && unreadable_set_error == 4444,
          "B1 text display passes an unreadable pointer to the original without dereferencing it");
    check(count_text(unreadable_text_log, ", readable=0, truncated=0, text=; bytes=") == 2,
          "B1 text diagnostics mark unreadable lookup and display pointers");

    constexpr char empty_text[]{""};
    b1_string_lookup_result = empty_text;
    static_cast<void>(brq::hooked_b1_string_lookup_impl(
        &state, &stub_b1_string_lookup, string_table, 44, text_caller));
    std::array<char, 194> long_text{};
    std::fill_n(long_text.begin(), 192, 'A');
    long_text[192] = '\x01';
    b1_string_lookup_result = long_text.data();
    static_cast<void>(brq::hooked_b1_string_lookup_impl(
        &state, &stub_b1_string_lookup, string_table, 45, text_caller));
    const std::string bounded_text_log = read_log(log_path.data());
    const auto long_line_begin = bounded_text_log.find("B1 string lookup: id=45");
    const auto long_line_end = long_line_begin == std::string::npos
                                   ? std::string::npos
                                   : bounded_text_log.find('\n', long_line_begin);
    const std::string_view long_line = long_line_begin == std::string::npos
                                           ? std::string_view{}
                                           : std::string_view{bounded_text_log}.substr(
                                                 long_line_begin, long_line_end - long_line_begin);
    check(count_text(bounded_text_log, ", readable=1, truncated=0, text=; bytes=") == 1,
          "B1 text diagnostics log an empty readable string");
    check(long_line.find(", readable=1, truncated=1, text=") != std::string_view::npos &&
              long_line.find("\\x01") == std::string_view::npos,
          "B1 text diagnostics stop before byte 193 and mark the 192-byte string truncated");

    state.b1_text_probe_count[0].store(0, std::memory_order_relaxed);
    state.b1_text_probe_count[1].store(0, std::memory_order_relaxed);
    b1_string_lookup_calls = 0;
    b1_string_lookup_result = traced_text;
    const auto lookup_logs_before = count_text(bounded_text_log, "B1 string lookup:");
    bool all_lookup_results_preserved = true;
    for (std::int32_t call = 0; call < 1024; ++call) {
        stub_output_error = 6000 + static_cast<DWORD>(call);
        ::SetLastError(5000 + static_cast<DWORD>(call));
        all_lookup_results_preserved =
            brq::hooked_b1_string_lookup_impl(
                &state, &stub_b1_string_lookup, string_table, 1000 + call, text_caller) == traced_text &&
            b1_string_lookup_table == string_table && b1_string_lookup_id == 1000 + call &&
            b1_string_lookup_input_error == 5000 + static_cast<DWORD>(call) &&
            ::GetLastError() == stub_output_error && all_lookup_results_preserved;
    }
    const std::string capped_lookup_log = read_log(log_path.data());
    check(all_lookup_results_preserved && b1_string_lookup_calls == 1024 &&
              b1_string_lookup_table == string_table && b1_string_lookup_id == 2023,
          "B1 lookup diagnostic limit still calls the original for all 1024 lookups");
    check(state.b1_text_probe_count[0].load(std::memory_order_relaxed) == 256 &&
              state.b1_text_probe_count[1].load(std::memory_order_relaxed) == 0 &&
              count_text(capped_lookup_log, "B1 string lookup:") - lookup_logs_before == 256,
          "B1 lookup diagnostics stop after 256 distinct logged calls");

    b1_text_set_calls = 0;
    const auto display_logs_before = count_text(capped_lookup_log, "B1 string display:");
    bool all_display_errors_preserved = true;
    for (DWORD call = 0; call < 1024; ++call) {
        stub_output_error = 8000 + call;
        ::SetLastError(7000 + call);
        const std::uint8_t flag = static_cast<std::uint8_t>(call % 3 == 0 ? 0 : call % 3 == 1 ? 1 : 0xa5);
        brq::hooked_b1_text_set_impl(&state, &stub_b1_text_set, text_object, traced_text, flag, text_caller);
        all_display_errors_preserved =
            b1_text_set_object == text_object && b1_text_set_text == traced_text &&
            b1_text_set_flag == flag && b1_text_set_input_error == 7000 + call &&
            ::GetLastError() == stub_output_error &&
            all_display_errors_preserved;
    }
    const std::string capped_display_log = read_log(log_path.data());
    check(all_display_errors_preserved && b1_text_set_calls == 1024 &&
              b1_text_set_object == text_object && b1_text_set_text == traced_text && b1_text_set_flag == 0,
          "B1 display diagnostic limit still calls the original for all 1024 updates");
    check(state.b1_text_probe_count[0].load(std::memory_order_relaxed) == 256 &&
              state.b1_text_probe_count[1].load(std::memory_order_relaxed) == 256 &&
              count_text(capped_display_log, "B1 string display:") - display_logs_before == 256,
          "B1 lookup and display diagnostics have independent 256-call limits");

    std::array<std::uint8_t, 0x3ab0> menu_object{};
    std::array<std::uint8_t, 0x20> menu_resource{};
    std::vector<std::uint8_t> menu_table(0x16000);
    const auto write_menu_field = [](void* destination, const auto& value) {
        std::memcpy(destination, &value, sizeof(value));
    };
    const std::uintptr_t menu_resource_address = reinterpret_cast<std::uintptr_t>(menu_resource.data());
    const std::uintptr_t menu_table_address = reinterpret_cast<std::uintptr_t>(menu_table.data());
    const std::uint32_t font0_map_offset = 0x80;
    const std::uint32_t font1_map_offset = 0x100;
    const std::uint32_t menu_header = 0xcafebabe;
    const std::uint16_t fallback_code = 0x1234;
    const std::uint16_t exempt_code = 0xbeef;
    const int selected_menu_font = 1;
    write_menu_field(menu_object.data() + 0x3a08, menu_resource_address);
    write_menu_field(menu_object.data() + 0x3a88, selected_menu_font);
    write_menu_field(menu_object.data() + 0x3aa6, fallback_code);
    write_menu_field(menu_object.data() + 0x3aa8, exempt_code);
    write_menu_field(menu_resource.data() + 0x18, menu_table_address);
    write_menu_field(menu_table.data(), menu_header);
    write_menu_field(menu_table.data() + 0x20, font0_map_offset);
    write_menu_field(menu_table.data() + 0x24, font1_map_offset);

    state.b1_menu_glyph_probe_count.store(0, std::memory_order_relaxed);
    state.logged_glyph_mappings.clear();
    b1_menu_glyph_calls = 0;
    b1_menu_glyph_result = false;
    stub_output_error = 41234;
    ::SetLastError(31234);
    const bool zero_map_result = brq::hooked_b1_menu_glyph_impl(
        &state, &stub_b1_menu_glyph, menu_object.data(), 0xac00, -1);
    const DWORD zero_map_return_error = ::GetLastError();
    check(!zero_map_result && b1_menu_glyph_calls == 1 && b1_menu_glyph_object == menu_object.data() &&
              b1_menu_glyph_code == 0xac00 && b1_menu_glyph_font == -1 &&
              b1_menu_glyph_input_error == 31234 && zero_map_return_error == 41234,
          "B1 menu glyph calls the original once with unchanged zero-map arguments and LastError");
    const std::string zero_map_log = read_log(log_path.data());
    check(zero_map_log.find("B1 menu glyph selection: input=U+AC00, selected=U+1234, font=1(requested=-1), glyph ID=0, "
                            "fallback=U+1234, exempt=U+BEEF, map read=1, fallback read=1") != std::string::npos,
          "B1 menu glyph selects the fallback for a zero UTF-16 map number and reads font at +3A88");

    const std::uint16_t nonzero_map_number = 0x0042;
    write_menu_field(menu_table.data() + font1_map_offset + static_cast<std::size_t>(0xac01) * 2,
                     nonzero_map_number);
    b1_menu_glyph_result = true;
    stub_output_error = 51234;
    ::SetLastError(41234);
    const bool nonzero_map_result = brq::hooked_b1_menu_glyph_impl(
        &state, &stub_b1_menu_glyph, menu_object.data(), 0xac01, selected_menu_font);
    const DWORD nonzero_map_return_error = ::GetLastError();
    check(nonzero_map_result && b1_menu_glyph_calls == 2 && b1_menu_glyph_object == menu_object.data() &&
              b1_menu_glyph_code == 0xac01 && b1_menu_glyph_font == selected_menu_font &&
              b1_menu_glyph_input_error == 41234 && nonzero_map_return_error == 51234,
          "B1 menu glyph preserves a nonzero UTF-16 map and explicit font argument");
    const std::string nonzero_map_log = read_log(log_path.data());
    check(nonzero_map_log.find("B1 menu glyph selection: input=U+AC01, selected=U+AC01, font=1(requested=1), glyph ID=66, "
                               "fallback=U+1234, exempt=U+BEEF, map read=1, fallback read=1") != std::string::npos,
          "B1 menu glyph keeps the input code when the UTF-16 map number is nonzero");

    const std::uint16_t zero_map_number{};
    write_menu_field(menu_table.data() + font1_map_offset + static_cast<std::size_t>(0xac02) * 2,
                     zero_map_number);
    write_menu_field(menu_object.data() + 0x3aa8, static_cast<std::uint16_t>(0xac02));
    stub_output_error = 61234;
    ::SetLastError(51234);
    const bool exempt_result = brq::hooked_b1_menu_glyph_impl(
        &state, &stub_b1_menu_glyph, menu_object.data(), 0xac02, -1);
    const DWORD exempt_return_error = ::GetLastError();
    check(exempt_result && b1_menu_glyph_calls == 3 && b1_menu_glyph_object == menu_object.data() &&
              b1_menu_glyph_code == 0xac02 && b1_menu_glyph_font == -1 &&
              b1_menu_glyph_input_error == 51234 && exempt_return_error == 61234,
          "B1 menu glyph preserves the -1 font and exempt code arguments");
    void* const unreadable_menu_object = reinterpret_cast<void*>(static_cast<std::uintptr_t>(1));
    stub_output_error = 71234;
    ::SetLastError(61234);
    const bool unreadable_menu_result = brq::hooked_b1_menu_glyph_impl(
        &state, &stub_b1_menu_glyph, unreadable_menu_object, 0xac03, -1);
    const DWORD unreadable_menu_return_error = ::GetLastError();
    check(unreadable_menu_result && b1_menu_glyph_calls == 4 &&
              b1_menu_glyph_object == unreadable_menu_object &&
              b1_menu_glyph_code == 0xac03 && b1_menu_glyph_font == -1 &&
              b1_menu_glyph_input_error == 61234 && unreadable_menu_return_error == 71234,
          "B1 menu glyph safely diagnoses an unreadable object without changing original arguments");

    write_menu_field(menu_object.data() + 0x3aa8, exempt_code);
    const std::string menu_behavior_log = read_log(log_path.data());
    check(menu_behavior_log.find("B1 menu glyph selection: input=U+AC02, selected=U+AC02, font=1(requested=-1), glyph ID=0, "
                                "fallback=U+1234, exempt=U+AC02, map read=1, fallback read=1") != std::string::npos,
          "B1 menu glyph skips fallback for the exempt UTF-16 code");
    state.b1_menu_glyph_probe_count.store(0, std::memory_order_relaxed);
    state.logged_glyph_mappings.clear();
    b1_menu_glyph_calls = 0;
    b1_menu_glyph_result = true;
    bool all_menu_results_preserved = true;
    const auto menu_logs_before = count_text(menu_behavior_log, "B1 menu glyph selection:");
    for (std::uint16_t call = 0; call < 32; ++call) {
        const auto code = static_cast<std::uint16_t>(0xac20 + call);
        stub_output_error = 80000 + call;
        ::SetLastError(70000 + call);
        const bool result = brq::hooked_b1_menu_glyph_impl(
            &state, &stub_b1_menu_glyph, menu_object.data(), code, -1);
        all_menu_results_preserved =
            result && b1_menu_glyph_object == menu_object.data() && b1_menu_glyph_code == code &&
            b1_menu_glyph_font == -1 && b1_menu_glyph_input_error == 70000 + call &&
            ::GetLastError() == stub_output_error && all_menu_results_preserved;
    }
    const std::string capped_menu_log = read_log(log_path.data());
    check(all_menu_results_preserved && b1_menu_glyph_calls == 32 &&
              state.b1_menu_glyph_probe_count.load(std::memory_order_relaxed) == 16 &&
              state.logged_glyph_mappings.size() == 16 &&
              count_text(capped_menu_log, "B1 menu glyph selection:") - menu_logs_before == 16,
          "B1 menu glyph diagnostics stop at 16 Hangul calls while the original runs 32 times");

    brq::hook_slots[0].state.store(nullptr, std::memory_order_release);
    brq::pending_module_release = {};
    ::DeleteFileW(log_path.data());

    const bool core_installed = brq::install_api_hooks();
    check(core_installed, "core API hooks installed");
    LPVOID duplicate_target{};
    brq::LoadLibraryAFn duplicate_original{};
    const MH_STATUS duplicate_status = ::MH_CreateHookApiEx(
        L"kernel32.dll", "LoadLibraryA", reinterpret_cast<LPVOID>(&stub_load_library_a),
        reinterpret_cast<LPVOID*>(&duplicate_original), &duplicate_target);
    check(duplicate_status == MH_OK, "disabled duplicate transition hook created");
    brq::transition_lifecycle_ready.store(false, std::memory_order_release);
    check(!brq::install_transition_hooks(), "optional transition hooks may fail independently");
    check(!brq::transition_lifecycle_ready.load(std::memory_order_acquire),
          "optional transition hook failure keeps lifecycle inactive");
    const std::array<LPVOID, 4> core_targets{
        brq::create_file_a_target,
        brq::create_file_target,
        brq::get_attributes_target,
        brq::get_attributes_ex_target,
    };
    bool core_retained = core_installed;
    for (LPVOID target : core_targets) {
        core_retained = target != nullptr && ::MH_DisableHook(target) == MH_OK && core_retained;
        if (target != nullptr) {
            static_cast<void>(::MH_RemoveHook(target));
        }
    }
    check(core_retained, "optional failure leaves all core API hooks enabled");
    if (duplicate_status == MH_OK) {
        static_cast<void>(::MH_RemoveHook(duplicate_target));
    }
    static_cast<void>(::MH_Uninitialize());

    std::printf("failures=%d\n", failures);
    return failures == 0 ? 0 : 1;
}
