import socket from "/src/socket";

// Short-lived token for a plain-HTTP upload: the endpoint has no Socket.IO
// session of its own, so an already-authenticated socket asks for one (same
// mechanism as the session downloads; web.py request_download_token).
export function getUploadToken() {
  return new Promise((resolve, reject) => {
    const done = (fn) => (d) => {
      socket.off("download_token", ok);
      socket.off("auth_required", no);
      clearTimeout(t);
      fn(d);
    };
    const ok = done((d) => resolve(d.token));
    const no = done(() => reject(new Error("Login required to upload")));
    const t = setTimeout(
      () => done(() => reject(new Error("Timed out requesting upload token")))(),
      8000,
    );
    socket.on("download_token", ok);
    socket.on("auth_required", no);
    socket.emit("request_download_token");
  });
}
