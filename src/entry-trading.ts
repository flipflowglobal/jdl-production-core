import "dotenv/config";
import express from "express";
import { internalAuth } from "./middleware/internal-auth";
import flashLoansRouter from "./routes/flash-loans";
import agentsRouter from "./routes/agents";
import blockchainRouter from "./routes/blockchain";

const app = express();
app.use(express.json());
app.use("/api", internalAuth);

app.use("/api/flash-loans", flashLoansRouter);
app.use("/api/agents", agentsRouter);
app.use("/api/blockchain", blockchainRouter);

const port = Number(process.env.TRADING_PORT) || 8501;
app.listen(port, () => {
  console.log(`[Trading] Listening on ${port}`);
});
