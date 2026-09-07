"""Dev helper: print advisor matrix. Pipe encoders (exhale/fdkaac) show pipe routing, not ffmpeg -c:a."""
from core.advisor import advise_for_sample
from core.encoder import check_ffmpeg

avail = check_ffmpeg()
print("avail:", avail)
base = {'codec': 'mp3', 'bitrate_kbps': 32.0, 'sample_rate': 22050, 'channels': 1,
        'duration_sec': 34116, 'cutoff_hz': 8311, 'speech_score': 0.95, 'noise_floor_db': -60}
for ct in ['tts_monotone', 'natural_voice', 'full_cast']:
    i = dict(base); i['content'] = ct
    r = advise_for_sample(i, avail=avail, content=ct)
    print(ct, "to", r['target_kbps'], r['encoder'], r['profile'], r['sample_rate'], r['channels'],
          "|", r['reason'], "| est", r['est_size_mb'], "MB")
    if r['encoder'] in ("exhale", "fdkaac", "libfdk_aac"):
        print("   PIPE: ffmpeg decode+filters ->", r['encoder'], "-> out.m4b (see core/encoder_pipe.py)")
    else:
        from core.encoder import build_ffmpeg_cmd
        print("   CMD:", " ".join(build_ffmpeg_cmd("in.mp3", "out.m4b", r)))
cases = [(64, 44100, 2, 'natural_voice'), (128, 44100, 2, 'full_cast'),
         (640, 44100, 2, 'full_cast'), (128, 44100, 1, 'tts_monotone')]
for br, sr, ch, ct in cases:
    cutoff = 15000 if br > 64 else 10000
    i = {'bitrate_kbps': br, 'sample_rate': sr, 'channels': ch, 'duration_sec': 36000,
         'cutoff_hz': cutoff, 'speech_score': 0.9, 'content': ct}
    r = advise_for_sample(i, avail=avail, content=ct)
    print("src", br, "k", sr, ch, ct, "to", r["target_kbps"], "k",
          r["encoder"], r["profile"], r["sample_rate"], r["channels"], "|", r["reason"])
