// Shared helper for the deploy token required by update/deploy Socket.IO
// events (upload_update_start, deploy_update, stage_current_version,
// deploy_update_to_module, update_saviour_controller). Stored in
// localStorage so it's entered once per browser rather than per action.
const STORAGE_KEY = "saviour_deploy_token";

export function getDeployToken() {
  return localStorage.getItem(STORAGE_KEY) || "";
}

export function setDeployToken(token) {
  if (token) {
    localStorage.setItem(STORAGE_KEY, token);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}
