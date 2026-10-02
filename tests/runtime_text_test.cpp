#ifdef NDEBUG
#undef NDEBUG
#endif
#include "runtime_text.hpp"
#include <cassert>

int main() {
    const std::array<brq::RuntimeText, 3> rows{{
        {"B4", "Unavailable", "Indisponible"},
        {"B4", "Recipe%d unlocked", "Recette %d disponible"},
        {"B0", "Unavailable", "Missing"},
    }};
    assert(brq::translate_runtime_text("Unavailable", "B4", rows) == "Indisponible");
    assert(brq::translate_runtime_text("Unavailable", "B0", rows) == "Missing");
    assert(brq::translate_runtime_text("Unavailable", "B1", rows).empty());
    assert(brq::translate_runtime_text("Recipe123 unlocked", "B4", rows) == "Recette 123 disponible");
    assert(brq::translate_runtime_text("Recipe-1 unlocked", "B4", rows) == "Recette -1 disponible");
    for (const auto text : {"Recipe unlocked", "Recipe- unlocked", "Recipe1x unlocked", "Recipe1 unlocked!", "Other"}) {
        assert(brq::translate_runtime_text(text, "B4", rows).empty());
    }
}
