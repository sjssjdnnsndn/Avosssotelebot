import { spawn } from "child_process";

console.log("Starting Python Telegram Bot...");

const pythonProcess = spawn("python", ["bot.py"], {
  stdio: "inherit",
});

pythonProcess.on("close", (code) => {
  console.log(`Python process exited with code ${code}`);
  process.exit(code || 0);
});

pythonProcess.on("error", (err) => {
  console.error("Failed to start python process:", err);
  process.exit(1);
});
