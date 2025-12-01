

/** 
 * Parse a received serial command 
 *
 */
void handleCommand(String command, String param) {
  // =============================================================================
  // PARSE COMMAND
  // =============================================================================
  // command = command.toUpperCase();

  // =============================================================================
  // SPEED CONTROL COMMANDS
  // =============================================================================

  // sendMessage("DEBUG",  ("Command=" + String(command) + ", Param=" + String(param)).c_str());

  if (command == MSG_SET_SPEED) {
    // Handle set current
    if (param == "NONE") {
      sendMessage(MSG_ERROR, "No param given");
    } else {
      float rpmSetpoint = param.toDouble();
      if (rpmSetpoint > 0) {
        // Enable PID control with new setpoint
        pidEnabled = true;
        // Re-enable motor drivers (they may have been disabled during stop)
        md.enableDrivers();
        delay(1); // Required delay when bringing drivers out of sleep mode
        // Only reset PID terms if this is a new setpoint (not just re-enabling)
        if (abs(rpmSetpoint - rpmCurrent) > 0.5) {
          integral = 0;
          rpmErrorLast = 0;
          lastPidTime = millis(); // Reset PID timing
        }
        // Send success message?
      } else {
        // Disable PID and stop motor
        pidEnabled = false;
        setSpeedSmoothly(0);
        // Send success message?
        // sendMessage(MSG_SUCCESS, "PID disabled, motor stopping");
        }
    }
  }

  else if (command == MSG_START_MOTOR) {

  }

  else if (command == MSG_STOP_MOTOR) {
    pidEnabled = false;
    setSpeedSmoothly(0);
  }


  // =============================================================================
  // MOTOR CONFIGURATION COMMANDS
  // =============================================================================
  
  // else if (command == "FLIP_MOTOR") {
  // // Flip motor direction
  //   if (param == "NONE") {
  //     flipMotor(1);
  //     sendMessage(MSG_SUCCESS,  "Motor Flipped (1)");
  //   } else {
  //     bool flip_direction = param.toInt();
  //     flipMotor(flip_direction);
  //     sendMessage(MSG_SUCCESS,  ("Motor flipped (" + String(flip_direction) + ")").c_str());
  //   }
  // }

  // =============================================================================
  // STATUS AND MONITORING COMMANDS
  // =============================================================================

  else if (command == "READ_ENCODER") {
    // Return current encoder position and RPM
    String response = "Raw:" + String(encoderReading) + 
                     ",Position:" + String(encoderPosition, 2) + "deg" +
                     ",RPM:" + String(rpmCurrent, 2);
    sendMessage(MSG_SUCCESS,  response.c_str());
      }
  
  else if (command == "PID_STATUS") {
    // Return current PID status and values for debugging
    String response = "SetpointRPM:" + String(rpmSetpoint, 2) + 
                     ",CurrentRPM:" + String(rpmCurrent, 2) + 
                     ",ErrorRPM:" + String(rpmError, 2) + 
                     ",Integral:" + String(integral, 2) +
                     ",Output:" + String(output, 2) + 
                     ",TargetSpeed:" + String(static_cast<int>(output)) +
                     ",Enabled:" + String(pidEnabled ? "true" : "false");
    sendMessage(MSG_SUCCESS,  response.c_str());
  }

  // =============================================================================
  // CONFIGURATION COMMANDS
  // =============================================================================
  
  else if (command == "SET_PID") {
    // Configure PID gains: SET_PID:Kp:Ki:Kd
    // TODO: Replace with use of param
    if (param == "NONE") {
      sendMessage(MSG_ERROR,  "No param given");
    } else {
      int firstComma = param.indexOf(',');
      int secondComma = param.indexOf(',', firstComma + 1);
      if (firstComma > 0 && secondComma > 0) {
        Kp = param.substring(0, firstComma).toDouble();
        Ki = param.substring(firstComma + 1, secondComma).toDouble();
        Kd = param.substring(secondComma + 1).toDouble();
        sendMessage(MSG_SUCCESS,  ("Kp=" + String(Kp) + ", Ki=" + String(Ki) + ", Kd=" + String(Kd)).c_str());
      } else {
        sendMessage(MSG_ERROR,  "Invalid PID format. Use: SET_PID:Kp,Ki,Kd");
      }
    }
  }

  // =============================================================================
  // TESTING COMMANDS
  // =============================================================================
  
  else if (command == "SET_SPEED_MANUAL") {
    // Manually set motor speed (disables PID control)
    if (param == "NONE") {
      sendMessage(MSG_ERROR,  "No param given");
    } else {
      int manualSpeed = param.toInt();
      pidEnabled = false;
      setSpeedSmoothly(manualSpeed);
      String responseMsg = "Manual motor speed set to: " + String(manualSpeed);
      sendMessage(MSG_SUCCESS,  responseMsg.c_str());
    }
  }

  // =============================================================================
  // SYSTEM COMMANDS
  // =============================================================================
  
  else if (command == "CLEANUP") {
    // Stop motor and disable drivers
    cleanup();
  }
  
  else if (command == "RESET_PID") {
    // Reset PID state for debugging
    integral = 0;
    rpmErrorLast = 0;
    lastPidTime = millis();
    sendMessage(MSG_SUCCESS,  "PID state reset");
  }
  
  else if (command == "STOP_TEST_DEBUG") {
    // Stop test mode debug output (deprecated, now use DEBUG_MODE:0)
    debugMode = false;
    sendMessage(MSG_SUCCESS,  "Debug mode disabled");
  }
  
  else if (command == "DEBUG_MODE") {
    // Enable or disable debug output
    int mode = param.toInt();
    debugMode = (mode != 0);
    String responseMsg = String("Debug mode ") + (debugMode ? "enabled" : "disabled");
    sendMessage(MSG_SUCCESS,  responseMsg.c_str());
  }
  

  // ===========================================================#==================
  // STATUS AND MONITORING COMMANDS
  // =============================================================================
  
    else {
      String errorMessage = "No logic for " + command + " " + param;
      sendMessage(MSG_ERROR,  errorMessage);
    }
}
