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
* 🛡️ **Reale v0 - autorizzazione con blocco di sicurezza:** `authorization.py` si rifiuta di trasformare la percezione in movimento a meno che lo stato di sicurezza a monte non sia `READY` e i dati visivi non siano abbastanza freschi/affidabili. Esposto tramite il nuovo sottocomando `request` più sotto - non servono telecamera, NPU o il processo SAFETY-ZONES per eseguirlo o testarlo.
* 🔄 **Controllo a circuito chiuso:** Feedback continuo che bypassa l'orchestratore di alto livello per una bassa latenza. *(obiettivo architetturale - l'invio gRPC al core HYDRA-UMC resta lavoro futuro.)*
* 📐 **Stima della posa:** Stima della posa dell'oggetto 6-DOF da viste a telecamera singola o multipla. *(lavoro futuro - richiede la vera NPU Hailo-8 che questo ambiente non ha ancora.)*
* ⚡ **Accelerazione hardware:** Utilizza l'output di Hailo-8 per il calcolo istantaneo delle coordinate. *(lavoro futuro, stesso motivo.)*
* 🔌 **Limite di integrazione HailoRT, preparato in anticipo sul modulo:** `hailo_runtime.py` è scritto contro l'API reale e confermata `hailo_platform` (`VDevice`, `HEF`, `ConfigureParams`) - importata in modo lazy così che questo repository si installi/testi in modo pulito senza il pacchetto `hailort` né un modulo Hailo-8 presente - e `hailo_output_to_pose()` adatta un risultato di inferenza reale direttamente nel `Pose6D` che `compute_pose_error()` già consuma. *(implementato, solo limite di integrazione - eseguire davvero l'inferenza richiede ancora un `.hef` di stima della posa realmente compilato e un modulo Hailo-8 fisico.)*

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
* **Perché `authorize_correction()` controlla `safety_state` prima di confidenza/freschezza.** Un guasto di sicurezza deve prevalere su tutto il resto, anche di fronte a una rilevazione perfettamente fresca e affidabile - quindi `INHIBITED` (safety_state != "READY") viene controllato per primo e interrompe il resto della policy. Solo una volta confermato che il braccio può muoversi in sicurezza conta se i *dati* sono abbastanza affidabili da muoverlo (`REJECTED` per confidenza bassa o dati obsoleti). Questo rispecchia la stessa precedenza `INHIBITED`-prima-di-`DANGER`/`WARNING` già usata in HYDRA-UMC-SAFETY-ZONES.
* **Perché `request` è un nuovo sottocomando invece di modificare `correct`.** `correct` è l'utility di basso livello esistente, matematica pura (senza consapevolezza di sicurezza né concetto di freschezza della telecamera) con i propri chiamanti e test; avvolgerla sul posto in un blocco di sicurezza ne cambierebbe silenziosamente il contratto. `request` aggiunge il punto di ingresso con blocco, orientato alla telecamera, che il codice dell'ecosistema dovrebbe effettivamente chiamare, mentre `correct` resta disponibile invariato per l'uso diretto della matematica di posa.

---

## 📂 STRUTTURA DELLE CARTELLE

CM5 + Hailo-8 è hardware già esistente senza scheda propria, quindi questo
progetto non ha cartella `hardware/` né `firmware/`. `os/` e `models/`
vivono solo nel padre di integrazione, `HYDRA-UMC-VISION-NODE`.

```text
HYDRA-UMC-VISUAL-SERVOING-API/
├── src/                 # Codice sorgente (pacchetto hydra_umc_visual_servoing_api)
│   └── hydra_umc_visual_servoing_api/
│       ├── pose.py           # Pose6D - posa a 6-DOF (x, y, z, roll, pitch, yaw)
│       ├── servo.py          # Legge di correzione PBVS: errore di posa + comando di velocità
│       ├── authorization.py  # Policy con blocco di sicurezza (INHIBITED/REJECTED/ACCEPTED)
│       ├── hailo_runtime.py  # Vero limite di integrazione HailoRT (hailo_platform) dello stimatore di posa, importato in modo lazy
│       ├── api.py            # Superficie JSON/HTTP semplice (http.server di stdlib) su correct/request
│       └── main.py           # Entry point CLI (invocazione nuda + `correct` + `request`)
├── tests/               # Suite pytest reale (pose, servo, authorization, hailo_runtime, api, CLI)
├── docs/                # Documentazione e teoria cinematica
├── build/               # Output di build (qui vive anche il .venv locale)
├── images/              # Media e diagrammi
├── systemd/
│   └── hydra-umc-visual-servoing-api.service # Unità systemd della API locale di correzione PBVS sulla CM5
├── tools/
│   ├── build_test.py    # Controllo build senza versionamento
│   └── ci_validate.py   # Validazione manifest/CHANGELOG/docs usata dalla CI
├── pyproject.toml       # Metadati del pacchetto, dipendenze, versione a contachilometri
├── bump_version.py      # Incremento versione nativa a contachilometri (build.sh/.bat)
├── bump_manifest_version.py # Sincronizza la versione di hydra-umc.project.json con quella nativa (--sync)
├── build.sh / build.bat # venv + installazione editabile + compile-check + test
├── build-test.sh / build-test.bat # Controllo build senza versionamento
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

Esempio reale - richiedere una correzione con blocco di sicurezza (accettata, inibita e rifiutata):

```bash
./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.9 --data-age-ms 30 --safety-state READY
# outcome : ACCEPTED - frame 'cam0-f42' authorized (confidence=0.9, data_age_ms=30.0)
# pose error   : dx=1.000000 dy=0.000000 dz=0.000000  droll=0.000000 dpitch=0.000000 dyaw=0.000000
# velocity cmd : vx=1.000000 vy=0.000000 vz=0.000000  wroll=0.000000 wpitch=0.000000 wyaw=0.000000

./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.9 --data-age-ms 30 --safety-state FAULT
# outcome : INHIBITED - safety_state is 'FAULT', not 'READY'   (codice di uscita 2)

./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.2 --data-age-ms 30 --safety-state READY
# outcome : REJECTED - confidence 0.2 is below the required minimum 0.6 for frame 'cam0-f42'   (codice di uscita 1)
```

---

## ✅ Stato Attuale e Prossimi Passi

**Reale oggi:** la legge di correzione PBVS di errore di posa e comando
di velocità (`pose.py`, `servo.py`) - il passaggio "Calcolo dell'errore
(Pose Delta)" del diagramma del loop qui sopra - con un comando CLI
`correct` reale; la policy di autorizzazione con blocco di sicurezza
(`authorization.py`) che si rifiuta di trasformare una rilevazione
visiva in movimento a meno che lo stato di sicurezza a monte non sia
`READY` e i dati non siano abbastanza affidabili/freschi, esposta
tramite il comando CLI `request`; e un vero limite di integrazione
HailoRT (`hailo_runtime.py`) pronto per un vero stimatore di posa Hailo-8
non appena viene collegato. 68 test in totale.

**Ancora da fare, bloccato da hardware reale:** eseguire davvero la
*stima* di posa a 6 gradi di libertà tramite `hailo_runtime.py` richiede
un `.hef` di stima della posa realmente compilato (nessun modello
specifico ancora scelto) e una NPU Hailo-8 fisica collegata, e l'invio
gRPC a bassa latenza del comando di velocità risultante al core
HYDRA-UMC è un lavoro futuro separato.

## 🚀 TABELLA DI MARCIA
* **Fase 1:** Sincronizzazione e calibrazione della pipeline multi-camera per 8 ingressi USB 3.0.
* **Fase 2:** Migrazione a YOLOv11 e ottimizzazione per Hailo-8L per il rilevamento di componenti industriali.
* **Fase 3:** Ricostruzione 3D in tempo reale dai nodi di visione stereo e mappatura dinamica delle zone di sicurezza.
* **Fase 4:** Supporto per il tracciamento visivo 9-DOF (inclusa la ridondanza dell'orientamento) e correzione sub-micrometrica.

---

## 🔗 Progetti Correlati

Questo progetto fa parte dell'ecosistema robotico HYDRA-UMC dello stesso autore (JuanenRac / Electro Hobby 3D). Vale la pena conoscerlo, poiché una richiesta potrebbe in realtà riguardare uno di questi invece di questo repository.

**Progetto Padre**
- **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — hub di integrazione per la pipeline di visione Hailo-8, con un vero controllo di prontezza hardware per fase; il genitore di cui questo repository è una fase o un consumatore specifico, all'interno della propria pipeline di percezione.

**Progetti Fratelli** — le altre fasi/consumatori della pipeline di percezione Hailo-8 propria di HYDRA-UMC-VISION-NODE
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — generatore reale di pipeline GStreamer + config MediaMTX, con una vera barriera di integrazione HailoRT.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — registro reale di modelli compilati con verifica di caricamento sicuro per architettura Hailo/checksum.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — vero controllo di violazione zona e richiesta E-STOP, con imposizione della freschezza di calibrazione.

**Direttamente Correlati**
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la scheda madre fisica del braccio robotico: host CM5 + coprocessore STM32H745 dual-core, che coordina fino a 8 bracci utensile via CAN-OTA/SPI-OTA; il firmware core STM32 che riceve le correzioni cinematiche di posa di questa stessa API.

**Fa Anche Parte dell'Ecosistema**

*Hardware e Piattaforma di Base*
- **[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS)** — livello prodotto riproducibile su Raspberry Pi OS per il CM5: agente in sola lettura, config/profili validati, provisioning WiFi al primo contatto.
- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — il contratto JSON-Schema condiviso e la barriera di sicurezza contro cui ogni bridge valida i propri comandi.

*Backend Centrale e Client*
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — il vero backend headless (REST/WebSocket) con cui parla davvero ogni client di controllo.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — dashboard di controllo web con visualizzazione 3D multi-robot in tempo reale.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — centro di comando sciame desktop (PySide6) per più server contemporaneamente, pacchettizzato come eseguibile standalone.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — app di controllo nativa per Android con login biometrico e un companion Wear OS abbinato.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — app di controllo per iOS/iPadOS (Flutter) con sincronizzazione WebSocket in tempo reale.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — interfaccia touch nativa per il touchscreen DSI da 7" a bordo, incorporata direttamente nel CM5.
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — creatore/editor grafico desktop di URDF che invia i modelli finiti al catalogo di STUDIO.
- **[HYDRA-UMC-BRIDGE-AMR](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-AMR)** — barriera di coordinamento per flotte AGV/AMR tramite un publisher MQTT VDA 5050 reale.
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — coordinatore ad alto livello per celle CNC con accesso reale a stato/byte di controllo GRBL.
- **[HYDRA-UMC-BRIDGE-DROIDS](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-DROIDS)** — barriera di coordinamento per droidi con zampe/umanoidi, con un vero mittente di comandi per Boston Dynamics Spot.
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — coordinatore di sicurezza per celle laser che legge 3 salvaguardie GPIO reali di chiave/involucro/interblocco.
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — coordinatore ad alto livello sicuro per il flusso schede del pick-and-place OpenPnP.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — barriera di coordinamento sicura per stampanti 3D Moonraker/Klipper, con comandi di lavoro reali e controllati.
- **[HYDRA-UMC-BRIDGE-ROS2](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-ROS2)** — coordinatore di sicurezza con un vero trasporto ROS 2 rclpy, importato in modo lazy.
- **[HYDRA-UMC-BRIDGE-UAV](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-UAV)** — barriera di coordinamento per UAV dotati di fotocamera, con un vero mittente di comandi MAVLink.

*Piattaforma Strumenti URTC*
- **[URTC](https://github.com/JuanenRac/URTC)** — firmware per la scheda fisica dell'Universal Robot Tool Controller, oltre 25 profili utensile su bus CAN.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — strumento desktop con GUI per il flashing delle schede URTC, CAN-OTA più SWD/JTAG a chip intero.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — strumento desktop di diagnostica CAN-bus dal vivo per schede URTC, un pannello per profilo utensile.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — alternativa basata su browser a URTC-TESTER tramite la Web Serial API, senza installazione locale.

*Nodo IA Cognitivo (Hailo-10)*
- **[HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)** — hub di integrazione per la pipeline cognitiva Hailo-10 (orchestrazione LLM/VLA/voce).
- **[HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)** — vera codifica/decodifica di token d'azione e generazione di traiettoria per un modello Vision-Language-Action.
- **[HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)** — vero front-end vocale (VAD + parser di intenti) con un relay verso Watch limitato e soggetto a conferma.
- **[HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)** — vera scomposizione dei task basata su regole e recupero semantico degli errori sui codici errore MCU.
- **[HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)** — vera ricerca documentale TF-IDF (solo libreria standard) sui documenti Markdown di questo ecosistema.

*Orchestrazione e Sciame*
- **[HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)** — hub di integrazione con un vero contratto di health-report gRPC/Protobuf e una macchina a stati di missione.
- **[HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)** — vera coda di lavori basata su priorità con deduplicazione, su una vera API HTTP.
- **[HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)** — vero watchdog di salute della flotta basato su gRPC, con retry/backoff e rilevamento di discrepanza d'identità.
- **[HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)** — vero pianificatore di percorsi 3D basato su RRT, con vera validazione delle collisioni ostacolo/spazio di lavoro.
- **[HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)** — vera sincronizzazione di stato CRDT LWW-Element-Map, con property test per la convergenza multi-cella.

*Gemello Digitale e Simulazione*
- **[HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)** — hub di integrazione per il motore di gemello digitale, con un vero contratto di sincronizzazione per compatibilità di versione.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — vero interblocco di sicurezza hardware-in-the-loop che instrada i comandi tra simulazione e hardware reale.
- **[HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)** — vera cinematica diretta e validazione dei limiti articolari su un vero sottoinsieme URDF.
- **[HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)** — vero generatore procedurale di scene 2D con esportazione di annotazioni YOLO/COCO.

*Dati e Analisi*
- **[HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)** — vero archivio di serie temporali basato su sqlite3, con una vera API HTTP di ingestione/query.
- **[HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)** — vero rilevatore di anomalie FFT + baseline statistica, con monitoraggio della deriva.
- **[HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)** — vero calcolo OEE/disponibilità sullo storico di DATALAKE, con esportazione CSV riproducibile.
- **[HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)** — vera pipeline di ingestione CAN/WebSocket verso DATALAKE, con deduplicazione per sequenza.

*Gateway Industriale*
- **[HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)** — hub di integrazione che inoltra ai protocolli industriali, con un vero livello di allowlist dei comandi/backpressure.
- **[HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)** — vero spazio di indirizzi OPC-UA, verificato con una vera sessione client del protocollo binario.
- **[HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)** — vero broker MQTT con autenticazione opzionale per client e ACL sui topic.
- **[HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)** — veri endpoint XML `/probe` e `/current` di MTConnect, con output in modalità degradata.

*Strumenti Complementari e Operazioni dell'Ecosistema*
- **[HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)** — pannelli Smart Summaries e Anomaly Highlighting su DATALAKE/ANOMALY-DETECTOR, con un fallback statistico onesto.
- **[HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)** — CLI di flotta con un vero e stabile contratto di exit-code, un client live reale della stessa API di HYDRA-UMC-SERVER.
- **[HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)** — app companion WearOS con avvisi aptici reali e un relay vocale verso il telefono abbinato.
- **[URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)** — firmware per un rack di montaggio schede con decodifica reale dell'ID utensile e logica di preriscaldamento Smart Idle.
- **[URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)** — firmware più un vero companion di visione Python per una testa utensile di ispezione termica/RGB.
- **[HYDRA-UMC-UPDATER](https://github.com/JuanenRac/HYDRA-UMC-UPDATER)** — strumento amministrativo desktop che scopre, clona e aggiorna ogni repository di questo ecosistema.


---

## 📚 Documentazione e Comunità

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — stack tecnologico e linee guida di codifica per una pull request.
- **[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)** — gli standard di comportamento attesi in questa comunità.
- **[SECURITY.md](SECURITY.md)** — come segnalare una vulnerabilità, e le reali aree di attenzione sulla sicurezza di questo progetto.
- **[SUPPORT.md](SUPPORT.md)** — dove porre domande e segnalare bug.
- **[LICENSE.md](LICENSE.md)** — la licenza propria di questo progetto.

## 👤 AUTORE
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com
📺 [youtube.com/@electrohobby3d](https://youtube.com/@electrohobby3d)

## 📜 LICENZA
GPL-3.0 - Vedere LICENSE per i dettagli.
