from core.encoder_pipe import tool_paths, exhale_preset_for_mono

def test_tools_present():
    p = tool_paths()
    assert p["exhale"].endswith("exhale.exe")
    assert p["fdkaac"].endswith("fdkaac.exe")

def test_exhale_preset_map():
    assert exhale_preset_for_mono(18) == "a"
    assert exhale_preset_for_mono(24) == "0"
    assert exhale_preset_for_mono(21) == "b"
