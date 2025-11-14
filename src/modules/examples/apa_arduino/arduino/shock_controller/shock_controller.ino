/**
 * Arduino Shock Controller with Timer-Based Pulse Generation
 * 
 * This sketch implements a controller for the Lafayette Instruments HSCK100AP 
 * scrambled grid current generator. It provides precise timing control for 
 * shock delivery with safety limits and fault detection.
 * 
 * Hardware Requirements:
 * - Arduino board (Uno, Mega, etc.)
 * - Lafayette Instruments HSCK100AP Scrambled Grid Current Generator
 * - DB25 cable to connect Arduino to shock generator
 * - TimerOne library for precise timing control
 * 
 * Features:
 * - DB25 communication with shock generator
 * - Precise PWM timing control using TimerOne
 * - Fault detection and protection
 * - Serial command interface
 * - Hardcoded safety limits on shock delivery
 * - Self-test input monitoring for shock verification
 * 
 * Safety Features:
 * - Maximum current limit: 5.1mA
 * - Maximum pulses per trial: 50
 * - Current step resolution: 0.2mA
 * - Automatic deactivation on limits
 * - Emergency stop capability
 * 
 * Serial Commands:
 * - CURRENT:float - Set shock current in mA (0.0-5.1mA, 0.2mA steps)
 * - TIME_ON:float - Set pulse duration in seconds
 * - TIME_OFF:float - Set inter-pulse interval in seconds  
 * - PULSES:int - Set number of pulses (0=infinite, >0=specific count)
 * - ACTIVATE - Begin shock sequence with current parameters
 * - DEACTIVATE - Stop active shock sequence
 * - CLEANUP - Emergency stop and reset all systems
 * - STATUS - Get current system status and parameters
 * 
 * @author: Andrew SG / Domagoj Anticic
 * @date: 2025-06-02
 * @version: 2.1 (Enhanced documentation and structure)
 * @license: MIT
 */

#include <TimerOne.h>

// =============================================================================
// HARDWARE CONFIGURATION
// =============================================================================

// DB25 interface pins (LSB to MSB for current control)
// Each pin represents a binary weight for current: 0.2mA, 0.4mA, 0.8mA, 1.6mA, 3.2mA, etc.
const int CURRENT_OUT[8] = {A3, A2, A1, A0, 4, 5, 6, 7};

// Timing control pins
const int TRIGGER_OUT = 9;        // PWM output for precise timing control (must be pin 9 for TimerOne)
const int SELF_TEST_OUT = A4;     // Test signal output to shock generator
const int SELF_TEST_IN = 2;       // Test signal input from shock generator (active low)

// =============================================================================
// SERIAL COMMUNICATION
// =============================================================================

// Response types
const char MSG_ACK [] = "ACK";
const char MSG_NACK [] = "NACK";
const char MSG_SUCCESS [] = "SUCCESS";
const char MSG_ERROR [] = "ERROR";

// Command types
const char MSG_IDENTITY [] = "IDENTITY";
const char MSG_DATA [] = "DATA";

// Messaging Protocol
const char START_MARKER = '<';
const char END_MARKER = '>';
const char MSG_ID_UNSOLICITED [] = "M0"; // Message ID for unsolicited messages
const char SYSTEM_ID [] = "SHOCK_ARDUINO";
int seqId = 0;


// =============================================================================
// SAFETY CONSTANTS AND LIMITS
// =============================================================================

// Current control limits
const float MAX_CURRENT_MA = 1.0;         // Maximum current in mA
const float CURRENT_STEP_MA = 0.2;        // Current resolution in mA
const int MAX_CURRENT_MICROAMPS = 1000;   // Maximum current in microamps (1.0mA * 1000)

// Timing limits
const unsigned long MIN_PULSE_DURATION_MS = 1;     // Minimum pulse width
const unsigned long MAX_PULSE_DURATION_MS = 1000;  // Maximum pulse width
const unsigned long MIN_INTERVAL_MS = 10;          // Minimum interval between pulses

// Safety limits
const int MAX_PULSES_PER_TRIAL = 50;              // Maximum pulses per experimental trial
const int EMERGENCY_STOP_DELAY_MS = 500;          // Delay for emergency stop operations

// =============================================================================
// SHOCK PARAMETERS
// =============================================================================

// Current control parameters
float currentAmps = -1;               // Current setting in mA
int current = -1;                     // Current setting in microamps

// Timing parameters
int timeOn = -1;                      // Pulse duration in milliseconds
int timeOff = -1;                     // Inter-pulse interval in milliseconds

// Pulse control parameters
int pulseTarget = -1;                 // Target pulse count (-1=disabled, 0=infinite, >0=specific count)
int pulseCounter = 0;                 // Pulses delivered in current sequence
int globalPulseCounter = 0;           // Total pulses delivered this trial
int verifiedShockCounter = 0;        // Verified shocks delivered (via self-test input)

// =============================================================================
// SYSTEM STATE
// =============================================================================

// Control state
bool activatedState = false;          // Shock sequence currently active
bool shockBeingDelivered = false;     // Shock currently being delivered (from self-test input)

// =============================================================================
// FUNCTION PROTOTYPES
// =============================================================================

// Communication functions
String makeMessage(String payload);
String getStatus();
void sendMessage(String type, String msgId, String message);
void parseCommand(String command, String arg, String msgId);
void listen();

// Current control functions
byte calculateCurrentOutput(int current);
void setCurrent(byte binary_current);

// Timing and control functions
void deactivate();
void onCompleteCycle();
bool validateShockParameters(String msgId);

// System functions
void cleanup();

// =============================================================================
// ARDUINO SETUP AND LOOP
// =============================================================================

/**
 * Arduino setup function - initialize hardware and systems
 * 
 * This function is called once at startup and performs:
 * 1. Pin initialization for DB25 interface
 * 2. Timer initialization for precise timing control
 * 3. Serial communication setup
 * 4. System safety initialization
 * 5. Ready state notification
 */
void setup() {
  // Initialize timing control pins
  pinMode(TRIGGER_OUT, OUTPUT);
  pinMode(SELF_TEST_OUT, OUTPUT);
  pinMode(SELF_TEST_IN, INPUT);

  // Set pins to safe state (active low logic)
  digitalWrite(TRIGGER_OUT, HIGH);    // Ensure trigger is off
  digitalWrite(SELF_TEST_OUT, HIGH);  // Ensure test output is off

  // Initialize current control pins (DB25 interface)
  for (int i = 0; i < 8; i++) {
    pinMode(CURRENT_OUT[i], OUTPUT);
    digitalWrite(CURRENT_OUT[i], HIGH); // Set to safe state (active low)
  }

  // Initialize serial communication
  Serial.begin(115200);
  
  // Clear any pending serial data
  while(Serial.available()) {
    Serial.read();
  }

  // Initialize TimerOne for precise timing control
  Timer1.initialize();

  delay(1000); // Allow time for systems to stabilize
  // Send identity and ready message
  // Serial.println("<IDENTITY:SHOCK_CONTROLLER>");
  // Serial.println("<READY>");
  // sendMessage("IDENTITY", "SHOCK_CONTROLLER");
  // sendMessage("READY", " ");
  sendMessage(MSG_IDENTITY, MSG_ID_UNSOLICITED, SYSTEM_ID); // Send identity on startup
}

// =============================================================================
// COMMUNICATION FUNCTIONS
// =============================================================================

/**
 * Receive a serial message and process It
 *
 */
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

          // Split by ':'
          // Example command: <M1:HELLO:2|0c>
          int firstSep = payload.indexOf(':'); // Index of the first : that preceeds command
          if (firstSep > 0) {
            String msgId = payload.substring(0, firstSep); // e.g. M25
            String rest = payload.substring(firstSep + 1); // e.g. SET_SPEED: 2.0
            int secondSep = rest.indexOf(':');
            String command;
            String arg;
            command = rest.substring(0, secondSep);
            arg = rest.substring(secondSep+1);
            if (command == arg) {  
              arg = "NONE";
            }
            parseCommand(command.c_str(), arg.c_str(), msgId);
          }

        } else { // Failed checksum
          sendMessage(MSG_ERROR, MSG_ID_UNSOLICITED, ("CHK_FAIL" + String(payload)).c_str());
        }
      }
    }
  }
}

/** 
 * Parse a received serial command 
 *
 */
void parseCommand(String command, String param, String msgId) {
  // =============================================================================
  // PARSE COMMAND
  // =============================================================================
  // command = command.toUpperCase();

  // =============================================================================
  // CURRENT CONTROL COMMANDS
  // =============================================================================

  if (command == "CURRENT") {
    // Handle set current
    // float currentMa = cmd["param"];     // Set shock current in mA
    if (param == "NONE") {
      sendMessage(MSG_ERROR, msgId, "No param given");
    } else {
      float currentMa = param.toFloat();
      if (currentMa >= 0 && currentMa <= MAX_CURRENT_MA) {
        currentAmps = currentMa;
        current = (int)(currentMa * 1000); // Convert to microamps
          byte db25out = calculateCurrentOutput(current);
          setCurrent(db25out);
        sendMessage(MSG_SUCCESS, msgId, ("Current set to " + String(currentMa, 2) + "mA").c_str());
      } else {
        sendMessage(MSG_ERROR, msgId, ("Current must be 0.0-" + String(MAX_CURRENT_MA, 1) + "mA").c_str());
      }
    } 
  }
  // =============================================================================
  // TIMING CONTROL COMMANDS
  // =============================================================================
  
  else if (command == "TIME_ON") {
    if (param == "NONE") {
      sendMessage(MSG_ERROR, msgId, "No param given");
    } else {
      float timeSec = param.toFloat();
      if (timeSec > 0) {
        timeOn = (int)(timeSec * 1000); // Convert to ms
        sendMessage(MSG_SUCCESS, msgId, ("On time set to " + String(timeSec, 3) + "s").c_str());
      } else {
        sendMessage(MSG_ERROR, msgId, "Time on must be greater than 0");
      }
    } 
  }

  else if (command == "TIME_OFF") {
    // Set inter-pulse interval in seconds
    if (param == "NONE") {
      sendMessage(MSG_ERROR, msgId, "No param given");
    } else {
      float timeSec = param.toFloat();
      if (timeSec > 0) {
        timeOff = (int)(timeSec * 1000); // Convert to milliseconds
        sendMessage(MSG_SUCCESS, msgId, ("Off time set to " + String(timeOff, 3) + "s").c_str());
      } else {
        sendMessage(MSG_ERROR, msgId, "Time off must be greater than 0");
      }
    }
  }

  // =============================================================================
  // PULSE CONTROL COMMANDS
  // =============================================================================
  
  else if (command == "PULSES") {
    // Set number of pulses to deliver
    if (param == "NONE") {
      sendMessage(MSG_ERROR, msgId, "No param given");
    } else {
      int pulses = param.toInt();
      if (pulses >= 0) {
        pulseTarget = pulses;
        String pulseDesc = (pulses == 0) ? "infinite" : String(pulses);
        sendMessage(MSG_SUCCESS, msgId, ("Pulse count set to " + pulseDesc).c_str());
      } else {
        sendMessage(MSG_ERROR, msgId, "Pulse count must be greater than or equal to 0");
      }
    }
  }
  
  else if (command == "RESET_PULSE_COUNTER") {
    // Reset global pulse counter for current trial
    globalPulseCounter = 0;
    verifiedShockCounter = 0;
    sendMessage(MSG_SUCCESS, msgId, ("Pulses reset, pulses=" + String(globalPulseCounter) + ", verified_pulses=" + String(verifiedShockCounter)).c_str());
  }

  // =============================================================================
  // SEQUENCE CONTROL COMMANDS
  // =============================================================================

  else if (command == "ACTIVATE") {
    // Begin shock sequence with current parameters
    if (validateShockParameters(msgId)) {
      // Calculate timing parameters for TimerOne
      long timeOnMicro = ((long)timeOn) * 1000;      // Convert to microseconds
      long timeOffMicro = ((long)timeOff) * 1000;    // Convert to microseconds
      long period = timeOnMicro + timeOffMicro;      // Total period
      float ratio = ((float)timeOff) / (timeOff + timeOn);  // Duty cycle ratio
      int dutyCycle = (int)(ratio * 1024);           // TimerOne duty cycle (0-1024)

      // Reset counters for new sequence
          pulseCounter = 0;
          verifiedShockCounter = 0;
          shockBeingDelivered = false;

      // Start PWM timing with TimerOne
      digitalWrite(TRIGGER_OUT, LOW);  // Start with active low
          Timer1.attachInterrupt(onCompleteCycle);
          Timer1.pwm(TRIGGER_OUT, dutyCycle, period); 

          activatedState = true;
          sendMessage(MSG_SUCCESS, msgId, "Shock sequence started");
        }
      }

  else if (command == "DEACTIVATE") {
    // Stop active shock sequence
    if (activatedState) {
      deactivate();
      sendMessage(MSG_SUCCESS, msgId, ("Shocks stopped, pulses="  + String(globalPulseCounter) + ", verified_pulses=" + String(verifiedShockCounter)).c_str());
    } else {
      sendMessage(MSG_ERROR, msgId, "No active shock sequence");
      }
    }

  // ===========================================================#==================
  // STATUS AND MONITORING COMMANDS
  // =============================================================================
  
  else if (command == "STATUS") {
    // Send current params back i.e. current, time_on, time_off, pulses
    sendMessage(MSG_SUCCESS, msgId, getStatus().c_str());
  }

  else {
    String errorMessage = "No logic for " + command + " " + param;
    sendMessage(MSG_ERROR, msgId, errorMessage);
  }
}

/**
 * Send a formatted response message over serial
 * 
 * @param type Message type ("ACK", "NACK", MSG_SUCCESS, "FAIL", "IDENTITY", "READY" )
 * @param msgId Message ID for tracking - originates from received command
 * @param message Message text e.g. status, error description, data payload
 */
void sendMessage(String type, String msgId, String message){
  String payload = type + ":" + msgId + ":" + message;
  Serial.println(makeMessage(payload));
}

/**
 * Make a formatted message with checksum
 * 
 * @param payload Message payload (without start/end markers or checksum)
 * @return Formatted message string with start/end markers and checksum
 */
String makeMessage(String payload) {
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

/**
 * Get current system status as a formatted string
 * 
 * @return Status string with all current parameters and state
 */
String getStatus() {
  float verificationRate = (globalPulseCounter > 0) ? 
    ((float)verifiedShockCounter / globalPulseCounter * 100) : 0.0;
  
  return "Current:" + String(currentAmps, 2) + "mA" +
         ",TimeOn:" + String(timeOn/1000.0, 3) + "s" +
         ",TimeOff:" + String(timeOff/1000.0, 3) + "s" +
         ",Pulses:" + String(pulseTarget) +
         ",Active:" + String(activatedState ? "true" : "false") +
         ",Delivered:" + String(globalPulseCounter) +
         ",Verified:" + String(verifiedShockCounter) +
         ",VerificationRate:" + String(verificationRate, 1) + "%";
}

// =============================================================================
// CURRENT CONTROL FUNCTIONS
// =============================================================================

/**
 * Calculate binary output for current setting
 * 
 * Converts current in microamps to 8-bit binary pattern for DB25 interface.
 * Each bit represents a current step of 0.2mA (20 microamps).
 * 
 * @param current Current in microamps (0-5100)
 * @return 8-bit binary pattern for DB25 output
 */
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

/**
 * Set current output on DB25 interface
 * 
 * Applies binary pattern to current control pins. Uses active-low logic
 * where LOW = bit set, HIGH = bit clear.
 * 
 * @param binary_current 8-bit pattern for current setting
 */
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

// =============================================================================
// TIMING AND CONTROL FUNCTIONS
// =============================================================================

/**
 * Validate shock parameters before activation
 * 
 * Checks all parameters are within safe limits and properly configured
 * before allowing shock sequence to start.
 * 
 * @return true if all parameters are valid, false otherwise
 */
bool validateShockParameters(String msgId) {
  if (timeOn <= 0) {
    sendMessage(MSG_ERROR, msgId, "Time on must be greater than 0");
    return false;
  }
  if (timeOff <= 0) {
    sendMessage(MSG_ERROR, msgId, "Time off must be greater than 0");
    return false;
  }
  if (currentAmps <= 0 || currentAmps > MAX_CURRENT_MA) {
    sendMessage(MSG_ERROR, msgId, ("Current must be 0.0-" + String(MAX_CURRENT_MA, 1) + "mA").c_str());
    return false;
  }
  if (pulseTarget < 0) {
    sendMessage(MSG_ERROR, msgId, "Pulse count must be greater than or equal to 0");
    return false;
  }
  if (activatedState) {
    sendMessage(MSG_ERROR, msgId, "Shock sequence already active");
    return false;
  }
  return true;
}

/**
 * Check for delivered shocks via self-test input
 */

void checkSelfTestInput() {
  // Monitor self-test input for shock verification
  // This provides feedback that shocks are actually being delivered
  if (!shockBeingDelivered && activatedState) { // Only check if pulse is active
    if (digitalRead(SELF_TEST_IN) == LOW) {  // Active low input
      shockBeingDelivered = true;
      verifiedShockCounter++; // Increment verified shocks counter
      String message = "Shock delivery verified, verified_pulses=" + String(verifiedShockCounter);
      sendMessage("SUCCESS", MSG_ID_UNSOLICITED, message);

    } else {
      if (digitalRead(SELF_TEST_IN) == HIGH) {  // Active low input
        shockBeingDelivered = false;
      }
    } 
  }
  // Shock is being delivered, nothing to do here. It will be reset when input goes high (active low).
}


/**
 * Deactivate shock sequence and stop all outputs
 * 
 * Safely stops the shock sequence by:
 * 1. Disabling TimerOne PWM output
 * 2. Detaching timer interrupt
 * 3. Setting trigger output to safe state
 * 4. Updating system state
 */
void deactivate() {
  activatedState = false;
  Timer1.disablePwm(TRIGGER_OUT);
  Timer1.detachInterrupt();
  digitalWrite(TRIGGER_OUT, HIGH);  // Ensure output is off (active low)
  shockBeingDelivered = false;
}

/**
 * Timer interrupt handler for shock pulse timing
 * 
 * This function is called by TimerOne at the specified intervals to:
 * 1. Check if pulse limits have been reached
 * 2. Toggle the trigger output for pulse generation
 * 3. Update pulse counters
 * 4. Handle automatic deactivation on limits
 */
void onCompleteCycle() {
  // Check if we should stop due to pulse count limits
  if (pulseTarget > 0 && pulseCounter >= pulseTarget) {
    deactivate();
    return;
  }

  // Check if maximum pulses per trial have been reached
  if (globalPulseCounter >= MAX_PULSES_PER_TRIAL) {
    deactivate();
    // sendMessage(MSG_ERROR, "Max number of pulses has been delivered. Trial must end.");
    return;
  }

  // Toggle the trigger output to generate pulse
  if (digitalRead(TRIGGER_OUT) == LOW) {
    digitalWrite(TRIGGER_OUT, HIGH);
  } else {
    digitalWrite(TRIGGER_OUT, LOW);
  }

  // Update pulse counters
  pulseCounter++;
  globalPulseCounter++;
}

/**
 * Arduino main loop - continuous monitoring and command processing
 * 
 * This function runs continuously and handles:
 * 1. Serial command processing and response
 * 2. Self-test input monitoring for shock verification
 * 3. System state monitoring
 * 4. Safety checks and fault detection
 */
void loop() {
  // Process incoming serial commands
  listen();

  // Monitor self-test input for shock verification
  // This provides feedback that shocks are actually being delivered
  checkSelfTestInput();
  
  // Fault detection: Alert if shock is triggered but not delivered
  static unsigned long lastFaultCheck = 0;
  static bool faultReported = false;
  
  if (activatedState && current > 0 && !shockBeingDelivered) {
    // Check if we've been waiting too long for a shock to be delivered
    if (millis() - lastFaultCheck > 5000 && !faultReported) { // 5 second timeout
      sendMessage("WARNING", "NONE", "Shock triggered but not delivered - check hardware connections");
      faultReported = true;
    }
  } else {
    // Reset fault detection when shock is delivered or sequence stops
    lastFaultCheck = millis();
    faultReported = false;
  }
}

// =============================================================================
// SYSTEM FUNCTIONS
// =============================================================================
/**
 * Emergency cleanup and system reset
 * 
 * This function performs a complete emergency stop and reset:
 * 1. Stops any active shock sequence
 * 2. Disables all outputs to safe state
 * 3. Resets all pins to inactive state
 * 4. Provides delay for system stabilization
 * 5. Sends confirmation of cleanup completion
 * 
 * This is the primary safety function for emergency situations.
 */
void cleanup() {
  // Stop any active shock sequence
  if (activatedState) {
    deactivate();
  }
  
  // Reset all pins to safe state (active low logic)
  digitalWrite(TRIGGER_OUT, HIGH);      // Ensure trigger is off
  digitalWrite(SELF_TEST_OUT, HIGH);    // Ensure test output is off
  
  // Reset all current control pins to safe state
  for (int i = 0; i < 8; i++) {
    digitalWrite(CURRENT_OUT[i], HIGH); // Set to inactive state
  }
  
  // Allow time for system to stabilize
  delay(EMERGENCY_STOP_DELAY_MS);
  
  // Send confirmation of cleanup completion
  sendMessage(MSG_SUCCESS, MSG_ID_UNSOLICITED, "Cleanup complete - all systems in safe state");
}
