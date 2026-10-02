#pragma once
#include <array>
#include "source_pack.hpp"
namespace brq {
// Module-only builds have no translation inputs to fingerprint.
inline constexpr std::array<SourcePack, 0> source_pack_fingerprints{};
}
