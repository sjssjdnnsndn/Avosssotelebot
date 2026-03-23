import { spawn } from "child_process";
import http from "http";

console.log("Starting Python Telegram Bot...");

const pythonProcess = spawn("python", ["main.py"], {
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

const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/plain" });
  res.end("Bot is running\n");
});

server.listen(5000, "0.0.0.0", () => {
  console.log("Health check server listening on port 5000");
});
