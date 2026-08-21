// Shared target -> module-list resolution, used by both NewSessionForm
// (to decide what a session would actually record) and ReadinessSummary
// (to decide what to show readiness for) -- kept in one place so the two
// can never silently disagree on what a given target selection means.

export function groupModulesByGroup(modules) {
  const map = {};
  modules.forEach((m) => {
    if (m.group) {
      map[m.group] = [...(map[m.group] ?? []), m];
    }
  });
  return map;
}

export function resolveTargetModules(modules, target, groups) {
  if (target === "all") return modules;
  if (target in groups) return groups[target];
  return modules.filter((m) => m.id === target);
}

// "Ready" matches NewSessionForm's own allTargetReady check -- the
// definition of ready must stay identical between the form (which gates
// whether Start is actually clickable) and the readiness summary (which
// tells the operator why), or the two would tell conflicting stories.
export function isModuleReady(module) {
  return module.status === "READY";
}
