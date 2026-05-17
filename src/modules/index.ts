export function getSystemHealth(): any {
  return { overallStatus: "healthy", modules: [] };
}

export function getModuleHealth(): any {
  return { overallStatus: "healthy", modules: [] };
}

export async function getEndpointWatchdogs(): Promise<any> {
  return { overall_status: "healthy", watchdogs: [] };
}

export function isKillSwitchActive(): boolean {
  return false;
}

export function activateKillSwitch(reason: string): void {
  console.warn("[KillSwitch] Activated:", reason);
}

export function liftKillSwitch(): void {
  console.info("[KillSwitch] Lifted");
}
