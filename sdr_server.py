#!/usr/bin/env python3
"""
3D Radar SDR backend - Airspy real-time processing.
Pouziva libairspy pres ctypes. Pripojeni k Airspy zarizeni,
FFT zpracovani v realnem case, detekce objektu.
Automaticky prepina do simulace pokud Airspy neni pripojen.
"""

import asyncio
import json
import signal
import subprocess
import sys
import ctypes
import numpy as np
import websockets
from collections import deque
import threading

CONFIG = {
    'freq': 100e6,
    'sample_rate': 2.5e6,
    'sample_rate_enum': 1,
    'gain': 'auto',
    'fft_size': 1024,
    'threshold': 25,
    'port': 8765,
}

HAS_AIRSPY = False
LIBAIRSPY = None
try:
    import ctypes
    from ctypes import (c_int, c_int16, c_uint8, c_uint32, c_void_p,
                        c_char_p, POINTER, byref, CFUNCTYPE, Structure)

    LIBAIRSPY = ctypes.cdll.LoadLibrary('libairspy.so.0')

    LIBAIRSPY.airspy_init.argtypes = []
    LIBAIRSPY.airspy_init.restype = c_int
    LIBAIRSPY.airspy_init()
    HAS_AIRSPY = True
except Exception as e:
    print(f"[AIRSPY] Knihovna nenactena: {e}")


class AirspyReader:
    def __init__(self):
        self.device = None
        self.running = False
        self.simulating = True
        self.thread = None
        self.buffer = deque(maxlen=CONFIG['fft_size'] * 32)
        self.lock = threading.Lock()
        self._callback = None

    def open(self):
        if not HAS_AIRSPY:
            return False
        try:
            dev = c_void_p()
            LIBAIRSPY.airspy_open.argtypes = [POINTER(c_void_p)]
            LIBAIRSPY.airspy_open.restype = c_int
            if LIBAIRSPY.airspy_open(byref(dev)) != 0:
                return False
            self.device = dev

            LIBAIRSPY.airspy_set_samplerate.argtypes = [c_void_p, c_uint32]
            LIBAIRSPY.airspy_set_samplerate.restype = c_int
            ret = LIBAIRSPY.airspy_set_samplerate(self.device, c_uint32(CONFIG['sample_rate_enum']))
            if ret != 0:
                print(f"[AIRSPY] Chyba nastaveni sample rate: {ret}")
                return False

            LIBAIRSPY.airspy_set_freq.argtypes = [c_void_p, c_uint32]
            LIBAIRSPY.airspy_set_freq.restype = c_int
            LIBAIRSPY.airspy_set_freq(self.device, c_uint32(int(CONFIG['freq'])))

            try:
                LIBAIRSPY.airspy_set_lna_agc.argtypes = [c_void_p, c_uint32]
                LIBAIRSPY.airspy_set_lna_agc.restype = c_int
                LIBAIRSPY.airspy_set_lna_agc(self.device, c_uint32(1))
            except Exception as e:
                print(f"[AIRSPY] LNA AGC nelze: {e}")
            try:
                LIBAIRSPY.airspy_set_mixer_agc.argtypes = [c_void_p, c_uint32]
                LIBAIRSPY.airspy_set_mixer_agc.restype = c_int
                LIBAIRSPY.airspy_set_mixer_agc(self.device, c_uint32(1))
            except Exception as e:
                print(f"[AIRSPY] Mixer AGC nelze: {e}")

            self._setup_rx_callback()
            self.running = True
            self.simulating = False
            return True
        except Exception as e:
            print(f"[AIRSPY] Otevreni selhalo: {e}")
            return False

    def _setup_rx_callback(self):
        class AirspyTransfer(Structure):
            _fields_ = [
                ('device', c_void_p),
                ('ctx', c_void_p),
                ('sample_count', c_int),
                ('samples', c_void_p),
            ]

        SAMPLE_CB = CFUNCTYPE(c_int, POINTER(AirspyTransfer), c_void_p)

        def callback(transfer, ctx):
            try:
                count = transfer.contents.sample_count
                ptr = transfer.contents.samples
                samples_ptr = ctypes.cast(ptr, POINTER(c_int16 * count))
                arr = np.frombuffer(samples_ptr.contents, dtype=np.int16)
                iq = arr[0::2] + 1j * arr[1::2]
                with self.lock:
                    self.buffer.extend(iq)
            except:
                pass
            return 0

        self._callback = SAMPLE_CB(callback)
        LIBAIRSPY.airspy_start_rx.argtypes = [c_void_p, SAMPLE_CB, c_void_p]
        LIBAIRSPY.airspy_start_rx.restype = c_int
        LIBAIRSPY.airspy_start_rx(self.device, self._callback, None)

    def read_samples(self, n):
        if not self.running or not HAS_AIRSPY:
            self.simulating = True
            return self._simulate_samples(n)
        with self.lock:
            samples = list(self.buffer)
            self.buffer.clear()
        if len(samples) == 0:
            return np.array([], dtype=np.complex64)
        return np.array(samples[:n], dtype=np.complex64)

    def _simulate_samples(self, n):
        fs = float(CONFIG['sample_rate'])
        t = np.arange(n, dtype=np.float32) / fs
        noise = (np.random.randn(n) + 1j * np.random.randn(n)) * 0.3
        signals = np.zeros(n, dtype=np.complex64)
        for obj in SIM_OBJECTS:
            d = obj['dist']
            p = obj['power']
            beat_freq = d * RANGE_SCALE
            sig = p * np.exp(1j * 2 * np.pi * beat_freq * t)
            sig *= np.exp(-t * 200)
            signals += sig
        return signals + noise

    def close(self):
        self.running = False
        self.simulating = True
        if HAS_AIRSPY and self.device:
            try:
                LIBAIRSPY.airspy_stop_rx.argtypes = [c_void_p]
                LIBAIRSPY.airspy_stop_rx.restype = c_int
                LIBAIRSPY.airspy_stop_rx(self.device)
            except:
                pass
            try:
                LIBAIRSPY.airspy_close.argtypes = [c_void_p]
                LIBAIRSPY.airspy_close.restype = c_int
                LIBAIRSPY.airspy_close(self.device)
            except:
                pass


RANGE_SCALE = 5000.0


# ===== NOISE FLOOR ESTIMATOR =====
# Running median odhad sumu pozadi, adaptivni prah
class NoiseFloor:
    def __init__(self, window=60):
        self.window = window
        self.history = []
        self.noise_floor = CONFIG['threshold']
        self.noise_mad = 2.0

    def update(self, power):
        p = np.percentile(power, 8)
        self.history.append(p)
        if len(self.history) > self.window:
            self.history.pop(0)
        if len(self.history) >= 10:
            arr = np.array(self.history)
            self.noise_floor = float(np.median(arr))
            self.noise_mad = float(np.median(np.abs(arr - self.noise_floor)))

    def get_threshold(self):
        return max(-60, self.noise_floor + 2.5 * self.noise_mad)

    def get_snr(self, peak_power):
        return peak_power - self.noise_floor


SIM_OBJECTS = [
    {'dist': 3,   'power': 0.9, 'class_name': 'kuchyne/hrnec'},
    {'dist': 5.5, 'power': 0.6, 'class_name': 'hracky/plysak'},
    {'dist': 8,   'power': 0.4, 'class_name': 'elektronika/notebook'},
    {'dist': 10,  'power': 0.5, 'class_name': 'sport/fotbalovy_mic'},
    {'dist': 4.2, 'power': 0.7, 'class_name': 'kuchyne/sklenice'},
    {'dist': 7,   'power': 0.4, 'class_name': 'koupelna/rucnik'},
    {'dist': 12,  'power': 0.3, 'class_name': 'elektronika/mobilni_telefon'},
    {'dist': 2.5, 'power': 0.5, 'class_name': 'jidlo/jablko'},
]


class SDRProcessor:
    def __init__(self):
        self.reader = AirspyReader()
        self.spectrum = []
        self.current_power = 0.0
        self.initialized = False
        self.noise_floor = NoiseFloor()


    def init(self):
        if HAS_AIRSPY:
            opened = self.reader.open()
            if opened:
                print("[SDR] Airspy zarizeni otevreno - LIVE rezim")
            else:
                print("[SDR] Airspy nelze otevrit - SIMULACE")
        else:
            print("[SDR] Airspy knihovna nenactena - SIMULACE")
        self.initialized = True
        return False

    def read_fft(self):
        n = CONFIG['fft_size']
        samples = self.reader.read_samples(n * 8)
        if len(samples) < n:
            return None
        window = np.hanning(n)
        chunk = samples[:n] * window
        fft = np.fft.fftshift(np.fft.fft(chunk))
        power = 10 * np.log10(np.abs(fft) ** 2 + 1e-10)
        self.current_power = float(np.mean(power))
        self.spectrum = power.tolist()
        self.noise_floor.update(power)
        return power

    def detect_peaks(self, power, threshold=None):
        if power is None or len(power) < 3:
            return []
        if threshold is None:
            threshold = self.noise_floor.get_threshold()
        peaks = []
        for i in range(1, len(power) - 1):
            if power[i] > power[i - 1] and power[i] > power[i + 1] and power[i] > threshold:
                bw = CONFIG['sample_rate']
                fft_size = len(power)
                freq_offset = abs((i - fft_size // 2) / fft_size * bw)
                range_est = freq_offset / RANGE_SCALE
                if 0.5 < range_est < 20:
                    peaks.append({
                        'index': int(i),
                        'power': float(round(power[i], 1)),
                        'range': float(round(range_est, 1)),
                    })
        peaks.sort(key=lambda p: p['power'], reverse=True)
        return peaks[:8]

    def scan(self, azimuth, elevation):
        power = self.read_fft()
        peaks = self.detect_peaks(power)
        detections = []
        for peak in peaks:
            r = peak['range']
            if r < 0.5 or r > 20:
                continue
            x = float(r * np.cos(elevation) * np.sin(azimuth))
            y = float(r * np.sin(elevation))
            z = float(r * np.cos(elevation) * np.cos(azimuth))
            obj_type, confidence = 'neznama', 0.0
            snr = self.noise_floor.get_snr(peak['power'])
            # Boost confidence if SNR is high
            confidence = min(1.0, confidence * (1.0 + max(0, snr - 5) * 0.02))
            detections.append({
                'x': round(x, 2), 'y': round(y, 2), 'z': round(z, 2),
                'power': peak['power'],
                'range': peak['range'],
                'class_name': obj_type,
                'confidence': round(confidence, 2),
                'snr': round(snr, 1),
            })
        return {
            'type': 'scan_result',
            'azimuth': float(round(azimuth, 3)),
            'elevation': float(round(elevation, 3)),
            'detections': detections,
            'power': float(round(self.current_power, 1)),
            'spectrum': self.spectrum[-256:] if len(self.spectrum) > 256 else self.spectrum,
            'hardware': HAS_AIRSPY,
            'noise_floor': round(self.noise_floor.noise_floor, 1),
        }

    def close(self):
        self.reader.close()


class SDRServer:
    def __init__(self):
        self.processor = SDRProcessor()

    async def handle(self, websocket):
        self.processor.init()
        await websocket.send(json.dumps({
            'type': 'status', 'connected': True,
            'simulate': self.processor.reader.simulating,
            'config': {k: v for k, v in CONFIG.items() if k != 'port'},
            'noise_floor': round(self.processor.noise_floor.noise_floor, 1),
        }))
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    t = data.get('type', '')
                    if t == 'scan':
                        r = self.processor.scan(
                            data.get('azimuth', 0),
                            data.get('elevation', 0))
                        await websocket.send(json.dumps(r))
                    elif t in ('get_status', 'config'):
                        await websocket.send(json.dumps({
                            'type': 'status',
                            'connected': True,
                            'simulate': self.processor.reader.simulating,
                            'config': {k: v for k, v in CONFIG.items()
                                       if k != 'port'},
                            'noise_floor': round(self.processor.noise_floor.noise_floor, 1),
                        }))
                        if t == 'config':
                            for k in ('freq', 'sample_rate', 'gain', 'threshold'):
                                if k in data:
                                    CONFIG[k] = data[k]
                except json.JSONDecodeError:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.processor.close()


async def main():
    port = CONFIG['port']

    print(f"\n{'='*55}")
    print(f"  3D RADAR - SDR SERVER")
    print(f"{'='*55}")
    print(f"  WebSocket: ws://localhost:{port}")
    print(f"  Zarizeni:  {'AIRSPY' if HAS_AIRSPY else '—'}")
    print(f"  Frekvence: {CONFIG['freq']/1e6:.0f} MHz")
    print(f"  Vzorkovani:{CONFIG['sample_rate']/1e6:.1f} MS/s")
    print(f"  Threshold: {CONFIG['threshold']} dB")
    print(f"  Rezim:     {'LIVE' if HAS_AIRSPY else 'SIMULACE'}")
    print(f"{'='*55}\n")

    server = SDRServer()
    async with websockets.serve(server.handle, "0.0.0.0", port,
                                ping_interval=30, ping_timeout=10):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[SDR] Server ukoncen.")
