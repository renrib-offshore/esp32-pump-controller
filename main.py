'''
 07.70 - Read a potentiometer

 This sketch shows how to read a potentiometer and use it to control
 the brightness of an LED.

 When the button is pressed, the LED turns on for 500msec.

 Components
 ----------
  - ESP32
  - 330Ohm resistor for the LED
  - 5mm LED
  -     Connect anode to GPIO 21
  -     Connect cathode to GND via the resistor
  - 10KOhm potentiometer
  -     Connect the pin to GPIO 34
  -     Connect one of the side pins to 3.3V
  -     Connect the last pin to GND
  - Wires
  - Breadboard

 Documentation:
 Pins and GPIO: https://micropython-docs-esp32.readthedocs.io/en/esp32_doc/esp32/quickref.html#pins-and-gpio
 sleep_ms: http://docs.micropython.org/en/latest/library/utime.html?highlight=utime%20sleep#utime.sleep_ms
 ADC: https://micropython-docs-esp32.readthedocs.io/en/esp32_doc/esp32/quickref.html#adc-analog-to-digital-conversion
 int(): https://docs.python.org/3/library/functions.html#int

 Beware:
 By default, ADC values are 12 bits, therefore they range from 0 to 4095.
 By default, PWM values are 10 bits, therefore they range from 0 to 1023.
 We must scale a ADC value to the PWM range to correctly control the LED.
 To do so, divide 1023/4095 = 0.24, and multiply the actual ADC value by 0.24.

 Course:
 MicroPython with the ESP32
 https://techexplorations.com

'''

from machine import ADC, Pin, PWM, SoftI2C, Timer
import SSD1306
import network, usys
import urequests as requests
import ujson as json
from time import sleep
i2c = SoftI2C(scl=Pin(21), sda=Pin(22), freq=400000)  # Usando o software I2C WORKS
 
oled_width = 128
oled_height = 64
oled = SSD1306.SSD1306_I2C(oled_width, oled_height, i2c)
pwmB1 = PWM(Pin(18))    # cria um ADC object no pino ADC 
pwmB2 = PWM(Pin(19))
adcB1 = ADC(Pin(35))     # cria um LED object
adcB2 = ADC(Pin(34))
B1 = Pin(27, Pin.OUT)
B2 = Pin(26, Pin.OUT)
ledvd1 = Pin(25, Pin.OUT)
ledam = Pin(15, Pin.OUT)
ledvm1 = Pin(5, Pin.OUT)
ledvm2 = Pin(4, Pin.OUT)
ledvd2 = Pin(33, Pin.OUT)
adcB1.atten(ADC.ATTN_11DB)  # Range máximo: 3.3v
adcB2.atten(ADC.ATTN_11DB)
tim = Timer(0)
dht_timer = Timer(1)
alarmtimer = Timer(2)
Btimer = Timer(3)
with open("/wifi_settings_test.json") as credentials_json:   # Permite abrir e ler o arquivo .json na memória do esp32
    settings = json.loads(credentials_json.read())

headers = {"Content-Type": "application/json"}

url = "https://dweet.io:443/dweet/for/" + settings["thing"]


def do_connect():
    wlan.active(True)             # Ativa a interface de rede
    if not connection():    # Se não estiver conectado, tenta conectar.
        print('connecting to network...')
        wlan.connect(settings["wifi_name"], settings["password"])  # Conecta usando as credenciais do arquivo .json.
        if not connection():
            print("Can't connect to network with given credentials.")
             # Interrompe o script e retorna para o shell.
    print('network config:', connection())

wlan = network.WLAN(network.STA_IF) # cria o objeto no formato Station.
connection = wlan.isconnected
net_test = False
do_connect()

if connection() == True:    # Testa a rede
        print("Connected")
        print("My IP address: ", wlan.ifconfig()[0]) # Prints the acquired IP address.
        net_test = True
else:
    print("Not connected")
    net_test = False


def control(): #define os controles do hardware.
    
    global BB1
    BB1 = False
    global BB2
    BB2 = False
    global alarmB1
    alarmB1= False
    global alarmB2
    alarmB2 = False
    potB1_value = adcB1.read()  #lê o valor do potenciometro da bomba
    potB2_value = adcB2.read()
    pwmB1_value = int(potB1_value * 0.2498) #Valor do led
    pwmB2_value = int(potB2_value * 0.2498)
    global cis_value
    cis_value = int(potB1_value * 1.221) #Valor simulado do flowmeter de 0 a 30 l/min
    global caixa_value
    caixa_value = int(potB2_value * 0.2442)
   
   # Condições para atuação da bomba da cisterna
    cond1 = cis_value <= 500
    cond2 = cis_value >500
    cond3 = cis_value <3000
    cond4 = cis_value >= 3000
    cond5 = cis_value <= 2000
    cond6 = cis_value >=4980
    
    if cond1: #liga o led indicador visual vermelho
        ledvm1.on()
        ledvd1.off()
        ledam.off()
        alarmB1 = True
    
    elif cond2 and cond3: #liga o led indicador visual amarelo
        ledvm1.off()
        ledvd1.off()
        ledam.on()
        alarmB1 = False
        
    elif cond4: #liga o led indicador visual verde
        ledvm1.off()
        ledvd1.on()
        ledam.off()
        alarmB1 = False
    if cond5: #aciona a bomba da cisterna
        B1.value(1)
        BB1 = True
        
    elif cond6: #desaciona a bomba da cisterna
        B1.value(0)
        BB1 = False
    
    # Condições para atuação da bomba da caixa d'água
    cond7 = caixa_value < 300
    cond8 = caixa_value > 300
    cond9 = caixa_value <= 400
    cond10 = caixa_value >950
    
    if cond7: #liga o led indicador visual vermelho
        ledvm2.on()
        ledvd2.off()
        alarmB2 = True
        
    elif cond8: #liga o led indicador visual verde
        ledvm2.off()
        ledvd2.on()
        alarmB2 = False
    if cond9: #aciona a bomba da caixa d'água
        B2.value(1)
        BB2 = True
        
    elif cond10:
        B2.value(0)
        BB2 = False
        
    
    oled.fill(0) #fundo preto do display
    oled.text("Cisterna", 0, 16)
    oled.text("Caixa", 80, 16)
    
    if BB1 == True:
        oled.text("B1-lig", 0, 0)
        
    elif BB1 == False:
        oled.text("B1-des", 0, 0)
      
    if BB2 == True:
        oled.text("B2-lig", 65, 0)
        
    elif BB2 == False:
        oled.text("B2-des", 65, 0)
         
      
    if net_test == True:
        oled.text("connected", 25, 50)
    
    elif net_test == False:
        oled.text("offline", 32, 50)
        
    oled.text(str(cis_value), 0, 29)
    oled.text(str(caixa_value), 80, 29)
    oled.show()
    pwmB1.duty(pwmB1_value)  # 0.24 derives from scaling 0..4095 to 0..1023 =>
    pwmB2.duty(pwmB2_value)
    sleep(0.1)
    
       
def alarmgeral():
    control()
    if alarmB1 == True:
        print("Atenção: ou o sistema está em falha ou estamos sem água na rua. Verifique!!!")
        
    if alarmB2 == True:
        print("Atenção: ou o sistema está em falha ou estamos sem água na cisterna. Verifique!!!")
        
    if cis_value == 0:
        print("Nível zerado! Verifique a bomba da cisterna!")
        
    if caixa_value == 0:
        print("Nível zerado! Verifique a bomba da caixa d'água!")



def post_to_dweet_isr(event):
    
    control()
    cis_level = cis_value
    caixa_level = caixa_value
    alarmtext = alarmgeral()
    data = { "cis": cis_level, "caixa": caixa_level, "alger":alarmtext }

    response = requests.post(url, headers=headers, data=json.dumps(data)) # Make a POST request
    dweet_back = json.loads(response.content)            # The response from Dweet.io comes as a JSON object.
    print("\nResponse from Dweet.io: ", dweet_back)
    print("Created: ", dweet_back["with"]["created"])
    print("Transaction: ", dweet_back["with"]["transaction"])
    print("thing: ", dweet_back["with"]["thing"])
    print("Nivel da cisterna: ", dweet_back["with"]["content"]["cis"])
    print("Nivel da caixa: ", dweet_back["with"]["content"]["caixa"])
    print("Status alger: ", dweet_back["with"]["content"]["alger"])
alarmtimer.init(period=30000, mode=Timer.PERIODIC, callback=lambda t:alarmgeral())
tim.init(period=350, mode=Timer.PERIODIC, callback=lambda t:control())
dht_timer.init(period=15000, mode=Timer.PERIODIC, callback=post_to_dweet_isr)
alarmgeral()
        
    
    
           
    
                
    
        
    
    