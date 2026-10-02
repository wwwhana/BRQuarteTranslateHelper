#include "../src/loader.cpp"

#include <atomic>
#include <cstdio>

namespace {

#if defined(_MSC_VER)
#define TEST_NOINLINE __declspec(noinline)
#else
#define TEST_NOINLINE __attribute__((noinline))
#endif

HANDLE entered_event{};
HANDLE release_event{};
std::atomic_bool block_open{};
std::atomic_int open_calls{};
std::atomic_bool retirement_finished{};
bool retirement_result{};
DWORD load_input_error{};
int load_calls{};
int free_calls{};
DWORD map_input_error{};
int map_calls{};

TEST_NOINLINE bool fake_open(void*, const char*, int a3, int a4, int a5) {
    open_calls.fetch_add(1, std::memory_order_relaxed);
    if (block_open.load(std::memory_order_acquire)) {
        ::SetEvent(entered_event);
        ::WaitForSingleObject(release_event, INFINITE);
    }
    return a3 + a4 + a5 == 6;
}

TEST_NOINLINE std::uint32_t fake_file_size(const char* path) {
    const std::uint32_t size = path == nullptr ? 0U : static_cast<std::uint32_t>(std::strlen(path));
    return size;
}

BOOL WINAPI failing_free_library(HMODULE) {
    ++free_calls;
    ::SetLastError(487);
    return FALSE;
}

HMODULE WINAPI failing_load_library(LPCWSTR) {
    ++load_calls;
    load_input_error = ::GetLastError();
    ::SetLastError(126);
    return nullptr;
}

BOOL WINAPI tracked_free_library(HMODULE module) {
    ++free_calls;
    return ::FreeLibrary(module);
}

HMODULE WINAPI map_without_resolving(LPCWSTR path) {
    ++map_calls;
    map_input_error = ::GetLastError();
    return ::LoadLibraryExW(path, nullptr, DONT_RESOLVE_DLL_REFERENCES);
}

DWORD WINAPI call_open(LPVOID parameter) {
    const auto open = reinterpret_cast<brq::OpenFn>(parameter);
    static_cast<void>(open(nullptr, "test.bin", 1, 2, 3));
    return 0;
}

DWORD WINAPI retire_slot(LPVOID parameter) {
    auto* const state = static_cast<brq::State*>(parameter);
    ::SetLastError(741);
    retirement_result = brq::retire_module_release(*state, 1);
    retirement_finished.store(true, std::memory_order_release);
    return 0;
}

DWORD WINAPI finish_preparation(LPVOID parameter) {
    const auto index = reinterpret_cast<std::uintptr_t>(parameter);
    ::Sleep(300);
    brq::hook_slots[index].worker_running.store(false, std::memory_order_release);
    return 0;
}

void clear_fake_slot(std::size_t index) {
    auto& slot = brq::hook_slots[index];
    static_cast<void>(brq::set_module_hooks_enabled(slot, false));
    for (std::size_t target_index = 0; target_index < slot.created_target_count; ++target_index) {
        static_cast<void>(::MH_RemoveHook(slot.created_targets[target_index]));
    }
    slot.state.store(nullptr, std::memory_order_release);
    slot.detour_gate.store(0, std::memory_order_release);
    slot.original_open = nullptr;
    slot.original_file_size = nullptr;
    slot.open_target = nullptr;
    slot.file_size_target = nullptr;
    slot.created_targets.fill(nullptr);
    slot.enabled_targets.fill(nullptr);
    slot.created_target_count = 0;
    slot.enabled_target_count = 0;
}

}  // namespace

int main() {
    int failures{};
    const auto check = [&](bool condition, const char* message) {
        std::printf("%s: %s\n", condition ? "PASS" : "FAIL", message);
        failures += !condition;
    };

    entered_event = ::CreateEventW(nullptr, TRUE, FALSE, nullptr);
    release_event = ::CreateEventW(nullptr, TRUE, FALSE, nullptr);
    check(entered_event != nullptr && release_event != nullptr, "create synchronization events");

    brq::original_free_library = &failing_free_library;
    brq::transition_lifecycle_ready.store(false, std::memory_order_release);
    free_calls = 0;
    ::SetLastError(777);
    const BOOL inactive_free = brq::hooked_free_library_impl(reinterpret_cast<HMODULE>(1),
                                                              reinterpret_cast<void*>(&fake_open));
    const DWORD inactive_free_error = ::GetLastError();
    load_calls = 0;
    ::SetLastError(778);
    const HMODULE inactive_load = brq::hooked_load_library(&failing_load_library, L"B2.dll",
                                                            reinterpret_cast<void*>(&fake_open));
    const DWORD inactive_load_error = ::GetLastError();
    check(inactive_free == FALSE && free_calls == 1 && inactive_free_error == 487 &&
              inactive_load == nullptr && load_calls == 1 && load_input_error == 778 && inactive_load_error == 126,
          "inactive lifecycle detours only call originals and preserve their LastError values");
    check(brq::pending_module_release.state == nullptr, "inactive lifecycle detours do not publish transition state");
    brq::transition_lifecycle_ready.store(true, std::memory_order_release);

    brq::State state{};
    state.spec = &brq::module_specs[1];
    state.game_module = ::GetModuleHandleW(nullptr);
    state.owns_game_module_reference = true;
    brq::original_free_library = &failing_free_library;
    free_calls = 0;
    const bool installed = brq::install_segment_hooks(
        state, 1, reinterpret_cast<LPVOID>(&fake_open), reinterpret_cast<LPVOID>(&fake_file_size), nullptr, nullptr);
    state.hooks_ready.store(true, std::memory_order_release);
    check(installed, "install fake module hooks");
    check(brq::hook_slots[1].created_target_count == 2 && brq::hook_slots[1].enabled_target_count == 2,
          "track exactly created and enabled targets");

    brq::hook_slots[1].detour_gate.store(brq::ActiveDetour::retiring, std::memory_order_release);
    const int calls_before_fallback = open_calls.load(std::memory_order_acquire);
    check(!brq::hooked_open_for<1>(nullptr, "late.bin", 1, 2, 3), "late detour entry returns fallback");
    check(open_calls.load(std::memory_order_acquire) == calls_before_fallback,
          "late detour entry does not touch trampoline");
    brq::hook_slots[1].detour_gate.store(0, std::memory_order_release);

    block_open.store(true, std::memory_order_release);
    const HANDLE open_thread = ::CreateThread(nullptr, 0, &call_open, reinterpret_cast<LPVOID>(&fake_open), 0, nullptr);
    check(open_thread != nullptr, "start active detour");
    check(::WaitForSingleObject(entered_event, 2'000) == WAIT_OBJECT_0, "active detour entered trampoline");
    const HANDLE retire_thread = ::CreateThread(nullptr, 0, &retire_slot, &state, 0, nullptr);
    check(retire_thread != nullptr, "start module retirement");
    ::Sleep(20);
    check(!retirement_finished.load(std::memory_order_acquire), "retirement waits for active detour");
    ::SetEvent(release_event);
    check(::WaitForSingleObject(open_thread, 2'000) == WAIT_OBJECT_0, "active detour completed");
    check(::WaitForSingleObject(retire_thread, 2'000) == WAIT_OBJECT_0, "retirement completed");
    check(!retirement_result && free_calls == 1, "failed release keeps the module generation");
    check((brq::hook_slots[1].detour_gate.load(std::memory_order_acquire) & brq::ActiveDetour::retiring) == 0,
          "failed release reopens detour entry");

    block_open.store(false, std::memory_order_release);
    const brq::OpenFn open = &fake_open;
    check(open(nullptr, "again.bin", 1, 2, 3), "failed release re-enables original hooks");

    brq::pending_module_release = {&state, 1};
    load_calls = 0;
    ::SetLastError(258);
    const HMODULE load_result = brq::hooked_load_library(&failing_load_library, L"B2.dll",
                                                         reinterpret_cast<void*>(&fake_open));
    const DWORD load_error = ::GetLastError();
    check(load_result == nullptr && load_calls == 1, "lifecycle failure still calls original loader once");
    check(load_input_error == 258 && load_error == 126, "lifecycle failure preserves loader LastError");

    clear_fake_slot(1);
    state.owns_game_module_reference = false;

    const auto verify_hash_patch = [&](std::size_t index, HMODULE mapped_game) {
        if (mapped_game == nullptr) {
            check(false, "hash check requires the mapped game DLL");
            return;
        }
        std::printf("Checking %s hash installation\n", brq::module_specs[index].segment);
        auto& runtime = brq::pack_indexes[index];
        const auto& spec = brq::pack_index_specs[index];
        auto* const saved_base = runtime.module_base;
        const auto saved_status = runtime.patch_state.load(std::memory_order_acquire);
        const bool saved_logged = runtime.result_logged.load(std::memory_order_acquire);
        auto* const base = reinterpret_cast<std::uint8_t*>(mapped_game);
        runtime.module_base = base;
        runtime.result_logged.store(false, std::memory_order_release);
        const auto original_bytes = [&] {
            return brq::pack_code_matches(base + spec.builder_hash_rva, spec.builder_hash_bytes) &&
                   brq::pack_code_matches(base + spec.lookup_hash_rva, spec.lookup_hash_bytes);
        };
        check(brq::index_is_pristine(index) && original_bytes(),
              "mapped module has empty indexes and both original hash ranges");

        runtime.patch_state.store(brq::IndexPatchState::pending, std::memory_order_release);
        brq::prepare_index_patch(index, 1);
        check(runtime.patch_state.load() == brq::IndexPatchState::disabled && original_bytes(),
              "missed first PAK leaves both hash ranges unchanged");

        runtime.patch_state.store(brq::IndexPatchState::pending, std::memory_order_release);
        brq::reject_pending_index_patch(index);
        check(runtime.patch_state.load() == brq::IndexPatchState::disabled && original_bytes(),
              "lookup before first PAK cancels the pending patch");

        auto* const slots = reinterpret_cast<std::uintptr_t*>(base + spec.pack_slots_rva);
        const auto saved_slot = slots[0];
        std::array<std::uintptr_t, 4> populated_index{1, 0, 0, 0};
        slots[0] = reinterpret_cast<std::uintptr_t>(populated_index.data());
        runtime.patch_state.store(brq::IndexPatchState::pending, std::memory_order_release);
        brq::prepare_index_patch(index, 0);
        check(runtime.patch_state.load() == brq::IndexPatchState::disabled && original_bytes(),
              "existing index prevents changing either hash range");
        slots[0] = saved_slot;

        runtime.patch_state.store(brq::IndexPatchState::pending, std::memory_order_release);
        brq::prepare_index_patch(index, 0);
#if BRQ_DISABLE_B0_HASH
        if (index == 0) {
            check(runtime.patch_state.load() == brq::IndexPatchState::disabled && original_bytes(),
                  "B0 hash OFF keeps both original code ranges");
        } else
#endif
        {
            check(runtime.patch_state.load() == brq::IndexPatchState::enabled,
                  "empty indexes allow optimization before the first PAK");
            check(brq::pack_code_matches(base + spec.builder_hash_rva, brq::builder_hash_patch()) &&
                      brq::pack_code_matches(base + spec.lookup_hash_rva, brq::lookup_hash_patch()),
                  "builder and lookup receive the same bucket algorithm together");
            auto* const builder = base + spec.builder_hash_rva;
            auto* const lookup = base + spec.lookup_hash_rva;
            check(brq::restore_hash_bytes(index, builder, lookup,
                      static_cast<std::size_t>(lookup + spec.lookup_hash_bytes.size() - builder),
                      PAGE_EXECUTE_READ), "restore both original hash ranges and page protection");
        }
        runtime.module_base = saved_base;
        runtime.patch_state.store(saved_status, std::memory_order_release);
        runtime.result_logged.store(saved_logged, std::memory_order_release);
    };
    HMODULE b0_game = ::LoadLibraryExW(L"B0.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
    HMODULE b0_owned{};
    const BOOL retained = b0_game != nullptr ? ::GetModuleHandleExW(0, L"B0.dll", &b0_owned) : FALSE;
    HMODULE b0_extra = retained != FALSE ? ::LoadLibraryExW(L"B0.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES)
                                         : nullptr;
    check(b0_game != nullptr && retained != FALSE && b0_owned == b0_game && b0_extra == b0_game,
          "map real B0 with game, loader, and extra references");

    verify_hash_patch(0, b0_game);

    brq::State transition_state{};
    transition_state.spec = &brq::module_specs[0];
    transition_state.game_module = b0_owned;
    transition_state.owns_game_module_reference = retained != FALSE;
    brq::original_free_library = &tracked_free_library;
    const bool transition_hooks = b0_game != nullptr &&
                                  brq::install_segment_hooks(transition_state, 0,
                                                             reinterpret_cast<LPVOID>(&fake_open),
                                                             reinterpret_cast<LPVOID>(&fake_file_size), nullptr, nullptr);
    transition_state.hooks_ready.store(true, std::memory_order_release);
    check(transition_hooks, "install first-visit hooks");
    brq::Asset stale_asset{};
    const brq::BridgeMatch stale_match{&transition_state, &stale_asset};
    check(brq::bridge_match_active(stale_match), "captured bridge match is active before retirement");

    free_calls = 0;
    const BOOL game_freed = b0_game != nullptr
                                ? brq::hooked_free_library_impl(b0_game, reinterpret_cast<void*>(&fake_open))
                                : FALSE;
    check(game_freed != FALSE && free_calls == 1 && brq::pending_module_release.state == &transition_state,
          "game release records a same-thread pending generation");

    brq::State b2_sentinel{};
    b2_sentinel.spec = &brq::module_specs[2];
    brq::hook_slots[2].state.store(&b2_sentinel, std::memory_order_release);
    map_calls = 0;
    ::SetLastError(731);
    const HMODULE blocked_b2 = brq::hooked_load_library(&map_without_resolving, L"B2.dll",
                                                         reinterpret_cast<void*>(&fake_open));
    check(blocked_b2 == nullptr && map_calls == 1 && map_input_error == 731,
          "extra reference blocks B2 while calling the original loader once");
    check(brq::pending_module_release.state == &transition_state &&
              transition_state.owns_game_module_reference && ::GetModuleHandleW(L"B0.dll") == b0_game,
          "remaining B0 reference is reacquired and keeps the pending transition");
    check((brq::hook_slots[0].detour_gate.load(std::memory_order_acquire) & brq::ActiveDetour::retiring) == 0,
          "reference rollback reopens first-visit hooks");

    if (blocked_b2 != nullptr) {
        static_cast<void>(::FreeLibrary(blocked_b2));
    }
    if (b0_extra != nullptr) {
        static_cast<void>(::FreeLibrary(b0_extra));
    }
    ::SetLastError(732);
    const HMODULE loaded_b2 = brq::hooked_load_library(&map_without_resolving, L"B2.dll",
                                                        reinterpret_cast<void*>(&fake_open));
    check(loaded_b2 != nullptr && map_calls == 2 && map_input_error == 732,
          "released B0 lets the safe mapper load B2 through the original loader once");
    check(brq::pending_module_release.state == nullptr && ::GetModuleHandleW(L"B0.dll") == nullptr &&
              brq::hook_slots[0].state.load(std::memory_order_acquire) == nullptr,
          "successful transition removes B0 and clears its hook slot");
    check(!brq::bridge_match_active(stale_match), "captured bridge match is inactive after retirement");
    brq::hook_slots[2].state.store(nullptr, std::memory_order_release);
    if (loaded_b2 != nullptr) {
        static_cast<void>(::FreeLibrary(loaded_b2));
    }

    b0_game = ::LoadLibraryExW(L"B0.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
    b0_owned = nullptr;
    const BOOL revisited_retained = b0_game != nullptr ? ::GetModuleHandleExW(0, L"B0.dll", &b0_owned) : FALSE;
    brq::State revisit_state{};
    revisit_state.spec = &brq::module_specs[0];
    revisit_state.game_module = b0_owned;
    revisit_state.owns_game_module_reference = revisited_retained != FALSE;
    const bool revisit_hooks = b0_game != nullptr && revisited_retained != FALSE &&
                               brq::install_segment_hooks(revisit_state, 0,
                                                          reinterpret_cast<LPVOID>(&fake_open),
                                                          reinterpret_cast<LPVOID>(&fake_file_size), nullptr, nullptr);
    revisit_state.hooks_ready.store(true, std::memory_order_release);
    check(revisit_hooks, "second visit installs fresh hooks on the same targets");
    const BOOL revisit_game_freed = b0_game != nullptr
                                        ? brq::hooked_free_library_impl(b0_game,
                                                                        reinterpret_cast<void*>(&fake_open))
                                        : FALSE;
    const HMODULE same_b0 = brq::hooked_load_library(&map_without_resolving, L"B0.dll",
                                                      reinterpret_cast<void*>(&fake_open));
    check(revisit_game_freed != FALSE && same_b0 == b0_owned && brq::pending_module_release.state == nullptr,
          "same-DLL reload cancels retirement without tearing down its hooks");
    check(brq::hook_slots[0].state.load(std::memory_order_acquire) == &revisit_state,
          "same-DLL reload keeps the current generation published");

    clear_fake_slot(0);
    revisit_state.owns_game_module_reference = false;
    if (b0_owned != nullptr) {
        static_cast<void>(::FreeLibrary(b0_owned));
    }
    if (same_b0 != nullptr) {
        static_cast<void>(::FreeLibrary(same_b0));
    }
    // B0/B4 cannot coexist at their fixed preferred base; test B4 after releasing B0.
    const HMODULE b4_game = ::LoadLibraryExW(L"B4.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
    check(b4_game != nullptr, "map real B4 after B0 retirement");
    if (b4_game != nullptr) {
        const auto b0_status = brq::pack_indexes[0].patch_state.load(std::memory_order_acquire);
        verify_hash_patch(4, b4_game);
        check(brq::pack_indexes[0].patch_state.load(std::memory_order_acquire) == b0_status,
              "B4 patch does not change B0 patch state");
        brq::State b4_state{};
        b4_state.spec = &brq::module_specs[4];
        b4_state.game_module = b4_game;
        b4_state.owns_game_module_reference = true;
        auto* const base = reinterpret_cast<std::uint8_t*>(b4_game);
        const bool segment_hooks = brq::install_segment_hooks(b4_state, 4,
            base + b4_state.spec->open_rva, base + b4_state.spec->file_size_rva,
            base + brq::read_whole_spec("B4")->rva, base + brq::glyph_lookup_spec("B4")->rva);
        const auto pack_hooks = segment_hooks ? brq::install_pack_hooks(b4_state, 4)
                                             : brq::PackHookInstallResult{};
        b4_state.hooks_ready.store(true, std::memory_order_release);
        const std::size_t b4_hook_count = 7 + 2 * static_cast<std::size_t>(std::count_if(
            brq::runtime_text_hooks.begin(), brq::runtime_text_hooks.end(),
            [](const auto& hook) { return hook.segment == "B4"; }));
        check(segment_hooks && pack_hooks.timing_installed && pack_hooks.index_patch_armed &&
                  brq::hook_slots[4].created_target_count == b4_hook_count &&
                  brq::hook_slots[4].enabled_target_count == b4_hook_count,
              "real B4 installs all translation, runtime text and three PAK hooks");
        // Simulate slow preparation after the real LoadLibrary result is available.
        brq::hook_slots[4].worker_running.store(true, std::memory_order_release);
        const ULONGLONG foreign_started = ::GetTickCount64();
        const HMODULE foreign_load = brq::hooked_load_library(&map_without_resolving, L"B4.dll", base);
        check(foreign_load == b4_game && ::GetTickCount64() - foreign_started < 200 &&
                  brq::hook_slots[4].worker_running.load(),
              "DLL-originated load never waits for preparation under a possible loader lock");
        if (foreign_load != nullptr) ::FreeLibrary(foreign_load);
        const HANDLE preparation = ::CreateThread(nullptr, 0, &finish_preparation,
            reinterpret_cast<LPVOID>(4), 0, nullptr);
        check(preparation != nullptr, "start delayed B4 preparation");
        if (preparation != nullptr) {
            const ULONGLONG load_started = ::GetTickCount64();
            const HMODULE ready_load = brq::hooked_load_library(&map_without_resolving, L"B4.dll",
                reinterpret_cast<void*>(&fake_open));
            check(ready_load == b4_game && !brq::hook_slots[4].worker_running.load() &&
                      ::GetTickCount64() - load_started >= 200,
                  "main EXE LoadLibrary waits for B4 preparation before it can read PAK zero");
            ::WaitForSingleObject(preparation, 2'000);
            ::CloseHandle(preparation);
            if (ready_load != nullptr) ::FreeLibrary(ready_load);
        }
        brq::hook_slots[4].worker_running.store(false, std::memory_order_release);
        check(brq::retire_module_release(b4_state, 4) && ::GetModuleHandleW(L"B4.dll") == nullptr &&
                  brq::hook_slots[4].state.load() == nullptr && brq::pack_indexes[4].module_base == nullptr &&
                  brq::pack_indexes[4].patch_state.load() == brq::IndexPatchState::disabled,
              "B4 retirement clears its PAK hooks and patch state");
    }
    for (std::size_t index = 1; index < 4; ++index) {
        const HMODULE game = ::LoadLibraryExW(brq::module_specs[index].file_name, nullptr, DONT_RESOLVE_DLL_REFERENCES);
        check(game != nullptr, "map real B1-B3 for the PAK hash check");
        verify_hash_patch(index, game);
        if (game != nullptr) ::FreeLibrary(game);
    }
    static_cast<void>(::MH_Uninitialize());
    if (open_thread != nullptr) {
        ::CloseHandle(open_thread);
    }
    if (retire_thread != nullptr) {
        ::CloseHandle(retire_thread);
    }
    if (entered_event != nullptr) {
        ::CloseHandle(entered_event);
    }
    if (release_event != nullptr) {
        ::CloseHandle(release_event);
    }
    std::printf("failures=%d\n", failures);
    return failures == 0 ? 0 : 1;
}

#undef TEST_NOINLINE
