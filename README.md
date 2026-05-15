# 3D Radar s ML klasifikací

Interaktivní 3D radarová vizualizace s refrakčními paprsky, ML klasifikací 522 předmětů z domácnosti a real-time point-to-mesh modelováním.

![screen](https://img.shields.io/badge/status-functional-brightgreen)
![three.js](https://img.shields.io/badge/three.js-r160-blue)
![python](https://img.shields.io/badge/python-3.14-blue)

## Funkce

- **3D radarové skenování** — rotující anténa vysílá svazky refrakčních paprsků
- **Refrakce paprsků** — paprsky se ohýbají k objektům podle jejich 3D tvaru (ML-driven)
- **ML klasifikace 522 objektů** — Cosine similarity / ONNX neuronová síť rozpoznává předměty z 22 kategorií domácnosti (kuchyň, elektronika, nábytek, sport, nářadí, hračky, jídlo, hudba, auto-moto, domácí mazlíčci atd.)
- **Real-time noise floor** — adaptivní práh detekce, running median noise estimation
- **Point-to-mesh modelování** — kNN outlier removal, density-adaptive connectivity, triangle face mesh
- **Start/Stop tlačítko** — zastavení skenování pro prohlížení zamrzlé mesh
- **Wireframe → Face mesh** — každý target má transparentní vyplněný povrch + wireframe
- **PPI displeje** — horizontální (azimut) a vertikální (range-height) náhled
- **Airspy SDR podpora** — live RF data nebo FFT simulace
- **Geometrický feedback** — beam bounce depth, hit spread, point density se posílají zpět do backendu
- **Bloom post-processing** — glow efekty pro paprsky a detekce

## Ovládání

| Akce | Ovládání |
|------|----------|
| Otáčení scény | Levé tlačítko + tažení |
| Přiblížení/oddálení | Kolečko myši |
| Posun | Pravé tlačítko + tažení |
| Start/Stop scan | Tlačítko v horní liště |

## Spuštění

```bash
# 1. Nainstalovat závislosti
pip install -r requirements.txt

# 2. Spustit (backend + frontend)
./start.sh

# 3. Otevřít v prohlížeči
#    http://localhost:8080
```

### Ruční spuštění

```bash
# Backend (volitelný, bez něj běží jen simulace v prohlížeči)
python3 sdr_server.py

# Frontend (HTTP server)
python3 -m http.server 8080
```

## SDR Backend

Pro reálná RF data je potřeba **Airspy** zařízení:

- `libairspy.so.0` — ctypes binding
- Automatická detekce: pokud je Airspy připojen → LIVE režim, jinak → FFT simulace
- WebSocket server na `ws://localhost:8765`
- Noise floor estimator s adaptivním prahem

## ML architektura

### Backend klasifikátor (Python)

1. **Cosine Classifier** — 50-dim feature vektor (FFT + beam + cross features), porovnání s 522 reference templaty
2. **TF/ONNX Classifier** (fallback) — 10-64-32-522 neuronová síť, Adam optimizer, 500 epoch
   - Váhy se cachují do `/tmp/radar_tf_weights.npz`
   - Pokud chybí ONNX, padá na Cosine classifier
3. **NoiseFloor** — running median z 8. percentilu spektra, MAD = 2.5

### Frontend feedback (JavaScript)

- **BeamAnalyzer** — sbírá bounce depth, hit spread per class_name
- **PointCloudAnalyzer** — počítá hustotu bodů, prostorový rozptyl
- Feedback se posílá každých 15 frame přes WebSocket do backendu

### 22 kategorií, 522 objektů

kuchyň, nápoje, elektronika, nábytek, oblečení, nářadí, sport, dekorace, koupelna, kancelář, hračky, jídlo, zahrada, hudba, nářadí péče, koberce, auto-moto, domácí mazlíčci, knihy, lékárnička, stavební materiál, osobní věci

## Technologie

| Vrstva | Technologie |
|--------|------------|
| 3D rendering | Three.js r160, WebGL |
| Post-processing | UnrealBloomPass |
| Popisky | CSS2DRenderer |
| Backend | Python 3, asyncio, websockets |
| ML inference | ONNX Runtime / numpy |
| SDR | Airspy (libairspy.so.0, ctypes) |
| FFT | NumPy, Hanning window |

## Struktura

```
3D-Radar/
├── index.html          # Kompletní 3D radar + ML vizualizace (JS)
├── sdr_server.py       # Python backend: Airspy, FFT, ML klasifikátor
├── start.sh            # Spouštěcí skript
├── requirements.txt    # Python závislosti
└── README.md
```
