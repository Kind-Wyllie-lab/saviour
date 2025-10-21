// Helper class to give access to websocket connection to flask backend (Singleton socket connection)
import { io } from "socket.io-client";

const socket = io("http://192.168.1.1:5000");  // Flask backend
export default socket;