import { spawn, ChildProcess } from "child_process";
import path from "path";
import { logger } from "./logger";

const PYTHON_WORKER = process.env.PYTHON_WORKER_PATH || path.join(__dirname, "../../python/bridge.py");

class PythonBridge {
  private proc: ChildProcess | null = null;
  private pending = new Map<string, { resolve: Function; reject: Function }>();
  private idCounter = 0;

  start(): void {
    if (this.proc) return;
    try {
      this.proc = spawn("python3", [PYTHON_WORKER], {
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env },
      });
    } catch (err) {
      logger.error({ err }, "Failed to spawn Python worker");
      this.proc = null;
      return;
    }
    let buffer = "";
    this.proc.stdout?.on("data", (data) => {
      buffer += data.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          const pending = this.pending.get(msg.id);
          if (pending) {
            if (msg.error) pending.reject(new Error(msg.error));
            else pending.resolve(msg.result);
            this.pending.delete(msg.id);
          }
        } catch {}
      }
    });
    this.proc.on("exit", () => {
      this.proc = null;
      for (const [_, p] of this.pending) p.reject(new Error("Python worker exited"));
      this.pending.clear();
    });
  }

  async call(method: string, params: any[] = []): Promise<any> {
    if (!this.proc) this.start();
    if (!this.proc) {
      return { error: "Python worker is not available" };
    }
    const id = String(++this.idCounter);
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc!.stdin!.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }

  stop(): void {
    if (this.proc) {
      this.proc.kill();
      this.proc = null;
    }
  }
}

export const pythonBridge = new PythonBridge();
export default pythonBridge;
