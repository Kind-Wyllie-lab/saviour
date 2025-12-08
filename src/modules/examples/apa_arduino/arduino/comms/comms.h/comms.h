/*
  Comms.h - Implementation of a serial communication protocol for the SAVIOUR system.
  Created by Andrew SG, August 26, 2025.
*/
#ifndef Comms_h
#define Comms_h

#include "Arduino.h"

class Comms
{
  public:
    Comms();
    void sendMessage();
    void parseCommand();
    enum MSG_TYPES { ACK, NACK, SUCCESS, ERROR, IDENTITY, DATA };
    enum CMD_TYPES { GET_IDENTITY, GET_DATA };
  private:
    String _makeMessage();
}

#endif