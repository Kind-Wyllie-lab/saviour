

/** 
 * Parse a received serial command 
 *
 */
void handleCommand(String command, String param) {
  // =============================================================================
  // IDENTITY
  // =============================================================================
  if (command == MSG_IDENTITY) {
    sendMessage(MSG_IDENTITY, SYSTEM_ID);
  }

  else if (command == MSG_KEEPALIVE) {
    // noteHostPacket() has already done its work
  }


  // =============================================================================
  // SPEED CONTROL COMMANDS
  // =============================================================================

  else if (command == MSG_SET_SPEED) {
    // Handle set current
    if (param == "NONE") {
      sendMessage(MSG_ERROR, "No param given");
    } else {
      rpmSetpoint = param.toDouble();
      if (rpmSetpoint == 0) {
        // Disable PID and stop motor
        pidEnabled = false;
        motorRunning = false;
        setSpeedSmoothly(0);
        resetMotorOutput();
      }
    }
  }

  else if (command == MSG_START_MOTOR) {
    if (motorRunning && pidEnabled) {
      // Already running (e.g. a session start after a manual start): a
      // restart would drop it back into the soft start.
    } else if (rpmSetpoint > 0) {
      // Enable PID control with new setpoint
      pidEnabled = true;
      motorRunning = true;
      // Re-enable motor drivers (they may have been disabled during stop)
      md.enableDrivers();
      delay(1); // Required delay when bringing drivers out of sleep mode
      // Fresh PID state every start: the feedforward carries what was
      // learned, and a stale integral would only add to it.
      integral = 0;
      rpmErrorLast = 0;
      lastPidTime = millis(); // Reset PID timing
      motorStartMs = millis();
      resetMotorOutput();
    } else {
      sendMessage(MSG_ERROR, "rpmSetpoint=0");
    }
  }

  else if (command == MSG_STOP_MOTOR) {
    pidEnabled = false;
    motorRunning = false;
    setSpeedSmoothly(0);
    resetMotorOutput();
  }

  else if (command == MSG_SET_FEEDFORWARD) {
    // Motor command that held this setpoint last time (motor.py learns it)
    feedforward = constrain(param.toDouble(), 0, PID_SPEED_LIMIT);
  }


  // =============================================================================
  // MOTOR CONFIGURATION COMMANDS
  // =============================================================================

  

  // =============================================================================
  // STATUS AND MONITORING COMMANDS
  // =============================================================================
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
      resetMotorOutput();
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
