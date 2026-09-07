from core.advisor import advise_for_sample, SAMPLE_32K_MONO

HE = {"libfdk_aac": True, "exhale": False, "aac": True}
LC = {"libfdk_aac": False, "exhale": False, "aac": True}

def test_lowrate_mono_he():
    rec = advise_for_sample(SAMPLE_32K_MONO, avail=HE, content="natural_voice")
    assert rec["target_kbps"] == 24, rec
    assert rec["sample_rate"] == 22050
    assert rec["channels"] == 1
    assert rec["container"] == "m4b"
    # ~9.5hr @24k ~= 97-105MB
    assert 80 < rec["est_size_mb"] < 120, rec

def test_lowrate_mono_lc_fallback_no_fdk():
    # no HE tools -> LC32 fallback (same size, stable, no thin 24k LC)
    rec = advise_for_sample(SAMPLE_32K_MONO, avail=LC, content="natural_voice")
    assert rec["target_kbps"] == 32, rec
    assert rec["encoder"] == "aac", rec

def test_tts_monotone_low():
    info = dict(SAMPLE_32K_MONO); info["content"] = "tts_monotone"
    rec = advise_for_sample(info, avail=LC, content="tts_monotone")
    assert 24 <= rec["target_kbps"] <= 32, rec
    rec_he = advise_for_sample(info, avail=HE, content="tts_monotone")
    assert 16 <= rec_he["target_kbps"] <= 22, rec_he

def test_full_cast_preserves_stereo():
    info = {"bitrate_kbps": 128, "sample_rate": 44100, "channels": 2, "duration_sec": 36000,
            "cutoff_hz": 15000, "speech_score": 0.5, "content": "full_cast"}
    rec = advise_for_sample(info, avail=LC, content="full_cast")
    assert rec["channels"] == 2 and rec["sample_rate"] == 44100, rec
    assert rec["target_kbps"] == 80, rec

def test_full_cast_low_src_no_bloat():
    info = dict(SAMPLE_32K_MONO); info["content"] = "full_cast"
    rec = advise_for_sample(info, avail=LC, content="full_cast")
    assert rec["target_kbps"] <= 48, rec  # 32k->64k bloat guard

def test_manual_override():
    rec = advise_for_sample(SAMPLE_32K_MONO, manual_kbps=32)
    assert rec["target_kbps"] == 32
