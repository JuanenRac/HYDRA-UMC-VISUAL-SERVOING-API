<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-VISUAL-SERVOING-API banner" width="100%">
</p>

# 🎯 HYDRA-UMC-VISUAL-SERVOING-API

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | 🇮🇹 <b>Italiano</b> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 📐 Correzione cinematica a circuito chiuso tramite feedback visivo

<p align="left">
  <img src="https://img.shields.io/badge/Licenza-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Metodo-Eye--in--Hand%20%2F%20Eye--to--Hand-orange.svg" alt="Method">
  <img src="https://img.shields.io/badge/Sincronizzazione-gRPC%20%2F%20SPI-yellow.svg" alt="Sync">
</p>

---

## 1. 🛠️ PANORAMICA TECNICA

**HYDRA-UMC-VISUAL-SERVOING-API** è il ponte di precisione tra percezione e movimento. Calcola il delta di errore tra una posa desiderata e la posa visiva effettiva di un oggetto, fornendo correzioni cinematiche in tempo reale al core HYDRA-UMC.

Supporta configurazioni **Eye-in-Hand** (telecamera sullo strumento) ed **Eye-to-Hand** (telecamera fissa), consentendo Pick-and-Place ad altissima precisione, allineamento SMD e regolazione dinamica della traiettoria.

### Caratteristiche principali:
* ✅ **Reale v0 - legge di correzione PBVS:** `pose.py` + `servo.py` calcolano il delta di posa tra una posa attuale e una obiettivo (con avvolgimento angolare per il giro più breve, senza il lungo giro che favorisce il blocco cardanico) e lo trasformano in un comando di velocità proporzionale, limitato senza distorcerne la direzione. Esposto tramite il sottocomando `correct` più sotto - non serve telecamera né NPU per eseguirlo o testarlo.
* 🔄 **Controllo a circuito chiuso:** Feedback continuo che bypassa l'orchestratore di alto livello per una bassa latenza. *(obiettivo architetturale - l'invio gRPC al core HYDRA-UMC resta lavoro futuro.)*
* 📐 **Stima della posa:** Stima della posa dell'oggetto 6-DOF da viste a telecamera singola o multipla. *(lavoro futuro - richiede la vera NPU Hailo-8 che questo ambiente non ha ancora.)*
* ⚡ **Accelerazione hardware:** Utilizza l'output di Hailo-8 per il calcolo istantaneo delle coordinate. *(lavoro futuro, stesso motivo.)*

---

## 2. 🔄 LOOP DI SERVOING VISIVO

```mermaid
flowchart LR
    TARGET["Oggetto bersaglio"] --> CAM["Acquisizione telecamera"]
    CAM --> NPU["Rilevamento posa Hailo-8"]
    NPU --> API["VISUAL-SERVOING-API"]
    API --> ERROR["Calcolo dell'errore (Pose Delta)"]
    ERROR --> CORE["HYDRA-UMC Core (STM32)"]
    CORE --> MOVE["Attuazione motore"]
    MOVE --> TARGET
```

---

## 3. 🧱 ARCHITETTURA E DECISIONI DI PROGETTAZIONE

* **Perché questa API non ha hardware/firmware propri.** Gira interamente sul modulo condiviso CM5 + Hailo-8 posseduto dal genitore di integrazione, HYDRA-UMC-VISION-NODE - nessuna scheda propria da progettare qui, quindi `hardware/`/`firmware/`/`os/` sono state rimosse invece di lasciarle vuote.
* **Perché è sorella, non un sottomodulo, di HYDRA-UMC-VISION-NODE.** La correzione di posa gira come proprio processo/deployment così che un crash o un ciclo di inferenza lento qui non possa bloccare la pipeline di rilevamento del genitore, da cui dipende HYDRA-UMC-SAFETY-ZONES per il timing dell'E-STOP.
* **Perché la legge di correzione arriva prima della stima della posa.** Trasformare una coppia di pose in un comando di velocità limitato è pura matematica di teoria del controllo - non serve telecamera né NPU per scriverla o testarla, quindi v0 consegna prima questo pezzo (`pose.py`, `servo.py`). La vera stima di posa a 6 gradi di libertà richiede l'hardware Hailo-8 che questo ambiente non ha, e arriverà più avanti.
* **Come si inserisce nel resto dell'ecosistema.** Si colloca a valle della percezione (HYDRA-UMC-VISION-NODE) e a monte del movimento (firmware HYDRA-UMC) - trasforma gli scostamenti rilevati nelle correzioni cinematiche che il ciclo jog/servo del braccio robotico applica.

---

## 📂 STRUTTURA DELLE CARTELLE

CM5 + Hailo-8 è hardware già esistente senza scheda propria, quindi questo
progetto non ha cartella `hardware/` né `firmware/`. `os/` e `models/`
vivono solo nel padre di integrazione, `HYDRA-UMC-VISION-NODE`.

```text
HYDRA-UMC-VISUAL-SERVOING-API/
├── src/                 # Codice sorgente (pacchetto hydra_umc_visual_servoing_api)
│   └── hydra_umc_visual_servoing_api/
│       ├── pose.py      # Pose6D - posa a 6-DOF (x, y, z, roll, pitch, yaw)
│       ├── servo.py     # Legge di correzione PBVS: errore di posa + comando di velocità
│       └── main.py      # Entry point CLI (invocazione nuda + `correct`)
├── tests/               # Suite pytest reale (pose, servo, CLI)
├── docs/                # Documentazione e teoria cinematica
├── build/               # Output di build (qui vive anche il .venv locale)
├── images/              # Media e diagrammi
├── scripts/             # Script di utilità
├── pyproject.toml       # Metadati del pacchetto, dipendenze, versione a contachilometri
├── bump_version.py      # Incremento versione a contachilometri (build.sh/.bat)
├── build.sh / build.bat # venv + installazione editabile + compile-check + test
└── run.sh / run.bat     # Esegue l'entry point dal venv locale
```

---

## 🏗️ BUILD E RUN

Richiede Python 3.10+.

```bash
# Linux / macOS
./build.sh   # incrementa la versione a contachilometri, crea .venv, installa
             # il pacchetto in modo editabile (con extra dev), compile-check
             # di tutto src/, ed esegue la suite pytest reale
./run.sh     # esegue l'entry point dal .venv, stampa nome + versione + ruolo
```

```bat
:: Windows
build.bat
run.bat
```

`build.sh`/`build.bat` incrementano la versione nel `pyproject.toml` di
questo progetto seguendo la regola "a contachilometri" dell'ecosistema
(PATCH+1, con riporto su MINOR oltre il 9) prima di ogni build reale, poi
eseguono il compile-check del codice sorgente con `python -m compileall`.

Esempio reale - calcolare la correzione da una posa attuale a una obiettivo:

```bash
./run.sh correct --current "0,0,0.5,0,0,0" --target "0.02,-0.01,0.48,0,0,0.05" \
  --gain 0.8 --max-linear-speed 0.05
# pose error   : dx=0.020000 dy=-0.010000 dz=-0.020000  droll=0.000000 dpitch=0.000000 dyaw=0.050000
# error norm   : linear=0.030000 m  angular=0.050000 rad
# velocity cmd : vx=0.016000 vy=-0.008000 vz=-0.016000  wroll=0.000000 wpitch=0.000000 wyaw=0.040000
# converged    : False
```

---

## ✅ Stato Attuale e Prossimi Passi

**Reale oggi:** la legge di correzione PBVS di errore di posa e comando
di velocità (`pose.py`, `servo.py`) - il passaggio "Calcolo dell'errore
(Pose Delta)" del diagramma del loop qui sopra - con 15 test e un
comando CLI `correct` reale.

**Ancora da fare, bloccato da hardware reale:** la vera stima di posa a
6 gradi di libertà da fotogrammi della telecamera (richiede la NPU
Hailo-8), e l'invio gRPC a bassa latenza del comando di velocità
risultante al core HYDRA-UMC.

## 🚀 ROADMAP
* **Fase 1:** Sincronizzazione e calibrazione della pipeline multi-camera per 8 ingressi USB 3.0.
* **Fase 2:** Migrazione a YOLOv11 e ottimizzazione per Hailo-8L per il rilevamento di componenti industriali.
* **Fase 3:** Ricostruzione 3D in tempo reale dai nodi di visione stereo e mappatura dinamica delle zone di sicurezza.
* **Fase 4:** Supporto per il tracciamento visivo 9-DOF (inclusa la ridondanza dell'orientamento) e correzione sub-micrometrica.

---

## 🔗 Progetti Correlati

Questo progetto fa parte di un ecosistema robotico più ampio dello stesso autore (JuanenRac / Electro Hobby 3D), che copre firmware, software di controllo, nodi IA e strumenti di flotta. Utile saperlo, perché una richiesta potrebbe in realtà riguardare uno di questi progetti anziché questo repository.

### Famiglia

**Genitore:** **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — il genitore di integrazione per cui questa API trasforma la percezione in correzioni di posa.

**Fratelli:**
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — cattura e pre-elabora i flussi camera consumati dal genitore.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — compila i modelli `.hef` che il genitore carica sulla sua NPU Hailo-8.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — trasforma la percezione del genitore in rilevamento intrusioni e attivazione E-STOP.

### Relazione Diretta (fuori dalla famiglia)

- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — invia correzioni cinematiche di posa a questo firmware.

### Resto dell'Ecosistema

**Piattaforma HYDRA-UMC** — la cella di micro-fabbrica multi-robot
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la scheda madre CM5 + STM32H745 che orchestra fino a 8 bracci robotici.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — il backend Express/WebSocket con cui parla ogni client di controllo.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — dashboard di controllo web, visualizzazione 3D multi-robot.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — app di controllo Android via Wi-Fi/Bluetooth.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — app di controllo iOS/iPadOS costruita in Flutter.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — centro di comando sciame desktop (Python/PySide6).
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — editor desktop di modelli URDF per il catalogo robot.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — interfaccia touch nativa per lo schermo DSI a bordo.

**Piattaforma URTC** — il controller della testa utensile che ogni braccio HYDRA-UMC porta con sé
- **[URTC](https://github.com/JuanenRac/URTC)** — controller testa utensile su bus CAN, 25 profili utensile.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — strumento desktop di flashing CAN-OTA + SWD/JTAG.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — strumento desktop di diagnostica CAN live.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — alternativa basata su browser via Web Serial API.

**🧠 Cognitive AI Node (Hailo-10)**
- [HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)
- [HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)
- [HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)
- [HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)
- [HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)

**🐝 Orchestration & Swarm**
- [HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)
- [HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)
- [HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)
- [HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)
- [HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)

**🎮 Digital Twin & Simulation**
- [HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)
- [HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)
- [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)
- [HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)

**📊 Data & Analytics**
- [HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)
- [HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)
- [HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)
- [HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)

**🏭 Industrial Gateway**
- [HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)
- [HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)
- [HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)
- [HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)

**🛠️ Complementary Tools**
- [URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)
- [URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)
- [HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)
- [HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)
- [HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)


## 👤 AUTORE
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 LICENZA
GPL-3.0 - Vedere LICENSE per i dettagli.

## 🛠️ BUILD & RUN

Usa il controllo di compilazione senza versionamento prima di una compilazione di rilascio:

| Azione | Windows | Linux / macOS |
|---|---|---|
| Controllo di compilazione (senza modificare versione o CHANGELOG) | `build-test.bat` | `./build-test.sh` |
| Esecuzione / sviluppo (se disponibile) | `run*.bat` o `dev*.bat` | `./run*.sh` o `./dev*.sh` |

`build-test.bat` e `build-test.sh` compilano o convalidano lo stack del progetto senza incrementare `hydra-umc.project.json` né modificare `CHANGELOG.md`. Possono creare solo i normali output del compilatore. Gli script esistenti `build*.bat`, `build*.sh`, `run*` e `dev*` mantengono il comportamento specifico di versione o esecuzione; usali quando tale comportamento è necessario.