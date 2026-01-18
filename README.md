# MQTT-Telegram Bridge for Meshtastic

Bot care face legătura între rețeaua Meshtastic (433MHz/868MHz) și Telegram.

## Configurare

### Fișierul `.env`

Configurația se face în fișierul `src/.env`:

```bash
# Telegram
TOKEN="your_telegram_bot_token"
CHAT_ID=-1001234567890
TOPIC_ID_1=12345

# MQTT Broker
MQTT_BROKER="happybees.10net.ro"
MQTT_PORT=1883
MQTT_CLIENT_ID="botpi"
MQTT_USERNAME="botpi"
MQTT_PASSWORD="your_password"

# 433MHz Node
MQTT_TOPIC_SUBSCRIBE_1="msh/EU_433/2/json/#"
MQTT_TOPIC_PUBLISH_1="msh/EU_433/2/json/mqtt/!433e0370"

# 868MHz Node
MQTT_TOPIC_SUBSCRIBE_2="msh/EU_868/2/json/longfast/#"
MQTT_TOPIC_PUBLISH_2="msh/EU_868/2/json/mqtt/!25e8b180"

# Keywords
KEYWORDS="cq mesh"

# Noduri banate (hex IDs, decimal IDs, sau patterns shortname)
IGNORED_NODES="!1c07ca4c,!da9e92b0,RVE,KOKO"
```

---

## Schimbarea nodului MQTT Bridge

### Când schimbi nodul 433MHz:

1. **Află noul Node ID** (hex format, ex: `!433e0370`)
   - Din aplicația Meshtastic: Settings → Radio → Node ID
   - Sau din MQTT dashboard

2. **Editează `.env`:**
   ```bash
   nano /home/bogdan/MQTT-Telegram/src/.env
   ```

3. **Modifică linia `MQTT_TOPIC_PUBLISH_1`:**
   ```bash
   # Înlocuiește !VECHIUL_ID cu !NOUL_ID
   MQTT_TOPIC_PUBLISH_1="msh/EU_433/2/json/mqtt/!NOUL_NODE_ID"
   ```

   Exemplu:
   ```bash
   # Vechi
   MQTT_TOPIC_PUBLISH_1="msh/EU_433/2/json/mqtt/!ea243c30"
   # Nou
   MQTT_TOPIC_PUBLISH_1="msh/EU_433/2/json/mqtt/!433e0370"
   ```

4. **Restart serviciul:**
   ```bash
   sudo systemctl restart mesh-tele
   ```

5. **Verifică funcționarea:**
   ```bash
   sudo journalctl -u mesh-tele -f
   ```
   Trimite "cq mesh" din mesh și verifică răspunsul.

---

### Când schimbi nodul 868MHz:

1. **Editează `.env`:**
   ```bash
   nano /home/bogdan/MQTT-Telegram/src/.env
   ```

2. **Modifică linia `MQTT_TOPIC_PUBLISH_2`:**
   ```bash
   MQTT_TOPIC_PUBLISH_2="msh/EU_868/2/json/mqtt/!NOUL_NODE_ID"
   ```

3. **Restart serviciul:**
   ```bash
   sudo systemctl restart mesh-tele
   ```

---

## Funcții Bot

### CQ Mesh
Răspunde automat la "cq mesh" cu confirmarea că te aude:
```
Roger SHORTNAME, te aud direct!
Roger SHORTNAME, te aud prin 2 hop-uri!
```

### Meteo (WX)
Trigger: "ibot wx" sau "ibot meteo"

Răspunde cu date meteo de la nodurile cu senzori (max 90 min vechi):
```
Meteo: MVO3:-5.5C,83%,1017hPa(22m) | IHG2:24.0C,31%,1015hPa(45m)
```

---

## Sistem de Ban

### Configurare în `.env`:
```bash
IGNORED_NODES="!1c07ca4c,!da9e92b0,RVE,KOKO,BBT"
```

Acceptă:
- **Hex IDs**: `!1c07ca4c` - banează nodul specific
- **Decimal IDs**: `470272588` - banează nodul specific
- **Patterns**: `RVE` - banează orice shortname care conține "RVE"

### Comportament:
- Nodurile banate sunt complet ignorate (text, telemetrie, poziție, nodeinfo)
- Dacă un nod nou match-uiește un pattern, ID-ul său e salvat în `nodes.json` pentru ban permanent
- Ban-ul persistă chiar dacă nodul își schimbă shortname-ul

---

## Fișiere

- `src/mesh-tele.py` - Codul principal al botului
- `src/.env` - Configurație (NU se commitează în git)
- `src/nodes.json` - Baza de date cu noduri (NU se commitează în git)

---

## Serviciu Systemd

```bash
# Status
sudo systemctl status mesh-tele

# Restart
sudo systemctl restart mesh-tele

# Logs
sudo journalctl -u mesh-tele -f

# Logs ultimele 50 linii
sudo journalctl -u mesh-tele -n 50 --no-pager
```

---

## Troubleshooting

### Botul nu răspunde la comenzi:
1. Verifică dacă serviciul rulează: `sudo systemctl status mesh-tele`
2. Verifică log-urile: `sudo journalctl -u mesh-tele -f`
3. Verifică dacă `MQTT_TOPIC_PUBLISH_1` are Node ID-ul corect în `.env`
4. Restart: `sudo systemctl restart mesh-tele`

### Diacritice corupte:
- Unele gateway-uri pot corupe diacriticele (înlocuiesc cu `\x00`)
- Botul ignoră automat mesajele corupte și așteaptă versiunea curată de la alt gateway

### Mesaje duplicate:
- Deduplicarea folosește Message ID + Sender ID
- Fereastra de deduplicare: 30 secunde
