// TODO: Implement MSG_ID
// TODO: Switch to Char arrays instead of strings
const char MSG_IDENTITY [] = "I";
const char MSG_ACK [] = "A"; // Used to acknowledge received message, may be unnecessary.
const char MSG_DATA [] = "D"; 
const char MSG_WRITE_SELF_TEST_OUT [] = "W";
const char MSG_WRITE_PIN_HIGH [] = "H";
const char MSG_WRITE_PIN_LOW [] = "L";
const char MSG_ERROR [] = "E";

const char MSG_CURRENT [] = "C";

const char IDENTITY [] = "SHOCK";
const int SELF_TEST_OUT = 12;
const int SELF_TEST_IN = 2;
const int TRIGGER_OUT = 9;
const int CURRENT_OUT[8] = {17, 16, 15, 14, 4, 5, 6, 7};


// Current control limits
const float MAX_CURRENT_MA = 1.0;         // Maximum current in mA
const float CURRENT_STEP_MA = 0.2;        // Current resolution in mA
const int MAX_CURRENT_MICROAMPS = 1000;   // Maximum current in microamps (1.0mA * 1000)

// System state - shock intensity settings, self test in, self test out, trigger.
int state[11];
unsigned long lastSentState = 0;
int sendStatePeriod = 3000;

String lastCommand;
String lastParam;

bool acknowledgeMessages = false;

void readState() {
  for (int i=0; i<8; i++) {
    state[i] = digitalRead(CURRENT_OUT[i]);
  }
  state[8] = digitalRead(SELF_TEST_OUT);
  state[9] = digitalRead(SELF_TEST_IN);
  state[10] = digitalRead(TRIGGER_OUT);
}

void sendState() {
  String stateMessage = "";
  for(int i=0; i<11; i++){
    stateMessage += String(state[i]) + ",";
  }
  sendMessage(MSG_DATA, stateMessage);
  lastSentState = millis();
}



void sendMessage(String type, String message) {
  String payload = "<" + type + ":" + message + ">";
  Serial.println(payload);
}

void handleCommand(String cmd, String param) {
  // if (cmd == MSG_WRITE_SELF_TEST_OUT) {
  //   sendMessage(MSG_WRITE_SELF_TEST_OUT, "I don't know what to do yet.");
  // }
  if (cmd == MSG_WRITE_PIN_LOW) {
    int pin = param.toInt();
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }
  if (cmd == MSG_WRITE_PIN_HIGH) {
    int pin = param.toInt();
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);
  }
  if (cmd == MSG_CURRENT) {
    if (param == "NONE") {
      sendMessage(MSG_ERROR, "No param");
    } else {
      float currentMa = param.toFloat();
      if (currentMa >= 0 && currentMa <= MAX_CURRENT_MA) {
        currentAmps = currentMa;
        current = (int)(currentMa * 1000); // Convert to microamps
          byte db25out = calculateCurrentOutput(current);
          setCurrent(db25out);
      } else {
        sendMessage(MSG_ERROR,  ("Current must be 0.0-" + String(MAX_CURRENT_MA, 1) + "mA").c_str());
      }
  }
}

byte calculateCurrentOutput(int current) {
  byte binary_out;
  if (current <= MAX_CURRENT_MICROAMPS && current >= 0) {
    int value = current / 20;  // Convert to 0.2mA steps (20 microamps per step)
    binary_out = byte(value);
  } else {
    binary_out = B00000000;   // Safe default - no current
  }	
  return binary_out;
}

void setCurrent(byte binary_current) {
  // Apply LSB to MSB pattern to pins
  for (int i = 0, mask = 1; i < 8; i++, mask = mask << 1) {
    // Active low logic: 1 = LOW, 0 = HIGH
    if (binary_current & mask) {
      digitalWrite(CURRENT_OUT[i], LOW);
    } else {
      digitalWrite(CURRENT_OUT[i], HIGH);
    }	
  }
}


void parseCommand(String payload) {
  int firstSep = payload.indexOf(':'); // Index of the first : that preceeds command
  String cmd;
  String param;
  if (firstSep < 0) {
    cmd = payload;
    param = "";
  }
  else {
    cmd = payload.substring(0, firstSep); // e.g. MSG_WRITE
    param = payload.substring(firstSep + 1); 
  }

  sendMessage(cmd, param);
  handleCommand(cmd, param);
}

void listen() {
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('>'); // Read until >
    if (incoming.startsWith("<")) {
      incoming.remove(0, 1); // Drop <
      String payload = incoming; // Drop >

      // Send acknowledgement
      if (acknowledgeMessages == true) {
        sendMessage(MSG_ACK, payload);
      }

      // Process command
      parseCommand(payload);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(SELF_TEST_OUT, OUTPUT);
  pinMode(TRIGGER_OUT, OUTPUT);
  pinMode(SELF_TEST_IN, INPUT);

  digitalWrite(SELF_TEST_OUT, HIGH);
  digitalWrite(TRIGGER_OUT, HIGH);
  for(int i=0; i<8; i++) {
    digitalWrite(CURRENT_OUT[i], HIGH);
  }

  sendMessage(MSG_IDENTITY, IDENTITY);
}

void loop() {
  // Read pins
  listen();


  if(millis() - lastSentState > sendStatePeriod) {
    readState();
    sendState();
  }

}
