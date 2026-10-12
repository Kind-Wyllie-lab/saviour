/**
 * Dummy APA shock Arduino: a simulated stand-in for shock_controller.ino.
 *
 * For developing / testing the GUI and apa_arduino module on a desk with a
 * bare Arduino (Uno, Leonardo, ...): no shock generator, no DB25, no library
 * dependencies. Speaks the same serial protocol as the real sketch, but every
 * "pin" is virtual: H/L commands and the current setting only change what the
 * state message reports. It drives no pins except LED_BUILTIN, lit while the
 * (virtual) trigger is on, i.e. while a shock would be attempted.
 *
 * Simulated shock generator: while the trigger is on and a current is set,
 * SELF_TEST_IN reads 1 (an animal on the grid, shock "delivered"); the grid
 * self-test (SELF_TEST_OUT low, trigger off) reads 0 (no short).
 *
 * NEVER flash this onto a rig's real shock Arduino.
 *
 * Protocol (same as shock_controller.ino; host is shock.py / protocol.py):
 *   <I:>          -> <I:SHOCK>         identity (also sent on boot)
 *   <K:>          keepalive; 1 s of host silence turns the trigger off and the
 *                 next packet re-announces <I:SHOCK>
 *   <C:mA>        current, 0-1.0 mA in 0.02 mA steps (8 active-low bits)
 *   <T:s> <Y:s>   pulse on / off time in seconds
 *   <H:pin> <L:pin>  virtual pin high / low (9 = TRIGGER_OUT, active low;
 *                 12 = SELF_TEST_OUT, active low)
 *   <Z:> <X:>     start / stop a firmware-timed pulse train (counts pulses,
 *                 capped at 50 per trial); <R:> resets the count
 *   state every 50 ms:
 *     <D:b0,..,b7,self_test_out,self_test_in,trigger_out,time_on_ms,time_off_ms,pulses,>
 *
 * Flash from the module Pi (stop saviour.service first, it holds the port):
 *   arduino-cli compile --fqbn arduino:avr:uno dummy_shock
 *   arduino-cli upload  --fqbn arduino:avr:uno -p /dev/ttyACMx dummy_shock
 */

const char SYSTEM_ID[] = "SHOCK";
const unsigned long STATE_PERIOD_MS = 50;
const unsigned long HOST_TIMEOUT_MS = 1000;
const float MAX_CURRENT_MA = 1.0;
const int MAX_PULSES_PER_TRIAL = 50;
const int TRIGGER_OUT = 9;
const int SELF_TEST_OUT = 12;

// Virtual pin levels (HIGH = off for the active-low trigger/self-test lines).
byte triggerOut = HIGH;
byte selfTestOut = HIGH;
byte currentBits = 0;          // bit i set = CURRENT_OUT[i] driven LOW

int timeOn = -1;               // ms
int timeOff = -1;              // ms
float currentMa = -1;
int globalPulseCounter = 0;

// Firmware-timed sequence (<Z:>)
bool activated = false;
unsigned long phaseStartMs = 0;

unsigned long lastStateMs = 0;
unsigned long lastHostMs = 0;
bool hostActive = false;
bool failsafeTripped = false;

char rxBuf[64];
byte rxLen = 0;
bool inPacket = false;

void sendMessage(const char *type, const String &message) {
  Serial.print('<');
  Serial.print(type);
  Serial.print(':');
  Serial.print(message);
  Serial.println('>');
}

void setTrigger(byte level) {
  triggerOut = level;
  digitalWrite(LED_BUILTIN, level == LOW ? HIGH : LOW);
}

void deactivate() {
  activated = false;
  setTrigger(HIGH);
}

void setVirtualPin(int pin, byte level) {
  if (pin == TRIGGER_OUT) setTrigger(level);
  else if (pin == SELF_TEST_OUT) selfTestOut = level;
  // Other pins: accepted and ignored.
}

void handleCommand(const String &cmd, const String &param) {
  if (cmd == "K") return;  // noteHostPacket() already ran
  if (cmd == "I") {
    sendMessage("I", SYSTEM_ID);
  } else if (cmd == "H" || cmd == "L") {
    if (param.length() == 0) sendMessage("E", "No param given");
    else setVirtualPin(param.toInt(), cmd == "H" ? HIGH : LOW);
  } else if (cmd == "C") {
    float ma = param.toFloat();
    if (param.length() > 0 && ma >= 0 && ma <= MAX_CURRENT_MA) {
      currentMa = ma;
      currentBits = (byte)(((int)(ma * 1000)) / 20);  // 20 uA per step, as the real board
      sendMessage("S", "Current set to " + String(ma, 2) + "mA");
    } else {
      sendMessage("E", "Current must be 0.0-" + String(MAX_CURRENT_MA, 1) + "mA");
    }
  } else if (cmd == "T" || cmd == "Y") {
    float s = param.toFloat();
    if (s > 0) {
      if (cmd == "T") timeOn = (int)(s * 1000);
      else timeOff = (int)(s * 1000);
      sendMessage("S", String(cmd == "T" ? "On" : "Off") + " time set to " + String(s, 3) + "s");
    } else {
      sendMessage("E", String("Time ") + (cmd == "T" ? "on" : "off") + " must be greater than 0");
    }
  } else if (cmd == "R") {
    globalPulseCounter = 0;
    sendMessage("S", "Pulses reset, pulses=0, verified_pulses=0");
  } else if (cmd == "Z") {
    if (timeOn <= 0 || timeOff <= 0) sendMessage("E", "Time on/off must be greater than 0");
    else if (currentMa <= 0) sendMessage("E", "Current must be 0.0-" + String(MAX_CURRENT_MA, 1) + "mA");
    else if (activated) sendMessage("E", "Shock sequence already active");
    else {
      activated = true;
      phaseStartMs = millis();
      setTrigger(LOW);
      globalPulseCounter++;
      sendMessage("S", "Shock sequence started");
    }
  } else if (cmd == "X") {
    if (activated) {
      deactivate();
      sendMessage("S", "Shocks stopped, pulses=" + String(globalPulseCounter));
    } else {
      sendMessage("E", "No active shock sequence");
    }
  }
}

void noteHostPacket() {
  lastHostMs = millis();
  hostActive = true;
  if (failsafeTripped) {
    failsafeTripped = false;
    sendMessage("I", SYSTEM_ID);
  }
}

void checkHostSilence() {
  if (hostActive && millis() - lastHostMs > HOST_TIMEOUT_MS) {
    hostActive = false;
    failsafeTripped = true;
    deactivate();
    selfTestOut = HIGH;
    sendMessage("E", "Host silent: trigger off");
  }
}

// Non-blocking <cmd:param> reader.
void listen() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '<') {
      inPacket = true;
      rxLen = 0;
    } else if (c == '>' && inPacket) {
      inPacket = false;
      rxBuf[rxLen] = '\0';
      String payload(rxBuf);
      noteHostPacket();
      int sep = payload.indexOf(':');
      if (sep < 0) handleCommand(payload, "");
      else handleCommand(payload.substring(0, sep), payload.substring(sep + 1));
    } else if (inPacket && rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    }
  }
}

// Firmware-timed pulse train, the <Z:> path (the module normally drives
// pulses itself with L:9 / H:9 and never sends Z).
void runSequence() {
  if (!activated) return;
  unsigned long elapsed = millis() - phaseStartMs;
  if (triggerOut == LOW && elapsed >= (unsigned long)timeOn) {
    setTrigger(HIGH);
    phaseStartMs = millis();
  } else if (triggerOut == HIGH && elapsed >= (unsigned long)timeOff) {
    if (globalPulseCounter >= MAX_PULSES_PER_TRIAL) {
      deactivate();
      return;
    }
    setTrigger(LOW);
    globalPulseCounter++;
    phaseStartMs = millis();
  }
}

void sendState() {
  // Simulated generator: delivering while the trigger is on with a current set.
  byte selfTestIn = (triggerOut == LOW && currentBits > 0) ? 1 : 0;
  String state = "";
  for (int i = 0; i < 8; i++) {
    state += String((currentBits >> i) & 1 ? 0 : 1) + ",";  // active low
  }
  state += String(selfTestOut) + "," + String(selfTestIn) + "," + String(triggerOut) + ",";
  state += String(timeOn) + "," + String(timeOff) + "," + String(globalPulseCounter) + ",";
  sendMessage("D", state);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(115200);
  delay(1000);
  sendMessage("I", SYSTEM_ID);
}

void loop() {
  listen();
  checkHostSilence();
  runSequence();
  if (millis() - lastStateMs >= STATE_PERIOD_MS) {
    lastStateMs = millis();
    sendState();
  }
}
