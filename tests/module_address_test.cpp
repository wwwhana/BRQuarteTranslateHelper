#include <windows.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "module_specs.hpp"

int check_loader(const char* asi_path) {
    HMODULE b0 = ::LoadLibraryExW(L"B0.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
    if (!b0) {
        std::printf("FAIL: map B0, error=%lu\n", ::GetLastError());
        return 1;
    }
    HMODULE asi = ::LoadLibraryA(asi_path);
    if (!asi) {
        std::printf("FAIL: load ASI, error=%lu\n", ::GetLastError());
        return 1;
    }
    const auto initialize = reinterpret_cast<void (*)()>(::GetProcAddress(asi, "InitializeASI"));
    if (!initialize) {
        std::puts("FAIL: InitializeASI is missing");
        return 1;
    }
    initialize();
    const auto started = ::GetTickCount64();
    bool installed{};
    do {
        std::uint8_t first_byte{};
        SIZE_T read{};
        const auto* target = reinterpret_cast<const std::uint8_t*>(b0) + brq::b0_index_spec.pack_lookup_rva;
        installed = ::ReadProcessMemory(::GetCurrentProcess(), target, &first_byte, 1, &read) &&
                    read == 1 && first_byte == 0xe9;
        if (!installed) {
            ::Sleep(10);
        }
    } while (!installed && ::GetTickCount64() - started < 10'000);
    if (!installed) {
        std::puts("FAIL: B0 hooks were not installed; see the log beside this test executable");
        return 1;
    }
    std::puts("PASS: installed ASI hooks on mapped B0 (no B0 code executed)");
    const BOOL freed = ::FreeLibrary(b0);
    const DWORD error = ::GetLastError();
    const HMODULE remaining = ::GetModuleHandleW(L"B0.dll");
    std::printf("game_free=%d error=%lu remaining_B0=%p\n", freed, error, static_cast<void*>(remaining));
    HMODULE b2 = ::LoadLibraryExW(L"B2.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
    const DWORD load_error = b2 ? ERROR_SUCCESS : ::GetLastError();
    std::printf("B2=%p error=%lu\n", static_cast<void*>(b2), load_error);
    if (b2) {
        ::FreeLibrary(b2);
    }
    const bool passed = freed && !remaining && b2;
    std::puts(passed ? "PASS: loader allows B0 to B2 transition" : "FAIL: loader prevents B0 to B2 transition");
    // ASI and its worker threads are process-lifetime. Let process teardown end them.
    return passed ? 0 : 1;
}

// Run from the game directory. Map the real images without executing their
// entry points or resolving imports; no game code, audio, or saves are touched.
int main(int argc, char** argv) {
    ::SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX);
    if (argc == 3 && std::strcmp(argv[1], "--loader") == 0) {
        return check_loader(argv[2]);
    }
    if (argc != 1) {
        std::puts("Usage: module_address_test.exe [--loader path-to-embedded-ASI]");
        return 2;
    }
    constexpr std::array<const wchar_t*, 4> names{L"B1.dll", L"B2.dll", L"B3.dll", L"B4.dll"};
    constexpr std::uintptr_t preferred_base = 0x180000000;
    int failures{};
    const auto check = [&](bool ok, const char* label) {
        std::printf("%s: %s\n", ok ? "PASS" : "FAIL", label);
        failures += !ok;
    };
    for (std::size_t index = 0; index < names.size(); ++index) {
        std::printf("\nB%zu\n", index + 1);
        HMODULE baseline = ::LoadLibraryExW(names[index], nullptr, DONT_RESOLVE_DLL_REFERENCES);
        const DWORD baseline_error = baseline ? ERROR_SUCCESS : ::GetLastError();
        std::printf("baseline=%p error=%lu\n", static_cast<void*>(baseline), baseline_error);
        check(index == 0 ? baseline != nullptr : reinterpret_cast<std::uintptr_t>(baseline) == preferred_base,
              "baseline image loads at an allowed address");
        if (baseline) {
            check(::FreeLibrary(baseline) != FALSE, "release baseline");
        }

        HMODULE b0 = ::LoadLibraryExW(L"B0.dll", nullptr, DONT_RESOLVE_DLL_REFERENCES);
        check(reinterpret_cast<std::uintptr_t>(b0) == preferred_base, "B0 preferred address");
        if (!b0) {
            continue;
        }
        HMODULE retained{};
        check(::GetModuleHandleExW(0, L"B0.dll", &retained) != FALSE, "retain B0 like loader");
        check(::FreeLibrary(b0) != FALSE, "game releases B0");
        check(::GetModuleHandleW(L"B0.dll") == retained && retained != nullptr, "extra reference keeps B0 loaded");

        HMODULE occupied = ::LoadLibraryExW(names[index], nullptr, DONT_RESOLVE_DLL_REFERENCES);
        const DWORD occupied_error = occupied ? ERROR_SUCCESS : ::GetLastError();
        std::printf("with_retained_B0=%p error=%lu\n", static_cast<void*>(occupied), occupied_error);
        check(index == 0 ? occupied != nullptr && occupied != retained
                         : occupied == nullptr && occupied_error != ERROR_SUCCESS,
              index == 0 ? "B1 can move" : "fixed-address image cannot load beside B0");
        if (occupied) {
            check(::FreeLibrary(occupied) != FALSE, "release moved image");
        }
        if (retained) {
            check(::FreeLibrary(retained) != FALSE, "release loader reference");
        }
        check(::GetModuleHandleW(L"B0.dll") == nullptr, "B0 is now unloaded");

        HMODULE released = ::LoadLibraryExW(names[index], nullptr, DONT_RESOLVE_DLL_REFERENCES);
        const DWORD released_error = released ? ERROR_SUCCESS : ::GetLastError();
        std::printf("after_B0_release=%p error=%lu\n", static_cast<void*>(released), released_error);
        check(index == 0 ? released != nullptr : reinterpret_cast<std::uintptr_t>(released) == preferred_base,
              "target loads after B0 release");
        if (released) {
            check(::FreeLibrary(released) != FALSE, "release final image");
        }
    }
    std::printf("\nfailures=%d\n", failures);
    return failures == 0 ? 0 : 1;
}
