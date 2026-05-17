export async function compileContracts(): Promise<any> {
  return { success: true, contracts: [] };
}

export async function getCompiledContract(name?: string): Promise<any> {
  return { abi: [], version: "1.0.0", contractName: name ?? "JDLFlashReceiver" };
}

export async function getCompilerStatus(): Promise<any> {
  return { status: "idle", lastCompilation: null };
}

export async function rebuildContract(name?: string): Promise<any> {
  return { success: true, contract: name ?? "JDLFlashReceiver", version: "1.0.0", contractName: name ?? "JDLFlashReceiver", solcVersion: "0.8.20", compiledAt: new Date().toISOString() };
}
