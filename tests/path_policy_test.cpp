#ifdef NDEBUG
#undef NDEBUG
#endif

#include "path_policy.hpp"
#include "replacement_log.hpp"

#include <cassert>

int main() {
    using brq::AssetKind;
    using brq::FontOpenPlan;
    assert(brq::classify_lookup_key("event/b1/balloonsel/balloonseldata_jp.bsb", "B1") == AssetKind::event);
    assert(brq::is_japanese_event_namespace("event/b1/balloonsel/balloonseldata_jp.bsb", "B1"));
    assert(brq::classify_lookup_key("event/b1/balloonsel/balloonseldata_en.bsb", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("event/b1/balloonsel/balloonseldata_jp.bsb", "B2") == AssetKind::none);

    assert(brq::font_open_plan(false, false) == FontOpenPlan::original);
    assert(brq::font_open_plan(false, true) == FontOpenPlan::replacement);
    assert(brq::font_open_plan(true, false) == FontOpenPlan::fail_closed);
    assert(brq::font_open_plan(true, true) == FontOpenPlan::replacement);
    assert(!brq::uses_root_file_bridge("B0"));
    assert(!brq::uses_root_file_bridge("B1"));
    assert(brq::uses_root_file_bridge("B2"));
    assert(brq::uses_root_file_bridge("B3"));
    assert(brq::uses_root_file_bridge("B4"));
    assert(brq::original_fits_buffer(8, 8));
    assert(brq::original_fits_buffer(8, 16));
    assert(!brq::original_fits_buffer(0, 16));
    assert(!brq::original_fits_buffer(17, 16));

    assert(brq::clean_path("ROOT:\\Event\\B0\\event_JP\\a.ebm") == "Event/B0/event_JP/a.ebm");
    assert(brq::clean_path("rEs:////Font\\MainFont00_HD1080_0.g1t") == "Font/MainFont00_HD1080_0.g1t");
    assert(brq::clean_path("RES:Res_JP/Font/MainFont00_HD1080_0.g1t") ==
           "Font/MainFont00_HD1080_0.g1t");
    assert(brq::make_lookup_key("Event/B4/event_JP/EV_001.EBM") == "event/b4/event_jp/ev_001.ebm");
    assert(brq::font_slot_for_request("res:\\Font\\MainFont00_HD1080_0.g1t", "B0") == 0);
    assert(brq::font_slot_for_request("RES:Res_JP/Font/MainFont00_HD1080_0.g1t", "B0") == 0);
    assert(brq::font_slot_for_request("ROOT:////font/mainfont02_hd1080_0.g1t", "B4") == 2);
    assert(brq::font_slot_for_request("disc:/Font/MainFontHD720_0.g1t", "B1") == 0);
    assert(brq::font_slot_for_request("Font/MainFontHD720_0.g1t", "B0") == 0xff);
    assert(brq::font_slot_for_request("Font/SubFont_hd1080.g1t", "B0") == 0xff);
    for (const auto path : {"RES:LAYOUT/default.g1n", "RES:Res_JP/Layout/default.g1n",
                            "ROOT:\\data\\b1\\x64\\res_jp\\layout\\default.g1n"}) {
        const auto key = brq::make_lookup_key(brq::clean_path(path));
        assert(key == "layout/default.g1n");
        assert(brq::classify_lookup_key(key, "B1") == AssetKind::font_bundle);
        assert(brq::classify_lookup_key(key, "B0") == AssetKind::none);
        assert(brq::font_slot_for_request(path, "B1") == 0xff);
    }
    assert(brq::classify_lookup_key("res_en/layout/default.g1n", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("layout/other.g1n", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("layout/../default.g1n", "B1") == AssetKind::none);

    assert(brq::is_safe_manifest_path("Event/B0/event_JP/a.ebm"));
    assert(!brq::is_safe_manifest_path("Event/B0/event_JP/../a.ebm"));
    assert(!brq::is_safe_manifest_path("Event/B0//event_JP/a.ebm"));
    assert(!brq::is_safe_manifest_path("Event\\B0\\event_JP\\a.ebm"));
    assert(!brq::is_safe_manifest_path("C:/outside.ebm"));
    assert(!brq::is_safe_manifest_path("Event/B0/event_JP/bad. /a.ebm"));
    assert(!brq::is_safe_manifest_path("Event/B0/event_JP/bad.. /a.ebm"));
    assert(!brq::is_safe_manifest_path("Event/B0/event_JP/bad\x7f" "/a.ebm"));

    assert(brq::replacement_log_message("B1", "Event/B1/event_JP/a.ebm", false) ==
           "Event/B1/event_JP/a.ebm => BRQuarteTranslateHelper-assets/B1/Event/B1/event_JP/a.ebm");
    assert(brq::replacement_log_message("B1", "Event/B1/event_JP/a.ebm", true) ==
           "Event/B1/event_JP/a.ebm => BRQuarteTranslateHelper.asi!/B1/Event/B1/event_JP/a.ebm");
    assert(brq::is_japanese_event_namespace("event/b0/event_jp/a.ebd", "B0"));
    assert(brq::is_segment_text_namespace("saves/b1/text_jp/strcombineall.xml", "B1"));
    assert(brq::is_japanese_text_namespace("saves/b1/text_jp/strcombineall.xml", "B1"));
    assert(!brq::is_japanese_text_namespace("saves/b1/text_en/strcombineall.xml", "B1"));
    assert(!brq::korean_text_committed_after_open(false, AssetKind::text, true, false));
    assert(!brq::korean_text_committed_after_open(false, AssetKind::text, false, true));
    assert(!brq::korean_text_committed_after_open(false, AssetKind::font, true, true));
    assert(brq::korean_text_committed_after_open(false, AssetKind::text, true, true));
    assert(brq::korean_text_committed_after_open(false, AssetKind::event, true, true));
    assert(brq::korean_text_committed_after_open(true, AssetKind::text, false, true));
    assert(brq::korean_text_committed_after_open(true, AssetKind::text, false, false));
    assert(brq::classify_lookup_key("event/b0/event_jp/a.ebm", "B0") == AssetKind::event);
    assert(brq::classify_lookup_key("event/b0/event_jp/a.ebd", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("event/b1/event_jp/a.ebm", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("event/b0/event_en/a.ebm", "B0") == AssetKind::none);

    assert(brq::classify_lookup_key("font/mainfont00_hd1080_0.g1t", "B0") == AssetKind::font);
    assert(brq::classify_lookup_key("font/mainfonthd720_0.g1t", "B1") == AssetKind::font);
    assert(brq::classify_lookup_key("font/subfont_hd720.g1t", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("font/subfont_hd1080.g1t", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("font/externalchars_hd1080_0.g1t", "B4") == AssetKind::none);
    assert(brq::classify_lookup_key("font/mainfont00_hd1080_0.g1t", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("font/unrelated.g1t", "B0") == AssetKind::none);
    assert(brq::is_graphics_path("data/b4/x64/res_jp/ui/b0_title.g1t", "B0"));
    assert(brq::is_graphics_path("res_jp/ipu/logo_kt.g1t", "B0"));
    assert(brq::classify_lookup_key("data/b4/x64/res_jp/ui/b0_title.g1t", "B0") == AssetKind::image);
    assert(brq::classify_lookup_key("data/b4/x64/res_jp/ui/other.g1t", "B4") == AssetKind::image);
    assert(!brq::is_graphics_path("data/b4/x64/res_jp/ipu/title.g1t", "B0"));
    assert(!brq::is_graphics_path("data/b3/x64/res_jp/ui/other.g1t", "B2"));
    assert(!brq::is_graphics_path("data/b2/x64/res_jp/ui/other.png", "B2"));
    assert(!brq::is_graphics_path("data/b2/x64/res_jp/font/other.g1t", "B2"));
    assert(!brq::is_graphics_path("data/b2/x64/res_cmn/ui/bad. /other.g1t", "B2"));
    assert(brq::classify_lookup_key("data/b2/x64/res_cmn/ui/other.g1t", "B2") == AssetKind::image);
    assert(brq::classify_lookup_key("data/b2/x64/res_jp/ipu/other.g1t", "B2") == AssetKind::image);
    assert(brq::classify_lookup_key("data/b2/x64/res_jp/layout/other.g1t", "B2") == AssetKind::image);
    assert(brq::is_graphics_path("DATA/B2/X64/RES_CMN/UI/other.G1T", "b2"));
    assert(brq::classify_lookup_key("saves/b0/text_jp/strcombineall.xml", "B0") == AssetKind::text);
    assert(brq::classify_lookup_key("saves/b0/text_jp/strcombineall.xml.e", "B0") == AssetKind::text);
    assert(brq::classify_lookup_key("saves/b0/event/eventglossarykeyword.xml", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("saves/b0/text_jp/strcombineall.xml", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("saves/b0/text_en/strcombineall.xml", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("saves/b1/text_jp/strcombineall.xml", "B1") == AssetKind::text);
    assert(brq::classify_lookup_key("saves/b1/text_jp/strcombineall.xml.e", "B1") == AssetKind::text);
    assert(brq::classify_lookup_key("saves/b1/event/eventglossarykeyword.xml", "B1") == AssetKind::text);
    assert(brq::classify_lookup_key("data/b1/x64/res_jp/ipu/headsup.g1t", "B1") == AssetKind::image);
    assert(brq::classify_lookup_key("res_jp/ipu/logo_kt.g1t", "B0") == AssetKind::image);
    assert(brq::classify_lookup_key("res_jp/ipu/logo_gust.g1t", "B0") == AssetKind::none);
    assert(brq::classify_lookup_key("data/b1/x64/res_jp/ipu/logo_gust.g1t", "B1") == AssetKind::image);
    assert(brq::classify_lookup_key("data/b1/x64/res_jp/other/logo_gust.g1t", "B1") == AssetKind::none);
    for (const auto segment : {std::string_view{"B2"}, std::string_view{"B3"}, std::string_view{"B4"}}) {
        std::string prefix{"saves/b0/"};
        prefix[7] = segment[1];
        assert(brq::classify_lookup_key(prefix + "text_jp/strcombineall.xml", segment) == AssetKind::text);
        assert(brq::classify_lookup_key(prefix + "text_jp/strcombineall.xml.e", segment) == AssetKind::text);
        assert(brq::classify_lookup_key(prefix + "event/eventglossarykeyword.xml", segment) == AssetKind::text);
        assert(brq::classify_lookup_key(prefix + "ui_cmn/seed_cabinet/uil_seed_cabinet_cell.xml", segment) ==
               AssetKind::text);
        assert(brq::classify_lookup_key(prefix + "ui_cmn/seed_dlc_battle/uil_seed_dlc_battle.xml", segment) ==
               AssetKind::text);
        assert(brq::classify_lookup_key(prefix + "text_en/strcombineall.xml", segment) == AssetKind::none);
        assert(brq::classify_lookup_key(prefix + "ui_cmn/other.xml", segment) == AssetKind::text);
    }
    for (const auto path : {"saves/b1/text_jp/str_ui.xml", "saves/b1/text_jp/str_ui.xml.e",
                            "saves/b1/ui/scl_system/confirm.xml", "saves/b1/ui/scl_system/confirm.xml.e"}) {
        assert(brq::classify_lookup_key(path, "B1") == AssetKind::text);
        assert(brq::classify_lookup_key(path, "B2") == AssetKind::none);
    }
    assert(brq::classify_lookup_key("saves/b1/ui/../../other.xml", "B1") == AssetKind::none);
    assert(brq::classify_lookup_key("saves/b1/ui/other.bin", "B1") == AssetKind::none);
    for (const auto path : {"RES:Layout/99_cmn_dialog.kscl", "res:Res_JP/Layout/99_cmn_dialog.kscl",
                            "data/b1/x64/res_jp/layout/99_cmn_dialog.kscl"}) {
        const auto key = brq::make_lookup_key(brq::clean_path(path));
        assert(key == "data/b1/x64/res_jp/layout/99_cmn_dialog.kscl");
        assert(brq::classify_lookup_key(key, "B1") == AssetKind::text);
        assert(brq::classify_lookup_key(key, "B2") == AssetKind::none);
    }
    assert(brq::classify_lookup_key("data/b1/x64/res_jp/layout/../bad.kscl", "B1") == AssetKind::none);
    return 0;
}
