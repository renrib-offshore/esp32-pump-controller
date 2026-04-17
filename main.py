"""
ESP32 IoT Water Level Monitor & Pump Controller
================================================
MicroPython firmware for dual-pump automation with local OLED display
and cloud monitoring via dweet.io.

Hardware
--------
- ESP32 (DOIT DevKit V1)
- SSD1306 OLED 128x64, I2C (SCL=GPIO21, SDA=GPIO22)
- 2x Analog level sensors on GPIO 34, 35 (ADC)
- 2x Relay modules on GPIO 26, 27
- Status LEDs: red / yellow / green per channel
- WiFi 802.11 b/g/n (built-in)

Author : Renato Ribeiro — Computer Engineer & Electronic Technician
"""

import ujson as json
import urequests as requests
import network
from machine import ADC, Pin, SoftI2C, Timer
from time import sleep
import SSD1306

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTROL_PERIOD_MS  = 350      # Main control loop interval
CLOUD_PERIOD_MS    = 15_000   # Cloud sync interval
ALARM_PERIOD_MS    = 30_000   # Alarm check interval
WIFI_RETRIES       = 5        # WiFi connection attempts
WIFI_RETRY_DELAY_S = 2        # Delay between attempts

# Cistern thresholds (scaled sensor units, ~0–5000)
CIS_ALARM_THRESH  = 500
CIS_WARN_LOW      = 500
CIS_WARN_HIGH     = 3000
CIS_PUMP_ON       = 2000
CIS_PUMP_OFF      = 4980

# Water tank thresholds (scaled sensor units, ~0–1000)
TANK_ALARM_THRESH = 300
TANK_PUMP_ON      = 400
TANK_PUMP_OFF     = 950

# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

i2c  = SoftI2C(scl=Pin(21), sda=Pin(22), freq=400_000)
oled = SSD1306.SSD1306_I2C(128, 64, i2c)


class PumpChannel:
    """One monitored channel: analog sensor, relay, and LED indicators."""

    def __init__(self, name, adc_pin, relay_pin,
                 pin_led_green, pin_led_red, pin_led_yellow=None,
                 scale=1.0,
                 pump_on_thresh=0, pump_off_thresh=1000,
                 alarm_thresh=0,
                 warn_low=None, warn_high=None):

        self.name    = name
        self.adc     = ADC(Pin(adc_pin))
        self.adc.atten(ADC.ATTN_11DB)          # Full 3.3 V range
        self.relay   = Pin(relay_pin, Pin.OUT)
        self.led_grn = Pin(pin_led_green, Pin.OUT)
        self.led_red = Pin(pin_led_red,   Pin.OUT)
        self.led_ylw = Pin(pin_led_yellow, Pin.OUT) if pin_led_yellow else None

        self.scale        = scale
        self.pump_on_th   = pump_on_thresh
        self.pump_off_th  = pump_off_thresh
        self.alarm_th     = alarm_thresh
        self.warn_low     = warn_low
        self.warn_high    = warn_high

        # Runtime state
        self.level    = 0
        self.pump_on  = False
        self.alarm    = False

    # ------------------------------------------------------------------

    def _set_leds(self, green=False, yellow=False, red=False):
        self.led_grn.value(green)
        self.led_red.value(red)
        if self.led_ylw:
            self.led_ylw.value(yellow)

    def update(self):
        """Read sensor, drive relay and LEDs. Must be called from the main loop."""
        raw        = self.adc.read()
        self.level = int(raw * self.scale)

        # LED status indicators
        if self.level <= self.alarm_th:
            self._set_leds(red=True)
            self.alarm = True
        elif self.warn_low is not None and self.warn_low < self.level < self.warn_high:
            self._set_leds(yellow=True)
            self.alarm = False
        else:
            self._set_leds(green=True)
            self.alarm = False

        # Relay — hysteresis prevents rapid switching
        if self.level <= self.pump_on_th:
            self.relay.value(1)
            self.pump_on = True
        elif self.level >= self.pump_off_th:
            self.relay.value(0)
            self.pump_on = False


# Cistern: GPIO 35 → ADC, GPIO 27 → relay, LEDs: green=25, yellow=15, red=5
cistern = PumpChannel(
    name           = "Cistern",
    adc_pin        = 35,
    relay_pin      = 27,
    pin_led_green  = 25,
    pin_led_yellow = 15,
    pin_led_red    = 5,
    scale          = 1.221,
    pump_on_thresh = CIS_PUMP_ON,
    pump_off_thresh= CIS_PUMP_OFF,
    alarm_thresh   = CIS_ALARM_THRESH,
    warn_low       = CIS_WARN_LOW,
    warn_high      = CIS_WARN_HIGH,
)

# Water tank: GPIO 34 → ADC, GPIO 26 → relay, LEDs: green=33, red=4 (no yellow)
tank = PumpChannel(
    name           = "Water Tank",
    adc_pin        = 34,
    relay_pin      = 26,
    pin_led_green  = 33,
    pin_led_red    = 4,
    scale          = 0.2442,
    pump_on_thresh = TANK_PUMP_ON,
    pump_off_thresh= TANK_PUMP_OFF,
    alarm_thresh   = TANK_ALARM_THRESH,
)

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def update_display(wifi_ok):
    b1 = "ON " if cistern.pump_on else "OFF"
    b2 = "ON " if tank.pump_on    else "OFF"
    net = "connected" if wifi_ok else "offline  "

    oled.fill(0)
    oled.text("B1-" + b1 + "  B2-" + b2,     0,  0)
    oled.text("Cisterna    Caixa",             0, 16)
    oled.text(str(cistern.level) + "  " + str(tank.level), 0, 29)
    oled.text(net,                            25, 50)
    oled.show()

# ---------------------------------------------------------------------------
# WiFi
# ---------------------------------------------------------------------------

def load_settings(path="wifi_settings.json"):
    with open(path) as f:
        return json.loads(f.read())


def connect_wifi(settings, retries=WIFI_RETRIES):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return wlan

    print("[wifi] Connecting to", settings["wifi_name"], "...")
    wlan.connect(settings["wifi_name"], settings["password"])

    for attempt in range(1, retries + 1):
        if wlan.isconnected():
            print("[wifi] Connected — IP:", wlan.ifconfig()[0])
            return wlan
        print("[wifi] Attempt", attempt, "/", retries)
        sleep(WIFI_RETRY_DELAY_S)

    print("[wifi] Connection failed.")
    return wlan

# ---------------------------------------------------------------------------
# Cloud sync
# ---------------------------------------------------------------------------

def post_to_cloud(url, headers):
    """Publish sensor data to dweet.io. Must be called from the main loop."""
    payload = {
        "cistern_level" : cistern.level,
        "tank_level"    : tank.level,
        "cistern_pump"  : cistern.pump_on,
        "tank_pump"     : tank.pump_on,
        "cistern_alarm" : cistern.alarm,
        "tank_alarm"    : tank.alarm,
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        result   = json.loads(response.content)
        response.close()
        created = result["with"]["created"]
        print("[cloud] Synced at", created,
              "| cis =", cistern.level,
              "| tank =", tank.level)
    except Exception as e:
        print("[cloud] POST failed:", e)

# ---------------------------------------------------------------------------
# Alarm reporting
# ---------------------------------------------------------------------------

def check_alarms():
    if cistern.alarm:
        print("[ALARM] Cistern LOW — check mains supply or pump failure.")
    if tank.alarm:
        print("[ALARM] Water tank LOW — check cistern pump.")
    if cistern.level == 0:
        print("[ALARM] Cistern reads zero — sensor or pump fault.")
    if tank.level == 0:
        print("[ALARM] Tank reads zero — sensor or pump fault.")

# ---------------------------------------------------------------------------
# Timer flags
# Callbacks run in interrupt context: only set a boolean flag here.
# All blocking work (network, display) happens in the main loop.
# ---------------------------------------------------------------------------

_do_cloud = False
_do_alarm = False


def _cloud_flag(_):
    global _do_cloud
    _do_cloud = True


def _alarm_flag(_):
    global _do_alarm
    _do_alarm = True

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    global _do_cloud, _do_alarm

    settings = load_settings()
    wlan     = connect_wifi(settings)
    url      = "https://dweet.io:443/dweet/for/" + settings["thing"]
    headers  = {"Content-Type": "application/json"}

    Timer(1).init(period=CLOUD_PERIOD_MS, mode=Timer.PERIODIC, callback=_cloud_flag)
    Timer(2).init(period=ALARM_PERIOD_MS, mode=Timer.PERIODIC, callback=_alarm_flag)

    while True:
        # 1. Read sensors and drive relays + LEDs
        cistern.update()
        tank.update()

        # 2. Check WiFi, reconnect if dropped
        if not wlan.isconnected():
            print("[wifi] Connection lost, reconnecting...")
            wlan = connect_wifi(settings, retries=3)

        wifi_ok = wlan.isconnected()

        # 3. Update local display
        update_display(wifi_ok)

        # 4. Deferred cloud sync (flag set by timer interrupt)
        if _do_cloud:
            _do_cloud = False
            if wifi_ok:
                post_to_cloud(url, headers)

        # 5. Deferred alarm check (flag set by timer interrupt)
        if _do_alarm:
            _do_alarm = False
            check_alarms()

        sleep(CONTROL_PERIOD_MS / 1000)


main()
