import { Router, type IRouter } from "express";
import healthRouter from "./health";
import avisoRouter from "./aviso";

const router: IRouter = Router();

router.use(healthRouter);
router.use(avisoRouter);

export default router;
