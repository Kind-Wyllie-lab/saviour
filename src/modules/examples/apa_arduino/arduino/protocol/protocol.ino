/*
  Protocol test sketch
  - Periodically sends RESPONSE, DATA, and ACK messages
  - Computes XOR checksum
  - Verifies incoming messages from Raspberry Pi
*/

const char MSG_ACK [] = "ACK";
const char MSG_NACK [] = "NACK";
const char MSG_SUCCESS [] = "SUCCESS";
const char MSG_ERROR [] = "ERROR";
const char MSG_IDENTITY [] = "IDENTITY";
const char MSG_DATA [] =  "DATA";
const char MSG_ID_UNSOLICITED [] = "0"; // Message ID for unsolicited messages
// const char CMD_IDENTITY [] = "GET_IDENTITY";
// const char CMD_DATA [] = "GET_DATA";

int seqId = 0; 

// Function prototypes
String makeMessage(String payload);
void sendMessage(String type, String msgId, String message);
void listen();

String makeMessage(String payload) {
  // TODO: Should it request type + payload or expect it to be preformatted?
  String sequencePayload = payload + ":S" + seqId;
  uint8_t chk = 0;
  for (size_t i = 0; i < sequencePayload.length(); i++) {
    chk ^= sequencePayload[i];   // XOR checksum
  }

  char buf[5];
  sprintf(buf, "%02X", chk);   // hex string (2 chars)
  String msg = "<" + sequencePayload + "|" + String(buf) + ">";
  seqId += 1;
  return msg;
}

void sendMessage(String type, String msgId, String message){
  String payload = type + ":" + msgId + ":" + message;
  Serial.println(makeMessage(payload));
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  sendMessage(MSG_IDENTITY, "M0", "PROTOCOL");
//   Serial.println(makeMessage("RESPONSE:OK:Motor started at RPM=2"));
}

void listen() {
  // --- Listen for incoming messages ---
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('>');  // read until '>'
    if (incoming.startsWith("<")) {
      incoming.remove(0, 1); // drop '<'

      int sep = incoming.lastIndexOf('|');
      if (sep > 0) {
        String payload = incoming.substring(0, sep);
        String chkStr = incoming.substring(sep + 1);

        // compute checksum
        uint8_t chk = 0;
        for (size_t i = 0; i < payload.length(); i++) {
          chk ^= payload[i];
        }

        // parse hex checksum
        uint8_t chkRecv = (uint8_t) strtol(chkStr.c_str(), NULL, 16);

        if (chk == chkRecv) {
          Serial.println(makeMessage("ACK:" + payload)); // Send acknowledgement

          // TODO: Process command - definitely abstract this to a new method
          // Split by ':'
          int firstSep = payload.indexOf(':'); // Index of the :
          if (firstSep > 0) {
            String msgId = payload.substring(0, firstSep); // e.g. M25
            String rest = payload.substring(firstSep + 1); // e.g. SET_SPEED: 2.0

            // From here could use "if rest.startsWith" ? Handle command function?

            int secondSep = rest.indexOf(':');
            String command;
            String arg;
            if (secondSep == -1) {
                command = rest; // no arg
                arg = "";
            } else {
                command = rest.substring(0, secondSep);
                arg = rest.substring(secondSep + 1);
            }

            if (command == "GET_DATA") {
                float rpm = random(18,22) / 10.0;
                float pos = random(0,3600) / 10.0;
                String sendData = "RPM=" + String(rpm) + ", POS=" + String(pos);
                sendMessage(MSG_DATA, msgId, sendData);
            } else if (command == MSG_IDENTITY) {
                // Serial.println(makeMessage(String(MSG_IDENTITY) + ":" + msgId + ":PROTOCOL"));
                sendMessage(MSG_IDENTITY, msgId, "PROTOCOL");
            } else {
                // Handle command
                String errorMessage = "No logic for " + command + " " + arg;
                sendMessage(MSG_ERROR, msgId, errorMessage);
                // Serial.println(makeMessage("ERROR:" + msgId + ':' + "No logic for " + command + " with arg " + arg));
            }


          }

        } else { // Failed checksum
          Serial.println(makeMessage("ERROR:CHK_FAIL:" + payload)); // Add message ID?
          // sendMesssage(MSG_ERROR, "CHK_FAIL", + payload);
        }
      }
    }
  }
}

void handleCommand(String payload) {
  int firstSep = payload.indexOf(':'); // Index of the :
  if (firstSep > 0) {
    String msgId = payload.substring(0, firstSep); // e.g. M25
    String rest = payload.substring(firstSep + 1); // e.g. SET_SPEED: 2.0

    // From here could use "if rest.startsWith" ? Handle command function?

    int secondSep = rest.indexOf(':');
    String command;
    String arg;
    if (secondSep == -1) {
        command = rest; // no arg
        arg = "";
    } else {
        command = rest.substring(0, secondSep);
        arg = rest.substring(secondSep + 1);
    }

    if (command == "GET_DATA") {
        float rpm = random(18,22) / 10.0;
        float pos = random(0,3600) / 10.0;
        String sendData = "RPM=" + String(rpm) + ", POS=" + String(pos);
        sendMessage(MSG_DATA, msgId, sendData);
    } else if (command == MSG_IDENTITY) {
        // Serial.println(makeMessage(String(MSG_IDENTITY) + ":" + msgId + ":PROTOCOL"));
        sendMessage(MSG_IDENTITY, msgId, "PROTOCOL");
    } else {
        // Handle command
        String errorMessage = "No logic for " + command + " " + arg;
        sendMessage(MSG_ERROR, msgId, errorMessage);
        // Serial.println(makeMessage("ERROR:" + msgId + ':' + "No logic for " + command + " with arg " + arg));
    }


  }
}

void loop() {
  // --- Send test data every 2 seconds ---
  static unsigned long lastSend = 0;
  if (millis() - lastSend > 1000) {
    lastSend = millis();
    // Serial.println(makeMessage("DATA:rpm=2,position=180"));
    // Serial.println(makeMessage("ACK:Loop alive"));
  }

  // // --- Listen for incoming messages ---
  listen();
  // if (Serial.available()) {
  //   String incoming = Serial.readStringUntil('>');  // read until '>'
  //   if (incoming.startsWith("<")) {
  //     incoming.remove(0, 1); // drop '<'

  //     int sep = incoming.lastIndexOf('|');
  //     if (sep > 0) {
  //       String payload = incoming.substring(0, sep);
  //       String chkStr = incoming.substring(sep + 1);

  //       // compute checksum
  //       uint8_t chk = 0;
  //       for (size_t i = 0; i < payload.length(); i++) {
  //         chk ^= payload[i];
  //       }

  //       // parse hex checksum
  //       uint8_t chkRecv = (uint8_t) strtol(chkStr.c_str(), NULL, 16);

  //       if (chk == chkRecv) {
  //         Serial.println(makeMessage("ACK:" + payload)); // Send acknowledgement

  //         // TODO: Process command - definitely abstract this to a new method
  //         // Split by ':'
  //         int firstSep = payload.indexOf(':'); // Index of the :
  //         if (firstSep > 0) {
  //           String msgId = payload.substring(0, firstSep); // e.g. M25
  //           String rest = payload.substring(firstSep + 1); // e.g. SET_SPEED: 2.0

  //           // From here could use "if rest.startsWith" ? Handle command function?

  //           int secondSep = rest.indexOf(':');
  //           String command;
  //           String arg;
  //           if (secondSep == -1) {
  //               command = rest; // no arg
  //               arg = "";
  //           } else {
  //               command = rest.substring(0, secondSep);
  //               arg = rest.substring(secondSep + 1);
  //           }

  //           if (command == "GET_DATA") {
  //               float rpm = random(18,22) / 10.0;
  //               float pos = random(0,3600) / 10.0;
  //               String sendData = "RPM=" + String(rpm) + ", POS=" + String(pos);
  //               sendMessage(MSG_DATA, msgId, sendData);
  //           } else if (command == MSG_IDENTITY) {
  //               // Serial.println(makeMessage(String(MSG_IDENTITY) + ":" + msgId + ":PROTOCOL"));
  //               sendMessage(MSG_IDENTITY, msgId, "PROTOCOL");
  //           } else {
  //               // Handle command
  //               String errorMessage = "No logic for " + command + " " + arg;
  //               sendMessage(MSG_ERROR, msgId, errorMessage);
  //               // Serial.println(makeMessage("ERROR:" + msgId + ':' + "No logic for " + command + " with arg " + arg));
  //           }


  //         }

  //       } else {
  //         Serial.println(makeMessage("ERROR:CHK_FAIL:" + payload)); // Add message ID?
  //       }
  //     }
  //   }
  // }
}
