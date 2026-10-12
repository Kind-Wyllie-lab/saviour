/**
 * Dummy APA motor Arduino: a simulated stand-in for motor_controller.ino.
 *
 * For developing / testing the GUI and apa_arduino module on a desk with a
 * bare Arduino (Uno, Leonardo, ...): no motor shield, no encoder, no library
 * dependencies. Speaks the same serial protocol as the real sketch, and the
 * "motor" is a first-order model: RPM eases towards the setpoint while
 * running and back to 0 when stopped, and the encoder angle integrates it.
 * Drives no pins except LED_BUILTIN, lit while the motor is "running".
 *
 * NEVER flash this onto a rig's real motor Arduino.
 *
 * Protocol (same as motor_controller.ino; host is motor.py / protocol.py):
 *   <I:>        -> <I:MOTOR>           identity (also sent on boot)
 *   <K:>        keepalive; 1 s of host silence stops the motor and the next
 *               packet re-announces <I:MOTOR> (host treats it as a reset)
 *   <S:rpm>     set target RPM (0 also stops)
 *   <M:>        start (needs a setpoint > 0)
 *   <N:>        stop
 *   <F:units>   feedforward (the real sketch's learned motor command); here
 *               it just shortens the simulated spin-up
 *   <SET_PID:kp,ki,kd>  accepted and ignored
 *   state every 50 ms: <D:rpm,position_deg,setpoint,output,> where output is
 *               UNITS_PER_RPM * rpm while running, so the host can learn it
 *
 * Flash from the module Pi (stop saviour.service first, it holds the port):
 *   arduino-cli compile --fqbn arduino:avr:uno dummy_motor
 *   arduino-cli upload  --fqbn arduino:avr:uno -p /dev/ttyACMx dummy_motor
 */

const char SYSTEM_ID[] = "MOTOR";
const unsigned long STATE_PERIOD_MS = 50;
const unsigned long HOST_TIMEOUT_MS = 1000;
const float RAMP_TAU_S = 3.0;       // time constant of the simulated spin-up
const float FF_RAMP_TAU_S = 1.0;    // ... once a feedforward has been sent
const float UNITS_PER_RPM = 48.5;   // simulated motor command per rpm
const float RPM_NOISE = 0.03;       // +/- jitter on the reported RPM

float rpmSetpoint = 0;
float feedforward = 0;
float rpmCurrent = 0;
float position = 0;                 // degrees, 0-360
bool running = false;

unsigned long lastStateMs = 0;
unsigned long lastSimMs = 0;
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

void stopMotor() {
  running = false;
  digitalWrite(LED_BUILTIN, LOW);
}

void handleCommand(const String &cmd, const String &param) {
  if (cmd == "I") {
    sendMessage("I", SYSTEM_ID);
  } else if (cmd == "K") {
    // noteHostPacket() already ran
  } else if (cmd == "S") {
    if (param.length() == 0) {
      sendMessage("E", "No param given");
      return;
    }
    rpmSetpoint = param.toFloat();
    if (rpmSetpoint == 0) stopMotor();
  } else if (cmd == "M") {
    if (rpmSetpoint > 0) {
      running = true;
      digitalWrite(LED_BUILTIN, HIGH);
    } else {
      sendMessage("E", "rpmSetpoint=0");
    }
  } else if (cmd == "N") {
    stopMotor();
  } else if (cmd == "F") {
    feedforward = param.toFloat();
  } else if (cmd == "SET_PID") {
    // accepted for protocol parity; the simulation has no PID
  } else {
    sendMessage("E", "No logic for " + cmd + " " + param);
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
    stopMotor();
    sendMessage("E", "Host silent: motor stopped");
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

void simulate() {
  unsigned long now = millis();
  float dt = (now - lastSimMs) / 1000.0;
  lastSimMs = now;
  float target = running ? rpmSetpoint : 0;
  float tau = feedforward > 0 ? FF_RAMP_TAU_S : RAMP_TAU_S;
  rpmCurrent += (target - rpmCurrent) * (dt / (tau + dt));
  position += rpmCurrent * 6.0 * dt;   // rpm * 360 deg / 60 s
  while (position >= 360.0) position -= 360.0;
}

void sendState() {
  float jitter = rpmCurrent > 0.01 ? (random(-100, 101) / 100.0) * RPM_NOISE : 0;
  float output = running ? UNITS_PER_RPM * rpmCurrent : 0;
  String state = String(rpmCurrent + jitter) + "," + String(position) + "," + String(rpmSetpoint) + "," +
                 String(output) + ",";
  sendMessage("D", state);
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(115200);
  randomSeed(analogRead(A0));
  delay(500);
  sendMessage("I", SYSTEM_ID);
  lastSimMs = millis();
}

void loop() {
  listen();
  checkHostSilence();
  simulate();
  if (millis() - lastStateMs >= STATE_PERIOD_MS) {
    lastStateMs = millis();
    sendState();
  }
}
