#ifdef NDEBUG
#undef NDEBUG
#endif

#include "b0_index_hash.hpp"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

struct Inventory final {
    std::vector<std::string> paths;
    std::size_t declared_count{};
};

bool decode_name(std::string_view line, std::string& decoded) {
    const auto name = line.find("\"name\"");
    if (name == std::string_view::npos) {
        return false;
    }
    const auto colon = line.find(':', name + 6);
    auto cursor = colon == std::string_view::npos ? colon : line.find('"', colon + 1);
    if (cursor == std::string_view::npos) {
        return false;
    }
    ++cursor;
    decoded.clear();
    while (cursor < line.size()) {
        const char value = line[cursor++];
        if (value == '"') {
            return true;
        }
        if (value != '\\') {
            decoded.push_back(value);
            continue;
        }
        if (cursor >= line.size()) {
            return false;
        }
        switch (const char escaped = line[cursor++]) {
            case '\\':
            case '"':
            case '/':
                decoded.push_back(escaped);
                break;
            case 'b':
                decoded.push_back('\b');
                break;
            case 'f':
                decoded.push_back('\f');
                break;
            case 'n':
                decoded.push_back('\n');
                break;
            case 'r':
                decoded.push_back('\r');
                break;
            case 't':
                decoded.push_back('\t');
                break;
            default:
                return false;
        }
    }
    return false;
}

Inventory load_inventory(const std::filesystem::path& path) {
    std::ifstream input{path};
    assert(input);
    Inventory inventory;
    std::string line;
    std::string decoded;
    while (std::getline(input, line)) {
        if (const auto count = line.find("\"nb_files\""); count != std::string::npos) {
            const auto value = line.find("0x", count);
            assert(value != std::string::npos);
            inventory.declared_count = std::stoull(line.substr(value), nullptr, 16);
        }
        if (decode_name(line, decoded) && !decoded.empty() && decoded.front() == '\\') {
            inventory.paths.push_back(decoded);
        }
    }
    assert(inventory.declared_count != 0);
    assert(inventory.paths.size() == inventory.declared_count);
    return inventory;
}

class IndexModel final {
  public:
    void insert(std::string path, std::size_t value) {
        const auto bucket = brq::b0_index_bucket(path);
        int current = heads_[bucket];
        while (current >= 0) {
            ++node_visits_;
            auto& node = nodes_[static_cast<std::size_t>(current)];
            if (node.path.size() == path.size()) {
                ++equal_length_comparisons_;
                if (node.path == path) {
                    node.value = value;
                    return;
                }
            }
            current = node.next;
        }
        nodes_.push_back({std::move(path), value, heads_[bucket]});
        heads_[bucket] = static_cast<int>(nodes_.size() - 1);
    }

    std::optional<std::size_t> lookup(std::string_view path) const {
        int current = heads_[brq::b0_index_bucket(path)];
        while (current >= 0) {
            const auto& node = nodes_[static_cast<std::size_t>(current)];
            if (node.path == path) {
                return node.value;
            }
            current = node.next;
        }
        return std::nullopt;
    }

    std::size_t maximum_bucket_size() const {
        std::array<std::size_t, 128> sizes{};
        for (const auto& node : nodes_) {
            ++sizes[brq::b0_index_bucket(node.path)];
        }
        std::size_t maximum{};
        for (const auto size : sizes) {
            maximum = size > maximum ? size : maximum;
        }
        return maximum;
    }

    std::size_t node_visits() const noexcept {
        return node_visits_;
    }

    std::size_t equal_length_comparisons() const noexcept {
        return equal_length_comparisons_;
    }

  private:
    struct Node final {
        std::string path;
        std::size_t value{};
        int next{-1};
    };

    std::array<int, 128> heads_ = [] {
        std::array<int, 128> result{};
        result.fill(-1);
        return result;
    }();
    std::vector<Node> nodes_;
    std::size_t node_visits_{};
    std::size_t equal_length_comparisons_{};
};

void verify_edge_cases() {
    assert(brq::b0_index_hash("") == 0x811c9dc5U);
    assert(brq::b0_index_hash("a") == 0xe40c292cU);
    assert(brq::b0_index_hash("foobar") == 0xbf9cf968U);
    assert(brq::b0_index_hash("\\path") != brq::b0_index_hash("path"));
    assert(brq::b0_index_hash("\\PATH") != brq::b0_index_hash("\\path"));

    constexpr char high_byte[]{static_cast<char>(0x80), '\0'};
    assert(brq::b0_index_hash(std::string_view{high_byte, 1}) ==
           ((brq::b0_index_hash_offset ^ 0x80U) * brq::b0_index_hash_prime));
    constexpr char nul_terminated[]{'\\', 'a', '\0', 'x', '\0'};
    assert(brq::b0_index_bucket_c_string(nul_terminated) == brq::b0_index_bucket(std::string_view{nul_terminated, 2}));

    IndexModel model;
    model.insert("\\same", 1);
    model.insert("\\Same", 2);
    model.insert("/same", 3);
    model.insert("\\same", 4);
    assert(model.lookup("\\same") == 4);
    assert(model.lookup("\\Same") == 2);
    assert(model.lookup("/same") == 3);
    assert(!model.lookup("\\missing"));
}

void verify_inventory(const std::filesystem::path& path, std::size_t& total_paths) {
    const auto inventory = load_inventory(path);
    IndexModel model;
    std::unordered_map<std::string, std::size_t> reference;
    for (std::size_t index = 0; index < inventory.paths.size(); ++index) {
        assert(brq::b0_index_bucket_c_string(inventory.paths[index].c_str()) ==
               brq::b0_index_bucket(inventory.paths[index]));
        model.insert(inventory.paths[index], index);
        reference[inventory.paths[index]] = index;
    }
    for (const auto& [name, value] : reference) {
        assert(model.lookup(name) == value);
    }
    std::string missing{"\\__brq_missing__\\not_found.bin"};
    while (reference.contains(missing)) {
        missing.push_back('_');
    }
    assert(!model.lookup(missing));

    if (path.filename() == "PACK01_03.json") {
        assert(inventory.paths.size() == 23'348);
        assert(model.maximum_bucket_size() == 221);
        assert(model.node_visits() == 2'128'750);
        assert(model.equal_length_comparisons() == 1'289'521);
    }
    total_paths += inventory.paths.size();
    std::cout << path.filename().string() << ": " << inventory.paths.size() << " paths, "
              << model.node_visits() << " build visits, max bucket " << model.maximum_bucket_size() << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    assert(argc == 1 || argc == 2);
    verify_edge_cases();
    if (argc == 1) {
        std::cout << "synthetic index checks passed\n";
        return 0;
    }
    const std::filesystem::path inventory_root{argv[1]};
    assert(std::filesystem::is_directory(inventory_root));

    std::vector<std::filesystem::path> inventories;
    for (const auto& entry : std::filesystem::directory_iterator{inventory_root}) {
        if (entry.is_regular_file() && entry.path().extension() == ".json") {
            inventories.push_back(entry.path());
        }
    }
    std::sort(inventories.begin(), inventories.end());
    assert(!inventories.empty());
    std::size_t total_paths{};
    for (const auto& path : inventories) {
        verify_inventory(path, total_paths);
    }
    std::cout << "verified " << total_paths << " paths from " << inventories.size() << " inventories\n";
    return 0;
}
