import { Router, type IRouter } from "express";
import healthRouter from "./health";
import agentsRouter from "./agents";
import flashLoansRouter from "./flash-loans";
import walletsRouter from "./wallets";
import blockchainRouter from "./blockchain";

const router: IRouter = Router();

router.use(healthRouter);
router.use(agentsRouter);
router.use(flashLoansRouter);
router.use(walletsRouter);
router.use("/blockchain", blockchainRouter);

export default router;
