import "dotenv/config";
import express from "express";
import { internalAuth } from "./middleware/internal-auth";
import healthRouter from "./routes/health";
import walletsRouter from "./routes/wallets";

const app = express();
app.use(express.json());
app.use("/api", internalAuth);

app.use("/api", healthRouter);
app.use("/api", walletsRouter);

const port = Number(process.env.WORKER_PORT) || 8502;
app.listen(port, () => {
  console.log(`[Worker] Listening on ${port}`);
});
