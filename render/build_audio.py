"""Audio stage: VO -> trimmed lines -> timeline.json (audio drives everything) -> music bed + SFX -> final mix.
Deterministic: same input => same timeline, so CI and the sandbox agree to the millisecond.
Needs ffmpeg (pip install imageio-ffmpeg).
"""
import json, os, subprocess, sys, wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AU = os.path.join(ROOT, "audio")
SR = 48000
FPS = 30
TARGET = 60.0
LEAD, TAIL = 0.55, 1.15
GAP = 0.42
LINES = [
    "South America holds twelve independent nations. One of them is half the continent's economy.",
    "This is Brazil: eight point four million square kilometres. The fifth largest country on Earth.",
    "It touches ten neighbours. Every South American country except Chile and Ecuador. Plus France, which still keeps a slice of Amazonian coastline.",
    "Forty percent of everything Brazil sells abroad is food.",
    "Fifty-eight percent of the world's exported soybeans. Three quarters of its orange juice. Nearly half of its sugar.",
    "Yet crops cover just seven percent of the national territory. India plants on fifty-two. France on thirty-one.",
    "The rest stays wild. Fifty-nine percent of Brazil is forest, and the Amazon carries more water to the sea than any river alive.",
    "Two hundred and twelve million people. Eighty-eight percent of them in cities.",
    "Brazil doesn't just occupy a continent. It supplies one.",
]
# which numeric "reveal" belongs to each line (drives counters + boom SFX)
REVEAL = [12, 8.4, 10, 40, 58, 7, 59, 212, None]


def ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    from shutil import which
    f = which("ffmpeg")
    if f:
        return f
    raise SystemExit("ffmpeg not found: pip install imageio-ffmpeg")


def read_wav(path):
    w = wave.open(path)
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    assert w.getframerate() == SR, f"{path} is {w.getframerate()}Hz, want {SR}"
    if w.getnchannels() > 1:
        x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x


def trim(x, thr=0.006):
    """edge-trim silence + 40ms fades so nothing clicks."""
    a = np.abs(x)
    env = np.lib.stride_tricks.sliding_window_view(a, 480).mean(axis=1)
    on = np.where(env > thr)[0]
    if len(on) == 0:
        return x
    s, e = max(0, on[0] - 24), min(len(x), (on[-1] + 480) * 1)
    y = x[s:e].copy()
    f = int(0.012 * SR)
    if len(y) > 2 * f:
        y[:f] *= np.linspace(0, 1, f)
        y[-f:] *= np.linspace(1, 0, f)
    return y


def decode_all(ff):
    os.makedirs(os.path.join(AU, "wav"), exist_ok=True)
    out = []
    for i in range(1, 10):
        mp3 = os.path.join(AU, f"vo_{i:02d}.mp3")
        wav = os.path.join(AU, "wav", f"vo_{i:02d}.wav")
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", mp3,
                        "-ac", "1", "-ar", str(SR), wav], check=True)
        x = trim(read_wav(wav))
        out.append(x)
    return out


def music_bed(n, rng):
    """Docu bed: sub drone + slow pulse + airy noise, ducked hard under speech (lesson
    from the reference video, whose music sat only 4.8 dB below the narration)."""
    t = np.arange(n) / SR
    drone = (np.sin(2 * np.pi * 55 * t) * 0.5 + np.sin(2 * np.pi * 82.4 * t) * 0.28
             + np.sin(2 * np.pi * 110 * t) * 0.16)
    lfo = 0.55 + 0.45 * np.sin(2 * np.pi * 0.08 * t)
    drone *= lfo
    bpm, beat = 92.0, SR * 60 / 92.0
    pulse = np.zeros(n)
    for b in range(int(n / beat)):
        i = int(b * beat)
        ln = min(int(beat * 0.55), n - i)
        if ln <= 0:
            break
        e = np.exp(-np.arange(ln) / (SR * 0.10))
        pulse[i:i + ln] += np.sin(2 * np.pi * 41 * np.arange(ln) / SR) * e * 0.9
    air = np.convolve(rng.standard_normal(n), np.ones(200) / 200.0, "same")
    air *= 0.035 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t))
    bed = drone * 0.16 + pulse * 0.10 + air
    bed *= np.minimum(1, t / 2.0) * np.minimum(1, (n / SR - t) / 1.6)   # in/out ramps
    return bed


def sfx(n, tl):
    """whoosh into every line, boom on every numeric reveal."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    rng = np.random.default_rng(11)
    for ln in tl:
        i = int(max(0, (ln["start"] - 0.28)) * SR)
        L = int(0.26 * SR)
        if i + L < n:
            e = np.linspace(0, 1, L) ** 2
            out[i:i + L] += np.convolve(rng.standard_normal(L), np.ones(24) / 24, "same") * e * 0.16
        if ln.get("reveal") is not None:
            j = int(ln["start"] * SR)
            L2 = int(0.5 * SR)
            if j + L2 < n:
                e = np.exp(-np.arange(L2) / (SR * 0.13))
                sw = np.sin(2 * np.pi * np.linspace(72, 44, L2) * np.arange(L2) / SR)
                out[j:j + L2] += sw * e * 0.42
    return out


def main():
    ff = ffmpeg()
    parts = decode_all(ff)
    speech = sum(len(p) / SR for p in parts)
    gaps = [GAP] * (len(parts) - 1)
    gaps = gaps + [TAIL]                      # last "gap" is the outro hold
    slack = TARGET - (speech + sum(gaps) )
    if abs(slack) > 1e-6:                      # spread the fit over the 8 real gaps
        gaps = [max(0.12, g + slack / (len(gaps) - 1)) for g in gaps]
        slack = TARGET - (speech + sum(gaps))
    tl, t = [], LEAD
    for i, p in enumerate(parts):
        tl.append({"i": i + 1, "start": round(t, 3), "end": round(t + len(p) / SR, 3),
                   "dur": round(len(p) / SR, 3), "text": LINES[i], "reveal": REVEAL[i]})
        t += len(p) / SR + gaps[i]
    total = t
    n = int(round(total * SR))
    print(f"[audio] speech {speech:.1f}s + gaps {sum(gaps):.1f}s + pad {LEAD+TAIL:.2f}s -> {total:.2f}s "
          f"({int(round(total*FPS))} frames) slack {slack:.2f}s")

    track = np.zeros(n, dtype=np.float32)
    for ln, p in zip(tl, parts):
        s = int(ln["start"] * SR)
        track[s:s + len(p)] += p
    pk = np.abs(track).max()
    track = track / max(pk, 1e-6) * 0.90          # narration peaks at -0.9 dBFS
    rng = np.random.default_rng(4)
    bed = music_bed(n, rng)
    hits = sfx(n, tl)
    # side-chain duck: -12 dB while narration is active
    env = np.abs(track)
    env = np.maximum.accumulate(np.lib.stride_tricks.sliding_window_view(env, SR // 8).max(axis=1))
    duck = np.concatenate([env, np.zeros(SR // 8)])[:n]
    duck = 1.0 - 0.70 * np.clip(duck / 0.25, 0, 1)
    mix = track + bed * duck + hits * 0.8
    mix = mix / max(np.abs(mix).max(), 1e-6) * 0.95
    json.dump({"fps": FPS, "sr": SR, "w": 1080, "h": 1920, "total": round(total, 3),
               "frames": int(round(total * FPS)), "lead": LEAD, "tail": TAIL, "lines": tl},
              open(os.path.join(AU, "timeline.json"), "w"), indent=1)
    for name, sig in (("vo_track.wav", track), ("music_bed.wav", bed), ("sfx.wav", hits), ("mix.wav", mix)):
        w = wave.open(os.path.join(AU, name), "wb")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((sig * 32767).astype(np.int16).tobytes()); w.close()
    mono = mix
    st = np.stack([mono, mono * 0.995 + np.roll(mono, 40) * 0.05], axis=1)
    w = wave.open(os.path.join(AU, "mix_stereo.wav"), "wb")
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((st * 32767).astype(np.int16).tobytes()); w.close()
    rms = lambda x: round(float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)), 1)
    print(f"[audio] narration rms {rms(track)} dBFS | bed rms {rms(bed*duck)} | mix rms {rms(mix)} peak {round(float(np.abs(mix).max()),3)}")
    for ln in tl:
        print(f"   line {ln['i']}: {ln['start']:6.2f} -> {ln['end']:6.2f}  ({ln['dur']:5.2f}s)  {ln['text'][:58]}...")
    print("[audio] wrote timeline.json, vo_track.wav, music_bed.wav, sfx.wav, mix.wav, mix_stereo.wav")
    if not (55.0 <= total <= 62.5):
        print(f"[audio] WARN duration {total:.1f}s outside 55-62.5 window", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
