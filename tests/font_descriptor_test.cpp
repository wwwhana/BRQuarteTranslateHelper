#ifdef NDEBUG
#undef NDEBUG
#endif

#include "module_specs.hpp"

#include <cassert>

int main() {
    using brq::B1FontDescriptorPlan;
    using brq::b1_font_descriptor_plan;

    assert(b1_font_descriptor_plan("B1", brq::b1_main_font_key, 2048, 1024, 2565) ==
           B1FontDescriptorPlan::set_replacement);
    assert(b1_font_descriptor_plan("B1", brq::b1_main_font_key, 2048, 2048, 2565) ==
           B1FontDescriptorPlan::keep_replacement);
    assert(b1_font_descriptor_plan("B1", brq::b1_main_font_key, 2048, 1536, 2565) ==
           B1FontDescriptorPlan::reject);
    assert(b1_font_descriptor_plan("B1", brq::b1_main_font_key, 2048, 1024, 2564) ==
           B1FontDescriptorPlan::reject);
    assert(b1_font_descriptor_plan("B2", brq::b1_main_font_key, 2048, 1024, 2565) ==
           B1FontDescriptorPlan::not_applicable);
    assert(b1_font_descriptor_plan("B1", "font/subfont_hd720.g1t", 2048, 1024, 2565) ==
           B1FontDescriptorPlan::not_applicable);
    assert(brq::read_whole_specs[0].rva == 0x004e4540);
    assert(brq::read_whole_specs[1].rva == 0x004e7360);
    assert(brq::read_whole_specs[2].rva == 0x004ecba0);
    return 0;
}
