"""Original background music for the explainer, synthesized from scratch.

A mellow ii-V-I-VI loop at 96 BPM (one bar = 2.5 s), so section changes land
on the video's scene cuts: pads alone under the title, the band comes in when
the game starts at 0:05, and the end card is a held chord that fades out.

    python video/music.py out.wav [seconds] [--variation]
"""

import random
import sys
import wave

import numpy as np

SR = 44100
BPM = 96
BEAT = 60 / BPM
BAR = 4 * BEAT
SWING = 0.62  # where the off-beat 8th falls inside a beat

# (bass root, chord voicing) as MIDI notes: Dm9, G13, Cmaj9, A7b9
CHORDS = [
    (38, [53, 57, 60, 64]),
    (43, [53, 59, 64, 69]),
    (36, [52, 55, 59, 62]),
    (45, [55, 61, 64, 70]),
]
# Optional B section for longer pieces: Fmaj7, Em9, Dm9, G13, leading back into A.
CHORDS_B = [
    (41, [57, 60, 64, 67]),
    (40, [55, 59, 62, 66]),
    (38, [53, 57, 60, 64]),
    (43, [53, 59, 64, 69]),
]


def chord(b, variation):
    """(root, voicing) of bar b; with variation, every other 8 bars use the B section."""
    progression = CHORDS_B if variation and (b // 8) % 2 == 1 else CHORDS
    return progression[b % 4]


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def swung(beat):
    """Beat position (in beats, may be fractional .5) to seconds, with swing."""
    whole, frac = divmod(beat, 1)
    return (whole + (SWING if frac >= 0.5 else 0)) * BEAT


class Mix:
    def __init__(self, seconds):
        self.n = int(seconds * SR)
        self.left = np.zeros(self.n)
        self.right = np.zeros(self.n)

    def add(self, start, sig, gain=1.0, pan=0.0):
        i = int(start * SR)
        if i >= self.n or i + len(sig) <= 0:
            return
        sig = sig[: self.n - i] * gain
        self.left[i:i + len(sig)] += sig * (1 - pan) / 2 ** 0.5
        self.right[i:i + len(sig)] += sig * (1 + pan) / 2 ** 0.5


def envelope(n, attack, decay, release, length):
    t = np.arange(n) / SR
    env = np.minimum(1, t / attack) * np.exp(-t / decay)
    rel = np.clip((length + release - t) / release, 0, 1)
    return env * rel


def keys(freq, length, detune=0.0):
    """Electric-piano-ish tone: sine with a bell-like FM attack."""
    n = int((length + 0.6) * SR)
    t = np.arange(n) / SR
    f = freq * (1 + detune)
    index = 1.6 * np.exp(-t * 9)
    sig = np.sin(2 * np.pi * f * t + index * np.sin(2 * np.pi * f * t))
    sig += 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 3)
    return sig * envelope(n, 0.006, 1.6, 0.25, length)


def bell(freq, length):
    n = int((length + 1.2) * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t + 0.8 * np.exp(-t * 6) * np.sin(2 * np.pi * 3.5 * freq * t))
    return sig * envelope(n, 0.004, 0.7, 0.4, length)


def bass(freq, length):
    n = int((length + 0.15) * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(2 * np.pi * 2 * freq * t) + 0.06 * np.sin(2 * np.pi * 3 * freq * t)
    return sig * envelope(n, 0.008, 1.2, 0.08, length)


def kick():
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    pitch = 48 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(pitch) / SR) * np.exp(-t * 11)


def smooth(x, k):
    return np.convolve(x, np.ones(k) / k, mode="same")


def snare(rng):
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    noise = smooth(rng.standard_normal(n), 6)
    return (0.7 * noise * np.exp(-t * 22) + 0.4 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30))


def hat(rng, open_=False):
    n = int((0.18 if open_ else 0.05) * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise = noise - smooth(noise, 4)  # crude high-pass
    return noise * np.exp(-t * (16 if open_ else 70))


def echo(x, delay, feedback, taps=4):
    out = x.copy()
    d = int(delay * SR)
    for k in range(1, taps + 1):
        out[d * k:] += x[: len(x) - d * k] * feedback ** k
    return out


def render(seconds, variation=False):
    rng = np.random.default_rng(14)
    pick = random.Random(14)
    drums = Mix(seconds)
    band = Mix(seconds)
    lead = Mix(seconds)
    bars = int(np.ceil(seconds / BAR))
    end_bar = int((seconds - 7.0) // BAR)  # last section: held chord under the end card

    for b in range(bars):
        t0 = b * BAR
        root, voicing = chord(b, variation)
        next_root = chord(b + 1, variation)[0]

        if b >= end_bar:
            if b == end_bar:  # one long Dm9 to finish on
                hold = seconds - t0
                for k, m in enumerate(CHORDS[0][1] + [69]):
                    band.add(t0 + 0.03 * k, keys(hz(m), hold, 0.002), 0.11, pan=-0.3 + 0.15 * k)
                band.add(t0, bass(hz(CHORDS[0][0]), hold), 0.32)
                lead.add(t0 + 0.4, bell(hz(81), 2.0), 0.08, pan=0.3)
            continue

        intro = b < 2
        # comping: a long chord on 1, a short push on the and of 3
        for beat, length, gain in ((0, 2.4 * BEAT, 0.10), (2.5, 1.0 * BEAT, 0.07)):
            if intro and beat:
                continue
            for k, m in enumerate(voicing):
                band.add(t0 + swung(beat) + 0.012 * k, keys(hz(m), length, 0.0015 * (k - 1.5)),
                         gain, pan=-0.35 + 0.23 * k)

        # bass: root, fifth, chromatic approach into the next chord
        if not intro:
            approach = next_root + (1 if pick.random() < 0.5 else -1)
            for beat, m, length in ((0, root, 1.7), (2, root + 7, 0.9), (3, approach, 0.8)):
                band.add(t0 + swung(beat), bass(hz(m), length * BEAT), 0.34)
        else:
            band.add(t0, bass(hz(root), 3.5 * BEAT), 0.22)

        # drums
        if not intro:
            for beat in (0, 2):
                drums.add(t0 + swung(beat), kick(), 0.55)
            if b % 2:
                drums.add(t0 + swung(2.5), kick(), 0.3)
            for beat in (1, 3):
                drums.add(t0 + swung(beat), snare(rng), 0.26, pan=0.05)
            for e in range(8):
                beat = e / 2
                accent = 0.11 if e % 2 == 0 else 0.07
                drums.add(t0 + swung(beat), hat(rng, open_=(e == 7 and b % 4 == 3)), accent, pan=0.35)

        # a sparse bell melody once the game is under way
        if 4 <= b < end_bar - 1 and b % 8 not in (6, 7):
            tones = [m + 12 for m in voicing]
            for beat in (0.5, 1.5, 2.0, 3.5) if b % 2 == 0 else (0.0, 1.0, 2.5):
                if pick.random() < 0.8:
                    m = pick.choice(tones)
                    lead.add(t0 + swung(beat), bell(hz(m), 0.6 * BEAT), 0.065, pan=0.25)

    band_l, band_r = echo(band.left, 0.27, 0.22), echo(band.right, 0.31, 0.22)
    lead_l, lead_r = echo(lead.left, BEAT * 0.75, 0.35), echo(lead.right, BEAT * 0.5, 0.35)
    left = band_l + lead_l + drums.left
    right = band_r + lead_r + drums.right
    stereo = np.stack([left, right], axis=1)

    # gentle saturation, fade in and out, then level the whole thing
    stereo = np.tanh(stereo * 1.4) / 1.4
    t = np.arange(len(stereo)) / SR
    fade = np.minimum(1, t / 1.5) * np.clip((seconds - t) / 3.0, 0, 1)
    stereo *= fade[:, None]
    stereo *= 0.8 / np.max(np.abs(stereo))
    return stereo


def write_wav(path, stereo):
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


if __name__ == "__main__":
    write_wav(sys.argv[1], render(float(sys.argv[2]) if len(sys.argv) > 2 else 87.0, variation="--variation" in sys.argv))
