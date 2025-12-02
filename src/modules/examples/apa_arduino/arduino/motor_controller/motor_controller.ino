/**
 * Arduino Motor Controller with PID Control
 * 
 * This sketch implements a PID-controlled motor system using the Pololu G2 Dual Motor Shield.
 * It provides closed-loop speed control based on encoder feedback, with configurable PID gains
 * and various motor control commands via serial communication.
 * 
 * Hardware Requirements:
 * - Arduino board (Uno, Mega, etc.)
 * - Pololu G2 Dual Motor Shield (24v14 version) https://github.com/pololu/dual-g2-high-power-motor-shield
 * - Analog absolute encoder 0-5V (connected to A2)
 * - DC motor with encoder feedback
 * 
 * Features:
 * - PID-based speed control with configurable gains
 * - Encoder feedback for closed-loop control
 * - Smooth motor speed ramping
 * - Serial command interface
 * - Fault detection and protection
 * - Configurable motor direction
 * 
 * Serial Commands:
 * - SET_SPEED:rpm - Set target RPM (enables PID control)
 * - FLIP_MOTOR:0/1 - Flip motor direction
 * - READ_ENCODER - Get current encoder position and RPM
 * - PID_STATUS - Get current PID status and values
 * - SET_PID:Kp:Ki:Kd - Configure PID gains
 * - SET_SPEED_MANUAL:speed - Manually set motor speed (disables PID)
 * - CLEANUP - Stop motor and disable drivers
 * 
 * @author: Andrew SG
 * @date: 2025-06-02
 * @version: 2.0 (PID-enhanced)
 * @license: MIT
 */

#include "DualG2HighPowerMotorShield.h"

// =============================================================================
// MOTOR SHIELD CONFIGURATION
// =============================================================================

DualG2HighPowerMotorShield24v14 md;  // Motor shield instance

// =============================================================================
// MOTOR CONTROL PARAMETERS
// =============================================================================

// Speed control parameters
const int MAX_SPEED = 400;           // Maximum motor speed (0-400), can be negative or positive depending on direction.
const int SPEED_DELAY = 1;           // Delay between speed steps (ms)
const int PID_SPEED_LIMIT = 135;     // Maximum speed for PID control (corresponds to ~2 RPM)
int lastSpeed = 0;                   // Last commanded motor speed
float targetSpeed = 0;

// =============================================================================
// PID CONTROL SYSTEM
// =============================================================================

// PID control variables
double rpmSetpoint = 0;              // Target RPM for PID control
double rpmError = 0;                 // Current error (setpoint - current)
double rpmErrorLast = 0;             // Previous error for derivative calculation
double integral = 0;                 // Integral term accumulator
double derivative = 0;               // Derivative term
double output = 0;                   // PID controller output

// PID gains (simple, conservative values)
double Kp = 25.0;                      // Proportional gain
double Ki = 2.0;                     // Integral gain
double Kd = 0;                       // Derivative gain 

// PID limits
const double INTEGRAL_LIMIT = 500;   // Anti-windup limit

// PID control state
bool pidEnabled = true;              // Whether PID control is active
bool debugMode = false;              // New: Whether to show debug output in any mode
bool motorRunning = false;

// PID timing control
unsigned long lastPidTime = 0;       // Last PID calculation time
unsigned long currentPidTime = 0;    // Current time for PID calculations
const unsigned long PID_INTERVAL = 500; // PID update interval (2Hz)

// =============================================================================
// ENCODER SYSTEM
// =============================================================================

// Encoder hardware configuration
const int ENCODER_PIN = A2;          // Analog encoder input pin
const double DEGREES_PER_INCREMENT = 360.0/1023.0; // Encoder resolution

// Encoder state variables
int encoderReading = 0;              // Raw analog reading (0-1023)
double lastEncoderPosition = 0;      // Previous encoder position (degrees)
double encoderPosition = 0;          // Current encoder position (degrees)

// Enhanced RPM calculation with history to handle stuck readings
double encoderPositions[3] = {0, 0, 0};  // Last 3 position readings
int positionIndex = 0;                    // Current index in circular buffer

// Encoder timing for RPM calculation
unsigned long lastEncoderTime = 0;   // Previous encoder reading time
unsigned long currentEncoderTime = 0; // Current encoder reading time
unsigned long deltaEncoderTime = 0;  // Time between encoder readings

// RPM calculation
double rpmCurrent = 0.0;             // Current calculated RPM

// =============================================================================
// SYSTEM TIMING
// =============================================================================

// Monitoring and control loop timing
unsigned long lastMonitoringTime = 0;        // Last monitoring cycle time
const unsigned long MONITORING_INTERVAL = 200; // Encoder reading interval (5Hz)

// =============================================================================
// SERIAL COMMUNICATION
// =============================================================================

// Response types
const char MSG_ACK [] = "ACK";
const char MSG_NACK [] = "NACK";
const char MSG_SUCCESS [] = "SUCCESS";
const char MSG_ERROR [] = "ERROR";

// Command types
const char MSG_IDENTITY [] = "I";
const char MSG_DATA [] = "D";
const char MSG_SET_PIN_HIGH [] = "H";
const char MSG_SET_PIN_LOW [] = "L";

// Motor commands
const char MSG_SET_SPEED [] = "S";
const char MSG_START_MOTOR [] = "M";
const char MSG_STOP_MOTOR [] = "N";

// Messaging Protocol
const char START_MARKER = '<';
const char END_MARKER = '>';
const char SYSTEM_ID [] = "MOTOR";
bool acknowledgeMessages = false;

// =============================================================================
// FUNCTION PROTOTYPES
// =============================================================================

// Communication functions
String getStatus();
void sendMessage(String type, String message);
void parseCommand(String command, String arg);
void listen();

// State
void sendState();
int lastSentState = 0;
int sendStatePeriod = 50;

// Motor Control
void applyPID();
void setMotorControl(double output);
void stopIfFault();
void flipMotor(bool flip_direction);
void setSpeedSmoothly(int speed);
void setSpeedSmoothly(int speed, bool sendResponseFlag);
void setSpeed(int speed, bool sendResponseFlag);

// Encoder
void readEncoder();
float convertReadingToDegrees(int encoderReading);
float calculateRPM();

// System functions
void cleanup();

// =============================================================================
// PID CONTROL FUNCTIONS
// =============================================================================

/**
 * Apply PID control algorithm to maintain target RPM
 * 
 * This function implements a standard PID controller that:
 * 1. Calculates the error between setpoint and current RPM
 * 2. Applies proportional, integral, and derivative corrections
 * 3. Outputs a motor speed command to minimize the error
 * 
 * The controller uses separate timing from encoder readings for stability
 * and includes anti-windup protection and deadband filtering.
 */
void applyPID() {
  // Skip PID if disabled or setpoint is zero
  if (!motorRunning) {
    return;
  }
  if (!pidEnabled || rpmSetpoint == 0) {
    return;
  }
  
  // Calculate error (setpoint - current)
  rpmError = rpmSetpoint - rpmCurrent;

  // Calculate time delta for PID calculations using dedicated timing
  currentPidTime = millis();
  unsigned long timeDelta = currentPidTime - lastPidTime;
  double pidTimeDelta = 0.0;
  if (timeDelta > 0) {
    pidTimeDelta = timeDelta / 1000.0; // Convert ms to seconds
  } else {
    pidTimeDelta = 0.05; // Default to 50ms if timing is off
  }

  // Update integral and derivative terms only with valid timing
  if (pidTimeDelta > 0) {
    // Debug: Print timing and error values
    static unsigned long lastDebugTime = 0;
    if (debugMode && millis() - lastDebugTime > 500) { // Print every 500ms if debug enabled
      Serial.print("DEBUG: pidTimeDelta=");
      Serial.print(pidTimeDelta, 4);
      Serial.print("s, error=");
      Serial.print(rpmError, 3);
      Serial.print("RPM, integral=");
      Serial.print(integral, 3);
      Serial.print(", derivative=");
      Serial.print(derivative, 3);
      Serial.print(", output=");
      Serial.print(output, 3);
      Serial.print(", pos=");
      Serial.print(encoderPosition, 2);
      Serial.print("deg, lastPos=");
      Serial.print(lastEncoderPosition, 2);
      Serial.print("deg, rpmCurrent=");
      Serial.print(rpmCurrent, 3);
      Serial.print("RPM");
      Serial.println();
      lastDebugTime = millis();
    }
    
    // Integral term: accumulates error over time
    integral += rpmError * pidTimeDelta;
    // Anti-windup: limit integral to prevent saturation
    integral = constrain(integral, -INTEGRAL_LIMIT, INTEGRAL_LIMIT);

    // Derivative term: rate of change of error
    derivative = (rpmError - rpmErrorLast) / pidTimeDelta;
  }

  // Calculate PID output: P + I + D
  output = Kp * rpmError + Ki * integral + Kd * derivative;
  
  // Constrain output to valid motor speed range
  output = constrain(output, -MAX_SPEED, MAX_SPEED);

  // Apply the calculated output to motor control
  setMotorControl(output);

  // Update state for next iteration
  rpmErrorLast = rpmError;
  lastPidTime = currentPidTime;
}

/**
 * Convert PID output to motor speed command
 * 
 * @param output PID controller output value
 */
void setMotorControl(double output) {
  // Convert double output to integer speed
  int targetSpeed = static_cast<int>(-output);
  
  // Apply PID speed limit to prevent excessive motor speeds
  if (targetSpeed > PID_SPEED_LIMIT) {
    targetSpeed = PID_SPEED_LIMIT;
  } else if (targetSpeed < -PID_SPEED_LIMIT) {
    targetSpeed = -PID_SPEED_LIMIT;
  }
  
  // Only update motor if speed actually changed (prevents command spam)
  if (targetSpeed != lastSpeed) {
    setSpeed(targetSpeed, false); // Don't send response for PID updates
  }
}

// =============================================================================
// MOTOR CONTROL FUNCTIONS
// =============================================================================

/**
 * Check for motor driver faults and stop if detected
 * 
 * This function monitors the motor driver for fault conditions and
 * immediately stops the motor if a fault is detected to prevent damage.
 */
void stopIfFault() {
  if (md.getM1Fault()) {
    md.disableDrivers();
	delay(1);
    Serial.println("M1 fault");
    while (1); // Halt execution on fault
  }
}

/**
 * Flip motor direction
 * 
 * @param flip_direction true to flip direction, false for normal
 */
void flipMotor(bool flip_direction) {
  md.flipM1(flip_direction);
}

/**
 * Set motor speed with smooth ramping (overloaded version)
 * 
 * @param speed Target motor speed (-400 to 400)
 */
void setSpeedSmoothly(int speed) {
  setSpeedSmoothly(speed, true); // Default to sending response
}

/**
 * Set motor speed with smooth ramping
 * 
 * This function gradually ramps the motor speed to the target value
 * to prevent sudden movements and reduce mechanical stress.
 * 
 * @param speed Target motor speed (-400 to 400)
 * @param sendResponseFlag Whether to send a response message
 */
void setSpeedSmoothly(int speed, bool sendResponseFlag) {
  // Skip if speed is already at target
    if (speed == lastSpeed) {
    if (sendResponseFlag) {
        sendMessage(MSG_SUCCESS,  "Speed already at target");
    }
        return;
    }
        
  // Enable motor drivers before setting speed
  md.enableDrivers();
  delay(1); // Required delay when bringing drivers out of sleep mode
  
  // Handle motor stop condition
  if (speed == 0) {
    md.setM1Speed(0);
    md.disableDrivers(); // Put drivers to sleep when stopped
    lastSpeed = 0;
    if (sendResponseFlag) {
      sendMessage(MSG_SUCCESS,  "Motor stopped");
    }
    return;
  }
  
  // Ramp speed up or down gradually
    if (speed > lastSpeed) {
    // Ramp up: increment speed step by step
        for (int i = lastSpeed; i <= speed; i++) {
            md.setM1Speed(i);
      stopIfFault(); // Check for faults during ramping
      delay(SPEED_DELAY);
        }
    } else {
    // Ramp down: decrement speed step by step
        for (int i = lastSpeed; i >= speed; i--) {
            md.setM1Speed(i);
      stopIfFault(); // Check for faults during ramping
      delay(SPEED_DELAY);
        }
    }
  
  // Update last speed and send response
    lastSpeed = speed;
  if (sendResponseFlag) {
    String responseMsg = "Speed set to " + String(speed);
    sendMessage(MSG_SUCCESS,  responseMsg.c_str());
  }
}

/**
 * Set motor speed directly (no ramping)
 * 
 * @param speed Target motor speed (-400 to 400)
 * @param sendResponseFlag Whether to send a response message
 */
void setSpeed(int speed, bool sendResponseFlag) {
  md.setM1Speed(speed);
  lastSpeed = speed; // Update lastSpeed for PID control
}

// =============================================================================
// ENCODER FUNCTIONS
// =============================================================================

/**
 * Read current encoder position and update timing
 * 
 * This function reads the analog encoder value and converts it to angular position.
 * It also updates the timing variables used for RPM calculation.
 */
void readEncoder() {
  // Read raw analog value from encoder (0-1023)
  encoderReading = analogRead(ENCODER_PIN);
  
  // Store previous position for RPM calculation
  lastEncoderPosition = encoderPosition;
  
  // Convert raw reading to angular position in degrees
  encoderPosition = convertReadingToDegrees(encoderReading);
  
  // Update position history for enhanced RPM calculation
  encoderPositions[positionIndex] = encoderPosition;
  positionIndex = (positionIndex + 1) % 3; // Circular buffer
  
  // Update timing for RPM calculation
  lastEncoderTime = currentEncoderTime;
  currentEncoderTime = millis();
}

/**
 * Convert raw encoder reading to angular position
 * 
 * @param encoderReading Raw analog reading (0-1023)
 * @return Angular position in degrees (0-360)
 */
float convertReadingToDegrees(int encoderReading) {
  return DEGREES_PER_INCREMENT * encoderReading;
}

/**
 * Calculate RPM from encoder position changes
 * 
 * This function calculates the motor speed in RPM by:
 * 1. Computing the change in encoder position over time
 * 2. Converting position change to rotational speed
 * 3. Applying filtering to reduce noise
 * 4. Properly handling encoder wraparound at 0/360 degrees
 * 
 * @return Current RPM (filtered and validated)
 */
float calculateRPM() {
  // First reading - no RPM data available yet
  if (lastEncoderTime == 0) {
    return 0.0;
  }
  
  // Calculate time between readings
  unsigned long timeDelta = currentEncoderTime - lastEncoderTime;
  
  // Require minimum time between readings to avoid noise amplification
  if (timeDelta == 0 || timeDelta < 20) {
    return rpmCurrent; // Return previous value if timing is too short
  }
  
  // Check for "stuck" readings - if position hasn't changed for 2 cycles
  bool isStuck = false;
  if (abs(encoderPosition - lastEncoderPosition) < 0.1 && 
      abs(lastEncoderPosition - encoderPositions[(positionIndex + 1) % 3]) < 0.1) {
    isStuck = true;
    if (debugMode) {
      Serial.print("STUCK_DEBUG: Position stuck at ");
      Serial.print(encoderPosition, 2);
      Serial.println("deg, using previous RPM");
    }
  }
  
  // If stuck, return previous RPM value to prevent PID spikes
  if (isStuck) {
    return rpmCurrent;
  }
  
  // Calculate position change with proper wraparound handling
  float positionDelta = encoderPosition - lastEncoderPosition;
  
  // Debug: Log position changes that might indicate rollover
  static bool lastRolloverDebug = false;
  bool currentRolloverDebug = (abs(positionDelta) > 150.0); // Log when we're near rollover threshold
  
  if (debugMode && (currentRolloverDebug || lastRolloverDebug)) {
    Serial.print("ROLLOVER_DEBUG: pos=");
    Serial.print(encoderPosition, 2);
    Serial.print("deg, lastPos=");
    Serial.print(lastEncoderPosition, 2);
    Serial.print("deg, rawDelta=");
    Serial.print(positionDelta, 2);
    Serial.print("deg, rawEncoder=");
    Serial.print(encoderReading);
    Serial.print(", lastRawEncoder=");
    Serial.print(analogRead(ENCODER_PIN)); // Read current raw value for comparison
  }
  
  // Handle wraparound at 0/360 degrees boundary
  // The key insight: we need to find the shortest angular distance
  // between two angles, considering that 359° and 1° are only 2° apart
  
  // If the absolute difference is greater than 180°, we've wrapped around
  if (positionDelta > 180.0) {
    // Wrapped from high to low (e.g., 359° to 1° = -358° change)
    positionDelta -= 360.0;
    if (debugMode && currentRolloverDebug) {
      Serial.print(", WRAPPED_HIGH_TO_LOW, correctedDelta=");
      Serial.print(positionDelta, 2);
      Serial.println("deg");
    }
  } else if (positionDelta < -180.0) {
    // Wrapped from low to high (e.g., 1° to 359° = +358° change)
    positionDelta += 360.0;
    if (debugMode && currentRolloverDebug) {
      Serial.print(", WRAPPED_LOW_TO_HIGH, correctedDelta=");
      Serial.print(positionDelta, 2);
      Serial.println("deg");
    }
  } else if (debugMode && currentRolloverDebug) {
    Serial.println(", NO_WRAPAROUND");
  }
  
  lastRolloverDebug = currentRolloverDebug;
  
  // Convert to RPM: (degrees/time_ms) * (60000ms/min) / (360deg/revolution)
  float rpm = (positionDelta / timeDelta) * 60000.0 / 360.0;
  
  // Validate RPM reading - reject unreasonable values
  // Increased threshold since we now handle wraparound correctly
  if (abs(rpm) > 50) {
    rpm = rpmCurrent; // Use previous value if new reading is unreasonable
  }
  
  // Apply low-pass filter for stability (80% old + 20% new)
  rpmCurrent = 0.8 * rpmCurrent + 0.2 * rpm;
  
  return rpmCurrent;
}

// =============================================================================
// COMMUNICATION FUNCTIONS
// =============================================================================

/**
 * Receive a serial message and process It
 *
 */
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


/**
 * Parse a command payload into cmd and param, then pass it to command handler function
 *
 */
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

  handleCommand(cmd, param);
}





/**
 * Send a formatted response message over serial
 * 
 * @param type Message type ("ACK", "NACK", MSG_SUCCESS, "FAIL", "IDENTITY", "READY" )
 * @param msgId Message ID for tracking - originates from received command
 * @param message Message text e.g. status, error description, data payload
 */
void sendMessage(String type, String message){
  String payload = "<" + type + ":" + message + ">";
  Serial.println(payload);
}


/**
 * Clean up motor controller and stop motor
 * 
 * This function safely stops the motor and disables the motor drivers
 * to prevent damage when shutting down the system.
 */
void cleanup() {
  md.setM1Speed(0);  // Stop the motor (full brake)
  md.disableDrivers(); // Put the MOSFET drivers into sleep mode
  delay(500); // Allow time for motor to stop
  sendMessage(MSG_SUCCESS,  "Cleanup complete");
}

// STATE
void sendState() {
  String stateMessage = String(rpmCurrent) + "," + String(encoderPosition) + "," + String(rpmSetpoint);
  sendMessage(MSG_DATA, stateMessage);
  lastSentState = millis();
}

// =============================================================================
// ARDUINO SETUP AND LOOP
// =============================================================================

/**
 * Arduino setup function - initialize hardware and systems
 * 
 * This function is called once at startup and performs:
 * 1. Serial communication initialization
 * 2. Motor shield initialization and calibration
 * 3. Motor driver enable/disable setup
 * 4. System ready notification
 */
void setup() {
  // Initialize serial communication at 115200 baud
  Serial.begin(115200);

  // Initialize the motor shield
  md.init();
  md.calibrateCurrentOffsets();
  
  delay(100); // Increased delay for better stability
  
  // Enable motor drivers initially
  md.enableDrivers();
  delay(1); // Required delay when bringing drivers out of sleep mode
  
  // Clear any pending serial data
  while(Serial.available()) {
    Serial.read();
  }

  // Send identity and ready message to host
  sendMessage(MSG_IDENTITY, SYSTEM_ID); // Send identity on startup
}

/**
 * Arduino main loop - continuous control and command processing
 * 
 * This function runs continuously and handles:
 * 1. Encoder reading and RPM calculation at fixed intervals
 * 2. PID control updates at fixed intervals
 * 3. Serial command processing and response
 * 4. System monitoring and fault detection
 */
void loop() {
  unsigned long currentTime = millis();
  
  // Process incoming serial commands
  listen();

  if(currentTime - lastSentState > sendStatePeriod) {
    sendState(); // Send RPM and position across serial
  }


  
  // Read encoder and calculate RPM at fixed intervals (5Hz)
  // This prevents noise amplification from too-frequent readings
  if (currentTime - lastMonitoringTime >= MONITORING_INTERVAL) {
    readEncoder();
    calculateRPM();
    lastMonitoringTime = currentTime;
    
    // Debug output for test mode
    if (debugMode && !pidEnabled) {
      static unsigned long lastTestDebugTime = 0;
      if (currentTime - lastTestDebugTime >= 200) { // Print every 200ms
        Serial.print("TEST_DEBUG: RPM=");
        Serial.print(rpmCurrent, 3);
        Serial.print(", encoderRaw=");
        Serial.print(encoderReading);
        Serial.print(", position=");
        Serial.print(encoderPosition, 2);
        Serial.println("deg");
        lastTestDebugTime = currentTime;
      }
    }
  }
  
  // Run PID control at fixed intervals (5Hz)
  // Separate timing from encoder readings for stability
  if (currentTime - lastPidTime >= PID_INTERVAL) {
    applyPID();
  }
}
