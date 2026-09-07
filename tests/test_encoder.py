from core.encoder import build_ffmpeg_cmd
from core.advisor import advise_for_sample, SAMPLE_32K_MONO

def test_cmd_native_lc_fallback_this_machine():
    rec = advise_for_sample(SAMPLE_32K_MONO, avail={}, content="natural_voice")
    # empty avail -> no HE tools -> native LC fallback, safe for direct ffmpeg
    assert rec["encoder"] == "aac", rec
    cmd = build_ffmpeg_cmd("in.mp3", "out.m4b", rec)
    s = " ".join(cmd)
    assert "-ar 22050" in s and "-ac 1" in s and "lowpass" in s and "-c:a aac" in s and "-cutoff" in s, s

def test_cmd_he_must_use_pipe():
    rec = advise_for_sample(SAMPLE_32K_MONO, avail={"exhale": True, "fdkaac": True}, content="natural_voice")
    assert rec["encoder"] == "exhale", rec
    try:
        build_ffmpeg_cmd("in.mp3", "out.m4b", rec)
    except ValueError as ex:
        assert "must go via core.encoder_pipe" in str(ex)
        return
    raise AssertionError("expected ValueError for exhale via build_ffmpeg_cmd")
